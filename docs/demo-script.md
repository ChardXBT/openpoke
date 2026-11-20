# Live demo (5–6 minutes)

Everything runs against the local demo mailbox, so there's no real inbox and nothing is sent.
The panel on the right shows what Poke knows about you and what it's tracking, live. Every
step below was run end to end on 2026-11-20 in the web UI. The screenshots in the README come
from that run.

## Setup (once)

In `.env`, next to your key:

```bash
OPENPOKE_PROVIDER=openai        # or anthropic (ANTHROPIC_API_KEY), or leave it out for OPENROUTER_API_KEY
OPENPOKE_FAKE_GMAIL=1
```

Before each rehearsal, stop the server and wipe the demo data. That folder holds this
checkout's chat, agents, profile and mailbox, so don't run this against your own Poke's data.

```bash
rm -rf server/data
python -m server.server            # wait ~5 s: the watcher's first poll marks old mail as read
python -m evals.seed_demo --agents 5000   # months of finished history; free, no model calls
npm run dev --prefix web           # open http://localhost:3000 in a wide window (the panel needs ≥1024 px)
```

**Rule of thumb while presenting:** wait for each reply, and for drafts to appear, before the
next message. Poke drafts and asks before it sends, and it won't send a draft you haven't seen.

## 1. It gets to know you (30 s)

> hey! quick intro: my mom is Linda Song, linda.song@gmail.com. I keep emails short and sign them "Cheers, Eric". and I'm in Lisbon until Sunday

The panel fills in: **mom** (with her email), **email style**, **current location: Lisbon
(until 2026‑11‑22)**. *Say: no forms. It learned from how I talk, and the Lisbon fact expires on
its own after Sunday.*

## 2. It knows who you mean (60 s)

> email my mother that I'll call her Sunday evening

The draft goes to linda.song@gmail.com, short and signed "Cheers, Eric". *Say: I said "my
mother", it stored "mom", and the draft follows my style without my repeating it.*

> also draft alice chen a note asking for her comments on the q3 deck by next friday

Wait until **Open items** shows two drafts *waiting on you*.

## 3. It asks instead of guessing (45 s)

> what's waiting on me?

> send it

Poke asks **"Which one: Linda's note or Alice's?"** *Say: two drafts are open and nothing
singles one out, so the send tool refuses an irreversible step on an ambiguous reference. That's
enforced in code, not left to the prompt. OpenPoke today would pick one.*

> the one to my mom

"Sent to Linda." The outbox in the panel shows it, and Linda's item becomes *waiting on reply*.

## 4. New work stays separate, and the reply finds its thread (90 s)

> ask alice if thursday lunch works

Point at the panel: a **new** item, *Alice Chen – Thursday lunch*. *Say: Alice already has an
open item, the deck, and the old router would have reused it because the name matched. The
resolver sees that "lunch" fits none of her open items, so it's new work.*

When the draft shows up:

> looks good, send the lunch one

Then click **Alice replies about lunch** in the panel. Poke tells you "Alice says Thursday works
and suggests 12:30 at Pai on Duncan". No new agent appears, and the lunch item leaves *Open
items*: a reply on the thread it was waiting on closes the loop. *Say: the notification carries
the thread id, so routing uses Gmail's metadata, not the email's text. A sender can't steer it by
writing an agent's name.*

## 4b. It still finds old things among thousands (30 s)

> where did we book dim sum in the annex back in september?

> how much was my property tax bill?

Poke finds the September booking and the $64.20 bill with `search_agents`, among 5,000 past
agents it never puts in the prompt. *Say: the panel says "2 of 5,000 agents". OpenPoke today puts
all 5,000 names in every prompt, about 30k tokens of names alone. Mine shows the open items and
searches for the rest.*

## 5. You stay in control (30 s)

> what do you know about me?

> forget that I'm in Lisbon, the trip got cancelled

Or click ✕ next to any fact. The same profile is at `GET /api/v1/me`. It lives in
`server/data/profile.db` on this machine and nowhere else.

## If something goes differently

- **"send it" sends instead of asking.** That's correct when only one draft is ready or Poke just
  pointed at one. Say so: it asks only when it's genuinely ambiguous.
- **"send" is answered with "I haven't shown you the draft yet".** The draft was still being
  written. Wait for it.
- **The demo buttons** skip one step of the real watcher, the model call that decides whether an
  email matters, so the demo doesn't depend on it. Everything after that step is the real path.

## Questions you may get

- *How does it scale?* The agents view Poke sees is about 30 lines whether there are 30 agents or
  20,000 (`GET /api/v1/agents/open`). Numbers are in `docs/agent-overload.md`.
- *What if two items really match?* It asks, and the tool won't send until you choose.
- *Where does memory come from?* Only your own messages. Emails and agent output are never
  learned from, so a stranger can't plant a "fact" by emailing you.
