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
  deal_offer:   { cls: "deal", delay: 400 },   // deals also get an icon between the boards (playDealMoment)
  deal_accept:  { cls: "deal accept", delay: 400 },
  deal_decline: { cls: "deal decline", delay: 400 },
  deal_break:   { cls: "deal break", banner: true, delay: 600 },
  deal_kept:    { cls: "deal accept", delay: 300 },   // a deal ran its full length unbroken: a quiet line only
};
// Forge log categories that get their own pacing.
const CATEGORY_DELAY = { TURN: 1000, PHASE: 120, MANA: 40, STATE: 150, COMBAT: 700, DAMAGE: 600 };
const NOISY = new Set(["PHASE", "MANA", "STATE"]); // hidden from the log unless the box is ticked
const SPEEDS = [0.5, 0.75, 1, 2, 4, Infinity];    // Infinity = "Max": jump straight to the latest event
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
  dealSeqs: [],      // positions of deal events (offers, accepts, breaks), in order
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
  logTab: "log",     // which log tab is open: "log", "all" (phases & mana too) or "politics"
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
  const asked = prompt("Name for the new Boy (letters, digits, spaces, - _ or ', up to 32 characters):");
  // Tidy up: no spaces at the ends, and "Nicol   Bolas" becomes "Nicol Bolas".
  const name = (asked || "").trim().replace(/\s+/g, " ");
  if (!name) { select.value = "simple"; return; } // cancelled
  // Same rule as VALID_NAME in showdown/boys/storage.py.
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9 _'-]{0,30}[A-Za-z0-9])?$/.test(name)) {
    alert(`"${name}" can't be a Boy's name. Use letters, digits, spaces, - _ or ' (up to 32 characters, starting and ending with a letter or digit).`);
    select.value = "simple";
    return;
  }
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
      deals: $("#deals").checked,
    });
    pollBatch();
  } catch (e) {
    $("#setup-error").textContent = e.message;
  }
};

let batchTimer = null;
let batchWasRunning = false; // so we celebrate only a batch we saw running, not one finished long ago
async function pollBatch() {
  clearTimeout(batchTimer);
  const b = await api("/api/batch");
  if (b.status === "none") return;
  renderBatch(b);
  const running = b.status === "running" || b.status === "stopping";
  if (running) batchTimer = setTimeout(pollBatch, 1500);
  else if (batchWasRunning) celebrateBatch(b);
  batchWasRunning = running;
}

// A batch just ended: confetti, a summary card, and "✓" in the tab title in
// case you're looking at another tab.
function celebrateBatch(b) {
  const finished = b.done - b.crashed;
  const best = [...b.seats].sort((x, y) => y.wins - x.wins)[0];
  const minutes = Math.max(1, Math.round((b.elapsed || 0) / 60));
  const card = el("div", "batch-done");
  card.innerHTML = `<div class="batch-done-icon">🎉</div>
    <b>${b.status === "finished" ? "All games done!" : "Batch stopped"}</b>
    <div>${finished} of ${b.games} games in about ${minutes} min${b.crashed ? ` · ${b.crashed} crashed` : ""}</div>
    ${best && finished ? `<div>Most wins: <b>${best.deck}</b> · ${botLabel(best.bot)} · ${best.wins} of ${finished}</div>` : ""}
    <button>Nice</button>`;
  card.querySelector("button").onclick = () => card.remove();
  document.body.append(card);
  if (b.status === "finished") confetti();
  const title = document.title;
  document.title = "✓ Games done · " + title;
  // Put the title back once you come back to this tab (or right away if you're on it).
  const restore = () => { if (!document.hidden) { document.title = title; document.removeEventListener("visibilitychange", restore); } };
  document.addEventListener("visibilitychange", restore);
  setTimeout(restore, 4000);
}

// Party popper: 140 little coloured pieces burst up from the bottom corners and
// fall down. Each piece is a <div> with a CSS animation (see .confetti in
// style.css). Its path comes from CSS variables we set per piece.
function confetti() {
  const colors = ["#e5534b", "#f2cc60", "#57ab5a", "#539bf5", "#b083f0", "#f69d50"];
  const layer = el("div", "confetti-layer");
  for (let i = 0; i < 140; i++) {
    const piece = el("div", "confetti");
    const fromLeft = i % 2 === 0;
    piece.style.left = fromLeft ? "0" : "100%";
    piece.style.background = colors[i % colors.length];
    // --dx: how far sideways it flies (towards the middle), --up: how high it goes.
    piece.style.setProperty("--dx", `${(fromLeft ? 1 : -1) * (15 + Math.random() * 45)}vw`);
    piece.style.setProperty("--up", `${45 + Math.random() * 45}vh`);
    piece.style.setProperty("--spin", `${(Math.random() * 4 - 2) * 360}deg`);
    piece.style.animationDelay = `${Math.random() * 0.3}s`;
    piece.style.animationDuration = `${2.2 + Math.random() * 1.5}s`;
    layer.append(piece);
  }
  document.body.append(layer);
  setTimeout(() => layer.remove(), 4500);
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
  box.classList.remove("muted");
  box.append(el("p", "muted small", `${games.length} saved games, newest first. They're in the games/ folder.`));
  for (const g of games) {
    // An id looks like "2026-11-10_234210_zidane-anhelo-nelly-kuja_003" (the last part only for batch games).
    const [date, time, decks, number] = g.id.split("_");
    const row = el("div", "saved-row");
    const when = time ? `${date} ${time.slice(0, 2)}:${time.slice(2, 4)}` : g.id;
    row.append(el("span", "saved-when", when),
               el("span", "saved-decks", (decks || "").replaceAll("-", " · ")),
               el("span", "muted small", number ? `batch game ${Number(number) + 1}` : ""));
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
    await api("/api/game/new", { decks, bots, seed: $("#seed").value, deals: $("#deals").checked });
    enterGame();
  } catch (e) {
    $("#setup-error").textContent = e.message;
  }
};

// ===========================================================================
// Game screen: downloading the timeline
// ===========================================================================
function resetPlayback() {
  Object.assign(app, { events: [], stateSeqs: [], dealSeqs: [], seats: [], pos: 0, shownStateSeq: -1,
                       lastLife: {}, playing: false, shownStack: [], holdUntil: 0,
                       rounds: { byTurn: new Map(), firstSeat: null, lastPlace: null, round: 0 } }); // a new game opens paused: press ▶ to start
  cancelCastAnimation();
  app.knownCards.clear();
  $("#log").innerHTML = "";
  $("#boards").innerHTML = "";
  $("#stack").textContent = "empty";
  $("#turn-info").textContent = "Loading rules…";
  // The arrows live in their own layer on top of the page, not in #boards, so
  // clear them too. Otherwise the last game's attack arrows stay up until the
  // new game's first table arrives.
  drawTargetArrows();
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

// Rounds: a round is over once every player still in the game has had a turn.
// Turns go around the table in seat order, starting with whoever went first.
// So a new round starts when the turn comes back around past the first player:
// the active player's place in that order is lower than the last one's. That
// still works after a knockout (nobody waits for a player who's out), and an
// extra turn (same player again) doesn't start a new round.
function countRound(state) {
  const r = app.rounds;
  if (!state.turn || r.byTurn.has(state.turn)) return; // mulligans, or a turn we've seen
  const seat = parseInt(state.active_player, 10) - 1; // player names look like "3-Nelly"
  const seats = app.seats.length || 4;
  if (r.firstSeat === null) r.firstSeat = seat;
  const place = (seat - r.firstSeat + seats) % seats; // 0 = the player who went first
  if (r.lastPlace === null) r.round = 1;
  else if (place < r.lastPlace) r.round += 1;
  r.lastPlace = place;
  r.byTurn.set(state.turn, r.round);
}

function addEvent(ev) {
  if (ev.seq !== app.events.length) return; // already have it (or a gap: next poll fixes it)
  app.events.push(ev);
  if (ev.state) { app.stateSeqs.push(ev.seq); countRound(ev.state); }
  if (ev.kind.startsWith("deal_")) app.dealSeqs.push(ev.seq);
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
  if (style?.banner && app.speed !== Infinity) showBanner(ev.text, 2500, ev.kind === "deal_break" ? "break" : "");
  if (ev.kind in DEAL_COLORS) playDealMoment(ev);
  if (ev.kind === "game_over") { app.playing = false; updateButtons(); showBanner(ev.text, 6000); }
  app.stepping = true; // render() may animate what changed, since we moved one step forward
  render();
  app.stepping = false;
}

/** Jump the playhead to any position (scrubbing, step back, replay). */
function goTo(pos) {
  cancelCastAnimation();
  app.dealLayer?.remove();
  document.querySelectorAll(".deal-glow").forEach((b) => b.classList.remove("deal-glow"));
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
        ? `Round ${app.rounds.byTurn.get(state.turn) ?? "?"} · Turn ${state.turn} · ${state.active_player ?? "–"} · ${phase}`
        : "Opening hands (mulligans)";
      drawBoards(state);
      drawStack(state);
      if (app.fitSize) scheduleFit();
      const before = new Set(app.shownStack.map((item) => item.id));
      app.shownStack = state.stack || [];
      // A spell that just went on the stack: show it big (only when playing forward).
      const cast = app.shownStack.find((item) => item.kind === "spell" && item.id !== undefined && !before.has(item.id));
      if (cast && app.stepping) playCastAnimation(cast);
      drawTargetArrows();
      if (app.followLog) $("#log").scrollTop = $("#log").scrollHeight; // the stack may have resized it
    }
  }
  // The Politics tab changes with deal events too, which carry no table snapshot.
  if (app.logTab === "politics") drawPolitics(app.shownStateSeq >= 0 ? app.events[app.shownStateSeq].state.turn : 0);
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
    // Short, so the header stays on one line: "⚔ 4 Nelly". Hover for the full name.
    const span = el("span", d.damage >= 15 ? "warn" : "", `⚔ ${d.damage} ${d.commander.split(/[ ,]/)[0]}`);
    span.title = `${d.damage} commander damage from ${d.commander} (21 kills)`;
    stats.append(span);
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
  const params = new URLSearchParams({ name: card.name });
  if (card.token) {
    params.set("token", "1");
    // Many tokens share a name: these pick the right picture (e.g. which "Knight").
    if (card.colors !== undefined) params.set("colors", card.colors);
    if (card.base_power !== undefined) params.set("pt", `${card.base_power}/${card.base_toughness}`);
    if (card.oracle !== undefined) params.set("oracle", card.oracle);
  }
  return `/api/card-image?${params}`;
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
    const div = el("div", "stack-item", cleanText(item.description));
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
function showBanner(text, ms = 1600, cls = "") {
  const b = $("#banner");
  b.textContent = text;
  b.className = cls;
  b.classList.remove("hidden");
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => b.classList.add("hidden"), Math.max(400, ms / Math.min(app.speed, 4)));
}

// ===========================================================================
// The log
// ===========================================================================
function isShownInLog(ev) {
  if (app.logTab === "politics") return ev.kind.startsWith("deal_");
  if (ev.kind !== "log") return true;
  if (!ev.text) return false; // a snapshot without text
  return !NOISY.has(ev.category) || app.logTab === "all";
}

/** Forge adds an internal card number after names, "Garnet (403)": drop it, it means nothing to us. */
function cleanText(text) {
  return text.replace(/ \(\d+\)/g, "");
}

function makeLogLine(ev) {
  const style = EVENT_STYLES[ev.kind] || {};
  const line = el("div", `log-line ${style.cls || ""} cat-${ev.category}`);
  if (ev.category === "TURN") line.classList.add("turn");
  if (ev.player && ev.kind !== "log") line.style.borderLeftColor = `var(--seat-${seatIndex(ev.player)})`;
  if (ev.kind === "log" && ev.category !== "TURN") line.append(el("span", "cat", ev.category.toLowerCase().replace("_", " ")));
  line.append(document.createTextNode(cleanText(ev.text)));
  // Forge logs life changes as "Life: 2-Anhelo 33 > 36": green when it went up.
  const life = ev.category === "LIFE" && /(\d+)\s*>\s*(\d+)/.exec(ev.text);
  if (life && Number(life[2]) > Number(life[1])) line.classList.add("gain");
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
  await api("/api/game/new", { decks: app.config.decks, bots: app.config.bots, seed: null, deals: !!app.config.deals });
  enterGame();
};
// Log tabs: the game log, the same with every phase and mana line, or only politics (deals).
for (const tab of document.querySelectorAll(".log-tabs button")) {
  tab.onclick = () => {
    app.logTab = tab.dataset.log;
    document.querySelectorAll(".log-tabs button").forEach((t) => t.classList.toggle("on", t === tab));
    $("#politics-now").classList.toggle("hidden", app.logTab !== "politics");
    rebuildLog();
    render();
  };
}
$("#show-hands").onchange = () => { app.shownStateSeq = -2; render(); };
// ---------------------------------------------------------------------------
// Card size: steps of 8 px with the value shown, or "Fit", which keeps picking
// the biggest size where no board needs scrolling (cards come and go, so it
// re-checks whenever the table changes). Your choice is remembered.
// ---------------------------------------------------------------------------
const SIZE_MIN = 48, SIZE_MAX = 160, SIZE_STEP = 8;

function setCardSize(px) {
  app.cardSize = Math.max(SIZE_MIN, Math.min(SIZE_MAX, px));
  document.documentElement.style.setProperty("--card-size", `${app.cardSize}px`);
  $("#size-value").textContent = app.cardSize;
}

function boardsOverflow() {
  return [...document.querySelectorAll("#boards .board")].some(
    (b) => b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1);
}

/** The biggest size where nothing overflows (each try re-measures the boards). */
function fitCardSize() {
  if ($("#game").classList.contains("hidden") || !document.querySelector("#boards .board")) return;
  const before = app.cardSize;
  let size = SIZE_MAX;
  setCardSize(size);
  while (size > SIZE_MIN && boardsOverflow()) setCardSize((size -= SIZE_STEP));
  if (app.cardSize !== before) drawTargetArrows();
}

let fitFrame = 0;
function scheduleFit() { // at most once per screen refresh, even when many events play at once
  cancelAnimationFrame(fitFrame);
  fitFrame = requestAnimationFrame(fitCardSize);
}

function saveSize() {
  try { localStorage.setItem("cardSize", JSON.stringify({ size: app.cardSize, fit: app.fitSize })); } catch {}
}

function changeSize(delta) {
  app.fitSize = false;
  $("#size-fit").classList.remove("on");
  setCardSize(app.cardSize + delta);
  saveSize();
  drawTargetArrows();
}

$("#size-down").onclick = () => changeSize(-SIZE_STEP);
$("#size-up").onclick = () => changeSize(SIZE_STEP);
$("#size-fit").onclick = () => {
  app.fitSize = !app.fitSize;
  $("#size-fit").classList.toggle("on", app.fitSize);
  if (app.fitSize) fitCardSize();
  saveSize();
};
window.addEventListener("resize", () => { if (app.fitSize) fitCardSize(); });

(() => { // the size you used last time
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("cardSize")); } catch {}
  setCardSize(saved?.size ?? 80);
  app.fitSize = !!saved?.fit;
  $("#size-fit").classList.toggle("on", app.fitSize);
})();

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
  $("#tab-saved").classList.toggle("hidden", name !== "saved");
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
// Deals between bots (showdown/deals.py). Two things show them:
//   * the Politics tab next to the log: deals that are on right now, grudges,
//     and every offer, answer and break so far,
//   * a moment on the table when a deal is offered, accepted, declined or
//     broken: a big icon between the two boards with what was said. Playback
//     waits for it, like the cast animation.
// ===========================================================================
const DEAL_COLORS = { deal_offer: "#c38bff", deal_accept: "#57ab5a", deal_decline: "#8a94a6", deal_break: "#e5534b" };
const DEAL_ICONS = { deal_offer: "🤝?", deal_accept: "🤝", deal_decline: "✋", deal_break: "💔" };

/** Deals that are on and grudges, as of the playhead. Reads only the deal events. */
function dealsNow(turn) {
  const deals = new Map(), grudges = [];
  for (const seq of app.dealSeqs) {
    if (seq >= app.pos) break;
    const ev = app.events[seq], deal = ev.data?.deal;
    if (!deal) continue;
    if (ev.kind === "deal_accept") deals.set(deal.id, deal);
    if (ev.kind === "deal_kept") deals.delete(deal.id);
    if (ev.kind === "deal_break") {
      deals.delete(deal.id);
      const victim = deal.a === ev.player ? deal.b : deal.a;
      grudges.push({ victim, breaker: ev.player });
    }
  }
  return { deals: [...deals.values()].filter((d) => turn <= d.expires_turn), grudges };
}

function shortName(player) { return player.replace(/^\d+-/, ""); }

/** The "Right now" part of the Politics tab: deals that are on, who is hunted, grudges. */
function drawPolitics(turn) {
  const box = $("#politics-now");
  const { deals, grudges } = dealsNow(turn);
  const tab = $('.log-tabs [data-log="politics"]');
  tab.textContent = deals.length ? `Politics (${deals.length})` : "Politics";
  if (!app.dealSeqs.length) {
    box.innerHTML = '<p class="muted">No deals in this game. Tick "Bots make deals" in the main menu to turn them on.</p>';
    return;
  }
  const lines = [];
  const both = (d) => `${shortName(d.a)} & ${shortName(d.b)}`;
  for (const d of deals) {
    if (d.kind === "gang_up") lines.push(["gang", `⚔ ${both(d)} are ganging up on ${shortName(d.against)}`, `until turn ${d.expires_turn}`]);
    else lines.push(["peace", `🤝 ${both(d)} have peace`, `until turn ${d.expires_turn}`]);
  }
  for (const g of grudges) lines.push(["grudge", `💢 ${shortName(g.victim)} wants revenge on ${shortName(g.breaker)}`, "broke a deal"]);
  box.replaceChildren(el("h4", "", "Right now"));
  if (!lines.length) box.append(el("p", "muted", "No deals right now."));
  for (const [cls, text, note] of lines) {
    const row = el("div", `politics-row ${cls}`, text);
    row.append(el("span", "muted", ` · ${note}`));
    box.append(row);
  }
  box.append(el("h4", "", "What happened"));
}

function playDealMoment(ev) {
  const deal = ev.data?.deal;
  if (!deal || app.speed === Infinity) return;
  // Who "speaks": the offerer offers, the other one answers, the breaker breaks.
  const from = ev.kind === "deal_offer" ? deal.a : ev.kind === "deal_break" ? ev.player : deal.b;
  const to = from === deal.a ? deal.b : deal.a;
  const a = $(`#boards [data-player="${CSS.escape(from)}"]`), b = $(`#boards [data-player="${CSS.escape(to)}"]`);
  if (!a || !b) return;
  app.dealLayer?.remove();
  const total = (ev.kind === "deal_break" ? 2600 : 1800) / app.speed;
  app.holdUntil = Math.max(app.holdUntil, performance.now() + total);
  const color = DEAL_COLORS[ev.kind];
  // A big icon halfway between the two boards, with what was said underneath.
  const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
  const x = (ra.left + ra.width / 2 + rb.left + rb.width / 2) / 2;
  const y = (ra.top + ra.height / 2 + rb.top + rb.height / 2) / 2;
  const layer = el("div", "deal-layer");
  const icon = el("div", `deal-icon ${ev.kind}`, DEAL_ICONS[ev.kind]);
  icon.style.borderColor = color;
  icon.style.boxShadow = `0 0 30px ${color}`;
  const label = el("div", "deal-label", cleanText(ev.text));
  label.style.borderColor = color;
  for (const node of [icon, label]) Object.assign(node.style, { left: `${x}px`, top: `${y}px` });
  layer.append(icon, label);
  document.body.append(layer);
  app.dealLayer = layer;
  icon.animate([{ transform: "translate(-50%, -50%) scale(.3)" }, { transform: "translate(-50%, -50%) scale(1.15)", offset: .15 },
                { transform: "translate(-50%, -50%) scale(1)", offset: .25 }], { duration: total, fill: "forwards" });
  for (const board of [a, b]) board.style.setProperty("--deal", color), board.classList.add("deal-glow");
  layer.animate([{ opacity: 0 }, { opacity: 1, offset: .12 }, { opacity: 1, offset: .85 }, { opacity: 0 }], { duration: total, fill: "forwards" });
  setTimeout(() => {
    for (const board of [a, b]) board.classList.remove("deal-glow");
    if (app.dealLayer === layer) { layer.remove(); app.dealLayer = null; }
  }, total);
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
  const size = app.cardSize;
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

// ---------------------------------------------------------------------------
// Combat arrows: each attacker gets a red arrow to the middle of the board of
// the player it attacks, ending in a sword. A blocker gets a blue arrow to the
// creature it blocks, and a shield on itself. (Snapshots only have attackers
// and blockers during combat, so these show only then.)
// ---------------------------------------------------------------------------
const SWORD = '<path d="M27 5 L31 5 L31 9 L16 24 L12 20 Z" fill="#fff"/><path d="M9 21 L15 27 M11 29 L7 25 M13 25 L8 30" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/>';
const SHIELD = '<path d="M18 6 L28 10 L28 18 C28 25 23 29 18 31 C13 29 8 25 8 18 L8 10 Z" fill="#fff"/>';

function combatIcon(svg, x, y, shape, color) {
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("class", "arrow combat-icon");
  g.setAttribute("transform", `translate(${x - 18},${y - 18})`);
  g.innerHTML = `<circle cx="18" cy="18" r="17" fill="${color}" stroke="#fff" stroke-width="2"/>${shape}`;
  svg.append(g);
}

function curve(svg, x1, y1, x2, y2, cls) {
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const cx = (x1 + x2) / 2 + (y2 - y1) * 0.12, cy = (y1 + y2) / 2 - (x2 - x1) * 0.12;
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("class", `arrow ${cls}`);
  path.setAttribute("d", `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`);
  svg.append(path);
  return len;
}

function drawCombatArrows(svg) {
  const center = (r) => [r.left + r.width / 2, r.top + r.height / 2];
  const cards = [...document.querySelectorAll("#boards .card")].filter((n) => n._card);
  // A blocked attacker shows only the blocker's blue arrow, not its own red one.
  const blocked = new Set(cards.flatMap((n) => n._card.blocks || []));
  for (const node of cards) {
    const c = node._card;
    const ids = c._ids || [c.id];
    if (c.attacking && !ids.every((id) => blocked.has(id))) {
      // c.attacking is the defending player (or a planeswalker's name: then aim at that card's controller's board).
      const board = $(`#boards [data-player="${CSS.escape(c.attacking)}"]`);
      if (board) {
        const [x1, y1] = center(node.getBoundingClientRect()), [x2, y2] = center(board.getBoundingClientRect());
        curve(svg, x1, y1, x2, y2, "attack");
        combatIcon(svg, x2, y2, SWORD, "#e5534b");
      }
    }
    for (const id of c.blocks || []) {
      const target = cardNode(id);
      if (!target) continue;
      const r = node.getBoundingClientRect();
      const [x1, y1] = center(r);
      const [x2, y2] = pointToward(target.getBoundingClientRect(), x1, y1);
      curve(svg, x1, y1, x2, y2, "block");
      combatIcon(svg, x1, y1, SHIELD, "#539bf5"); // the shield sits on the blocker
    }
  }
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

  drawCombatArrows(svg);
  // Triggered abilities waiting on the stack: their card glows gold with a "TRIGGER" tag.
  document.querySelectorAll(".card.triggering").forEach((n) => n.classList.remove("triggering"));
  for (const item of app.shownStack) {
    if (item.kind === "trigger" && item.card) cardNode(item.card.id)?.classList.add("triggering");
  }
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
