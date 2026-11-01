"""The barista agent: Gemini + function-calling over the room state.

One agent with good tools, rather than a multi-agent system — it behaves identically on
screen for a fraction of the build cost. Tool calls are returned to the UI as a trace so
the demo can show the agent's reasoning, not just its prose.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import eta as eta_model
from .state import MENU, MENU_BY_ID, CUSTOMER_BY_ID, room

# Explicit path: bare load_dotenv() walks the call stack and can assert when the module
# is imported from an unusual frame. In Cloud Run there is no .env at all — the key
# arrives as a real environment variable and this is a harmless no-op.
load_dotenv(Path(__file__).parent.parent / ".env")

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# The AI Studio free tier allows ~20 generate_content calls per day *per model*, so a
# long demo session can exhaust one model. Each entry has its own quota bucket; on a 429
# we move down the chain rather than dropping to the keyword fallback mid-demo.
MODEL_CHAIN = [m.strip() for m in os.getenv(
    "GEMINI_MODEL_CHAIN",
    "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite,"
    "gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if m.strip()]
if MODEL not in MODEL_CHAIN:
    MODEL_CHAIN.insert(0, MODEL)
_exhausted: set[str] = set()

MAX_HOPS = 6
TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "45000"))

_client = None


def client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("no-key")
        from google import genai
        from google.genai import types
        # Hard timeout: a slow or broken model must fail fast, not hang the request.
        _client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    return _client


def has_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def active_model() -> str:
    """First model in the chain that has not hit its daily quota."""
    for m in MODEL_CHAIN:
        if m not in _exhausted:
            return m
    return MODEL_CHAIN[0]


def generate(contents, config):
    """generate_content with per-model quota failover.

    Returns (response, model_used). Raises the last error if every model is spent.
    """
    last = None
    for model in MODEL_CHAIN:
        if model in _exhausted:
            continue
        try:
            return client().models.generate_content(
                model=model, contents=contents, config=config), model
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            msg = str(exc)
            if code == 429 or "RESOURCE_EXHAUSTED" in msg:
                _exhausted.add(model)
                last = exc
                continue
            if code in (404, 503) or "NOT_FOUND" in msg or "DEADLINE_EXCEEDED" in msg:
                _exhausted.add(model)   # retired or unhealthy — skip for this session
                last = exc
                continue
            raise
    raise last or RuntimeError("no usable model")


# --------------------------------------------------------------------- tools
def _ready_in(item: dict) -> int:
    """How soon one item could be in your hand, given the queue right now."""
    est = eta_model.estimate([{**item, "qty": 1}], room.queue_ahead())
    return est["eta_minutes"]


def t_search_menu(query: str = "", tags: list[str] | None = None,
                  max_ready_minutes: int | None = None) -> dict:
    tags = [t.lower() for t in (tags or [])]
    q = (query or "").lower().strip()
    hits = []
    for m in MENU:
        score = 0
        if q:
            if q in m["name"].lower():
                score += 3
            if q in m["note"].lower():
                score += 1
            # Exact tag/category hits only. Substring matching made "sweet" match
            # the "not-sweet" tag, which inverted the meaning.
            item_tags = set(m["tags"])
            for w in q.split():
                if w == m["category"]:
                    score += 2
                elif w in item_tags:
                    score += 1
        matched = sum(1 for t in tags if t in m["tags"])
        if tags and matched < len(tags):
            # All requested tags must hold: "warm" + "not-sweet" must not return
            # an iced drink. Relaxed below if that leaves nothing.
            continue
        score += matched * 2
        if not q and not tags:
            score = 1
        if score <= 0:
            continue
        ready = _ready_in(m)
        if max_ready_minutes and ready > max_ready_minutes:
            continue
        hits.append({
            "item_id": m["id"], "name": m["name"], "price": m["price"],
            "tags": m["tags"], "note": m["note"],
            "prep_minutes": m["prep_minutes"], "ready_in_minutes": ready,
            "_score": score,
        })
    if not hits and len(tags) > 1:
        # Nothing satisfied every tag — retry on the single most selective one so the
        # agent gets something to work with instead of an empty list.
        return t_search_menu(query=query, tags=tags[:1],
                             max_ready_minutes=max_ready_minutes)
    hits.sort(key=lambda h: (-h["_score"], h["ready_in_minutes"]))
    for h in hits:
        h.pop("_score")
    return {"results": hits[:6], "count": len(hits)}


def t_get_usual(customer_id: str) -> dict:
    c = CUSTOMER_BY_ID.get(customer_id)
    if not c or not c["usual"]:
        return {"has_usual": False, "visits": c["visits"] if c else 0}
    items = [{"item_id": u["item_id"], "name": MENU_BY_ID[u["item_id"]]["name"],
              "qty": u["qty"]} for u in c["usual"]]
    return {"has_usual": True, "items": items, "visits": c["visits"],
            "likes": c["preferences"]["likes"], "dietary": c["preferences"]["dietary"]}


def t_add_to_cart(customer_id: str, item_id: str, qty: int = 1) -> dict:
    if item_id not in MENU_BY_ID:
        return {"ok": False, "error": f"no such item '{item_id}'",
                "hint": "call search_menu first and use an exact item_id"}
    cart = room.add_to_cart(customer_id, item_id, max(1, int(qty)))
    return {"ok": True, "cart": _cart_view(cart)}


def t_remove_from_cart(customer_id: str, item_id: str) -> dict:
    return {"ok": True, "cart": _cart_view(room.remove_from_cart(customer_id, item_id))}


def t_view_cart(customer_id: str) -> dict:
    return {"cart": _cart_view(room.cart(customer_id))}


def t_get_wait_estimate(customer_id: str) -> dict:
    cart = room.cart(customer_id)
    est = room.estimate_for(cart) if cart else room.estimate_for(
        [room.line_item("flat-white")])
    return {
        "eta_minutes": est["eta_minutes"], "position": est["position"],
        **est["breakdown"],
        "basis": "your current cart" if cart else "a typical single coffee",
    }


def t_place_order(customer_id: str) -> dict:
    c = CUSTOMER_BY_ID.get(customer_id)
    order = room.place_order(customer_id, c["name"] if c else "Guest")
    if not order:
        return {"ok": False, "error": "cart is empty — add something first"}
    return {"ok": True, "order_id": order["id"], "eta_minutes": order["eta_minutes"],
            "position": order["position"], "total": order["total"],
            "rush": order["eta_breakdown"]["rush_label"]}


def t_get_best_time_to_visit() -> dict:
    bt = eta_model.best_times_today()
    return {"day": bt["day"], "current_hour": bt["current_hour"],
            "quietest_upcoming": bt["quietest_upcoming"],
            "hourly": [{"hour": h["hour"], "orders": h["orders"]} for h in bt["hours"]]}


def _cart_view(cart: list[dict]) -> dict:
    return {
        "items": [{"item_id": l["item_id"], "name": l["name"], "qty": l["qty"],
                   "price": l["price"]} for l in cart],
        "total": round(sum(l["price"] * l["qty"] for l in cart), 2),
        "count": sum(l["qty"] for l in cart),
    }


# Tools that need the caller's identity injected rather than hallucinated.
NEEDS_CID = {"get_usual", "add_to_cart", "remove_from_cart", "view_cart",
             "get_wait_estimate", "place_order"}

HANDLERS = {
    "search_menu": t_search_menu, "get_usual": t_get_usual,
    "add_to_cart": t_add_to_cart, "remove_from_cart": t_remove_from_cart,
    "view_cart": t_view_cart, "get_wait_estimate": t_get_wait_estimate,
    "place_order": t_place_order, "get_best_time_to_visit": t_get_best_time_to_visit,
}

DECLARATIONS: list[dict[str, Any]] = [
    {"name": "search_menu",
     "description": "Search the menu. Returns ready_in_minutes for each item, which "
                    "accounts for the live queue — use it to say what is fastest now.",
     "parameters": {"type": "OBJECT", "properties": {
         "query": {"type": "STRING", "description": "free text, e.g. 'coffee', 'warm'"},
         "tags": {"type": "ARRAY", "items": {"type": "STRING"},
                  "description": "filter tags, e.g. warm, cold, vegan, caffeine-free, "
                                 "sweet, not-sweet, quick, strong, savoury, high-protein"},
         "max_ready_minutes": {"type": "INTEGER",
                               "description": "only items ready within N minutes"}}}},
    {"name": "get_usual",
     "description": "The signed-in customer's usual order, visit count and preferences.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "add_to_cart",
     "description": "Add a menu item to the cart. Use an exact item_id from search_menu.",
     "parameters": {"type": "OBJECT", "properties": {
         "item_id": {"type": "STRING"}, "qty": {"type": "INTEGER"}},
         "required": ["item_id"]}},
    {"name": "remove_from_cart", "description": "Remove an item from the cart.",
     "parameters": {"type": "OBJECT", "properties": {"item_id": {"type": "STRING"}},
                    "required": ["item_id"]}},
    {"name": "view_cart", "description": "What is currently in the cart.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "get_wait_estimate",
     "description": "Live wait estimate with the breakdown that produced it (queue depth, "
                    "stations, rush multiplier). Quote the reason, not just the number.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "place_order",
     "description": "Place the cart as a real order. Only call after the customer confirms.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "get_best_time_to_visit",
     "description": "Historical hourly load for today and the quietest upcoming hours.",
     "parameters": {"type": "OBJECT", "properties": {}}},
]

SYSTEM = """You are the barista at Loom & Bean, a neighbourhood cafe. You take orders by \
chat.

Voice: warm, brief, a regular who knows the menu. Two or three sentences at most. No \
bullet lists, no emoji, no corporate chirp. Never invent menu items or prices.

How to work:
- Always search_menu before recommending; use exact item_ids from it.
- Recommend at most two options and say why each suits what they asked for.
- ready_in_minutes already accounts for the live queue. When someone is in a hurry, or when \
two options differ by more than two minutes, say which is faster and by how much.
- If they mention "the usual", call get_usual and add those items.
- Respect dietary needs from get_usual absolutely.
- Add items to the cart as soon as they choose. Confirm what is in the cart in words.
- Never call place_order until they clearly confirm. After ordering, give the ETA and queue \
position, and mention the reason if it is busy.
- When you quote a wait, quote the cause too — "it's the morning rush" or "the queue is \
short right now".
"""


# ------------------------------------------------------------------ the loop
def chat(customer_id: str, message: str) -> dict:
    if not has_key():
        return _fallback(customer_id, message)

    from google.genai import types

    c = CUSTOMER_BY_ID.get(customer_id)
    snap_rush = eta_model.rush_multiplier()
    ctx = (f"Signed in: {c['name'] if c else 'Guest'} (customer_id={customer_id}, "
           f"{c['visits'] if c else 0} previous visits). "
           f"Right now the queue has {len(room.queue_ahead())} orders and the shop is "
           f"{snap_rush[1]}.")

    history = room.chats.setdefault(customer_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=message)]))

    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM + "\n" + ctx,
        tools=[types.Tool(function_declarations=DECLARATIONS)],
        temperature=0.6,
    )

    trace: list[dict] = []
    text = ""
    used = MODEL
    for _ in range(MAX_HOPS):
        resp, used = generate(history, cfg)
        cand = (resp.candidates or [None])[0]
        if cand is None or cand.content is None:
            break
        history.append(cand.content)

        # resp.function_calls is the accessor documented by the gemini-api skill
        # (references/structured_and_tools.md).
        calls = resp.function_calls or []
        if not calls:
            text = resp.text or ""
            break

        responses = []
        for call in calls:
            args = dict(call.args or {})
            if call.name in NEEDS_CID:
                args["customer_id"] = customer_id
            handler = HANDLERS.get(call.name)
            try:
                result = handler(**args) if handler else {"error": "unknown tool"}
            except Exception as exc:                      # keep the loop alive
                result = {"error": f"{type(exc).__name__}: {exc}"}
            trace.append({"tool": call.name,
                          "args": {k: v for k, v in args.items() if k != "customer_id"},
                          "result": result})
            responses.append(types.Part.from_function_response(
                name=call.name, response=result))
        history.append(types.Content(role="user", parts=responses))

    del history[:-20]  # keep the context small; this is a demo, not a long session
    return {"reply": text.strip() or "Sorry, I didn't catch that — say that again?",
            "trace": trace, "cart": _cart_view(room.cart(customer_id)),
            "order": room.order_for(customer_id), "mode": "gemini", "model": used}


def _fallback(customer_id: str, message: str) -> dict:
    """Keyword ordering so the UI is demoable before a key is set. Not the real path."""
    m = message.lower()
    trace: list[dict] = []
    reply = ""
    if "usual" in m:
        u = t_get_usual(customer_id)
        trace.append({"tool": "get_usual", "args": {}, "result": u})
        if u.get("has_usual"):
            for it in u["items"]:
                t_add_to_cart(customer_id, it["item_id"], it["qty"])
            reply = ("Your usual: " + ", ".join(i["name"] for i in u["items"])
                     + ". Added — confirm and I'll send it through.")
        else:
            reply = "You've not been in before, so no usual yet. What do you fancy?"
    elif any(w in m for w in ("yes", "confirm", "go ahead", "place", "order it")):
        r = t_place_order(customer_id)
        trace.append({"tool": "place_order", "args": {}, "result": r})
        reply = (f"Order {r['order_id']} is in — about {r['eta_minutes']} minutes, "
                 f"you're number {r['position']} in the queue."
                 if r.get("ok") else "Your cart's empty — what can I get you?")
    else:
        hits = t_search_menu(query=m)
        trace.append({"tool": "search_menu", "args": {"query": m}, "result": hits})
        if hits["results"]:
            top = hits["results"][0]
            t_add_to_cart(customer_id, top["item_id"])
            reply = (f"{top['name']} — {top['note']} About {top['ready_in_minutes']} "
                     f"minutes right now. Added to your cart.")
        else:
            reply = "I couldn't match that. Try 'coffee', 'something warm', or 'the usual'."
    return {"reply": reply + "  (demo mode — set GEMINI_API_KEY for the real agent)",
            "trace": trace, "cart": _cart_view(room.cart(customer_id)),
            "order": room.order_for(customer_id), "mode": "fallback"}
