from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from whisker_local import simulator
from whisker_local.simulator import FakeRobot, parse_args, run


def test_without_a_ca_the_simulator_uses_plain_mqtt_on_the_internal_port():
    args = parse_args(["--host", "broker"])
    assert args.ca is None and args.port is None


def test_it_keeps_the_tls_defaults_when_given_a_ca():
    args = parse_args(["--host", "10.0.0.5", "--ca", "ca.crt", "--serial", "LR4C000009"])
    assert (args.ca, args.serial) == ("ca.crt", "LR4C000009")


def test_weights_are_a_comma_separated_list():
    assert parse_args(["--host", "b", "--weights", "10.1,9"]).weights == [10.1, 9.0]


def test_a_host_is_required():
    with pytest.raises(SystemExit):
        parse_args([])


class FakeClient:
    def __init__(self, *, hostname, port, identifier, username, tls_context):
        self.settings = SimpleNamespace(
            hostname=hostname, port=port, identifier=identifier, username=username, tls=tls_context
        )
        self.published: list[tuple[str, dict]] = []
        self.messages = self._never()

    @staticmethod
    async def _never():
        await asyncio.Event().wait()
        yield

    async def __aenter__(self):
        FakeClient.last = self
        return self

    async def __aexit__(self, *_):
        return False

    async def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))

    async def subscribe(self, *_args, **_kwargs):
        return None


def test_running_without_a_ca_connects_without_tls_to_1883(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(simulator.aiomqtt, "Client", FakeClient)
    args = parse_args(["--host", "broker", "--serial", "LR4C000001"])

    async def go():
        task = asyncio.create_task(run(args))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())

    settings = FakeClient.last.settings
    assert (settings.hostname, settings.port, settings.tls) == ("broker", 1883, None)
    assert settings.identifier == "LR4C000001"
    assert settings.username == simulator.ROBOT_USERNAME


def test_random_visits_pick_one_of_the_given_weights(monkeypatch: pytest.MonkeyPatch):
    seen: list[float] = []

    async def record(self, weight_lb, seconds):
        seen.append(weight_lb)

    monkeypatch.setattr(FakeRobot, "run_visit", record)
    robot = FakeRobot(client=None, serial="LR4C000001", cycle_seconds=0)

    async def go():
        task = asyncio.create_task(robot.run_random_visits(0.001, [10.0, 6.0]))
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(go())

    assert seen
    assert all(abs(w - 10.0) < 1 or abs(w - 6.0) < 1 for w in seen)


def test_empty_parks_the_globe_until_a_cycle_press_brings_it_home():
    robot = FakeRobot(client=None, serial="LR4C000001", cycle_seconds=0)
    sent: list[dict] = []

    async def send_state():
        sent.append(dict(robot.state))

    async def send_reading(*_):
        return None

    async def send_activity(*_):
        return None

    robot.send_state = send_state
    robot.send_reading = send_reading
    robot.send_activity = send_activity

    async def go():
        await robot.handle("0x02010801")
        await asyncio.gather(*robot.running)
        assert robot.parked
        assert sent[-1]["robotStatus"] == simulator.EMPTYING
        assert robot.state["DFILevelPercent"] > simulator.STARTING_DRAWER_PERCENT
        await robot.handle("0x02010201")
        await asyncio.gather(*robot.running)

    asyncio.run(go())

    assert not robot.parked
    assert sent[-1]["robotStatus"] == simulator.READY
