// Staff board: reads the same room state the customer surface writes to.
const $ = (s) => document.querySelector(s);
const api = async (url, body) => {
  const r = await fetch(url, body ? {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : {});
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

const LATE_AFTER = 10;           // minutes waiting before a ticket turns red
const NEXT = { queued: "preparing", preparing: "ready", ready: "collected" };
const ACTION = { queued: "Start", preparing: "Mark ready", ready: "Collected" };

let seenFeedback = new Set();
let firstPaint = true;
let digestBusy = false;

const waitedMin = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));

// ----------------------------------------------------------------- tickets
function ticket(o) {
  const waited = waitedMin(o.placed_at);
  const late = o.status !== "ready" && o.status !== "collected" && waited >= LATE_AFTER;
  const el = document.createElement("div");
  el.className = `ticket ${o.status}${late ? " late" : ""}`;

  const items = o.items.map((i) =>
    `<span class="${i.premade ? "pre" : ""}">${i.qty > 1 ? i.qty + "× " : ""}${i.name}</span>`
  ).join("");

  const side = (o.status === "ready" || o.status === "collected")
    ? `<span class="t-stage">${o.status}</span>`
    : `<span class="t-eta">${o.prep_minutes}<small>min prep</small></span>`;

  el.innerHTML = `
    <div class="t-main">
      <div class="t-head">
        <span class="t-id">${o.id}</span>
        <span class="t-name">${o.customer_name}</span>
        <span class="t-wait">waiting ${waited}m${late ? " — overdue" : ""}</span>
      </div>
      <div class="t-items">${items}</div>
    </div>
    <div class="t-side">${side}</div>`;

  if (NEXT[o.status]) {
    const b = document.createElement("button");
    b.className = `t-btn${o.status === "ready" ? " flat" : ""}`;
    b.textContent = ACTION[o.status];
    b.onclick = async () => {
      b.disabled = true;
      await api(`/api/order/${o.id.replace("#", "")}/status`, { status: NEXT[o.status] });
      tick();
    };
    el.querySelector(".t-side").appendChild(b);
  }
  return el;
}

// ------------------------------------------------------------------- room
function renderRoom(s) {
  $("#sQueue").textContent = s.queue_depth;
  $("#sWork").textContent = s.queue_work_minutes;
  $("#sRush").textContent = s.rush.label;
  $("#sScore").textContent = s.sentiment.total ? s.sentiment.score + "%" : "—";
  $("#sRush").parentElement.classList.toggle("hot", s.rush.multiplier >= 1.3);

  const sent = s.sentiment;
  $("#gaugeFill").style.width = sent.score + "%";
  $("#upCount").textContent = sent.up;
  $("#downCount").textContent = sent.down;
  $("#fbCount").textContent = `${sent.total} today`;

  $("#themes").innerHTML = sent.themes.length
    ? sent.themes.map((t, i) =>
        `<span class="${i === 0 ? "rise" : ""}">${t.term} <b>${t.count}</b></span>`).join("")
    : `<span class="muted">no themes yet</span>`;

  $("#recent").innerHTML = "";
  sent.recent.forEach((f) => {
    const fresh = !firstPaint && !seenFeedback.has(f.id);
    seenFeedback.add(f.id);
    const row = document.createElement("div");
    row.className = `fbk ${f.rating}${fresh ? " fresh" : ""}`;
    row.innerHTML = `<span class="dot">${f.rating === "up" ? "●" : "▲"}</span>
      <span><span class="who">${f.customer}:</span> ${f.comment}</span>`;
    $("#recent").appendChild(row);
    if (fresh) loadDigest();          // new feedback → re-read the room
  });
}

// ----------------------------------------------------------------- digest
async function loadDigest() {
  if (digestBusy) return;
  digestBusy = true;
  $("#digestChip").textContent = "reading…";
  try {
    const d = await api("/api/digest");
    $("#digest").innerHTML = d.digest.split("\n")
      .map((l) => l.replace(/^\s*[-•*]\s*/, "").trim())
      .filter(Boolean)
      .map((l) => `<p class="b">${l}</p>`).join("");
    $("#digestChip").textContent = d.mode === "gemini" ? "Gemini" : d.mode;
  } catch {
    $("#digestChip").textContent = "unavailable";
  } finally {
    digestBusy = false;
  }
}

// ------------------------------------------------------------------- tick
async function tick() {
  try {
    const s = await api("/api/room");
    const active = s.orders.filter((o) => o.status === "queued" || o.status === "preparing");
    const done = s.orders.filter((o) => o.status === "ready" || o.status === "collected");

    active.sort((a, b) => new Date(a.placed_at) - new Date(b.placed_at));
    const q = $("#queue");
    q.innerHTML = "";
    if (!active.length) {
      q.innerHTML = `<div class="empty"><strong>Queue's clear.</strong>Nothing waiting — good time to restock the case.</div>`;
    } else active.forEach((o) => q.appendChild(ticket(o)));

    const d = $("#done");
    d.innerHTML = "";
    $("#doneCount").textContent = done.length ? `${done.length} today` : "";
    if (!done.length) {
      d.innerHTML = `<p class="muted small" style="margin:0">Nothing served yet.</p>`;
    } else done.slice(0, 6).forEach((o) => d.appendChild(ticket(o)));

    renderRoom(s);
    firstPaint = false;
  } catch (e) { console.error(e); }
}

$("#refreshDigest").onclick = loadDigest;
tick();
loadDigest();
setInterval(tick, 2000);
