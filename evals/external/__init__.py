"""Load routing test cases published by other public OpenPoke forks into ``evals.schema`` types.

Other forks are untrusted. Adapters only parse their JSON data files from a local clone at a
pinned commit; they never import, run or copy fork code. Clones live under
``OPENPOKE_EXTERNAL_SRC/<owner>`` (default ``~/Downloads/openpoke-external-src/<owner>``).

Each adapter module exposes ``load(src_dir) -> list[(World, [Scenario])]`` and
``load_report(src_dir) -> ExternalLoad`` (the same worlds plus skip counts by reason).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from evals.schema import Scenario, World

DEFAULT_SRC_ROOT = Path.home() / "Downloads" / "openpoke-external-src"

# module name -> clone directory name (the GitHub owner).
ADAPTERS = {"kareemelmaaddawy": "KareemElMaaddawy", "draggle": "draggle"}

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ExternalSourceError(RuntimeError):
    """The local clone is missing, unreadable or not at the commit the adapter was written against."""


@dataclass
class ExternalLoad:
    owner: str
    worlds: list[tuple[World, list[Scenario]]]
    skipped: Counter = field(default_factory=Counter)  # reason -> cases not loaded
    adjusted: Counter = field(default_factory=Counter)  # lossy-but-loaded mapping -> cases

    @property
    def loaded(self) -> int:
        return sum(len(scenarios) for _, scenarios in self.worlds)


def src_root() -> Path:
    return Path(os.environ.get("OPENPOKE_EXTERNAL_SRC") or DEFAULT_SRC_ROOT)


def check_pinned(src_dir: Path, sha: str) -> None:
    """Refuse a git checkout at another commit; directories without ``.git`` (test fixtures) pass."""
    head_file = src_dir / ".git" / "HEAD"
    if not head_file.exists():
        return
    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        ref_file = src_dir / ".git" / head.split(":", 1)[1].strip()
        if not ref_file.exists():
            raise ExternalSourceError(f"{src_dir}: cannot resolve {head!r}; run `git checkout {sha}`")
        head = ref_file.read_text(encoding="utf-8").strip()
    if head != sha:
        raise ExternalSourceError(f"{src_dir} is at {head}, adapter is pinned to {sha}; run `git checkout {sha}`")


def read_json(path: Path):
    if not path.exists():
        raise ExternalSourceError(f"missing data file {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise ExternalSourceError(f"missing data file {path}")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def iso_utc(stamp: str) -> str:
    """Their "YYYY-MM-DD HH:MM:SS" (UTC, per their transcript format) -> our "YYYY-MM-DDTHH:MM:SSZ"."""
    return datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S").strftime(ISO_FORMAT)


def days_before(now_iso: str, days: float) -> str:
    now = datetime.strptime(now_iso, ISO_FORMAT).replace(tzinfo=timezone.utc)
    return (now - timedelta(days=days)).strftime(ISO_FORMAT)


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def factories(world: World, scenarios: list[Scenario]) -> tuple[Callable[[int, int], World], Callable[[World], list[Scenario]]]:
    """``world_factory`` / ``scenario_factory`` for ``evals.harness.runner.run`` with ``sizes=[world.size]``."""
    return (lambda size, seed: world), (lambda w: list(scenarios))


def load_all(root: Path | None = None) -> dict[str, ExternalLoad]:
    """Every adapter against its clone under ``root`` (default ``src_root()``)."""
    import importlib

    base = root or src_root()
    return {module: importlib.import_module(f"evals.external.{module}").load_report(base / owner)
            for module, owner in ADAPTERS.items()}
