'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type Fact = { kind: string; subject: string; value: string; email?: string | null; since?: string; until?: string | null };
type Item = { id: number; name: string; state: string; label: string; note: string; last_active_at: string };
type Sent = { to: string; subject: string; body: string; at: string };

const POLL_MS = 2500;
const NEW_FOR_MS = 8000;
const KINDS: [string, string][] = [
  ['person', 'People'],
  ['preference', 'Preferences'],
  ['routine', 'Routines'],
  ['place', 'Places'],
  ['fact', 'Facts'],
];
const STATE_DOT: Record<string, string> = {
  waiting_on_user: 'bg-amber-500',
  waiting_on_others: 'bg-sky-500',
  scheduled: 'bg-violet-500',
  running: 'bg-emerald-500',
  failed: 'bg-red-500',
  done: 'bg-gray-300',
};
const DEMO_EMAILS = [
  {
    label: 'Alice replies about lunch',
    sender: 'Alice Chen <alice.chen@acme.com>',
    subject: 'Re: Lunch Thursday?',
    body: 'Thursday works! 12:30 at Pai on Duncan?',
  },
  {
    label: 'Bob asks about the invoice',
    sender: 'Bob Stone <bob@northwind.com>',
    subject: 'Re: Invoice #402',
    body: 'Did invoice #402 come through okay? Happy to add a PO number if accounting needs one.',
  },
];

const factKey = (f: Fact) => `${f.subject}|${f.value}|${f.email ?? ''}`;

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

/** What Poke knows about you and what it is tracking, live beside the chat. */
export function PokePanel() {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [open, setOpen] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [outbox, setOutbox] = useState<Sent[] | null>(null); // null: demo mailbox off
  const [fresh, setFresh] = useState<Record<string, number>>({});
  const [sending, setSending] = useState<string | null>(null);
  const known = useRef<Set<string> | null>(null);

  const refresh = useCallback(async () => {
    const [me, agents, box] = await Promise.all([
      getJson<{ facts: Fact[] }>('/api/me'),
      getJson<{ open: Item[]; total: number }>('/api/agents/open'),
      getJson<{ outbox: Sent[] }>('/api/demo/mailbox'),
    ]);
    if (me) {
      const keys = me.facts.map(factKey);
      if (known.current) {
        const added = keys.filter(k => !known.current!.has(k));
        if (added.length) {
          const now = Date.now();
          setFresh(prev => ({ ...prev, ...Object.fromEntries(added.map(k => [k, now])) }));
        }
      }
      known.current = new Set(keys);
      setFacts(me.facts);
    }
    if (agents) {
      setOpen(agents.open);
      setTotal(agents.total);
    }
    setOutbox(box ? box.outbox : null);
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const forget = useCallback(
    async (subject: string) => {
      await fetch(`/api/me?subject=${encodeURIComponent(subject)}`, { method: 'DELETE' });
      void refresh();
    },
    [refresh],
  );

  const deliver = useCallback(
    async (email: (typeof DEMO_EMAILS)[number]) => {
      setSending(email.label);
      try {
        await fetch('/api/demo/email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sender: email.sender, subject: email.subject, body: email.body, reply_to_last: true }),
        });
      } finally {
        window.setTimeout(() => setSending(null), 1500);
      }
    },
    [],
  );

  const isFresh = (f: Fact) => (fresh[factKey(f)] ?? 0) > Date.now() - NEW_FOR_MS;

  return (
    <div className="sticky top-6 flex max-h-[calc(100vh-3rem)] flex-col gap-4 overflow-y-auto">
      <section className="card p-4">
        <h2 className="text-sm font-semibold text-gray-900">What Poke knows about you</h2>
        {facts.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500">
            Nothing yet. Tell it about yourself: “my mom is Linda, linda@example.com”, “keep my emails
            short”.
          </p>
        ) : (
          KINDS.map(([kind, title]) => {
            const group = facts.filter(f => f.kind === kind);
            if (!group.length) return null;
            return (
              <div key={kind} className="mt-3">
                <h3 className="text-xs font-medium uppercase tracking-wide text-gray-500">{title}</h3>
                <ul className="mt-1 space-y-1">
                  {group.map(f => (
                    <li key={factKey(f)} className="group flex items-start gap-2 text-sm">
                      <span className="min-w-0 flex-1">
                        <span className="font-medium text-gray-900">{f.subject}</span>
                        <span className="text-gray-700"> · {f.value}</span>
                        {f.until && <span className="text-xs text-gray-500"> (until {f.until.slice(0, 10)})</span>}
                        {f.email && <span className="block truncate text-xs text-gray-500">{f.email}</span>}
                      </span>
                      {isFresh(f) && <span className="chip bg-emerald-100 text-emerald-800">new</span>}
                      <button
                        className="text-gray-300 hover:text-red-600"
                        title={`Forget ${f.subject}`}
                        aria-label={`Forget ${f.subject}`}
                        onClick={() => void forget(f.subject)}
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}
      </section>

      <section className="card p-4">
        <h2 className="text-sm font-semibold text-gray-900">
          Open items <span className="font-normal text-gray-500">({open.length} of {total} agents)</span>
        </h2>
        {open.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500">Nothing waiting. Drafts, replies and reminders show up here.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {open.map(item => (
              <li key={item.id} className="flex items-start gap-2 text-sm">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATE_DOT[item.state] ?? 'bg-gray-300'}`} />
                <span className="min-w-0">
                  <span className="font-medium text-gray-900">#{item.id} {item.name}</span>
                  <span className="block text-xs text-gray-500">
                    {item.label}
                    {item.note ? ` · ${item.note}` : ''}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {outbox !== null && (
        <section className="card p-4">
          <h2 className="text-sm font-semibold text-gray-900">Demo mailbox</h2>
          <p className="mt-1 text-xs text-gray-500">Local only: nothing is really sent.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DEMO_EMAILS.map(email => (
              <button
                key={email.label}
                className="rounded-md border border-gray-200 px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                disabled={sending !== null}
                onClick={() => void deliver(email)}
              >
                {sending === email.label ? 'Delivered…' : email.label}
              </button>
            ))}
          </div>
          {outbox.length > 0 && (
            <ul className="mt-3 space-y-2">
              {outbox
                .slice(-5)
                .reverse()
                .map((m, i) => (
                  <li key={`${m.at}-${i}`} className="text-xs">
                    <span className="font-medium text-gray-900">Sent to {m.to}</span>
                    <span className="block text-gray-600">{m.subject}</span>
                    <span className="block truncate text-gray-500">{m.body}</span>
                  </li>
                ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
