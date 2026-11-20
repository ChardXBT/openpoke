"""Routing scenarios (spec section 7) built on a world's core agents.

Seed 1 gives the "dev" split and seed 2 the "heldout" split. Each split has its own
random stream and its own wording (asks, relays, chatter, messages), so the two share
no scenario text. Scenarios read only core agents, so they are identical at every
world size for a seed.

History rules that keep gold labels honest:
- an agent turn is stamped at that agent's ``last_active_at`` and reports its current
  state (draft id, thread id or trigger id; done agents report their result);
- only the turns a family needs mention open items; background turns are chatter or
  results from done agents.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from evals.schema import EmailEvent, Gold, Scenario, Turn, World, WorldAgent
from evals.world.generate import (
    CORE_AGENT_COUNT, CORE_IDS, CORE_SPEC_BY_ID, CORE_SPECS, contact, iso, parse_iso,
)

SPLIT_BY_SEED = {1: "dev", 2: "heldout"}

# Human labels poke uses for core matters (world-level, shared by both splits).
_SHORT = {
    "alice_deck": "the Q3 deck", "alice_lunch": "lunch Thursday", "acme_renewal": "the Acme renewal redlines",
    "alice_travel": "Lisbon fares", "romero_brand": "the brand guidelines", "allison_intro": "the Allison/Jordan intro",
    "priya_sow": "the revised SOW", "marcus_onboarding": "the onboarding call", "omar_invoice": "invoice #1042",
    "jen_workshop": "the workshop agenda", "landlord_lease": "the lease renewal",
    "bob_reimburse": "the concert tickets", "accountant_call": "your 2pm call with Ken tomorrow",
    "spin_class": "Tuesday spin", "costco_receipt": "the Costco receipt", "weather_hike": "Saturday's forecast",
}
_BACKGROUND_DONE = ("costco_receipt", "weather_hike")  # done agents surfaced this morning


# --------------------------------------------------------------------------- wording per split

@dataclass(frozen=True)
class _Wording:
    asks: dict[str, str]  # user request that spawned an agent surfaced in a history
    acks: tuple[str, ...]
    result: dict[str, str]  # agent turn by state
    status: dict[str, str]  # agent turn answering "where is it at?"
    relay: dict[str, str]  # poke's relay of a result, by state (done: per agent key)
    relay_status: dict[str, str]
    chatter: tuple[tuple[str, str], ...]
    holds: tuple[str, ...]  # short user double-texts
    defer: tuple[str, str]  # user defers a draft, poke confirms


_DEV = _Wording(
    asks={
        "alice_deck": "can you draft an email to alice w the q3 deck? want comments by fri",
        "alice_lunch": "email alice chen and see if she's free for lunch thursday",
        "acme_renewal": "draft the reply to alice with our redlines on the acme renewal",
        "romero_brand": "send alice romero our notes on the brand guidelines",
        "priya_sow": "draft the revised sow for priya with the new timeline",
        "marcus_onboarding": "email marcus at northwind to set up the onboarding call",
        "omar_invoice": "follow up w omar about invoice 1042, it's overdue",
        "jen_workshop": "draft the workshop agenda email to jen at kestrel",
        "bob_reimburse": "ask bob to etransfer me $84 for the concert tix",
        "accountant_call": "remind me about the call with ken tomorrow at 2",
        "spin_class": "remind me every tuesday at 6 about spin",
        "landlord_lease": "draft a reply to greg saying yes to the 12 month lease",
        "allison_intro": "draft an intro between allison and jordan for the design role",
        "costco_receipt": "find my costco receipt for the tv from october",
        "weather_hike": "what's the weather looking like saturday for the hike",
    },
    acks=("On it.", "Sure, on it.", "Working on it."),
    result={
        "waiting_on_user": 'Draft {draft} is ready for {full} <{email}>, subject "{subject}". Waiting for your OK before sending.',
        "waiting_on_others": 'Sent to {email} (subject "{subject}", thread {thread}). Waiting on their reply.',
        "scheduled": "Set trigger {trigger}: {detail}.",
        "done": "Done: {detail}.",
    },
    status={
        "waiting_on_user": "Draft {draft} to {email} is still unsent and waiting for your OK.",
        "waiting_on_others": "Checked thread {thread}: no reply from {email} yet.",
        "scheduled": "Trigger {trigger} is still active: {detail}.",
    },
    relay={
        "waiting_on_user": "Draft to {first} about {short} is ready. Want me to send it?",
        "waiting_on_others": "Sent to {first} about {short}. I'll tell you when they reply.",
        "scheduled": "Reminder's set: {short}.",
        "costco_receipt": 'Found it: Oct 14, 65" TV for $899.99.',
        "weather_hike": "Saturday looks sunny, around 4°C, low chance of rain.",
    },
    relay_status={
        "waiting_on_user": "Your draft to {first} ({short}) is still waiting on your OK.",
        "waiting_on_others": "No reply from {first} on {short} yet.",
        "scheduled": "Still on: {short}.",
    },
    chatter=(
        ("lol ok", "😄"), ("whats 18% of 64", "$11.52."), ("how many days till christmas", "35 days."),
        ("thx", "Anytime."), ("what's a good word for 'very tired'", "Exhausted, or drained."),
        ("how far is a 10k in miles", "About 6.2 miles."), ("long week", "Almost there, it's Friday."),
        ("convert 12 oz to grams", "About 340 g."), ("capital of australia again?", "Canberra."),
        ("spell rhythm", "R-H-Y-T-H-M."), ("is it affect or effect, 'it ___ my sleep'", '"Affects."'),
        ("ok cool", "👍"),
    ),
    holds=("one sec", "ok", "back"),
    defer=("not yet, i'll look later", "Okay, I'll hold it as a draft."),
)

_HELDOUT = _Wording(
    asks={
        "alice_deck": "write alice chen a note with deck v2, need feedback by friday",
        "alice_lunch": "ask alice if thursday lunch works",
        "acme_renewal": "prep a response to alice chen w/ the acme redlines",
        "romero_brand": "pass our brand guide feedback to alice romero",
        "priya_sow": "put together the updated sow email for priya",
        "marcus_onboarding": "reach out to marcus bell re onboarding call times",
        "omar_invoice": "chase omar on #1042 please",
        "jen_workshop": "write up the dec 4 workshop agenda for jen ito",
        "bob_reimburse": "email bob for the $84 concert money",
        "accountant_call": "set a reminder for my ken morita call tmrw 2pm",
        "spin_class": "weekly reminder tues 6am for spin pls",
        "landlord_lease": "reply to greg novak accepting the lease renewal, 12 months",
        "allison_intro": "intro allison wu to jordan blake re the design role, draft it",
        "costco_receipt": "dig up the costco tv receipt from oct",
        "weather_hike": "forecast for saturday at rattlesnake point?",
    },
    acks=("Okay, starting now.", "Got it, doing that now.", "Will do."),
    result={
        "waiting_on_user": "Prepared draft {draft} to {full} ({email}) re: {subject}. Not sent; needs your approval.",
        "waiting_on_others": "Email to {full} went out on thread {thread} re: {subject}. No answer yet.",
        "scheduled": "Trigger {trigger} scheduled: {detail}.",
        "done": "Finished. Result: {detail}.",
    },
    status={
        "waiting_on_user": "Still holding draft {draft} for {full}; nothing has been sent.",
        "waiting_on_others": "Thread {thread}: still no answer from {full}.",
        "scheduled": "Trigger {trigger} unchanged: {detail}.",
    },
    relay={
        "waiting_on_user": "{first}'s email ({short}) is drafted. OK to send?",
        "waiting_on_others": "Emailed {first} re {short}; waiting to hear back.",
        "scheduled": "Scheduled: {short}.",
        "costco_receipt": "Got the receipt: $899.99 for the TV on Oct 14.",
        "weather_hike": "Sunny and cold Saturday, about 4°C.",
    },
    relay_status={
        "waiting_on_user": "Draft for {first} ({short}) is still unsent.",
        "waiting_on_others": "Nothing back from {first} yet on {short}.",
        "scheduled": "Still scheduled: {short}.",
    },
    chatter=(
        ("haha", "🙂"), ("what's a 15% tip on 82", "About $12.30."), ("how many weeks until feb 1", "Just over 10 weeks."),
        ("cheers", "Any time!"), ("synonym for happy?", "Cheerful, glad, content."),
        ("what's 3 tbsp in ml", "About 45 ml."), ("brutal day", "Sorry, that sounds rough."),
        ("how many cups in a litre", "About 4.2 cups."), ("fewer or less people?", '"Fewer people."'),
        ("nice", "👌"), ("tallest building in the world?", "The Burj Khalifa."),
        ("how do you spell necessary", "N-E-C-E-S-S-A-R-Y."),
    ),
    holds=("hold on", "ok so", "sorry, was driving"),
    defer=("leave it for now", "Sure, it stays unsent until you say so."),
)
_WORDING = {"dev": _DEV, "heldout": _HELDOUT}


# --------------------------------------------------------------------------- cases per split
# Keys name core agents (evals.world.generate.CORE_SPECS).

_CASES: dict[str, dict[int, list[tuple]]] = {
    "dev": {
        1: [  # (target, message)
            ("ramen", "what was that noodle shop near shibuya you found"),
            ("car_insurance", "which auto policy came out cheapest again"),
            ("passport", "what did you say i need for the service canada appointment, is a guarantor required?"),
            ("subs_audit", "which streaming services was i double paying for"),
            ("al_sink", "has the plumber gotten back with a price for the faucet"),
            ("aircanada_refund", "any news on getting my money back for that cancelled flight"),
            ("shopify_app", "did that e-commerce company ever answer about the product role"),
            ("ramen", "what was the second place on that japan food list"),
        ],
        2: [  # (target, decoy surfaced last, message, irreversible)
            ("alice_lunch", "alice_deck", "did alice get back to you about lunch", False),
            ("alice_deck", "alice_lunch", "alice wants slide 4 cut from the deck before it goes out", False),
            ("acme_renewal", "alice_deck", "for the acme renewal w alice, cap the price bump at 3%", False),
            ("alice_travel", "acme_renewal", "is the fare alert for alice's conference trip still running?", False),
            ("alice_deck", "acme_renewal", "send alice the deck", True),
            ("alice_lunch", "romero_brand", "alice can't do thursday lunch, ask her about friday instead", True),
            ("alice_travel", "alice_lunch", "stop watching flights for the conference w alice, we booked", True),
            ("acme_renewal", "alice_lunch", "add the new payment terms to alice's renewal draft", False),
        ],
        3: [  # (target, message, irreversible)
            ("bob_reimburse", "send it", True),
            ("priya_sow", "looks good send", True),
            ("alice_deck", "ok send it", True),
            ("jen_workshop", "yep go ahead", True),
            ("acme_renewal", "send that one", True),
            ("alice_lunch", "did she reply?", False),
            ("marcus_onboarding", "did he get back to us yet", False),
            ("omar_invoice", "any reply from him?", False),
            ("accountant_call", "move it to 3pm", False),
            ("spin_class", "make it 6:30 instead", False),
        ],
        4: [  # (open item, open item of the same state, vague irreversible message)
            ("alice_deck", "acme_renewal", "yep send it"),
            ("jen_workshop", "acme_renewal", "ok send"),
            ("priya_sow", "alice_deck", "looks good, send it"),
            ("bob_reimburse", "priya_sow", "send it pls"),
            ("alice_deck", "jen_workshop", "go for it, send"),
            ("priya_sow", "jen_workshop", "sure send that"),
            ("accountant_call", "spin_class", "actually cancel it"),
            ("alice_lunch", "romero_brand", "nudge her again"),
        ],
        5: [  # ("named", a, b, gold, message, irreversible) | ("stale", old, fresh, message)
            ("named", "alice_deck", "acme_renewal", "acme_renewal", "send the renewal one", True),
            ("named", "bob_reimburse", "priya_sow", "bob_reimburse", "send the one to bob", True),
            ("named", "priya_sow", "jen_workshop", "jen_workshop", "send jen's", True),
            ("named", "alice_deck", "jen_workshop", "alice_deck", "the deck one is good, send it", True),
            ("named", "accountant_call", "spin_class", "spin_class", "cancel the spin reminder", True),
            ("stale", "landlord_lease", "alice_deck", "great, send it"),
            ("stale", "allison_intro", "bob_reimburse", "ok send"),
            ("stale", "landlord_lease", "priya_sow", "yep send"),
        ],
        6: [  # (look-alike, message, irreversible)
            ("dentist_vancouver", "find a dentist in toronto that's open saturdays", False),
            ("ramen", "any good ramen near king and spadina?", False),
            ("car_insurance", "get me a few quotes for tenant insurance", False),
            ("passport", "my nexus card expires in feb, what do i need to renew it", False),
            ("sofa_return", "i need to return a desk lamp to ikea, can you start that", False),
            ("dentist_vancouver", "remind me about my eye exam dec 9 at 10", False),
            ("romero_brand", "email alice romero asking if she'd come on our podcast in january", True),
            ("rent_reminder", "set a monthly reminder to pay the hydro bill", False),
        ],
        7: [  # (target, same-company sibling, message, irreversible)
            ("priya_sow", "marcus_onboarding", "priya wants the start date pushed to jan 12 in the sow", False),
            ("marcus_onboarding", "priya_sow", "has marcus picked an onboarding slot yet", False),
            ("priya_sow", "marcus_onboarding", "send priya the sow", True),
            ("marcus_onboarding", "priya_sow", "nudge marcus about the call times", True),
            ("omar_invoice", "jen_workshop", "omar says accounting needs the invoice resent with PO 7781 on it", True),
            ("jen_workshop", "omar_invoice", "jen wants a 30 min q&a at the end of the agenda", False),
            ("marcus_onboarding", "priya_sow", "anything back from northwind on scheduling the onboarding?", False),
            ("jen_workshop", "omar_invoice", "the kestrel workshop email to jen looks good, send it", True),
        ],
        8: [  # (owner of the thread, importance-classifier summary)
            ("alice_lunch", "Alice Chen replied: Thursday 12:30 works, she suggests Pai on Duncan St."),
            ("romero_brand", "Alice Romero sent notes on brand guidelines v3: tighten logo spacing, otherwise approved."),
            ("al_sink", "Al Diaz sent a quote for the kitchen sink and faucet: $480 parts and labour, can come Tuesday."),
            ("marcus_onboarding", "Marcus Bell proposed onboarding call times: Monday 10am or Wednesday 2pm ET."),
            ("omar_invoice", "Omar Haddad says invoice #1042 was paid today; remittance advice attached."),
            ("aircanada_refund", "Air Canada approved the refund for booking AC8812; 7-10 business days to the card."),
            ("shopify_app", "Dana Whitfield at Shopify wants to book a first-round interview for the product manager role."),
            ("alice_lunch", "Alice Chen asked to push lunch to next week; Thursday got busy."),
        ],
        9: [  # (archived target, message, irreversible)
            ("alicia_bday", "what restaurant did we book for alicia's birthday", False),
            ("photographer", "forward the photographer's package details to my mom, linda.song@gmail.com", True),
            ("offer_letter", "dig up the job offer pdf from globex again", False),
            ("bob_cabin", "what did bob and i end up paying for the cabin", False),
            ("sofa_return", "did article ever refund the sofa? how much", False),
            ("photographer", "which wedding photographer did we go with?", False),
            ("alicia_bday", "alicia wants the name of the place from her birthday dinner", False),
            ("bob_cabin", "how did the cabin weekend split shake out with bob?", False),
        ],
        10: [  # (chosen, other, phrase, first message, poke question, choice, later message, irreversible)
            ("romero_brand", "alice_deck", "the alice thing", "where are we on the alice thing?",
             "Two Alice items are open: the Q3 deck draft for Alice Chen, or the brand guidelines email to Alice Romero. Which one?",
             "romero", "any movement on the alice thing", False),
            ("marcus_onboarding", "priya_sow", "the northwind email", "what's up with the northwind email",
             "Northwind has two: Priya's SOW draft or the onboarding email to Marcus. Which?",
             "marcus", "ok chase them on the northwind email", True),
            ("spin_class", "accountant_call", "my reminder", "is my reminder still on",
             "Which reminder: the call with Ken tomorrow at 2, or Tuesday spin?",
             "spin one", "push my reminder to 5:30", False),
            ("omar_invoice", "jen_workshop", "the kestrel thing", "where's the kestrel thing at",
             "For Kestrel: Jen's workshop agenda draft, or Omar's invoice follow-up?",
             "omar's", "has the kestrel thing been paid yet", False),
        ],
    },
    "heldout": {
        1: [
            ("ramen", "which noodle spot was the top pick for japan?"),
            ("car_insurance", "what did intact want per month for coverage"),
            ("aircanada_refund", "any reply from the airline about reimbursing me"),
        ],
        2: [
            ("alice_lunch", "acme_renewal", "any word from alice re: lunch?", False),
            ("acme_renewal", "alice_deck", "make alice's renewal draft say 24 months instead of 12", False),
            ("alice_travel", "alice_deck", "alice says the summit dates moved, have the fare tracker look at dec 2-6 instead", False),
        ],
        3: [
            ("acme_renewal", "go ahead and send that", True),
            ("romero_brand", "has she answered?", False),
            ("spin_class", "push that to 6:45", False),
            ("bob_reimburse", "perfect, send", True),
        ],
        4: [
            ("acme_renewal", "bob_reimburse", "go ahead and send it"),
            ("marcus_onboarding", "omar_invoice", "bump him again"),
            ("jen_workshop", "alice_deck", "approved, send"),
        ],
        5: [
            ("named", "alice_deck", "acme_renewal", "acme_renewal", "send the redlines one", True),
            ("named", "marcus_onboarding", "omar_invoice", "marcus_onboarding", "nudge marcus", True),
            ("stale", "allison_intro", "jen_workshop", "go ahead and send it"),
        ],
        6: [
            ("alice_travel", "look for flights to lisbon for my sister in march", False),
            ("ramen", "book a sushi place downtown for 4 on friday", False),
            ("car_insurance", "renew my car registration online", False),
        ],
        7: [
            ("priya_sow", "marcus_onboarding", "update priya's sow draft: net 30, not net 45", False),
            ("omar_invoice", "jen_workshop", "did omar ever pay", False),
            ("jen_workshop", "omar_invoice", "jen's agenda: swap the two afternoon sessions", False),
        ],
        8: [
            ("marcus_onboarding", "Marcus Bell asked whether Tuesday at 11am ET works for the onboarding call instead."),
            ("romero_brand", "Alice Romero approved brand guidelines v3 with two small copy edits."),
            ("al_sink", "Al Diaz can't get the faucet until December and offers a cheaper alternative model."),
        ],
        9: [
            ("alicia_bday", "which place hosted alicia's bday dinner", False),
            ("offer_letter", "need my globex offer letter again for the mortgage people", False),
            ("sofa_return", "how much did article refund for the couch", False),
        ],
        10: [
            ("acme_renewal", "alice_deck", "alice's draft", "what's happening with alice's draft",
             "Alice has two drafts waiting: the Q3 deck or the Acme renewal redlines. Which one?",
             "the renewal", "send alice's draft", True),
            ("omar_invoice", "marcus_onboarding", "the client follow-up", "status on the client follow-up?",
             "Do you mean Omar's invoice #1042 or Marcus's onboarding call?",
             "omar", "anything on the client follow-up?", False),
            ("spin_class", "accountant_call", "the reminder thing", "is the reminder thing set",
             "The Ken call tomorrow or the Tuesday spin class?",
             "spin", "move the reminder thing to 6:15", False),
        ],
    },
}


# --------------------------------------------------------------------------- building blocks

class _Ctx:
    def __init__(self, world: World, split: str):
        self.world = world
        self.split = split
        self.words = _WORDING[split]
        self.rng = random.Random(f"openpoke-world:scenarios:{split}:{world.seed}")
        self.now = parse_iso(world.now)

    def agent(self, key: str) -> WorldAgent:
        return self.world.agents[CORE_IDS[key] - 1]

    def at(self, key: str) -> datetime:
        return parse_iso(self.agent(key).last_active_at)

    def facts(self, key: str) -> dict[str, str]:
        agent = self.agent(key)
        spec = CORE_SPEC_BY_ID[agent.id]
        refs = {kind: value for kind, value in reversed(agent.refs)}
        person = contact(spec.contact_id) if spec.contact_id else None
        return {
            "full": person.name if person else "", "first": person.name.split()[0] if person else "",
            "email": person.email if person else "", "company": person.company if person else "",
            "subject": spec.subject or "", "draft": refs.get("draft", ""), "thread": refs.get("thread", ""),
            "trigger": refs.get("trigger", ""), "detail": spec.detail, "short": _SHORT.get(key, ""),
        }

    def casual(self, text: str) -> str:
        return text[0].upper() + text[1:] if self.rng.random() < 0.2 else text


def _turn(role: str, text: str, at: datetime, agent_id: Optional[int] = None) -> Turn:
    return Turn(role=role, text=text, ts=iso(at), agent_id=agent_id)


def _agent_turn(ctx: _Ctx, key: str, *, status: bool = False) -> Turn:
    agent = ctx.agent(key)
    templates = ctx.words.status if status else ctx.words.result
    text = templates[agent.state].format_map(ctx.facts(key))
    return _turn("agent", text, ctx.at(key), agent.id)


def _relay(ctx: _Ctx, key: str, at: datetime, *, status: bool = False) -> Turn:
    state = ctx.agent(key).state
    templates = ctx.words.relay_status if status else ctx.words.relay
    template = templates[key] if state == "done" else templates[state]
    return _turn("poke", template.format_map(ctx.facts(key)), at)


def _surface(ctx: _Ctx, key: str, *, relay: bool = True) -> list[Turn]:
    """User asks, poke acks, the agent reports at its last_active_at (and poke relays)."""
    at = ctx.at(key)
    turns = [
        _turn("user", ctx.words.asks[key], at - timedelta(minutes=4, seconds=ctx.rng.randint(0, 40))),
        _turn("poke", ctx.rng.choice(ctx.words.acks), at - timedelta(minutes=3, seconds=30)),
        _agent_turn(ctx, key),
    ]
    if relay:
        turns.append(_relay(ctx, key, at + timedelta(seconds=25)))
    return turns


def _chatter(ctx: _Ctx, start: datetime, end: datetime, pairs: int, used: set[int]) -> list[Turn]:
    """``pairs`` unused user/poke exchanges spread evenly inside (start, end)."""
    gap = (end - start) / (pairs + 1)
    if pairs <= 0 or gap < timedelta(seconds=60):
        return []
    choices = ctx.rng.sample([i for i in range(len(ctx.words.chatter)) if i not in used], pairs)
    turns: list[Turn] = []
    for n, index in enumerate(choices, start=1):
        used.add(index)
        user_text, poke_text = ctx.words.chatter[index]
        at = start + gap * n
        turns += [_turn("user", user_text, at), _turn("poke", poke_text, at + timedelta(seconds=20))]
    return turns


def _start_of(turns: list[Turn]) -> datetime:
    return parse_iso(turns[0].ts)


def _end_of(turns: list[Turn]) -> datetime:
    return parse_iso(turns[-1].ts)


def _prelude(ctx: _Ctx, before: datetime, used: set[int]) -> list[Turn]:
    """Background before ``before``: maybe a done result from this morning, maybe chatter."""
    turns: list[Turn] = []
    fitting = [key for key in _BACKGROUND_DONE if ctx.at(key) + timedelta(minutes=5) < before]
    if fitting and ctx.rng.random() < 0.6:
        turns += _surface(ctx, ctx.rng.choice(fitting))
    start = _end_of(turns) if turns else before - timedelta(minutes=60)
    turns += _chatter(ctx, max(start, before - timedelta(minutes=60)), before - timedelta(minutes=1),
                      ctx.rng.randint(0, 2), used)
    return turns


def _tail(ctx: _Ctx, key: str, count: int, used: set[int], *, status: bool = False) -> list[Turn]:
    """``count`` (1-4) turns after the agent turn of ``key``: the relay, then chatter/holds."""
    at = ctx.at(key)
    turns = [_relay(ctx, key, at + timedelta(seconds=25), status=status)]
    if count >= 3:
        turns += _chatter(ctx, at + timedelta(minutes=1), ctx.now - timedelta(minutes=2), 1, used)
    if count in (2, 4):
        turns.append(_turn("user", ctx.rng.choice(ctx.words.holds), ctx.now - timedelta(seconds=50)))
    return turns


def _open_background(ctx: _Ctx, avoid: set[str], used: set[int]) -> list[Turn]:
    """History for families whose message does not lean on the conversation."""
    pool = [key for key in ctx.words.asks if key not in avoid and ctx.now - ctx.at(key) < timedelta(hours=12)]
    picked = sorted(ctx.rng.sample(pool, ctx.rng.randint(0, 2)), key=ctx.at)
    turns: list[Turn] = []
    for key in picked:
        segment = _surface(ctx, key)
        if turns and _start_of(segment) <= _end_of(turns):
            continue
        turns += segment
    if not turns:
        return _prelude(ctx, ctx.now - timedelta(minutes=3), used)
    turns = _prelude(ctx, _start_of(turns), used) + turns
    turns += _chatter(ctx, _end_of(turns), ctx.now - timedelta(minutes=1), ctx.rng.randint(0, 2), used)
    return turns


def _pair_history(ctx: _Ctx, first: str, second: str, used: set[int]) -> list[Turn]:
    """Two items surfaced back to back (earlier one first), ending 1-2 turns after the second."""
    first, second = sorted((first, second), key=ctx.at)
    a, b = _surface(ctx, first), _surface(ctx, second, relay=False)
    middle = _chatter(ctx, _end_of(a), _start_of(b), ctx.rng.randint(0, 1), used)
    return _prelude(ctx, _start_of(a), used) + a + middle + b + _tail(ctx, second, ctx.rng.randint(1, 2), used)


def _scenario(ctx: _Ctx, family: int, index: int, history: list[Turn], message: Optional[str], gold: Gold,
              irreversible: bool, note: str, event: Optional[EmailEvent] = None) -> Scenario:
    return Scenario(
        id=f"{ctx.split}-{family:02d}-{index:03d}", family=family, split=ctx.split, history=history,
        message=ctx.casual(message) if message else None, event=event, gold=gold, irreversible=irreversible,
        note=note,
    )


def _label(ctx: _Ctx, key: str) -> str:
    agent = ctx.agent(key)
    return f"#{agent.id} '{agent.name}' ({agent.state})"


def _idle_days(ctx: _Ctx, key: str) -> int:
    return (ctx.now - ctx.at(key)).days


def _gold(ctx: _Ctx, key: str) -> Gold:
    return Gold(kind="agent", agent_id=ctx.agent(key).id)


# --------------------------------------------------------------------------- families

def _needle(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, message = case
    history = _open_background(ctx, {key}, set())
    note = f"{_label(ctx, key)}, idle {_idle_days(ctx, key)}d, owns this; message paraphrases its topic without its name words"
    return _scenario(ctx, 1, index, history, message, _gold(ctx, key), False, note)


def _same_person(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, decoy, message, irreversible = case
    used: set[int] = set()
    segment = _surface(ctx, decoy, relay=False)
    history = _prelude(ctx, _start_of(segment), used) + segment + _tail(ctx, decoy, ctx.rng.randint(1, 3), used)
    note = (f"Alice Chen has four live agents; message names {_SHORT[key]} "
            f"-> {_label(ctx, key)}; {_label(ctx, decoy)} surfaced last is a decoy")
    return _scenario(ctx, 2, index, history, message, _gold(ctx, key), irreversible, note)


def _conversational(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, message, irreversible = case
    used: set[int] = set()
    count = ctx.rng.randint(1, 4)
    segment = _surface(ctx, key, relay=False)
    history = _prelude(ctx, _start_of(segment), used) + segment + _tail(ctx, key, count, used)
    note = f"only open item surfaced: {_label(ctx, key)}, {count} turn(s) back; '{message}' refers to it"
    return _scenario(ctx, 3, index, history, message, _gold(ctx, key), irreversible, note)


def _true_ambiguity(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    first, second, message = case
    assert ctx.agent(first).state == ctx.agent(second).state, case
    history = _pair_history(ctx, first, second, set())
    note = (f"{_label(ctx, first)} and {_label(ctx, second)} surfaced back to back; "
            f"'{message}' fits either and is irreversible -> ask")
    return _scenario(ctx, 4, index, history, message, Gold(kind="ASK"), True, note)


def _looks_ambiguous(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    used: set[int] = set()
    if case[0] == "named":
        _, first, second, key, message, irreversible = case
        history = _pair_history(ctx, first, second, used)
        other = second if key == first else first
        note = f"both surfaced, but the message singles out {_SHORT[key]} -> {_label(ctx, key)}, not #{ctx.agent(other).id}; no need to ask"
        return _scenario(ctx, 5, index, history, message, _gold(ctx, key), irreversible, note)

    _, old, key, message = case
    defer_user, defer_poke = ctx.words.defer
    old_at = ctx.at(old)
    old_part = _surface(ctx, old) + [
        _turn("user", defer_user, old_at + timedelta(minutes=2)),
        _turn("poke", defer_poke, old_at + timedelta(minutes=2, seconds=15)),
    ]
    segment = _surface(ctx, key, relay=False)
    history = (old_part + _prelude(ctx, _start_of(segment), used) + segment
               + _tail(ctx, key, ctx.rng.randint(1, 3), used))
    note = (f"only {_label(ctx, key)} was surfaced today; {_label(ctx, old)} was surfaced "
            f"{_idle_days(ctx, old)} days ago and deferred -> #{ctx.agent(key).id}")
    return _scenario(ctx, 5, index, history, message, _gold(ctx, key), True, note)


def _new_task(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    lookalike, message, irreversible = case
    history = _open_background(ctx, set(), set())
    note = f"no agent does this task; look-alike {_label(ctx, lookalike)} is a different task -> new agent"
    return _scenario(ctx, 6, index, history, message, Gold(kind="NEW"), irreversible, note)


def _cross_client(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, sibling, message, irreversible = case
    used: set[int] = set()
    if ctx.rng.random() < 0.5:
        history = _pair_history(ctx, key, sibling, used)
    else:
        segment = _surface(ctx, sibling, relay=False)
        history = _prelude(ctx, _start_of(segment), used) + segment + _tail(ctx, sibling, ctx.rng.randint(1, 3), used)
    facts, sibling_facts = ctx.facts(key), ctx.facts(sibling)
    note = (f"{facts['company']}: {_label(ctx, key)} is {facts['first']}'s, {_label(ctx, sibling)} is "
            f"{sibling_facts['first']}'s; message is about {facts['first']}")
    return _scenario(ctx, 7, index, history, message, _gold(ctx, key), irreversible, note)


def _inbound(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, summary = case
    facts = ctx.facts(key)
    event = EmailEvent(thread_id=facts["thread"], sender=facts["email"], subject=f"Re: {facts['subject']}",
                       summary=summary)
    history = _open_background(ctx, {key}, set())
    note = f"reply on {facts['thread']}, held by {_label(ctx, key)} -> route to it, not a new agent"
    return _scenario(ctx, 8, index, history, None, _gold(ctx, key), False, note, event=event)


def _archived(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, message, irreversible = case
    history = _open_background(ctx, set(), set())
    note = f"archived {_label(ctx, key)}, idle {_idle_days(ctx, key)}d, holds this; reactivate it rather than spawn"
    return _scenario(ctx, 9, index, history, message, _gold(ctx, key), irreversible, note)


def _repeat_reference(ctx: _Ctx, index: int, case: tuple) -> Scenario:
    key, other, phrase, first_message, question, choice, message, irreversible = case
    used: set[int] = set()
    at = ctx.at(key)
    # The other option must already exist when poke offers it.
    assert ctx.at(other) < at - timedelta(minutes=7), case
    clarify = [
        _turn("user", first_message, at - timedelta(minutes=6)),
        _turn("poke", question, at - timedelta(minutes=5, seconds=40)),
        _turn("user", choice, at - timedelta(minutes=5)),
        _turn("poke", ctx.rng.choice(ctx.words.acks), at - timedelta(minutes=4, seconds=45)),
        _agent_turn(ctx, key, status=True),
    ]
    history = _prelude(ctx, _start_of(clarify), used) + clarify + _tail(ctx, key, 3, used, status=True)
    note = (f"when '{phrase}' was ambiguous the user picked {_label(ctx, key)} over #{ctx.agent(other).id}; "
            f"same phrase again -> #{ctx.agent(key).id}")
    return _scenario(ctx, 10, index, history, message, _gold(ctx, key), irreversible, note)


_FAMILIES = {
    1: _needle, 2: _same_person, 3: _conversational, 4: _true_ambiguity, 5: _looks_ambiguous,
    6: _new_task, 7: _cross_client, 8: _inbound, 9: _archived, 10: _repeat_reference,
}


def _check_core(world: World) -> None:
    if len(world.agents) < CORE_AGENT_COUNT:
        raise ValueError(f"world has {len(world.agents)} agents; scenarios need the {CORE_AGENT_COUNT} core agents")
    for agent, spec in zip(world.agents, CORE_SPECS):
        if not agent.core or agent.name != spec.name:
            raise ValueError(f"agent #{agent.id} is not core agent {spec.name!r}; build the world with build_world")


def build_scenarios(world: World) -> list[Scenario]:
    """Scenarios for the world's split (seed 1 = dev, seed 2 = heldout), using core agents only."""
    if world.seed not in SPLIT_BY_SEED:
        raise ValueError(f"no scenario split for seed {world.seed}; use 1 (dev) or 2 (heldout)")
    _check_core(world)
    ctx = _Ctx(world, SPLIT_BY_SEED[world.seed])
    scenarios: list[Scenario] = []
    for family, builder in _FAMILIES.items():
        for index, case in enumerate(_CASES[ctx.split][family], start=1):
            scenarios.append(builder(ctx, index, case))
    return scenarios
