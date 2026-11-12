// Replay mode for the GitHub Pages site.
//
// The normal viewer (app.js) talks to a Python server: it asks "/api/game/events"
// for the timeline and "/api/card-image" for pictures. GitHub Pages can only
// hand out files, so this script pretends to be that server:
//
//   * fetch("/api/...") is answered from the replay files in replays/,
//   * card pictures come straight from Scryfall (URLs saved by export_replay.py),
//   * buttons that need a real game (Play, Stats, New game, Stop) are hidden.
//
// app.js itself is unchanged. This file is loaded just before it (build_site.py).
// Link to one game directly with  ...?replay=<id>

"use strict";

(() => {
  const replay = { list: null, current: null };   // current = { id, config, events, images }
  const realFetch = window.fetch.bind(window);

  const answer = (data, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

  async function replayList() {
    if (!replay.list) replay.list = await (await realFetch("replays/index.json")).json();
    return replay.list;
  }

  async function loadReplay(id) {
    const list = await replayList();
    if (!list.some((g) => g.id === id)) return false;   // only ids from the list, nothing else
    const game = await (await realFetch(`replays/${encodeURIComponent(id)}.json`)).json();
    replay.current = { id, ...game };
    return true;
  }

  function summary() {
    const g = replay.current;
    if (!g) return { status: "none" };
    return { id: g.id, status: "finished", event_count: g.events.length, config: g.config };
  }

  // The pretend server: the same answers server.py gives, from files.
  async function fakeServer(url, options) {
    const path = url.pathname;
    const body = options && options.body ? JSON.parse(options.body) : {};
    if (path === "/api/game") {
      const wanted = new URLSearchParams(location.search).get("replay");
      if (!replay.current && wanted) await loadReplay(wanted);
      return answer(summary());
    }
    if (path === "/api/game/events") {
      if (!replay.current) return answer({ events: [], status: "none" });
      const since = Number(url.searchParams.get("since") || 0);
      return answer({ events: replay.current.events.slice(since, since + 400), ...summary() });
    }
    if (path === "/api/saved/load") {
      return (await loadReplay(body.id)) ? answer(summary()) : answer({ error: "no such replay" }, 404);
    }
    if (path === "/api/saved") return answer({ games: (await replayList()).map((g) => ({ id: g.id })) });
    if (path === "/api/decks") return answer({ decks: [], bots: [] });
    if (path === "/api/batch") return answer({ status: "none" });
    if (path === "/api/game/stop") return answer({ ok: true });
    return answer({ error: "This is a replay site: games can't be started here." }, 400);
  }

  window.fetch = (input, options) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    // Only the viewer's own "/api/..." requests are faked; everything else is a normal download.
    return typeof input === "string" && input.startsWith("/api/") ? fakeServer(url, options) : realFetch(input, options);
  };

  // --- after app.js has loaded ------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    // Card pictures: look the card up in the replay's saved URLs (same key as
    // card_key() in export_replay.py). No URL -> a broken src, and app.js then
    // shows its plain text card.
    window.imageUrl = (card) => {
      const images = (replay.current && replay.current.images) || {};
      const pt = card.base_power !== undefined && card.base_power !== null ? `${card.base_power}/${card.base_toughness}` : "";
      const key = card.token ? [card.name, card.colors || "", pt, card.oracle || ""].join("|") : card.name;
      return images[key] || "data:,";
    };

    // Hide what only works with a real game behind it.
    for (const sel of ["nav.tabs", "#tab-play", "#tab-stats", "#tab-saved", "#btn-new", "#btn-stop"]) {
      document.querySelector(sel).classList.add("replay-hidden");
    }
    const style = document.createElement("style");
    style.textContent = `
      .replay-hidden { display: none !important; }
      #replays .replay { border-bottom: 1px solid var(--line); padding: 10px 0; display: grid;
                         grid-template-columns: 1fr auto; gap: 2px 12px; align-items: center; }
      #replays .replay b { font-size: 15px; }
      #replays .replay button { grid-row: span 2; }
      #replays .note { margin-top: 18px; }
      #replays a { color: var(--accent); }`;
    document.head.append(style);

    // Our own list of replays, with a title and who played what.
    const box = document.createElement("div");
    box.id = "replays";
    box.innerHTML = `<p class="muted">Recorded games between bots, played by the
      <a href="https://github.com/Card-Forge/forge">Forge</a> rules engine. Pick one to watch.</p>
      <div id="replay-list" class="muted small">Loading…</div>
      <p class="note muted small">To pick your own decks and bots, run the project on your computer:
      <a href="https://github.com/Duqil/Commander-AI-Showdown">Commander AI Showdown on GitHub</a>.
      Card images from <a href="https://scryfall.com">Scryfall</a>.</p>`;
    document.querySelector(".setup-box h1").after(box);

    replayList().then((list) => {
      const listBox = document.querySelector("#replay-list");
      listBox.innerHTML = "";
      listBox.classList.remove("muted", "small");
      if (!list.length) listBox.textContent = "No replays yet.";
      for (const g of list) {
        const row = document.createElement("div");
        row.className = "replay";
        const title = document.createElement("b");
        title.textContent = g.title;
        const watch = document.createElement("button");
        watch.className = "primary";
        watch.textContent = "Watch";
        watch.onclick = async () => {
          await loadReplay(g.id);
          history.replaceState(null, "", `?replay=${encodeURIComponent(g.id)}`);   // a link you can share
          enterGame();   // from app.js
        };
        const about = document.createElement("span");
        about.className = "muted small";
        about.textContent = g.about;
        row.append(title, watch, about);
        listBox.append(row);
      }
    });

    // "Main Menu" goes back to the list: forget the ?replay= link.
    document.querySelector("#btn-setup").addEventListener("click", () => history.replaceState(null, "", location.pathname));
  });
})();
