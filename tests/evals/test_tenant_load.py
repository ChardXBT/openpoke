import json
import os
import subprocess
import sys
from pathlib import Path

from evals.tenant_load import run


def teardown_function():
    os.environ.pop("OPENPOKE_MULTI_TENANT", None)
    os.environ.pop("OPENPOKE_DATA_DIR", None)


def test_tenant_load_harness_small_run_isolated(tmp_path):
    result = run(Path(tmp_path), tenants=2, operations=2, workers=2)
    assert result["ok"] is True
    assert result["total_operations"] == 16
    assert result["isolation_mismatches"] == 0
    assert result["operations_per_second"] > 0


def test_tenant_load_harness_runs_directly_as_a_script(tmp_path):
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "evals/tenant_load.py",
            "--tenants",
            "1",
            "--operations",
            "1",
            "--workers",
            "1",
            "--data-dir",
            str(tmp_path),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["total_operations"] == 4
