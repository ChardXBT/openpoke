# Multi-tenant storage groundwork (2026-11-26)

## Scope and threat model

This is local groundwork only. It is not hosted multi-user deployment, authentication, authorization, or a claim
that the existing single-user background workers are multi-tenant. The trust boundary begins *after* a future host
has authenticated a user and mapped that user to a stable, non-reassignable tenant id.

The experimental flag is `OPENPOKE_MULTI_TENANT_STORAGE=1`. With the flag off, OpenPoke keeps the current
single-user paths and singleton behavior. With it on:

- every API request must carry `X-OpenPoke-Tenant`; a missing, empty, overlong or control-character id fails closed
  with HTTP 400 before service storage is touched;
- the id is never used as a filesystem path. It is UTF-8 encoded and SHA-256 hashed, and storage lives under
  `server/data/tenants/<32 hex chars>/`;
- memory (`profile.db`), execution-agent/contact references (`execution_agents/agents.db`), reminder triggers
  (`triggers.db`) and Gmail seen-mail state (`gmail_seen.json`) are separate per tenant;
- a context variable carries the tenant through the request task. Store factories fail closed when no tenant is
  bound, so a wrong call path cannot silently fall back to the legacy owner's data.

The header is a storage-contract stand-in, not authorization. A real deployment must set the tenant from an
authenticated principal and must never let callers choose it directly.

## Why separate directories instead of a tenant column

The existing stores have different schemas and several file-backed journals. Adding `tenant_id` correctly to every
SQLite table, FTS row, migration, uniqueness rule and file format in one step would be a larger migration with more
ways to miss a read. Separate tenant namespaces make the default failure mode an empty database, not another
tenant's row. Hashing the external id means unicode, slashes, dots, long ids and look-alike strings cannot escape
the tenant directory.

SQLite may reuse local row ids inside each tenant database. That is safe because ids are never global references in
this design: `#1` or trigger `1` is resolved only through the registry/trigger store for the current tenant. Tenant
identity ids themselves must still be non-reassignable; no storage system can distinguish a new human who is handed
an old human's id.

## Implemented storage routing

Behind the flag these factories resolve tenant-local stores and cache one instance per tenant:

- `get_profile_store()` — private memory, including people/contact facts;
- `get_agent_registry()` — execution agents and contact/subject/thread references;
- `get_trigger_service()` — reminder storage;
- `get_gmail_seen_store()` — seen Gmail message ids;
- `get_timezone_store()` — tenant timezone preference;
- `get_conversation_log()` and `get_working_memory_log()` — chat and working-memory journals;
- `get_execution_agent_logs()` and `get_agent_roster()` — execution journals and legacy roster compatibility.

Trigger and execution-log tool code now resolves its service at call time instead of capturing the legacy singleton
at import time. Execution batching and per-agent locks are additionally keyed by tenant so concurrent tenants do not
share a result batch or serialize unrelated agents through one global lock.

The legacy single-user trigger scheduler and Gmail watcher are deliberately not started while the experimental flag
is on. They are process-wide workers designed around one account; tenant fan-out needs authenticated tenant
enumeration, per-tenant context propagation, and duplicate/rate-limit control before it is safe. This is documented
follow-up work, not hidden behavior.

## Tests and local load evidence

`tests/test_tenant_storage.py` proves, with no network or model calls:

- missing tenant ids fail closed for memory, registry/contacts, reminders and mail state;
- one tenant cannot read another tenant's memory facts, contacts, reminders or seen mail ids;
- separator-containing, unicode and look-alike ids remain inside their own hashed namespace;
- reused local SQLite ids resolve only in the current tenant;
- concurrent writes by two tenants remain separate;
- malformed empty/overlong/control/non-string ids fail closed.

Local harness: `python scripts/load_test_tenants.py --tenants 8 --operations 25 --workers 8`.

Observed on this worker (Python 3.11, local temporary disk): 800 storage operations in 0.497 wall seconds,
1,610.7 operations/second; mean tenant time 0.488s, max 0.494s. The harness uses eight concurrent tenants and covers
profile, registry/contact, trigger and Gmail-seen writes. It uses only temporary local files and no hosted deploy.

## Follow-up before any hosted use

1. Authenticate users and derive tenant ids server-side; remove caller-selected headers.
2. Enumerate tenants deliberately and run reminder/mail workers once per tenant with context propagation, quotas,
   per-tenant rate limits and deadlock/ starvation protections.
3. Decide migration/export for the legacy single-user data; do not silently copy it into a tenant.
4. Finish the audit of any remaining long-lived execution/interaction object that captures tenant state across
   requests, especially once background workers are reintroduced.
5. Add cleanup/deletion/export APIs per tenant and test deletion, backup/restore, clock/timezone behavior and
   cross-process file locking.
