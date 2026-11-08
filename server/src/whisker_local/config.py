from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    serial: str
    access_key: str
    mqtt_host: str
    mqtt_port: int
    db_path: Path
    web_root: Path

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            serial=os.environ["ROBOT_SERIAL"],
            access_key=os.environ["ACCESS_KEY"],
            mqtt_host=os.environ.get("MQTT_HOST", "broker"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            db_path=Path(os.environ.get("DB_PATH", "/data/whisker-local.db")),
            web_root=Path(os.environ.get("WEB_ROOT", "/srv/web")),
        )
