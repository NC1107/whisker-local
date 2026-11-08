#!/usr/bin/env python3
"""End-to-end smoke test against a stack that is already running.

Starts tools/fake_robot.py against the real broker over real TLS, then drives the real server's
HTTP API. It proves the plumbing between broker, server and robot, and the LAN listener's
isolation. It says nothing about real firmware.
"""

from __future__ import annotations

import argparse
import asyncio
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
from whiskerless.mqtt import MqttSettings, build_tls_context

from whisker_local.certs import ensure

FAKE_ROBOT = Path(__file__).with_name("fake_robot.py")
CLEAN_COMMAND = "0x02010201"
BROKER_PORT = 8883
STRANGER_LISTEN_SECONDS = 2

results: list[bool] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{info}]" if info else ""), flush=True)


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


def lan_client(host: str, port: int, ca: Path, identifier: str) -> aiomqtt.Client:
    tls = build_tls_context(MqttSettings(host=host, ca_cert_path=str(ca)))
    return aiomqtt.Client(
        hostname=host, port=port, identifier=identifier, tls_context=tls, timeout=5
    )


async def stranger_topics(host: str, port: int, ca: Path, serial: str) -> list[str]:
    seen: list[str] = []
    async with lan_client(host, port, ca, "stranger") as client:
        await client.subscribe("prod/LR4/#", qos=0)
        await client.subscribe(f"prod/LR4/{serial}/#", qos=0)
        await client.publish(
            f"prod/LR4/{serial}/command", json.dumps({"serial": serial, "data": [CLEAN_COMMAND]})
        )

        async def drain() -> None:
            async for message in client.messages:
                seen.append(str(message.topic))

        task = asyncio.create_task(drain())
        await asyncio.sleep(STRANGER_LISTEN_SECONDS)
        task.cancel()
    return seen


async def wrong_ca_refused(host: str, port: int, ca: Path) -> bool:
    try:
        async with lan_client(host, port, ca, "wrong-ca"):
            return False
    except aiomqtt.MqttError:
        return True


async def run_checks(args: argparse.Namespace, scratch: Path) -> None:
    api = Api(args.base_url, args.access_key)
    try:
        status = await api.wait_status(lambda s: bool(s.get("online") and s.get("robot")), 30)
        online = bool(status and status.get("online") and status.get("robot"))
        check("server sees the robot over the real broker", online, json.dumps(status)[:200])
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
            "clean_cycle and command reach history", {"clean_cycle", "command"} <= kinds, str(kinds)
        )

        response = await api.client.patch(
            "/api/settings",
            json={
                "night_light_brightness": 77,
                "clean_cycle_wait_minutes": 15,
                "night_light_mode": "on",
            },
        )
        check(
            "settings confirmed by read-back",
            response.status_code == 200,
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
            "weekly schedule written and verified",
            response.status_code == 200,
            str(response.status_code),
        )

        await api.client.patch("/api/settings", json={"keypad_lockout": True})
        await api.wait_status(lambda s: s["robot"]["settings"]["keypad_lockout"] is True, 8)
        response = await api.client.post("/api/actions/clean")
        check(
            "locked robot refuses a press with a reason", response.status_code == 409, response.text
        )
        await api.client.patch("/api/settings", json={"keypad_lockout": False})
        await api.wait_status(lambda s: s["robot"]["settings"]["keypad_lockout"] is False, 8)

        cycles_before = (await api.status())["robot"]["clean_cycles"]
        seen = await stranger_topics(args.broker_host, BROKER_PORT, args.ca, args.serial)
        check("stranger on the LAN listener sees no robot traffic", seen == [], str(seen[:3]))
        status = await api.status()
        check(
            "stranger's clean command did not reach the robot",
            status["online"] and robot_is(status, status="ready", clean_cycles=cycles_before),
            str(status["robot"]["status"]),
        )

        other_ca = new_ca(scratch / "other-ca")
        check(
            "client with the wrong CA is refused",
            await wrong_ca_refused(args.broker_host, BROKER_PORT, other_ca),
        )
    finally:
        await api.close()


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


def start_fake_robot(args: argparse.Namespace) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 - argv is our own script plus operator-supplied flags
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
        ]
    )


def main() -> int:
    args = parse_args()
    robot = start_fake_robot(args)
    try:
        with tempfile.TemporaryDirectory(prefix="wl-smoke-") as scratch:
            asyncio.run(run_checks(args, Path(scratch)))
    finally:
        robot.terminate()
        robot.wait(timeout=10)
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
