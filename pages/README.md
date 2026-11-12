# Replay website (GitHub Pages) – a trial

A small website where anyone can watch a few recorded games in their browser,
with nothing to install. It uses the same viewer as `python -m showdown.viewer`,
but plays back saved games instead of running Forge. Card pictures come from
Scryfall.

Everything for it lives in two places, so it's easy to remove:

| File | What it does |
|---|---|
| `pages/replays/*.json.gz` | The recorded games shown on the site |
| `pages/replay.js` | Stands in for the Python server, so the viewer can run as plain files |
| `pages/build_site.py` | Copies the viewer + `replay.js` + replays into `pages/_site/` |
| `pages/export_replay.py` | Turns a game from `games/` into a replay (adds card picture URLs) |
| `pages/publish.py` | The viewer's "Put on website" button: commits and pushes one replay |
| `.github/workflows/replay-site.yml` | On GitHub: builds the site and publishes it on every change |

The site is built from the normal viewer (`showdown/viewer/static/`), so viewer
improvements show up on it automatically. The only website code inside the
viewer is the Saved games button, which hides itself when `pages/` is gone.

## Try it on your computer

```bash
python -m pages.build_site
python -m http.server 8001 --directory pages/_site
```

Open http://127.0.0.1:8001 and press Ctrl+C in the terminal to stop.

## Put it online

1. On GitHub, open the repo's **Settings > Pages**.
2. Under **Build and deployment > Source**, pick **GitHub Actions**.
3. Open the **Actions** tab, click **Replay site**, then **Run workflow**.

After a minute the site is at `https://duqil.github.io/Commander-AI-Showdown/`.
From then on it rebuilds by itself whenever `pages/` or the viewer changes on `main`.
The repo must be public for Pages on a free GitHub plan.

## Add or remove a game (one click)

1. Watch a game in the viewer (`python -m showdown.viewer`) and let it finish,
   or tick "Save games for replay" in Run many games.
2. Open **Saved games** and press **Put on website** next to the game.
   After about a minute it says **On website ✓**, and a minute later the site has it.
3. To take it off, press **On website ✓** and confirm.

The title and description are made from the decks and bots, e.g.
"Kaiba (Kuja), Tristan (Garnet), ...". No winner is shown, so nothing is spoiled.

Behind the button (`pages/publish.py`): it makes the replay, commits only that
one file to GitHub's `main` and pushes, using your normal GitHub login. It never
changes your files or the branch you're on. `git pull` on main later brings the
replay files to your folder too.

By hand, if you prefer: `python -m pages.export_replay games/<file>.json.gz`
(optional `--title "..." --about "..."`), then commit `pages/replays/` and push.

## Remove the website completely

1. **Settings > Pages**: under Source, pick **Deploy from a branch** and then
   **Unpublish site** at the top (or set Source to **None** if it's offered).
   The site goes offline.
2. Delete the `pages/` folder and `.github/workflows/replay-site.yml`, commit and push.

The viewer then simply stops showing the website buttons (it only shows them
when `pages/` is there). No other code needs changing.
