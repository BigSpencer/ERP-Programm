# ERP Desk

Ein modulares ERP-System nach dem Vorbild klassischer Unternehmenssoftware (Module
analog zu SD, MM, FI). Es wird **lokal auf jedem Arbeitsplatz installiert** und
greift auf eine **gemeinsame zentrale Datenbank** zu. Alle Benutzer arbeiten so
in Echtzeit auf denselben Daten.

```
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ Arbeitsplatz │   │ Arbeitsplatz │   │ Arbeitsplatz │   ERP-Desk-Client
 │  (Windows)   │   │   (macOS)    │   │   (Linux)    │   (lokal installiert)
 └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
        └──────────────────┼──────────────────┘
                           │  TCP 5432 (LAN / VPN)
                  ┌────────┴────────┐
                  │   PostgreSQL    │   gemeinsame Datenbank
                  │ (Server / NAS)  │   + tägliche Sicherung
                  └─────────────────┘
```

## Funktionsumfang

| Modul | Funktionen |
|---|---|
| **Übersicht** | Kennzahlen (Umsatz, offene Forderungen, Überfälliges, offene Aufträge/Bestellungen), Umsatzdiagramm, Nachschubbedarf |
| **Stammdaten** | Kunden (inkl. Kreditlimit, Zahlungsziel), Lieferanten, Artikel/Material (Preise, USt-Satz, Mindestbestand, Standardlieferant), automatische Nummernkreise |
| **Vertrieb (SD)** | Kundenaufträge mit Positionen und Rabatten · Belegfluss *Entwurf → Bestätigt → Geliefert → Fakturiert* · Kreditlimit- und Verfügbarkeitsprüfung · Auftragsbestätigung als PDF/Druck |
| **Rechnungen** | Ausgangsrechnungen mit automatischer Buchung, Zahlungseingänge (Teilzahlungen), Überfälligkeit, Rechnung als PDF/Druck |
| **Einkauf (MM)** | Bestellungen · *Entwurf → Bestellt → (Teil-)Geliefert* · Wareneingang mit Teilmengen · automatische Bestellvorschläge aus Mindestbeständen |
| **Lager** | Bestände je Lagerort, Zu-/Abgänge, Umlagerungen, Inventur, vollständige Bewegungshistorie |
| **Finanzbuchhaltung (FI)** | Doppelte Buchführung (SKR03-angelehnter Kontenrahmen), Journal, manuelle Buchungssätze, Summen- und Saldenliste, offene Posten, GuV |
| **Administration** | Benutzer & Rollen (Admin, Vertrieb, Einkauf, Lager, Buchhaltung), Firmendaten für Belege, Änderungsprotokoll |

Automatische Buchungen:

* Rechnung: *Forderungen (1400)* an *Erlöse (8400/8300)* und *Umsatzsteuer (1776/1771)*
* Zahlung: *Bank (1200) / Kasse (1000)* an *Forderungen (1400)*
* Wareneingang: *Wareneingang (3400/3300)* und *Vorsteuer (1576/1571)* an *Verbindlichkeiten (1600)*

### Mehrbenutzerbetrieb

* **Optimistisches Sperren:** Jeder Datensatz hat eine Versionsnummer. Ändern zwei
  Benutzer gleichzeitig denselben Datensatz, erhält der zweite einen Hinweis statt
  die Änderung des ersten still zu überschreiben.
* **Nummernkreise und Bestände** werden mit Zeilensperren (`SELECT … FOR UPDATE`)
  vergeben bzw. gebucht – parallele Arbeitsplätze erhalten nie dieselbe Belegnummer
  und Bestände werden nie doppelt verbraucht (getestet mit parallelen Clients gegen PostgreSQL).
* **Transaktionen:** Jeder Geschäftsvorfall (z. B. Lieferung = Bestandsbuchung +
  Statuswechsel) läuft vollständig oder gar nicht.
* **Rechte:** Die Navigation zeigt nur die Module der eigenen Rolle.

## Installation

### 1. Zentrale Datenbank einrichten (einmalig)

**Mit Docker** (Server, NAS, oder ein beliebiger immer laufender Rechner):

```bash
cd server
cp .env.example .env          # Passwort in .env setzen
docker compose up -d
```

Damit läuft PostgreSQL auf Port 5432, inklusive täglicher Sicherung nach `server/backup/`.

**Ohne Docker:** PostgreSQL installieren und `server/setup_postgres_native.sql`
ausführen (Netzfreigabe in `pg_hba.conf`/`postgresql.conf` beachten, siehe Datei).

> Port 5432 nur im Firmennetz oder per VPN erreichbar machen, nicht direkt ins Internet.

### 2. Client auf jedem Arbeitsplatz installieren

**Variante A – Installationspaket (empfohlen):**
Der GitHub-Actions-Workflow baut bei jedem Push fertige Pakete
(Actions → *Tests & Installer* → Artefakte):

* Windows: `ERPDesk-Setup-1.0.0.exe` (Installer mit Startmenü- und Desktop-Verknüpfung)
* macOS / Linux: Ordner `ERPDesk/` mit ausführbarer Datei

Selbst bauen:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/erp_desk.spec        # -> dist/ERPDesk/
iscc packaging\installer.iss               # nur Windows, Inno Setup 6 -> dist/ERPDesk-Setup-*.exe
```

**Variante B – aus dem Quellcode (Python ≥ 3.10):**

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m erp
```

### 3. Erster Start

1. Beim ersten Start erscheint der Dialog **Datenbankverbindung**: Typ *PostgreSQL*,
   Server-Adresse (z. B. `192.168.0.10`), Port, Datenbank `erp`, Benutzer `erp`, Passwort.
   *Verbindung testen* → *OK*. Die Einstellung wird lokal gespeichert
   (Windows: `%APPDATA%\ERPDesk\config.ini`, Linux/macOS: `~/.config/erpdesk/config.ini`).
2. Ist die Datenbank leer, legt der erste Client das Schema automatisch an.
3. Anmeldung mit **admin / admin** – danach sofort über *Datei → Passwort ändern*
   ein neues Passwort setzen und unter *Administration* weitere Benutzer anlegen.

Die Verbindung lässt sich jederzeit über *Verbindung …* im Anmeldedialog,
*Datei → Datenbankverbindung* oder den Startparameter `--setup` ändern.

### Kommandozeile

```bash
python -m erp --setup        # Verbindungsdialog erzwingen
python -m erp --init-db      # Schema anlegen/aktualisieren (ohne Oberfläche)
python -m erp --demo-data    # Schema + Demodaten (Kunden, Artikel, Aufträge, Buchungen, Benutzer)
```

Für automatisierte Verteilung kann die Verbindung statt per Dialog auch über
`ERP_DATABASE_URL` (z. B. `postgresql+psycopg2://erp:pw@server/erp`) oder eine
vorbereitete `config.ini` (Pfad über `ERP_CONFIG`) vorgegeben werden.

Demodaten-Benutzer: `vertrieb/vertrieb`, `lager/lager`, `buchhaltung/buchhaltung`.

Unterstützte Datenbanken: **PostgreSQL** (empfohlen), MySQL/MariaDB, SQLite
(nur Einzelplatz/Test – SQLite-Dateien auf Netzlaufwerken sind für
Mehrbenutzerbetrieb nicht zuverlässig).

## Projektstruktur

```
erp/
  config.py            lokale Verbindungskonfiguration
  db.py                Verbindung, Schema, Grunddaten (Kontenrahmen, Nummernkreise, admin)
  models.py            Datenmodell (SQLAlchemy)
  services/            Geschäftslogik ohne UI (auth, masterdata, documents, inventory, finance, reports)
  ui/                  Desktop-Oberfläche (PySide6/Qt)
  demo.py              Demodaten
server/                Docker-Compose für PostgreSQL + Backup
packaging/             PyInstaller-Spec und Windows-Installer (Inno Setup)
tests/                 Tests (SQLite, optional PostgreSQL, UI-Rauchtest)
```

## Tests

```bash
pip install pytest
QT_QPA_PLATFORM=offscreen pytest -q
# zusätzlich gegen einen echten PostgreSQL-Server:
ERP_TEST_DATABASE_URL=postgresql+psycopg2://erp:erp@localhost/erp_test pytest -q tests/test_services.py
```

## Grenzen / mögliche Erweiterungen

Dies ist eine solide Basis, aber kein Ersatz für ein zertifiziertes Großsystem.
Nicht enthalten sind u. a.: GoBD-konforme Festschreibung und Revisionssicherheit,
DATEV-Export, E-Rechnung (XRechnung/ZUGFeRD), Gutschriften/Stornorechnungen,
Kreditoren-Rechnungsprüfung, Produktion/Stücklisten, Mehrmandanten und
Mehrwährung. Die Architektur (Service-Schicht getrennt von der Oberfläche) ist
darauf ausgelegt, solche Module zu ergänzen. Für steuerrelevanten Echtbetrieb
sollte die Buchhaltung durch einen Steuerberater abgenommen werden.
