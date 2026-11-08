from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

# The value older .env.example files shipped, which install.sh also treats as unset.
SAMPLE_ACCESS_KEY = "generated-by-install"


class InvalidAccessKey(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    serial: str
    access_key: str
    mqtt_host: str
    mqtt_port: int
    db_path: Path
    web_root: Path
    timezone: tzinfo = UTC
    demo: bool = False

    @property
    def photo_dir(self) -> Path:
        return self.db_path.parent / "cat-photos"

    @classmethod
    def from_env(cls) -> Config:
        access_key = os.environ["ACCESS_KEY"]
        # An empty key would otherwise match an empty Authorization header and open every route.
        if not access_key.strip() or access_key == SAMPLE_ACCESS_KEY:
            raise InvalidAccessKey("ACCESS_KEY is empty or the sample value, set a real secret")
        return cls(
            serial=os.environ["ROBOT_SERIAL"],
            access_key=access_key,
            mqtt_host=os.environ.get("MQTT_HOST", "broker"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            db_path=Path(os.environ.get("DB_PATH", "/data/whisker-local.db")),
            web_root=Path(os.environ.get("WEB_ROOT", "/srv/web")),
            timezone=ZoneInfo(os.environ.get("TZ") or "UTC"),
            demo=os.environ.get("DEMO_MODE", "") == "1",
        )
