from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .logging_config import configure_logging, logger
from .routes import api_router
from .services import get_important_email_watcher, get_trigger_scheduler
from .services.tenancy import TenantResolutionError, multi_tenant_storage_enabled, use_tenant, validate_tenant_id


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def tenant_storage_context(request: Request, call_next):
    """Bind experimental storage to the request tenant; fail closed when absent."""
    if not multi_tenant_storage_enabled():
        return await call_next(request)
    try:
        tenant = validate_tenant_id(request.headers.get("x-openpoke-tenant"))
    except TenantResolutionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)
    with use_tenant(tenant):
        return await call_next(request)

register_exception_handlers(app)
app.include_router(api_router)


@app.on_event("startup")
# Initialize background services (trigger scheduler and email watcher) when the app starts
async def _start_trigger_scheduler() -> None:
    if multi_tenant_storage_enabled():
        logger.warning("multi-tenant storage is experimental; legacy background loops are disabled")
        return
    scheduler = get_trigger_scheduler()
    await scheduler.start()
    watcher = get_important_email_watcher()
    await watcher.start()


@app.on_event("shutdown")
# Gracefully shutdown background services when the app stops
async def _stop_trigger_scheduler() -> None:
    if multi_tenant_storage_enabled():
        return
    scheduler = get_trigger_scheduler()
    await scheduler.stop()
    watcher = get_important_email_watcher()
    await watcher.stop()


__all__ = ["app"]
