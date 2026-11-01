# Google Skills used

Mandatory requirement: https://github.com/google/skills

## Installed

```bash
claude plugin marketplace add google/skills        # clones the repo + plugin marketplace
```

The marketplace exposes 17 MCP-server plugins; the 150+ **skills** live under `skills/` in
the same repo (normally installed with `npx skills add google/skills`, which needs Node —
not present on this machine, so the relevant skills were copied from the marketplace clone
into `.claude/skills/`, where Claude Code loads project skills from).

```
.claude/skills/
├── gemini-api/                 ← load-bearing
├── gemini-interactions-api/
├── bigquery-ai-ml/
└── finding-google-skills/
```

## What each one actually changed

| Skill | Effect on this codebase |
|-------|------------------------|
| **`cloud/gemini-api`** | The core of `app/agent.py`. `references/structured_and_tools.md` gave the function-calling contract: `types.Tool(function_declarations=[...])`, `types.Part.from_function_response(...)` for returning results, and `response.function_calls` as the accessor for pending calls (we had hand-rolled a `parts` scan; the skill's accessor replaced it). Its structured-output section (`response_json_schema`) is the documented fallback if function-calling ever misbehaves. |
| **`cloud/gemini-interactions-api`** | Multi-turn conversation shape — appending the model's own `Content` back into history before the tool-response turn, which is what keeps a multi-hop tool loop coherent. |
| **`cloud/bigquery-ai-ml`** | Not executed. It documents `AI.FORECAST` / `ML.FORECAST`, the production path for the wait-time model that `app/eta.py` currently implements as a transparent formula. See README §"What we deliberately didn't build". |
| **`developers/finding-google-skills`** | How the skill catalogue is organised (`ads/ analytics/ cloud/ developers/ identity/`) — used to pick the four above out of 150+. |

## Note on model ids

The `gemini-api` skill's examples use `gemini-3.8-flash`. On this API key that model returns
**504 DEADLINE_EXCEEDED**, and `gemini-2.5-flash` returns **404** ("no longer available to
new users"). The app therefore defaults to `gemini-3.5-flash` and fails over down a chain
of models — see README §"Model chain and quota".
