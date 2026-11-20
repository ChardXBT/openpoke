# Running your own Poke

This fork is meant to be lived with, not only benchmarked. This page is the short path from
clone to a Poke that knows you, plus what it stores and how to make it forget.

## 1. Start it (five minutes, no Gmail needed)

```bash
git clone https://github.com/ChardXBT/openpoke && cd openpoke && git checkout agent-overload
python3 -m venv .venv && source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r server/requirements.txt
npm install --prefix web
cp .env.example .env
```

Put one model key in `.env`:

| You have | Put in `.env` | Default model |
|---|---|---|
| An OpenRouter key | `OPENROUTER_API_KEY=...` | `anthropic/claude-sonnet-5.5` |
| An OpenAI key | `OPENPOKE_PROVIDER=openai` and `OPENAI_API_KEY=...` | `gpt-6.1-terra` |

`OPENPOKE_MODEL` picks any other model on that provider. The memory learner runs on a small model
(`claude-haiku-5.5` or `gpt-6-luna`; `OPENPOKE_MEMORY_MODEL` to change it).

To try it without connecting your real inbox, add `OPENPOKE_FAKE_GMAIL=1`. Poke then uses a local
demo mailbox (Alice's Q3 deck and lunch threads, Bob's invoice, an Air Canada refund, a lease
renewal). Every Gmail tool works against it, and **nothing is ever sent**: "sent" mail lands in an
outbox you can read at `GET /api/v1/demo/mailbox`.

```bash
python -m server.server            # API on http://127.0.0.1:8001 (docs at /docs)
npm run dev --prefix web           # chat on http://localhost:3000
```

## 2. Connect your real Gmail

Remove `OPENPOKE_FAKE_GMAIL`, add `COMPOSIO_API_KEY` and `COMPOSIO_GMAIL_AUTH_CONFIG_ID` (from
[composio.dev](https://composio.dev/)), restart, then open *Settings → Gmail* in the web app and
finish the OAuth flow. Poke can then search, draft, reply and forward, and the important-email
watcher starts telling you about mail that matters.

## 3. Let it get to know you

Talk to it normally. After each of your messages, a small model reads **your words only** and
updates a private profile. It reads neither emails nor agent output, so a stranger's email can't
plant a "memory". It picks up:

- **people** ("my mom is Linda, linda@...", "Jordan's my partner"), so "email my mom" just works,
  and so does "my mother", "email Linda" or her address;
- **preferences** ("keep my emails short and sign them 'Cheers, Eric'", "aisle seats", "vegetarian"),
  which Poke and its email agents follow without being reminded;
- **places, routines and facts** (home city, job, school, gym days, birthday);
- **temporary things** ("I'm in Lisbon until Sunday"): they carry an end date and drop out on their own;
- **changes** ("I moved to Toronto" replaces the old city; "I sold my car" retires the car; history is kept).

You can also be explicit: "remember that my dentist is Dr. Patel", "what do you know about me?",
"forget my dentist".

The learner is measured, not just described: `python -m evals.memory.run` scores it on 51 fixed
cases, including updates, retirements, end dates, pronouns and injection attempts. Results are in
[`agent-overload.md`](agent-overload.md#personal-memory).

## 4. See what it's tracking

In the web app, the panel beside the chat shows both live: what Poke knows about you (click ✕ to
forget a fact) and its open items. The same data is available over HTTP:

| Endpoint | What you get |
|---|---|
| `GET /api/v1/me` | everything Poke has learned about you |
| `DELETE /api/v1/me/{subject}` | forget one subject, history included (e.g. `/me/dentist`) |
| `GET /api/v1/agents/open` | the open items Poke sees each turn: drafts waiting on you, replies it's waiting for, reminders |
| `POST /api/v1/demo/email` | (demo mailbox) deliver an incoming email and run the watcher now |
| `POST /api/v1/demo/reset` | (demo mailbox) start the mailbox over |

```bash
curl -s localhost:8001/api/v1/me
curl -s -X POST localhost:8001/api/v1/demo/email -H 'content-type: application/json' \
  -d '{"sender": "Alice Chen <alice.chen@acme.com>", "subject": "Re: Lunch Thursday?", "body": "12:30 works!"}'
```

## 5. Privacy and safety

- **Everything stays on your machine** in `server/data/`: conversation log, agent registry
  (`agents.db`), profile (`profile.db`), demo mailbox. Back up or wipe that folder and that's all of
  it. The only things that leave are model calls (to OpenRouter or OpenAI) and Gmail calls
  (to Composio, when connected).
- **Local only by default.** The API has no login, and it can read and send your email. So it
  listens on `127.0.0.1` and accepts browser calls only from the web app's own origin. Upstream
  listened on every interface with CORS `*`, which let anyone on your Wi‑Fi, or any web page you
  had open, drive it. Set `OPENPOKE_HOST=0.0.0.0` only behind your own auth.
- **Irreversible steps ask first when they're ambiguous.** If "send it" could mean two drafts,
  Poke asks which one instead of guessing.

## 6. What it costs

Poke's prompt stays at about 5k tokens per step whether you have 30 agents or 20,000. Upstream
grows with every agent ever created: about 14k tokens at 1,000 agents and 254k at 20,000. A
typical request is one to three Interaction Agent steps plus the email agent's work. On GPT‑6.1
Terra or Sonnet 5.5 that is a few cents per request. The memory learner adds a fraction of a cent
per message on Luna or Haiku. Measured per-turn costs for every setup and size are in
[`agent-overload.md`](agent-overload.md#results).
