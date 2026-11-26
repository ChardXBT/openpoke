from __future__ import annotations

import threading
from pathlib import Path

from ..tenancy import cached_tenant_store
from .models import TriggerRecord
from .service import TriggerService
from .store import TriggerStore


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_default_db_path = _DATA_DIR / "triggers.db"
_trigger_service: TriggerService | None = None
_trigger_lock = threading.Lock()
_trigger_cache: dict[str, TriggerService] = {}


def get_trigger_service() -> TriggerService:
    def legacy() -> TriggerService:
        return TriggerService(TriggerStore(_default_db_path))

    def tenant(root: Path) -> TriggerService:
        return TriggerService(TriggerStore(root / "triggers.db"))

    def set_legacy(instance: TriggerService) -> None:
        global _trigger_service
        _trigger_service = instance

    return cached_tenant_store(
        cache=_trigger_cache,
        lock=_trigger_lock,
        legacy_cache_name="default",
        legacy_factory=legacy,
        tenant_factory=tenant,
        base_data_dir=_DATA_DIR,
        set_legacy=set_legacy,
    )


__all__ = [
    "TriggerRecord",
    "TriggerService",
    "get_trigger_service",
]
