from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import aclosing
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
    derive,
    events_from_readings,
    litter_level_percent_from_mm,
)
from whiskerless.exceptions import WhiskerlessConnectionError
from whiskerless.mqtt import MqttSettings

from .config import Config
from .store import Store, stamp

log = logging.getLogger(__name__)

# The robot only speaks on change, so silence is ambiguous without a regular question.
STATE_POLL_SECONDS = 120
OFFLINE_AFTER = timedelta(seconds=STATE_POLL_SECONDS * 2 + 30)
TICK_SECONDS = 5
MAX_RECONNECT_SECONDS = 30.0
# One drawer pull emits several readings, and neither their values nor their count mean anything.
DRAWER_EVENT_GAP = timedelta(seconds=60)

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


class RobotLocked(Exception):
    """Control lock is on, and a locked robot silently ignores panel presses."""


class SettingNotConfirmed(Exception):
    """A settings write was sent but the robot never read back the new value."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else stamp(value)


class RobotBridge:
    def __init__(self, config: Config, store: Store) -> None:
        self._config = config
        self._store = store
        self._link: LitterRobot4Link | None = None
        self._settings_lock = asyncio.Lock()
        self._pending_visits: list[derive.VisitEnded] = []
        self._alerts = alerts.AlertTracker()
        self._alert_details: dict[alerts.Alert, dict[str, Any]] = {}
        self._last_drawer_event: datetime | None = None
        self._litter_percent: int | None = None
        self.derived = derive.DerivedState.from_dict(store.get_json("derived"))
        self.latest: LitterRobot4State | None = None
        self.last_seen: datetime | None = None

    def _mqtt(self, client_id: str) -> MqttSettings:
        # TLS and the robot's restricted listener are for the LAN side of the broker; this
        # connection stays on the compose network.
        return MqttSettings(
            host=self._config.mqtt_host,
            port=self._config.mqtt_port,
            tls=False,
            client_id=client_id,
        )

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                async with LitterRobot4Link(
                    self._mqtt("whisker-local"), self._config.serial
                ) as link:
                    self._link = link
                    delay = 1.0
                    log.info("connected to broker at %s", self._config.mqtt_host)
                    await self._consume(link)
            except (WhiskerlessConnectionError, aiomqtt.MqttError) as err:
                log.warning("broker connection lost: %s", err)
            finally:
                self._link = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_SECONDS)

    async def _consume(self, link: LitterRobot4Link) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(link))
        try:
            async with aclosing(link.messages()) as stream:
                async for message in stream:
                    try:
                        self.ingest(message, _utcnow())
                    except Exception:
                        # The protocol is still being mapped upstream. A message we cannot
                        # interpret is already saved raw and must not end the session.
                        log.exception("could not process a robot message")
        finally:
            heartbeat.cancel()

    async def _heartbeat(self, link: LitterRobot4Link) -> None:
        while True:
            await link.request_state()
            for _ in range(STATE_POLL_SECONDS // TICK_SECONDS):
                await asyncio.sleep(TICK_SECONDS)
                self._settle_visits(_utcnow())

    def ingest(self, message: StateMessage | ActivityMessage, now: datetime) -> None:
        kind = "state" if isinstance(message, StateMessage) else "activity"
        self._store.add_message(now, kind, message.raw)
        self.last_seen = now

        fault_before = self.derived.globe_motor_fault
        update = derive.apply_message(self.derived, message, now)
        self.derived = update.state

        if isinstance(message, StateMessage):
            self.latest = message.state
            self._remember_litter(message.state)
        else:
            self._record_drawer_moves(message, now)
        fault_now = self.derived.globe_motor_fault
        if fault_now and fault_now != fault_before:
            self._store.add_event(now, "fault", {"code": fault_now})

        self._record_effects(update.effects, now)
        if update.changed:
            self._store.put_json("derived", self.derived.as_dict())
        self._settle_visits(now)
        self._refresh_alerts(now)

    def _record_effects(self, effects: Sequence[derive.Effect], now: datetime) -> None:
        for effect in effects:
            if isinstance(effect, derive.VisitEnded):
                self._pending_visits.append(effect)
            elif isinstance(effect, derive.CleanCycleCompleted):
                drawer = self.latest.waste_drawer_level if self.latest else None
                self._store.add_event(
                    now, "clean_cycle", {"count": effect.count, "drawer_percent": drawer}
                )
            elif isinstance(effect, derive.FirmwareChanged):
                self._store.add_event(now, "firmware", {"version": effect.version})

    def _record_drawer_moves(self, message: ActivityMessage, now: datetime) -> None:
        if not any(isinstance(e, DrawerBayMoved) for e in events_from_readings(message.readings)):
            return
        last = self._last_drawer_event
        if last is None or now - last >= DRAWER_EVENT_GAP:
            self._store.add_event(now, "drawer_moved", {})
            self._last_drawer_event = now

    def _settle_visits(self, now: datetime) -> None:
        """Save visits once the weight has had time to arrive; it often lands after the end."""
        waiting: list[derive.VisitEnded] = []
        for visit in self._pending_visits:
            if now - visit.ended < derive.VISIT_WEIGHT_GRACE:
                waiting.append(visit)
                continue
            self._store.add_event(
                visit.ended,
                "visit",
                {
                    "started": stamp(visit.started),
                    "duration_s": visit.duration_s,
                    "weight_lb": derive.visit_weight(self.derived, visit),
                },
            )
        self._pending_visits = waiting

    def _remember_litter(self, robot: LitterRobot4State) -> None:
        # Readings are withheld while the globe turns or a cat blocks the sensor. Keeping the
        # last real one stops the gauge from blanking out on every cycle.
        percent = self._litter_from(robot)
        if percent is not None:
            self._litter_percent = percent

    def _litter_from(self, robot: LitterRobot4State) -> int | None:
        # The firmware's own percentage is the device's answer. Our scale only stands in for it.
        if robot.litter_level_reported or robot.litter_level_mm is None:
            return robot.litter_level
        full_mm, empty_mm = derive.litter_scale(self.derived)
        return litter_level_percent_from_mm(
            robot.litter_level_mm, full_mm=full_mm, empty_mm=empty_mm
        )

    def online(self, now: datetime) -> bool:
        return self.last_seen is not None and now - self.last_seen < OFFLINE_AFTER

    def _refresh_alerts(self, now: datetime) -> None:
        snapshot = alerts.Snapshot(
            robot=self.latest,
            derived=self.derived,
            online=self.online(now),
            litter_percent=self._litter_percent,
        )
        raw = alerts.conditions(snapshot, alerts.AlertSettings(), now, self._alerts.active)
        self._alerts.update(raw, now)
        self._alert_details = {alert: details for alert, (_, details) in raw.items()}

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
        if self.latest is None:
            return []
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
            "cat_present": bool(robot.cat_detected),
            "drawer_percent": robot.waste_drawer_level,
            # After a Reset the gauge reads zero until the next cycle actually measures it.
            "drawer_is_estimate": bool(robot.is_dfi_reset_pending),
            "drawer_full": bool(robot.is_dfi_full) or bool(robot.drawer_full_lockout),
            "litter_percent": self._litter_percent,
            "clean_cycles": robot.odometer_clean_cycles,
            "wifi_rssi": robot.wifi_rssi,
            "esp_firmware": robot.esp_firmware,
            "pic_firmware": robot.pic_firmware,
            "last_visit_at": _iso(self.derived.last_cat_visit),
            "last_visit_duration_s": self.derived.last_visit_duration_s,
            "last_weight_lb": self.derived.cat_weight_lb,
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

    def _require_link(self) -> LitterRobot4Link:
        if self._link is None or not self.online(_utcnow()):
            raise RobotOffline
        return self._link

    async def press(self, command: Command) -> None:
        link = self._require_link()
        if self.latest is not None and self.latest.keypad_lockout:
            raise RobotLocked
        await link.publish(command)
        self._store.add_event(_utcnow(), "command", {"label": command.label})

    async def refresh(self) -> None:
        if self._link is None:
            raise RobotOffline
        await self._link.request_state()

    async def apply(self, commands: Sequence[Command]) -> None:
        link = self._require_link()
        async with self._settings_lock:
            # Read-back consumes a message stream, so it needs its own connection or it would
            # swallow the telemetry the main loop is recording.
            async with LitterRobot4Link(
                self._mqtt("whisker-local-settings"), self._config.serial
            ) as settings_link:
                for command in commands:
                    if not await settings_link.apply_setting(command):
                        raise SettingNotConfirmed(command.label)
        await link.request_state()
