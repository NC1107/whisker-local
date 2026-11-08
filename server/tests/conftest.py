from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whisker_local.api import create_app
from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store


@pytest.fixture
def parts(tmp_path: Path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>dashboard</title>")
    config = Config("LR4C000001", "right-key", "broker", 1883, tmp_path / "h.db", web)
    store = Store(config.db_path)
    return config, store, RobotBridge(config, store)


@pytest.fixture
def client(parts):
    config, store, bridge = parts
    with TestClient(create_app(config, store, bridge, run_bridge=False)) as test_client:
        yield test_client
