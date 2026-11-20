"""Seeded synthetic agent worlds for the agent-overload evaluation.

A world is a snapshot of one user's execution-agent registry at ``NOW``:
- core agents (ids 1..CORE_AGENT_COUNT) that scenarios depend on, identical for a
  seed at every size;
- filler agents (ids above that) that scale the roster to ``size`` and act as
  retrieval distractors without ever changing a scenario's gold label.

Pure stdlib and deterministic: string seeds go through ``random.Random``'s sha512
seeding, so results do not depend on PYTHONHASHSEED or the platform.

CLI: ``python -m evals.world.generate --size N --seed S`` prints counts by state.
"""

from __future__ import annotations

import argparse
import itertools
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from evals.schema import Contact, State, World, WorldAgent

NOW = "2026-11-20T15:00:00Z"
_NOW = datetime(2026, 11, 20, 15, 0, 0)
_EARLIEST = _NOW - timedelta(days=90) + timedelta(minutes=1)
_DAY = 86400.0

# Global Constraints of the plan: archive done/failed after 14 idle days without an
# active trigger; waiting_on_others lapses to done after 7 days.
ARCHIVE_IDLE = timedelta(days=14)
LAPSE_IDLE = timedelta(days=7)


def iso(moment: datetime) -> str:
    return f"{moment:%Y-%m-%dT%H:%M:%S}Z"


def parse_iso(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def name_key(name: str) -> str:
    """Strictest normalization (casefold, drop every non-alphanumeric).

    Names unique under this key stay unique under any looser punctuation-collapsing
    key the registry may use.
    """
    return re.sub(r"[\W_]+", "", name.casefold())


# --------------------------------------------------------------------------- contacts

@dataclass(frozen=True)
class _ContactRow:
    id: str
    name: str
    email: str
    company: str
    person: bool = True  # False for org mailboxes (refunds@, support@)


_CONTACT_ROWS: tuple[_ContactRow, ...] = (
    # Collision family: two Alices, plus near-name distractors.
    _ContactRow("c01", "Alice Chen", "alice.chen@acme.com", "Acme"),
    _ContactRow("c02", "Alice Romero", "alice@brightline.io", "Brightline"),
    _ContactRow("c03", "Alicia Park", "alicia.park@gmail.com", ""),
    _ContactRow("c04", "Al Diaz", "al@diazplumbing.ca", "Diaz Plumbing"),
    _ContactRow("c05", "Allison Wu", "allison.wu@hey.com", ""),
    # Two clients at one company, twice.
    _ContactRow("c06", "Priya Shah", "priya.shah@northwind.com", "Northwind"),
    _ContactRow("c07", "Marcus Bell", "marcus.bell@northwind.com", "Northwind"),
    _ContactRow("c08", "Omar Haddad", "omar.haddad@kestrel.io", "Kestrel Labs"),
    _ContactRow("c09", "Jen Ito", "jen.ito@kestrel.io", "Kestrel Labs"),
    _ContactRow("c10", "Greg Novak", "greg@novakproperties.ca", "Novak Properties"),
    _ContactRow("c11", "Bob Lee", "bob.lee@gmail.com", ""),
    _ContactRow("c12", "Air Canada Refunds", "refunds@aircanada.ca", "Air Canada", person=False),
    _ContactRow("c13", "Dana Whitfield", "dana.whitfield@shopify.com", "Shopify"),
    _ContactRow("c14", "Maple Dental", "frontdesk@mapledental.ca", "Maple Dental", person=False),
    _ContactRow("c15", "Jordan Blake", "jordan.blake@gmail.com", ""),
    _ContactRow("c16", "Ken Morita", "ken@moritacpa.ca", "Morita CPA"),
    _ContactRow("c17", "Mara Lens Studio", "hello@maralens.com", "Mara Lens Studio", person=False),
    _ContactRow("c18", "Article Support", "support@article.com", "Article", person=False),
    _ContactRow("c19", "Globex HR", "hr@globex.com", "Globex", person=False),
    # Filler-only contacts.
    _ContactRow("c20", "Sam Ortiz", "sam.ortiz@gmail.com", ""),
    _ContactRow("c21", "Nina Kowalski", "nina.k@outlook.com", ""),
    _ContactRow("c22", "Tom Becker", "tom.becker@lumen.co", "Lumen"),
    _ContactRow("c23", "Rachel Adeyemi", "rachel.adeyemi@gmail.com", ""),
    _ContactRow("c24", "Leo Fischer", "leo@fischerdesign.de", "Fischer Design"),
    _ContactRow("c25", "Mia Santos", "mia.santos@yahoo.com", ""),
    _ContactRow("c26", "Ethan Brooks", "ethan.brooks@harborbank.com", "Harbor Bank"),
    _ContactRow("c27", "Grace Liu", "grace.liu@uoft.ca", "UofT"),
    _ContactRow("c28", "Daniel Okafor", "daniel.okafor@gmail.com", ""),
    _ContactRow("c29", "Sofia Marino", "sofia@marinolaw.ca", "Marino Law"),
    _ContactRow("c30", "Kevin Tran", "kevin.tran@northwind.com", "Northwind"),
    _ContactRow("c31", "Hannah Weiss", "hannah.weiss@gmail.com", ""),
    _ContactRow("c32", "Chris Patel", "chris@fitlifegym.ca", "FitLife Gym"),
    _ContactRow("c33", "Olivia Brown", "olivia.brown@gmail.com", ""),
    _ContactRow("c34", "Raj Mehta", "raj.mehta@deloitte.com", "Deloitte"),
    _ContactRow("c35", "Lucy Zhang", "lucy.zhang@gmail.com", ""),
    _ContactRow("c36", "Ben Carter", "ben@cartercontracting.ca", "Carter Contracting"),
    _ContactRow("c37", "Allie Moreau", "allie.moreau@gmail.com", ""),
    _ContactRow("c38", "Alan Wright", "alan.wright@gmail.com", ""),
    _ContactRow("c39", "Bobby Nguyen", "bobby.nguyen@gmail.com", ""),
    _ContactRow("c40", "Maya Chen", "maya.chen@gmail.com", ""),
)
_CONTACT = {row.id: row for row in _CONTACT_ROWS}
CONTACTS: tuple[Contact, ...] = tuple(Contact(r.id, r.name, r.email, r.company) for r in _CONTACT_ROWS)


def contact(contact_id: str) -> Contact:
    row = _CONTACT[contact_id]
    return Contact(row.id, row.name, row.email, row.company)


# --------------------------------------------------------------------------- core agents

@dataclass(frozen=True)
class CoreSpec:
    key: str
    name: str
    topic: str
    state: State
    contact_id: Optional[str]
    subject: Optional[str]  # email subject, when the agent touched an email
    detail: str  # scheduled: what the trigger does; done: the result line
    idle_minutes: float  # minutes before NOW of last activity (jittered per seed)
    span_minutes: float  # created_at = last_active_at - span
    thread: bool = False  # done/waiting_on_user agents that also hold a thread


def _today(hhmm: str) -> float:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return (15 * 60) - (hours * 60 + minutes)


def _days(days: float) -> float:
    return days * 24 * 60


# Order fixes the ids (1..K). Times: "today" agents are what the conversation surfaced
# today; older ones are needles, archived matters and look-alikes.
CORE_SPECS: tuple[CoreSpec, ...] = (
    CoreSpec("alice_deck", "Alice - Q3 Deck Draft",
             "Draft an email to Alice Chen attaching Q3 deck v2 and asking for comments by Friday",
             "waiting_on_user", "c01", "Q3 deck v2 - comments by Fri?", "", _today("12:05"), 25),
    CoreSpec("alice_lunch", "Lunch w/ Alice",
             "Email Alice Chen to ask if she's free for lunch on Thursday",
             "waiting_on_others", "c01", "lunch thursday?", "", _today("09:40"), 10),
    CoreSpec("acme_renewal", "Acme Contract Renewal",
             "Draft a reply to Alice Chen with our redlines on the Acme contract renewal",
             "waiting_on_user", "c01", "Acme renewal - redlines", "", _today("13:35"), _days(8), thread=True),
    CoreSpec("alice_travel", "Alice Chen - Lisbon Conf Travel",
             "Track fares to Lisbon for Web Summit (travelling with Alice Chen) and flag anything under $900",
             "scheduled", "c01", None,
             'daily 08:00 fare check YYZ-LIS for Web Summit with Alice Chen, alert under $900',
             _days(1.05), _days(9)),
    CoreSpec("romero_brand", "Email Alice R. - Brand Guide",
             "Send Alice Romero our feedback on the Brightline brand guidelines v3",
             "waiting_on_others", "c02", "brand guidelines v3 feedback", "", _today("13:00"), 20),
    CoreSpec("alicia_bday", "Alicia Bday Dinner",
             "Find and book a restaurant for Alicia's birthday dinner on Saturday",
             "done", "c03", "birthday dinner sat 7pm",
             "booked Bar Isabel for 6 people, Saturday 7pm; confirmation sent to alicia.park@gmail.com",
             _days(41), 120, thread=True),
    CoreSpec("al_sink", "Al - Kitchen Sink Quote",
             "Ask Al Diaz for a quote to replace the kitchen sink and faucet",
             "waiting_on_others", "c04", "quote: kitchen sink + faucet", "", _days(3.2), 15),
    CoreSpec("allison_intro", "Intro Allison <> Jordan",
             "Draft an intro email between Allison Wu and Jordan Blake about the design role",
             "waiting_on_user", "c05", "Intro: Allison <> Jordan (design role)", "", _days(2.1), 30),
    CoreSpec("priya_sow", "Northwind SOW - Priya",
             "Draft the revised SOW email to Priya Shah at Northwind with the new timeline",
             "waiting_on_user", "c06", "SOW v2 - revised timeline", "", _today("10:20"), _days(2)),
    CoreSpec("marcus_onboarding", "Marcus Bell Onboarding Call",
             "Email Marcus Bell at Northwind to set up the onboarding call",
             "waiting_on_others", "c07", "onboarding call - times?", "", _today("11:30"), 15),
    CoreSpec("omar_invoice", "Kestrel Invoice 1042",
             "Follow up with Omar Haddad at Kestrel Labs about overdue invoice #1042",
             "waiting_on_others", "c08", "invoice #1042 - overdue", "", _today("13:50"), _days(5)),
    CoreSpec("jen_workshop", "Jen Ito - Workshop Agenda",
             "Draft the workshop agenda email to Jen Ito at Kestrel Labs",
             "waiting_on_user", "c09", "Kestrel workshop agenda (Dec 4)", "", _today("12:40"), 40),
    CoreSpec("landlord_lease", "Reply to Landlord - Lease",
             "Draft a reply to Greg Novak accepting the 12-month lease renewal",
             "waiting_on_user", "c10", "Re: lease renewal - 12 month term", "", _days(4.3), 60, thread=True),
    CoreSpec("bob_reimburse", "Email to Bob - Concert $",
             "Email Bob Lee asking him to e-transfer $84 for the concert tickets",
             "waiting_on_user", "c11", "e-transfer for concert tickets ($84)", "", _today("09:05"), 5),
    CoreSpec("aircanada_refund", "Air Canada Refund",
             "Request a refund from Air Canada for cancelled flight AC8812",
             "waiting_on_others", "c12", "refund request - AC8812", "", _days(5.4), 30),
    CoreSpec("dentist_vancouver", "Dentist Reminder (Vancouver)",
             "Remind the user about the Maple Dental cleaning in Vancouver on Dec 3",
             "scheduled", "c14", None, 'remind "Maple Dental cleaning, Vancouver" on 2026-12-02 18:00',
             _days(6.0), 2),
    CoreSpec("accountant_call", "Accountant Call Reminder",
             "Remind the user about the call with Ken Morita (accountant) tomorrow at 2pm",
             "scheduled", "c16", None, 'remind "call with Ken Morita (accountant)" on 2026-11-21 14:00',
             _today("10:55"), 2),
    CoreSpec("spin_class", "Spin Class Reminder",
             "Remind the user every Tuesday at 6am about the 7am spin class",
             "scheduled", None, None, 'remind "7am spin class" every Tuesday 06:00', _today("13:20"), 3),
    CoreSpec("rent_reminder", "Rent Reminder",
             "Remind the user on the 1st of each month to pay rent",
             "scheduled", None, None, 'remind "pay rent" monthly on the 1st at 09:00', _days(19.25), _days(70)),
    CoreSpec("ramen", "Tokyo Ramen Search",
             "Find the best ramen near Shibuya for the Tokyo trip",
             "done", None, None, "shared 5 ramen spots near Shibuya; top pick Fuunji, runner-up Afuri",
             _days(6.5), 12),
    CoreSpec("car_insurance", "Car Insurance Quotes",
             "Compare car insurance quotes for the 2019 Civic",
             "done", None, None, "compared 3 quotes for the 2019 Civic; cheapest Intact $142/mo, then Belair $151/mo",
             _days(9.2), 180),
    CoreSpec("passport", "Passport Renewal Checklist",
             "Put together what's needed to renew the user's Canadian passport",
             "done", None, None,
             "listed steps: simplified form PPTC 054, 2 photos, no guarantor needed, $160 fee, book at Service Canada",
             _days(11.8), 20),
    CoreSpec("subs_audit", "Subscription Audit",
             "Go through the inbox and list every recurring subscription charge",
             "done", None, None, "found 9 recurring charges; streaming double-billed: Crave and Disney+",
             _days(4.1), 30),
    CoreSpec("shopify_app", "Shopify PM Application",
             "Follow up with Dana Whitfield at Shopify about the product manager application",
             "waiting_on_others", "c13", "following up - product manager application", "", _days(6.0), 45),
    CoreSpec("photographer", "Wedding Photographer Search",
             "Find wedding photographers for June 14 under $4k and email the top pick",
             "done", "c17", "wedding photography inquiry - June 14",
             "shortlisted 3 photographers; user chose Mara Lens Studio ($3,600 package)",
             _days(58), _days(2), thread=True),
    CoreSpec("offer_letter", "Find Globex Offer Letter",
             "Find the Globex offer letter PDF in the inbox",
             "done", "c19", "Offer of Employment - Globex", "found the Globex offer letter (PDF, March 2024) in the inbox",
             _days(72), 5, thread=True),
    CoreSpec("bob_cabin", "Bob - Cabin Weekend",
             "Work out the cabin weekend cost split with Bob Lee",
             "done", "c11", "cabin weekend split", "split agreed: $1,120 total, Bob e-transferred his $560",
             _days(33), _days(1), thread=True),
    CoreSpec("sofa_return", "Sofa Return - Article",
             "Return the Article sofa and get the refund",
             "done", "c18", "Return request #A-55120", "return picked up Oct 28; $1,240 refund issued to Visa",
             _days(24), _days(6), thread=True),
    CoreSpec("costco_receipt", "Costco Receipt Search",
             "Find the Costco receipt for the October TV purchase",
             "done", None, None, 'found Costco receipt from Oct 14: 65" TV $899.99, card ending 4417',
             _today("08:20"), 3),
    CoreSpec("weather_hike", "Weather - Saturday Hike",
             "Check the weather for Saturday's hike at Rattlesnake Point",
             "done", None, None, "Saturday at Rattlesnake Point: 4°C, sunny, 10% chance of rain",
             _today("08:45"), 2),
)
CORE_AGENT_COUNT = len(CORE_SPECS)
CORE_IDS: dict[str, int] = {spec.key: index for index, spec in enumerate(CORE_SPECS, start=1)}
CORE_SPEC_BY_ID: dict[int, CoreSpec] = {index: spec for index, spec in enumerate(CORE_SPECS, start=1)}

# Term combos no filler agent may contain (all terms of a combo, each matched at a word
# start). They cover every core topic a scenario routes to and every task family 6
# expects to be NEW, so filler can be a look-alike but never a second correct answer.
RESERVED_TERMS: tuple[tuple[str, ...], ...] = (
    ("q3",), ("alice", "deck"), ("alice", "slide"), ("alice", "lunch"), ("acme", "renewal"),
    ("acme", "contract"), ("redline",), ("lisbon",), ("web summit",), ("brand",), ("alicia", "birthday"),
    ("alicia", "dinner"), ("bar isabel",), ("sink",), ("faucet",), ("plumb",), ("intro", "jordan"),
    ("allison", "intro"), ("sow",), ("statement of work",), ("onboarding",), ("invoice",), ("1042",),
    ("workshop",), ("kestrel", "agenda"), ("lease",), ("landlord",), ("bob", "concert"),
    ("bob", "e-transfer"), ("bob", "ticket"), ("concert",), ("air canada",), ("ac8812",),
    ("flight", "refund"), ("cancelled flight",), ("dentist", "vancouver"), ("dentist", "toronto"),
    ("dentist", "saturday"), ("maple dental",), ("accountant",), ("spin",), ("rent", "reminder"),
    ("pay rent",), ("ramen",), ("shibuya",), ("noodle",), ("tokyo",), ("insurance",), ("civic",),
    ("passport",), ("nexus",), ("subscription", "audit"), ("streaming",), ("crave",), ("shopify",),
    ("product manager",), ("photograph",), ("mara lens",), ("offer letter",), ("globex",),
    ("cabin",), ("sofa",), ("couch",), ("costco",), ("hike",), ("rattlesnake",),
    # Family 6 tasks that must stay NEW.
    ("tenant",), ("ikea",), ("lamp",), ("eye exam",), ("optometr",), ("podcast",), ("sushi",),
    ("hydro",), ("car", "registration"), ("sister",),
)


# --------------------------------------------------------------------------- filler archetypes

@dataclass(frozen=True)
class _Archetype:
    key: str
    names: tuple[str, ...]
    topic: str
    states: tuple[tuple[State, int], ...]
    slots: tuple[tuple[str, tuple[str, ...]], ...] = ()
    done: tuple[str, ...] = ()  # done notes for agents that did not touch an email
    contact: str = ""  # "", "person", "work", "service:<slot>" or a contact id
    subject: str = ""
    schedule: str = ""
    weight: int = 1
    reply: bool = False  # drafts that answer an existing thread


_EMAIL_STATES = (("done", 80), ("waiting_on_user", 7), ("waiting_on_others", 10), ("failed", 3))
_PERSONAL_PAIRS = (
    "Coffee|coffee next week?", "Photos|photos from saturday", "Thank You|thank you!",
    "Book Rec|that book I mentioned", "Holiday Cards|address for holiday cards",
    "Moving Help|help moving on the 30th?", "Recipe|the chili recipe", "Carpool|carpool to the game",
    "Running Club|running club sunday", "Housewarming|housewarming dec 7", "Game Night|board game night",
    "Ski Trip|ski trip in feb?", "Playlist|playlist for the party", "Spare Keys|spare keys",
    "Babysitting|babysitting saturday?", "Catch Up|long time! catch up soon?", "Brunch|brunch sunday?",
    "Wedding Plans|wedding plans", "Dog Sitting|dog sitting over the holidays", "Trivia|trivia night team",
)
_WORK_PAIRS = (
    "Status Update|weekly status update", "Q4 Budget|Q4 budget numbers", "Offsite|offsite planning",
    "Hiring Plan|hiring plan for Jan", "Retro Notes|retro notes", "Roadmap|2027 roadmap draft",
    "Timesheet|timesheet for November", "Vendor List|vendor shortlist",
    "Security Review|security review follow-up", "Demo Recording|demo recording",
    "Holiday Party|holiday party headcount", "Expense Policy|question on the expense policy",
)
_CITIES = ("Montreal", "Chicago", "Mexico City", "Banff", "New York", "Quebec City", "Seattle", "Barcelona",
           "Reykjavik", "Halifax", "Austin", "Mont-Tremblant", "Ottawa", "Boston")
_HOODS = ("Ossington", "Leslieville", "Kensington", "the Annex", "Queen West", "Roncesvalles",
          "Liberty Village", "the Junction", "Danforth", "Yorkville")
_PRODUCTS = ("air fryer", "standing desk", "winter boots", "headphones", "stroller", "robot vacuum", "monitor",
             "rain jacket", "kettle", "office chair", "blender", "backpack")
_STORES = ("Amazon|cs-reply@amazon.ca", "Best Buy|support@bestbuy.ca", "Uniqlo|help@uniqlo.ca",
           "Winners|care@winners.ca", "MEC|help@mec.ca", "Indigo|help@indigo.ca")
_SLOTS = ("Tue 5:30pm", "Wed 7am", "Thu 6pm", "Dec 3 at 10am", "Nov 27 at 4pm", "Mon 12:15pm")
_WHEN = ("tomorrow 9am", "every Monday 8pm", "Dec 3 at 10am", "daily at 9pm", "every Sunday 7pm",
         "Friday at noon", "Nov 28 at 6pm", "weekdays at 8:30am")

_ARCHETYPES: tuple[_Archetype, ...] = (
    _Archetype("inbox_search", ("Inbox Search - {doc}", "Find {doc} Email", "{doc} search", "search inbox for {doc}"),
               "Search the inbox for {doc} and summarize what turns up", (("done", 93), ("failed", 7)),
               (("doc", ("Uber receipts", "Amazon order 112-4471", "Rogers bill", "Wealthsimple statement",
                         "Raptors tickets", "Airbnb confirmation", "T4 slip", "parking ticket", "FitLife contract",
                         "Best Buy warranty", "UofT transcript", "phone plan details", "Zoom invite from Tom",
                         "train booking", "vet records")),),
               done=('inbox search done: {n} matches for "{doc}"', "found {doc} from {month}; summarized for user"),
               weight=3),
    _Archetype("email_person", ("Email to {first}", "{first} - {msg}", "Email {first} re {msg}", "Draft: {msg}",
                                "{msg} email to {first}", "email {first}"),
               "Email {full} about {msg_2}", _EMAIL_STATES, (("msg", _PERSONAL_PAIRS),),
               contact="person", subject="{msg_2}", weight=4),
    _Archetype("email_reply", ("Reply to {first}", "Re: {msg}", "{first} reply - {msg}", "Respond to {first}"),
               "Reply to {full} on the thread about {msg_2}", _EMAIL_STATES, (("msg", _PERSONAL_PAIRS),),
               contact="person", subject="Re: {msg_2}", weight=3, reply=True),
    _Archetype("work_email", ("{company} - {msg}", "{first} @ {company} {msg}", "Email {first} ({company})",
                              "{msg} - {first}"),
               "Email {full} at {company} about {msg_2}", _EMAIL_STATES, (("msg", _WORK_PAIRS),),
               contact="work", subject="{msg_2}", weight=3),
    _Archetype("reminder", ("{thing} Reminder", "Remind me - {thing}", "Reminder: {thing}", "{thing} reminder"),
               "Remind the user to {thing_2} {when}", (("done", 70), ("scheduled", 30)),
               (("thing", ("Water Plants|water the plants", "Vitamins|take vitamins", "Call Grandma|call grandma",
                           "Trash Night|put the bins out", "Library Books|renew library books",
                           "Dry Cleaning|pick up dry cleaning", "Timesheet|submit the timesheet",
                           "Haircut|book a haircut", "Credit Card|pay the credit card",
                           "Meds Refill|refill the prescription", "Tire Pressure|check tire pressure",
                           "Mom's Birthday|call mom for her birthday", "Furnace Filter|change the furnace filter")),
                ("when", _WHEN)),
               done=("reminder fired {date}; trigger completed",), schedule='remind "{thing_2}" {when}', weight=3),
    _Archetype("travel", ("{city} Trip Planning", "Flights to {city}", "{city} Hotels", "{city} weekend ideas",
                          "Trip - {city}"),
               "Research {aspect} for a trip to {city} in {month}", (("done", 95), ("failed", 5)),
               (("city", _CITIES), ("aspect", ("hotels", "flights", "things to do", "day trips", "train options"))),
               done=("shared {n} {aspect} options for {city}", "summarized {aspect} for {city}; user saved 2"),
               weight=2),
    _Archetype("restaurant", ("{cuisine} Search - {hood}", "{cuisine} spots {hood}", "{cuisine} near {hood}",
                              "Find {cuisine} place", "Dinner Res - {cuisine}"),
               "Find a good {cuisine} place near {hood}", (("done", 97), ("failed", 3)),
               (("cuisine", ("Tacos", "Dim Sum", "Pho", "Thai", "Pizza", "Korean BBQ", "Brunch", "Ethiopian",
                             "Indian", "Burgers", "Tapas", "Falafel")), ("hood", _HOODS)),
               done=("shared {n} {cuisine} picks near {hood}", "booked a {cuisine} spot near {hood} for {n} at 7pm"),
               weight=2),
    _Archetype("bills", ("{utility} Bill", "Pay {utility}", "{utility} bill due", "{utility} Autopay"),
               "Keep track of the {utility} bill and remind the user before it's due",
               (("done", 72), ("scheduled", 23), ("failed", 5)),
               (("utility", ("Rogers", "Enbridge Gas", "Bell", "Visa", "Amex", "Fido", "Property Tax", "City Water")),
                ("amt", ("45.00", "64.20", "88.15", "112.40", "231.77")), ("due", ("5th", "12th", "15th", "21st", "28th"))),
               done=("{utility} bill ${amt} due {date}; user notified",),
               schedule="monthly reminder, {utility} bill due on the {due}"),
    _Archetype("subscriptions", ("Cancel {svc}", "{svc} Subscription", "{svc} renewal", "{svc} trial"),
               "Handle the {svc} subscription: find the charge and cancel if asked", _EMAIL_STATES,
               (("svc", ("Adobe|support@adobe.com", "NYT|help@nytimes.com", "Headspace|help@headspace.com",
                         "Dropbox|support@dropbox.com", "Grammarly|support@grammarly.com",
                         "Strava|support@strava.com", "Patreon|support@patreon.com",
                         "Duolingo|support@duolingo.com", "Peloton|support@onepeloton.com")),),
               contact="service:svc", subject="cancel my {svc} plan"),
    _Archetype("job_application", ("{co} Application", "Job App - {co} {role}", "{co} recruiter follow up",
                                   "Apply {co}"),
               "Follow up on the {role} application at {co}", _EMAIL_STATES,
               (("co", ("Wealthsimple|careers@wealthsimple.com", "Lightspeed|talent@lightspeedhq.com",
                        "Ada|recruiting@ada.cx", "Cohere|careers@cohere.com", "Faire|jobs@faire.com",
                        "Wattpad|careers@wattpad.com", "Clio|talent@clio.com", "Benevity|careers@benevity.com")),
                ("role", ("Product Designer", "Data Analyst", "Ops Lead", "Program Manager", "UX Researcher"))),
               contact="service:co", subject="{role} application - checking in", weight=2),
    _Archetype("apartment", ("Apartment Search {hood}", "{hood} Rentals", "2BR {hood}", "Viewing - {hood}"),
               "Find 2-bedroom apartments in {hood} under ${amt}", (("done", 88), ("scheduled", 12)),
               (("hood", _HOODS), ("amt", ("2,400", "2,800", "3,100", "3,500"))),
               done=("shortlisted {n} listings in {hood} under ${amt}",),
               schedule="viewing in {hood} {when}"),
    _Archetype("gym", ("{gym} Membership", "Book {cls} Class", "Cancel {gym}", "{cls} class times"),
               "Sort out {cls} classes at {gym}", (("done", 80), ("scheduled", 20)),
               (("gym", ("FitLife", "GoodLife", "YMCA")), ("cls", ("yoga", "pilates", "boxing", "HIIT"))),
               done=("booked {cls} at {gym} for {slot}", "listed {gym} {cls} times for the week"),
               schedule='remind "{cls} at {gym}" {when}'),
    _Archetype("shopping", ("{product} Price Check", "Buy {product}", "{product} Comparison", "{product} deals"),
               "Compare prices for a {product}", (("done", 96), ("failed", 4)),
               (("product", _PRODUCTS), ("store", _STORES), ("amt", ("59", "89", "129", "249", "399"))),
               done=("compared {n} {product} options; best price ${amt} at {store}",), weight=2),
    _Archetype("returns", ("Return {product}", "{store} Return", "Refund - {store} {product}"),
               "Return the {product} to {store} and confirm the refund", _EMAIL_STATES,
               (("product", _PRODUCTS), ("store", _STORES)),
               contact="service:store", subject="return for order {order}"),
    _Archetype("school", ("{course} Assignment", "Email Prof Liu", "{course} Registration", "UofT {course}"),
               "Help with {course}: deadlines and emailing the instructor", _EMAIL_STATES,
               (("course", ("STA302", "CSC343", "ECO101", "French night class", "PSY100")),),
               contact="c27", subject="{course} extension request"),
    _Archetype("medical", ("{appt} Appointment", "Book {appt}", "{appt} Reminder", "{appt} - follow up"),
               "Book and remind the user about the {appt} appointment", (("done", 70), ("scheduled", 25), ("failed", 5)),
               (("appt", ("Physio", "Dermatologist", "Blood Work", "Flu Shot", "Dentist Cleaning", "Massage",
                          "Family Doctor")),),
               done=("{appt} booked for {slot}; added to calendar",), schedule='remind "{appt} appointment" {when}',
               weight=2),
    _Archetype("meeting", ("Schedule call w/ {first}", "{first} Meeting", "Find time with {first}", "{first} 1:1"),
               "Find a time for a call with {full} next week", _EMAIL_STATES, (("msg", _WORK_PAIRS),),
               contact="work", subject="time for a quick call?"),
    _Archetype("package", ("Track {store} Package", "Where's my {product}", "{store} delivery"),
               "Track the {store} package with the {product}", (("done", 97), ("failed", 3)),
               (("product", _PRODUCTS), ("store", _STORES)),
               done=("{store} package delivered {date}", "{store} package out for delivery, ETA {date}")),
    _Archetype("research", ("Research {topic}", "{topic} summary", "Look into {topic}", "{topic} notes"),
               "Research {topic} and summarize the tradeoffs", (("done", 96), ("failed", 4)),
               (("topic", ("Heat Pumps", "Index Funds", "RRSP vs TFSA", "E-bikes", "Sourdough", "Solar Panels",
                           "Toddler Car Seats", "Electric Cars", "Mesh Wifi", "Standing Desks")),),
               done=("summarized {topic}: {n} options with pros and cons",), weight=2),
    _Archetype("newsletter", ("Unsubscribe {sender}", "Inbox Cleanup", "Newsletter cleanup {month}"),
               "Unsubscribe from {sender} and other promo lists", (("done", 95), ("failed", 5)),
               (("sender", ("Old Navy", "Medium", "LinkedIn", "Groupon", "Indeed", "Wayfair")),),
               done=("unsubscribed from {n} lists including {sender}",)),
    _Archetype("expenses", ("Expense Report {month}", "{store} Receipt", "Receipts for {month}"),
               "Collect {month} receipts for the expense report", (("done", 92), ("failed", 8)),
               (("store", _STORES),), done=("collected {n} receipts for {month}, total ${amt}",)),
    _Archetype("car", ("Oil Change Booking", "Winter Tire Swap", "Car Wash Membership", "Book {svc} service"),
               "Book {svc} for the car", (("done", 80), ("scheduled", 20)),
               (("svc", ("oil change", "tire swap", "detailing", "brake check")),),
               done=("{svc} booked for {slot}",), schedule='remind "{svc}" {when}'),
    _Archetype("gifts", ("{first} Birthday Gift", "Gift Ideas - {first}", "{first} gift"),
               "Find gift ideas for {first} under ${amt}", (("done", 100),), (("amt", ("50", "75", "100")),),
               done=("sent {n} gift ideas for {first} under ${amt}",), contact="giftee"),
    _Archetype("tickets", ("{event} Tickets", "Tickets - {event}", "{event} seats"),
               "Find two tickets for the {event}", (("done", 95), ("failed", 5)),
               (("event", ("Raptors game", "Leafs game", "comedy show", "Hamilton", "TIFF screening", "jazz night")),
                ("amt", ("65", "120", "180", "240"))),
               done=("found {n} pairs for {event}; best ${amt} each",)),
    _Archetype("home_services", ("Find {svc}", "{svc} Booking", "{svc} quote"),
               "Find and book a {svc}", _EMAIL_STATES,
               (("svc", ("Electrician", "Cleaner", "Furnace Tune-up", "Window Washing", "Snow Removal", "Handyman")),),
               contact="c36", subject="{svc} - availability next week?"),
    _Archetype("pet", ("Miso - {need}", "Miso {need_2}", "{need} for Miso"),
               "Take care of Miso's {need_2}", (("done", 85), ("scheduled", 15)),
               (("need", ("Vet Checkup|vet checkup", "Grooming|grooming", "Dog Food|food order",
                          "Vaccines|vaccines")),),
               done=("Miso {need_2} sorted for {slot}",), schedule='remind "Miso {need_2}" {when}'),
    _Archetype("banking", ("Harbor Bank Statement", "Dispute Charge - {store}", "Transfer to Savings",
                           "Harbor e-Statement {month}"),
               "Handle the Harbor Bank {task}", _EMAIL_STATES,
               (("task", ("statement", "charge dispute", "savings transfer")), ("store", _STORES)),
               contact="c26", subject="question about my {month} statement"),
    _Archetype("commute", ("TTC Delays", "Weather Check {day}", "Commute time {day}"),
               "Check {day} commute and weather", (("done", 100),),
               (("day", ("Mon", "Tue", "Wed", "Thu", "Fri")),), done=("{day}: Line 1 delays, 3°C, light snow",)),
    _Archetype("rsvp", ("RSVP {party}", "{first} Wedding RSVP", "{party} Invite"),
               "RSVP to {first}'s {party}", _EMAIL_STATES,
               (("party", ("Holiday Party", "Baby Shower", "Engagement Party", "Retirement Dinner")),),
               contact="person", subject="RSVP: {party}"),
    _Archetype("documents", ("Find {doc}", "Notarize {doc_2}", "{doc} copy", "Scan {doc}"),
               "Find or prepare the {doc_2}", (("done", 94), ("failed", 6)),
               (("doc", ("Marriage Certificate|marriage certificate", "Health Card|health card",
                         "SIN Letter|SIN letter", "Vehicle Ownership|vehicle ownership", "Diploma|diploma",
                         "Tax Docs|tax documents", "Birth Certificate|birth certificate")),),
               done=("located the {doc_2} scan from {month}",)),
    _Archetype("learning", ("{skill} lessons", "{skill} Tutor Search", "{skill} practice reminder"),
               "Set up {skill} practice", (("done", 75), ("scheduled", 25)),
               (("skill", ("Spanish", "Guitar", "Piano", "Pottery", "Chess")),),
               done=("found {n} {skill} options near {hood}",), schedule='remind "{skill} practice" {when}'),
)


def _cumulative(weights) -> list[int]:
    total, out = 0, []
    for weight in weights:
        total += weight
        out.append(total)
    return out


_ARCHETYPE_CUM_WEIGHTS = _cumulative(a.weight for a in _ARCHETYPES)
_STATE_TABLE = {a.key: ([s for s, _ in a.states], _cumulative(w for _, w in a.states)) for a in _ARCHETYPES}

# Filler may reference core contacts (as distractors) only in `done`, and on topics no
# scenario uses; these pools are kept apart so open filler never touches them.
_CORE_CONTACT_IDS = frozenset(spec.contact_id for spec in CORE_SPECS if spec.contact_id)
_CORE_COMPANIES = frozenset(_CONTACT[cid].company for cid in _CORE_CONTACT_IDS if _CONTACT[cid].company)
_DONE_ONLY_COMPANIES = frozenset({"Acme", "Brightline", "Northwind", "Kestrel Labs", "Morita CPA"})
_OFFICE_COMPANIES = frozenset({"Lumen", "Fischer Design", "Marino Law", "Deloitte"})  # generic work email


def _pool(*, work: bool, sensitive: bool) -> tuple[_ContactRow, ...]:
    rows = []
    for row in _CONTACT_ROWS:
        if not row.person or bool(row.company) != work:
            continue
        is_sensitive = row.id in _CORE_CONTACT_IDS or row.company in _CORE_COMPANIES
        if is_sensitive != sensitive:
            continue
        if sensitive and work and row.company not in _DONE_ONLY_COMPANIES:
            continue  # e.g. Shopify, Diaz Plumbing: their company names are core topics
        if not sensitive and work and row.company not in _OFFICE_COMPANIES:
            continue  # gym, school, bank, contractor contacts belong to their own archetypes
        rows.append(row)
    return tuple(rows)


_FREE_PEOPLE = _pool(work=False, sensitive=False)
_FREE_WORK = _pool(work=True, sensitive=False)
_CORE_PEOPLE = tuple(r for r in _pool(work=False, sensitive=True) if r.id != "c03")  # Alicia: birthday is core
_CORE_WORK = _pool(work=True, sensitive=True)
_DISTRACTOR_RATE = 0.2  # share of done email filler that reuses a core contact

_FAILED_NOTES = (
    "gmail_search failed: request timed out after 60s",
    "web_search failed: rate limited (429)",
    "gmail_create_draft failed: invalid recipient address",
    "gmail_reply_to_thread failed: thread not found",
    "run failed: no tool call after 4 iterations",
)
_DONE_EMAIL_NOTES = (
    'sent to {email} re "{subject}"; no reply needed',
    '{full} replied re "{subject}"; nothing pending',
    'sent to {email} re "{subject}"; no reply after 7 days',
)


# --------------------------------------------------------------------------- building

def _draw_id(prefix: str, rng: random.Random, used: set[str]) -> str:
    for _ in range(32):
        value = f"{prefix}_{rng.getrandbits(16):04x}"
        if value not in used:
            used.add(value)
            return value
    while True:  # 16-bit space nearly full (very large worlds): widen
        value = f"{prefix}_{rng.getrandbits(24):06x}"
        if value not in used:
            used.add(value)
            return value


def _claim_name(base: str, created: datetime, used: set[str]) -> str:
    """The IA must pick a fresh name for a new agent; mimic how it disambiguates."""
    candidates = itertools.chain(
        (base, f"{base} ({created:%b})", f"{base} - {created:%b} {created.day}"),
        (f"{base} {n}" for n in itertools.count(2)),
    )
    for candidate in candidates:
        key = name_key(candidate)
        if key not in used:
            used.add(key)
            return candidate
    raise AssertionError("unreachable")


def _archive_flag(state: State, last_active: datetime, has_trigger: bool) -> bool:
    return state in ("done", "failed") and _NOW - last_active >= ARCHIVE_IDLE and not has_trigger


def _build_core(seed: int, used_names: set[str], used_ids: set[str]) -> list[WorldAgent]:
    rng = random.Random(f"openpoke-world:core:{seed}")
    agents: list[WorldAgent] = []
    for agent_id, spec in enumerate(CORE_SPECS, start=1):
        if spec.idle_minutes < 12 * 60:  # surfaced today: small absolute jitter keeps the day's order
            idle = spec.idle_minutes + rng.uniform(-4, 4)
        else:
            idle = spec.idle_minutes * rng.uniform(0.98, 1.02)
        last_active = _NOW - timedelta(seconds=int(idle * 60))
        created = max(last_active - timedelta(seconds=int(spec.span_minutes * 60)), _EARLIEST)

        refs: list[tuple[str, str]] = []
        person = _CONTACT[spec.contact_id] if spec.contact_id else None
        if person:
            refs += [("contact", person.email), ("contact_name", person.name)]
        if spec.subject:
            refs.append(("subject", spec.subject))
        thread = draft = trigger = None
        if spec.state == "waiting_on_others" or spec.thread:
            thread = _draw_id("thr", rng, used_ids)
            refs.append(("thread", thread))
        if spec.state == "waiting_on_user":
            draft = _draw_id("dr", rng, used_ids)
            refs.append(("draft", draft))
        if spec.state == "scheduled":
            trigger = _draw_id("trg", rng, used_ids)
            refs.append(("trigger", trigger))

        if spec.state == "waiting_on_user":
            note = f'draft {draft} to {person.email} re "{spec.subject}" awaiting your OK'
        elif spec.state == "waiting_on_others":
            note = f'sent to {person.email} re "{spec.subject}"; waiting for reply'
        elif spec.state == "scheduled":
            note = f"trigger {trigger}: {spec.detail}"
        else:
            note = spec.detail

        used_names.add(name_key(spec.name))
        agents.append(WorldAgent(
            id=agent_id, name=spec.name, topic=spec.topic, created_at=iso(created),
            last_active_at=iso(last_active), state=spec.state, state_note=note, refs=refs,
            archived=_archive_flag(spec.state, last_active, spec.state == "scheduled"),
            has_active_trigger=spec.state == "scheduled", core=True,
        ))
    return agents


def _filler_times(rng: random.Random, state: State) -> tuple[datetime, datetime]:
    if state == "waiting_on_others":
        idle = rng.uniform(300, LAPSE_IDLE.total_seconds() - 3600)
    elif state == "waiting_on_user":
        idle = 300 + min(rng.expovariate(1 / (3 * _DAY)), 30 * _DAY)
    elif state == "scheduled":
        idle = rng.uniform(600, 30 * _DAY)
    else:
        idle = rng.uniform(300, 90 * _DAY - 7200)
    last_active = _NOW - timedelta(seconds=int(idle))
    span = rng.expovariate(1 / 1200) if rng.random() < 0.75 else rng.uniform(3600, 10 * _DAY)
    created = max(last_active - timedelta(seconds=int(span)), _EARLIEST)
    return created, last_active


def _style(name: str, rng: random.Random) -> str:
    """IA naming is not consistent: separators and casing drift."""
    roll = rng.random()
    if roll < 0.07:
        return name.lower()
    if roll < 0.15 and " - " in name:
        return name.replace(" - ", " – ")
    if roll < 0.20 and " - " in name:
        return name.replace(" - ", ": ")
    return name


def _pick_contact(archetype: _Archetype, state: State, rng: random.Random, values: dict[str, str]) -> Optional[tuple[str, str, str]]:
    """Return (full name, email, company) or None."""
    kind = archetype.contact
    if not kind:
        return None
    if kind.startswith("service:"):
        slot = kind.split(":", 1)[1]
        return values[slot], values[f"{slot}_2"], values[slot]
    if kind.startswith("c"):
        row = _CONTACT[kind]
        return row.name, row.email, row.company
    if kind == "giftee":
        pool = _CORE_PEOPLE + _FREE_PEOPLE if state == "done" else _FREE_PEOPLE
    elif state == "done" and rng.random() < _DISTRACTOR_RATE:
        pool = _CORE_WORK if kind == "work" else _CORE_PEOPLE
    else:
        pool = _FREE_WORK if kind == "work" else _FREE_PEOPLE
    row = rng.choice(pool)
    return row.name, row.email, row.company


def _filler_agent(agent_id: int, rng: random.Random, used_names: set[str], used_ids: set[str]) -> WorldAgent:
    archetype = _ARCHETYPES[rng.choices(range(len(_ARCHETYPES)), cum_weights=_ARCHETYPE_CUM_WEIGHTS)[0]]
    states, cum_weights = _STATE_TABLE[archetype.key]
    state: State = rng.choices(states, cum_weights=cum_weights)[0]
    created, last_active = _filler_times(rng, state)

    values: dict[str, str] = {
        "n": str(rng.randint(2, 9)), "amt": str(rng.choice((45, 89, 120, 240, 1800, 2400, 3100))),
        "month": rng.choice(("Aug", "Sep", "Oct", "Nov")),
        "date": f"{last_active:%b} {last_active.day}", "when": rng.choice(_WHEN), "hood": rng.choice(_HOODS),
        "order": str(rng.randint(100000, 999999)), "slot": rng.choice(_SLOTS),
    }
    for slot, options in archetype.slots:
        first, _, second = rng.choice(options).partition("|")
        values[slot], values[f"{slot}_2"] = first, second or first
    person = _pick_contact(archetype, state, rng, values)
    if person:
        values["full"], values["email"], company = person
        values["first"] = values["full"].split()[0]
        values["company"] = company or values["first"]

    base = rng.choice(archetype.names).format_map(values)
    name = _claim_name(_style(base, rng), created, used_names)
    topic = archetype.topic.format_map(values)

    refs: list[tuple[str, str]] = []
    subject = archetype.subject.format_map(values) if person else ""
    if person:
        refs += [("contact", values["email"]), ("contact_name", values["full"])]
        if subject:
            refs.append(("subject", subject))
    has_trigger = state == "scheduled"

    if state == "waiting_on_user":
        if archetype.reply:
            refs.append(("thread", _draw_id("thr", rng, used_ids)))
        draft = _draw_id("dr", rng, used_ids)
        refs.append(("draft", draft))
        note = f'draft {draft} to {values["email"]} re "{subject}" awaiting your OK'
    elif state == "waiting_on_others":
        thread = _draw_id("thr", rng, used_ids)
        refs.append(("thread", thread))
        note = f'sent to {values["email"]} re "{subject}"; waiting for reply'
    elif state == "scheduled":
        trigger = _draw_id("trg", rng, used_ids)
        refs.append(("trigger", trigger))
        note = f"trigger {trigger}: " + archetype.schedule.format_map(values)
    elif state == "failed":
        note = rng.choice(_FAILED_NOTES)
    elif person and archetype.subject:
        refs.append(("thread", _draw_id("thr", rng, used_ids)))
        note = rng.choice(_DONE_EMAIL_NOTES).format_map({**values, "subject": subject})
    else:
        note = rng.choice(archetype.done).format_map(values)

    return WorldAgent(
        id=agent_id, name=name, topic=topic, created_at=iso(created), last_active_at=iso(last_active),
        state=state, state_note=note, refs=refs, archived=_archive_flag(state, last_active, has_trigger),
        has_active_trigger=has_trigger, core=False,
    )


def build_world(size: int, seed: int) -> World:
    """Build a world of exactly ``size`` agents; raises ValueError if ``size`` < core count.

    Core agents (ids 1..CORE_AGENT_COUNT) depend only on ``seed``; filler is generated in
    id order from its own stream, so a bigger world is a strict extension of a smaller one.
    """
    if size < CORE_AGENT_COUNT:
        raise ValueError(f"size must be at least {CORE_AGENT_COUNT} (the core agents); got {size}")
    used_names: set[str] = set()
    used_ids: set[str] = set()
    agents = _build_core(seed, used_names, used_ids)
    rng = random.Random(f"openpoke-world:filler:{seed}")
    for agent_id in range(CORE_AGENT_COUNT + 1, size + 1):
        agents.append(_filler_agent(agent_id, rng, used_names, used_ids))
    return World(size=size, seed=seed, now=NOW, contacts=list(CONTACTS), agents=agents)


# --------------------------------------------------------------------------- CLI

def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Print counts for a synthetic agent world.")
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)

    world = build_world(args.size, args.seed)
    states = Counter(agent.state for agent in world.agents)
    archived = Counter(agent.state for agent in world.agents if agent.archived)
    print(f"world size={world.size} seed={world.seed} core={CORE_AGENT_COUNT} now={world.now}")
    print(f"{'state':<20}{'agents':>8}{'archived':>10}")
    for state in ("running", "waiting_on_user", "waiting_on_others", "scheduled", "done", "failed"):
        print(f"{state:<20}{states[state]:>8}{archived[state]:>10}")
    print(f"{'total':<20}{world.size:>8}{sum(archived.values()):>10}")


if __name__ == "__main__":
    main()
