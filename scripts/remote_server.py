"""Run the standalone P7 HTTPS remote storage service.

Example:

    python scripts/remote_server.py --state .backnest/remote --port 4780 \
        --cert tests/fixtures/tls/server.pem \
        --key tests/fixtures/tls/server.key \
        --add-user demo:demo-password

The default certificate is generated locally under .backnest/tls; it is
self-signed and for local tests only.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backnest.remote_server import RemoteService  # noqa: E402
from backnest.tls import TlsUnavailable, ensure_test_cert  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=str(ROOT / ".backnest" / "remote"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4780)
    parser.add_argument("--cert", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--add-user", action="append", default=[], metavar="USER:PASSWORD")
    args = parser.parse_args()
    certfile, keyfile = args.cert, args.key
    if not certfile or not keyfile:
        try:
            paths = ensure_test_cert(ROOT / ".backnest" / "tls")
        except TlsUnavailable as error:
            raise SystemExit(str(error))
        certfile, keyfile = paths.certfile, paths.keyfile
    service = RemoteService(Path(args.state), certfile, keyfile, args.host, args.port)
    for item in args.add_user:
        if ":" not in item:
            raise SystemExit("--add-user must be USER:PASSWORD")
        username, password = item.split(":", 1)
        service.add_user(username, password)
    service.start()
    print(f"BackNest remote HTTPS: {service.url}")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            service.thread.join(1)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
