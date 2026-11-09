from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import re
import secrets
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import aiomqtt
from whiskerless.devices.litter_robot_4 import (
    ActivityMessage,
    Command,
    DrawerBayMoved,
    LitterRobot4Link,
    LitterRobot4State,
    StateMessage,
    alerts,
    const,
    derive,
    events_from_readings,
    litter_level_percent_from_mm,
    parse_message,
)
from whiskerless.devices.litter_robot_4.calibration import hopper_percent_provisional
from whiskerless.devices.litter_robot_4.const import activity_topic, state_topic
from whiskerless.exceptions import WhiskerlessConnectionError
from whiskerless.mqtt import MqttSettings

from . import alert_settings
from .cats import CatTracker
from .config import Config
from .cycle import CYCLES_CONSIDERED, CycleWatch, FinishedCycle, typical_active_seconds
from .limits import TokenBucket
from .moments import Moments
from .push import Push, PushNotifier
from .push_catalog import alert_text, is_urgent
from .store import Store, stamp
from .visit_capture import Episode, Visit, VisitRecorder, WeightUpdate

log = logging.getLogger(__name__)

# The robot only speaks on change, so silence is ambiguous without a regular question.
STATE_POLL_SECONDS = 120
OFFLINE_AFTER = timedelta(seconds=STATE_POLL_SECONDS * 2 + 30)
TICK_SECONDS = 5
MAX_RECONNECT_SECONDS = 30.0
# One drawer pull emits several readings, and neither their values nor their count mean anything.
DRAWER_EVENT_GAP = timedelta(seconds=60)
# The robot only publishes a state document when asked, so activity is the cue to ask. A burst of
# activity then costs one question, and the question trails the burst instead of missing its end.
ACTIVITY_REFRESH_GAP_SECONDS = 2.0
FRESH_STATE_TIMEOUT_SECONDS = 8.0
PRESS_ECHO_TIMEOUT_SECONDS = 5.0
BROKER_ERRORS = (WhiskerlessConnectionError, aiomqtt.MqttError)
ACTIVE_ALERTS_KEY = "alerts_active"
# Alerts that were pushed (or held back for quiet hours) during their current run, so turning a
# kind on while its alert is active tells the person once, and turning it off and on does not.
ANNOUNCED_ALERTS_KEY = "alerts_announced"
LOCK_KEY = "keypad_lockout"
PENDING_VISITS_KEY = "pending_visits"

# The robot's biggest document is about a kilobyte. The LAN listener lets anyone who knows the
# serial publish as the robot, so anything much larger is not the robot.
MAX_MESSAGE_BYTES = 16 * 1024

# A visit sends a few dozen activity messages over a minute or so and a state document follows
# each burst. This leaves room for several visits back to back, and an endless stream of junk
# still stops filling the disk after the first burst.
STORE_BURST_MESSAGES = 120
STORE_MESSAGES_PER_SECOND = 1.0
DROP_LOG_SECONDS = 60.0

# Each message costs the consumer a moment, so a flood grows an unbounded queue until the
# container is killed. A full queue drops the newest message instead.
INBOUND_QUEUE_MESSAGES = 1024


class _QueueFullLogLimiter(logging.Filter):
    """aiomqtt warns once per discarded message, which a flood turns into its own flood."""

    def __init__(self) -> None:
        super().__init__()
        self._dropped = 0
        self._logged = float("-inf")

    def filter(self, record: logging.LogRecord) -> bool:
        if "queue is full" not in record.getMessage():
            return True
        self._dropped += 1
        moment = time.monotonic()
        if moment - self._logged < DROP_LOG_SECONDS:
            return False
        self._logged = moment
        record.msg = "inbound message queue is full, %d dropped so far"
        record.args = (self._dropped,)
        return True


logging.getLogger("mqtt").addFilter(_QueueFullLogLimiter())

# The raw stream is only worth keeping for re-deriving recent visits. Whichever limit is hit
# first wins; visits and other events are never pruned.
MESSAGE_RETENTION = timedelta(days=90)
MESSAGE_BUDGET_BYTES = 200 * 1024 * 1024
PRUNE_EVERY_SECONDS = 3600.0

# Events are kept for good, so what telemetry can make the robot "do" has to be bounded too. A
# burst covers a busy day, and the refill rate is far above anything a real robot produces.
EVENT_BURST = 30
EVENT_REFILL_PER_SECOND = 1 / 60
# A version string is shown in the app and kept forever as an event, and the robot's port is open
# to the LAN, so only what a real version looks like is accepted.
FIRMWARE_VERSION = re.compile(r"[0-9A-Za-z._-]{1,64}")
CALIBRATION_KEY = "litter_calibration"

STATUS_LABELS = {
    "ready": "Ready",
    "bonnet_removed": "Bonnet removed",
    "cat_detected": "Cat inside",
    "cat_sensor_timing": "Waiting to cycle",
    "clean_cycle": "Cleaning",
    "empty_cycle": "Emptying",
    "power_up_cycle": "Starting up",
    "powering_up": "Starting up",
    "powering_down": "Shutting down",
    "off": "Off",
    "changing_filter": "Changing filter",
}


class RobotOffline(Exception):
    """The robot has not been heard from recently enough to send it anything."""


class BrokerUnavailable(RobotOffline):
    """The broker did not take the message, so nothing reached the robot."""


class RobotLocked(Exception):
    """Control lock is on, and a locked robot silently ignores panel presses."""


class DrawerFull(Exception):
    """Emptying the globe sends every bit of litter to the waste drawer, which has no room."""


class SettingNotConfirmed(Exception):
    """A settings write was sent but the robot never read back the new value."""


class StateNotFresh(Exception):
    """The robot did not answer a request for its state in time."""


class LitterNotReadable(Exception):
    """The distance sensor is not looking at the litter, or the reading cannot be used."""


class PressNotAcknowledged(Exception):
    """A press was sent once, and neither an echo nor a fresh state shows that the robot acted."""


def refuse_when_drawer_full(robot: LitterRobot4State) -> Exception | None:
    return DrawerFull() if robot.is_dfi_full or robot.drawer_full_lockout else None


@contextlib.contextmanager
def _broker_failures_as_unavailable() -> Iterator[None]:
    try:
        yield
    except BROKER_ERRORS as err:
        raise BrokerUnavailable from err


def _hopper_status(connected: bool | None, empty: bool | None) -> str:
    if connected is False:
        return "disconnected"
    if empty:
        return "empty"
    return "ok" if connected or empty is False else "unknown"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else stamp(value)


def bound_inbound_queue(client: aiomqtt.Client) -> None:
    # aiomqtt only accepts the bound at construction, which whiskerless does not expose.
    client._queue = asyncio.Queue(maxsize=INBOUND_QUEUE_MESSAGES)


def _is_utf8_encodable(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if isinstance(value, dict):
        return all(_is_utf8_encodable(k) and _is_utf8_encodable(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return all(_is_utf8_encodable(item) for item in value)
    return True


def _firmware_or_none(version: Any) -> str | None:
    if isinstance(version, str) and FIRMWARE_VERSION.fullmatch(version):
        return version
    return None


class RobotBridge:
    def __init__(
        self,
        config: Config,
        store: Store,
        *,
        store_limiter: TokenBucket | None = None,
        notifier: PushNotifier | None = None,
    ) -> None:
        self._config = config
        self._notifier = notifier
        self._store = store
        self._store_limiter = store_limiter or TokenBucket(
            STORE_MESSAGES_PER_SECOND, STORE_BURST_MESSAGES
        )
        self._event_limiters: dict[str, TokenBucket] = {}
        self._unstored = 0
        self._unstored_logged = float("-inf")
        self._cats = CatTracker(store, config.timezone)
        self._link: LitterRobot4Link | None = None
        self._io_lock = asyncio.Lock()
        self._state_arrived = asyncio.Event()
        self._press_echo = asyncio.Event()
        self._awaited_press: int | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._last_refresh = float("-inf")
        self._robot_topics = frozenset((state_topic(config.serial), activity_topic(config.serial)))
        self._visits = VisitRecorder(waiting=self._stored_waiting_visits())
        self._saved_waiting: list[dict[str, Any]] = [e.as_dict() for e in self._visits.waiting]
        self._last_visit: dict[str, Any] | None = self._remember_latest_visit()
        self._presence: bool | None = self._resume_presence()
        self._alerts = alerts.AlertTracker(active=self._stored_active_alerts())
        self._announced = self._stored_announced()
        self._locked: bool | None = self._stored_lock()
        self._moments = (
            Moments(store, notifier, self._cats, config.timezone) if notifier is not None else None
        )
        if self._moments is not None:
            self._moments.resume(_utcnow())
        self._alert_details: dict[alerts.Alert, dict[str, Any]] = {}
        self._last_drawer_event: datetime | None = None
        self._cycle = CycleWatch()
        self._litter_percent: int | None = None
        self._litter_mm: int | None = None
        self._litter_reported = False
        self._calibration = self._stored_calibration()
        self.derived = derive.DerivedState.from_dict(store.get_json("derived"))
        self.latest: LitterRobot4State | None = None
        self.last_seen: datetime | None = None

    def _stored_active_alerts(self) -> set[alerts.Alert]:
        # Alerts still true after a restart must not be announced a second time.
        stored = self._store.get_json(ACTIVE_ALERTS_KEY)
        known = {alert.value: alert for alert in alerts.Alert}
        return {known[key] for key in stored if key in known} if isinstance(stored, list) else set()

    def _stored_announced(self) -> set[str]:
        stored = self._store.get_json(ANNOUNCED_ALERTS_KEY)
        return (
            {key for key in stored if isinstance(key, str)} if isinstance(stored, list) else set()
        )

    def _stored_lock(self) -> bool | None:
        stored = self._store.get_json(LOCK_KEY)
        return stored if type(stored) is bool else None

    def _stored_waiting_visits(self) -> list[Episode]:
        # A visit still waiting for its weight when the server stopped would otherwise be lost.
        stored = self._store.get_json(PENDING_VISITS_KEY)
        if not isinstance(stored, list):
            return []
        try:
            return [Episode.from_dict(item) for item in stored]
        except (KeyError, TypeError, ValueError, AttributeError):
            log.warning("ignoring unreadable pending visits")
            return []

    def _persist_waiting_visits(self) -> None:
        waiting = [episode.as_dict() for episode in self._visits.waiting]
        if waiting != self._saved_waiting:
            self._store.put_json(PENDING_VISITS_KEY, waiting)
            self._saved_waiting = waiting

    def _remember_latest_visit(self) -> dict[str, Any] | None:
        latest = self._store.visits(newest_first=True, limit=1)
        if not latest:
            return None
        visit = latest[0]
        raw = visit["detail"].get("weight_raw")
        if not visit["detail"].get("imported"):
            self._visits.remember(visit["id"], datetime.fromisoformat(visit["at"]), raw)
        return {
            "at": visit["at"],
            "duration_s": visit["detail"].get("duration_s"),
            "weight_lb": visit["detail"].get("weight_lb"),
        }

    def _resume_presence(self) -> bool | None:
        """The online record as the last run left it, closed off if that run ended while online."""
        rows = self._store.presence()
        if not rows:
            return None
        if rows[-1][1]:
            # Nothing was listening after the last message, whatever the robot was doing.
            self._store.add_presence(self._store.latest_message_at() or _utcnow(), False)
        return False

    def _note_online(self, now: datetime) -> None:
        self._check_presence(now)
        if self._presence is not True:
            self._store.add_presence(now, True)
            self._presence = True

    def _check_presence(self, now: datetime) -> None:
        """Record the robot going quiet, at the last time it was heard."""
        if self._presence is True and self.last_seen is not None and not self.online(now):
            self._store.add_presence(self.last_seen, False)
            self._presence = False

    def _stored_calibration(self) -> dict[str, int | None]:
        stored = self._store.get_json(CALIBRATION_KEY)
        stored = stored if isinstance(stored, dict) else {}
        return {
            point: value if type(value := stored.get(point)) is int else None
            for point in ("full_mm", "empty_mm")
        }

    def _open_link(self, role: str) -> LitterRobot4Link:
        # Mosquitto lets a new connection take over a client id, whichever listener it arrives
        # on. An id nobody can guess keeps a LAN client from knocking this one off the broker.
        client_id = f"whisker-local-{role}-{secrets.token_hex(8)}"
        # TLS and the robot's restricted listener are for the LAN side of the broker; this
        # connection stays on the compose network.
        settings = MqttSettings(
            host=self._config.mqtt_host,
            port=self._config.mqtt_port,
            tls=False,
            client_id=client_id,
        )
        link = LitterRobot4Link(settings, self._config.serial)
        bound_inbound_queue(link.client)
        return link

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                async with self._open_link("bridge") as link:
                    self._link = link
                    delay = 1.0
                    log.info("connected to broker at %s", self._config.mqtt_host)
                    await self._consume(link)
            except BROKER_ERRORS as err:
                log.warning("broker connection lost: %s", err)
            except Exception:
                # Nothing a message or the broker does may end this task for good: the
                # container would stay up and report healthy while recording nothing.
                log.exception("bridge session failed")
            finally:
                self._link = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_SECONDS)

    async def _consume(self, link: LitterRobot4Link) -> None:
        tasks = [
            asyncio.create_task(self._heartbeat(link)),
            asyncio.create_task(self._read_messages(link)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            pending = [*tasks, self._refresh_task]
            for task in pending:
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in pending if t is not None), return_exceptions=True)

    async def _read_messages(self, link: LitterRobot4Link) -> None:
        async for message in link.client.messages:
            self._handle(message, link)

    def _handle(self, message: aiomqtt.Message, link: LitterRobot4Link) -> None:
        topic = str(message.topic)
        # Anything but the robot's own live telemetry is somebody else's traffic: our command
        # echoes, other topics under the serial, or a retained message replayed on subscribe.
        if message.retain or topic not in self._robot_topics:
            return
        size = len(message.payload) if isinstance(message.payload, bytes | bytearray | str) else 0
        if size > MAX_MESSAGE_BYTES:
            log.warning(
                "dropped a %d byte message on %s, over the %d byte limit",
                size,
                topic,
                MAX_MESSAGE_BYTES,
            )
            return
        try:
            parsed = parse_message(topic, message.payload)
            if parsed is None:
                return
            if not _is_utf8_encodable(parsed.raw):
                # JSON escapes can decode to a lone surrogate, which no database row or HTTP
                # response can hold as UTF-8.
                log.warning("dropped a message on %s with text that is not valid Unicode", topic)
                return
            self.ingest(parsed, _utcnow())
        except Exception:
            # The protocol is still being mapped upstream, and the robot's listener is open to
            # the LAN. A message we cannot interpret must not end the session.
            log.exception("could not process a message on %s", topic)
            return
        if isinstance(parsed, ActivityMessage):
            self._request_refresh(link)

    def prune_messages(self, now: datetime) -> None:
        removed = self._store.prune_messages(
            older_than=now - MESSAGE_RETENTION, max_bytes=MESSAGE_BUDGET_BYTES
        )
        if removed:
            log.info("pruned %d old raw messages", removed)

    async def _heartbeat(self, link: LitterRobot4Link) -> None:
        loop = asyncio.get_running_loop()
        next_prune = loop.time()
        while True:
            if loop.time() >= next_prune:
                self.prune_messages(_utcnow())
                next_prune = loop.time() + PRUNE_EVERY_SECONDS
            await link.request_state()
            for _ in range(STATE_POLL_SECONDS // TICK_SECONDS):
                await asyncio.sleep(TICK_SECONDS)
                self._settle_visits(_utcnow())

    def _request_refresh(self, link: LitterRobot4Link) -> None:
        # A write in progress asks for its own state, and a refresh landing between two of its
        # paced writes would only add traffic the robot has to answer in the middle of them.
        if self._io_lock.locked():
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_after_activity(link))

    async def _refresh_after_activity(self, link: LitterRobot4Link) -> None:
        loop = asyncio.get_running_loop()
        wait = self._last_refresh + ACTIVITY_REFRESH_GAP_SECONDS - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        if self._io_lock.locked():
            return
        self._last_refresh = loop.time()
        try:
            await link.request_state()
        except BROKER_ERRORS as err:
            log.warning("could not ask the robot for its state: %s", err)

    def ingest(self, message: StateMessage | ActivityMessage, now: datetime) -> None:
        kind = "state" if isinstance(message, StateMessage) else "activity"
        self._store_raw(now, kind, message.raw)
        self._note_online(now)
        self.last_seen = now
        if isinstance(message, ActivityMessage):
            self._note_press_echo(message)

        for late in self._visits.observe(message, now):
            self._add_late_weight(late)

        finished = (
            self._cycle.observe(message.state, now) if isinstance(message, StateMessage) else None
        )
        fault_before = self.derived.globe_motor_fault
        update = derive.apply_message(self.derived, message, now)
        self.derived = update.state

        if isinstance(message, StateMessage):
            self.latest = message.state
            self._remember_litter(message.state)
            self._note_lock(message.state, now)
            self._note_power(message.state, now)
            self._state_arrived.set()
        else:
            self._record_drawer_moves(message, now)
        fault_now = self.derived.globe_motor_fault
        if fault_now and fault_now != fault_before:
            self._add_event(now, "fault", {"code": fault_now})

        self._record_effects(update.effects, now, finished)
        if update.changed:
            self._store.put_json("derived", self.derived.as_dict())
        self._settle_visits(now)
        self._refresh_alerts(now)

    def _store_raw(self, now: datetime, kind: str, raw: Any) -> None:
        if self._store_limiter.allow():
            self._store.add_message(now, kind, raw)
            return
        self._unstored += 1
        moment = time.monotonic()
        if moment - self._unstored_logged >= DROP_LOG_SECONDS:
            log.warning("raw message storage is rate limited, %d dropped so far", self._unstored)
            self._unstored_logged = moment

    def _add_event(self, at: datetime, kind: str, detail: dict[str, Any]) -> int | None:
        limiter = self._event_limiters.setdefault(
            kind, TokenBucket(EVENT_REFILL_PER_SECOND, EVENT_BURST)
        )
        if not limiter.allow():
            return None
        return self._store.add_event(at, kind, detail)

    def _note_press_echo(self, message: ActivityMessage) -> None:
        if self._awaited_press is not None and any(
            reading.register == const.Register.PANEL_BUTTON and reading.value == self._awaited_press
            for reading in message.readings
        ):
            self._press_echo.set()

    def _record_effects(
        self, effects: Sequence[derive.Effect], now: datetime, finished: FinishedCycle | None
    ) -> None:
        for effect in effects:
            if isinstance(effect, derive.CleanCycleCompleted):
                drawer = self.latest.waste_drawer_level if self.latest else None
                detail = {"count": effect.count, "drawer_percent": drawer}
                if finished is not None:
                    detail.update(finished.detail())
                event_id = self._add_event(now, "clean_cycle", detail)
                if event_id is not None and self._moments is not None:
                    self._moments.cycle_completed(event_id, drawer)
            elif isinstance(effect, derive.FirmwareChanged) and FIRMWARE_VERSION.fullmatch(
                effect.version
            ):
                event_id = self._add_event(now, "firmware", {"version": effect.version})
                if event_id is not None and self._moments is not None:
                    self._moments.firmware_updated(event_id, effect.version)

    def _note_lock(self, robot: LitterRobot4State, now: datetime) -> None:
        locked = robot.keypad_lockout
        if locked is None or locked == self._locked:
            return
        previous, self._locked = self._locked, locked
        self._store.put_json(LOCK_KEY, locked)
        # The first reading after install is a baseline, not a change.
        if previous is None:
            return
        event_id = self._add_event(now, "panel_lock", {"locked": locked})
        if event_id is not None and self._moments is not None:
            self._moments.panel_lock_changed(event_id, locked)

    def _note_power(self, robot: LitterRobot4State, now: datetime) -> None:
        if self._moments is None or robot.unit_power_type is None:
            return
        change = self._moments.power.update(robot.unit_power_type == 1, now)
        if change is None:
            return
        self._moments.power_changed(change)
        self._announce_transition("power_loss", change, {}, now)

    def _record_drawer_moves(self, message: ActivityMessage, now: datetime) -> None:
        if not any(isinstance(e, DrawerBayMoved) for e in events_from_readings(message.readings)):
            return
        last = self._last_drawer_event
        if last is None or now - last >= DRAWER_EVENT_GAP:
            self._add_event(now, "drawer_moved", {})
            self._last_drawer_event = now

    def _settle_visits(self, now: datetime) -> None:
        """Save visits once the weight has had time to arrive; it often lands after the end."""
        self._check_presence(now)
        for visit in self._visits.ready(now):
            self._save_visit(visit)
        self._persist_waiting_visits()
        if self._moments is not None:
            self._moments.settle(now)

    def _save_visit(self, visit: Visit) -> None:
        # A restart between saving the visit and forgetting it must not save it twice.
        if self._store.has_event(visit.ended, "visit"):
            return
        detail: dict[str, Any] = {
            "origin": "live",
            "started": stamp(visit.started),
            "duration_s": visit.duration_s,
            "weight_lb": visit.weight_lb,
            "weight_raw": visit.weight_raw,
            "settled_s": visit.settled_s,
            "readings": visit.readings,
            "source": visit.source,
        }
        if visit.long_stay:
            detail["long_stay"] = True
        event_id = self._add_event(visit.ended, "visit", detail)
        if event_id is None:
            return
        self._visits.saved(event_id, visit)
        if self._last_visit is None or stamp(visit.ended) >= self._last_visit["at"]:
            self._last_visit = {
                "at": stamp(visit.ended),
                "duration_s": visit.duration_s,
                "weight_lb": visit.weight_lb,
            }
        self._label_saved(event_id)
        if self._moments is not None:
            self._moments.visit_saved(event_id)

    def _add_late_weight(self, late: WeightUpdate) -> None:
        weight_lb = round(late.weight_raw / const.CAT_WEIGHT_DIVISOR, 2)
        self._store.merge_event_detail(
            late.event_id, {"weight_lb": weight_lb, "weight_raw": late.weight_raw, "readings": 1}
        )
        if self._last_visit is not None:
            self._last_visit["weight_lb"] = weight_lb
        self._label_saved(late.event_id)

    def _label_saved(self, event_id: int) -> None:
        try:
            self._cats.on_visit_saved(event_id)
        except Exception:
            # The visit is already saved, and the next relabel picks it up. Losing the
            # robot link over a labelling problem would be worse.
            log.exception("could not label visit %s", event_id)

    def _remember_litter(self, robot: LitterRobot4State) -> None:
        # Readings are withheld while the globe turns or a cat blocks the sensor. Keeping the
        # last real one stops the gauge from blanking out on every cycle.
        percent = self._litter_from(robot)
        if percent is not None:
            self._litter_percent = percent
            self._litter_mm = robot.litter_level_mm
            self._litter_reported = robot.litter_level_reported

    def _litter_from(self, robot: LitterRobot4State) -> int | None:
        # The firmware's own percentage is the device's answer. Our scale only stands in for it.
        if robot.litter_level_reported or robot.litter_level_mm is None:
            return robot.litter_level
        full_mm, empty_mm = self._litter_scale()
        return litter_level_percent_from_mm(
            robot.litter_level_mm, full_mm=full_mm, empty_mm=empty_mm
        )

    def _litter_scale(self) -> tuple[int | None, int | None]:
        return derive.litter_scale(
            self.derived,
            full_mm=self._calibration["full_mm"],
            empty_mm=self._calibration["empty_mm"],
        )

    def _rescale_litter(self) -> None:
        """Re-read the last real distance against the scale as it is now."""
        if self._litter_mm is None or self._litter_reported:
            return
        full_mm, empty_mm = self._litter_scale()
        self._litter_percent = litter_level_percent_from_mm(
            self._litter_mm, full_mm=full_mm, empty_mm=empty_mm
        )
        self._refresh_alerts(_utcnow())

    def _litter_source(self) -> str | None:
        if self._litter_percent is None:
            return None
        if self._litter_reported:
            return "reported"
        if self._calibration["full_mm"] is not None:
            return "calibrated"
        return "learned" if self.derived.learned_litter.low is not None else "default"

    def _last_visit_at(self) -> datetime | None:
        # The library stamps a visit on any sensor edge, and the globe raises those in every
        # cycle. The timer that alerts on a quiet box has to count only visits that were saved.
        return None if self._last_visit is None else datetime.fromisoformat(self._last_visit["at"])

    def online(self, now: datetime) -> bool:
        return self.last_seen is not None and now - self.last_seen < OFFLINE_AFTER

    def recheck_alerts(self) -> None:
        """Look at the alerts again now, after something they depend on was changed."""
        self._refresh_alerts(_utcnow())

    def _refresh_alerts(self, now: datetime) -> None:
        snapshot = alerts.Snapshot(
            robot=self.latest,
            derived=dataclasses.replace(self.derived, last_cat_visit=self._last_visit_at()),
            online=self.online(now),
            litter_percent=self._litter_percent,
            maintenance=self._store.maintenance_last_done(),
            today=now.astimezone(self._config.timezone).date(),
        )
        settings = alert_settings.load(self._store)
        raw = self._with_wording_details(
            alerts.conditions(snapshot, settings, now, self._alerts.active), now
        )
        transitions = self._alerts.update(raw, now)
        self._alert_details = {alert: details for alert, (_, details) in raw.items()}
        if transitions:
            self._record_transitions(transitions, now)

    def _with_wording_details(
        self, raw: dict[alerts.Alert, tuple[Any, dict[str, Any]]], now: datetime
    ) -> dict[alerts.Alert, tuple[Any, dict[str, Any]]]:
        """Add what the notice text needs and the library does not say."""
        zone = self._config.timezone
        today = now.astimezone(zone).date()
        for alert, (truth, details) in list(raw.items()):
            extra: dict[str, Any] = {}
            if alert is alerts.Alert.LITTER_LOW:
                extra["estimated"] = self._litter_source() in ("learned", "default")
            elif alert is alerts.Alert.NO_VISIT and "last_visit" in details:
                last = datetime.fromisoformat(details["last_visit"]).astimezone(zone)
                extra["since"] = last.strftime("%a %H:%M")
            elif "last_done" in details:
                extra["days"] = (today - datetime.fromisoformat(details["last_done"]).date()).days
            if extra:
                raw[alert] = (truth, {**details, **extra})
        return raw

    def _record_transitions(self, transitions: Sequence[alerts.Transition], now: datetime) -> None:
        for transition in transitions:
            key = transition.alert.value
            self._add_event(
                now,
                "alert",
                {
                    "key": key,
                    "raised": transition.raised,
                    "urgent": transition.alert in alerts.URGENT,
                    "message": alerts.describe(
                        transition.alert, transition.raised, transition.details
                    ),
                },
            )
            self._notify_transition(key, transition.raised, transition.details)
        self._store.put_json(ACTIVE_ALERTS_KEY, sorted(a.value for a in self._alerts.active))
        self._persist_announced()

    def _announce_transition(
        self, key: str, raised: bool, details: dict[str, Any], now: datetime
    ) -> None:
        """An alert this class tracks itself, recorded the same way as the library's."""
        message = alert_text(key, details, raised)[1]
        self._add_event(
            now,
            "alert",
            {"key": key, "raised": raised, "urgent": is_urgent(key), "message": message},
        )
        self._notify_transition(key, raised, details)
        self._persist_announced()

    def _notify_transition(self, key: str, raised: bool, details: dict[str, Any]) -> None:
        if self._notifier is None:
            return
        if not raised:
            self._announced.discard(key)
            self._notifier.cleared(key)
            return
        title, body = alert_text(key, details)
        if self._notifier.notify(Push(key, body, is_urgent(key), title)):
            self._announced.add(key)

    def _persist_announced(self) -> None:
        self._store.put_json(ANNOUNCED_ALERTS_KEY, sorted(self._announced))

    def announce_active(self, kinds: set[str]) -> None:
        """Tell the person about alerts already active whose kind was just switched on."""
        for alert in sorted(self._alerts.active, key=lambda a: a.value):
            key = alert.value
            if key in kinds and key not in self._announced:
                self._notify_transition(key, True, self._alert_details.get(alert, {}))
        self._persist_announced()

    def on_irregularity(self, cat: Any, kind: str, title: str, body: str) -> bool:
        """Passes a change in a cat's routine to the behaviour notification."""
        if self._moments is None:
            return False
        return self._moments.on_irregularity(cat, kind, title, body)

    def status(self) -> dict[str, Any]:
        now = _utcnow()
        self._settle_visits(now)
        self._refresh_alerts(now)
        return {
            "serial": self._config.serial,
            "online": self.online(now),
            "broker_connected": self._link is not None,
            "last_seen": _iso(self.last_seen),
            "robot": None if self.latest is None else self._robot_view(self.latest),
            "alerts": self._alert_view(),
        }

    def _alert_view(self) -> list[dict[str, Any]]:
        ordered = sorted(
            self._alerts.active, key=lambda alert: (alert not in alerts.URGENT, alert.value)
        )
        return [
            {
                "key": alert.value,
                "urgent": alert in alerts.URGENT,
                "message": alerts.describe(alert, True, self._alert_details.get(alert, {})),
            }
            for alert in ordered
        ]

    def _robot_view(self, robot: LitterRobot4State) -> dict[str, Any]:
        status = robot.robot_status
        return {
            "status": status,
            "status_label": STATUS_LABELS.get(status or "", "Unknown"),
            "is_cleaning": robot.is_cleaning,
            "cycle": self._cycle_view(),
            "cat_present": bool(robot.cat_detected),
            "drawer_percent": robot.waste_drawer_level,
            # After a Reset the gauge reads zero until the next cycle actually measures it.
            "drawer_is_estimate": bool(robot.is_dfi_reset_pending),
            "drawer_full": bool(robot.is_dfi_full) or bool(robot.drawer_full_lockout),
            "drawer_full_lockout": bool(robot.drawer_full_lockout),
            "drawer_last_moved": _iso(self.derived.drawer_last_moved),
            "bonnet_removed": bool(robot.is_bonnet_removed),
            "litter_percent": self._litter_percent,
            "litter_level_mm": self._litter_mm,
            "litter_source": self._litter_source(),
            # Without the firmware's own figure or a calibration, "full" is only the fullest
            # level seen so far, so the percentage is relative to that and not a measurement.
            "litter_is_estimate": self._litter_source() in ("learned", "default"),
            "litter_calibrated": self._calibration["full_mm"] is not None,
            "litter_calibration": dict(self._calibration),
            "hopper": self._hopper_view(),
            "clean_cycles": robot.odometer_clean_cycles,
            "empty_cycles": robot.odometer_empty_cycles,
            "wifi_rssi": robot.wifi_rssi,
            "esp_firmware": _firmware_or_none(robot.esp_firmware),
            "pic_firmware": _firmware_or_none(robot.pic_firmware),
            "last_visit_at": None if self._last_visit is None else self._last_visit["at"],
            "last_visit_duration_s": None
            if self._last_visit is None
            else self._last_visit["duration_s"],
            "last_weight_lb": None if self._last_visit is None else self._last_visit["weight_lb"],
            "settings": {
                "night_light_mode": robot.night_light_mode,
                "night_light_brightness": robot.night_light_brightness,
                "clean_cycle_wait_minutes": robot.clean_cycle_wait_minutes,
                "keypad_lockout": robot.keypad_lockout,
                "sleep_enabled": robot.weekday_sleep_enabled,
                "sleep_minute": robot.panel_sleep_time,
                "wake_minute": robot.panel_wake_time,
            },
        }

    def _cycle_view(self) -> dict[str, Any] | None:
        if not self._cycle.running:
            return None
        recent = self._store.events(limit=CYCLES_CONSIDERED, kinds=["clean_cycle"])
        return self._cycle.view(_utcnow(), typical_active_seconds(recent))

    def _hopper_view(self) -> dict[str, Any] | None:
        derived = self.derived
        if not derived.sighted(derive.Capability.HOPPER):
            return None
        raw = derived.hopper_fill_raw
        level = derive.hopper_level_percent(derived)
        source = "measured" if level is not None else None
        if level is None and raw is not None and not derived.hopper_gauge_stale:
            level, source = hopper_percent_provisional(raw), "estimate"
        empty = derive.hopper_empty(derived)
        return {
            "status": _hopper_status(derived.hopper_connected, empty),
            "level_percent": level,
            "level_source": source,
            "fill_raw": raw,
            "connected": derived.hopper_connected,
            "empty": empty,
            "last_dispensed": _iso(derived.last_hopper_dispensed),
        }

    async def calibrate_litter(self, point: str) -> None:
        """Take the distance the robot reports right now as the full or the empty reference.

        The percentage is what is being calibrated, so the raw distance is used, and a reading
        taken while the globe turns or a cat is inside would pin the scale to nonsense.
        """
        self._require_link()
        with _broker_failures_as_unavailable():
            async with self._io_lock:
                link = self._require_link()
                # The globe has only just been filled or emptied, and a cached reading is the
                # one from before.
                if not await self._fresh_state(link):
                    raise StateNotFresh
                robot = self.latest
                if robot is None or robot.litter_level_mm is None:
                    raise LitterNotReadable("The robot has no litter reading right now.")
                self._check_calibration_order(point, robot.litter_level_mm)
                self._calibration = {**self._calibration, f"{point}_mm": robot.litter_level_mm}
                self._store.put_json(CALIBRATION_KEY, self._calibration)
                self._remember_litter(robot)
                self._refresh_alerts(_utcnow())

    def _check_calibration_order(self, point: str, mm: int) -> None:
        # The distance grows as the litter drops, so full has to read closer than empty.
        full, empty = self._calibration["full_mm"], self._calibration["empty_mm"]
        if point == "empty" and full is not None and mm <= full:
            raise LitterNotReadable("Empty has to read farther away than full.")
        if point == "full" and empty is not None and mm >= empty:
            raise LitterNotReadable("Full has to read closer than empty.")

    def clear_litter_calibration(self) -> None:
        self._calibration = {"full_mm": None, "empty_mm": None}
        self._store.put_json(CALIBRATION_KEY, self._calibration)
        self._rescale_litter()

    def _require_link(self) -> LitterRobot4Link:
        if self._link is None or not self.online(_utcnow()):
            raise RobotOffline
        return self._link

    async def press(
        self,
        command: Command,
        *,
        confirms: Callable[[LitterRobot4State], bool] | None = None,
        refuse: Callable[[LitterRobot4State], Exception | None] | None = None,
    ) -> None:
        """Send a panel press exactly once, and say so if the robot never shows it acted.

        There is no retry: a press is edge-triggered and carries no request id, so a repeat
        after a lost echo could run a second cycle. Repeating is the person's call.
        """
        self._require_link()
        with _broker_failures_as_unavailable():
            async with self._io_lock:
                link = self._require_link()
                # The cached state can be minutes old, and a robot locked at its own panel
                # drops the press without a word.
                await self._fresh_state(link)
                if self.latest is not None and self.latest.keypad_lockout:
                    raise RobotLocked
                if refuse is not None and self.latest is not None:
                    reason = refuse(self.latest)
                    if reason is not None:
                        raise reason
                self._press_echo.clear()
                self._awaited_press = command.value
                try:
                    await link.publish(command)
                    self._store.add_event(_utcnow(), "command", {"label": command.label})
                    if await self._echoed():
                        # The press landed; this only brings the dashboard up to date, and its
                        # failure must not make a landed press look failed.
                        with contextlib.suppress(*BROKER_ERRORS):
                            await link.request_state()
                        return
                finally:
                    self._awaited_press = None
                # A lost echo does not mean a lost press, so look at the robot itself.
                if await self._fresh_state(link) and self._acted(confirms):
                    return
        raise PressNotAcknowledged(command.label)

    def _acted(self, confirms: Callable[[LitterRobot4State], bool] | None) -> bool:
        return confirms is not None and self.latest is not None and confirms(self.latest)

    async def _echoed(self) -> bool:
        try:
            async with asyncio.timeout(PRESS_ECHO_TIMEOUT_SECONDS):
                await self._press_echo.wait()
        except TimeoutError:
            return False
        return True

    async def _fresh_state(self, link: LitterRobot4Link) -> bool:
        """Ask for a state document and wait for the next one to be recorded."""
        self._state_arrived.clear()
        await link.request_state()
        try:
            async with asyncio.timeout(FRESH_STATE_TIMEOUT_SECONDS):
                await self._state_arrived.wait()
        except TimeoutError:
            return False
        return True

    async def refresh(self) -> None:
        if self._link is None:
            raise RobotOffline
        with _broker_failures_as_unavailable():
            await self._link.request_state()

    async def apply(self, commands: Sequence[Command]) -> None:
        self._require_link()
        with _broker_failures_as_unavailable():
            async with self._io_lock:
                link = self._require_link()
                try:
                    # Read-back consumes a message stream, so it needs its own connection or it
                    # would swallow the telemetry the main loop is recording.
                    async with self._open_link("settings") as settings_link:
                        for command in commands:
                            if not await settings_link.apply_setting(command):
                                raise SettingNotConfirmed(command.label)
                except SettingNotConfirmed:
                    # Earlier writes in the batch stuck, so show where the robot really is.
                    with contextlib.suppress(*BROKER_ERRORS):
                        await link.request_state()
                    raise
                # The caller reports status next, and the new values only exist here once the
                # robot has answered.
                await self._fresh_state(link)
