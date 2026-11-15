"""Start a private local web UI on a free port; Ctrl+C closes jobs cleanly."""
import sys
import webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import DemoServer, ROOT


def main():
    server = DemoServer(0, ROOT / '.backnest')
    url = f'http://127.0.0.1:{server.server_port}/'
    print('BackNest: ' + url, flush=True)
    print('Keep this window open. Press Ctrl+C to stop safely.', flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
