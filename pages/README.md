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
| `.github/workflows/replay-site.yml` | On GitHub: builds the site and publishes it on every change |

The viewer itself (`showdown/viewer/`) isn't changed, so viewer improvements
show up on the site automatically.

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

## Add a game

1. Play a game in the viewer (or tick "Save games for replay" in Run many games).
   It's saved in `games/`.
2. Export it:
   ```bash
   python -m pages.export_replay games/<file>.json.gz --title "A short title" --about "Kaiba (Kuja), Tristan (Garnet), ..."
   ```
3. Commit the new file in `pages/replays/` and push.

To take a game off the site, delete its file in `pages/replays/`.

## Remove the website completely

1. **Settings > Pages**: under Source, pick **Deploy from a branch** and then
   **Unpublish site** at the top (or set Source to **None** if it's offered).
   The site goes offline.
2. Delete the `pages/` folder and `.github/workflows/replay-site.yml`, commit and push.

Nothing else in the project uses these files.
