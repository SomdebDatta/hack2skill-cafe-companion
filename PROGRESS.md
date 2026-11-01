# Build Progress — The Cafe Companion

**Target:** 3.5h POC, clean, runs E2E live. Shape C (the Companion Loop).
**Stack:** Python 3.12 + FastAPI + vanilla JS. Gemini via `google-genai`. No build step.
**Run:** see [Running it locally](#running-it-locally) below.

**Status: complete, deployed, and verified in production.**

🔗 **https://cafe-companion-102256300780.us-central1.run.app** · [staff board](https://cafe-companion-102256300780.us-central1.run.app/staff)

---

## Running it locally

From `/Users/somdebdatta/coding/hack2skill`:

```bash
# start (foreground — Ctrl+C to stop)
.venv/bin/uvicorn app.main:app --reload

# start in the background instead
nohup .venv/bin/uvicorn app.main:app --port 8000 > /tmp/uvicorn.log 2>&1 &
```

| | |
|---|---|
| Customer | http://127.0.0.1:8000 |
| Staff board | http://127.0.0.1:8000/staff |
| Health | http://127.0.0.1:8000/api/health |

```bash
# stop it
pkill -f "uvicorn app.main:app"

# check whether it is running
pgrep -f "uvicorn app.main:app" && echo running || echo stopped

# tail the log if started in the background
tail -f /tmp/uvicorn.log
```

**Backend.** Locally it defaults to the in-memory store — no GCP needed, nothing to
configure, and state resets on every restart. To point local code at the real Firestore
database instead (needs `gcloud auth application-default login` once, which opens a
browser):

```bash
STORE=firestore GOOGLE_CLOUD_PROJECT=cafe-companion-515751 \
  .venv/bin/uvicorn app.main:app --reload
```

`GET /api/health` tells you which backend is live (`"store":"memory"` vs `"firestore"`).

**Gemini key** is read from `.env` in this directory. Without it the app still runs on a
keyword fallback, so the UI is never dead.

**Fresh start if a venv is missing:**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**gcloud** was installed from Google's tarball (Homebrew's cask is broken) and is **not on
your PATH**. Use the full path, or prepend it for the session:

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
```

---

## Blocks

| # | Block | Status | Gate result |
|---|-------|--------|-------------|
| 0 | Scaffold: venv, deps, structure | ✅ | fastapi + google-genai installed |
| 1 | Seed data | ✅ | 20 items, prep 1–8m, 3 personas, 84 traffic rows, 8 reviews |
| 2 | `state.py`, `eta.py` | ✅ | order placed, ETA 12m @ peak rush |
| 3 | `agent.py` + 8 tools | ✅ **gate passed** | "a flat white please" → agent upgraded it to Oat Flat White from profile |
| 4 | `main.py` API + customer UI | ✅ | full order flow in browser |
| 5 | Staff board | ✅ | chat order appears on board within 2s |
| 6 | Feedback → sentiment tile | ✅ | loop closes; "oat milk" ticks 2→3 |
| 7 | Polish | ✅ | centred empty states, loading skeletons, tool-trace chips |
| 8 | README, SKILLS.md | ✅ | both written |

## Verified in the browser

- Customer page renders; greeting, suggestion chips, spark chart, cart, ETA panel.
- Agent reply, verbatim: *"the Batch Filter is warm, not sweet, and ready in just 1 minute
  since it's already brewed… Long Black… takes 10 minutes behind the espresso
  queue—making the filter 9 minutes faster."* ← demo beat #2.
- Staff board renders queue, grab-and-go pills, sentiment gauge, themes, Gemini digest.
- Digest unprompted surfaced the 14-minute wait complaint **and** the recurring croissant
  stockout, with a concrete next-hour action.

---

## Decisions made

- **Python + FastAPI, not Next.js** — Node isn't installed on this machine. No build step,
  one command to run.
- **2s polling, no websockets, no auth** (persona dropdown instead).
- ~~**In-memory state, no DB**~~ → **reversed after deploying.** Cloud Run's scale-to-zero
  wiped the room mid-demo, so orders and feedback now persist to Firestore. Carts and chat
  history are still deliberately in memory. See *Persistence* below.
- **One agent with 8 tools**, not a multi-agent system.
- **ETA is a transparent formula**, not a trained model — narratable and reproducible.

## Notes & deviations

**Model selection — three real failures:**

| Model | Result |
|-------|--------|
| `gemini-2.5-flash` | 404 — "no longer available to new users" |
| `gemini-3.8-flash` | 504 DEADLINE_EXCEEDED after 39s, even with `thinking_budget=0` |
| `gemini-3.5-flash` | 3.0s ✅ — current default |

The 3.8 hang initially looked like a connection failure and wasn't (`models.list()`
succeeded throughout). Added `HttpOptions(timeout=45000)` so a bad model fails fast.

**429 free-tier quota (found during testing, not at demo time).** AI Studio allows ~20
`generate_content` calls/day **per model**. Testing exhausted `gemini-3.5-flash`. Added a
failover chain across 5 models (~100 calls of headroom), each marked spent on a 429, with
the keyword path as the final floor. `GET /api/health` reports the active and exhausted
models. Verified live: 3.5-flash 429'd and 3.5-flash-lite served the request transparently.

**Considered and rejected: switching the AI layer to Claude.** No `ANTHROPIC_API_KEY` is
set, the Claude Code `/login` is an OAuth session rather than a usable app key, and the
brief mandates Google skills — a Gemini-free submission is a judging risk. Gemini was
healthy; only the model id was wrong.

**Bugs found and fixed during the build:**

1. Tag search was OR, so "warm + not-sweet" returned **Cold Brew**. Now requires all tags,
   relaxing to the first tag only if that yields nothing.
2. Substring tag matching made `"sweet"` match the `not-sweet` tag — inverting the meaning
   and ranking Batch Filter top for "something sweet". Now exact tag membership.
3. `search_menu("coffee")` returned nothing — scoring ignored `category`. Fixed.
4. Sentiment themes used a `[a-z]{4,}` bag-of-words, which **missed "oat"** — the phrase the
   demo's closing beat depends on. Replaced with a tracked-phrase watchlist.
5. Theme counts double-counted overlapping needles ("oat milk" + "oat" in one comment).
   Now counts reviews mentioning a theme, which is also what the demo line claims.
6. `line_item` dropped the `premade` flag, so stored orders overcounted queue work by
   treating pastries as barista work.
7. Staff tickets showed a frozen order-time ETA next to live elapsed wait ("waiting 7m" /
   "2 min est") — contradictory. Now shows **prep minutes**, which is what a barista needs.
8. **Customer ETA was frozen at placement while queue position updated live**, so the panel
   could read "11 min" beside "1st in the queue". `/api/order/{cid}` now recomputes the
   estimate from the current queue on every poll; orders ahead only ever leave, so it
   counts down monotonically. Verified: 11m/3rd → 9m/2nd → 3m/1st. The placement-time
   value is kept as `eta_at_order`.

## How order time is tracked

Only two timestamps are stored — `placed_at` and `ready_at`. There is no timer, scheduler
or countdown job; every figure is derived from `placed_at` at read time.

| Figure | Computed in | Refresh |
|--------|-------------|---------|
| Customer ETA | `/api/order/{cid}` from the live queue | every 2s poll |
| Customer position | `/api/order/{cid}` | every 2s poll |
| Staff elapsed wait | browser, `Date.now() - placed_at` | every 2s poll |
| Staff prep minutes | `state.snapshot()` | every 2s poll |

Seeded orders are backdated with `timedelta(minutes=mins_ago)` so the board is never empty.

**Modelling change that made the demo work.** Originally every item carried the full queue
drain, so the fastest and slowest warm drinks were 9 vs 10 minutes — "I'm in a rush" was
meaningless. Added `premade` for already-brewed and case items: they need no barista, so
they skip the queue. The spread became 1 vs 10 minutes, which is what gives the agent
something real to say.

## Deployment (Cloud Run)

| | |
|---|---|
| Project | `cafe-companion-515751` (billing: the \$5 Codelab credit — id kept out of the repo) |
| Service | `cafe-companion`, us-central1 (revision at handoff: `00005`) |
| Database | Firestore Native, `nam5` |
| Redeploy | `./deploy.sh` (pinned to the project; `--bootstrap` creates a new one) |
| Reset before a demo | `curl -X POST <url>/api/reset` |
| Check live revision | `gcloud run services describe cafe-companion --project cafe-companion-515751 --region us-central1 --format='value(status.latestReadyRevisionName)'` |

**Credit spent: effectively nothing.** Project, APIs, Artifact Registry and Firestore
provisioning are free; builds sit inside Cloud Build's 120 min/day and traffic inside
Cloud Run's 2M requests/month. Firestore's daily free tier (1 GiB, 50k reads / 20k writes)
dwarfs demo usage.

### Snags hit while deploying

1. **Homebrew's gcloud cask is broken** — `Unexpected method 'postflight_steps' called on
   Cask gcloud-cli`. Installed from Google's tarball to `~/google-cloud-sdk` instead.
   My `| tail -25` swallowed brew's exit code, so the failure first looked like `EXIT=0`.
2. **Artifact Registry `IAM_PERMISSION_DENIED` despite `roles/owner`** — propagation lag on
   a minutes-old project. Cleared on retry; `deploy.sh --bootstrap` now has the retry loop.
3. **Cloud Run issues two hostnames** for one service (`...-102256300780.us-central1.run.app`
   and `...-q66hvn3bwq-uc.a.run.app`). Both 200, same service.

## Persistence

Added `app/store.py`: one interface, two backends.

| Backend | When | Durable |
|---------|------|---------|
| `MemoryStore` | default, local dev, tests | no |
| `FirestoreStore` | `STORE=firestore` | yes |

**Only orders and feedback persist.** Carts and chat history stay in memory deliberately —
per-session scratch that churns on every keystroke; persisting it would burn quota for no
benefit. A Firestore backend that fails to initialise logs a warning and degrades to
memory rather than 500ing: a demo that forgets beats a demo that breaks.

`--max-instances=1` **still stands.** Firestore fixes the cold-start reset, not cart
continuity — a cart would not follow a customer across instances mid-conversation.

**Verified in production, not assumed:** placed `#003 Priya · Masala Chai` plus a test
review, forced a brand-new revision (`00004`, fresh container, in-memory state destroyed),
and both survived — `orders 3`, `feedback 9`. Then `POST /api/reset` cleared Firestore and
re-seeded cleanly (`orders 2, feedback 8`, no leftover test rows), and the agent still
returned `mode: gemini` with a correct tool trace.

## Open / possible next

- Voice input via Web Speech API into the existing `/api/chat` (~40m, best ratio remaining).
- Semantic menu search over Gemini embeddings (~30m).
- Free-tier quota is the one live demo risk; a paid key removes it entirely.
