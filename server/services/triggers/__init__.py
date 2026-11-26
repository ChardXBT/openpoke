from __future__ import annotations

import threading
from pathlib import Path

from ..tenancy import current_namespace, data_root, multi_tenant_enabled, scoped_data_dir
from .models import TriggerRecord
from .service import TriggerService
from .store import TriggerStore


def _default_db_path() -> Path:
    return scoped_data_dir(data_root()) / "triggers.db"


_lock = threading.Lock()
_service = None
_tenant_services = {}


def get_trigger_service() -> TriggerService:
    global _service
    with _lock:
        if multi_tenant_enabled():
            namespace = current_namespace()
            if namespace not in _tenant_services:
                _tenant_services[namespace] = TriggerService(TriggerStore(_default_db_path()))
            return _tenant_services[namespace]
        if _service is None:
            _service = TriggerService(TriggerStore(_default_db_path()))
        return _service


__all__ = [
    "TriggerRecord",
    "TriggerService",
    "get_trigger_service",
]
