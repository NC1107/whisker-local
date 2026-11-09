from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from whiskerless.devices.litter_robot_4 import Command, commands, const, derive

from . import alert_settings, push_options
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
    NotEnoughReadings,
    VisitNotFound,
)
from .config import Config
from .coverage import Coverage, RangeNotFound
from .coverage import InvalidRange as InvalidCoverageRange
from .demo import ensure_mode
from .export import visits_csv
from .insights import Granularity, Insights, InvalidRange
from .maintenance import (
    EARLIEST_DONE_ON,
    MaintenanceInFuture,
    MaintenanceLog,
    MaintenanceNotFound,
)
from .photos import MAX_UPLOAD_BYTES, CatPhotos, ImageTooLarge, NotAnImage, decoder, normalise
from .push import (
    DEFAULT_KINDS,
    PushNotifier,
    add_device,
    kind_settings,
    remove_devices,
    stored_devices,
    update_kinds,
)
from .push_catalog import GROUPS, catalog_view
from .store import InvalidCursor, Store
from .whisker_import import (
    MAX_IMPORT_BYTES,
    NOT_A_WHISKER_EXPORT,
    BatchNotFound,
    Importer,
    NotWhiskerExport,
    is_valid_batch_id,
)

log = logging.getLogger(__name__)

_NIGHT_LIGHT_MODES = {name: value for value, name in const.NIGHT_LIGHT_MODE.items()}

Minute = Annotated[int, Field(ge=0, le=1439)]
CatName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
CatWeight = Annotated[float, Field(ge=2, le=40)]
CatBreed = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
CatSex = Literal["female", "spayed_female", "male", "neutered_male", "unknown"]
EARLIEST_BIRTHDAY = date(1995, 1, 1)

# SQLite integers are 64-bit, and a bigger id would raise instead of finding nothing.
_MAX_ROW_ID = 2**63 - 1
RowId = Annotated[int, Field(ge=1, le=_MAX_ROW_ID)]
RowIdPath = Annotated[int, Path(ge=1, le=_MAX_ROW_ID)]

# Bumped whenever the rules that name a cat change, so that history is named afresh once.
IDENTITY_VERSION_KEY = "identity_version"
IDENTITY_VERSION = 2

# Only this many uploads may hold a request body in memory at once. The rest wait, unread, so a
# flood of large uploads queues up on the socket instead of in the process.
MAX_BUFFERING_UPLOADS = 2
UPLOAD_QUEUE_SECONDS = 20


MAX_BODY_BYTES = 64 * 1024
_PUBLIC_PATHS = frozenset({"/api/health"})
_PHOTO_PATH = re.compile(r"/api/cats/\d+/photo")
_IMPORT_PATH = "/api/import/whisker-activity"


def body_limit(method: str, path: str) -> int:
    """Only a cat's photo is allowed to be bigger than a JSON document."""
    if method == "PUT" and _PHOTO_PATH.fullmatch(path):
        return MAX_UPLOAD_BYTES
    if method == "POST" and path == _IMPORT_PATH:
        return MAX_IMPORT_BYTES
    return MAX_BODY_BYTES


class _BodyTooLarge(Exception):
    pass


class SafeJSONResponse(JSONResponse):
    """JSON that can always be encoded, whatever text a stored row happens to hold.

    A lone surrogate cannot be written as UTF-8, and one saved row must not be able to turn a
    whole endpoint into a permanent 500.
    """

    def render(self, content: Any) -> bytes:
        text = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return text.encode("utf-8", errors="replace")


CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        # CanvasKit compiles its renderer from a wasm file it fetches from this server.
        "script-src 'self' 'wasm-unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self' data: blob:",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeaders:
    """Adds the browser hardening headers to every response, errors and the static app included.

    Not every install sits behind a proxy that adds them.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message["headers"]}
                message["headers"] = [
                    *message["headers"],
                    *(pair for pair in SECURITY_HEADERS if pair[0] not in present),
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


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
        if not self.access_key or not secrets.compare_digest(supplied, self.access_key):
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
    cat_id: RowId | None = None

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


class AnchorSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Omitted, the anchor is taken from the cat's latest confident readings.
    weight_lb: CatWeight | None = None


class UnreliableRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Annotated[date, Field(ge=EARLIEST_DONE_ON)]
    end: Annotated[date, Field(ge=EARLIEST_DONE_ON)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class AlertSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drawer_level_percent: Annotated[int, Field(ge=50, le=95)] | None = None
    litter_low_percent: Annotated[int, Field(ge=10, le=50)] | None = None
    no_visit_hours: Annotated[int, Field(ge=1, le=720)] | None = None
    long_visit_minutes: Annotated[int, Field(ge=2, le=5)] | None = None
    frequent_visits_per_hour: Annotated[int, Field(ge=3, le=10)] | None = None
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


class PushDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(pattern=r"^[\x21-\x7e]{1,1024}$")]
    platform: Literal["ios", "android"]


class PushDeviceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(pattern=r"^[\x21-\x7e]{1,1024}$")]


class PushKindsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: dict[str, bool]

    @model_validator(mode="after")
    def _known_kinds(self) -> PushKindsPatch:
        unknown = sorted(set(self.kinds) - set(DEFAULT_KINDS))
        if not self.kinds or unknown:
            raise ValueError(f"send known alert kinds, not: {', '.join(unknown) or 'nothing'}")
        return self


ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class QuietHoursPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    start: ClockTime | None = None
    end: ClockTime | None = None
    mode: Literal["hold", "drop"] | None = None


class CatChoicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["any", "selected"] | None = None
    cat_ids: Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=50)] | None = None
    include_unknown: bool | None = None


class PushOptionsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quiet_hours: QuietHoursPatch | None = None
    cats: dict[str, CatChoicePatch] | None = None
    daily_summary_hour: Annotated[int, Field(ge=0, le=23)] | None = None

    @model_validator(mode="after")
    def _valid(self) -> PushOptionsPatch:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("send at least one option, each with a value")
        unknown = sorted(set(self.cats or {}) - push_options.CAT_KINDS)
        if unknown:
            raise ValueError(f"these alerts have no per-cat choice: {', '.join(unknown)}")
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


class WebApp(StaticFiles):
    """Flutter's asset names are not content-hashed, so every response must be revalidated.

    no-transform stops proxies such as Cloudflare from injecting scripts the CSP would block.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as err:
            # A missing asset must not be edge-cached either, or a deploy swap sticks.
            response = PlainTextResponse(
                err.detail, status_code=err.status_code, headers=err.headers
            )
        response.headers["Cache-Control"] = "no-cache, no-transform"
        return response


def create_app(
    config: Config,
    store: Store,
    bridge: RobotBridge,
    *,
    run_bridge: bool = True,
    notifier: PushNotifier | None = None,
):
    photos = CatPhotos(config.photo_dir)
    cats = CatTracker(store, config.timezone, photos=photos)
    cat_insights = CatInsights(store, cats)
    maintenance = MaintenanceLog(store, config.timezone)
    insights = Insights(store, config.timezone)
    coverage = Coverage(store, config.timezone)
    importer = Importer(store, cats, config.timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        # Labels made by an earlier version were worked out by other rules.
        if store.get_json(IDENTITY_VERSION_KEY) != IDENTITY_VERSION:
            cats.rebuild()
            store.put_json(IDENTITY_VERSION_KEY, IDENTITY_VERSION)
        task = asyncio.create_task(bridge.run()) if run_bridge else None
        if task is not None:
            task.add_done_callback(_log_bridge_end)
        app.state.bridge_task = task
        push_task = asyncio.create_task(notifier.run()) if notifier is not None else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            if push_task is not None:
                push_task.cancel()
            store.close()

    # The schema is served without the access key otherwise, and it maps every route.
    app = FastAPI(
        title="whisker-local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        default_response_class=SafeJSONResponse,
    )

    app.add_middleware(GuardApi, access_key=config.access_key.encode())
    app.add_middleware(SecurityHeaders)
    upload_slots = asyncio.Semaphore(MAX_BUFFERING_UPLOADS)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(_: Request, err: RequestValidationError) -> SafeJSONResponse:
        # The rejected input is left out because NaN and broken text cannot be sent back as JSON.
        errors = [{key: e[key] for key in ("type", "loc", "msg") if key in e} for e in err.errors()]
        return SafeJSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

    @app.exception_handler(UnicodeDecodeError)
    async def on_undecodable(_: Request, __: UnicodeDecodeError) -> SafeJSONResponse:
        return SafeJSONResponse({"detail": "Invalid request body"}, status_code=400)

    @app.exception_handler(OverflowError)
    async def on_out_of_range(_: Request, __: OverflowError) -> SafeJSONResponse:
        return SafeJSONResponse({"detail": "Value out of range"}, status_code=422)

    @app.exception_handler(RobotOffline)
    async def on_offline(_: Request, __: RobotOffline) -> JSONResponse:
        return JSONResponse({"detail": "Robot offline"}, status_code=503)

    @app.exception_handler(BrokerUnavailable)
    async def on_broker_down(_: Request, __: BrokerUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": "Broker unreachable, try again"},
            status_code=503,
        )

    @app.exception_handler(PressNotAcknowledged)
    async def on_unacknowledged(_: Request, __: PressNotAcknowledged) -> JSONResponse:
        return JSONResponse(
            {"detail": "Robot did not acknowledge, check it before pressing again"},
            status_code=504,
        )

    @app.exception_handler(RobotLocked)
    async def on_locked(_: Request, __: RobotLocked) -> JSONResponse:
        return JSONResponse(
            {"detail": "Panel lock is on, turn it off to use the robot here"},
            status_code=409,
        )

    @app.exception_handler(SettingNotConfirmed)
    async def on_unconfirmed(_: Request, __: SettingNotConfirmed) -> JSONResponse:
        return JSONResponse({"detail": "Robot did not confirm, try again"}, status_code=504)

    @app.exception_handler(StateNotFresh)
    async def on_stale(_: Request, __: StateNotFresh) -> JSONResponse:
        return JSONResponse({"detail": "Robot did not respond, try again"}, status_code=504)

    @app.exception_handler(LitterNotReadable)
    async def on_unreadable(_: Request, err: LitterNotReadable) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=409)

    @app.exception_handler(MaintenanceInFuture)
    async def on_future(_: Request, __: MaintenanceInFuture) -> JSONResponse:
        return JSONResponse({"detail": "Date is in the future"}, status_code=422)

    @app.exception_handler(MaintenanceNotFound)
    async def on_no_entry(_: Request, __: MaintenanceNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Maintenance entry not found"}, status_code=404)

    @app.exception_handler(InvalidRange)
    async def on_bad_range(_: Request, err: InvalidRange) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=422)

    @app.exception_handler(InvalidCoverageRange)
    async def on_bad_coverage_range(_: Request, err: InvalidCoverageRange) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=422)

    @app.exception_handler(RangeNotFound)
    async def on_no_range(_: Request, __: RangeNotFound) -> JSONResponse:
        return JSONResponse({"detail": "No such range."}, status_code=404)

    @app.exception_handler(NotEnoughReadings)
    async def on_few_readings(_: Request, __: NotEnoughReadings) -> JSONResponse:
        return JSONResponse(
            {"detail": "This cat does not have enough named visits to take a weight from yet."},
            status_code=409,
        )

    @app.exception_handler(CatNotFound)
    async def on_no_cat(_: Request, __: CatNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Cat not found"}, status_code=404)

    @app.exception_handler(VisitNotFound)
    async def on_no_visit(_: Request, __: VisitNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Visit not found"}, status_code=404)

    @app.exception_handler(BatchNotFound)
    async def on_no_batch(_: Request, __: BatchNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Import not found"}, status_code=404)

    @app.exception_handler(CatNameTaken)
    async def on_name_taken(_: Request, __: CatNameTaken) -> JSONResponse:
        return JSONResponse({"detail": "Name already in use"}, status_code=409)

    @app.exception_handler(CatArchived)
    async def on_archived(_: Request, __: CatArchived) -> JSONResponse:
        return JSONResponse(
            {"detail": "Cat is archived, restore it first"},
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
            "push_configured": notifier is not None,
            "cats": cats.cats(),
            "visits_needing_cat": cats.needing_cat(),
            "latest_visit_needing_cat": cats.latest_unassigned_visit(),
            "last_clean_at": store.latest_event_at("clean_cycle"),
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
        cat_id: Annotated[int | None, Query(ge=1, le=_MAX_ROW_ID)] = None,
        needs_cat: bool = False,
    ) -> list[dict[str, Any]]:
        if cat_id is not None and needs_cat:
            raise HTTPException(status_code=422, detail="Use either cat_id or needs_cat")
        return cats.visits(days=days, cat_id=cat_id, needs_cat=needs_cat)

    @api.post("/visits/{visit_id}/label")
    async def label_visit(visit_id: RowIdPath, body: VisitLabel) -> dict[str, Any]:
        return cats.label_visit(visit_id, body.label, body.cat_id)

    @api.get("/cats")
    async def list_cats(include_archived: bool = False) -> list[dict[str, Any]]:
        return cats.cats(include_archived=include_archived)

    def require_possible_birthday(birthday: date | None) -> None:
        if birthday is not None and not EARLIEST_BIRTHDAY <= birthday <= cats.today():
            raise HTTPException(status_code=422, detail="Invalid birthday")

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
    async def get_cat(cat_id: RowIdPath) -> dict[str, Any]:
        return cats.cat(cat_id)

    @api.patch("/cats/{cat_id}")
    async def patch_cat(cat_id: RowIdPath, body: CatPatch) -> dict[str, Any]:
        require_possible_birthday(body.birthday)
        return cats.update_cat(
            cat_id, name=body.name, archived=body.archived, profile=body.profile()
        )

    @api.put("/cats/{cat_id}/photo")
    async def put_cat_photo(cat_id: RowIdPath, request: Request) -> dict[str, Any]:
        cats.cat(cat_id)
        try:
            async with asyncio.timeout(UPLOAD_QUEUE_SECONDS):
                await upload_slots.acquire()
        except TimeoutError:
            raise HTTPException(status_code=503, detail="Busy, try again") from None
        try:
            body = await request.body()
            picture = await asyncio.get_running_loop().run_in_executor(decoder, normalise, body)
        except ImageTooLarge:
            raise HTTPException(
                status_code=413, detail="Picture too large, use a smaller one"
            ) from None
        except NotAnImage:
            raise HTTPException(
                status_code=415, detail="Not a picture, use JPEG, PNG, WebP or GIF"
            ) from None
        finally:
            upload_slots.release()
        photos.save(cat_id, picture)
        return cats.cat(cat_id)

    @api.get("/cats/{cat_id}/photo")
    async def get_cat_photo(cat_id: RowIdPath) -> FileResponse:
        cats.cat(cat_id)
        path = photos.path(cat_id)
        if path is None:
            raise HTTPException(status_code=404, detail="No photo")
        # The version in the cat object changes with the picture, so the app can cache by it.
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})

    @api.delete("/cats/{cat_id}/photo", status_code=204)
    async def delete_cat_photo(cat_id: RowIdPath) -> Response:
        cats.cat(cat_id)
        photos.delete(cat_id)
        return Response(status_code=204)

    @api.get("/cats/{cat_id}/insights")
    async def get_cat_insights(
        cat_id: RowIdPath,
        period: Period = "week",
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        return cat_insights.report(cat_id, period, offset)

    @api.put("/cats/{cat_id}/anchor")
    async def put_cat_anchor(cat_id: RowIdPath, body: AnchorSet) -> dict[str, Any]:
        return cats.set_anchor(cat_id, body.weight_lb)

    @api.delete("/cats/{cat_id}/anchor", status_code=204)
    async def delete_cat_anchor(cat_id: RowIdPath) -> Response:
        cats.clear_anchor(cat_id)
        return Response(status_code=204)

    @api.get("/identity")
    async def get_identity(days: Annotated[int, Query(ge=1, le=366)] = 30) -> dict[str, Any]:
        return {**cats.identity_overview(days), "anchors": cats.anchors()}

    @api.get("/cats/{cat_id}/series")
    async def cat_series(
        cat_id: RowIdPath, start: date | None = None, end: date | None = None
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
    async def delete_maintenance(entry_id: RowIdPath) -> Response:
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

    def push_catalog() -> list[dict[str, Any]]:
        seen = {
            "hopper": bridge.derived.sighted(derive.Capability.HOPPER),
            "maintenance": bool(store.maintenance_last_done()),
        }
        return [
            {**kind, "available": kind["needs"] is None or seen.get(kind["needs"], False)}
            for kind in catalog_view()
        ]

    def push_view() -> dict[str, Any]:
        thresholds = alert_settings.as_dict(alert_settings.load(store))
        return {
            "configured": notifier is not None,
            "devices": len(stored_devices(store)),
            "kinds": kind_settings(store),
            "defaults": DEFAULT_KINDS,
            "groups": list(GROUPS),
            "catalog": push_catalog(),
            "options": push_options.load(store),
            "thresholds": thresholds,
            "cats": [{"id": c["id"], "name": c["name"]} for c in store.cats()],
        }

    def require_push() -> PushNotifier:
        if notifier is None:
            raise HTTPException(status_code=404, detail="Push is not set up")
        return notifier

    @api.get("/push")
    async def get_push() -> dict[str, Any]:
        return push_view()

    @api.put("/push/devices", status_code=204)
    async def put_push_device(body: PushDevice) -> Response:
        require_push()
        add_device(store, body.token, body.platform)
        return Response(status_code=204)

    @api.delete("/push/devices", status_code=204)
    async def delete_push_device(body: PushDeviceRef) -> Response:
        require_push()
        remove_devices(store, {body.token})
        return Response(status_code=204)

    @api.patch("/push/kinds")
    async def patch_push_kinds(body: PushKindsPatch) -> dict[str, Any]:
        require_push()
        before = kind_settings(store)
        update_kinds(store, body.kinds)
        # An alert already active while its kind was off was never announced.
        bridge.announce_active({k for k, on in body.kinds.items() if on and not before[k]})
        return push_view()

    @api.patch("/push/options")
    async def patch_push_options(body: PushOptionsPatch) -> dict[str, Any]:
        require_push()
        try:
            push_options.update(
                store,
                body.model_dump(exclude_unset=True, exclude_none=True),
                known_cats={c["id"] for c in store.cats()},
            )
        except push_options.InvalidOptions as err:
            raise HTTPException(status_code=422, detail=str(err)) from None
        return push_view()

    @api.post("/push/test")
    async def send_push_test() -> dict[str, int]:
        require_push()
        if not stored_devices(store):
            raise HTTPException(status_code=409, detail="No phone registered for notifications")
        try:
            delivered = await require_push().send_test()
        except Exception as err:
            log.warning("test push failed: %s", type(err).__name__)
            raise HTTPException(status_code=502, detail="Push relay rejected the test") from None
        return {"delivered": delivered}

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

    @api.get("/coverage")
    async def get_coverage(
        days: Annotated[int | None, Query(ge=1, le=366)] = None,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        last = end or coverage.today()
        first = start or last - timedelta(days=(days or 30) - 1)
        if (last - first).days >= 366:
            raise HTTPException(status_code=422, detail="Ask for at most 366 days at a time")
        return coverage.view(first, last)

    @api.get("/coverage/unreliable")
    async def get_unreliable() -> list[dict[str, Any]]:
        return coverage.unreliable_ranges()

    @api.post("/coverage/unreliable", status_code=201)
    async def mark_unreliable(body: UnreliableRange) -> dict[str, Any]:
        return coverage.mark_unreliable(body.start, body.end, body.note)

    @api.delete("/coverage/unreliable/{range_id}", status_code=204)
    async def unmark_unreliable(range_id: RowIdPath) -> Response:
        coverage.unmark_unreliable(range_id)
        return Response(status_code=204)

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

    @api.post("/import/whisker-activity")
    async def import_whisker_activity(
        request: Request,
        dry_run: bool = False,
        reference_date: date | None = None,
        reprocess: bool = False,
    ) -> dict[str, Any]:
        if reference_date is not None and reference_date > cats.today():
            raise HTTPException(status_code=422, detail="Reference date is in the future")
        body = await request.body()
        try:
            run = importer.reprocess if reprocess else importer.run
            return run(body.decode("utf-8"), reference=reference_date, dry_run=dry_run)
        except UnicodeDecodeError:
            raise HTTPException(status_code=422, detail=NOT_A_WHISKER_EXPORT) from None
        except NotWhiskerExport as err:
            raise HTTPException(status_code=422, detail=str(err)) from None

    @api.get("/import/batches")
    async def import_batches() -> list[dict[str, Any]]:
        return importer.batches()

    @api.delete("/import/batches/{batch_id}")
    async def undo_import(batch_id: str) -> dict[str, Any]:
        if not is_valid_batch_id(batch_id):
            raise BatchNotFound(batch_id)
        return importer.undo(batch_id)

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
        app.mount("/", WebApp(directory=config.web_root, html=True), name="web")
    return app


def create_app_from_env() -> FastAPI:
    config = Config.from_env()
    store = Store(config.db_path)
    ensure_mode(store, config.demo)
    notifier = (
        PushNotifier(config.push_relay_url, store, timezone=config.timezone)
        if config.push_relay_url
        else None
    )
    return create_app(
        config, store, RobotBridge(config, store, notifier=notifier), notifier=notifier
    )
