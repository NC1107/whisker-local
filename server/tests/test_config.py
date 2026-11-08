from __future__ import annotations

import pytest

from whisker_local import __main__ as entrypoint
from whisker_local.config import Config, InvalidAccessKey


@pytest.mark.parametrize("key", ["", "   ", "generated-by-install"])
def test_from_env_refuses_unusable_access_keys(monkeypatch, key):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", key)
    with pytest.raises(InvalidAccessKey):
        Config.from_env()


def test_main_exits_with_a_message_on_an_empty_key(monkeypatch):
    monkeypatch.setenv("ROBOT_SERIAL", "LR4C000001")
    monkeypatch.setenv("ACCESS_KEY", "")
    with pytest.raises(SystemExit, match="ACCESS_KEY"):
        entrypoint.main()
