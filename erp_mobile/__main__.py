"""Start: ``python -m erp_mobile [--port 8080] [--host 0.0.0.0] [--cert c.pem --key k.pem]``.

Die Datenbankverbindung kommt wie bei ERP Desk aus ``ERP_DATABASE_URL`` oder der
lokalen ``config.ini`` (siehe README).
"""
from __future__ import annotations

import argparse
import socket
import sys

from erp import config, db

from . import APP_NAME, __version__
from .server import make_server


def _lan_addresses() -> list[str]:
    addrs = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))     # es wird nichts gesendet
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="erp-mobile", description=f"{APP_NAME} – Server für Smartphones")
    ap.add_argument("--host", default="0.0.0.0", help="Adresse (Standard: alle Netzwerkkarten)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--cert", help="TLS-Zertifikat (PEM) für HTTPS")
    ap.add_argument("--key", help="privater Schlüssel (PEM) zum Zertifikat")
    ap.add_argument("--quiet", action="store_true", help="keine Zugriffsprotokollierung")
    args = ap.parse_args(argv)

    cfg, _ = config.load()
    if cfg.backend == "sqlite":
        config.config_dir().mkdir(parents=True, exist_ok=True)
    try:
        db.connect(cfg.url())
        db.init_schema()
    except Exception as exc:  # noqa: BLE001
        print(f"Datenbank nicht erreichbar ({cfg.display()}): {getattr(exc, 'orig', exc)}", file=sys.stderr)
        return 1
    httpd = make_server(args.host, args.port, args.cert, args.key, args.quiet)
    scheme = "https" if args.cert else "http"
    print(f"{APP_NAME} {__version__} – Datenbank: {cfg.display()}")
    for addr in _lan_addresses() or ["<IP dieses Rechners>"]:
        print(f"Auf dem Smartphone öffnen: {scheme}://{addr}:{args.port}/")
    print("Beenden mit Strg+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
