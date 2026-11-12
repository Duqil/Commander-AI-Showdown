"""Put a saved game on the website, or take it off, with one click in the viewer.

The viewer's Saved games tab calls this when you press "Put on website" or
"Remove from website". It does what you would otherwise type by hand:

  1. make the replay (export_replay.make_replay: card pictures, title),
  2. commit it to pages/replays/ on GitHub's main branch,
  3. push. GitHub then rebuilds the website (about a minute).

It never touches your own files or the branch you have checked out. Git can
build a commit "on the side": we start from GitHub's main (origin/main), add
or remove the one replay in a temporary *index* (git's list of files for the
next commit), and push that commit. Your folder, your branch and anything you
are in the middle of stay exactly as they were. Run `git pull` on main later
to see the replay files locally too.

Pushing uses the same GitHub login as your normal `git push`.

Python concepts used here:
  * ``subprocess.run``      - run another program (git) and read what it prints.
  * ``tempfile``            - a throwaway folder that is deleted afterwards.
  * ``threading.Thread``    - publishing takes a minute, so it runs in the background.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from pages.export_replay import make_replay

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY_PATH = "pages/replays"   # where replays live in the repo (git always uses "/")
REMOTE, BRANCH = "origin", "main"


class PublishError(Exception):
    """Something went wrong; the message is shown in the viewer."""


def git(*args: str, env: dict | None = None, stdin: bytes | None = None) -> str:
    """Run one git command in the project folder and return what it printed."""
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, input=stdin, capture_output=True,
                            env={**os.environ, **(env or {})})
    if result.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode(errors="replace").strip()


def published_ids() -> set[str]:
    """Ids of the games on the website, as far as we last heard from GitHub."""
    try:
        names = git("ls-tree", "--name-only", f"{REMOTE}/{BRANCH}", f"{REPLAY_PATH}/")
    except (PublishError, OSError):
        return set()   # no git, or never fetched: show nothing as published
    return {Path(n).name.removesuffix(".json.gz") for n in names.splitlines() if n.endswith(".json.gz")}


def commit_on_main(message: str, add: tuple[str, bytes] | None = None, remove: str | None = None) -> None:
    """Make one commit on GitHub's main that adds or removes one file, and push it."""
    for attempt in (1, 2):   # if someone pushed in between, start again from the new main
        git("fetch", "--quiet", REMOTE, BRANCH)
        base = f"{REMOTE}/{BRANCH}"
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}   # a temporary index, not yours
            git("read-tree", base, env=env)                       # start from main's files
            if add:
                path, data = add
                blob = git("hash-object", "-w", "--stdin", stdin=data)   # store the file in git
                git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            if remove:
                git("update-index", "--force-remove", remove, env=env)
            tree = git("write-tree", env=env)
            commit = git("commit-tree", tree, "-p", base, "-m", message)
        try:
            git("push", "--quiet", REMOTE, f"{commit}:refs/heads/{BRANCH}")
            git("fetch", "--quiet", REMOTE, BRANCH)
            return
        except PublishError:
            if attempt == 2:
                raise


class Publisher:
    """Runs publish/remove jobs in the background and remembers how they went."""

    def __init__(self) -> None:
        self.jobs: dict[str, str] = {}   # game id -> "adding", "removing" or an error message
        self._lock = threading.Lock()

    def status(self) -> dict[str, str]:
        with self._lock:
            return dict(self.jobs)

    def start(self, game_id: str, game_file: Path | None) -> None:
        """game_file = the saved game to add, or None to remove it from the website."""
        with self._lock:
            if self.jobs.get(game_id) in ("adding", "removing"):
                return
            self.jobs[game_id] = "adding" if game_file else "removing"
        threading.Thread(target=self._run, args=(game_id, game_file), daemon=True).start()

    def _run(self, game_id: str, game_file: Path | None) -> None:
        path = f"{REPLAY_PATH}/{game_id}.json.gz"
        try:
            if game_file:
                commit_on_main(f"Add replay {game_id}", add=(path, make_replay(game_file)))
            else:
                commit_on_main(f"Remove replay {game_id}", remove=path)
            with self._lock:
                del self.jobs[game_id]
        except Exception as e:   # show any problem in the viewer
            with self._lock:
                self.jobs[game_id] = f"Failed: {e}"
