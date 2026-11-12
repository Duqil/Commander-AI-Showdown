"""Build the replay website into pages/_site/.

The website is the normal viewer (showdown/viewer/static/) plus one extra file,
pages/replay.js, which stands in for the Python server. So whenever the viewer
gets better, the website gets the same improvement on its next build.

Steps:
  1. copy the viewer's files (index.html, app.js, style.css),
  2. add replay.js to index.html, just before app.js,
  3. unzip each replay in pages/replays/ and write a list of them (index.json).

Try it on your own computer:

    python -m pages.build_site
    python -m http.server 8001 --directory pages/_site

then open http://127.0.0.1:8001. (Opening index.html as a file doesn't work:
browsers don't let a file:// page download other files.)
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
VIEWER = HERE.parent / "showdown" / "viewer" / "static"
SITE = HERE / "_site"


def build() -> Path:
    if SITE.exists():
        shutil.rmtree(SITE)  # start clean, so deleted replays disappear too
    (SITE / "replays").mkdir(parents=True)

    for name in ("app.js", "style.css"):
        shutil.copy(VIEWER / name, SITE / name)
    shutil.copy(HERE / "replay.js", SITE / "replay.js")

    # The local viewer serves its files under /static/. On GitHub Pages the site
    # lives at /<repo name>/, so use relative paths ("app.js") instead.
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/', 'href="').replace('src="/static/', 'src="')
    html = html.replace('<script src="app.js"></script>',
                        '<script src="replay.js"></script>\n<script src="app.js"></script>')
    if "replay.js" not in html:
        raise SystemExit("Couldn't find the app.js <script> line in index.html; update build_site.py")
    (SITE / "index.html").write_text(html, encoding="utf-8")

    # Newest first, like the viewer's Saved games tab.
    listing = []
    for file in sorted(HERE.glob("replays/*.json.gz"), reverse=True):
        game_id = file.name.removesuffix(".json.gz")
        with gzip.open(file, "rt", encoding="utf-8") as f:
            game = json.load(f)
        (SITE / "replays" / f"{game_id}.json").write_text(json.dumps(game, separators=(",", ":")), encoding="utf-8")
        # No winner or game length in the list: that would spoil the game.
        listing.append({"id": game_id, "title": game.get("title", game_id), "about": game.get("about", "")})
    (SITE / "replays" / "index.json").write_text(json.dumps(listing, indent=1), encoding="utf-8")
    print(f"Built {SITE} with {len(listing)} replays")
    return SITE


if __name__ == "__main__":
    build()
