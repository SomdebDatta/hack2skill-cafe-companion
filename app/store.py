"""Persistence for the room state.

Two backends behind one interface:

* ``MemoryStore``    — the original behaviour. No provisioning, nothing to configure,
  and the right choice for local development and tests.
* ``FirestoreStore`` — survives a Cloud Run cold start. Serverless and scale-to-zero,
  so it costs nothing while idle, unlike an always-on Cloud SQL instance.

Only **orders** and **feedback** are persisted. Carts and chat history are deliberately
left in memory: they are per-session scratch state, they churn on every keystroke, and
writing them would burn Firestore quota for no benefit.

Select with ``STORE=firestore`` (and ``GOOGLE_CLOUD_PROJECT``); anything else, including
unset, gives you memory. A Firestore backend that fails to initialise degrades to memory
with a warning rather than taking the app down — a demo that forgets is better than a
demo that 500s.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("cafe.store")

ORDERS = "orders"
FEEDBACK = "feedback"


class MemoryStore:
    """No-op persistence. State lives in the Room object and dies with the process."""

    kind = "memory"
    durable = False

    def load(self, collection: str) -> list[dict]:
        return []

    def put(self, collection: str, key: str, doc: dict) -> None:
        pass

    def clear(self) -> None:
        pass


class FirestoreStore:
    """Firestore in Native mode. One document per order / feedback item."""

    kind = "firestore"
    durable = True

    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        from google.cloud import firestore

        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.database = database or os.getenv("FIRESTORE_DATABASE", "(default)")
        self._db = firestore.Client(project=self.project, database=self.database)

    def load(self, collection: str) -> list[dict]:
        return [d.to_dict() for d in self._db.collection(collection).stream()]

    def put(self, collection: str, key: str, doc: dict) -> None:
        # "#003" is a fine Firestore document id, but stripping the sigil keeps ids
        # readable in the console and safe in any future path-based lookup.
        self._db.collection(collection).document(key.lstrip("#")).set(doc)

    def clear(self) -> None:
        for collection in (ORDERS, FEEDBACK):
            for doc in self._db.collection(collection).stream():
                doc.reference.delete()


def build_store():
    """Pick a backend from the environment, degrading to memory on any failure."""
    want = os.getenv("STORE", "memory").strip().lower()
    if want != "firestore":
        return MemoryStore()
    try:
        store = FirestoreStore()
        log.info("store: firestore (project=%s db=%s)", store.project, store.database)
        return store
    except Exception as exc:
        log.warning("store: firestore unavailable (%s: %s) — falling back to memory",
                    type(exc).__name__, exc)
        return MemoryStore()
