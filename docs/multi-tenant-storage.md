# Multi-tenant storage groundwork

Status: behind `OPENPOKE_MULTI_TENANT=1`; default single-user behavior is unchanged. This is local-hosting groundwork only. It is not a hosted deployment, not an authentication system, and not yet safe to expose beyond a trusted local proxy.

## Threat model

The first multi-user risk is accidental cross-tenant reads caused by shared files, singleton objects or numeric agent ids. The design assumes one OpenPoke process can serve several tenants concurrently. A tenant must never read another tenant's profile memory, contacts/reference data, mail state, reminders, conversation memory or execution logs, even when:

- the tenant id is missing, malformed or path-like;
- two requests use the same agent names or numeric ids at the same time;
- a tenant id is retired and later issued to a different person;
- two tenants perform reads and writes concurrently.

A tenant id is a routing identifier only. It is not a secret and must not become a filesystem path.

## Design

`server/services/tenancy.py` owns tenant validation and namespace allocation:

- ids are 1-64 characters of ASCII letters, digits, dot, underscore or hyphen, and must start alphanumeric;
- `data/tenants/registry.json` maps a public tenant id to a random 128-bit storage namespace;
- tenant files live below `data/tenants/<random-namespace>/`; the public id never appears in the path;
- retiring a tenant removes the active mapping. Provisioning the same public id again allocates a new random namespace, so id reuse cannot inherit retired files;
- corrupt or unreadable registry state fails closed rather than falling back to shared storage.

No existing single-user data is migrated automatically; enabling the flag starts each provisioned tenant with empty tenant-local storage. Migration/export is an owner decision because it changes data ownership.

When the flag is on, `TenantASGIMiddleware` requires `X-Openpoke-Tenant` on API calls except health checks. The middleware provisions the id and places it in a `ContextVar`, so concurrent async tasks resolve their own namespace. Missing or malformed ids are rejected before request handling.

The following singleton storage factories are tenant-aware caches keyed by random namespace:

- profile memory (`profile.db`);
- execution-agent registry and contacts (`execution_agents/agents.db`);
- reminders/triggers (`triggers.db`);
- conversation and working-memory logs;
- demo mailbox and Gmail seen state;
- execution-agent logs, legacy roster and timezone preference.

Numeric agent ids and trigger ids remain local to a tenant database. They are not global handles and must never be exposed across tenant boundaries.

Gmail account activation is scoped by tenant namespace. Without a tenant context there is no active Gmail account, so execution code fails closed instead of using whichever account connected last.

## Explicitly not done yet

- `X-Openpoke-Tenant` is a groundwork contract, not authentication. A real deployment still needs a trusted identity boundary that authenticates the user and sets this header/server-side context. Never let clients spoof it through an untrusted proxy.
- The global trigger scheduler and important-email watcher are stopped when the flag is enabled. Tenant-aware runners must iterate active tenant contexts explicitly before reminders or proactive mail are enabled.
- Tenant registry updates are sufficient for the local load harness but need a transactional database or file lock before multiple writer processes use them.
- Retention/deletion currently tombstones the mapping; secure deletion of the retired namespace is a separate owner decision.
- Composio/Gmail connection lifecycle needs a live owner review. The code scopes the active account, but OAuth account ownership must be verified with the real provider before deployment.
- Extra memory sources remain disabled; this change does not add or enable any memory source.

## Local verification

Offline tests cover missing/wrong/path-like ids, concurrent profile access, contact/reference isolation, tenant-local numeric ids, reminder isolation, conversation memory isolation, Gmail active-account isolation, and id reuse after retirement.

`python evals/tenant_load.py` runs a local filesystem/SQLite load harness with no hosted deploy, network service, model key or real mail. It reports throughput and fails if any tenant observes foreign memory or incomplete local state.
