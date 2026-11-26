# Gmail push notifications

OpenPoke can receive Gmail Pub/Sub pushes at `POST /api/v1/gmail/push`. This is a fast path for
mail on threads an execution agent already owns: the reply reaches the owning agent immediately,
without waiting for the next polling pass. Unrelated mail still belongs to the important-email
polling watcher, so push does not change the memory sources or the importance model.

The code path is offline-tested with a fake Gmail API. No Google sign-in, OAuth flow, or live Gmail
call is part of the test suite.

## Owner setup

1. Finish the normal Composio Gmail connection first (`Settings -> Gmail`).
2. In a Google Cloud project you control, enable the **Gmail API** and **Cloud Pub/Sub API**.
3. Create a topic, for example `openpoke-gmail`, and grant
   `gmail-api-push@system.gserviceaccount.com` the **Pub/Sub Publisher** role on that topic.
4. Pick a long random push token and put it in `.env`:

   ```bash
   OPENPOKE_GMAIL_PUBSUB_TOPIC=projects/YOUR_PROJECT_ID/topics/openpoke-gmail
   OPENPOKE_GMAIL_PUSH_SHARED_SECRET=generate-a-long-random-string
   ```

5. Expose **only** `POST /api/v1/gmail/push` through your own HTTPS reverse proxy. Do not expose
   `/api/v1/gmail/push/watch`, `/connect`, `/status`, or any other OpenPoke route: those routes stay behind the owner-token API boundary. Only the push endpoint is exempted so Pub/Sub can use its one-purpose URL token, and the server refuses push when no shared secret is configured.
6. Create a push subscription whose endpoint includes the token:

   ```bash
   gcloud pubsub subscriptions create openpoke-gmail-push \
     --topic=projects/YOUR_PROJECT_ID/topics/openpoke-gmail \
     --push-endpoint='https://YOUR-HOST/api/v1/gmail/push?token=generate-a-long-random-string'
   ```

   Treat that subscription URL as a secret. If it leaks, rotate both the subscription URL token and
   `OPENPOKE_GMAIL_PUSH_SHARED_SECRET`.
7. Restart OpenPoke. When the topic is configured, the server registers Gmail `users.watch` and
   checks it hourly, renewing before Gmail's seven-day expiry. To force a check from the machine
   running OpenPoke:

   ```bash
   curl -H "Authorization: Bearer $OPENPOKE_OWNER_TOKEN" -X POST http://127.0.0.1:8001/api/v1/gmail/push/watch
   ```

## Multi-tenant mode

Gmail push is owner-global in this change. When `OPENPOKE_MULTI_TENANT=1`, the push endpoints
return 503 and the automatic watch renewer stays stopped with the other global Gmail services.
Do not expose the push endpoint in multi-tenant mode until tenant-aware Gmail ownership exists.

## Delivery semantics

- The first notification seeds Gmail's current history id; OpenPoke does not replay old inbox mail.
- A missed or out-of-order notification is recovered by listing history from the last stored id.
  Duplicate and older notifications are acknowledged without refetching.
- If Gmail says the stored start history id expired, OpenPoke resynchronizes to the notification's
  current history id instead of replaying an unbounded backlog.
- Message ids are stored in `server/data/gmail_push_state.json` (bounded to the most recent 500),
  so overlapping history pages and Pub/Sub retries do not surface the same reply twice.
- Only replies on threads owned by the execution-agent registry are dispatched by this fast path.
  Sent-only mail, foreign-account notifications, malformed envelopes, empty or nonpositive history
  ids, missing shared-secret configuration, and wrong shared-secret requests are ignored or rejected before any Gmail read.
