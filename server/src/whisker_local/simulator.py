"""A stand-in Litter-Robot 4 for development and for reviewing the app with sample data.

With ``--ca`` it connects the way the real robot does: TLS to port 8883, no login, client id set
to the serial, trusting only the CA it was given. Without it, it talks plain MQTT to the broker's
internal port 1883, which is how the demo service inside the compose network reaches the broker.
It answers register reads and walks through visits and clean cycles using the status codes
captured from real robots.

It talks the way the captures in docs/devices/litter-robot-4/capture-notebook.md show a real one
does. Status and settings change silently. Only the activity stream says that something happened,
with the sensor events, weight and duration registers, and a state document goes out only when
one is requested or on the robot's slow periodic cadence. A server that waits for state to arrive
unprompted therefore sees nothing here, as it would with the real thing.

It proves the plumbing and nothing about real firmware.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random

import aiomqtt
from whiskerless.devices.litter_robot_4 import const
from whiskerless.mqtt import MqttSettings, build_tls_context

READY = 4
CAT_INSIDE = 25
COUNTDOWN = 7
CLEANING = 10
EMPTYING = "robot_empty"

# Activity registers and values as captured. 0x37 is an event, not the catDetect field: the high
# nibble names the sensor (1 curtain, 2 scale) and the low bit says detected or clear.
REG_CAT_DETECT = 0x37
CURTAIN_DETECTED, CURTAIN_CLEAR = 0x11, 0x10
SCALE_DETECTED, SCALE_CLEAR = 0x21, 0x20
REG_CAT_WEIGHT = 0x09
REG_VISIT_DURATION = 0xBC
REG_ODOMETER = 0x3E
REG_DRAWER_LEVEL = 0x43
REG_SESSION_START = 0x33
SESSION_START_VALUE = 0x22
PERIODIC_STATE_SECONDS = 300.0
VISIT_SECONDS = 4.0
STARTING_DRAWER_PERCENT = 35


def initial_state() -> dict:
    state = {
        "robotStatus": READY,
        "robotCycleState": 1,
        "robotCycleStatus": 1,
        "litterLevelPercentage": 62,
        "litterLevel": 455,
        "DFILevelPercent": STARTING_DRAWER_PERCENT,
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
        self.parked = False
        self.running: set[asyncio.Task] = set()

    async def send_state(self) -> None:
        await self.client.publish(f"prod/LR4/{self.serial}/state", json.dumps(self.state), qos=1)

    async def send_reading(self, register: int, value: int) -> None:
        await self.send_activity(f"0x{register:02X}{value:04X}")

    async def send_activity(self, *codes: str) -> None:
        await self.client.publish(
            f"prod/LR4/{self.serial}/activity", json.dumps({"data": list(codes)}), qos=1
        )

    async def run_random_visits(self, every: float, weights: list[float]) -> None:
        """Visits at uneven intervals, so the history does not look like a metronome."""
        while True:
            await asyncio.sleep(random.uniform(0.5, 1.5) * every)  # noqa: S311
            weight = random.choice(weights) + random.gauss(0, 0.12)  # noqa: S311
            await self.run_visit(weight, VISIT_SECONDS)

    async def run_visit_after(self, delay: float, weight_lb: float) -> None:
        await asyncio.sleep(delay)
        await self.run_visit(weight_lb, VISIT_SECONDS)

    async def run_cycle(self) -> None:
        async with self.busy:
            cycles = self.state["odometerCleanCycles"] + 1
            self.state.update(robotStatus=CLEANING, odometerCleanCycles=cycles)
            await self.send_reading(REG_ODOMETER, cycles)
            await asyncio.sleep(self.cycle_seconds)
            drawer = min(self.state["DFILevelPercent"] + 2, 100)
            self.state.update(robotStatus=READY, DFILevelPercent=drawer)
            await self.send_reading(REG_DRAWER_LEVEL, drawer)

    async def run_empty(self) -> None:
        """Dump the globe into the drawer, then park until a Cycle or Reset press brings it home."""
        async with self.busy:
            self.state.update(robotStatus=EMPTYING)
            await asyncio.sleep(self.cycle_seconds)
            drawer = min(self.state["DFILevelPercent"] + 20, 100)
            self.state.update(DFILevelPercent=drawer)
            await self.send_reading(REG_DRAWER_LEVEL, drawer)
            self.parked = True
            await self.send_state()

    async def bring_home(self) -> None:
        self.parked = False
        self.state.update(robotStatus=READY)
        await self.send_state()

    async def run_visit(self, weight_lb: float, seconds: float) -> None:
        async with self.busy:
            self.state.update(robotStatus=CAT_INSIDE, catDetect=3)
            await self.send_reading(REG_CAT_DETECT, CURTAIN_DETECTED)
            await self.send_reading(REG_CAT_DETECT, SCALE_DETECTED)
            await asyncio.sleep(seconds * 0.7)
            await self.send_reading(REG_CAT_WEIGHT, round(weight_lb * 100))
            await asyncio.sleep(seconds * 0.3)
            self.state.update(robotStatus=COUNTDOWN, catDetect=0)
            await self.send_reading(REG_CAT_DETECT, CURTAIN_CLEAR)
            await self.send_reading(REG_CAT_DETECT, SCALE_CLEAR)
            await self.send_reading(REG_VISIT_DURATION, round(seconds))
        await asyncio.sleep(self.cycle_seconds)
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
            if self.parked and value in (const.PANEL_BUTTON_CYCLE, const.PANEL_BUTTON_RESET):
                self.spawn(self.bring_home())
            elif value == const.PANEL_BUTTON_CYCLE:
                self.spawn(self.run_cycle())
            elif value == const.PANEL_BUTTON_EMPTY:
                self.spawn(self.run_empty())
        elif register in self.registers:
            self.state[self.registers[register]] = value
            await self.send_activity(f"0x{register:02X}{value:04X}")

    def spawn(self, work) -> None:
        task = asyncio.create_task(work)
        self.running.add(task)
        task.add_done_callback(self.running.discard)

    async def publish_periodically(self, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            await self.send_state()

    async def listen(self) -> None:
        await self.client.subscribe(f"prod/LR4/{self.serial}/command", qos=1)
        await self.send_reading(REG_SESSION_START, SESSION_START_VALUE)
        async for message in self.client.messages:
            body = json.loads(message.payload)
            if body.get("serial") != self.serial:
                continue
            for code in body.get("data", []):
                await self.handle(code)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ca", help="CA file; leave out to use plain MQTT on the internal port")
    parser.add_argument("--port", type=int, help="default 8883 with --ca, 1883 without")
    parser.add_argument("--serial", default="LR4C000001")
    parser.add_argument("--cycle-seconds", type=float, default=6.0)
    parser.add_argument("--visit-every", type=float, default=0, help="seconds, 0 for never")
    parser.add_argument(
        "--visit-after", type=float, default=0, help="one visit after this many seconds"
    )
    parser.add_argument("--state-every", type=float, default=PERIODIC_STATE_SECONDS)
    parser.add_argument("--weight", type=float, default=9.4)
    parser.add_argument(
        "--weights",
        type=lambda text: [float(part) for part in text.split(",")],
        help="comma separated cat weights; with --visit-every, visits pick one of them at random",
    )
    return parser.parse_args(argv)


# Real firmware sends its AWS SDK banner as the username, and broker ACLs treat that differently
# from a client with no username, so the simulator has to send it too.
ROBOT_USERNAME = "?SDK=C&Version=3.0.1"


async def run(args: argparse.Namespace) -> None:
    tls = build_tls_context(MqttSettings(host=args.host, ca_cert_path=args.ca)) if args.ca else None
    port = args.port or (8883 if args.ca else 1883)
    async with aiomqtt.Client(
        hostname=args.host,
        port=port,
        identifier=args.serial,
        username=ROBOT_USERNAME,
        tls_context=tls,
    ) as client:
        robot = FakeRobot(client, args.serial, args.cycle_seconds)
        robot.spawn(robot.publish_periodically(args.state_every))
        if args.visit_after:
            robot.spawn(robot.run_visit_after(args.visit_after, args.weight))
        if args.visit_every and args.weights:
            robot.spawn(robot.run_random_visits(args.visit_every, args.weights))
        listener = asyncio.create_task(robot.listen())
        while args.visit_every and not args.weights:
            await asyncio.sleep(args.visit_every)
            await robot.run_visit(args.weight, VISIT_SECONDS)
        await listener


def main(argv: list[str] | None = None) -> None:
    asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    main()
