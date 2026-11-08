#!/usr/bin/env python3
"""End-to-end smoke test against a stack that is already running.

Starts tools/fake_robot.py against the real broker over real TLS, then drives the real server's
HTTP API. It proves the plumbing between broker, server and robot, and the LAN listener's
isolation. It says nothing about real firmware.

The fake robot only publishes a state document when asked, like the captures of a real one, so a
recorded visit is also the proof that the server asks for state when the robot reports activity.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import aiomqtt
import httpx2
from PIL import Image
from whiskerless.mqtt import MqttSettings, build_tls_context

from whisker_local.certs import ensure

FAKE_ROBOT = Path(__file__).with_name("fake_robot.py")
CLEAN_COMMAND = "0x02010201"
BROKER_PORT = 8883
STRANGER_LISTEN_SECONDS = 1.5
OVERSIZED_PAYLOAD_BYTES = 256 * 1024
RETAIN_FLOOD_CLIENTS = 200
RETAIN_FLOOD_PAYLOAD_BYTES = 15 * 1024
VISIT_WAIT_SECONDS = 75
VISIT_WEIGHT_LB = 9.4
FORGED_RSSI = -1
FRESH_ROBOT = {"drawer_percent": 35, "clean_cycles": 1284}

results: list[bool] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append(ok)
    print(
        f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{info}]" if info else ""),
        flush=True,
    )


class Api:
    def __init__(self, base_url: str, access_key: str) -> None:
        self.client = httpx2.AsyncClient(
            base_url=base_url,
            timeout=20,
            headers={"Authorization": f"Bearer {access_key}"},
        )

    async def status(self) -> dict:
        return (await self.client.get("/api/status")).json()

    async def wait_status(self, predicate: Callable[[dict], bool], seconds: float) -> dict | None:
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            try:
                last = await self.status()
                if predicate(last):
                    return last
            except (httpx2.HTTPError, ValueError, KeyError, TypeError):
                pass
            await asyncio.sleep(0.25)
        return last

    async def close(self) -> None:
        await self.client.aclose()


def robot_is(snapshot: dict | None, **expected: object) -> bool:
    robot = (snapshot or {}).get("robot")
    return bool(robot) and all(robot.get(key) == value for key, value in expected.items())


def new_ca(out: Path) -> Path:
    # A second, unrelated CA is what a stranger with their own certificate would present.
    ensure(out, "127.0.0.1")
    return out / "ca.crt"


def command_message(serial: str) -> str:
    return json.dumps({"serial": serial, "data": [CLEAN_COMMAND]})


def lan_client(host: str, port: int, ca: Path, identifier: str) -> aiomqtt.Client:
    tls = build_tls_context(MqttSettings(host=host, ca_cert_path=str(ca)))
    return aiomqtt.Client(
        hostname=host, port=port, identifier=identifier, tls_context=tls, timeout=5
    )


async def listen(client: aiomqtt.Client, seconds: float) -> list[tuple[str, bool]]:
    seen: list[tuple[str, bool]] = []

    async def drain() -> None:
        async for message in client.messages:
            seen.append((str(message.topic), message.retain))

    task = asyncio.create_task(drain())
    await asyncio.sleep(seconds)
    task.cancel()
    return seen


async def stranger_topics(host: str, port: int, ca: Path, serial: str) -> list[tuple[str, bool]]:
    """What hostile clients with their own client ids can see, after trying to command.

    Forged telemetry is not tried: the ACL names the robot's topics, so anyone who knows the
    serial can publish them, which is the limit the docs describe.
    """
    seen: list[tuple[str, bool]] = []
    for identifier in ("stranger", f"{serial}/command", f"{serial}/x"):
        async with lan_client(host, port, ca, identifier) as client:
            for topic_filter in ("#", "prod/LR4/#", f"prod/LR4/{serial}/#"):
                await client.subscribe(topic_filter, qos=0)
            for retain in (False, True):
                await client.publish(
                    f"prod/LR4/{serial}/command", command_message(serial), retain=retain
                )
            seen += await listen(client, STRANGER_LISTEN_SECONDS)
    return seen


async def impersonator_leaves_nothing_behind(
    host: str, port: int, ca: Path, serial: str
) -> list[tuple[str, bool]]:
    """A client using the robot's own id tries to leave a command for when the robot reconnects."""
    command_topic = f"prod/LR4/{serial}/command"
    async with lan_client(host, port, ca, serial) as impersonator:
        for retain in (True, False):
            await impersonator.publish(command_topic, command_message(serial), qos=1, retain=retain)
    async with lan_client(host, port, ca, serial) as robot:
        await robot.subscribe(command_topic, qos=1)
        return await listen(robot, STRANGER_LISTEN_SECONDS)


async def send_oversized_state(host: str, port: int, ca: Path, serial: str) -> None:
    """A state document far past anything the robot sends must not reach the server."""
    padding = "x" * OVERSIZED_PAYLOAD_BYTES
    document = json.dumps({"robotStatus": 4, "wifiRssi": FORGED_RSSI, "padding": padding})
    try:
        async with lan_client(host, port, ca, serial) as client:
            await client.publish(f"prod/LR4/{serial}/state", document, qos=0, retain=True)
            # The broker cuts this connection. Reading from the client is what reports that;
            # nothing else retrieves the disconnect error, and asyncio prints it at exit.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(STRANGER_LISTEN_SECONDS):
                    async for _ in client.messages:
                        pass
    except aiomqtt.MqttError:
        pass


async def retained_flood_is_refused(host: str, port: int, ca: Path, serial: str) -> list[str]:
    """Made-up client ids try to park retained documents in the broker's memory."""
    document = json.dumps({"padding": "x" * RETAIN_FLOOD_PAYLOAD_BYTES})
    for i in range(RETAIN_FLOOD_CLIENTS):
        identifier = f"flood-{i}"
        async with lan_client(host, port, ca, identifier) as client:
            for leaf in ("state", "activity"):
                await client.publish(f"prod/LR4/{identifier}/{leaf}", document, retain=True)
    async with lan_client(host, port, ca, serial) as robot:
        await robot.subscribe("prod/LR4/#", qos=0)
        seen = await listen(robot, STRANGER_LISTEN_SECONDS)
    return [topic for topic, _ in seen]


async def wrong_ca_refused(host: str, port: int, ca: Path) -> bool:
    try:
        async with lan_client(host, port, ca, "wrong-ca"):
            return False
    except aiomqtt.MqttError:
        return True


async def run_checks(args: argparse.Namespace, scratch: Path, fake: FakeRobot) -> None:
    api = Api(args.base_url, args.access_key)
    try:
        health = await api.client.get("/api/health")
        check("health is ok", health.status_code == 200 and health.json() == {"ok": True})
        # The server keeps the last state it saw, so a stack that has been smoked before would
        # otherwise answer with the previous robot's numbers before the new one has spoken.
        status = await api.wait_status(
            lambda s: bool(s.get("online")) and robot_is(s, **FRESH_ROBOT), 30
        )
        online = bool(status and status.get("online") and status.get("robot"))
        check(
            "server sees the robot over the real broker",
            online,
            json.dumps(status)[:200],
        )
        if not online:
            return
        robot = status["robot"]

        check(
            "decoded fields",
            robot["status"] == "ready"
            and robot["drawer_percent"] == 35
            and robot["litter_percent"] == 62
            and robot["esp_firmware"] == "1.1.75",
            str(robot["status_label"]),
        )

        cycles_before = robot["clean_cycles"]
        response = await api.client.post("/api/actions/clean")
        check("clean accepted", response.status_code == 202, str(response.status_code))
        status = await api.wait_status(lambda s: robot_is(s, status="clean_cycle"), 10)
        check("robot reports cleaning", robot_is(status, status_label="Cleaning"))
        status = await api.wait_status(lambda s: robot_is(s, status="ready"), 15)
        check(
            "robot returns to ready and the odometer moved",
            robot_is(status, status="ready", clean_cycles=cycles_before + 1),
        )
        events = (await api.client.get("/api/events")).json()
        kinds = {event["kind"] for event in events}
        check(
            "clean_cycle and command reach history",
            {"clean_cycle", "command"} <= kinds,
            str(kinds),
        )

        response = await api.client.patch(
            "/api/settings",
            json={
                "night_light_brightness": 77,
                "clean_cycle_wait_minutes": 15,
                "night_light_mode": "on",
            },
        )
        answered = response.json().get("robot", {}).get("settings", {})
        check(
            "settings confirmed by read-back, and the answer already holds the new values",
            response.status_code == 200
            and answered.get("night_light_brightness") == 77
            and answered.get("clean_cycle_wait_minutes") == 15
            and answered.get("night_light_mode") == "on",
            f"{response.status_code} {response.text[:120]}",
        )
        status = await api.wait_status(
            lambda s: s["robot"]["settings"]["night_light_brightness"] == 77, 8
        )
        settings = (status or {}).get("robot", {}).get("settings", {})
        check(
            "settings reflected",
            settings.get("night_light_mode") == "on"
            and settings.get("clean_cycle_wait_minutes") == 15,
            str(settings),
        )

        response = await api.client.patch(
            "/api/settings",
            json={"wake_minute": 420, "sleep_minute": 1380, "sleep_enabled": True},
        )
        check(
            "weekly schedule written, verified and armed",
            response.status_code == 200
            and response.json()["robot"]["settings"]["sleep_enabled"] is True,
            str(response.status_code),
        )

        response = await api.client.patch("/api/settings", json={"keypad_lockout": True})
        check(
            "lock switch answer is not stale",
            response.json()["robot"]["settings"]["keypad_lockout"] is True,
        )
        response = await api.client.post("/api/actions/clean")
        check(
            "locked robot refuses a press with a reason",
            response.status_code == 409,
            response.text,
        )
        response = await api.client.patch("/api/settings", json={"keypad_lockout": False})
        check(
            "unlock answer is not stale",
            response.json()["robot"]["settings"]["keypad_lockout"] is False,
        )

        before = (await api.status())["robot"]
        seen = await stranger_topics(args.broker_host, BROKER_PORT, args.ca, args.serial)
        check(
            "strangers on the LAN listener see no robot traffic",
            seen == [],
            str(seen[:3]),
        )
        await asyncio.sleep(STRANGER_LISTEN_SECONDS)
        status = await api.status()
        check(
            "strangers' commands did not reach the robot or the server",
            status["online"]
            and robot_is(
                status,
                status="ready",
                clean_cycles=before["clean_cycles"],
                wifi_rssi=before["wifi_rssi"],
            ),
            str(status["robot"]),
        )

        other_ca = new_ca(scratch / "other-ca")
        check(
            "client with the wrong CA is refused",
            await wrong_ca_refused(args.broker_host, BROKER_PORT, other_ca),
        )

        fake.stop()
        left_behind = await impersonator_leaves_nothing_behind(
            args.broker_host, BROKER_PORT, args.ca, args.serial
        )
        check(
            "a client using the robot's id cannot leave a command for it",
            left_behind == [],
            str(left_behind),
        )

        await send_oversized_state(args.broker_host, BROKER_PORT, args.ca, args.serial)
        status = await api.status()
        check(
            "an oversized state document is dropped by the broker",
            (status.get("robot") or {}).get("wifi_rssi") != FORGED_RSSI,
            str((status.get("robot") or {}).get("wifi_rssi")),
        )

        parked = await retained_flood_is_refused(
            args.broker_host, BROKER_PORT, args.ca, args.serial
        )
        check("made-up client ids cannot park retained messages", parked == [], str(parked[:3]))
        check("the broker survives the retained flood", (await api.status())["online"] is not None)

        known_visits = {visit["id"] for visit in await visits(api)}
        fake.start("--visit-after", "3")
        visit = await wait_for_visit(api, known_visits, VISIT_WAIT_SECONDS)
        detail = (visit or {}).get("detail", {})
        check(
            "a visit is recorded although the robot never volunteers its state",
            bool(visit)
            and abs((detail.get("weight_lb") or 0) - VISIT_WEIGHT_LB) < 0.05
            and 0 < detail.get("duration_s", 0) <= 30
            and bool(visit.get("cursor")),
            json.dumps(visit),
        )
        if visit:
            await check_cat_profile(api, visit)
    finally:
        await api.close()


def sample_picture() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2400, 1200), "orange").save(out, "PNG")
    return out.getvalue()


async def check_cat_profile(api: Api, visit: dict) -> None:
    """Profile, photo and insights through the real server, with the visit the robot just made."""
    weight = visit["detail"]["weight_lb"]
    response = await api.client.post(
        "/api/cats",
        json={
            "name": f"Smoke {os.getpid()}",
            "breed": "Sample breed",
            "birthday": "2023-01-15",
            "sex": "unknown",
            "reference_weight_lb": weight,
        },
    )
    cat = response.json()
    check("a cat is made with a profile", response.status_code == 201, response.text[:150])

    response = await api.client.put(f"/api/cats/{cat['id']}/photo", content=sample_picture())
    fetched = await api.client.get(f"/api/cats/{cat['id']}/photo")
    stored = Image.open(io.BytesIO(fetched.content)) if fetched.status_code == 200 else None
    check(
        "a photo is stored as a downscaled jpeg",
        response.status_code == 200
        and stored is not None
        and stored.format == "JPEG"
        and max(stored.size) == 1600,
        f"{response.status_code} {fetched.status_code}",
    )
    refused = await api.client.put(f"/api/cats/{cat['id']}/photo", content=b"not a picture")
    check("a file that is not a picture is refused", refused.status_code == 415)

    status = await api.status()
    mine = next((c for c in status["cats"] if c["id"] == cat["id"]), {})
    check(
        "the new cat takes the visit and status carries its numbers",
        mine.get("summary", {}).get("latest_weight_lb") == weight
        and mine.get("has_photo") is True
        and status.get("demo") is False,
        json.dumps(mine)[:200],
    )

    report = (
        await api.client.get(f"/api/cats/{cat['id']}/insights", params={"period": "month"})
    ).json()
    check(
        "per-cat insights show today's visit on the last day of the month view",
        len(report.get("points", [])) == 30 and bool(report["visits"]["series"][-1]),
        json.dumps(report)[:200],
    )
    week = (
        await api.client.get(f"/api/cats/{cat['id']}/insights", params={"period": "week"})
    ).json()
    check(
        "the week view is the seven days before today",
        len(week.get("points", [])) == 7 and week["points"][-1] < report["points"][-1],
        json.dumps(week)[:200],
    )


async def visits(api: Api) -> list[dict]:
    try:
        return (await api.client.get("/api/visits", params={"days": 1})).json()
    except (httpx2.HTTPError, ValueError):
        return []


async def wait_for_visit(api: Api, known: set[int], seconds: float) -> dict | None:
    """The first visit that was not already in the history before the robot was asked for one."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for visit in await visits(api):
            if visit["id"] not in known:
                return visit
        await asyncio.sleep(1)
    return None


def parse_args() -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=env("BASE_URL"), help="dashboard URL, or env BASE_URL"
    )
    parser.add_argument("--broker-host", default=env("BROKER_HOST"), help="or env BROKER_HOST")
    parser.add_argument("--ca", type=Path, default=env("CA_CERT"), help="CA file, or env CA_CERT")
    parser.add_argument("--serial", default=env("ROBOT_SERIAL"), help="or env ROBOT_SERIAL")
    parser.add_argument("--access-key", default=env("ACCESS_KEY"), help="or env ACCESS_KEY")
    parser.add_argument("--cycle-seconds", type=float, default=3.0)
    args = parser.parse_args()
    missing = [
        flag
        for flag, value in (
            ("--base-url", args.base_url),
            ("--broker-host", args.broker_host),
            ("--ca", args.ca),
            ("--serial", args.serial),
            ("--access-key", args.access_key),
        )
        if not value
    ]
    if missing:
        parser.error(f"missing {', '.join(missing)} (or the matching environment variable)")
    return args


class FakeRobot:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.process: subprocess.Popen | None = None

    def start(self, *extra: str) -> None:
        args = self.args
        self.process = subprocess.Popen(  # noqa: S603 - argv is our own script plus operator flags
            [
                sys.executable,
                str(FAKE_ROBOT),
                "--host",
                args.broker_host,
                "--ca",
                str(args.ca),
                "--serial",
                args.serial,
                "--cycle-seconds",
                str(args.cycle_seconds),
                *extra,
            ]
        )

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process = None


def main() -> int:
    args = parse_args()
    fake = FakeRobot(args)
    fake.start()
    try:
        with tempfile.TemporaryDirectory(prefix="wl-smoke-") as scratch:
            asyncio.run(run_checks(args, Path(scratch), fake))
    finally:
        fake.stop()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
