"""Simple agent roster management - just a list of agent names."""

import json
import threading
import time
from pathlib import Path

from ...logging_config import logger
from ..tenancy import cached_tenant_store

try:  # POSIX
    import fcntl
except ImportError:  # Windows has no fcntl
    fcntl = None
try:  # Windows
    import msvcrt
except ImportError:
    msvcrt = None


def _lock_file(handle) -> None:
    """Take a non-blocking exclusive lock; raise BlockingIOError if it is held."""
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc


def _unlock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:
        try:
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


class AgentRoster:
    """Simple roster that stores agent names in a JSON file."""

    def __init__(self, roster_path: Path):
        self._roster_path = roster_path
        self._agents: list[str] = []
        self.load()

    def load(self) -> None:
        """Load agent names from roster.json."""
        if self._roster_path.exists():
            try:
                with open(self._roster_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._agents = [str(name) for name in data]
            except Exception as exc:
                logger.warning(f"Failed to load roster.json: {exc}")
                self._agents = []
        else:
            self._agents = []
            self.save()

    def save(self) -> None:
        """Save agent names to roster.json with file locking."""
        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                self._roster_path.parent.mkdir(parents=True, exist_ok=True)

                # Open file and acquire exclusive lock
                with open(self._roster_path, 'w') as f:
                    _lock_file(f)
                    try:
                        json.dump(self._agents, f, indent=2)
                        return
                    finally:
                        _unlock_file(f)

            except BlockingIOError:
                # Lock is held by another process
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.warning("Failed to acquire lock on roster.json after retries")
            except Exception as exc:
                logger.warning(f"Failed to save roster.json: {exc}")
                break

    def add_agent(self, agent_name: str) -> None:
        """Add an agent to the roster if not already present."""
        if agent_name not in self._agents:
            self._agents.append(agent_name)
            self.save()

    def get_agents(self) -> list[str]:
        """Get list of all agent names."""
        return list(self._agents)

    def clear(self) -> None:
        """Clear the agent roster."""
        self._agents = []
        try:
            if self._roster_path.exists():
                self._roster_path.unlink()
            logger.info("Cleared agent roster")
        except Exception as exc:
            logger.warning(f"Failed to clear roster.json: {exc}")


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ROSTER_PATH = _DATA_DIR / "execution_agents" / "roster.json"

_agent_roster: AgentRoster | None = None
_roster_lock = threading.Lock()
_roster_cache: dict[str, AgentRoster] = {}


def get_agent_roster() -> AgentRoster:
    """Get the legacy singleton or current tenant's isolated roster."""

    def legacy() -> AgentRoster:
        return AgentRoster(_ROSTER_PATH)

    def tenant(root: Path) -> AgentRoster:
        return AgentRoster(root / "execution_agents" / "roster.json")

    def set_legacy(instance: AgentRoster) -> None:
        global _agent_roster
        _agent_roster = instance

    return cached_tenant_store(
        cache=_roster_cache,
        lock=_roster_lock,
        legacy_cache_name="default",
        legacy_factory=legacy,
        tenant_factory=tenant,
        base_data_dir=_DATA_DIR,
        set_legacy=set_legacy,
    )
