"""FastAPI surface over the room state. Two pages, one JSON API, no build step."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent
from .state import CUSTOMERS, MENU, room

# Explicit path: bare load_dotenv() walks the call stack and can assert when the module
# is imported from an unusual frame. In Cloud Run there is no .env at all — the key
# arrives as a real environment variable and this is a harmless no-op.
load_dotenv(Path(__file__).parent.parent / ".env")

HERE = Path(__file__).parent
app = FastAPI(title="The Cafe Companion")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ----------------------------------------------------------------- payloads
class ChatIn(BaseModel):
    customer_id: str
    message: str


class CartIn(BaseModel):
    customer_id: str
    item_id: str
    qty: int = 1


class OrderIn(BaseModel):
    customer_id: str


class StatusIn(BaseModel):
    status: str


class FeedbackIn(BaseModel):
    customer_id: str | None = None
    customer: str = "Guest"
    rating: str
    comment: str = ""
    item_id: str | None = None


# -------------------------------------------------------------------- pages
@app.get("/")
def customer_page():
    return FileResponse(HERE / "static" / "customer.html")


@app.get("/staff")
def staff_page():
    return FileResponse(HERE / "static" / "staff.html")


# ---------------------------------------------------------------------- api
@app.get("/api/health")
def health():
    return {"ok": True, "model": agent.active_model(), "gemini": agent.has_key(),
            "exhausted": sorted(agent._exhausted), "orders": len(room.orders),
            "store": room.store.kind, "durable": room.store.durable}


@app.get("/api/bootstrap")
def bootstrap():
    return {"customers": [{"id": c["id"], "name": c["name"], "visits": c["visits"],
                           "dietary": c["preferences"]["dietary"]} for c in CUSTOMERS],
            "menu": MENU, "gemini": agent.has_key(), "model": agent.active_model()}


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    try:
        return agent.chat(body.customer_id, body.message)
    except Exception as exc:
        # Never leave the UI with a dead chat box: degrade to the keyword path.
        out = agent._fallback(body.customer_id, body.message)
        out["reply"] = f"{out['reply']}\n\n(agent error: {type(exc).__name__})"
        out["mode"] = "error-fallback"
        return JSONResponse(out, status_code=200)


@app.get("/api/cart/{customer_id}")
def get_cart(customer_id: str):
    return agent._cart_view(room.cart(customer_id))


@app.post("/api/cart/add")
def cart_add(body: CartIn):
    return agent.t_add_to_cart(body.customer_id, body.item_id, body.qty)


@app.post("/api/cart/remove")
def cart_remove(body: CartIn):
    return agent.t_remove_from_cart(body.customer_id, body.item_id)


@app.post("/api/order")
def place(body: OrderIn):
    r = agent.t_place_order(body.customer_id)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "could not place order"))
    return r


@app.get("/api/order/{customer_id}")
def my_order(customer_id: str):
    """Current order with a *live* ETA.

    The ETA stored at placement goes stale the moment the queue moves, which left the
    customer seeing a frozen "11 min" while their position counted down beside it.
    Orders ahead only ever leave the queue, so recomputing here counts down monotonically.
    """
    from . import eta as eta_model

    o = room.order_for(customer_id)
    if not o:
        return {"order": None}
    if o["status"] not in ("queued", "preparing"):
        return {"order": o, "position": 0}

    ahead = room.queue_ahead(before=o["placed_at"])
    live = eta_model.estimate(o["items"], ahead)
    return {
        "order": {**o,
                  "eta_minutes": live["eta_minutes"],
                  "eta_breakdown": live["breakdown"],
                  "eta_at_order": o["eta_minutes"]},
        "position": len(ahead) + 1,
    }


@app.post("/api/order/{order_id}/status")
def set_status(order_id: str, body: StatusIn):
    oid = order_id if order_id.startswith("#") else f"#{order_id}"
    o = room.set_status(oid, body.status)
    if not o:
        raise HTTPException(404, f"no order {oid}")
    return o


@app.get("/api/room")
def get_room():
    return room.snapshot()


@app.post("/api/feedback")
def add_feedback(body: FeedbackIn):
    name = body.customer
    if body.customer_id:
        from .state import CUSTOMER_BY_ID
        c = CUSTOMER_BY_ID.get(body.customer_id)
        if c:
            name = c["name"]
    return room.add_feedback(name, body.rating, body.comment, body.item_id)


@app.get("/api/digest")
def digest():
    """Gemini turns the day's feedback into something a manager can act on."""
    s = room.sentiment()
    if not agent.has_key():
        top = ", ".join(f"{t['term']} ({t['count']})" for t in s["themes"][:3])
        return {"digest": f"{s['up']} positive, {s['down']} negative today. "
                          f"Recurring themes: {top or 'none yet'}.", "mode": "fallback"}
    lines = "\n".join(
        f"- [{'+' if f['rating'] == 'up' else '-'}] {f['customer']}: {f['comment']}"
        for f in sorted(room.feedback, key=lambda f: f["at"], reverse=True)[:12])
    prompt = (
        "You are advising the manager of a busy cafe. Here is today's customer feedback:\n"
        f"{lines}\n\n"
        "Write exactly three single-sentence bullets, each starting with '- '. One on what "
        "is working, one on the most urgent problem, and one concrete action for the next "
        "hour. Be specific and quote numbers where the feedback supports it. No preamble.")
    try:
        from google.genai import types
        r, used = agent.generate(
            prompt, types.GenerateContentConfig(temperature=0.4))
        return {"digest": (r.text or "").strip(), "mode": "gemini", "model": used}
    except Exception as exc:
        return {"digest": f"{s['up']} positive, {s['down']} negative today.",
                "mode": f"error:{type(exc).__name__}"}


@app.post("/api/reset")
def reset():
    """Wipe orders and feedback, then re-seed. Run this right before a demo."""
    room.reset()
    return {"ok": True, "orders": len(room.orders), "feedback": len(room.feedback),
            "store": room.store.kind}


@app.get("/api/best-times")
def best_times():
    from . import eta as eta_model
    return eta_model.best_times_today()
