from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import DAQService, BusyError
from . import offline
from .network import lan_addresses, local_client, private_client

STATIC = Path(__file__).parent / "static"
LOG = logging.getLogger(__name__)


class SettingsRequest(BaseModel):
    data: dict
    revision: str


class RunRequest(BaseModel):
    daq_mode: Literal["full_monitor", "write_only"] = "write_only"
    trigger_mode: Literal["threshold", "external", "periodic"] = "threshold"


class ShutdownRequest(BaseModel):
    confirmation: Literal["stop_and_exit"]


class ScanRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    lower: int = Field(1800, ge=0, le=4095, strict=True)
    upper: int = Field(2600, ge=0, le=4095, strict=True)
    step: int = Field(100, ge=1, le=4095, strict=True)
    dwell_s: float = Field(1, ge=0.05, le=60)
    max_events: int = Field(100000, ge=1, le=10000000, strict=True)


def create_app(
    config_path=None,
    workspace=None,
    demo=False,
    factory=None,
    on_shutdown=None,
    lan=False,
):
    workspace = Path(workspace or Path.cwd()).resolve()
    config_path = Path(
        config_path or workspace / "configs/channel0_generator_test.yaml"
    )

    @asynccontextmanager
    async def lifespan(app):
        logs = workspace / "logs"
        logs.mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            logs / "web.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger = logging.getLogger("telescopedaq")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        service = None
        try:
            service = DAQService(config_path, workspace, demo, factory)
            app.state.service = service
            app.state.offline_gate = asyncio.Semaphore(2)
            LOG.info("Web application started; demo=%s", demo)
            yield
        finally:
            try:
                if service is not None:
                    await asyncio.to_thread(service.close)
            finally:
                logger.removeHandler(handler)
                handler.close()

    app = FastAPI(title="TelescopeDAQ", version="0.3.0", lifespan=lifespan)
    app.state.shutdown_task = None
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
        + (lan_addresses() if lan else []),
    )

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        peer = request.client.host if request.client else ""
        local = local_client(peer)
        if not local:
            if not lan or not private_client(peer):
                return JSONResponse(
                    {
                        "detail": "LAN viewing is disabled or client is not on a private network"
                    },
                    status_code=403,
                )
            if request.method not in ("GET", "HEAD"):
                return JSONResponse(
                    {
                        "detail": "LAN is read-only. Control DAQ on the acquisition computer using 127.0.0.1"
                    },
                    status_code=403,
                )
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin:
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(
                    {"detail": "Cross-origin control is disabled"}, status_code=403
                )
        try:
            service = getattr(app.state, "service", None)
            if (
                service is not None
                and service.closed
                and request.method not in ("GET", "HEAD")
                and request.url.path != "/api/shutdown"
            ):
                return JSONResponse(
                    {"detail": "Safe exit in progress; new operations are disabled"},
                    status_code=503,
                )
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = (
                "no-store" if request.url.path.startswith("/api") else "no-cache"
            )
            return response
        except Exception:
            LOG.exception("HTTP request failed")
            return JSONResponse(
                {"detail": "Operation failed. See Logs for details."}, status_code=500
            )

    @app.exception_handler(BusyError)
    @app.exception_handler(FileExistsError)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    @app.exception_handler(FileNotFoundError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def service():
        return app.state.service

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def state(request: Request):
        return {
            **service().snapshot(),
            "read_only": not local_client(
                request.client.host if request.client else ""
            ),
            "lan_enabled": lan,
        }

    @app.post("/api/shutdown", status_code=202)
    async def shutdown(payload: ShutdownRequest):
        future = service().request_shutdown()

        async def finish():
            await asyncio.shield(asyncio.wrap_future(future))
            if service().shutdown_state == "ready" and on_shutdown is not None:
                # Allow the browser to observe successful cleanup before closing HTTP.
                await asyncio.sleep(1.5)
                on_shutdown()

        if app.state.shutdown_task is None:
            app.state.shutdown_task = asyncio.create_task(finish())
        return {"accepted": True, "shutdown_state": service().shutdown_state}

    @app.get("/api/logs")
    def logs(after: int = 0):
        with service().lock:
            return [item for item in service().logs if item["id"] > after]

    @app.get("/api/waveforms")
    def waveforms(after: int = -1):
        with service().lock:
            preview = service().preview
            return (
                preview
                if preview["sequence"] != after
                else {"sequence": after, "channels": None}
            )

    @app.get("/api/config")
    def config():
        return service().configuration()

    @app.post("/api/config/validate")
    def validate(payload: SettingsRequest):
        return service().save_config(payload.data, payload.revision, save=False)

    @app.put("/api/config")
    def save(payload: SettingsRequest):
        return service().save_config(payload.data, payload.revision)

    @app.get("/api/config/download")
    def download_config():
        return FileResponse(service().config_path, filename=service().config_path.name)

    @app.post("/api/config/import")
    async def import_config(file: UploadFile = File()):
        try:
            content = await file.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("YAML must be smaller than 1 MiB")
            from ..config import validate_config

            try:
                data = yaml.safe_load(content.decode("utf-8"))
            except (yaml.YAMLError, UnicodeError) as exc:
                raise ValueError("Invalid YAML: " + str(exc)) from exc
            return {"data": validate_config(data, service().config_path).data}
        finally:
            await file.close()

    @app.post("/api/connect", status_code=202)
    def connect():
        return service().connect()

    @app.post("/api/disconnect", status_code=202)
    def disconnect():
        return service().disconnect()

    @app.post("/api/run/start", status_code=202)
    def start(payload: RunRequest):
        return service().start(payload.daq_mode, payload.trigger_mode)

    @app.post("/api/run/stop")
    def stop():
        return service().stop()

    @app.post("/api/run/emergency")
    def emergency():
        return service().stop(emergency=True)

    @app.post("/api/scan/start", status_code=202)
    def scan(payload: ScanRequest):
        return service().start_scan(**payload.model_dump())

    def root_path(value):
        path = service().local_path(value)
        roots = [
            service().local_path(service().config.run["output_dir"]),
            workspace / "output",
        ]
        if path.suffix.lower() != ".root" or not any(
            path.is_relative_to(root) for root in roots
        ):
            raise ValueError("Choose a ROOT file in the output directory")
        if service().active_path == path:
            raise BusyError("Current run is still writing this ROOT file")
        return path

    @app.get("/api/files")
    def files():
        roots = {
            service().local_path(service().config.run["output_dir"]),
            workspace / "output",
        }
        paths = {
            path for root in roots if root.exists() for path in root.rglob("*.root")
        }
        return [
            {
                "path": str(path.relative_to(workspace)).replace("\\", "/"),
                "name": path.name,
                "bytes": path.stat().st_size,
                "active": path == service().active_path,
            }
            for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[
                :200
            ]
        ]

    @app.get("/api/root/summary")
    async def root_summary(path: str):
        async with app.state.offline_gate:
            return await asyncio.to_thread(offline.summary, root_path(path))

    @app.get("/api/root/waveform")
    async def root_waveform(path: str, entry: int = 0):
        async with app.state.offline_gate:
            return await asyncio.to_thread(offline.waveform, root_path(path), entry)

    @app.get("/api/root/download")
    def root_download(path: str):
        target = root_path(path)
        return FileResponse(target, filename=target.name)

    @app.post("/api/root/upload")
    async def root_upload(file: UploadFile = File()):
        folder = workspace / "output" / "imports"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (uuid.uuid4().hex + ".root")
        try:
            total = 0
            with target.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise ValueError(
                            "Upload limit: 512 MiB; use the output folder for larger files"
                        )
                    await asyncio.to_thread(stream.write, chunk)
            async with app.state.offline_gate:
                await asyncio.to_thread(offline.summary, target)
            return {"path": str(target.relative_to(workspace)).replace("\\", "/")}
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise ValueError("ROOT upload failed: " + str(exc)) from exc
        finally:
            await file.close()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
