"""Local tenant-isolation load harness. Offline; no model, Gmail or hosted deploy."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services.execution.registry import AgentRegistry
from server.services.gmail.seen_store import GmailSeenStore
from server.services.profile.store import ProfileStore
from server.services.tenancy import provision_tenant, scoped_data_dir, tenant_context
from server.services.triggers.store import TriggerStore


def _work(root: Path, tenant: str, operations: int) -> dict:
    started = time.perf_counter()
    with tenant_context(tenant):
        base = scoped_data_dir(root)
        profile = ProfileStore(base / "profile.db")
        registry = AgentRegistry(base / "agents.db")
        triggers = TriggerStore(base / "triggers.db")
        seen = GmailSeenStore(base / "gmail_seen.json", max_entries=max(1, operations))
        agent, _ = registry.get_or_create(f"load-{tenant}")
        for index in range(operations):
            profile.remember("fact", f"fact-{index}", f"{tenant}-memory-{index}")
            registry.add_ref(agent.id, "contact", f"{tenant}-{index}@example.test")
            triggers.insert({
                "agent_name": agent.name,
                "payload": f"{tenant}-reminder-{index}",
                "start_time": None,
                "next_trigger": None,
                "recurrence_rule": None,
                "timezone": "UTC",
                "status": "paused",
                "last_error": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            })
            seen.mark_seen([f"{tenant}-mail-{index}"])
        facts = [f.value for f in profile.current()]
        contacts = [r.name for r in registry.owners("contact", f"{tenant}-0@example.test")]
        rows = triggers.list_for_agent(agent.name)
        mail = seen.snapshot()
    return {
        "tenant": tenant,
        "operations": operations * 4,
        "seconds": time.perf_counter() - started,
        "facts": len(facts),
        "contacts": len(contacts),
        "triggers": len(rows),
        "mail": len(mail),
        "foreign_bytes": (
            sum(1 for value in facts if not value.startswith(tenant + "-memory-"))
            + sum(1 for value in mail if not value.startswith(tenant + "-mail-"))
        ),
    }


def run(root: Path, tenants: int, operations: int, workers: int) -> dict:
    os.environ["OPENPOKE_MULTI_TENANT"] = "1"
    os.environ["OPENPOKE_DATA_DIR"] = str(root)
    root.mkdir(parents=True, exist_ok=True)
    names = [f"tenant-{index}" for index in range(tenants)]
    for name in names:
        provision_tenant(name, root)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda name: _work(root, name, operations), names))
    elapsed = time.perf_counter() - started
    total_ops = sum(r["operations"] for r in results)
    mismatches = sum(r["foreign_bytes"] for r in results)
    incomplete = [
        r["tenant"]
        for r in results
        if r["facts"] != operations or r["contacts"] != 1 or r["triggers"] != operations or r["mail"] != operations
    ]
    return {
        "schema": 1,
        "tenants": tenants,
        "operations_per_tenant": operations,
        "total_operations": total_ops,
        "workers": workers,
        "elapsed_seconds": round(elapsed, 3),
        "operations_per_second": round(total_ops / elapsed, 1) if elapsed else None,
        "isolation_mismatches": mismatches,
        "incomplete_tenants": incomplete,
        "max_tenant_seconds": round(max(r["seconds"] for r in results), 3) if results else 0,
        "ok": not mismatches and not incomplete,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenants", type=int, default=8)
    parser.add_argument("--operations", type=int, default=25)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.tenants < 1 or args.operations < 1 or args.workers < 1:
        raise SystemExit("tenants, operations and workers must be positive")
    if args.data_dir:
        result = run(Path(args.data_dir), args.tenants, args.operations, args.workers)
    else:
        with tempfile.TemporaryDirectory(prefix="openpoke-tenant-load-") as tmp:
            result = run(Path(tmp), args.tenants, args.operations, args.workers)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
