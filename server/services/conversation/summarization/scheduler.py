from __future__ import annotations

import asyncio

from ....logging_config import logger
from ...tenancy import current_namespace, multi_tenant_enabled
from .summarizer import summarize_conversation

_pending: set[str] = set()
_running: set[str] = set()


def _tenant_key() -> str:
    return current_namespace() if multi_tenant_enabled() else "single"


def schedule_summarization() -> None:
    """Schedule a background summarization pass for this tenant if not already queued."""
    key = _tenant_key()
    _pending.add(key)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("summarization skipped (no running event loop)")
        return

    if key not in _running:
        loop.create_task(_run_worker(key))


async def _run_worker(key: str) -> None:
    if key in _running:
        return

    _running.add(key)
    try:
        while key in _pending:
            _pending.discard(key)
            try:
                await summarize_conversation()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "summarization worker failed",
                    extra={"error": str(exc)},
                )
    finally:
        _running.discard(key)


__all__ = ["schedule_summarization"]
