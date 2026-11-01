# The Cafe Companion — Brainstorm

**Constraint (as written at the start):** 3–4 hour POC. Must look clean and run E2E live.
No scale, no auth, no DB.

> *Hindsight: the "no DB" call was reversed once this was deployed — see the end of this
> file. Everything else held. For current state read `CLAUDE.md`.*

---

## The idea

Don't build four features. **Build one loop.**

All four themes in the brief are the same data moving in a circle. Model one shared *room
state* (open orders, queue depth, recent feedback) and every theme falls out of it:

```
Customer chats → order placed → ETA from queue depth → appears on Staff Board
      ↑                                                        ↓
   sentiment tile ← 1-tap feedback ← customer notified ← staff marks Ready
```

One state object, two screens, all four themes, nothing bolted on.

**Customer screen:** chat ordering, "the usual", recommendations, live ETA, feedback.
**Staff board:** live queue, prep priority, mark-ready, room sentiment.

The staff board is the cheap half — it just reads what the customer screen wrote — and it's
what makes the demo feel real instead of like a chat transcript. It also answers *"both
customers and cafe teams"*, which a chatbot-only build misses.

---

## The demo (build backwards from this — 90 seconds)

1. Agent greets "Somdeb" by name → *preferences*
2. *"something warm, not too sweet, I'm in a rush"* → recommends 2 items, flags which is
   **faster right now** → **ordering + discovery + waits in one answer** ⭐
3. *"the usual plus that"* → cart fills → **"ready in ~6 min, 3rd in queue"**
4. Flip to staff board — **order is already there** ⭐
5. Staff clicks Ready → customer screen updates → 👍 + *"oat milk was perfect"*
6. Sentiment tile updates: *"3 mentions of oat milk today"* ⭐

Protect the three ⭐ moments above everything else.

---

## Stack

> **Built as:** Python 3.12 + FastAPI + vanilla JS — Node turned out not to be installed on
> this machine, and Python was. No build step, one command to run. Everything else below
> held. See `README.md` and `PROGRESS.md` for the as-built notes.

One app. Gemini via `google-genai` with **function-calling** — tools: `search_menu`,
`get_usual`, `add_to_cart`, `place_order`, `get_wait_estimate`, `get_best_time_to_visit`.

Three deliberate shortcuts (one since reversed — see below):
- ~~**In-memory state**, no DB.~~ → **Firestore**, once deployment made the cut hurt.
- **Polling every 2s**, not websockets. Feels live, can't break.
- **ETA is a formula:** `prep_time + queue_depth × service_time × rush_multiplier`.
  Reproducible on stage, and the agent can *narrate* it ("it's the 9am rush, +2 min") —
  which reads smarter than a black-box number.

**Google skills (mandatory):** `claude plugin marketplace add google/skills`.
`cloud/gemini-api` is the load-bearing one (SDK + function-calling). Also
`developers/finding-google-skills`, `cloud/gemini-interactions-api`. Log them in a short
`SKILLS.md` so judges can verify the constraint without digging.

---

## Cut, on purpose

BigQuery `AI.FORECAST` · AlloyDB/vector DB · ~~Agent Engine deploy~~ · real auth (use a
persona dropdown — better for demoing personalisation anyway) · voice · multi-agent
orchestration · POS/payments · mobile polish.

~~**Database**~~ — reversed. See below.

Each is either invisible to a judge or costs more than the budget. Cite `bigquery-ai-ml` in
the README as the production path for forecasting — a stated decision beats a half-trained
model that breaks on stage.

---

## Plan (3.5h)

| | Block | Gate |
|---|---|---|
| 15m | Scaffold, skills, Gemini key | `/api/health` → 200 |
| 20m | Seed data: 18 menu items w/ tags + prep times, 3 personas, hourly traffic, 8 reviews (**one negative**) | Loads |
| 40m | `state.ts`, `eta.ts`, `/api/orders` | curl creates an order |
| 50m | Agent + tools | **"a flat white please" → cart has a flat white** ⬅ hard gate |
| 35m | Customer chat UI | Order flow works in browser |
| 30m | Staff board | Chat order appears on board |
| 20m | Feedback → sentiment tile | Loop closes |
| 20m | Polish: spacing, one accent colour, empty + loading states | Looks intentional |
| 10m | README, `SKILLS.md`, rehearse once | Runs clean twice |

**At the 2h mark**, if function-calling isn't driving the cart, switch to strict JSON output
and parse it yourself. Same demo, saves the back half. Decide then, not at 3h.
*(Not needed — function-calling worked first try on `gemini-3.5-flash`.)*

Polish block is non-negotiable — 20 minutes of spacing buys more than any extra feature.

**If early:** voice via Web Speech API → existing text pipeline (~40m, skips Live API
websockets entirely). Best ratio of anything on the list.

---

## Settled

1. ~~Next.js~~ → **Python + FastAPI** (no Node on the machine).
2. **AI Studio key**, localhost.
3. Not deployed — localhost demo.

## What the plan got wrong

Both misses were in the **time model**, not the UI — which is where the plan assumed the
interesting work would be.

1. **Every item carried the full queue drain.** So the fastest and slowest warm drinks came
   out 9 vs 10 minutes, and "I'm in a rush" had no answer. Adding a `premade` flag —
   already-brewed coffee and pastries need no barista, so they skip the queue — moved the
   spread to **1 vs 10 minutes**. That is the line that lands in the demo.
2. **The ETA was frozen at order time while queue position updated live.** After a few
   minutes the panel read "11 min" next to "1st in the queue". Now recomputed on every
   poll, so it counts down with the queue: 11m/3rd → 9m/2nd → 3m/1st.

## The cut that had to be reversed: the database

"No DB, a module singleton survives the demo" was right for localhost and wrong the moment
it was deployed. On Cloud Run it cost two real things:

- **Scale-to-zero wiped the room.** Idle ~15 minutes → the container stops → queue, orders
  and feedback all reset mid-demo.
- **`--max-instances=1` was forced**, because a second instance would hold a different
  queue and the staff board would contradict the customer screen.

Now on **Firestore** (`STORE=firestore`), chosen over Cloud SQL because it is serverless
and costs nothing idle — Cloud SQL bills an always-on instance at roughly \$9/month and
would have drained the \$5 credit in days.

Only **orders and feedback** persist. Carts and chat history stay in memory on purpose:
they are per-session scratch, they churn constantly, and writing them would burn quota for
no benefit. That is also why `--max-instances=1` stays — a cart would not follow a customer
across instances mid-conversation.

The lesson is the same one as the ETA model: the shortcuts that hurt were in **state and
time**, not the UI.

## One live risk: free-tier quota

AI Studio allows ~20 `generate_content` calls/day **per model**, and a build session
exhausts that fast. The app now fails over down a chain of five models (~100 calls) and
falls back to keyword ordering only if all are spent. `GET /api/health` shows the active
and exhausted models. **Check it before presenting** — a paid key removes the risk.
