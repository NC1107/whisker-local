#!/usr/bin/env python3
"""A stand-in Litter-Robot 4 for development.

Connects the way the real robot does: TLS to port 8883, no login, client id set to the serial,
trusting only the CA it was given. It answers state requests and register reads, and walks through
visits and clean cycles using the status codes captured from real robots.

It proves the plumbing and nothing about real firmware.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aiomqtt
from whiskerless.devices.litter_robot_4 import const
from whiskerless.mqtt import MqttSettings, build_tls_context

READY = 4
CAT_INSIDE = 25
COUNTDOWN = 7
CLEANING = 10


def initial_state() -> dict:
    state = {
        "robotStatus": READY,
        "robotCycleState": 1,
        "robotCycleStatus": 1,
        "litterLevelPercentage": 62,
        "litterLevel": 455,
        "DFILevelPercent": 35,
        "nightLightMode": 2,
        "nightLightBrightness": 50,
        "cleanCycleWaitTime": 7,
        "isKeypadLockout": 0,
        "isPanelSleepMode": 0,
        "panelSleepTime": 1320,
        "panelWakeTime": 360,
        "weekdaySleepModeEnabled": 0,
        "unitPowerStatus": 1,
        "isBonnetRemoved": 0,
        "isDFIFull": 0,
        "DFINumberOfCycles": 1280,
        "odometerCleanCycles": 1284,
        "odometerEmptyCycles": 12,
        "catDetect": 0,
        "sleepStatus": 0,
        "wifiRssi": -54,
        "espFirmware": "1.1.75",
        "picFirmwareVersion": "10500.3072.2.89",
        "laserBoardVersion": "5.0.2.1",
    }
    for day in const.WEEKDAYS:
        state[f"sleepTime{day.capitalize()}"] = 1320
        state[f"wakeTime{day.capitalize()}"] = 360
    return state


def writable_registers() -> dict[int, str]:
    registers = {
        const.Register.CLEAN_CYCLE_WAIT_TIME: "cleanCycleWaitTime",
        const.Register.IS_KEYPAD_LOCKOUT: "isKeypadLockout",
        const.Register.NIGHT_LIGHT_MODE: "nightLightMode",
        const.Register.NIGHT_LIGHT_BRIGHTNESS: "nightLightBrightness",
        const.Register.WEEKDAY_SLEEP_MODE_ENABLED: "weekdaySleepModeEnabled",
    }
    for day, (sleep_register, wake_register) in const.WEEKDAY_SCHEDULE_REGS.items():
        registers[sleep_register] = f"sleepTime{day.capitalize()}"
        registers[wake_register] = f"wakeTime{day.capitalize()}"
    return registers


class FakeRobot:
    def __init__(self, client: aiomqtt.Client, serial: str, cycle_seconds: float) -> None:
        self.client = client
        self.serial = serial
        self.cycle_seconds = cycle_seconds
        self.state = initial_state()
        self.registers = writable_registers()
        self.busy = asyncio.Lock()
        self.running: set[asyncio.Task] = set()

    async def send_state(self, **changes) -> None:
        self.state.update(changes)
        await self.client.publish(
            f"prod/LR4/{self.serial}/state", json.dumps(self.state), qos=1
        )

    async def send_activity(self, *codes: str) -> None:
        await self.client.publish(
            f"prod/LR4/{self.serial}/activity", json.dumps({"data": list(codes)}), qos=1
        )

    async def run_cycle(self) -> None:
        async with self.busy:
            await self.send_state(
                robotStatus=CLEANING, odometerCleanCycles=self.state["odometerCleanCycles"] + 1
            )
            await asyncio.sleep(self.cycle_seconds)
            await self.send_state(
                robotStatus=READY, DFILevelPercent=min(self.state["DFILevelPercent"] + 2, 100)
            )

    async def run_visit(self, weight_lb: float, seconds: float) -> None:
        async with self.busy:
            await self.send_state(robotStatus=CAT_INSIDE, catDetect=3)
            await asyncio.sleep(seconds * 0.7)
            await self.send_activity(f"0x09{round(weight_lb * 100):04X}")
            await asyncio.sleep(seconds * 0.3)
            await self.send_state(robotStatus=COUNTDOWN, catDetect=0)
            await self.send_activity(f"0xBC{round(seconds):04X}")
        await self.run_cycle()

    async def handle(self, code: str) -> None:
        kind, register, value = code[3], int(code[4:6], 16), int(code[6:10], 16)
        if kind == "1":
            field = self.registers.get(register)
            current = int(self.state.get(field, 0)) if field else 0
            await self.send_activity(f"0x{register:02X}{current:04X}")
        elif register == const.Opcode.REQUEST_STATE:
            await self.send_state()
        elif register == const.Register.PANEL_BUTTON:
            if self.state["isKeypadLockout"]:
                return
            await self.send_activity(f"0x01{value:04X}")
            if value == const.PANEL_BUTTON_CYCLE:
                task = asyncio.create_task(self.run_cycle())
                self.running.add(task)
                task.add_done_callback(self.running.discard)
        elif register in self.registers:
            self.state[self.registers[register]] = value
            await self.send_activity(f"0x{register:02X}{value:04X}")
            await self.send_state()

    async def listen(self) -> None:
        await self.client.subscribe(f"prod/LR4/{self.serial}/command", qos=1)
        await self.send_state()
        async for message in self.client.messages:
            body = json.loads(message.payload)
            if body.get("serial") != self.serial:
                continue
            for code in body.get("data", []):
                await self.handle(code)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--serial", default="LR4C000001")
    parser.add_argument("--cycle-seconds", type=float, default=6.0)
    parser.add_argument("--visit-every", type=float, default=0, help="seconds, 0 for never")
    parser.add_argument("--weight", type=float, default=9.4)
    args = parser.parse_args()

    tls = build_tls_context(MqttSettings(host=args.host, ca_cert_path=args.ca))
    async with aiomqtt.Client(
        hostname=args.host, port=8883, identifier=args.serial, tls_context=tls
    ) as client:
        robot = FakeRobot(client, args.serial, args.cycle_seconds)
        listener = asyncio.create_task(robot.listen())
        while args.visit_every:
            await asyncio.sleep(args.visit_every)
            await robot.run_visit(args.weight, 4.0)
        await listener


if __name__ == "__main__":
    asyncio.run(main())
