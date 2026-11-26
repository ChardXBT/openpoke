"""Tenant-scoped local storage groundwork.

Disabled by default. With OPENPOKE_MULTI_TENANT=1 every tenant receives a random
storage namespace beneath the data directory; the public tenant id is never a
path component. Retiring and reusing an id creates a fresh namespace, so a new
tenant cannot inherit the previous tenant's files.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from contextlib import contextmanager

if os.name == "nt":
    import msvcrt
else:
    import fcntl
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional

_NAMESPACE = re.compile(r"[a-f0-9]{32}")
MAX_TENANT_ID_LENGTH = 128
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_current_tenant: ContextVar[Optional[str]] = ContextVar("openpoke_tenant", default=None)
_current_namespace: ContextVar[Optional[str]] = ContextVar("openpoke_tenant_namespace", default=None)
_registry_lock = threading.RLock()


class TenantError(RuntimeError):
    pass


def multi_tenant_enabled() -> bool:
    return (os.getenv("OPENPOKE_MULTI_TENANT") or "0").strip().lower() in {"1", "true", "yes", "on"}


def data_root() -> Path:
    return Path(os.getenv("OPENPOKE_DATA_DIR") or _DEFAULT_DATA_DIR)


def validate_tenant_id(tenant_id: object) -> str:
    """Validate the opaque public tenant id without using it as a path.

    Separators, unicode and spaces inside the id are legal: storage uses a random
    namespace instead. Control characters, outer whitespace, non-strings and very
    long ids are malformed and fail closed.
    """
    if not isinstance(tenant_id, str):
        raise TenantError("tenant id must be a string")
    if not tenant_id or not tenant_id.strip():
        raise TenantError("tenant id is required")
    if tenant_id != tenant_id.strip():
        raise TenantError("tenant id has leading or trailing whitespace")
    if len(tenant_id) > MAX_TENANT_ID_LENGTH:
        raise TenantError("tenant id is too long")
    try:
        tenant_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TenantError("tenant id is not valid UTF-8") from exc
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in tenant_id):
        raise TenantError("tenant id contains a control character")
    return tenant_id


def _registry_path(root: Optional[Path] = None) -> Path:
    return (root or data_root()) / "tenants" / "registry.json"


@contextmanager
def _registry_file_lock(root: Optional[Path] = None) -> Iterator[None]:
    """Serialize registry read/modify/write cycles across writer processes.

    The lock file itself may remain after a crash; the OS releases the lock when
    the process dies. Registry payloads are still replaced atomically below, so a
    crashed writer leaves either the old or the new complete JSON document.
    """
    path = _registry_path(root).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(root: Optional[Path] = None) -> dict:
    try:
        data = json.loads(_registry_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"active": {}, "retired": {}}
    except Exception as exc:
        raise TenantError("tenant registry is unreadable; refusing storage access") from exc
    if not isinstance(data, dict) or not isinstance(data.get("active"), dict) or not isinstance(data.get("retired"), dict):
        raise TenantError("tenant registry is corrupt; refusing storage access")
    return data


def _write(data: dict, root: Optional[Path] = None) -> None:
    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def provision_tenant(tenant_id: object, root: Optional[Path] = None) -> str:
    """Return the storage namespace for a tenant, creating it under a cross-process
    file lock. Re-provisioning an active id is stable; a retired id gets a fresh
    namespace rather than the old data."""
    public = validate_tenant_id(tenant_id)
    with _registry_lock, _registry_file_lock(root):
        data = _load(root)
        existing = data["active"].get(public)
        if isinstance(existing, str) and _NAMESPACE.fullmatch(existing):
            return existing
        namespace = secrets.token_hex(16)
        data["active"][public] = namespace
        data["retired"].pop(public, None)
        _write(data, root)
        return namespace


def retire_tenant(tenant_id: object, root: Optional[Path] = None) -> bool:
    public = validate_tenant_id(tenant_id)
    with _registry_lock, _registry_file_lock(root):
        data = _load(root)
        namespace = data["active"].pop(public, None)
        if not namespace:
            return False
        data["retired"][public] = namespace
        _write(data, root)
        return True


def tenant_namespace(tenant_id: object, root: Optional[Path] = None) -> str:
    public = validate_tenant_id(tenant_id)
    data = _load(root)
    namespace = data["active"].get(public)
    if not isinstance(namespace, str):
        raise TenantError("unknown tenant id")
    return namespace


@contextmanager
def tenant_context(tenant_id: object) -> Iterator[str]:
    public = validate_tenant_id(tenant_id)
    namespace = tenant_namespace(public)
    tenant_token = _current_tenant.set(public)
    namespace_token = _current_namespace.set(namespace)
    try:
        yield namespace
    finally:
        _current_namespace.reset(namespace_token)
        _current_tenant.reset(tenant_token)


def current_tenant() -> str:
    tenant = _current_tenant.get()
    if not tenant:
        raise TenantError("tenant id is required in multi-tenant mode")
    return tenant


def current_namespace() -> str:
    namespace = _current_namespace.get()
    if namespace:
        return namespace
    return tenant_namespace(current_tenant())


def scoped_data_dir(base: Path) -> Path:
    if not multi_tenant_enabled():
        return base
    return base / "tenants" / current_namespace()


class TenantASGIMiddleware:
    """Require and scope X-Openpoke-Tenant when multi-tenant mode is enabled."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not multi_tenant_enabled():
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.endswith("/health"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        tenant = headers.get("x-openpoke-tenant", "")
        try:
            namespace = provision_tenant(tenant)
        except TenantError as exc:
            from fastapi.responses import JSONResponse
            response = JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            await response(scope, receive, send)
            return
        tenant_token = _current_tenant.set(tenant)
        namespace_token = _current_namespace.set(namespace)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_namespace.reset(namespace_token)
            _current_tenant.reset(tenant_token)
