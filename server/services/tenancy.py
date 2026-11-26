"""Tenant-scoped local storage groundwork.

When enabled, each resolved tenant gets an isolated data directory beneath
``data/tenants``. Tenant identifiers are never used as paths directly: they are
validated, UTF-8 encoded and hashed, so unicode, separators, dots and very long
values cannot escape or collide with the legacy single-user files.
"""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterator, TypeVar

DEFAULT_TENANT = "default"
MAX_TENANT_ID_LENGTH = 128
TENANT_ENV = "OPENPOKE_MULTI_TENANT_STORAGE"

_current_tenant: ContextVar[str | None] = ContextVar("openpoke_current_tenant", default=None)
T = TypeVar("T")


class TenantResolutionError(RuntimeError):
    """The multi-tenant store was asked to read without a safe tenant identity."""


def multi_tenant_storage_enabled() -> bool:
    return (os.getenv(TENANT_ENV) or "0").strip().lower() in {"1", "true", "yes", "on"}


def validate_tenant_id(value: object) -> str:
    if value is None:
        raise TenantResolutionError("tenant id is required")
    if not isinstance(value, str):
        raise TenantResolutionError("tenant id must be a string")
    tenant = value.strip()
    if not tenant:
        raise TenantResolutionError("tenant id is required")
    if len(tenant) > MAX_TENANT_ID_LENGTH:
        raise TenantResolutionError("tenant id is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in tenant):
        raise TenantResolutionError("tenant id contains a control character")
    return tenant


def current_tenant_id() -> str:
    """Return the context tenant, failing closed when the flag is on and it is absent."""
    if not multi_tenant_storage_enabled():
        return DEFAULT_TENANT
    raw = _current_tenant.get()
    if raw is None:
        raise TenantResolutionError("tenant id is required")
    return validate_tenant_id(raw)


@contextmanager
def use_tenant(tenant_id: object) -> Iterator[str]:
    tenant = validate_tenant_id(tenant_id)
    token = _current_tenant.set(tenant)
    try:
        yield tenant
    finally:
        _current_tenant.reset(token)


def tenant_storage_dir(base_data_dir: Path, tenant_id: object | None = None) -> Path:
    """The isolated directory for a tenant, or the legacy root when the flag is off."""
    if not multi_tenant_storage_enabled():
        return base_data_dir
    tenant = validate_tenant_id(tenant_id) if tenant_id is not None else current_tenant_id()
    digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:32]
    return base_data_dir / "tenants" / digest


def cached_tenant_store(
    *,
    cache: dict[str, T],
    lock: threading.Lock,
    legacy_cache_name: str,
    legacy_factory: Callable[[], T],
    tenant_factory: Callable[[Path], T],
    base_data_dir: Path,
    set_legacy: Callable[[T], None],
) -> T:
    """Return a process-wide legacy store or a cached tenant-local store.

    This helper keeps the flag-off behavior identical while making tenant cache
    population atomic. ``legacy_cache_name`` is used only as a cache key shape.
    """
    if not multi_tenant_storage_enabled():
        legacy_key = f"legacy:{legacy_cache_name}"
        with lock:
            instance = cache.get(legacy_key)
            if instance is None:
                instance = legacy_factory()
                cache[legacy_key] = instance
                set_legacy(instance)
            return instance
    key = current_tenant_id()
    cache_key = f"tenant:{key}"
    with lock:
        instance = cache.get(cache_key)
        if instance is None:
            instance = tenant_factory(tenant_storage_dir(base_data_dir, key))
            cache[cache_key] = instance
        return instance
