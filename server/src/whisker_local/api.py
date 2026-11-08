from __future__ import annotations

import asyncio
import logging
import re
import secrets
import shutil
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from starlette.background import BackgroundTask
from whiskerless.devices.litter_robot_4 import Command, commands, const

from . import alert_settings
from .bridge import (
    BrokerUnavailable,
    LitterNotReadable,
    PressNotAcknowledged,
    RobotBridge,
    RobotLocked,
    RobotOffline,
    SettingNotConfirmed,
    StateNotFresh,
)
from .cat_insights import MAX_OFFSET, CatInsights, Period
from .cats import (
    MAX_SERIES_DAYS,
    CatArchived,
    CatNameTaken,
    CatNotFound,
    CatTracker,
    VisitNotFound,
)
from .config import Config
from .demo import ensure_mode
from .export import visits_csv
from .insights import Granularity, Insights, InvalidRange
from .maintenance import (
    EARLIEST_DONE_ON,
    MaintenanceInFuture,
    MaintenanceLog,
    MaintenanceNotFound,
)
from .photos import MAX_UPLOAD_BYTES, CatPhotos, ImageTooLarge, NotAnImage, normalise
from .store import InvalidCursor, Store

log = logging.getLogger(__name__)

_NIGHT_LIGHT_MODES = {name: value for value, name in const.NIGHT_LIGHT_MODE.items()}

Minute = Annotated[int, Field(ge=0, le=1439)]
CatName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
CatWeight = Annotated[float, Field(ge=2, le=40)]
CatBreed = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
CatSex = Literal["female", "spayed_female", "male", "neutered_male", "unknown"]
EARLIEST_BIRTHDAY = date(1995, 1, 1)


MAX_BODY_BYTES = 64 * 1024
_PUBLIC_PATHS = frozenset({"/api/health"})
_PHOTO_PATH = re.compile(r"/api/cats/\d+/photo")


def body_limit(method: str, path: str) -> int:
    """Only a cat's photo is allowed to be bigger than a JSON document."""
    if method == "PUT" and _PHOTO_PATH.fullmatch(path):
        return MAX_UPLOAD_BYTES
    return MAX_BODY_BYTES


class _BodyTooLarge(Exception):
    pass


class GuardApi:
    """Checks the access key and caps request bodies before the framework reads any body.

    A route dependency runs only after FastAPI has buffered and parsed the whole body, so on a
    LAN-exposed port an unauthenticated client could make the server hold an arbitrarily large
    request in memory. Chunked requests carry no Content-Length, hence the byte counting.
    """

    def __init__(self, app: Any, access_key: bytes) -> None:
        self.app = app
        self.access_key = access_key

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {name: value for name, value in scope["headers"]}
        supplied = headers.get(b"authorization", b"").removeprefix(b"Bearer ")
        if not secrets.compare_digest(supplied, self.access_key):
            await self._refuse(send, 401, "Wrong access key")
            return
        declared = headers.get(b"content-length", b"0")
        if not declared.isdigit():
            await self._refuse(send, 400, "Bad Content-Length")
            return
        limit = body_limit(scope.get("method", ""), path)
        if int(declared) > limit:
            await self._refuse(send, 413, "Request body too large")
            return

        received = 0
        exceeded = False

        async def capped_receive() -> Any:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def mute_after_overflow(message: Any) -> None:
            # FastAPI turns a failed body read into its own 400, which must not reach the client.
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, capped_receive, mute_after_overflow)
        except _BodyTooLarge:
            pass
        if exceeded:
            await self._refuse(send, 413, "Request body too large")

    @staticmethod
    async def _refuse(send: Any, status: int, detail: str) -> None:
        await JSONResponse({"detail": detail}, status_code=status)({"type": "http"}, None, send)


class CatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CatName
    reference_weight_lb: CatWeight | None = None
    # The field's name before the profile existed; it means the same thing.
    estimated_weight_lb: CatWeight | None = None
    breed: CatBreed | None = None
    birthday: date | None = None
    sex: CatSex = "unknown"

    @model_validator(mode="after")
    def _one_reference_weight(self) -> CatCreate:
        if self.reference_weight_lb is None:
            self.reference_weight_lb = self.estimated_weight_lb
        elif self.estimated_weight_lb not in (None, self.reference_weight_lb):
            raise ValueError("send reference_weight_lb, or estimated_weight_lb, not two values")
        return self


class CatPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CatName | None = None
    archived: bool | None = None
    breed: CatBreed | None = None
    birthday: date | None = None
    sex: CatSex | None = None
    reference_weight_lb: CatWeight | None = None

    @model_validator(mode="after")
    def _says_something(self) -> CatPatch:
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        for required in ("name", "archived", "sex"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be cleared")
        return self

    def profile(self) -> dict[str, Any]:
        fields = ("breed", "birthday", "sex", "reference_weight_lb")
        return {name: getattr(self, name) for name in fields if name in self.model_fields_set}


class VisitLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["cat", "not_cat", "unknown"]
    cat_id: int | None = None

    @model_validator(mode="after")
    def _cat_goes_with_cat(self) -> VisitLabel:
        if (self.label == "cat") != (self.cat_id is not None):
            raise ValueError("cat_id is required for the label cat and not allowed otherwise")
        return self


class MaintenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["filter", "deep_clean", "globe_clean"]
    done_on: Annotated[date, Field(ge=EARLIEST_DONE_ON)] | None = None


class LitterCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: Literal["full", "empty"]


class AlertSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drawer_level_percent: Annotated[int, Field(ge=1, le=100)] | None = None
    litter_low_percent: Annotated[int, Field(ge=1, le=99)] | None = None
    no_visit_hours: Annotated[int, Field(ge=1, le=720)] | None = None
    deep_clean_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    filter_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    globe_clean_days: Annotated[int, Field(ge=1, le=3650)] | None = None

    @model_validator(mode="after")
    def _says_something(self) -> AlertSettingsPatch:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("send at least one setting, each with a value")
        return self


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
    schedule: list[Command] = []
    if patch.sleep_minute is not None:
        schedule.extend(commands.set_panel_sleep_times(patch.sleep_minute))
    if patch.wake_minute is not None:
        schedule.extend(commands.set_panel_wake_times(patch.wake_minute))
    # The mask arms the schedule, so it goes last when arming and first when disarming. Then a
    # write that fails part way never leaves an armed schedule built from half-written times.
    if patch.sleep_enabled is None:
        out.extend(schedule)
    elif patch.sleep_enabled:
        out.extend(schedule)
        out.append(commands.set_weekday_sleep_enabled(True))
    else:
        out.append(commands.set_weekday_sleep_enabled(False))
        out.extend(schedule)
    return out


def _log_bridge_end(task: asyncio.Task) -> None:
    if not task.cancelled():
        log.critical("the robot bridge stopped: %r", task.exception())


def create_app(config: Config, store: Store, bridge: RobotBridge, *, run_bridge: bool = True):
    photos = CatPhotos(config.photo_dir)
    cats = CatTracker(store, config.timezone, photos=photos)
    cat_insights = CatInsights(store, cats)
    maintenance = MaintenanceLog(store, config.timezone)
    insights = Insights(store, config.timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        task = asyncio.create_task(bridge.run()) if run_bridge else None
        if task is not None:
            task.add_done_callback(_log_bridge_end)
        app.state.bridge_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            store.close()

    app = FastAPI(title="whisker-local", docs_url=None, redoc_url=None, lifespan=lifespan)

    app.add_middleware(GuardApi, access_key=config.access_key.encode())

    @app.exception_handler(RobotOffline)
    async def on_offline(_: Request, __: RobotOffline) -> JSONResponse:
        return JSONResponse({"detail": "The robot is offline."}, status_code=503)

    @app.exception_handler(BrokerUnavailable)
    async def on_broker_down(_: Request, __: BrokerUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": "The message broker is not reachable right now. Try again in a moment."},
            status_code=503,
        )

    @app.exception_handler(PressNotAcknowledged)
    async def on_unacknowledged(_: Request, __: PressNotAcknowledged) -> JSONResponse:
        return JSONResponse(
            {"detail": "The robot did not acknowledge that press. Check it before pressing again."},
            status_code=504,
        )

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

    @app.exception_handler(StateNotFresh)
    async def on_stale(_: Request, __: StateNotFresh) -> JSONResponse:
        return JSONResponse(
            {"detail": "The robot did not report back in time. Try again."}, status_code=504
        )

    @app.exception_handler(LitterNotReadable)
    async def on_unreadable(_: Request, err: LitterNotReadable) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=409)

    @app.exception_handler(MaintenanceInFuture)
    async def on_future(_: Request, __: MaintenanceInFuture) -> JSONResponse:
        return JSONResponse({"detail": "That date is in the future."}, status_code=422)

    @app.exception_handler(MaintenanceNotFound)
    async def on_no_entry(_: Request, __: MaintenanceNotFound) -> JSONResponse:
        return JSONResponse({"detail": "No such maintenance entry."}, status_code=404)

    @app.exception_handler(InvalidRange)
    async def on_bad_range(_: Request, err: InvalidRange) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=422)

    @app.exception_handler(CatNotFound)
    async def on_no_cat(_: Request, __: CatNotFound) -> JSONResponse:
        return JSONResponse({"detail": "No such cat."}, status_code=404)

    @app.exception_handler(VisitNotFound)
    async def on_no_visit(_: Request, __: VisitNotFound) -> JSONResponse:
        return JSONResponse({"detail": "No such visit."}, status_code=404)

    @app.exception_handler(CatNameTaken)
    async def on_name_taken(_: Request, __: CatNameTaken) -> JSONResponse:
        return JSONResponse({"detail": "Another cat already has that name."}, status_code=409)

    @app.exception_handler(CatArchived)
    async def on_archived(_: Request, __: CatArchived) -> JSONResponse:
        return JSONResponse(
            {"detail": "That cat is archived. Restore it before giving it visits."},
            status_code=409,
        )

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, bool]:
        task: asyncio.Task | None = app.state.bridge_task
        if task is not None and task.done():
            response.status_code = 503
            return {"ok": False}
        return {"ok": True}

    api = APIRouter(prefix="/api")

    @api.get("/status")
    async def status() -> dict[str, Any]:
        return {
            **bridge.status(),
            "demo": config.demo,
            "cats": cats.cats(),
            "visits_needing_cat": cats.needing_cat(),
            "latest_visit_needing_cat": cats.latest_unassigned_visit(),
        }

    @api.get("/events")
    async def events(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: str | None = None,
        kind: Annotated[list[str], Query(max_length=20)] = [],  # noqa: B006
    ) -> list[dict[str, Any]]:
        try:
            return store.events(limit=limit, before=before, kinds=kind)
        except InvalidCursor:
            raise HTTPException(status_code=422, detail="Unknown paging cursor") from None

    @api.get("/visits")
    async def visits(
        days: Annotated[int, Query(ge=1, le=3650)] = 30,
        cat_id: int | None = None,
        needs_cat: bool = False,
    ) -> list[dict[str, Any]]:
        if cat_id is not None and needs_cat:
            raise HTTPException(status_code=422, detail="Use either cat_id or needs_cat")
        return cats.visits(days=days, cat_id=cat_id, needs_cat=needs_cat)

    @api.post("/visits/{visit_id}/label")
    async def label_visit(visit_id: int, body: VisitLabel) -> dict[str, Any]:
        return cats.label_visit(visit_id, body.label, body.cat_id)

    @api.get("/cats")
    async def list_cats(include_archived: bool = False) -> list[dict[str, Any]]:
        return cats.cats(include_archived=include_archived)

    def require_possible_birthday(birthday: date | None) -> None:
        if birthday is not None and not EARLIEST_BIRTHDAY <= birthday <= cats.today():
            raise HTTPException(status_code=422, detail="That birthday is not possible.")

    @api.post("/cats", status_code=201)
    async def create_cat(body: CatCreate) -> dict[str, Any]:
        require_possible_birthday(body.birthday)
        return cats.create_cat(
            body.name,
            body.reference_weight_lb,
            breed=body.breed,
            birthday=body.birthday,
            sex=body.sex,
        )

    @api.get("/cats/suggestions")
    async def cat_suggestions() -> dict[str, Any]:
        return cats.suggestions()

    @api.get("/cats/{cat_id}")
    async def get_cat(cat_id: int) -> dict[str, Any]:
        return cats.cat(cat_id)

    @api.patch("/cats/{cat_id}")
    async def patch_cat(cat_id: int, body: CatPatch) -> dict[str, Any]:
        require_possible_birthday(body.birthday)
        return cats.update_cat(
            cat_id, name=body.name, archived=body.archived, profile=body.profile()
        )

    @api.put("/cats/{cat_id}/photo")
    async def put_cat_photo(cat_id: int, request: Request) -> dict[str, Any]:
        cats.cat(cat_id)
        try:
            picture = await asyncio.to_thread(normalise, await request.body())
        except ImageTooLarge:
            raise HTTPException(
                status_code=413, detail="That picture is too large to open. Use a smaller one."
            ) from None
        except NotAnImage:
            raise HTTPException(
                status_code=415, detail="That file is not a picture. Send a JPEG, PNG, WebP or GIF."
            ) from None
        photos.save(cat_id, picture)
        return cats.cat(cat_id)

    @api.get("/cats/{cat_id}/photo")
    async def get_cat_photo(cat_id: int) -> FileResponse:
        cats.cat(cat_id)
        path = photos.path(cat_id)
        if path is None:
            raise HTTPException(status_code=404, detail="This cat has no photo.")
        # The version in the cat object changes with the picture, so the app can cache by it.
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})

    @api.delete("/cats/{cat_id}/photo", status_code=204)
    async def delete_cat_photo(cat_id: int) -> Response:
        cats.cat(cat_id)
        photos.delete(cat_id)
        return Response(status_code=204)

    @api.get("/cats/{cat_id}/insights")
    async def get_cat_insights(
        cat_id: int,
        period: Period = "week",
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        return cat_insights.report(cat_id, period, offset)

    @api.get("/cats/{cat_id}/series")
    async def cat_series(
        cat_id: int, start: date | None = None, end: date | None = None
    ) -> dict[str, Any]:
        last = end or cats.today()
        first = start or last - timedelta(days=29)
        if first > last:
            raise HTTPException(status_code=422, detail="start is after end")
        if (last - first).days >= MAX_SERIES_DAYS:
            raise HTTPException(
                status_code=422, detail=f"Ask for at most {MAX_SERIES_DAYS} days at a time"
            )
        return cats.series(cat_id, first, last)

    @api.get("/maintenance")
    async def get_maintenance(
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        return maintenance.view(limit=limit)

    @api.post("/maintenance")
    async def record_maintenance(body: MaintenanceRecord, response: Response) -> dict[str, Any]:
        entry, created = maintenance.record(body.kind, body.done_on)
        response.status_code = 201 if created else 200
        bridge.recheck_alerts()
        return entry

    @api.delete("/maintenance/{entry_id}", status_code=204)
    async def delete_maintenance(entry_id: int) -> Response:
        maintenance.delete(entry_id)
        bridge.recheck_alerts()
        return Response(status_code=204)

    def alert_settings_view() -> dict[str, Any]:
        return {
            "settings": alert_settings.as_dict(alert_settings.load(store)),
            "defaults": alert_settings.defaults(),
        }

    @api.get("/alert-settings")
    async def get_alert_settings() -> dict[str, Any]:
        return alert_settings_view()

    @api.patch("/alert-settings")
    async def patch_alert_settings(body: AlertSettingsPatch) -> dict[str, Any]:
        alert_settings.update(store, body.model_dump(exclude_unset=True))
        bridge.recheck_alerts()
        return alert_settings_view()

    @api.delete("/alert-settings")
    async def reset_alert_settings() -> dict[str, Any]:
        alert_settings.reset(store)
        bridge.recheck_alerts()
        return alert_settings_view()

    @api.get("/insights")
    async def get_insights(
        days: Annotated[int | None, Query(ge=1, le=3660)] = None,
        start: date | None = None,
        end: date | None = None,
        granularity: Granularity | None = None,
    ) -> dict[str, Any]:
        if days is not None:
            if start is not None or end is not None:
                raise HTTPException(status_code=422, detail="Use either days or start and end")
            end = insights.today()
            start = end - timedelta(days=days - 1)
        return insights.report(start, end, granularity)

    @api.get("/export/visits.csv")
    async def export_visits(start: date | None = None, end: date | None = None) -> Response:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="start is after end")
        return Response(
            visits_csv(store, config.timezone, start, end),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="whisker-visits-{cats.today()}.csv"'
            },
        )

    @api.get("/export/database")
    async def export_database() -> FileResponse:
        path = await asyncio.to_thread(store.backup_copy)
        return FileResponse(
            path,
            media_type="application/vnd.sqlite3",
            filename=f"whisker-local-{cats.today()}.db",
            background=BackgroundTask(shutil.rmtree, path.parent, ignore_errors=True),
        )

    @api.post("/actions/clean", status_code=202)
    async def clean() -> dict[str, bool]:
        await bridge.press(
            commands.clean_cycle(), confirms=lambda robot: robot.robot_status == "clean_cycle"
        )
        return {"sent": True}

    @api.post("/actions/reset", status_code=202)
    async def reset() -> dict[str, bool]:
        await bridge.press(commands.panel_reset())
        return {"sent": True}

    @api.post("/actions/calibrate-litter")
    async def calibrate_litter(body: LitterCalibration) -> dict[str, Any]:
        await bridge.calibrate_litter(body.point)
        return bridge.status()

    @api.delete("/actions/calibrate-litter")
    async def clear_litter_calibration() -> dict[str, Any]:
        bridge.clear_litter_calibration()
        return bridge.status()

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
    ensure_mode(store, config.demo)
    return create_app(config, store, RobotBridge(config, store))
