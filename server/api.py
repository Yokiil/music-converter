"""上传 → 自动探测 → 转换 → 下载，不接受客户端密钥或路径。"""
from __future__ import annotations

import logging
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from urllib.parse import quote

import anyio
from fastapi import FastAPI, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from core import convert, locate, probe
from .config import ServerConfig

LOG = logging.getLogger(__name__)
API = "/api/v1"
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".wav": "audio/wav",
        ".ogg": "audio/ogg", ".opus": "audio/ogg"}
DETAILS = {
    "ready": "已识别到受支持的音频。",
    "needs_server_config": "已识别此封装，但服务器没有配置匹配的导出参数。",
    "requires_file_key": "此版本需要对应单曲的参数，不能由服务器共享配置自动导出。",
    "unsupported_format": "无法识别此文件，或此封装版本暂不支持。",
    "invalid_audio": "文件异常或没有可用的音轨。",
    "tools_unavailable": "服务器所需的音频工具或适配器尚未就绪。",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers: dict | None = None):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message
        self.headers = {**PRIVATE_HEADERS, **(headers or {})}


class Job:
    def __init__(self, temporary: tempfile.TemporaryDirectory, slots: anyio.Semaphore):
        self.temporary, self.slots = temporary, slots
        self.path = Path(temporary.name)
        self.handed_off = False

    def close(self) -> None:
        try:
            self.temporary.cleanup()
        except OSError:
            LOG.warning("Failed to clean up a temporary audio job")
        finally:
            self.slots.release()


class Download(FileResponse):
    def __init__(self, path: Path, name: str, job: Job, timeout: float, adapter: str,
                 *, binary: bool = False):
        headers = {**PRIVATE_HEADERS, "X-Audio-Adapter": adapter or "native", "Accept-Ranges": "none"}
        if binary:
            headers["X-Audio-Filename"] = quote(name, safe="")
        super().__init__(path, filename=None if binary else name,
                         media_type="application/octet-stream" if binary else MIME.get(path.suffix, "application/octet-stream"),
                         headers=headers)
        self.job, self.timeout = job, timeout

    async def __call__(self, scope, receive, send):
        # POST returns one full file, not a persistent resource for range requests.
        scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k not in (b"range", b"if-range")]}
        try:
            with anyio.move_on_after(self.timeout):
                await super().__call__(scope, receive, send)
        finally:
            self.job.close()


def options(request: Request, converting: bool) -> tuple[str, str, str, str | None]:
    accepted = {"filename", "format", "bitrate"} if converting else {"filename"}
    query = request.query_params
    if not query.keys() <= accepted or any(len(query.getlist(k)) > 1 for k in query):
        raise ApiError(400, "invalid_options", "请求只能指定文件名、输出格式和码率；不接收平台参数。")
    name = query.get("filename", "audio")
    if not name or len(name) > 255 or re.search(r"[\x00-\x1f\x7f]", name):
        raise ApiError(400, "invalid_options", "文件名无效。")
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1].rstrip(" .")
    basename, ext = Path(leaf).stem, Path(leaf).suffix.lower()
    suffix = ext if ext in probe.INPUT_EXTS else ".audio"
    fmt, bitrate = query.get("format", "mp3"), query.get("bitrate")
    if fmt not in convert.TARGETS or (bitrate is not None and bitrate not in convert.TARGETS[fmt].bitrates):
        raise ApiError(400, "invalid_options", "输出格式或码率不支持。")
    return convert.safe_name(basename), suffix, fmt, bitrate


def upload_limit(request: Request, limit: int) -> None:
    kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding != "identity" or not (kind == "application/octet-stream" or kind.startswith(("audio/", "video/"))):
        raise ApiError(415, "invalid_content_type", "请上传文件二进制内容，不接受 JSON、表单、链接或压缩请求。")
    declared = request.headers.get("content-length")
    if declared is not None:
        if not re.fullmatch(r"[0-9]{1,18}", declared):
            raise ApiError(400, "invalid_upload", "文件大小声明无效。")
        if int(declared) > limit:
            raise ApiError(413, "upload_too_large", "上传超过服务器大小限制。")


@asynccontextmanager
async def upload(request: Request, suffix: str):
    config = request.app.state.config
    upload_limit(request, config.max_upload_bytes)
    slots = request.app.state.slots
    try:
        slots.acquire_nowait()
    except anyio.WouldBlock:
        raise ApiError(429, "busy", "服务器正在处理其他文件，请稍后重试。", headers={"Retry-After": "3"}) from None
    job = None
    try:
        job = Job(tempfile.TemporaryDirectory(prefix="music-api-", dir=config.temp_dir), slots)
        # User input only affects the allowlisted suffix. The on-disk name is fixed.
        path = job.path / ("upload" + suffix)
        total = 0
        try:
            with anyio.fail_after(config.upload_timeout):
                async with await anyio.open_file(path, "xb") as writer:
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > config.max_upload_bytes:
                            raise ApiError(413, "upload_too_large", "上传超过服务器大小限制。")
                        if chunk:
                            await writer.write(chunk)
        except TimeoutError:
            raise ApiError(408, "upload_timeout", "上传超时。") from None
        except ClientDisconnect:
            raise ApiError(499, "client_disconnected", "客户端断开连接。") from None
        if not total:
            raise ApiError(400, "empty_upload", "没有收到文件内容。")
        yield job, path
    except OSError:
        raise ApiError(503, "temporary_storage_unavailable", "服务器临时工作空间不可用。") from None
    finally:
        if job is None:
            slots.release()
        elif not job.handed_off:
            job.close()


async def work(request: Request, fn, seconds: float):
    stop = Event()
    expired = False
    disconnected = False

    async def monitor_connection():
        nonlocal disconnected
        while (await request.receive())["type"] != "http.disconnect":
            pass
        disconnected = True
        stop.set()

    async def enforce_deadline():
        nonlocal expired
        await anyio.sleep(seconds)
        expired = True
        stop.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(monitor_connection)
        tasks.start_soon(enforce_deadline)
        try:
            # The slot/temporary directory stay alive until the cooperative worker exits.
            result = await anyio.to_thread.run_sync(fn, stop)
        finally:
            tasks.cancel_scope.cancel()
    if disconnected:
        raise ApiError(499, "client_disconnected", "客户端断开连接。")
    if expired:
        raise ApiError(504, "processing_timeout", "处理超时。")
    return result


def state(info: probe.AudioInfo) -> str:
    if info.requires_file_key:
        return "requires_file_key"
    if probe.is_convertible(info):
        return "ready"
    return {
        probe.VERDICT_PROFILE: "needs_server_config",
        probe.VERDICT_DEPENDENCY: "tools_unavailable",
        probe.VERDICT_BROKEN: "invalid_audio",
        probe.VERDICT_NOT_AUDIO: "invalid_audio",
    }.get(info.verdict, "unsupported_format")


def _openapi_upload(converting: bool) -> dict:
    parameters = [{"name": "filename", "in": "query", "schema": {"type": "string"},
                   "description": "原始文件名；老版封装可能需要后缀作为辅助信息"}]
    if converting:
        parameters += [
            {"name": "format", "in": "query", "schema": {"type": "string", "enum": list(convert.TARGETS), "default": "mp3"}},
            {"name": "bitrate", "in": "query", "schema": {"type": "string", "example": "128k"}},
        ]
    return {
        "parameters": parameters,
        "requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
    }


def create_app(config: ServerConfig | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = config if config is not None else ServerConfig.from_env()
        app.state.config = settings
        app.state.profile = settings.load_profile()
        app.state.slots = anyio.Semaphore(settings.concurrency)
        yield

    app = FastAPI(title="音乐转换器 API", version="0.1.0", lifespan=lifespan, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    web_dir = Path(__file__).resolve().parents[1] / "web"
    app.mount("/assets", StaticFiles(directory=web_dir), name="web-assets")

    @app.get("/", include_in_schema=False)
    async def website():
        return FileResponse(web_dir / "index.html", headers={
            **PRIVATE_HEADERS,
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                     "img-src 'self' data: blob:; connect-src 'self'; "
                                     "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "Referrer-Policy": "same-origin",
        })

    @app.exception_handler(ApiError)
    async def api_error(_request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status, headers=exc.headers)

    @app.get("/healthz")
    async def health():
        ready = locate.ffmpeg_available() and locate.probe_available()
        return JSONResponse({"status": "ok" if ready else "tools_unavailable"}, status_code=200 if ready else 503,
                            headers=PRIVATE_HEADERS)

    @app.get(f"{API}/capabilities")
    async def capabilities(request: Request):
        limits = request.app.state.config
        return JSONResponse({"max_upload_bytes": limits.max_upload_bytes,
                             "max_output_bytes": limits.max_output_bytes,
                             "formats": {name: list(target.bitrates) for name, target in convert.TARGETS.items()}},
                            headers=PRIVATE_HEADERS)

    @app.post(f"{API}/inspect", openapi_extra=_openapi_upload(False))
    async def inspect(request: Request):
        _, suffix, _, _ = options(request, converting=False)
        profile = request.app.state.profile
        async with upload(request, suffix) as (_, path):
            info = await work(request, lambda _stop: probe.diagnose(path, profile), 20)
        status = state(info)
        message = "已识别平台封装；实际导出时会验证音频和配置是否匹配。" if status == "ready" and info.wrapped else DETAILS[status]
        if status == "unsupported_format" and info.wrapped and info.adapter:
            message = info.note[:240]
        return JSONResponse({"state": status, "adapter": info.adapter or None, "container": info.container,
                             "codec": info.codec or None, "duration_seconds": info.duration or None,
                             "wrapped": info.wrapped, "can_convert": status == "ready", "size_bytes": info.size,
                             "message": message}, headers=PRIVATE_HEADERS)

    @app.post(f"{API}/convert", openapi_extra=_openapi_upload(True), response_class=FileResponse,
              responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def convert_upload(request: Request):
        name, suffix, fmt, bitrate = options(request, converting=True)
        profile = request.app.state.profile
        settings = request.app.state.config
        if not locate.ffmpeg_available() or not locate.probe_available():
            raise ApiError(503, "tools_unavailable", DETAILS["tools_unavailable"])
        async with upload(request, suffix) as (job, path):
            def run(stop):
                info = probe.diagnose(path, profile)
                if state(info) != "ready" or stop.is_set():
                    return info, None
                return info, convert.convert_to_directory(info, job.path / "output", fmt, bitrate,
                                                           profile=profile, cancel=stop, verify_copy=True,
                                                           max_output_bytes=settings.max_output_bytes)

            info, result = await work(request, run, settings.processing_timeout)
            error = state(info)
            if error != "ready":
                raise ApiError(503 if error == "tools_unavailable" else 422, error, DETAILS[error])
            if result is None or not result.ok or result.dst is None:
                if result is not None and result.error_code == "output_too_large":
                    raise ApiError(422, "output_too_large", "转换结果超过服务器大小限制，请选体积较小的输出格式。")
                raise ApiError(422, "conversion_failed", "没能生成可播放的结果；文件可能损坏，或与服务器配置不匹配。")
            binary = request.headers.get("accept", "").strip().lower() == "application/octet-stream"
            response = Download(result.dst, name + result.dst.suffix, job, settings.download_timeout,
                                info.adapter, binary=binary)
            job.handed_off = True
            return response

    return app


app = create_app()
