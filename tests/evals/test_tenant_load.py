import os
from pathlib import Path

from evals.tenant_load import run


def teardown_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def test_tenant_load_harness_small_run_isolated(tmp_path):
    result = run(Path(tmp_path), tenants=2, operations=2, workers=2)
    assert result["ok"] is True
    assert result["total_operations"] == 12
    assert result["isolation_mismatches"] == 0
    assert result["operations_per_second"] > 0
