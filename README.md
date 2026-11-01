# The Cafe Companion

A conversational cafe assistant for **Loom & Bean**, built for the Hack2Skill "Cafe
Companion" brief. One Gemini agent, two surfaces, one shared room state.

> **The idea:** don't build four features — close one loop. Ordering, wait prediction,
> discovery and room sentiment are the same data moving in a circle, so they share a single
> state object and each one makes the others better.

```
Customer chats → order placed → ETA from live queue → appears on Staff Board
      ↑                                                        ↓
   sentiment ← 1-tap feedback ← customer notified ← staff marks Ready
```

---

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env         # add a key from https://aistudio.google.com/apikey
.venv/bin/uvicorn app.main:app --reload
```

- Customer → http://127.0.0.1:8000
- Staff board → http://127.0.0.1:8000/staff

Open both side by side. Without a key the app still runs on a keyword fallback, so the UI
is never dead.

---

## The 90-second demo

| Step | Do this | What it shows |
|------|---------|---------------|
| 1 | Open as **Somdeb** — greeted by name, 47 visits | Preference memory |
| 2 | *"Something warm, not too sweet — I'm in a rush"* | Ordering + discovery + waits in one answer |
| 3 | Agent offers two options and flags the faster one — Batch Filter **1 min** (already brewed, skips the queue) vs Long Black **10 min** behind the espresso machine | The wait model feeding the recommendation |
| 4 | *"The usual please"* → Oat Flat White + Butter Croissant | Personalisation |
| 5 | Confirm → ETA + queue position, with the reason ("peak rush") | Explainable waits |
| 6 | Flip to **/staff** — the order is already on the board | One shared state |
| 7 | Click **Mark ready** → customer screen updates | Loop closing |
| 8 | Customer taps 👍 and types *"oat milk was perfect"* | Live feedback |
| 9 | Staff board: "oat milk" ticks to **3**, Gemini digest re-reads the room | Sentiment payoff |

Steps 3, 6 and 9 are the moments worth protecting.

---

## How it works

```
app/
├── main.py      FastAPI: 2 pages + JSON API
├── agent.py     Gemini + 8 function-calling tools
├── state.py     the shared room state (in-memory)
├── eta.py       the wait model
├── data/        menu, personas, traffic history, seeded feedback
└── static/      two pages, vanilla JS, no build step
```

**The agent's tools:** `search_menu`, `get_usual`, `add_to_cart`, `remove_from_cart`,
`view_cart`, `get_wait_estimate`, `place_order`, `get_best_time_to_visit`. Every tool call
is returned to the UI and rendered as a chip under the reply, so you can see the agent's
reasoning rather than just its prose.

### The wait model

A transparent formula, not a trained model — it is reproducible on stage and the agent can
*narrate* it, which reads as more intelligent than an opaque number:

```
eta = (queue_work / stations) × rush_multiplier + own_prep
```

- `queue_work` — barista-minutes already in the queue. Drinks share one espresso machine,
  so an order is not fully parallel: longest item in full, then 40% of the rest.
- `rush_multiplier` — this hour's historical load vs the weekly median (0.8–1.6), which is
  also what drives "best time to visit".
- **Grab-and-go items skip the queue entirely.** A pre-brewed batch filter or a croissant
  from the case needs no barista, so it is handed over in ~1 minute no matter how long the
  queue is. This is what lets the agent say *"the filter is 9 minutes faster"* — without it
  every item wears the same queue drain and "I'm in a rush" means nothing.

### Model chain and quota

The AI Studio free tier allows ~20 `generate_content` calls per day **per model**. The agent
walks a chain and marks a model spent on a 429, so a long demo doesn't fall off a cliff:

```
gemini-3.5-flash → 3.5-flash-lite → 3.1-flash-lite → 2.5-flash → 2.5-flash-lite
```

Override with `GEMINI_MODEL` / `GEMINI_MODEL_CHAIN`. `GET /api/health` reports the active
model and which ones are exhausted. If every model is spent, the app degrades to a keyword
ordering path rather than showing an error.

Two model ids that do **not** work on a fresh key, both handled by the chain:
`gemini-2.5-flash` → 404 (retired for new users), `gemini-3.8-flash` → 504.

---

## Deploy to Cloud Run

```bash
gcloud auth login          # interactive — run this yourself
./deploy.sh                # first run: creates project, links billing, enables APIs
./deploy.sh --redeploy     # subsequent runs
```

`Dockerfile` is a plain `python:3.12-slim` image; Cloud Build builds it remotely, so no
local Docker is needed.

**`--max-instances=1` is a correctness requirement, not a cost tweak.** The room state is
an in-memory singleton, so a second instance would hold a different queue and the staff
board would disagree with the customer screen. Same reason `uvicorn` runs `--workers 1`.

**Scale-to-zero resets the room.** With `--min-instances=0` the container stops after
~15 minutes idle and the queue, cart and feedback return to their seeded state. That is
fine — even useful — for a demo, but hit the URL once to warm it before presenting, or set
`--min-instances=1` for the duration (that bills continuously, so set it back after).

Cost: a demo sits inside Cloud Run's free tier (2M requests, 360k GB-s/month) and Cloud
Build's (120 build-min/day), so the \$5 credit is barely touched.

The Gemini key is passed with `--set-env-vars` and never baked into the image. For anything
beyond a demo, move it to Secret Manager and reference it with `--set-secrets`.

---

## Persistence

`app/store.py` — one interface, two backends:

| Backend | Selected by | Durable |
|---------|-------------|---------|
| `MemoryStore` | default | no |
| `FirestoreStore` | `STORE=firestore` + `GOOGLE_CLOUD_PROJECT` | yes |

Local development needs no GCP at all: unset `STORE` and it runs exactly as before.

Only **orders and feedback** are written through. Carts and chat history stay in memory on
purpose — per-session scratch that churns on every keystroke, where persisting would burn
quota for no benefit. If Firestore fails to initialise the app logs a warning and degrades
to memory rather than returning 500s.

This is also why `--max-instances=1` stays even with a database: Firestore fixes the
cold-start reset, not cart continuity across instances.

`POST /api/reset` wipes and re-seeds — run it right before a demo.
`GET /api/health` reports `store` and `durable` so you can see which backend is live.

---

## What we deliberately didn't build

This is a 3–4 hour proof of concept. These were cut on purpose, not overlooked:

| Cut | Why | Where it would go |
|-----|-----|-------------------|
| **BigQuery `AI.FORECAST`** | ~1h of table loading and model training to replace a formula a judge can't distinguish from it — and it can't be reproduced reliably on stage | `app/eta.py::rush_multiplier` — swap the historical lookup for a forecast query (see the `bigquery-ai-ml` skill) |
| **AlloyDB / vector taste graph** | Cluster provisioning alone exceeds the budget; 20 menu items don't need vector search | `agent.py::t_search_menu` |
| **Real auth** | Zero demo value, and a persona switcher is strictly better for showing personalisation | `identity/sign-in-with-google-web` |
| **Voice (Live API)** | Highest wow, highest risk — websockets, audio encoding, stage noise | Web Speech API into the existing `/api/chat` is the cheap 80% |
| **Multi-agent orchestration** | Function-calling looks identical on screen at a fraction of the cost | — |
| **Websockets** | 2-second polling feels live and cannot break | `app.js::refresh` |

## Mandatory Google skills

See **[SKILLS.md](SKILLS.md)** for what was installed and exactly what each skill changed.
