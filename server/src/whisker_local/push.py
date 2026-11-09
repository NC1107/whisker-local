"""Optional push notifications through a relay, inert unless PUSH_RELAY_URL is set.

The published apps can only be reached through the credentials of whoever signed them, so the
server never talks to Apple or Google. It registers once with a relay, keeps the key it is
given, and hands the relay the alert text and the phone tokens it was told about.

Nothing here may disturb the robot. Notices are queued in a small ring that drops its oldest
entry when full, and a single task sends them over plain HTTP in a thread with a timeout. The
relay allows 6 messages a minute per phone, so sends are paced under that, a burst of quiet
notices is folded into one, and a notice the relay turns away is tried once more.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import urlsplit

from . import push_options
from .push_catalog import (
    DEFAULT_KINDS,
    DRAWER_RANK,
    FALLBACK_TITLE,
    MAX_BODY_BYTES,
    MAX_TITLE_BYTES,
    alert_text,
    collapse_id,
    trim_bytes,
)
from .store import Store

__all__ = ["DEFAULT_KINDS", "Push", "PushNotifier", "RelayError"]

log = logging.getLogger(__name__)

DEVICES_KEY = "push_devices"
KINDS_KEY = "push_kinds"
RELAY_KEY_KEY = "push_relay_key"
HELD_KEY = "push_held"

MAX_DEVICES = 10
MAX_PENDING = 16
REQUEST_TIMEOUT_SECONDS = 10
# A relay that drips bytes keeps every socket timeout from firing, so the whole call, connect
# and body together, has a deadline of its own. The wait in the event loop gets a little more,
# so the transport normally reports the timeout first.
TOTAL_DEADLINE_SECONDS = 15
CALL_DEADLINE_SECONDS = TOTAL_DEADLINE_SECONDS + 2
# Sends are rare and one at a time, so a couple of threads of our own is plenty and keeps a
# stuck relay from using up the default executor that the rest of the server shares.
PUSH_THREADS = 2
REGISTER_RETRY_SECONDS = 300
MAX_RESPONSE_BYTES = 64 * 1024
PLATFORMS = ("ios", "android")

# The relay allows 6 a minute per phone. Staying under 4 leaves room for a test push and for the
# relay's clock not being this server's clock.
SENDS_PER_MINUTE = 4
PACING_WINDOW_SECONDS = 60.0
# More quiet notices than this waiting at once are folded into a single push.
MERGE_ABOVE = 3
# A relay that said "later" gets one more try after this long.
RETRY_AFTER_SECONDS = 60.0
MAX_HELD = 30
# How often the sender looks for the end of quiet hours when nothing else wakes it.
IDLE_CHECK_SECONDS = 30.0
SUMMARY_KIND = "summary"


class RelayError(Exception):
    pass


class RelayBusy(RelayError):
    """The relay turned the push away for now: rate limited, or failing on its side."""


class InvalidRelayUrl(Exception):
    pass


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_relay_url(url: str) -> str:
    """Return the base URL without a trailing slash, or raise InvalidRelayUrl.

    The key goes in a header and the alert text in the body, so plain http is only for a relay
    on the same machine.
    """
    parts = urlsplit(url.strip())
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host or parts.query or parts.fragment:
        raise InvalidRelayUrl("PUSH_RELAY_URL must be an http or https URL")
    if parts.scheme == "http" and not _is_loopback(host):
        raise InvalidRelayUrl("PUSH_RELAY_URL must be https unless the relay is on this machine")
    return url.strip().rstrip("/")


Post = Callable[[str, dict[str, Any], str | None], tuple[int, Any]]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key and the alert text to a place nobody configured."""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def post_json(url: str, payload: dict[str, Any], key: str | None) -> tuple[int, Any]:
    """POST JSON and return the status and the decoded body. Blocking, so run it in a thread.

    Raises TimeoutError once TOTAL_DEADLINE_SECONDS pass, however the bytes trickle in.
    """
    deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
    headers = {"Content-Type": "application/json", "User-Agent": "whisker-local"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # noqa: S310
        url, json.dumps(payload).encode(), headers, method="POST"
    )
    try:
        with _opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status, raw = response.status, _read(response, deadline)
    except urllib.error.HTTPError as err:
        with err:
            status, raw = err.code, _read(err, deadline)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _read(response: Any, deadline: float) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size < MAX_RESPONSE_BYTES:
        if time.monotonic() > deadline:
            raise TimeoutError("the relay took too long to answer")
        # read1 returns what has arrived, where read would wait for a full chunk and so
        # would sleep through the deadline on a slow relay.
        chunk = response.read1(min(4096, MAX_RESPONSE_BYTES - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def stored_devices(store: Store) -> list[dict[str, str]]:
    stored = store.get_json(DEVICES_KEY)
    if not isinstance(stored, list):
        return []
    return [
        d
        for d in stored
        if isinstance(d, dict)
        and isinstance(d.get("token"), str)
        and d.get("platform") in PLATFORMS
    ]


def add_device(store: Store, token: str, platform: str) -> None:
    """Remember a phone, newest last, forgetting the oldest once there are too many."""
    others = [d for d in stored_devices(store) if d["token"] != token]
    added = datetime.now(UTC).isoformat(timespec="seconds")
    devices = [*others, {"token": token, "platform": platform, "added": added}]
    store.put_json(DEVICES_KEY, devices[-MAX_DEVICES:])


def remove_devices(store: Store, tokens: set[str]) -> None:
    kept = [d for d in stored_devices(store) if d["token"] not in tokens]
    store.put_json(DEVICES_KEY, kept)


def kind_settings(store: Store) -> dict[str, bool]:
    stored = store.get_json(KINDS_KEY)
    chosen = (
        {k: v for k, v in stored.items() if k in DEFAULT_KINDS and type(v) is bool}
        if isinstance(stored, dict)
        else {}
    )
    return {**DEFAULT_KINDS, **chosen}


def update_kinds(store: Store, changes: dict[str, bool]) -> dict[str, bool]:
    """Keep only what was set on purpose, so a default that changes later still follows."""
    stored = store.get_json(KINDS_KEY)
    kept = (
        {k: v for k, v in stored.items() if k in DEFAULT_KINDS and type(v) is bool}
        if isinstance(stored, dict)
        else {}
    )
    store.put_json(KINDS_KEY, {**kept, **changes})
    return kind_settings(store)


@dataclass(frozen=True, slots=True)
class Push:
    kind: str
    message: str
    urgent: bool
    title: str | None = None
    # The cat a visit notice is about, None when nobody was named.
    cat_id: int | None = None
    # Tells apart two sorts of notice under one kind, such as different changes in routine.
    topic: str | None = None
    attempts: int = 0

    def collapse(self) -> str:
        return collapse_id(self.kind, self.cat_id, self.topic)

    def headline(self) -> str:
        return self.title or alert_text(self.kind, {})[0] or FALLBACK_TITLE

    def held(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.headline(),
            "message": self.message,
            "cat_id": self.cat_id,
            "topic": self.topic,
        }


def _held_push(item: Any) -> Push | None:
    if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
        return None
    title, message, cat_id = item.get("title"), item.get("message"), item.get("cat_id")
    topic = item.get("topic")
    if not isinstance(title, str) or not isinstance(message, str):
        return None
    return Push(
        item["kind"],
        message,
        False,
        title,
        cat_id if type(cat_id) is int else None,
        topic if isinstance(topic, str) else None,
    )


def _summary(pushes: list[Push], title: str | None = None) -> Push:
    lines = "; ".join(p.headline() for p in pushes)
    return Push(SUMMARY_KIND, lines, False, title or f"{len(pushes)} updates")


class PushNotifier:
    def __init__(
        self,
        relay_url: str,
        store: Store,
        *,
        post: Post = post_json,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        timezone: tzinfo = UTC,
    ) -> None:
        self._relay_url = validate_relay_url(relay_url)
        self._store = store
        self._post = post
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._tz = timezone
        self._pending: deque[Push] = deque(maxlen=MAX_PENDING)
        self._sent_at: deque[float] = deque()
        self._drawer_sent = 0
        self._wake = asyncio.Event()
        self._register_after = float("-inf")
        self._pool = ThreadPoolExecutor(max_workers=PUSH_THREADS, thread_name_prefix="push")

    def alert(self, kind: str, message: str, urgent: bool) -> bool:
        return self.notify(Push(kind, message, urgent))

    def notify(self, push: Push) -> bool:
        """Queue, hold or drop one notice by the person's choices. Never blocks or raises.

        Returns whether the notice was taken in (queued, held for later, or dropped by quiet
        hours), as opposed to turned away because the kind is off or no phone is registered.
        """
        try:
            if not kind_settings(self._store).get(push.kind, False):
                return False
            if not stored_devices(self._store):
                return False
            options = push_options.load(self._store)
            if push.kind in push_options.CAT_KINDS and not push_options.wants_cat(
                options, push.kind, push.cat_id
            ):
                return False
            if not push.urgent and self._is_quiet(options):
                if push_options.quiet_hours(options).mode == "hold":
                    self._hold(push)
                return True
            self._enqueue(push)
            return True
        except Exception:
            log.exception("could not queue a push")
            return False

    def cleared(self, kind: str) -> None:
        """A drawer condition ended, so the next one of its kind is news again."""
        if DRAWER_RANK.get(kind, 0) >= self._drawer_sent:
            self._drawer_sent = 0

    def _is_quiet(self, options: dict[str, Any]) -> bool:
        local = self._now().astimezone(self._tz)
        return push_options.quiet_hours(options).covers(local.time())

    def _enqueue(self, push: Push) -> None:
        rank = DRAWER_RANK.get(push.kind, 0)
        if rank:
            # One filling drawer climbs three kinds, and the phone should hear the worst only.
            if rank <= self._drawer_sent or any(
                DRAWER_RANK.get(p.kind, 0) >= rank for p in self._pending
            ):
                return
        wanted = push.collapse()
        self._pending = deque(
            (p for p in self._pending if p.collapse() != wanted),
            maxlen=MAX_PENDING,
        )
        if len(self._pending) == MAX_PENDING:
            log.warning("the push queue is full, dropping the oldest notice")
        self._pending.append(push)
        self._wake.set()

    def _hold(self, push: Push) -> None:
        wanted = push.collapse()
        kept = [
            item
            for item in self._held()
            if (held := _held_push(item)) is not None and held.collapse() != wanted
        ]
        self._store.put_json(HELD_KEY, [*kept, push.held()][-MAX_HELD:])

    def _held(self) -> list[dict[str, Any]]:
        stored = self._store.get_json(HELD_KEY)
        return [i for i in stored if isinstance(i, dict)] if isinstance(stored, list) else []

    def _release_held(self) -> None:
        held = self._held()
        if not held or self._is_quiet(push_options.load(self._store)):
            return
        # Forget them before queueing, so a crash in between loses a notice and never repeats it.
        self._store.put_json(HELD_KEY, [])
        pushes = [p for p in (_held_push(item) for item in held) if p is not None]
        if not pushes:
            return
        self._pending.append(pushes[0] if len(pushes) == 1 else _summary(pushes, "While you slept"))
        self._wake.set()

    def _fold_bursts(self) -> None:
        quiet = [p for p in self._pending if not p.urgent]
        if len(quiet) <= MERGE_ABOVE:
            return
        urgent = [p for p in self._pending if p.urgent]
        self._pending = deque([*urgent, _summary(quiet)], maxlen=MAX_PENDING)

    async def run(self) -> None:
        try:
            await self._run()
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), IDLE_CHECK_SECONDS)
            self._wake.clear()
            try:
                self._release_held()
            except Exception:
                log.exception("could not release held pushes")
            await self.flush()

    async def flush(self) -> None:
        """Send everything waiting, urgent first, at the pace the relay allows."""
        while self._pending:
            self._fold_bursts()
            push = min(self._pending, key=lambda p: not p.urgent)
            self._pending.remove(push)
            await self._wait_for_slot()
            try:
                await self._deliver(push)
            except RelayBusy as err:
                if push.attempts == 0:
                    self._pending.appendleft(replace(push, attempts=1))
                    log.info("the relay asked for a pause: %s", err)
                    await self._sleep(RETRY_AFTER_SECONDS)
                else:
                    log.warning("push to the relay failed twice: %s", err)
            except Exception as err:
                # The text of the error can hold the relay's reply, never a token.
                log.warning("push to the relay failed: %s", type(err).__name__)
            else:
                self._drawer_sent = max(self._drawer_sent, DRAWER_RANK.get(push.kind, 0))

    async def _wait_for_slot(self) -> None:
        while True:
            now = self._clock()
            while self._sent_at and now - self._sent_at[0] >= PACING_WINDOW_SECONDS:
                self._sent_at.popleft()
            if len(self._sent_at) < SENDS_PER_MINUTE:
                self._sent_at.append(now)
                return
            await self._sleep(self._sent_at[0] + PACING_WINDOW_SECONDS - now)

    async def send_test(self) -> int:
        """Send a test message to every phone, ignoring the per-kind settings."""
        if not stored_devices(self._store):
            raise RelayError("No phone registered for notifications")
        title, body = alert_text("test", {})
        return await self._deliver(Push("test", body, False, title))

    async def _deliver(self, push: Push) -> int:
        devices = stored_devices(self._store)
        if not devices:
            return 0
        key = await self._key()
        messages = [
            {
                "platform": d["platform"],
                "token": d["token"],
                "title": trim_bytes(push.headline(), MAX_TITLE_BYTES),
                "body": trim_bytes(push.message, MAX_BODY_BYTES),
                "data": {"kind": push.kind, "urgent": "1" if push.urgent else "0"},
                "collapseId": push.collapse(),
            }
            for d in devices
        ]
        status, body = await self._call("/v1/send", {"messages": messages}, key)
        if status in (401, 403):
            # The relay revoked or forgot this key, so ask for a new one on the next push.
            self._store.put_json(RELAY_KEY_KEY, None)
            raise RelayError(f"the relay refused the key (HTTP {status})")
        if status == 429 or status >= 500:
            raise RelayBusy(f"the relay answered HTTP {status}")
        if status != 200 or not isinstance(body, dict):
            raise RelayError(f"the relay answered HTTP {status}")
        results = body.get("results")
        results = results if isinstance(results, list) else []
        gone = {
            r["token"]
            for r in results
            if isinstance(r, dict)
            and isinstance(r.get("token"), str)
            # A forbidden result says nothing about the phone, only about this server's claim
            # on it, so the device stays.
            and r.get("status") == "unregistered"
        }
        if gone:
            remove_devices(self._store, gone)
        return sum(1 for r in results if isinstance(r, dict) and r.get("status") == "delivered")

    async def _key(self) -> str:
        stored = self._store.get_json(RELAY_KEY_KEY)
        if isinstance(stored, str) and stored:
            return stored
        if self._clock() < self._register_after:
            raise RelayError("waiting to register with the relay again")
        self._register_after = self._clock() + REGISTER_RETRY_SECONDS
        status, body = await self._call("/v1/register", {}, None)
        key = body.get("key") if isinstance(body, dict) else None
        if status != 200 or not isinstance(key, str) or not key:
            raise RelayError(f"registering with the relay answered HTTP {status}")
        self._store.put_json(RELAY_KEY_KEY, key)
        self._register_after = float("-inf")
        return key

    async def _call(self, path: str, payload: dict[str, Any], key: str | None):
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(self._pool, self._post, self._relay_url + path, payload, key),
            timeout=CALL_DEADLINE_SECONDS,
        )
