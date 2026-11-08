from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from whiskerless.devices.litter_robot_4 import Command, commands, const

from .bridge import RobotBridge, RobotLocked, RobotOffline, SettingNotConfirmed
from .config import Config
from .store import Store

_NIGHT_LIGHT_MODES = {name: value for value, name in const.NIGHT_LIGHT_MODE.items()}

Minute = Annotated[int, Field(ge=0, le=1439)]


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    night_light_mode: Literal["off", "on", "auto"] | None = None
    night_light_brightness: Annotated[int, Field(ge=0, le=100)] | None = None
    clean_cycle_wait_minutes: Literal[3, 7, 15, 25, 30] | None = None
    keypad_lockout: bool | None = None
    sleep_enabled: bool | None = None
    sleep_minute: Minute | None = None
    wake_minute: Minute | None = None


def _setting_commands(patch: SettingsPatch) -> list[Command]:
    out: list[Command] = []
    if patch.night_light_mode is not None:
        out.append(commands.set_night_light_mode(_NIGHT_LIGHT_MODES[patch.night_light_mode]))
    if patch.night_light_brightness is not None:
        out.append(commands.set_night_light_brightness(patch.night_light_brightness))
    if patch.clean_cycle_wait_minutes is not None:
        out.append(commands.set_clean_cycle_wait_minutes(patch.clean_cycle_wait_minutes))
    if patch.keypad_lockout is not None:
        out.append(commands.set_keypad_lockout(patch.keypad_lockout))
    if patch.sleep_enabled is not None:
        out.append(commands.set_weekday_sleep_enabled(patch.sleep_enabled))
    if patch.sleep_minute is not None:
        out.extend(commands.set_panel_sleep_times(patch.sleep_minute))
    if patch.wake_minute is not None:
        out.extend(commands.set_panel_wake_times(patch.wake_minute))
    return out


def create_app(config: Config, store: Store, bridge: RobotBridge, *, run_bridge: bool = True):
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        task = asyncio.create_task(bridge.run()) if run_bridge else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            store.close()

    app = FastAPI(title="whisker-local", docs_url=None, redoc_url=None, lifespan=lifespan)

    expected = config.access_key.encode()

    def require_key(authorization: Annotated[str, Header()] = "") -> None:
        supplied = authorization.removeprefix("Bearer ").encode()
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Wrong access key")

    @app.exception_handler(RobotOffline)
    async def on_offline(_: Request, __: RobotOffline) -> JSONResponse:
        return JSONResponse({"detail": "The robot is offline."}, status_code=503)

    @app.exception_handler(RobotLocked)
    async def on_locked(_: Request, __: RobotLocked) -> JSONResponse:
        return JSONResponse(
            {"detail": "Control lock is on. Turn it off to use the robot from here."},
            status_code=409,
        )

    @app.exception_handler(SettingNotConfirmed)
    async def on_unconfirmed(_: Request, __: SettingNotConfirmed) -> JSONResponse:
        return JSONResponse(
            {"detail": "The robot did not confirm that change. Try again."}, status_code=504
        )

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])

    @api.get("/status")
    async def status() -> dict[str, Any]:
        return bridge.status()

    @api.get("/events")
    async def events(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        return store.events(limit=limit, before=before)

    @api.get("/visits")
    async def visits(days: Annotated[int, Query(ge=1, le=3650)] = 30) -> list[dict[str, Any]]:
        return store.visits_since(datetime.now(UTC) - timedelta(days=days))

    @api.post("/actions/clean", status_code=202)
    async def clean() -> dict[str, bool]:
        await bridge.press(commands.clean_cycle())
        return {"sent": True}

    @api.post("/actions/reset", status_code=202)
    async def reset() -> dict[str, bool]:
        await bridge.press(commands.panel_reset())
        return {"sent": True}

    @api.post("/actions/refresh", status_code=202)
    async def refresh() -> dict[str, bool]:
        await bridge.refresh()
        return {"sent": True}

    @api.patch("/settings")
    async def settings(patch: SettingsPatch) -> dict[str, Any]:
        await bridge.apply(_setting_commands(patch))
        return bridge.status()

    app.include_router(api)
    if config.web_root.is_dir():
        app.mount("/", StaticFiles(directory=config.web_root, html=True), name="web")
    return app


def create_app_from_env() -> FastAPI:
    config = Config.from_env()
    store = Store(config.db_path)
    return create_app(config, store, RobotBridge(config, store))
