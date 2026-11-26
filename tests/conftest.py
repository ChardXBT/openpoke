"""Keep the offline suite independent of the developer's .env.

server.config loads .env into os.environ for keys that aren't set yet, so a personal
OPENPOKE_PROVIDER=anthropic (or demo mode) would change test behaviour and could even make
a real network call. Pin the defaults before anything imports server.config.
"""

import os

for _name, _value in {
    "OPENPOKE_PROVIDER": "openrouter",
    "OPENPOKE_FAKE_GMAIL": "0",
    "OPENPOKE_MODEL": "",
    "OPENPOKE_MEMORY_MODEL": "",
    "ANTHROPIC_WORKSPACE_ID": "",
    "OPENPOKE_OWNER_TOKEN": "offline-test-owner-token",
}.items():
    os.environ[_name] = _value
