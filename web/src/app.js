// The web app: log in, then read and write MeshCore channels through pyMeshRoom's KaaS API.
// The private key and channel secrets never leave the browser; the server only ever sees encrypted packets.
import { bytesToHex, randomBytes, utf8Encode } from "./bytes.js";
import { findGifs, gifFallbackUrl, gifPageUrl, gifUrl } from "./gif.js";
import { AuthError, KaasClient, kaasEndpoints } from "./kaas.js";
import {
  Identity, MAX_NAME_LEN, PT_ADVERT, PT_GRP_TXT, advertPayload, channelMessagePacket, floodPacket, makeChannel,
  packetHash, parsePacket, readChannelMessage, textBudget, zeroHopPacket,
} from "./meshcore.js";
import {
  forgetEverything, loadChannels, loadHistory, loadSettings, saveChannels, saveHistory, saveSettings,
} from "./store.js";

const HISTORY_PER_CHANNEL = 300;
const SEEN_MAX = 4096;
const $ = (id) => document.getElementById(id);

const app = {
  identity: null,
  name: "",
  client: null,
  channels: [],                 // makeChannel() objects, Public first
  active: null,                 // keyHex of the channel on screen
  history: {},                  // keyHex -> [message]
  unread: {},                   // keyHex -> count
  seen: new Set(),              // packet hashes already handled (floods arrive once per repeater)
  lastTs: 0,
  saveTimer: null,
};

// ---------------------------------------------------------------- small DOM helpers

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) e.append(c);
  return e;
}

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

function showError(id, text) {
  $(id).textContent = text || "";
  $(id).hidden = !text;
}

// ---------------------------------------------------------------- login

// Served by pyMeshRoom itself (at .../meshcore-web/): the server is the one this page came from, behind whatever
// proxy path it sits on. Otherwise (a file on disk, another host) null, and the URL is typed in.
function servedBy() {
  if (!/^https?:$/.test(location.protocol)) return null;
  const m = location.pathname.match(/^(.*?)\/meshcore-web(?:\/[^/]*)?$/);
  return m ? location.origin + m[1] : null;
}

function initLogin() {
  const s = loadSettings();
  const served = servedBy();
  $("f-url").value = served && !s.otherServer ? served : s.url || "";
  $("url-box").hidden = !!served && !s.otherServer;
  $("url-auto").hidden = !served || !!s.otherServer;
  $("url-host").textContent = served ? new URL(served).host : "";
  $("f-name").value = s.name || "";
  $("f-remember").checked = !!s.remember;
  if (s.remember) {
    $("f-key").value = s.apiKey || "";
    $("f-priv").value = s.privateKey || "";
  }
  $("key-box").open = !!$("f-key").value;
  updatePubPreview();
  $("login").hidden = false;
  const empty = ["f-url", "f-priv", "f-name"].map($).find((i) => !i.value && !i.closest("[hidden]"));
  (empty || $("login-go")).focus();
}

let previewTimer;
function updatePubPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const v = $("f-priv").value.trim();
    const hint = $("f-pub");
    if (!v) {
      hint.textContent = "Exported from a MeshCore app, or press New to make one. Keep a copy: a new key is a new node.";
      return;
    }
    try {
      hint.textContent = "Public key: " + new Identity(v).pubHex;
      hint.classList.add("mono");
    } catch (e) {
      hint.textContent = e.message === "not hex" ? "That isn't hex." : e.message;
      hint.classList.remove("mono");
    }
  }, 150);
}

async function login(ev) {
  ev.preventDefault();
  showError("login-error", "");
  const url = $("f-url").value.trim();
  const apiKey = $("key-box").open ? $("f-key").value.trim() : "";
  const priv = $("f-priv").value.trim();
  const name = $("f-name").value.trim();
  let identity;
  try {
    const ep = kaasEndpoints(url);
    if (location.protocol === "https:" && !ep.secure) throw new Error("This page is on HTTPS, so the server must be too (https://).");
    if (apiKey && !apiKey.startsWith("kaas_")) throw new Error("An API key starts with kaas_.");
    identity = new Identity(priv);
    if (!name) throw new Error("Enter your name.");
    if (utf8Encode(name).length > MAX_NAME_LEN) throw new Error("Your name is at most " + MAX_NAME_LEN + " bytes.");
  } catch (e) {
    return showError("login-error", e.message === "not hex" ? "The private key isn't hex." : e.message);
  }
  const remember = $("f-remember").checked;
  const otherServer = !$("url-box").hidden && !!servedBy();
  const prefs = { url, name, remember, otherServer, gifs: loadSettings().gifs };
  saveSettings(remember ? { ...prefs, apiKey: apiKey || undefined, privateKey: identity.privateHex } : prefs);

  const go = $("login-go");
  go.disabled = true;
  go.textContent = "Connecting…";
  const client = new KaasClient(url, apiKey ? { apiKey } : { identity, name });
  try {
    await client.start();
  } catch (e) {
    client.stop();
    go.disabled = false;
    go.textContent = "Connect";
    return showError("login-error", e instanceof AuthError ? "The server refused the login: " + e.message : e.message);
  }
  go.disabled = false;
  go.textContent = "Connect";
  $("f-priv").value = remember ? $("f-priv").value : "";
  $("f-key").value = remember ? $("f-key").value : "";
  startChat(identity, name, client);
}

// ---------------------------------------------------------------- chat session

function startChat(identity, name, client) {
  Object.assign(app, { identity, name, client, unread: {}, seen: new Set() });
  const saved = loadChannels(identity.pubHex) || [];
  app.channels = [makeChannel("Public", "public")];
  for (const c of saved) {
    if (c.kind === "public") continue;
    try {
      if (!app.channels.some((x) => x.keyHex === c.keyHex)) app.channels.push(makeChannel(c.name, c.kind, c.keyHex));
    } catch {
      // a damaged entry: skip it
    }
  }
  app.history = loadHistory(identity.pubHex) || {};
  for (const msgs of Object.values(app.history))
    for (const m of msgs) if (m.mine && (m.status === "sending" || m.status === "waiting")) m.status = "unknown";
  app.active = app.channels[0].keyHex;

  client.addEventListener("packet", (e) => onPacket(e.detail));
  client.addEventListener("state", (e) => onState(e.detail));
  client.addEventListener("hello", () => renderServer());
  client.addEventListener("dropped", (e) => toast("The server skipped " + e.detail + " packets (this tab fell behind)."));
  client.addEventListener("access", (e) => {
    toast(e.detail.send ? "Your key was heard on the mesh: you can send now." : "Sending was turned off for your key.");
    onState({ state: client.state });
  });

  $("me-name").textContent = name;
  $("me-key").textContent = identity.pubHex.slice(0, 8);
  $("me-key").title = identity.pubHex;
  $("login").hidden = true;
  $("chat").hidden = false;
  onState({ state: client.state });
  renderServer();
  renderChannels();
  renderRoom();
  $("text").focus();
}

function logout() {
  app.client?.stop();
  flushSave();
  const s = loadSettings();
  if (!s.remember) saveSettings({ url: s.url, name: s.name, remember: false, otherServer: s.otherServer, gifs: s.gifs });
  Object.assign(app, { identity: null, client: null, channels: [], history: {}, unread: {}, active: null });
  $("chat").hidden = true;
  $("messages").replaceChildren();
  document.title = "MeshCore Web";
  initLogin();
}

function persistChannels() {
  saveChannels(app.identity.pubHex, app.channels.map((c) => ({ name: c.name, kind: c.kind, keyHex: c.keyHex })));
}

function scheduleSave() {
  clearTimeout(app.saveTimer);
  app.saveTimer = setTimeout(flushSave, 500);
}

function flushSave() {
  clearTimeout(app.saveTimer);
  if (!app.identity) return;
  for (const k of Object.keys(app.history)) app.history[k] = app.history[k].slice(-HISTORY_PER_CHANNEL);
  saveHistory(app.identity.pubHex, app.history);
}

function markSeen(hash) {
  if (app.seen.has(hash)) return true;
  app.seen.add(hash);
  if (app.seen.size > SEEN_MAX) app.seen.delete(app.seen.values().next().value);
  return false;
}

function uniqueTime() {
  app.lastTs = Math.max(Math.floor(Date.now() / 1000), app.lastTs + 1);
  return app.lastTs;
}

// ---------------------------------------------------------------- receiving

function onPacket({ dir, raw, snr }) {
  const pkt = parsePacket(raw);
  if (!pkt || pkt.ptype !== PT_GRP_TXT) return;
  if (markSeen(packetHash(pkt))) return;                     // another copy, or our own packet going out
  const m = readChannelMessage(app.channels, pkt.payload);
  if (!m) return;
  const msgs = (app.history[m.channel.keyHex] ||= []);
  if (msgs.some((x) => x.ts === m.ts && x.sender === m.sender && x.text === m.text)) return;   // (resent)
  const msg = {
    id: crypto.randomUUID?.() || bytesToHex(randomBytes(8)), ts: m.ts, at: Date.now(), sender: m.sender, text: m.text,
    hops: pkt.isFlood ? pkt.hops : null, snr: dir === "rx" ? snr : null, dir,
  };
  msgs.push(msg);
  scheduleSave();
  if (m.channel.keyHex === app.active && !document.hidden) {
    appendMessage(msg);
  } else {
    app.unread[m.channel.keyHex] = (app.unread[m.channel.keyHex] || 0) + 1;
    if (m.channel.keyHex === app.active) appendMessage(msg);
    renderChannels();
  }
  updateTitle();
}

function onState({ state, reason }) {
  const dot = $("conn-dot");
  dot.className = "dot " + state;
  const label = { open: "Connected", connecting: "Connecting…", closed: "Disconnected", failed: "Stopped" }[state];
  dot.title = label + (reason ? ": " + reason : "");
  const banner = $("banner");
  const key = app.client?.hello?.key || {};
  let text = "", bad = false;
  if (state === "failed") { text = "Disconnected: " + reason + ". Log out and in again with a working key."; bad = true; }
  else if (state === "closed") text = "Disconnected: " + (reason || "connection lost") + ".";
  else if (state === "connecting") text = "Connecting…";
  else if (key.receive === false) text = "This API key can't receive, so you won't see messages. Ask for a key with Receive.";
  else if (key.web && !key.send) text = "You can read, but not send yet: this radio hasn't heard your key. Send an advert " +
    "from your MeshCore app within range of the radio, or ask the server's admin to allow your key. This updates by itself.";
  else if (key.send === false) text = "This API key can't send. You can read, but not post or advertise.";
  banner.textContent = text;
  banner.className = "banner" + (bad ? " bad" : "");
  banner.hidden = !text;
  updateComposer();
}

// ---------------------------------------------------------------- sending

async function sendMessage(ev) {
  ev?.preventDefault();
  const ta = $("text");
  const text = ta.value.trim();
  if (!text || !canSend()) return;
  if (utf8Encode(text).length > textBudget(app.name)) return toast("Too long for one message.");
  const ch = app.channels.find((c) => c.keyHex === app.active);
  ta.value = "";
  autosize();
  updateComposer();
  updateGifPreview();
  const msg = {
    id: crypto.randomUUID?.() || bytesToHex(randomBytes(8)), ts: uniqueTime(), at: Date.now(), sender: app.name, text,
    mine: true, status: "sending",
  };
  (app.history[ch.keyHex] ||= []).push(msg);
  appendMessage(msg);
  await transmit(ch, msg);
}

async function transmit(ch, msg) {
  const raw = channelMessagePacket(ch, app.name, msg.text, msg.ts);
  markSeen(packetHash(parsePacket(raw)));
  msg.status = app.client.backlog || app.client.state !== "open" ? "waiting" : "sending";
  updateMessage(msg);
  const res = await app.client.send(raw);
  msg.status = res.status === "sent" || res.status === "local" ? res.status : "failed";
  msg.reason = res.reason || "";
  updateMessage(msg);
  scheduleSave();
}

async function retry(msg) {
  const ch = app.channels.find((c) => (app.history[c.keyHex] || []).includes(msg));
  if (!ch || !canSend()) return;
  msg.ts = uniqueTime();
  msg.status = "sending";
  await transmit(ch, msg);
}

async function advertise(kind) {
  $("dlg-advert").close();
  if (!canSend()) return toast("Not connected, or this key can't send.");
  const payload = advertPayload(app.identity, app.name, uniqueTime());
  const raw = kind === "flood" ? floodPacket(PT_ADVERT, payload) : zeroHopPacket(PT_ADVERT, payload);
  markSeen(packetHash(parsePacket(raw)));
  toast("Sending advert…");
  const res = await app.client.send(raw);
  toast(res.status === "sent" || res.status === "local" ? "Advert sent." : "Advert not sent: " + (res.reason || res.status));
}

function canSend() {
  return app.client && app.client.state === "open" && app.client.hello?.key?.send !== false;
}

// ---------------------------------------------------------------- rendering

function renderServer() {
  const h = app.client?.hello;
  const parts = [];
  if (h) {
    parts.push(h.server || "pyMeshRoom");
    const room = h.nodes?.room && h.names?.[h.nodes.room];
    if (room) parts.push(room);
    if (h.radio) parts.push((h.radio.freq_hz / 1e6).toFixed(3) + " MHz, SF" + h.radio.sf + ", " + h.radio.bw_hz / 1000 + " kHz");
  }
  $("server-info").textContent = parts.join(" · ");
}

function renderChannels() {
  const list = $("channel-list");
  list.replaceChildren(...app.channels.map((ch) => {
    const n = app.unread[ch.keyHex] || 0;
    return el("button", {
      type: "button", class: ch.keyHex === app.active ? "on" : "", onclick: () => selectChannel(ch.keyHex),
      "aria-current": ch.keyHex === app.active ? "true" : null,
    },
    el("span", { class: "ch-name" }, ch.name),
    ch.kind === "private" ? el("span", { class: "ch-kind" }, "private") : null,
    n ? el("span", { class: "badge" }, String(n)) : null);
  }));
}

function selectChannel(keyHex) {
  app.active = keyHex;
  delete app.unread[keyHex];
  $("sidebar").classList.remove("open");
  renderChannels();
  renderRoom();
  updateTitle();
  $("text").focus();
}

function renderRoom() {
  const ch = app.channels.find((c) => c.keyHex === app.active);
  $("room-name").textContent = ch.name;
  $("room-sub").textContent = {
    public: "The public channel every MeshCore node knows",
    hashtag: "Hashtag channel: anyone who knows the name can read it",
    private: "Private channel: only people with its secret can read it",
  }[ch.kind];
  const box = $("messages");
  const msgs = app.history[ch.keyHex] || [];
  box.replaceChildren();
  if (!msgs.length) {
    box.append(el("p", { class: "empty" }, "No messages yet. Messages heard from now on show up here; the radio doesn't keep a backlog."));
  }
  lastDay = null;
  for (const m of msgs) appendMessage(m, false);
  box.scrollTop = box.scrollHeight;
  updateComposer();
}

let lastDay = null;

function shownTime(m) {
  // the sender's clock, unless it is far off from when we got it
  const sent = m.ts * 1000;
  return Math.abs(sent - m.at) > 12 * 3600 * 1000 ? m.at : sent;
}

function appendMessage(m, scroll = true) {
  const box = $("messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.querySelector(".empty")?.remove();
  const when = new Date(shownTime(m));
  const day = when.toDateString();
  if (day !== lastDay) {
    lastDay = day;
    box.append(el("div", { class: "day" }, when.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })));
  }
  box.append(messageNode(m));
  if (scroll && (nearBottom || m.mine)) box.scrollTop = box.scrollHeight;
}

function messageNode(m) {
  const when = new Date(shownTime(m));
  const route = [];
  if (m.hops != null) route.push(m.hops === 0 ? "direct" : m.hops + (m.hops === 1 ? " hop" : " hops"));
  if (m.snr != null) route.push("SNR " + Number(m.snr).toFixed(1));
  if (m.dir === "tx" && !m.mine) route.push("via this radio");
  return el("div", { class: "msg" + (m.mine ? " mine" : ""), dataset: { id: m.id } },
    el("div", { class: "meta" },
      el("span", { class: "sender" }, m.sender || "(no name)"),
      el("time", { datetime: when.toISOString(), title: when.toLocaleString() },
        when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })),
      route.length ? el("span", {}, route.join(" · ")) : null),
    el("div", { class: "text" }, ...richText(m.text)),
    m.mine ? statusNode(m) : null);
}

function statusNode(m) {
  const text = {
    sending: "Sending…", waiting: "Waiting for the rate limit…", sent: "Sent", local: "Delivered on the radio",
    failed: "Not sent" + (m.reason ? ": " + m.reason : ""), unknown: "Not confirmed",
  }[m.status] || m.status;
  const bad = m.status === "failed" || m.status === "unknown";
  return el("div", { class: "status" + (bad ? " bad" : "") }, text,
    bad ? el("button", { type: "button", class: "ghost", onclick: () => retry(m) }, "Retry") : null);
}

function updateMessage(m) {
  const node = $("messages").querySelector(`[data-id="${CSS.escape(m.id)}"]`);
  if (node) node.replaceWith(messageNode(m));
}

// A message's text: Giphy links become the GIF (unless GIFs are off), @[name] mentions stand out.
function richText(text) {
  const out = [];
  let i = 0;
  for (const g of findGifs(text)) {
    if (g.start > i) out.push(...withMentions(text.slice(i, g.start)));
    out.push(gifsOn() ? gifNode(g.id) : el("span", { class: "gif-link" }, text.slice(g.start, g.end)));
    i = g.end;
  }
  if (i < text.length) out.push(...withMentions(text.slice(i)));
  return out;
}

function gifNode(id) {
  const img = el("img", { src: gifUrl(id), alt: "GIF", loading: "lazy", referrerpolicy: "no-referrer" });
  img.addEventListener("error", () => {
    if (img.src !== gifFallbackUrl(id)) img.src = gifFallbackUrl(id);
    else img.replaceWith(el("span", { class: "gif-link" }, gifPageUrl(id)));
  });
  return el("a", { class: "gif", href: gifPageUrl(id), target: "_blank", rel: "noopener noreferrer", title: "Open on Giphy" }, img);
}

function gifsOn() {
  return loadSettings().gifs !== false;
}

function updateGifPreview() {
  const box = $("gif-preview");
  const gifs = gifsOn() ? findGifs($("text").value) : [];
  box.replaceChildren(...gifs.slice(0, 3).map((g) => el("img", { src: gifUrl(g.id), alt: "GIF preview", referrerpolicy: "no-referrer" })));
  box.hidden = !gifs.length;
}

// @[name] mentions, as the MeshCore apps write them; the reader's own name stands out.
function withMentions(text) {
  const out = [];
  const re = /@\[([^\]]+)\]/g;
  let i = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > i) out.push(text.slice(i, m.index));
    const me = m[1].toLowerCase() === app.name.toLowerCase();
    out.push(el("span", { class: "mention" + (me ? " me" : "") }, "@" + m[1]));
    i = m.index + m[0].length;
  }
  if (i < text.length) out.push(text.slice(i));
  return out;
}

function updateComposer() {
  if (!app.identity) return;
  const left = textBudget(app.name) - utf8Encode($("text").value.trim()).length;
  const count = $("count");
  count.textContent = String(left);
  count.className = "count" + (left < 0 ? " over" : "");
  const ok = canSend();
  $("send").disabled = !ok || left < 0 || !$("text").value.trim();
  $("text").placeholder = ok ? "Message " + ($("room-name").textContent || "") : "Can't send right now";
  $("advert").disabled = !ok;
}

function updateTitle() {
  const n = Object.values(app.unread).reduce((a, b) => a + b, 0);
  document.title = (n ? "(" + n + ") " : "") + "MeshCore Web";
}

function autosize() {
  const ta = $("text");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
}

// ---------------------------------------------------------------- channels: join, details, leave

let addKind = "hashtag";

function openAdd() {
  setAddKind("hashtag");
  $("a-tag").value = "";
  $("a-name").value = "";
  $("a-secret").value = "";
  showError("add-error", "");
  $("dlg-add").showModal();
  $("a-tag").focus();
}

function setAddKind(kind) {
  addKind = kind;
  for (const t of document.querySelectorAll("#dlg-add .tab")) t.classList.toggle("on", t.dataset.kind === kind);
  for (const p of document.querySelectorAll("#dlg-add [data-pane]")) p.hidden = p.dataset.pane !== kind;
}

function addChannel(ev) {
  ev.preventDefault();
  let ch;
  try {
    if (addKind === "hashtag") {
      let tag = $("a-tag").value.trim().replace(/\s+/g, "");
      if (!tag.replace(/^#/, "")) throw new Error("Enter a hashtag.");
      if (!tag.startsWith("#")) tag = "#" + tag;
      ch = makeChannel(tag, "hashtag");
    } else {
      const name = $("a-name").value.trim();
      if (!name) throw new Error("Give the channel a name.");
      ch = makeChannel(name, "private", parseSecret($("a-secret").value));
    }
  } catch (e) {
    return showError("add-error", e.message === "not hex" ? "The secret isn't hex." : e.message);
  }
  const existing = app.channels.find((c) => c.keyHex === ch.keyHex);
  $("dlg-add").close();
  if (existing) {
    toast("You're already in " + existing.name + ".");
    return selectChannel(existing.keyHex);
  }
  app.channels.push(ch);
  persistChannels();
  selectChannel(ch.keyHex);
}

// 32 hex characters, or base64 of 16 bytes
function parseSecret(s) {
  s = s.trim();
  if (/^[0-9a-f\s]+$/i.test(s)) return s.replace(/\s+/g, "");
  try {
    const bin = atob(s);
    if (bin.length === 16) return bytesToHex(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
  } catch {
    // not base64 either
  }
  throw new Error("A secret is 32 hex characters.");
}

function openInfo() {
  const ch = app.channels.find((c) => c.keyHex === app.active);
  $("i-name").textContent = ch.name;
  $("i-kind").textContent = $("room-sub").textContent + ".";
  $("i-secret-box").hidden = ch.kind !== "private";
  $("i-secret").value = ch.kind === "private" ? ch.keyHex : "";
  $("i-leave").hidden = ch.kind === "public";
  $("dlg-info").showModal();
}

function leaveChannel() {
  const ch = app.channels.find((c) => c.keyHex === app.active);
  if (!ch || ch.kind === "public") return;
  if (!confirm("Leave " + ch.name + "? Its messages on this device are deleted too.")) return;
  app.channels = app.channels.filter((c) => c !== ch);
  delete app.history[ch.keyHex];
  delete app.unread[ch.keyHex];
  persistChannels();
  flushSave();
  $("dlg-info").close();
  selectChannel(app.channels[0].keyHex);
}

function clearHistory() {
  app.history[app.active] = [];
  flushSave();
  $("dlg-info").close();
  renderRoom();
}

// ---------------------------------------------------------------- wiring

function wire() {
  $("login-form").addEventListener("submit", login);
  $("f-priv").addEventListener("input", updatePubPreview);
  $("f-priv-gen").addEventListener("click", () => {
    if ($("f-priv").value.trim() && !confirm("Replace this private key with a new one? A new key is a new node.")) return;
    $("f-priv").value = Identity.generate().privateHex;
    $("f-priv").type = "text";
    $("f-priv-show").textContent = "Hide";
    updatePubPreview();
  });
  $("f-priv-show").addEventListener("click", () => {
    const shown = $("f-priv").type === "text";
    $("f-priv").type = shown ? "password" : "text";
    $("f-priv-show").textContent = shown ? "Show" : "Hide";
  });

  $("url-change").addEventListener("click", () => {
    $("url-auto").hidden = true;
    $("url-box").hidden = false;
    $("f-url").value = "";
    $("f-url").focus();
  });
  $("show-gifs").checked = gifsOn();
  $("show-gifs").addEventListener("change", () => {
    saveSettings({ ...loadSettings(), gifs: $("show-gifs").checked });
    if (app.identity) renderRoom();
    updateGifPreview();
  });
  $("composer").addEventListener("submit", sendMessage);
  $("text").addEventListener("input", () => { autosize(); updateComposer(); updateGifPreview(); });
  $("text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) sendMessage(e);
  });
  $("add-channel").addEventListener("click", openAdd);
  $("add-form").addEventListener("submit", addChannel);
  for (const t of document.querySelectorAll("#dlg-add .tab")) t.addEventListener("click", () => setAddKind(t.dataset.kind));
  $("a-gen").addEventListener("click", () => { $("a-secret").value = bytesToHex(randomBytes(16)); });
  $("channel-info").addEventListener("click", openInfo);
  $("i-leave").addEventListener("click", leaveChannel);
  $("i-clear").addEventListener("click", clearHistory);
  $("i-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("i-secret").value);
      toast("Secret copied.");
    } catch {
      $("i-secret").select();
    }
  });
  $("advert").addEventListener("click", () => $("dlg-advert").showModal());
  for (const b of document.querySelectorAll("[data-advert]")) b.addEventListener("click", () => advertise(b.dataset.advert));
  for (const b of document.querySelectorAll("[data-close]")) b.addEventListener("click", () => b.closest("dialog").close());
  $("logout").addEventListener("click", () => {
    const s = loadSettings();
    const forget = s.remember && confirm("Also forget the saved API key and private key on this device?");
    if (forget) forgetEverything();
    logout();
  });
  $("toggle-side").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && app.active && app.unread[app.active]) {
      delete app.unread[app.active];
      renderChannels();
      updateTitle();
    }
  });
  window.addEventListener("pagehide", flushSave);
}

wire();
initLogin();
