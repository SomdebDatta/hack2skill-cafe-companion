"""The shared room state — the single object all four themes read and write.

Backed by a pluggable store (see ``store.py``): memory by default, Firestore when
``STORE=firestore``. Orders and feedback are written through on every mutation so they
survive a Cloud Run cold start; carts and chat history stay in memory by design.
"""
from __future__ import annotations

import itertools
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock

from . import eta as eta_model
from .store import FEEDBACK, ORDERS, build_store

DATA = Path(__file__).parent / "data"

MENU: list[dict] = json.loads((DATA / "menu.json").read_text())
CUSTOMERS: list[dict] = json.loads((DATA / "customers.json").read_text())
MENU_BY_ID = {m["id"]: m for m in MENU}
CUSTOMER_BY_ID = {c["id"]: c for c in CUSTOMERS}

_lock = RLock()

ACTIVE = ("queued", "preparing")


class Room:
    """Everything the cafe currently knows about itself."""

    def __init__(self, store=None) -> None:
        self.store = store or build_store()
        self.carts: dict[str, list[dict]] = {}
        self.chats: dict[str, list[dict]] = {}
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        self.orders = sorted(self.store.load(ORDERS), key=lambda o: o["placed_at"])
        self.feedback = self.store.load(FEEDBACK)
        # Order ids continue from whatever is already persisted rather than restarting
        # at #001 and colliding with an existing document.
        highest = max((int(o["id"].lstrip("#")) for o in self.orders), default=0)
        self._seq = itertools.count(highest + 1)
        if not self.orders and not self.feedback:
            self._seed()

    def reset(self) -> None:
        """Wipe and re-seed. Handy immediately before a demo."""
        with _lock:
            self.store.clear()
            self.orders, self.feedback = [], []
            self.carts, self.chats = {}, {}
            self._seq = itertools.count(1)
            self._seed()

    # ---------------------------------------------------------------- seeding
    def _seed(self) -> None:
        raw = json.loads((DATA / "feedback.json").read_text())
        now = datetime.now()
        for f in raw:
            mins = int(re.sub(r"[^0-9-]", "", f["at"]))
            fb = {**f, "at": (now + timedelta(minutes=mins)).isoformat()}
            self.feedback.append(fb)
            self.store.put(FEEDBACK, fb["id"], fb)

        # A couple of orders already in flight, so the board and the queue are never empty.
        self._seed_order("Marcus", [("cortado", 1), ("almond-croissant", 1)], 4, "preparing")
        self._seed_order("Aisha", [("halloumi-wrap", 1)], 2, "queued")

    def _seed_order(self, name: str, items: list[tuple[str, int]], mins_ago: int, status: str) -> None:
        line = [self.line_item(i, q) for i, q in items]
        placed = datetime.now() - timedelta(minutes=mins_ago)
        est = eta_model.estimate(line, [])
        order = {
            "id": f"#{next(self._seq):03d}",
            "customer_id": None,
            "customer_name": name,
            "items": line,
            "status": status,
            "placed_at": placed.isoformat(),
            "eta_minutes": est["eta_minutes"],
            "ready_at": None,
            "total": round(sum(i["price"] * i["qty"] for i in line), 2),
        }
        self.orders.append(order)
        self.store.put(ORDERS, order["id"], order)

    # ------------------------------------------------------------------ menu
    @staticmethod
    def line_item(item_id: str, qty: int = 1) -> dict:
        m = MENU_BY_ID[item_id]
        return {
            "item_id": m["id"], "name": m["name"], "price": m["price"],
            "prep_minutes": m["prep_minutes"], "qty": qty,
            # Carried through so stored orders still know what skips the queue.
            "premade": m.get("premade", False),
        }

    # ------------------------------------------------------------------ cart
    def cart(self, cid: str) -> list[dict]:
        return self.carts.setdefault(cid, [])

    def add_to_cart(self, cid: str, item_id: str, qty: int = 1) -> list[dict]:
        with _lock:
            cart = self.cart(cid)
            for line in cart:
                if line["item_id"] == item_id:
                    line["qty"] += qty
                    return cart
            cart.append(self.line_item(item_id, qty))
            return cart

    def remove_from_cart(self, cid: str, item_id: str) -> list[dict]:
        with _lock:
            self.carts[cid] = [l for l in self.cart(cid) if l["item_id"] != item_id]
            return self.carts[cid]

    def clear_cart(self, cid: str) -> None:
        with _lock:
            self.carts[cid] = []

    # ----------------------------------------------------------------- queue
    def active_orders(self) -> list[dict]:
        return [o for o in self.orders if o["status"] in ACTIVE]

    def queue_ahead(self, before: str | None = None) -> list[dict]:
        active = sorted(self.active_orders(), key=lambda o: o["placed_at"])
        if before is None:
            return active
        return [o for o in active if o["id"] != before and o["placed_at"] < before]

    def estimate_for(self, items: list[dict]) -> dict:
        return eta_model.estimate(items, self.queue_ahead())

    # ---------------------------------------------------------------- orders
    def place_order(self, cid: str, customer_name: str) -> dict | None:
        with _lock:
            items = self.cart(cid)
            if not items:
                return None
            est = self.estimate_for(items)
            order = {
                "id": f"#{next(self._seq):03d}",
                "customer_id": cid,
                "customer_name": customer_name,
                "items": [dict(i) for i in items],
                "status": "queued",
                "placed_at": datetime.now().isoformat(),
                "eta_minutes": est["eta_minutes"],
                "eta_breakdown": est["breakdown"],
                "position": est["position"],
                "ready_at": None,
                "total": round(sum(i["price"] * i["qty"] for i in items), 2),
            }
            self.orders.append(order)
            self.store.put(ORDERS, order["id"], order)
            self.clear_cart(cid)
            return order

    def set_status(self, order_id: str, status: str) -> dict | None:
        with _lock:
            for o in self.orders:
                if o["id"] == order_id:
                    o["status"] = status
                    if status == "ready":
                        o["ready_at"] = datetime.now().isoformat()
                    self.store.put(ORDERS, o["id"], o)
                    return o
            return None

    def order_for(self, cid: str) -> dict | None:
        mine = [o for o in self.orders if o["customer_id"] == cid]
        return mine[-1] if mine else None

    # -------------------------------------------------------------- feedback
    def add_feedback(self, customer: str, rating: str, comment: str, item_id: str | None) -> dict:
        with _lock:
            fb = {
                "id": uuid.uuid4().hex[:8], "customer": customer, "rating": rating,
                "comment": comment.strip(), "item_id": item_id,
                "at": datetime.now().isoformat(),
            }
            self.feedback.append(fb)
            self.store.put(FEEDBACK, fb["id"], fb)
            return fb

    def sentiment(self) -> dict:
        fb = self.feedback
        up = sum(1 for f in fb if f["rating"] == "up")
        down = len(fb) - up
        score = round(up / len(fb) * 100) if fb else 0

        # Deterministic phrase tracking. A bag-of-words count missed short but
        # load-bearing terms like "oat"; an explicit watchlist is reliable and the
        # Gemini digest supplies the nuance on top.
        comments = [f["comment"].lower() for f in fb]
        watch = {
            "oat milk": ("oat milk", "oat"),
            "wait times": ("waited", "waiting", "queue wasn't moving", "14 minutes"),
            "sold out": ("ran out", "sold out", "before 11"),
            "speed": ("instantly", "ready instantly", "fast", "quick"),
            "value": ("steal", "£", "price"),
            "not sweet": ("not sweet", "not too sweet"),
        }
        # Count reviews mentioning a theme, not raw needle hits, so overlapping
        # needles ("oat milk" / "oat") can't inflate the same comment twice.
        themes = []
        for label, needles in watch.items():
            hits = sum(1 for c in comments if any(n in c for n in needles))
            if hits:
                themes.append({"term": label, "count": hits})
        themes.sort(key=lambda t: -t["count"])
        themes = themes[:5]

        return {
            "score": score, "up": up, "down": down, "total": len(fb),
            "themes": themes,
            "recent": sorted(fb, key=lambda f: f["at"], reverse=True)[:6],
        }

    # ------------------------------------------------------------------ view
    def snapshot(self) -> dict:
        active = sorted(self.active_orders(), key=lambda o: o["placed_at"])
        ready = [o for o in self.orders if o["status"] == "ready"]
        mult, label = eta_model.rush_multiplier()
        # Barista-work minutes per order: what the staff board needs to prioritise,
        # as opposed to the customer-facing ETA frozen at order time.
        orders = []
        for o in sorted(self.orders, key=lambda o: o["placed_at"], reverse=True):
            orders.append({**o, "prep_minutes": round(eta_model.order_prep_minutes(o["items"]), 1)})
        return {
            "orders": orders,
            "queue_depth": len(active),
            "queue_work_minutes": round(
                sum(eta_model.order_prep_minutes(o["items"]) for o in active), 1),
            "ready_count": len(ready),
            "rush": {"multiplier": mult, "label": label},
            "sentiment": self.sentiment(),
            "best_times": eta_model.best_times_today(),
        }


room = Room()
