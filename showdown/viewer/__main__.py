"""Start the browser viewer.

    python -m showdown.viewer              opens http://127.0.0.1:8000 in your browser
    python -m showdown.viewer --port 8080  use another port
    python -m showdown.viewer --no-browser don't open a browser tab

(A file called __main__.py is what ``python -m showdown.viewer`` runs.)
Stop the server with Ctrl+C.
"""

from __future__ import annotations

import argparse
import webbrowser

from showdown.viewer.server import STATE, serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Commander games in your browser.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    server = serve(args.port)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Viewer running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if STATE.session:
            STATE.session.stop(wait=5)  # don't leave Forge running in the background
        server.server_close()


if __name__ == "__main__":
    main()
