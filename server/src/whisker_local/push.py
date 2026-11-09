"""Optional push notifications through a relay, inert unless PUSH_RELAY_URL is set.

The published apps can only be reached through the credentials of whoever signed them, so the
server never talks to Apple or Google. It registers once with a relay, keeps the key it is
given, and hands the relay the alert text and the phone tokens it was told about.

Nothing here may disturb the robot. Alerts are queued in a small ring that drops its oldest
entry when full, and a single task sends them over plain HTTP in a thread with a timeout.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from whiskerless.devices.litter_robot_4 import alerts

from .store import Store

log = logging.getLogger(__name__)

DEVICES_KEY = "push_devices"
KINDS_KEY = "push_kinds"
RELAY_KEY_KEY = "push_relay_key"

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

# Alerts that are routine or that the robot repeats all day stay off until asked for.
_DEFAULT_OFF = frozenset(
    {
        alerts.Alert.VISIT,
        alerts.Alert.LONG_VISIT,
        alerts.Alert.FREQUENT_VISITS,
        alerts.Alert.CYCLE_COMPLETED,
        alerts.Alert.PANEL_LOCK_CHANGED,
    }
)
DEFAULT_KINDS: dict[str, bool] = {a.value: a not in _DEFAULT_OFF for a in alerts.Alert}


class RelayError(Exception):
    pass


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


class PushNotifier:
    def __init__(
        self,
        relay_url: str,
        store: Store,
        *,
        post: Post = post_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._relay_url = validate_relay_url(relay_url)
        self._store = store
        self._post = post
        self._clock = clock
        self._pending: deque[Push] = deque(maxlen=MAX_PENDING)
        self._wake = asyncio.Event()
        self._register_after = float("-inf")
        self._pool = ThreadPoolExecutor(max_workers=PUSH_THREADS, thread_name_prefix="push")

    def alert(self, kind: str, message: str, urgent: bool) -> None:
        """Queue one raised alert. Never blocks and never raises into the caller."""
        try:
            if not kind_settings(self._store).get(kind, False) or not stored_devices(self._store):
                return
            self._pending.append(Push(kind, message, urgent))
            self._wake.set()
        except Exception:
            log.exception("could not queue a push")

    async def run(self) -> None:
        try:
            await self._run()
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                push = self._pending.popleft()
                try:
                    await self._deliver(push)
                except Exception as err:
                    # The text of the error can hold the relay's reply, never a token.
                    log.warning("push to the relay failed: %s", type(err).__name__)

    async def send_test(self) -> int:
        """Send a test message to every phone, ignoring the per-kind settings."""
        if not stored_devices(self._store):
            raise RelayError("No phone registered for notifications")
        return await self._deliver(Push("test", "Test notification", False))

    async def _deliver(self, push: Push) -> int:
        devices = stored_devices(self._store)
        if not devices:
            return 0
        key = await self._key()
        messages = [
            {
                "platform": d["platform"],
                "token": d["token"],
                "title": "Litter robot needs attention" if push.urgent else "Litter robot",
                "body": push.message,
                "data": {"kind": push.kind, "urgent": "1" if push.urgent else "0"},
                "collapseId": push.kind,
            }
            for d in devices
        ]
        status, body = await self._call("/v1/send", {"messages": messages}, key)
        if status in (401, 403):
            # The relay revoked or forgot this key, so ask for a new one on the next push.
            self._store.put_json(RELAY_KEY_KEY, None)
            raise RelayError(f"the relay refused the key (HTTP {status})")
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
