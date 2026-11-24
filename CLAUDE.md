# ERP Desk – Premium Deluxe Motorsport (GTA-RP-Autohaus)

Deutschsprachiges ERP für ein Autohaus auf einem GTA-Roleplay-Server. Antworten und Oberflächentexte auf Deutsch.

## Aufbau
- `erp/models.py` – Datenmodell (SQLAlchemy). Neue Spalten in bestehenden Tabellen werden beim Start per `db.add_missing_columns` ergänzt (keine Migrationen nötig, Spalten nullable anlegen).
- `erp/services/` – Geschäftslogik ohne UI: `dealer.py` (Katalog, Fahrzeuge, Angebote → Bestellungen → Verkauf, Ankauf/Inzahlungnahme), `auth.py` (Benutzer, Rollen, Rechte), `maintenance.py` (Admin: endgültig löschen, Datenbank bereinigen).
- `erp/web/` – **Web-Version (wird produktiv genutzt)**: `app.py` (FastAPI, `/api/state` liefert alles, Änderungen über `/api/action/<name>`), `beta.py` (Testumgebung), `static/` (`index.html`, `app.js`, `app.css` – Vanilla JS, kein Build).
- `erp/ui/` – Desktop-Client (PySide6), nur noch nebenbei gepflegt.
- `tests/` – `QT_QPA_PLATFORM=offscreen python3 -m pytest -q` (Web-Tests brauchen `fastapi`, `httpx`).

## Betrieb (Render.com)
- **Live:** https://pdm.bitstand.de – Render-Service `erp-programm`, Branch **`live`**, Auto-Deploy an.
- **Beta:** https://pdmbeta.bitstand.de – Render-Service `erp-programm-beta`, Branch **`ccr-e0dba8f1-14jvqt`**, Auto-Deploy an.
  Umgebungsvariable `ERP_BETA_SOURCE_URL` = Internal Database URL der Live-Datenbank, **kein** `ERP_DATABASE_URL`:
  die Beta kopiert beim Start die Live-Daten (Live-Verbindung nur lesend) in eine temporäre SQLite-Datei.
- Build Command: `pip install -r requirements-web.txt`
- Start Command: `uvicorn erp.web.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'`
- Live-Variablen: `ERP_DATABASE_URL` (PostgreSQL, Frankfurt), `ERP_SECRET_KEY`.
- DNS bei IONOS (Domain bitstand.de): CNAME `pdm` → Live-Service, CNAME `pdmbeta` → Beta-Service (`*.onrender.com`). SSL kommt von Render.

## Arbeitsweise
- Änderungen nur auf `ccr-e0dba8f1-14jvqt` pushen (landet in der Beta).
- Auf `live` **nur auf ausdrückliche Anweisung** pushen („Rolle Version x.y.z aus“): `git push origin <getesteter-commit>:refs/heads/live`, Version und Stand nennen.
- Versionsnummer bei jeder Änderung in kleinen Schritten erhöhen (2.1.3 → 2.1.4 …), Stellen: `erp/__init__.py`, `pyproject.toml`, `packaging/installer.iss`, `README.md` (Setup-Dateiname). Sie erscheint im Anmeldefenster und unten links.
- Vor dem Push: Tests laufen lassen, Web-Oberfläche mit Playwright im Browser prüfen (lokal: `ERP_INSECURE_COOKIES=1 uvicorn erp.web.app:app`).

## Konventionen
- Spielbetrieb: **keine Steuern** (USt fest 0, keine Steuerfelder), Währung **Dollar im US-Format** `$120,000.00`; Eingaben in US- und deutschem Format werden verstanden. Datum, Prozente, km deutsch.
- Statistiken: kleinster Zeitraum ist die **Kalenderwoche** (ISO, Mo–So).
- Design „Midnight & Gold“: dunkles Nachtblau/Schwarz, Gold nur als Akzent; Farb-Tokens in `app.css`.
- Listen: Spalten sortierbar, Kopfzeile fixiert, Aktionen als Knöpfe in der Zeile; „Löschen“ nur für Admins (erst sauber im Ablauf, sonst „Trotzdem endgültig löschen“).
- Rollen sind frei definierbar (Tabelle `roles`); Mitarbeiter können mehrere Rollen haben; Admins erscheinen nicht unter „Mitarbeiter“.
- Ablauf: Angebot → (Anzahlung) Bestellung → Eingetroffen → Ausgeliefert = Verkauf mit Restzahlung; Inzahlungnahmen werden im Gebrauchtwagen-Ankauf erfasst und beim Verkauf ausgewählt.
