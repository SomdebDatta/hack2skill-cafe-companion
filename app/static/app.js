// Customer surface: chat → cart → order → live ETA → feedback.
const $ = (s) => document.querySelector(s);
const api = async (url, body) => {
  const r = await fetch(url, body ? {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : {});
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};
const money = (n) => "£" + n.toFixed(2);

let cid = "somdeb";
let rating = null;
let sentFeedback = false;
let lastStatus = null;

const SUGGESTIONS = [
  "Something warm, not too sweet — I'm in a rush",
  "The usual please",
  "What's quickest right now?",
  "Something vegan and filling",
];

// ------------------------------------------------------------------ chat
function bubble(role, text, trace) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const label = document.createElement("div");
  label.className = "who-label";
  label.textContent = role === "me" ? "You" : "Barista";
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  wrap.append(label, b);
  if (trace?.length) {
    const t = document.createElement("div");
    t.className = "trace";
    trace.forEach((c) => {
      const code = document.createElement("code");
      code.textContent = c.tool;
      code.title = JSON.stringify(c.args);
      t.appendChild(code);
    });
    wrap.appendChild(t);
  }
  $("#chat").appendChild(wrap);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return wrap;
}

function typing() {
  const w = document.createElement("div");
  w.className = "msg bot";
  w.innerHTML = `<div class="who-label">Barista</div>
    <div class="bubble typing"><i></i><i></i><i></i></div>`;
  $("#chat").appendChild(w);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return w;
}

function greet(name, visits) {
  $("#chat").innerHTML = "";
  const e = document.createElement("div");
  e.className = "empty";
  e.innerHTML = visits
    ? `<strong>Morning, ${name}.</strong>You've been in ${visits} times — ask for “the usual”, or tell me what you're after.`
    : `<strong>First time? Welcome.</strong>Tell me what you feel like and I'll find it.`;
  $("#chat").appendChild(e);
}

async function send(text) {
  if (!text.trim()) return;
  $("#chat").querySelector(".empty")?.remove();
  bubble("me", text);
  $("#input").value = "";
  $("#send").disabled = true;
  const t = typing();
  try {
    const r = await api("/api/chat", { customer_id: cid, message: text });
    t.remove();
    bubble("bot", r.reply, r.trace);
    renderCart(r.cart);
    refresh();
  } catch (e) {
    t.remove();
    bubble("bot", "Something went wrong reaching the kitchen — try that again?");
    console.error(e);
  } finally {
    $("#send").disabled = false;
    $("#input").focus();
  }
}

// ------------------------------------------------------------------ cart
function renderCart(cart) {
  const box = $("#cart");
  $("#cartCount").textContent = cart.count ? `${cart.count} item${cart.count > 1 ? "s" : ""}` : "";
  if (!cart.items.length) {
    box.innerHTML = `<p class="muted small" style="margin:4px 0 0">Nothing yet — ask the barista.</p>`;
    $("#placeBtn").disabled = true;
    return;
  }
  box.innerHTML = "";
  cart.items.forEach((l) => {
    const row = document.createElement("div");
    row.className = "line";
    row.innerHTML = `<span class="qty">${l.qty}×</span><span class="nm">${l.name}</span>
      <span class="pr">${money(l.price * l.qty)}</span>`;
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "×"; x.title = "Remove";
    x.onclick = async () => renderCart((await api("/api/cart/remove",
      { customer_id: cid, item_id: l.item_id })).cart);
    row.appendChild(x);
    box.appendChild(row);
  });
  const tot = document.createElement("div");
  tot.className = "cart-total";
  tot.innerHTML = `<span>Total</span><span>${money(cart.total)}</span>`;
  box.appendChild(tot);
  $("#placeBtn").disabled = false;
}

// ----------------------------------------------------------------- order
const STAGES = ["queued", "preparing", "ready", "collected"];
const STAGE_LABEL = { queued: "In queue", preparing: "Making", ready: "Ready", collected: "Collected" };

function renderOrder(data) {
  const o = data.order;
  if (!o) { $("#orderPanel").hidden = true; return; }
  $("#orderPanel").hidden = false;
  $("#orderId").textContent = o.id;

  const i = STAGES.indexOf(o.status);
  $("#statusTrack").innerHTML = STAGES.map((s, n) =>
    `<span class="${n === i ? "on" : n < i ? "done" : ""}">${STAGE_LABEL[s]}</span>`).join("");

  if (o.status === "ready" || o.status === "collected") {
    $("#etaNum").textContent = "✓";
    $(".eta-unit").hidden = true;
    $("#etaWhy").innerHTML = `<span class="ready-note">Ready at the counter.</span>`;
    $("#feedbackBox").hidden = sentFeedback;
  } else {
    $("#etaNum").textContent = o.eta_minutes;
    $(".eta-unit").hidden = false;
    const b = o.eta_breakdown;
    $("#etaWhy").textContent = b
      ? `You're ${ordinal(data.position)} in the queue. ${b.queue_ahead} order${b.queue_ahead === 1 ? "" : "s"} ahead across ${b.stations} baristas — it's ${b.rush_label} right now.`
      : `You're ${ordinal(data.position)} in the queue.`;
  }

  if (lastStatus && lastStatus !== o.status && o.status === "ready") {
    bubble("bot", `Order ${o.id} is ready at the counter — come grab it.`);
  }
  lastStatus = o.status;
}
const ordinal = (n) => n + (["th", "st", "nd", "rd"][(n % 100 - 20) % 10] || ["th", "st", "nd", "rd"][n] || "th");

// ----------------------------------------------------------------- spark
function renderSpark(bt) {
  const peak = Math.max(...bt.hours.map((h) => h.orders)) || 1;
  const quiet = new Set(bt.quietest_upcoming.map((h) => h.hour));
  $("#spark").innerHTML = bt.hours.map((h) => {
    const cls = h.hour === bt.current_hour ? "now" : quiet.has(h.hour) ? "quiet" : "";
    const lbl = h.hour % 3 === 1 ? `<span>${h.hour}</span>` : "";
    return `<div class="${cls}" style="height:${Math.max(8, (h.orders / peak) * 100)}%"
       title="${h.hour}:00 — ${h.orders} orders">${lbl}</div>`;
  }).join("");
  const q = bt.quietest_upcoming[0];
  $("#sparkNote").textContent = q
    ? `Quietest from here: ${q.hour}:00. Orange is now, green is calm.`
    : `That's the day done — tomorrow opens at 7:00.`;
}

// --------------------------------------------------------------- polling
async function refresh() {
  try {
    const [ord, room] = await Promise.all([api(`/api/order/${cid}`), api("/api/room")]);
    renderOrder(ord);
    renderSpark(room.best_times);
    $("#rush").textContent = `${room.queue_depth} in the queue · ${room.rush.label}`;
    $("#rush").dataset.level = room.rush.label === "quiet" ? "quiet" : "busy";
  } catch (e) { console.error(e); }
}

// ------------------------------------------------------------------ init
async function switchUser(id, name, visits) {
  cid = id; rating = null; sentFeedback = false; lastStatus = null;
  $("#feedbackBox").hidden = true;
  $("#fbThanks").hidden = true;
  $("#fbComment").value = "";
  document.querySelectorAll(".fb").forEach((b) => b.classList.remove("sel"));
  greet(name, visits);
  renderCart(await api(`/api/cart/${id}`));
  refresh();
}

(async function init() {
  const boot = await api("/api/bootstrap");
  $("#modelChip").textContent = boot.gemini ? boot.model : "demo mode — no key";

  const sel = $("#who");
  sel.innerHTML = boot.customers.map((c) =>
    `<option value="${c.id}">${c.name}${c.visits ? ` · ${c.visits} visits` : " · new"}</option>`).join("");
  sel.onchange = () => {
    const c = boot.customers.find((x) => x.id === sel.value);
    switchUser(c.id, c.name, c.visits);
  };

  $("#suggestions").innerHTML = SUGGESTIONS.map((s) =>
    `<button type="button">${s}</button>`).join("");
  $("#suggestions").onclick = (e) => {
    if (e.target.tagName === "BUTTON") send(e.target.textContent);
  };

  $("#composer").onsubmit = (e) => { e.preventDefault(); send($("#input").value); };

  $("#placeBtn").onclick = async () => {
    $("#placeBtn").disabled = true;
    const r = await api("/api/order", { customer_id: cid });
    bubble("bot", `That's in — order ${r.order_id}, about ${r.eta_minutes} minutes. You're ${ordinal(r.position)} in the queue.`);
    renderCart({ items: [], total: 0, count: 0 });
    refresh();
  };

  document.querySelectorAll(".fb").forEach((b) => {
    b.onclick = () => {
      rating = b.dataset.rating;
      document.querySelectorAll(".fb").forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
      maybeSendFeedback();
    };
  });
  $("#fbComment").addEventListener("keydown", (e) => {
    if (e.key === "Enter") maybeSendFeedback();
  });

  const first = boot.customers[0];
  switchUser(first.id, first.name, first.visits);
  setInterval(refresh, 2000);
})();

async function maybeSendFeedback() {
  if (!rating || sentFeedback) return;
  const comment = $("#fbComment").value.trim();
  // Give them a beat to type before sending on the rating alone.
  if (!comment && !$("#fbComment").dataset.touched) {
    $("#fbComment").dataset.touched = "1";
    $("#fbComment").focus();
    return;
  }
  sentFeedback = true;
  await api("/api/feedback", { customer_id: cid, rating, comment, item_id: null });
  $("#fbThanks").hidden = false;
  $("#fbComment").disabled = true;
}
