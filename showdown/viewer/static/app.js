// Commander AI Showdown viewer.
//
// How it works:
//   1. Python runs the game and records a "timeline": a list of events
//      (see showdown/viewer/events.py). Some events carry a full snapshot of
//      the table ("state").
//   2. This page downloads the timeline bit by bit (polling /api/game/events).
//   3. A "playhead" walks through the timeline at the chosen speed. To draw
//      the table at the playhead we use the latest snapshot at or before it.
// Because playback only reads the recorded timeline, pause / rewind / speed
// never disturb the game itself.

"use strict";

// ---------------------------------------------------------------------------
// Event styles. Add an entry here when you add a new event kind in events.py.
//   cls:    CSS class for the log line
//   banner: also flash the text in the middle of the screen
//   delay:  how long playback lingers on this event at 1x speed (ms)
// ---------------------------------------------------------------------------
const EVENT_STYLES = {
  game_start:   { cls: "turn", banner: true, delay: 1200 },
  log:          { cls: "", delay: 450 },
  bot_choice:   { cls: "bot_choice", delay: 500 },
  game_over:    { cls: "game_over", banner: true, delay: 0 },
  error:        { cls: "error", banner: true, delay: 0 },
  deal_offer:   { cls: "deal", banner: true, delay: 1500 },
  deal_accept:  { cls: "deal", banner: true, delay: 1500 },
  deal_decline: { cls: "deal", delay: 1000 },
  deal_break:   { cls: "deal", banner: true, delay: 2000 },
};
// Forge log categories that get their own pacing.
const CATEGORY_DELAY = { TURN: 1000, PHASE: 120, MANA: 40, STATE: 150, COMBAT: 700, DAMAGE: 600 };
const NOISY = new Set(["PHASE", "MANA", "STATE"]); // hidden from the log unless the box is ticked
const SPEEDS = [0.5, 1, 2, 4, 10, Infinity];      // Infinity = "Max": jump straight to the latest event
const PHASE_NAMES = {
  UNTAP: "Untap", UPKEEP: "Upkeep", DRAW: "Draw", MAIN1: "Main 1",
  COMBAT_BEGIN: "Beginning of combat", COMBAT_DECLARE_ATTACKERS: "Declare attackers",
  COMBAT_DECLARE_BLOCKERS: "Declare blockers", COMBAT_FIRST_STRIKE_DAMAGE: "First strike damage",
  COMBAT_DAMAGE: "Combat damage", COMBAT_END: "End of combat", MAIN2: "Main 2",
  END_OF_TURN: "End step", CLEANUP: "Cleanup",
};

// ---------------------------------------------------------------------------
// Everything the page remembers, in one place.
// ---------------------------------------------------------------------------
const app = {
  events: [],        // the timeline downloaded so far
  stateSeqs: [],     // positions of events that carry a "state", in order
  status: "none",    // the game's status on the server
  config: null,      // decks/bots of the current game
  seats: [],         // from the game_start event
  pos: 0,            // playhead: how many events are "visible" (events[0 .. pos-1])
  playing: true,
  speed: 1,
  timer: null,
  shownStateSeq: -1, // which snapshot is drawn right now
  lastLife: {},      // to flash life changes
  knownCards: new Set(), // card ids drawn before, to animate new arrivals
  followLog: true,   // keep the log scrolled to the newest line (off while you scroll up)
  stepping: false,   // true while playback moves forward one event (not when jumping)
  shownStack: [],    // the stack drawn right now, to spot newly cast spells
  holdUntil: 0,      // playback waits until this time (performance.now()) for an animation
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const api = async (path, body) => {
  const options = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
};

// ===========================================================================
// Setup screen
// ===========================================================================
async function showSetup() {
  $("#setup").classList.remove("hidden");
  $("#game").classList.add("hidden");
  const { decks, bots } = await api("/api/decks");
  const defaults = ["zidane", "anhelo", "nelly", "kuja"];
  const previous = app.config || { decks: defaults, bots: ["simple", "simple", "simple", "simple"] };
  const seats = $("#seats");
  seats.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    const row = el("div", "seat-row");
    const label = el("span", "", `Seat ${i + 1}`);
    const dot = el("span", "dot");
    dot.style.background = `var(--seat-${i})`;
    label.prepend(dot);
    const deckSel = el("select");
    deckSel.dataset.seat = i;
    if (i >= 2) deckSel.append(new Option("(empty seat)", ""));
    for (const d of decks) deckSel.append(new Option(d, d));
    deckSel.value = previous.decks[i] ?? (i >= 2 ? "" : decks[i % decks.length]);
    const botSel = el("select");
    for (const b of bots) botSel.append(new Option(botLabel(b), b));
    botSel.append(new Option("New Boy…", NEW_BOY));
    const wanted = previous.bots[i] ?? "simple";
    if (![...botSel.options].some((o) => o.value === wanted)) botSel.append(new Option(botLabel(wanted), wanted));
    botSel.value = wanted;
    botSel.onchange = () => askNewBoy(botSel);
    row.append(label, deckSel, botSel);
    seats.append(row);
  }
  loadSavedList();
  pollBatch(); // show a batch that is running (or finished) in the background
}

function botLabel(kind) {
  if (kind.startsWith("boy:")) return `Boy: ${kind.slice(4)}`;
  return { simple: "SimpleBot (Python)", random: "RandomBot (Python)", forge: "Forge AI" }[kind] || kind;
}

// Picking "New Boy…" asks for a name. A Boy is created the first time it plays.
const NEW_BOY = "__new_boy__";
function askNewBoy(select) {
  if (select.value !== NEW_BOY) return;
  const name = (prompt("Name for the new Boy (letters, digits, - or _):") || "").trim();
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) { select.value = "simple"; return; }
  const value = `boy:${name}`;
  for (const other of document.querySelectorAll(".seat-row select:last-child")) {
    if (![...other.options].some((o) => o.value === value)) {
      other.insertBefore(new Option(botLabel(value), value), other.querySelector(`option[value="${NEW_BOY}"]`));
    }
  }
  select.value = value;
}

// ===========================================================================
// Run many games (no viewing)
// ===========================================================================
function chosenSeats() {
  const decks = [], bots = [];
  for (const row of document.querySelectorAll(".seat-row")) {
    const [deckSel, botSel] = row.querySelectorAll("select");
    if (!deckSel.value) continue;
    decks.push(deckSel.value);
    bots.push(botSel.value);
  }
  return { decks, bots };
}

$("#batch-start").onclick = async () => {
  try {
    $("#setup-error").textContent = "";
    await api("/api/batch/start", {
      ...chosenSeats(),
      games: Number($("#batch-games").value),
      parallel: Number($("#batch-parallel").value),
      learn: $("#batch-learn").checked,
      save_games: $("#batch-save").checked,
      seed: $("#seed").value,
    });
    pollBatch();
  } catch (e) {
    $("#setup-error").textContent = e.message;
  }
};

let batchTimer = null;
async function pollBatch() {
  clearTimeout(batchTimer);
  const b = await api("/api/batch");
  if (b.status === "none") return;
  renderBatch(b);
  if (b.status === "running" || b.status === "stopping") batchTimer = setTimeout(pollBatch, 1500);
}

function renderBatch(b) {
  const box = $("#batch");
  box.classList.remove("hidden");
  const pct = Math.round((100 * b.done) / b.games);
  const finished = b.done - b.crashed;
  const left = b.avg_seconds && b.status === "running"
    ? ` · about ${Math.ceil(((b.games - b.done) * b.avg_seconds) / b.parallel / 60)} min left` : "";
  const statusText = { running: "Running", stopping: "Stopping", finished: "Finished", stopped: "Stopped", error: "Error" }[b.status];
  let html = `<b>${statusText}</b>: ${b.done} of ${b.games} games${left}
    <div class="progress"><div style="width:${pct}%"></div></div>
    <table><tr><th>Seat</th><th>Deck</th><th>Bot</th><th>Wins</th><th></th>${b.seats.some((s) => s.avg_reward !== null) ? "<th>Avg reward</th>" : ""}</tr>`;
  for (const s of b.seats) {
    const rate = finished ? s.wins / finished : 0;
    html += `<tr><td><span class="dot" style="background:var(--seat-${s.seat})"></span> ${s.seat + 1}</td>
      <td>${s.deck}</td><td>${botLabel(s.bot)}</td><td>${s.wins} (${Math.round(rate * 100)}%)</td>
      <td><span class="bar" style="width:${Math.round(rate * 80)}px"></span></td>
      ${s.avg_reward !== null ? `<td>${s.avg_reward}</td>` : b.seats.some((x) => x.avg_reward !== null) ? "<td></td>" : ""}</tr>`;
  }
  html += `</table><p class="muted small">Draws ${b.draws} · crashed ${b.crashed} · average ${b.avg_turns ?? "–"} turns,
    ${b.avg_seconds ?? "–"} s per game${b.error ? ` · <span class="error">${b.error}</span>` : ""}</p>`;
  box.innerHTML = html;
  if (b.status === "running") {
    // Two ways to stop: let the games already being played finish (they count),
    // or end everything right away (unfinished games are thrown away).
    const buttons = el("div", "batch-stop");
    const soft = el("button", "", "Stop after the running games");
    soft.title = "Start no more games. The ones being played finish and count.";
    soft.onclick = () => api("/api/batch/stop", {}).then(pollBatch);
    const now = el("button", "danger", "Stop now");
    now.title = "End all games right away. Unfinished games don't count and nobody learns from them.";
    now.onclick = () => api("/api/batch/stop", { now: true }).then(pollBatch);
    buttons.append(soft, now);
    box.append(buttons);
  }
  if (b.status !== "running" && b.status !== "stopping") loadSavedList(); // new saved games may be there
}

async function loadSavedList() {
  const { games } = await api("/api/saved");
  const box = $("#saved");
  if (!games.length) return;
  box.innerHTML = "";
  for (const g of games.slice(0, 15)) {
    const row = el("div", "saved-row");
    row.append(el("span", "", g.id.replaceAll("_", " ")));
    const watch = el("button", "", "Watch");
    watch.onclick = async () => { await api("/api/saved/load", { id: g.id }); enterGame(); };
    row.append(watch);
    box.append(row);
  }
}

$("#start").onclick = async () => {
  const { decks, bots } = chosenSeats();
  try {
    $("#setup-error").textContent = "";
    await api("/api/game/new", { decks, bots, seed: $("#seed").value });
    enterGame();
  } catch (e) {
    $("#setup-error").textContent = e.message;
  }
};

// ===========================================================================
// Game screen: downloading the timeline
// ===========================================================================
function resetPlayback() {
  Object.assign(app, { events: [], stateSeqs: [], seats: [], pos: 0, shownStateSeq: -1,
                       lastLife: {}, playing: false, shownStack: [], holdUntil: 0 }); // a new game opens paused: press ▶ to start
  cancelCastAnimation();
  app.knownCards.clear();
  $("#log").innerHTML = "";
  $("#boards").innerHTML = "";
  $("#stack").textContent = "empty";
  $("#turn-info").textContent = "Loading rules…";
}

async function enterGame() {
  $("#setup").classList.add("hidden");
  $("#game").classList.remove("hidden");
  resetPlayback();
  updateButtons();
  poll();
  schedule(0);
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    // Keep asking until we have everything (each answer has at most 400 events).
    while (true) {
      const data = await api(`/api/game/events?since=${app.events.length}`);
      app.status = data.status;
      app.config = data.config;
      const hadState = app.stateSeqs.length > 0;
      for (const ev of data.events) addEvent(ev);
      // Paused at the very start and the first table snapshot just arrived:
      // show the opening table so there's something to look at before pressing play.
      if (!hadState && app.stateSeqs.length && !app.playing && app.pos === 0) {
        goTo(app.stateSeqs[0] + 1);
        showBanner("Ready. Press ▶ or Space to start", 4000);
      }
      if (data.events.length < 400) break;
    }
  } catch (e) {
    console.warn("poll failed", e);
  }
  polling = false;
  updateProgress();
  const finished = ["finished", "error", "stopped", "none"].includes(app.status);
  if (!finished) setTimeout(poll, 500);
  updateButtons();
}

function addEvent(ev) {
  if (ev.seq !== app.events.length) return; // already have it (or a gap: next poll fixes it)
  app.events.push(ev);
  if (ev.state) app.stateSeqs.push(ev.seq);
  if (ev.kind === "game_start") app.seats = ev.data.seats;
}

// ===========================================================================
// Playback
// ===========================================================================
function eventDelay(ev) {
  const style = EVENT_STYLES[ev.kind] || { delay: 500 };
  let ms = ev.kind === "log" ? (CATEGORY_DELAY[ev.category] ?? style.delay) : style.delay;
  if (!isShownInLog(ev) && !ev.state) ms = 0; // hidden and changes nothing: skip
  return ms / app.speed;
}

function schedule(ms) {
  clearTimeout(app.timer);
  app.timer = setTimeout(tick, ms);
}

function tick() {
  if (!app.playing) return;
  if (app.speed === Infinity) {
    goTo(app.events.length);
    schedule(250);
    return;
  }
  if (app.pos >= app.events.length) {
    schedule(200); // caught up with the live game: wait for more events
    return;
  }
  // Apply events until one takes noticeable time (hidden ones take none).
  let delay = 0;
  let steps = 0;
  while (app.pos < app.events.length && delay < 15 && steps < 200) {
    delay = eventDelay(app.events[app.pos]);
    stepForward();
    steps++;
    if (app.holdUntil > performance.now()) break; // an animation started: let it play
  }
  schedule(Math.max(delay, app.holdUntil - performance.now()));
}

function stepForward() {
  if (app.pos >= app.events.length) return;
  const ev = app.events[app.pos];
  app.pos++;
  appendLogLine(ev);
  const style = EVENT_STYLES[ev.kind];
  if (style?.banner && app.speed !== Infinity) showBanner(ev.text);
  if (ev.kind === "game_over") { app.playing = false; updateButtons(); showBanner(ev.text, 6000); }
  app.stepping = true; // render() may animate what changed, since we moved one step forward
  render();
  app.stepping = false;
}

/** Jump the playhead to any position (scrubbing, step back, replay). */
function goTo(pos) {
  cancelCastAnimation();
  app.pos = Math.max(0, Math.min(pos, app.events.length));
  rebuildLog();
  render();
}

function setPlaying(on) {
  app.playing = on;
  if (on && app.pos >= app.events.length && app.status === "finished") goTo(0); // replay a finished game
  updateButtons();
  schedule(0);
}

// ===========================================================================
// Drawing the table
// ===========================================================================
/** Index of the latest snapshot at or before the playhead (binary search). */
function currentStateSeq() {
  let lo = 0, hi = app.stateSeqs.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (app.stateSeqs[mid] < app.pos) { best = app.stateSeqs[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return best;
}

function render() {
  updateProgress();
  const seq = currentStateSeq();
  if (seq !== app.shownStateSeq) { // only redraw when the table changed
    app.shownStateSeq = seq;
    if (seq < 0) {
      app.shownStack = [];
      drawTargetArrows();
      if (app.seats.length) drawEmptyBoards();
    } else {
      const state = app.events[seq].state;
      const phase = PHASE_NAMES[state.phase] || state.phase || "";
      $("#turn-info").textContent = state.turn
        ? `Turn ${state.turn} · ${state.active_player ?? "–"} · ${phase}`
        : "Opening hands (mulligans)";
      drawBoards(state);
      drawStack(state);
      const before = new Set(app.shownStack.map((item) => item.id));
      app.shownStack = state.stack || [];
      // A spell that just went on the stack: show it big (only when playing forward).
      const cast = app.shownStack.find((item) => item.kind === "spell" && item.id !== undefined && !before.has(item.id));
      if (cast && app.stepping) playCastAnimation(cast);
      drawTargetArrows();
      if (app.followLog) $("#log").scrollTop = $("#log").scrollHeight; // the stack may have resized it
    }
  }
  const last = app.events[app.pos - 1];
  if (last?.kind === "game_over") $("#turn-info").textContent = last.text;
}

function seatIndex(name) {
  const i = app.seats.findIndex((s) => s.name === name);
  return i < 0 ? 0 : i;
}

// Boards go clockwise around the table: top left, top right, bottom right, bottom left.
// CSS "order" places seat i in grid cell CLOCKWISE[i] (cells are read row by row).
const CLOCKWISE = [0, 1, 3, 2];

function drawEmptyBoards() {
  const boards = $("#boards");
  if (boards.children.length) return;
  for (const s of app.seats) {
    const b = el("div", "board");
    b.style.setProperty("--seat", `var(--seat-${s.seat})`);
    b.append(el("div", "name", `${s.name} · ${botLabel(s.controller === "forge" ? "forge" : botOf(s.seat))}`));
    b.style.order = CLOCKWISE[s.seat] ?? s.seat;
    boards.append(b);
  }
}

function botOf(seat) {
  return app.config?.bots?.[seat] ?? "python";
}

function drawBoards(state) {
  const boards = $("#boards");
  // One board per player, created once and then updated in place.
  state.players.forEach((p, i) => {
    let board = boards.querySelector(`[data-player="${CSS.escape(p.name)}"]`);
    if (!board) {
      if (i === 0) boards.innerHTML = "";
      board = makeBoard(p.name);
      board.style.order = CLOCKWISE[i] ?? i;
      boards.append(board);
    }
    updateBoard(board, p, state);
  });
}

function makeBoard(name) {
  const seat = seatIndex(name);
  const b = el("div", "board");
  b.dataset.player = name;
  b.style.setProperty("--seat", `var(--seat-${seat})`);
  const info = app.seats[seat] || {};
  const head = el("div", "board-head");
  head.append(el("span", "name", name));
  head.append(el("span", "bot", info.controller === "forge" ? "Forge AI" : botLabel(botOf(seat))));
  // Everything that isn't a card fits on one line, so the cards get the height:
  // name, bot, counts (hand, library, ...), then the commander and the life total.
  head.append(el("div", "stats"));
  // A commander waiting in the command zone is a small picture (just its art)
  // next to the life total. Hover it for the whole card.
  const command = el("div", "zone-row command-row");
  command.dataset.zone = "command";
  head.append(command);
  head.append(el("span", "life", "40"));
  b.append(head);
  // Zones, top to bottom. Each is a row of cards we fill in updateBoard().
  for (const zone of ["creatures", "other", "lands", "hand"]) {
    const row = el("div", `zone-row ${zone}-row`);
    row.dataset.zone = zone;
    if (zone === "lands") row.classList.add("land-row");
    b.append(row);
  }
  return b;
}

function updateBoard(board, p, state) {
  board.classList.toggle("active", p.name === state.active_player);
  board.classList.toggle("lost", !!p.lost);

  // Life total, flashing red/green when it changes.
  const life = board.querySelector(".life");
  const before = app.lastLife[p.name];
  life.textContent = p.life;
  if (before !== undefined && before !== p.life) {
    life.classList.remove("hurt", "healed");
    void life.offsetWidth; // restart the CSS transition
    life.classList.add(p.life < before ? "hurt" : "healed");
    setTimeout(() => life.classList.remove("hurt", "healed"), 900);
  }
  app.lastLife[p.name] = p.life;

  // Small stats line.
  const stats = board.querySelector(".stats");
  stats.innerHTML = "";
  stats.append(el("span", "", `✋ ${p.hand_size}`), el("span", "", `📚 ${p.library_size}`));
  const gy = el("span", "zone-link", `🪦 ${p.graveyard.length}`);
  gy.onclick = () => showZone(`${p.name}: graveyard`, p.graveyard);
  const ex = el("span", "zone-link", `🌀 ${p.exile.length}`);
  ex.onclick = () => showZone(`${p.name}: exile`, p.exile);
  stats.append(gy, ex);
  if (p.poison) stats.append(el("span", "warn", `☠ ${p.poison} poison`));
  for (const d of p.commander_damage || []) {
    stats.append(el("span", d.damage >= 15 ? "warn" : "", `⚔ ${d.damage} from ${d.commander}`));
  }
  if (p.lost) stats.append(el("span", "warn", "OUT"));

  // Sort the battlefield into rows.
  const bf = p.battlefield;
  const attachedTo = new Map(); // host id -> [aura/equipment cards]
  for (const c of bf) if (c.attached_to !== undefined) {
    if (!attachedTo.has(c.attached_to)) attachedTo.set(c.attached_to, []);
    attachedTo.get(c.attached_to).push(c);
  }
  const hostIds = new Set(bf.map((c) => c.id));
  const isLand = (c) => /\bLand\b/.test(c.type) && !/Creature/.test(c.type);
  const free = bf.filter((c) => c.attached_to === undefined || !hostIds.has(c.attached_to));
  const withAttachments = (cards) => cards.flatMap((c) => [c, ...(attachedTo.get(c.id) || []).map((a) => ({ ...a, _attached: true }))]);

  // The commander waiting in the command zone ("Commander Effect" is a Forge helper, not a card).
  fillRow(board, "command", p.command.filter((c) => c.type), p);
  fillRow(board, "creatures", withAttachments(free.filter((c) => /Creature/.test(c.type))), p);
  fillRow(board, "other", withAttachments(free.filter((c) => !/Creature/.test(c.type) && !isLand(c))), p);
  fillRow(board, "lands", groupSame(free.filter(isLand)), p);
  const showHands = $("#show-hands").checked;
  fillRow(board, "hand", showHands ? (p.hand || []) : [], p);
}

/** Merge identical lands/tokens into one card with a "×N" badge, to save space. */
function groupSame(cards) {
  const groups = new Map();
  for (const c of cards) {
    const key = c.name + (c.counters ? JSON.stringify(c.counters) : "") + (c.controller !== c.owner ? "s" : "");
    if (!groups.has(key)) groups.set(key, { ...c, _count: 0, _tapped: 0, _ids: [] });
    const g = groups.get(key);
    g._count++;
    g._ids.push(c.id);
    if (c.tapped) g._tapped++;
  }
  return [...groups.values()].map((g) => ({ ...g, tapped: g._tapped === g._count }));
}

/** Make a zone row show exactly these cards, reusing card elements that are already there. */
function fillRow(board, zone, cards, player) {
  const row = board.querySelector(`[data-zone="${zone}"]`);
  const existing = new Map([...row.children].map((e) => [e.dataset.key, e]));
  const wanted = [];
  for (const c of cards) {
    const key = c._count ? `g${c.name}${c.controller !== c.owner ? "s" : ""}` : String(c.id);
    let node = existing.get(key);
    if (!node) {
      node = makeCard(c);
      node.dataset.key = key;
      if (app.knownCards.size && !app.knownCards.has(c.id)) {
        node.classList.add("new"); // a short "pop" for cards that just arrived
        setTimeout(() => node.classList.remove("new"), 600);
      }
    }
    app.knownCards.add(c.id);
    updateCard(node, c, player);
    wanted.push(node);
  }
  // Put them in order, moving only cards that are out of place (moving a card
  // restarts its animations), then remove anything not wanted any more.
  wanted.forEach((node, i) => {
    if (row.children[i] !== node) row.insertBefore(node, row.children[i] || null);
  });
  while (row.children.length > wanted.length) row.lastChild.remove();
}

function imageUrl(card) {
  return `/api/card-image?name=${encodeURIComponent(card.name)}${card.token ? "&token=1" : ""}`;
}

function makeCard(card) {
  const node = el("div", "card");
  const img = el("img");
  img.loading = "lazy";
  img.alt = card.name;
  img.src = imageUrl(card);
  // No picture (offline, odd token, ...): show a plain text card instead.
  img.onerror = () => {
    img.remove();
    const t = el("div", "text-card");
    t.append(el("b", "", card.name), el("span", "", card.type || ""));
    node.prepend(t);
  };
  node.append(img);
  node.addEventListener("mouseenter", () => showPreview(node._card));
  node.addEventListener("mouseleave", () => $("#preview").classList.add("hidden"));
  return node;
}

function updateCard(node, c, player) {
  node._card = c;
  node.classList.toggle("tapped", !!c.tapped);
  node.classList.toggle("sick", !!c.sick && /Creature/.test(c.type));
  node.classList.toggle("attacking", !!c.attacking);
  node.classList.toggle("blocking", !!c.blocking);
  node.classList.toggle("attached", !!c._attached);
  node.querySelectorAll(".badge").forEach((b) => b.remove());
  const badge = (cls, text, title) => { const b = el("span", `badge ${cls}`, text); if (title) b.title = title; node.append(b); };
  if (c.power !== undefined) {
    // +1/+1 counters turn the stats green, -1/-1 counters red. Details are in the hover preview.
    const net = ptCounters(c);
    badge(`pt ${net > 0 ? "buffed" : net < 0 ? "debuffed" : ""}`, `${c.power}/${c.toughness}`);
  }
  if (c._count > 1) badge("count", `×${c._count}`);
  if (c._count > 1 && c._tapped && c._tapped < c._count) badge("tapped-n", `${c._tapped} tapped`);
  const others = otherCounters(c);
  if (others.length) badge("counters", others.map(([k, v]) => `${v} ${shortCounter(k)}`).join(" "));
  if (c.chosen) badge("chosen", Object.values(c.chosen).join(", "), choiceText(c));
  if (c.commander_tax) badge("tax", `+${c.commander_tax}`, `Command tax: {${c.commander_tax}} more to cast`);
  // Controlled by someone who doesn't own it (stolen): a border in the owner's colour.
  // The hover preview says "owned by ...".
  const stolen = c.owner && c.controller && c.owner !== c.controller;
  node.classList.toggle("stolen", !!stolen);
  if (stolen) node.style.setProperty("--owner", `var(--seat-${seatIndex(c.owner)})`);
}

// Forge names these counters "+1/+1" and "-1/-1" (older Forge versions: "P1P1", "M1M1").
const PLUS = new Set(["+1/+1", "P1P1"]);
const MINUS = new Set(["-1/-1", "M1M1"]);

/** Net +1/+1 counters on a creature (negative if -1/-1 counters win). */
function ptCounters(c) {
  let net = 0;
  for (const [k, v] of Object.entries(c.counters || {})) {
    if (PLUS.has(k)) net += v;
    if (MINUS.has(k)) net -= v;
  }
  return net;
}

/** Counters other than +1/+1 and -1/-1 (loyalty, rope, ...), as [name, amount] pairs. */
function otherCounters(c) {
  return Object.entries(c.counters || {}).filter(([k]) => !PLUS.has(k) && !MINUS.has(k));
}

function choiceText(c) {
  return Object.entries(c.chosen || {}).map(([k, v]) => `chosen ${k}: ${v}`).join(", ");
}

function shortCounter(name) {
  return { P1P1: "+1/+1", M1M1: "-1/-1", LOYALTY: "loyalty" }[name] || name.toLowerCase();
}

function drawStack(state) {
  const box = $("#stack");
  if (!state.stack?.length) { box.textContent = "empty"; return; }
  box.innerHTML = "";
  for (const item of state.stack) { // top of the stack first
    const div = el("div", "stack-item", item.description);
    div.style.borderLeftColor = `var(--seat-${seatIndex(item.controller)})`;
    if (item.card) {
      div.addEventListener("mouseenter", () => showPreview(item.card));
      div.addEventListener("mouseleave", () => $("#preview").classList.add("hidden"));
    }
    box.append(div);
  }
}

function showPreview(card) {
  if (!card) return;
  const box = $("#preview");
  box.querySelector("img").src = imageUrl(card);
  const bits = [card.name, card.type];
  if (card.power !== undefined) {
    const net = ptCounters(card);
    if (net) {
      // e.g. "4/4 (2/2 + two +1/+1 counters)": the printed size before counters.
      const sign = net > 0 ? "+1/+1" : "-1/-1";
      bits.push(`${card.power}/${card.toughness} (${card.power - net}/${card.toughness - net} with ${Math.abs(net)} ${sign} counter${Math.abs(net) > 1 ? "s" : ""})`);
    } else {
      bits.push(`${card.power}/${card.toughness}`);
    }
  }
  for (const [k, v] of otherCounters(card)) bits.push(`${v} ${shortCounter(k)} counter${v > 1 ? "s" : ""}`);
  if (card.chosen) bits.push(choiceText(card));
  if (card.commander) bits.push(`command tax ${card.commander_tax || 0}`);
  if (card.owner && card.controller && card.owner !== card.controller) bits.push(`owned by ${card.owner}`);
  box.querySelector(".preview-text").textContent = bits.filter(Boolean).join(" · ");
  box.classList.remove("hidden");
}

function showZone(title, cards) {
  const popup = $("#zone-popup");
  popup.querySelector(".zone-head span").textContent = `${title} (${cards.length})`;
  const list = popup.querySelector(".zone-cards");
  list.innerHTML = "";
  for (const c of cards) { const n = makeCard(c); updateCard(n, c); list.append(n); }
  popup.classList.remove("hidden");
}
$("#zone-popup button").onclick = () => $("#zone-popup").classList.add("hidden");

let bannerTimer = null;
function showBanner(text, ms = 1600) {
  const b = $("#banner");
  b.textContent = text;
  b.classList.remove("hidden");
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => b.classList.add("hidden"), Math.max(400, ms / Math.min(app.speed, 4)));
}

// ===========================================================================
// The log
// ===========================================================================
function isShownInLog(ev) {
  if (ev.kind !== "log") return true;
  if (!ev.text) return false; // a snapshot without text
  return !NOISY.has(ev.category) || $("#show-noisy").checked;
}

function makeLogLine(ev) {
  const style = EVENT_STYLES[ev.kind] || {};
  const line = el("div", `log-line ${style.cls || ""} cat-${ev.category}`);
  if (ev.category === "TURN") line.classList.add("turn");
  if (ev.player && ev.kind !== "log") line.style.borderLeftColor = `var(--seat-${seatIndex(ev.player)})`;
  if (ev.kind === "log" && ev.category !== "TURN") line.append(el("span", "cat", ev.category.toLowerCase().replace("_", " ")));
  line.append(document.createTextNode(ev.text));
  line.dataset.seq = ev.seq;
  line.onclick = () => { setPlaying(false); goTo(ev.seq + 1); };
  return line;
}

const LOG_LIMIT = 400; // lines kept on screen; older ones are dropped to stay fast

function appendLogLine(ev) {
  if (!isShownInLog(ev)) return;
  const log = $("#log");
  log.append(makeLogLine(ev));
  while (log.children.length > LOG_LIMIT) log.firstChild.remove();
  if (app.followLog) log.scrollTop = log.scrollHeight;
}
// Scrolling up to read stops the auto-scroll; scrolling back to the bottom turns it on again.
// Only scrolls made by you count: the log also "scrolls" when the stack box above it grows.
let userScrolledAt = 0;
for (const type of ["wheel", "pointerdown", "touchstart", "keydown"]) {
  $("#log").addEventListener(type, () => { userScrolledAt = Date.now(); }, { passive: true });
}
$("#log").addEventListener("scroll", () => {
  if (Date.now() - userScrolledAt > 1500) return;
  const log = $("#log");
  app.followLog = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
});

function rebuildLog() {
  const log = $("#log");
  const lines = [];
  for (let i = app.pos - 1; i >= 0 && lines.length < LOG_LIMIT; i--) {
    if (isShownInLog(app.events[i])) lines.push(makeLogLine(app.events[i]));
  }
  log.replaceChildren(...lines.reverse());
  log.scrollTop = log.scrollHeight;
  app.followLog = true;
}

// ===========================================================================
// Controls
// ===========================================================================
function updateProgress() {
  const scrub = $("#scrub");
  scrub.max = app.events.length;
  scrub.value = app.pos;
  const live = app.status === "running" ? " · game running" : app.status === "starting" ? " · starting" : "";
  $("#progress").textContent = `${app.pos}/${app.events.length}${live}`;
}

function updateButtons() {
  $("#btn-play").textContent = app.playing ? "⏸" : "▶";
  const running = app.status === "running" || app.status === "starting";
  $("#btn-stop").classList.toggle("hidden", !running);
  document.querySelectorAll("#speeds button").forEach((b) => b.classList.toggle("on", Number(b.dataset.speed) === app.speed));
}

for (const s of SPEEDS) {
  const b = el("button", "", s === Infinity ? "Max" : `${s}×`);
  b.dataset.speed = s;
  b.title = s === Infinity ? "Always show the latest moment" : `Play at ${s}× speed`;
  b.onclick = () => { app.speed = s; updateButtons(); schedule(0); };
  $("#speeds").append(b);
}

$("#btn-play").onclick = () => setPlaying(!app.playing);
$("#btn-step").onclick = () => { setPlaying(false); stepForward(); };
$("#btn-back").onclick = () => { setPlaying(false); goTo(app.pos - 1); };
$("#btn-restart").onclick = () => { goTo(0); setPlaying(true); };
$("#btn-live").onclick = () => goTo(app.events.length);
$("#scrub").oninput = (e) => goTo(Number(e.target.value));
$("#btn-setup").onclick = () => { setPlaying(false); cancelCastAnimation(); app.shownStack = []; drawTargetArrows(); showSetup(); };
$("#btn-stop").onclick = () => api("/api/game/stop", {});
$("#btn-new").onclick = async () => {
  if (!app.config) return showSetup();
  await api("/api/game/new", { decks: app.config.decks, bots: app.config.bots, seed: null });
  enterGame();
};
$("#show-noisy").onchange = rebuildLog;
$("#show-hands").onchange = () => { app.shownStateSeq = -2; render(); };
$("#card-size").oninput = (e) => {
  document.documentElement.style.setProperty("--card-size", `${e.target.value}px`);
  drawTargetArrows(); // cards moved
};

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || $("#game").classList.contains("hidden")) return;
  const keys = {
    " ": () => setPlaying(!app.playing),
    ArrowRight: () => { setPlaying(false); stepForward(); },
    ArrowLeft: () => { setPlaying(false); goTo(app.pos - 1); },
    Home: () => goTo(0),
    End: () => goTo(app.events.length),
    Escape: () => $("#zone-popup").classList.add("hidden"),
  };
  if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
});

// ===========================================================================
// Main menu tabs: Play (decks, bots, batches, saved games) and Stats.
// ===========================================================================
for (const tab of document.querySelectorAll(".tabs button")) {
  tab.onclick = () => showTab(tab.dataset.tab);
}

function showTab(name) {
  for (const tab of document.querySelectorAll(".tabs button")) tab.classList.toggle("on", tab.dataset.tab === name);
  $("#tab-play").classList.toggle("hidden", name !== "play");
  $("#tab-stats").classList.toggle("hidden", name !== "stats");
  if (name === "stats") loadStats();
}

$("#stats-source").onchange = loadStats;

async function loadStats() {
  const s = await api(`/api/stats?source=${$("#stats-source").value}`);
  const body = $("#stats-body");
  if (!s.games) {
    body.innerHTML = "No finished games yet. Play a game or use <b>Run many games</b>, and the results show up here.";
    return;
  }
  const pct = (x) => `${Math.round(x * 100)}%`;
  body.innerHTML = `<p>${s.games} games${s.draws ? ` (${s.draws} draws)` : ""}, ${s.avg_turns} turns on average, from ${s.first.slice(0, 16)} to ${s.last.slice(0, 16)}.
    With 4 players an even match wins about ${pct(s.fair_share)}: the line on each bar.</p>`;
  const table = (title, rows, label) => {
    const wrap = el("div", "stats-table");
    wrap.append(el("h3", "", title));
    const t = el("table");
    t.innerHTML = `<tr><th>${label}</th><th>Games</th><th>Wins</th><th>Win rate</th><th></th></tr>`;
    for (const r of rows) {
      const tr = el("tr");
      const name = label === "Bot" ? botLabel(r.name) : r.name.replace(/ · (.*)$/, (m, bot) => ` · ${botLabel(bot)}`);
      // A bar for the win rate, with a mark at the "fair share" (25% for 4 players).
      tr.innerHTML = `<td>${name}</td><td>${r.games}</td><td>${r.wins}</td><td>${pct(r.win_rate)}</td>
        <td><div class="rate-bar ${r.win_rate > s.fair_share ? "above" : ""}" style="--rate:${r.win_rate}; --fair:${s.fair_share}">
          <div></div></div></td>`;
      t.append(tr);
    }
    wrap.append(t);
    return wrap;
  };
  body.append(table("Decks", s.decks, "Deck"), table("Bots", s.bots, "Bot"), table("Deck + bot", s.pairs, "Deck · bot"));
  body.append(el("p", "muted small", "Few games say little: with under ~30 games a win rate can easily be off by 15 points. Numbers come from stats/results.csv in the repo folder."));
}

// ===========================================================================
// Cast animation: a spell that is cast is shown big in the middle of its
// caster's board for a moment, then it shrinks into the Stack box on the right
// (where it waits to resolve). Playback waits for it. Faster at higher speeds,
// skipped at 10x and Max.
// ===========================================================================
const CAST_MS = 1500; // at 1x: about 0.3 s in, 0.8 s shown, 0.4 s out

function playCastAnimation(item) {
  if (!item.card || app.speed >= 10) return;
  const board = $(`#boards [data-player="${CSS.escape(item.controller)}"]`);
  if (!board) return;
  cancelCastAnimation();
  const total = CAST_MS / app.speed;
  app.holdUntil = performance.now() + total;

  const r = board.getBoundingClientRect();
  const layer = el("div", "cast-layer");
  // Dim the caster's board so the card stands out.
  const dim = el("div", "cast-dim");
  Object.assign(dim.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  // The card, as big as fits in the board (but not huge).
  const width = Math.min(250, r.width * 0.45, (r.height - 60) / 1.397);
  const card = el("img", "cast-card");
  card.src = imageUrl(item.card);
  card.alt = item.card.name;
  const left = r.left + r.width / 2 - width / 2;
  const top = r.top + r.height / 2 - (width * 1.397) / 2 - 14;
  Object.assign(card.style, { width: `${width}px`, left: `${left}px`, top: `${top}px`,
                              boxShadow: `0 0 0 4px var(--seat-${seatIndex(item.controller)}), 0 0 40px rgba(0,0,0,.8)` });
  const label = el("div", "cast-label", `${item.controller} casts ${item.card.name}`);
  Object.assign(label.style, { left: `${r.left + r.width / 2}px`, top: `${top + width * 1.397 + 10}px` });
  layer.append(dim, card, label);
  document.body.append(layer);
  app.castLayer = layer;

  // Where it goes: a permanent (creature, artifact, ...) flies to the spot on
  // its caster's board where it will land; an instant or sorcery never stays on
  // the table, so it shrinks into the Stack box on the right until it resolves.
  const spot = landingSpot(board, item.card) || stackSpot();
  const dx = spot.x - (left + width / 2);
  const dy = spot.y - (top + (width * 1.397) / 2);
  const opts = { duration: total, easing: "ease-in-out", fill: "forwards" };
  card.animate([
    { transform: "scale(.4)", opacity: 0, offset: 0 },
    { transform: "scale(1)", opacity: 1, offset: 0.2 },
    { transform: "scale(1)", opacity: 1, offset: 0.65 },
    { transform: `translate(${dx}px, ${dy}px) scale(${spot.width / width})`, opacity: spot.fade ? 0.2 : 1, offset: 1 },
  ], opts);
  for (const node of [dim, label]) {
    node.animate([{ opacity: 0 }, { opacity: 1, offset: 0.15 }, { opacity: 1, offset: 0.7 }, { opacity: 0 }], opts);
  }
  setTimeout(() => {
    if (app.castLayer !== layer) return;
    cancelCastAnimation();
    drawTargetArrows(); // held back while the card was shown big
  }, total);
}

/** Center and width of the next free place in the row this permanent goes to, or null for instants/sorceries. */
function landingSpot(board, card) {
  const type = card.type || "";
  if (/Instant|Sorcery/.test(type)) return null;
  const zone = /Creature/.test(type) ? "creatures" : /Land/.test(type) ? "lands" : "other";
  const row = board.querySelector(`[data-zone="${zone}"]`);
  const size = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--card-size")) || 72;
  const last = row.lastElementChild;
  if (last) { // right after the last card in the row
    const r = last.getBoundingClientRect();
    const w = Math.min(r.width, r.height / 1.397);
    return { x: r.right + 6 + w / 2, y: r.top + r.height / 2, width: w };
  }
  // The row is empty (and hidden): use the top left of the board, under the header.
  const w = zone === "lands" ? size * 0.75 : size;
  const head = board.querySelector(".board-head").getBoundingClientRect();
  return { x: head.left + w / 2, y: head.bottom + 6 + (w * 1.397) / 2, width: w };
}

function stackSpot() {
  const t = ($("#stack").firstElementChild || $("#stack")).getBoundingClientRect(); // newest item is first
  return { x: t.left + t.width / 2, y: t.top + t.height / 2, width: 40, fade: true };
}

function cancelCastAnimation() {
  app.castLayer?.remove();
  app.castLayer = null;
  app.holdUntil = 0;
}

// ===========================================================================
// Targeting arrows: while a spell or ability is on the stack, a red arrow goes
// from its source to each thing it targets. The top of the stack (the one that
// resolves next) is bright; older items are faint. The arrow ends on the target
// a little toward the source, not in the middle of it.
// ===========================================================================
const SVG_NS = "http://www.w3.org/2000/svg";

/** The card element showing this card id (also inside a group like "Treasure x3"). */
function cardNode(id) {
  for (const node of document.querySelectorAll("#boards .card")) {
    const c = node._card;
    if (c && (c.id === id || c._ids?.includes(id))) return node;
  }
  return null;
}

/** Where an arrow for this stack item starts: its card on the table, or its line in the Stack box. */
function arrowSource(item, index) {
  return (item.card && item.kind !== "spell" && cardNode(item.card.id)) || $("#stack").children[index] || null;
}

/** Where an arrow ends: a card, a player's life total, or a spell in the Stack box. */
function arrowTarget(t) {
  if (t.card !== undefined) return cardNode(t.card);
  if (t.player !== undefined) return $(`#boards [data-player="${CSS.escape(t.player)}"] .life`);
  if (t.spell !== undefined) {
    const i = app.shownStack.findIndex((item) => item.id === t.spell);
    return i >= 0 ? $("#stack").children[i] : null;
  }
  return null;
}

/** A point inside rectangle r, part of the way from its center toward (x, y). */
function pointToward(r, x, y, part = 0.55) {
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const dx = x - cx, dy = y - cy;
  const len = Math.hypot(dx, dy) || 1;
  // How far we can go in that direction before leaving the rectangle.
  const toEdge = Math.min(dx ? r.width / 2 / Math.abs(dx / len) : Infinity,
                          dy ? r.height / 2 / Math.abs(dy / len) : Infinity);
  return [cx + (dx / len) * toEdge * part, cy + (dy / len) * toEdge * part];
}

function drawTargetArrows() {
  let svg = $("#arrows");
  if (!svg) {
    svg = document.createElementNS(SVG_NS, "svg");
    svg.id = "arrows";
    svg.innerHTML = `<defs><marker id="arrow-head" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="4" markerHeight="4"
      orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#e5534b"/></marker></defs>`;
    document.body.append(svg);
  }
  svg.querySelectorAll(".arrow").forEach((n) => n.remove());
  document.querySelectorAll(".targeted, .targeting").forEach((n) => n.classList.remove("targeted", "targeting"));
  if ($("#game").classList.contains("hidden") || app.castLayer) return; // after the cast animation

  app.shownStack.forEach((item, index) => {
    const targets = item.targets || [];
    const from = targets.length ? arrowSource(item, index) : null;
    if (!from) return;
    const top = index === 0;
    if (top) from.classList.add("targeting");
    const a = from.getBoundingClientRect();
    for (const t of targets) {
      const to = arrowTarget(t);
      if (!to || to === from) continue;
      if (top) to.classList.add("targeted");
      const b = to.getBoundingClientRect();
      // Start at the source's edge facing the target (its center for a card on the table).
      const [x1, y1] = pointToward(a, b.left + b.width / 2, b.top + b.height / 2, from.classList.contains("card") ? 0 : 1);
      const [x2, y2] = pointToward(b, x1, y1);
      // A gentle curve: the control point sits off to the side of the straight line.
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, len = Math.hypot(x2 - x1, y2 - y1);
      const cx = mx + ((y2 - y1) / (len || 1)) * len * 0.18;
      const cy = my - ((x2 - x1) / (len || 1)) * len * 0.18;
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("class", `arrow${top ? "" : " faint"}`);
      path.setAttribute("d", `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`);
      path.setAttribute("marker-end", "url(#arrow-head)");
      svg.append(path);
    }
  });
}

// Cards move when the window is resized or a board is scrolled: redraw the arrows.
window.addEventListener("resize", drawTargetArrows);
$("#boards").addEventListener("scroll", drawTargetArrows, true);

// ===========================================================================
// Start: if a game is already running (page reload), go straight to it.
// ===========================================================================
(async () => {
  const game = await api("/api/game");
  if (game.status !== "none") { app.config = game.config; enterGame(); } else showSetup();
})();
