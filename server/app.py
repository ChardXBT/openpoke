from __future__ import annotations

import hmac
import json

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ensure_owner_token, get_settings
from .logging_config import configure_logging, logger
from .routes import api_router
from .services import get_important_email_watcher, get_trigger_scheduler
from .services.tenancy import TenantASGIMiddleware, multi_tenant_enabled


# Register global exception handlers for consistent error responses across the API
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.debug("validation error", extra={"errors": exc.errors(), "path": str(request.url)})
        return JSONResponse(
            {"ok": False, "error": "Invalid request", "detail": exc.errors()},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        logger.debug(
            "http error",
            extra={"detail": exc.detail, "status": exc.status_code, "path": str(request.url)},
        )
        detail = exc.detail
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        return JSONResponse({"ok": False, "error": detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error", extra={"path": str(request.url)})
        return JSONResponse(
            {"ok": False, "error": "Internal server error"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


configure_logging()
_settings = get_settings()

app = FastAPI(
    title=_settings.app_name,
    version=_settings.app_version,
    docs_url=_settings.resolved_docs_url,
    redoc_url=None,
)

app.add_middleware(TenantASGIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.casefold() == "bearer" else ""


@app.middleware("http")
async def _require_owner_token(request: Request, call_next):
    """Fail closed on every API route except health; the web proxy supplies the token server-side."""
    path = request.url.path
    if not path.startswith("/api/v1") or path in {"/api/v1/health", "/api/v1/gmail/push"}:
        return await call_next(request)

    configured = (get_settings().owner_token or "").strip()
    supplied = _bearer_token(request)
    if not configured or not supplied or not hmac.compare_digest(supplied, configured):
        return JSONResponse(
            {"ok": False, "error": "Unauthorized"},
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


register_exception_handlers(app)
app.include_router(api_router)


@app.on_event("startup")
# Initialize background services (trigger scheduler and email watcher) when the app starts
async def _start_trigger_scheduler() -> None:
    ensure_owner_token(get_settings())
    if multi_tenant_enabled():
        logger.info("multi-tenant storage enabled; global scheduler, Gmail watcher and Gmail push renewal stay stopped until tenant-aware runners are configured")
        return
    scheduler = get_trigger_scheduler()
    await scheduler.start()
    watcher = get_important_email_watcher()
    await watcher.start()
    if _settings.gmail_push_topic:
        from .services.gmail.push import get_gmail_push_watch_renewer

        await get_gmail_push_watch_renewer().start()


@app.on_event("shutdown")
# Gracefully shutdown background services when the app stops
async def _stop_trigger_scheduler() -> None:
    if multi_tenant_enabled():
        return
    scheduler = get_trigger_scheduler()
    await scheduler.stop()
    watcher = get_important_email_watcher()
    await watcher.stop()
    if _settings.gmail_push_topic:
        from .services.gmail.push import get_gmail_push_watch_renewer

        await get_gmail_push_watch_renewer().stop()


__all__ = ["app"]
