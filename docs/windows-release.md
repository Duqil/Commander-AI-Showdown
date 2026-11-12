# Windows download (automatic releases)

**Status: trial.** This makes a ready-to-run Windows zip, so friends can try the
app without installing Git, Python, Java or Maven. It's built fresh by GitHub
every time you ask, so it never goes stale.

## What's in the zip

The zip is the project folder after `setup_forge.sh`, plus a private copy of
Java and Python:

```
CommanderAIShowdown-v0.1.0-windows/
  Start Commander AI Showdown.bat   double-click this
  READ ME FIRST.txt
  showdown/, decks/, docs/          our code (exactly what's committed)
  forge-bridge/target/              our built bridge
  vendor/forge/                     the built Forge engine and its card scripts
  runtime/java/                     Java 21 (Eclipse Temurin JRE)
  runtime/python/                   Python 3.12 ("embeddable" version from python.org)
```

Nothing is installed on the PC; deleting the folder removes everything.
Your own `boys/`, `games/` and `stats/` are never included.

## Make a release

1. Make sure what you want to share is merged into `main`.
2. In Git Bash, in the project folder:

   ```bash
   git checkout main
   git pull
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. Open the **Actions** tab on GitHub and watch "Windows release" run
   (about 10 to 20 minutes, mostly building Forge).
4. When it's green, the zip is on the **Releases** page (marked "Pre-release").
   Share that page's link.

Next time, use a bigger number (`v0.1.1`, `v0.2.0`, ...).

**Just testing?** On the Actions tab, pick "Windows release" and press
**Run workflow**. That builds the zip without making a release. Download it
from the bottom of the run's page under "Artifacts" (kept 7 days).

**Undo a test release:** on the Releases page, delete the release, then
remove the tag: `git push origin --delete v0.1.0` and `git tag -d v0.1.0`.

## How it works

- [`.github/workflows/windows-release.yml`](../.github/workflows/windows-release.yml)
  tells GitHub when to run (a pushed `v...` tag, or the Run workflow button)
  and what to do: build Forge, run the packaging script, upload the zip.
- [`scripts/package_windows.sh`](../scripts/package_windows.sh) builds the zip.
  You can run it yourself after `setup_forge.sh`; it writes to `dist/`.

## Licenses

Forge is GPL-3.0. Sharing a zip that contains it means this project's code must
stay open source (a public repo is enough). The zip's `READ ME FIRST.txt` says
where every part comes from.

## Removing this feature

Delete these three files and the link to this page in the README:

- `.github/workflows/windows-release.yml`
- `scripts/package_windows.sh`
- `docs/windows-release.md`

(and the `dist/` line in `.gitignore`, if you like).
