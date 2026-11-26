#!/usr/bin/env python3
"""Local tenant-storage load harness. No network, models, hosted deploy, or real mail."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def configure(root: Path) -> None:
    os.environ["OPENPOKE_MULTI_TENANT_STORAGE"] = "1"
    import server.services.execution.registry as registry_module
    import server.services.gmail.seen_store as seen_module
    import server.services.profile.store as profile_module
    import server.services.triggers as triggers_module

    profile_module._DATA_DIR = root
    registry_module._DATA_DIR = root
    triggers_module._DATA_DIR = root
    seen_module._DEFAULT_BASE_DIR = root
    profile_module._store_cache = {}
    registry_module._registry_cache = {}
    triggers_module._trigger_cache = {}
    seen_module._seen_cache = {}


def tenant_work(tenant_index: int, operations: int) -> dict[str, float | int]:
    from server.services.execution.registry import get_agent_registry
    from server.services.gmail.seen_store import get_gmail_seen_store
    from server.services.profile.store import get_profile_store
    from server.services.tenancy import use_tenant
    from server.services.triggers import get_trigger_service

    started = time.perf_counter()
    completed = 0
    with use_tenant(f"load-tenant-{tenant_index}"):
        for index in range(operations):
            get_profile_store().remember("person", f"contact {index}", f"Person {index}", email=f"p{index}@x.test")
            record, _ = get_agent_registry().get_or_create(f"agent {index}", topic=f"topic {index}")
            get_agent_registry().add_ref(record.id, "contact", f"p{index}@x.test")
            get_trigger_service().create_trigger(agent_name=f"agent {index}", payload=f"reminder {index}")
            get_gmail_seen_store().mark_seen([f"mail-{index}"])
            completed += 4
    return {"operations": completed, "seconds": time.perf_counter() - started}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenants", type=int, default=8)
    parser.add_argument("--operations", type=int, default=25, help="profile+registry+trigger+mail groups per tenant")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="openpoke-tenant-load-") as temp:
        configure(Path(temp))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows = list(pool.map(lambda i: tenant_work(i, args.operations), range(args.tenants)))
        elapsed = time.perf_counter() - started

    durations = [float(row["seconds"]) for row in rows]
    operations = sum(int(row["operations"]) for row in rows)
    result = {
        "tenants": args.tenants,
        "workers": args.workers,
        "storage_operations": operations,
        "wall_seconds": round(elapsed, 3),
        "operations_per_second": round(operations / elapsed, 1),
        "tenant_seconds_mean": round(statistics.mean(durations), 3),
        "tenant_seconds_max": round(max(durations), 3),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
