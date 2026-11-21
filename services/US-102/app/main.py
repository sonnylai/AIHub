import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.codes import generate_code, generate_unique_code, is_valid_code

MAX_BODY_BYTES = 64 * 1024
MAX_URL_LENGTH = 2048
DEFAULT_BASE_URL = "http://localhost:8000"

logger = logging.getLogger("url_shortener")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str

    @field_validator("url")
    @classmethod
    def url_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("url must not be blank")
        return value


class CreateLinkResponse(BaseModel):
    code: str
    long_url: str
    short_url: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return format_utc(value)


class LinkStatsResponse(BaseModel):
    code: str
    long_url: str
    clicks: int = Field(ge=0)
    created_at: datetime
    last_clicked_at: datetime | None

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return format_utc(value)

    @field_serializer("last_clicked_at")
    def serialize_last_clicked_at(self, value: datetime | None) -> str | None:
        return format_utc(value) if value is not None else None


class HealthResponse(BaseModel):
    status: str


@dataclass
class LinkRecord:
    code: str
    long_url: str
    created_at: datetime
    clicks: int = 0
    last_clicked_at: datetime | None = None


@dataclass
class LinkStats:
    code: str
    long_url: str
    clicks: int
    created_at: datetime
    last_clicked_at: datetime | None


@dataclass
class InMemoryLinkStore:
    _links: dict[str, LinkRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def create(
        self,
        long_url: str,
        code_generator: Callable[[], str] = generate_code,
        clock: Callable[[], datetime] = utc_now,
    ) -> LinkRecord:
        with self._lock:
            code = generate_unique_code(lambda candidate: candidate in self._links, code_generator)
            record = LinkRecord(code=code, long_url=long_url, created_at=clock())
            self._links[code] = record
            return LinkRecord(
                code=record.code,
                long_url=record.long_url,
                created_at=record.created_at,
                clicks=record.clicks,
                last_clicked_at=record.last_clicked_at,
            )

    def record_click(self, code: str, clock: Callable[[], datetime] = utc_now) -> str | None:
        with self._lock:
            record = self._links.get(code)
            if record is None:
                return None
            record.clicks += 1
            record.last_clicked_at = clock()
            return record.long_url

    def get_stats(self, code: str) -> LinkStats | None:
        with self._lock:
            record = self._links.get(code)
            if record is None:
                return None
            return LinkStats(
                code=record.code,
                long_url=record.long_url,
                clicks=record.clicks,
                created_at=record.created_at,
                last_clicked_at=record.last_clicked_at,
            )

    def clear(self) -> None:
        with self._lock:
            self._links.clear()


def validate_long_url(value: str) -> None:
    if len(value) > MAX_URL_LENGTH:
        raise APIError(400, "invalid_url", f"url must not exceed {MAX_URL_LENGTH} characters")
    if value != value.strip() or any(character.isspace() for character in value):
        raise APIError(400, "invalid_url", "url must not contain whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise APIError(400, "invalid_url", "url must not contain ASCII control characters")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise APIError(400, "invalid_url", "url is not a valid absolute URL") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise APIError(400, "invalid_url", "url must use the http or https scheme")
    if not parsed.netloc or not hostname:
        raise APIError(400, "invalid_url", "url must be an absolute http or https URL with a host")


@dataclass
class LinkService:
    store: InMemoryLinkStore
    base_url: str
    code_generator: Callable[[], str] = generate_code

    def create_link(self, long_url: str) -> CreateLinkResponse:
        validate_long_url(long_url)
        record = self.store.create(long_url, self.code_generator)
        return CreateLinkResponse(
            code=record.code,
            long_url=record.long_url,
            short_url=f"{self.base_url.rstrip('/')}/{record.code}",
            created_at=record.created_at,
        )

    def resolve(self, code: str) -> str:
        if not is_valid_code(code):
            raise APIError(404, "not_found", "link not found")
        long_url = self.store.record_click(code)
        if long_url is None:
            raise APIError(404, "not_found", "link not found")
        return long_url

    def stats(self, code: str) -> LinkStatsResponse:
        if not is_valid_code(code):
            raise APIError(404, "not_found", "link not found")
        stats = self.store.get_stats(code)
        if stats is None:
            raise APIError(404, "not_found", "link not found")
        return LinkStatsResponse(
            code=stats.code,
            long_url=stats.long_url,
            clicks=stats.clicks,
            created_at=stats.created_at,
            last_clicked_at=stats.last_clicked_at,
        )


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


async def read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise APIError(413, "invalid_request", "request body exceeds 64 KiB")
        except ValueError:
            raise APIError(422, "invalid_request", "invalid Content-Length header")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise APIError(413, "invalid_request", "request body exceeds 64 KiB")
    return bytes(body)


def parse_create_request(body: bytes) -> CreateLinkRequest:
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise APIError(422, "invalid_request", "request body must be valid JSON") from exc

    try:
        return CreateLinkRequest.model_validate(payload)
    except ValidationError as exc:
        raise APIError(422, "invalid_request", "request body must contain a non-blank string url") from exc


def content_type_is_json(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("url shortener starting")
    yield
    logger.info("url shortener stopping")


def request_id_for(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    if supplied and len(supplied) <= 128 and re.fullmatch(r"[\x21-\x7e]+", supplied):
        return supplied
    return uuid4().hex


def create_app() -> FastAPI:
    application = FastAPI(
        title="URL Shortener API",
        version="1.0.0",
        lifespan=lifespan,
        redirect_slashes=False,
    )
    store = InMemoryLinkStore()
    service = LinkService(store=store, base_url=os.getenv("BASE_URL", DEFAULT_BASE_URL))
    application.state.store = store
    application.state.service = service

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request_id_for(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled error method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    @application.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "invalid_request", "request validation failed")

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, "not_found", "resource not found")
        if exc.status_code == 405:
            return error_response(405, "method_not_allowed", "method not allowed")
        if exc.status_code in {413, 415, 422}:
            return error_response(exc.status_code, "invalid_request", "invalid request")
        if exc.status_code >= 500:
            return error_response(500, "internal_error", "internal server error")
        return error_response(exc.status_code, "invalid_request", "invalid request")

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return error_response(500, "internal_error", "internal server error")

    @application.post(
        "/links",
        response_model=CreateLinkResponse,
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["url"],
                            "properties": {"url": {"type": "string"}},
                            "additionalProperties": True,
                        }
                    }
                },
            }
        },
    )
    async def create_link(request: Request) -> CreateLinkResponse:
        if not content_type_is_json(request):
            raise APIError(415, "invalid_request", "Content-Type must be application/json")
        body = await read_limited_body(request)
        payload = parse_create_request(body)
        return service.create_link(payload.url)

    @application.api_route(
        "/links",
        methods=["GET", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def links_method_not_allowed() -> None:
        raise APIError(405, "method_not_allowed", "method not allowed")

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/links/{code}/stats", response_model=LinkStatsResponse)
    async def link_stats(code: str) -> LinkStatsResponse:
        return service.stats(code)

    @application.get("/{code}")
    async def redirect_to_long_url(code: str) -> RedirectResponse:
        long_url = service.resolve(code)
        return RedirectResponse(
            url=long_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    return application


app = create_app()
