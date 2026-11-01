# The Cafe Companion — working notes

Hackathon POC (Hack2Skill "Cafe Companion" brief). **Built, deployed and working.**
Read this first; `PROGRESS.md` has the full build log and every decision's reasoning.

🔗 **Live:** https://cafe-companion-102256300780.us-central1.run.app
   · [staff board](https://cafe-companion-102256300780.us-central1.run.app/staff)
   · [health](https://cafe-companion-102256300780.us-central1.run.app/api/health)

## What it is

One Gemini agent, two surfaces, one shared room state. The brief's four themes (ordering,
waits, discovery, room sentiment) are the same data moving in a circle, so they share one
state object rather than being four features:

```
Customer chats → order placed → ETA from live queue → appears on Staff Board
      ↑                                                        ↓
   sentiment ← 1-tap feedback ← customer notified ← staff marks Ready
```

## Run it

```bash
.venv/bin/uvicorn app.main:app --reload     # → 127.0.0.1:8000 and /staff
pkill -f "uvicorn app.main:app"             # stop
```

Local defaults to the **in-memory** store — no GCP needed, state resets each restart.
Full command set (background start, status, Firestore locally, venv rebuild) is in
`PROGRESS.md` → *Running it locally*.

## Deploy

```bash
export PATH="$HOME/google-cloud-sdk/bin:$PATH"   # gcloud is NOT on PATH
./deploy.sh                                      # redeploy to the existing project
./deploy.sh --bootstrap                          # create a NEW project (rarely wanted)
```

| | |
|---|---|
| Project | `cafe-companion-515751` |
| Billing | $5 Codelab credit (account id not committed — `gcloud billing accounts list`) |
| Service | `cafe-companion` · us-central1 · revision `00005` |
| Database | Firestore Native, `nam5` |

## Layout

```
app/main.py    FastAPI: 2 pages + JSON API        app/data/     menu, personas, traffic, feedback
app/agent.py   Gemini + 8 function-calling tools  app/static/   2 pages, vanilla JS, no build step
app/state.py   the shared room state              app/store.py  memory | firestore backends
app/eta.py     the wait model
```

## Things that will bite you

1. **`--max-instances=1` is correctness, not cost.** Carts and chat history are in-memory
   per instance; a second instance would break cart continuity mid-conversation. Firestore
   fixed the cold-start reset, *not* this. Same reason uvicorn runs `--workers 1`.
2. **Gemini free tier is ~20 calls/day *per model*.** The agent walks a 5-model chain and
   marks each spent on a 429 (~100 calls total), then degrades to keyword ordering. Check
   `exhausted` in `/api/health` before demoing. Quota resets daily.
3. **Model ids rot.** `gemini-2.5-flash` → 404 (retired), `gemini-3.8-flash` → 504, and the
   Google skill's own examples use 3.8. Default is `gemini-3.5-flash`. Always set an
   explicit client timeout — a hang looks exactly like a network fault.
4. **gcloud lives at `~/google-cloud-sdk/bin`** — installed from Google's tarball because
   Homebrew's cask is broken (`Unexpected method 'postflight_steps'`).
5. **Fresh GCP projects have IAM propagation lag.** Artifact Registry returns
   `PERMISSION_DENIED` for a minute or two even with `roles/owner`. `--bootstrap` retries.
6. **Not a git repo.** No version history — worth `git init` before changing much.

## Before a demo

```bash
curl -X POST https://cafe-companion-102256300780.us-central1.run.app/api/reset   # clean board
curl -s  https://cafe-companion-102256300780.us-central1.run.app/api/health      # warm it + check quota
```

Cold start takes a few seconds (scale-to-zero), so always hit it once first.
The 90-second demo script is in `README.md`.

## The two design calls worth preserving

- **Grab-and-go items skip the queue.** Pre-brewed coffee and pastries need no barista, so
  they're handed over in ~1 min regardless of queue depth. Without this every item carried
  the same queue drain, the fastest and slowest warm drinks were 9 vs 10 minutes, and
  "I'm in a rush" had no answer. With it the spread is 1 vs 10 — that's the line that
  lands in the demo.
- **The ETA is a transparent formula, not a trained model.** Reproducible on stage, and the
  agent can *narrate* why ("it's the morning rush"), which reads smarter than a black-box
  number. `bigquery-ai-ml` is cited in `README.md` as the production path.

## Docs

| File | What's in it |
|------|--------------|
| `README.md` | Demo script, architecture, deploy, what was deliberately cut |
| `PROGRESS.md` | Build log, local-run commands, 8 bugs found, deploy snags |
| `brainstorm.md` | Original design thinking + what the plan got wrong |
| `SKILLS.md` | Mandatory Google-skills requirement and what each changed |
