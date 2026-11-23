# ERP Desk – Autohaus-Edition (Premium Deluxe Motorsport)

ERP für das Autohaus **Premium Deluxe Motorsport**: Fahrzeugbestand, Ausstellung,
Fahrzeugbestellungen, Verkauf mit Inzahlungnahme, Gebrauchtwagen-Ankauf, Kunden sowie
Ein- und Verkaufspreise – mehrbenutzerfähig auf einer gemeinsamen Datenbank.
Es löst die bisherige Google-Tabelle ab. Die Software enthält **keine Kunden- oder
Kaufdaten**; diese werden ausschließlich im laufenden Betrieb in der eigenen Datenbank erfasst.

## Autohaus-Module

| Modul | Funktionen |
|---|---|
| **Cockpit** | Bestand (Anzahl, EK-/Listenwert), Ausstellung, offene Bestellungen inkl. Anzahlungen, Verkäufe und Rohertrag (Monat/Jahr), Verkaufsdiagramm, Fahrzeuge mit längster Standzeit, erwartete Lieferungen |
| **Fahrzeugbestand** | Jedes Fahrzeug mit Nr. (FZ-…), Hersteller, Modell, Klasse, Zustand neu/gebraucht, FIN, Kennzeichen, Farbe, Baujahr, km, EK, VK, Marge, Standtage · Filter nach Status und Zustand · Reservieren für Kunden · Ausbuchen (z. B. Totalschaden) · doppelte FIN wird verhindert |
| **Ausstellung** | Showroom-Übersicht nach Stellplatz, Fahrzeuge ausstellen/umstellen/herausnehmen, doppelt belegte Stellplätze werden verhindert, Ausstellungsdauer |
| **Bestellungen** | Fahrzeugbestellung beim Hersteller – als Lagerfahrzeug oder Kundenbestellung mit vereinbartem Preis und Anzahlung · *Bestellt → Eingetroffen → Ausgeliefert* · bei Eingang wird das Fahrzeug automatisch angelegt und für den Kunden reserviert · Überfälligkeitsanzeige · Bestellbestätigung als PDF |
| **Angebote** (nur Web) | Angebot für ein Katalogmodell oder ein Fahrzeug aus dem Bestand mit Preis und Gültigkeit · Status per Auswahl in der Zeile: *Offen / Abgelehnt / Abgelaufen / Bestellt* · „Bestellt“ fragt die (An-)Zahlung ab und legt die Bestellung an |
| **Ablauf** | **Angebot → Bestellung → Verkauf**: Bestellung *Bestellt → Eingetroffen* (Fahrzeug wird eingebucht und reserviert) *→ Ausgeliefert* (Übergabe mit Restzahlung erzeugt den Kaufvertrag). Rückwärts-Wechsel und Löschen jeweils mit Bestätigung; abhängige Schritte werden sauber zurückgenommen |
| **Verkauf** | Kaufvertrag (KV-…) mit Listen- oder Verhandlungspreis, Bar/Überweisung, optionaler Inzahlungnahme (Fahrzeug geht automatisch als Gebrauchtwagen in den Bestand) · Rohertrag je Verkauf · Storno mit Gegenbuchung · Kaufvertrag als PDF/Druck · Auswertung nach Verkäufer und Modell |
| **Gebrauchtwagen-Ankauf** | Status beim Erfassen: *Angekauft* (sofort bezahlt, Fahrzeug im Bestand) oder *Inzahlungnahme* (Fahrzeug vorgemerkt, wird bei der Übergabe eines verkauften Fahrzeugs über eine Auswahlliste verrechnet → *Verrechnet*) · Storno · Vertrag als PDF/Druck |
| **Fahrzeugkatalog** | Modelle mit Standard-EK und -VK, Marge in € und %, Bestand je Modell · **CSV-Import** (z. B. aus Google Sheets) |
| **Administration** (nur Admins, Web) | *Löschen* in jeder Zeile: zuerst sauber im Ablauf (Storno, Rückbuchung, Verknüpfungen), sonst auf Wunsch endgültig; Kunden/Modelle mit Vorgängen wahlweise deaktivieren oder endgültig löschen · *Datenbank bereinigen* in den Firmendaten (alle Geschäftsdaten; Benutzer, Firmendaten, Kontenplan und wahlweise der Katalog bleiben; Bestätigung mit „LÖSCHEN“ und Passwort) |
| **Kunden** | Kundenstamm plus **Fahrzeughistorie** je Kunde (Käufe, Inzahlungnahmen, Ankäufe, Bestellungen, Reservierungen) |

Fahrzeugpreise werden brutto geführt. Automatische Buchungen in der Finanzbuchhaltung:

* Fahrzeugeingang: *Wareneingang (3400)* + *Vorsteuer (1576)* an *Verbindlichkeiten (1600)*
* Ankauf: *Wareneingang Gebrauchtfahrzeuge (3200)* an *Bank (1200) / Kasse (1000)*
* Verkauf: *Bank/Kasse* (+ *3200* bei Inzahlungnahme) an *Erlöse (8400)* + *USt (1776)*;
  bei Differenzbesteuerung *Erlöse 8190*, USt nur auf die Marge
* Storno: Gegenbuchung

Der USt-Satz für Fahrzeuge ist unter *Administration → Firmendaten* einstellbar (0 = ohne
Umsatzsteuer, z. B. für Rollenspiel-Server).

### Tabelle übernehmen (Fahrzeuge und Preise)

In Google Sheets *Datei → Herunterladen → CSV*, dann im ERP *Stammdaten → Fahrzeugkatalog →
CSV-Import*. Erkannte Spalten: *Hersteller/Marke*, *Modell/Fahrzeug/Name*, *Klasse/Kategorie*,
*EK/Einkaufspreis*, *VK/Verkaufspreis/Preis* (auch `$ 1.250.000` oder `80.000,00`).
Kunden- und Kaufdaten werden bewusst nicht importiert.

## Web-Version (Browser, z. B. auf Render oder Hetzner)

Dieselben Autohaus-Funktionen laufen auch als Website mit Anmeldung, Rollen und gemeinsamer
Datenbank (`erp/web/`). Die Geschäftsregeln sind identisch mit dem Desktop-Programm.

**Render.com (Web Service):**

| Feld | Wert |
|---|---|
| Branch | der Branch mit der Web-Version |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements-web.txt` |
| Start Command | `uvicorn erp.web.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'` |
| Health Check Path | `/healthz` |

Umgebungsvariablen:

| Variable | Wert |
|---|---|
| `ERP_DATABASE_URL` | *Internal Database URL* der Render-PostgreSQL-Datenbank |
| `ERP_SECRET_KEY` | lange Zufallszeichenfolge (Render: *Generate*) |
| `ERP_ADMIN_PASSWORD` | optional: Startpasswort für `admin` |

Alternativ legt `render.yaml` (Render → *New → Blueprint*) Web-Dienst und Datenbank in einem Schritt an.

Erste Anmeldung mit `admin` / `admin` (bzw. `ERP_ADMIN_PASSWORD`). Solange das Standardpasswort
aktiv ist, lässt die Web-Version nichts anderes zu als den Passwortwechsel. Danach unter
*System → Benutzer* die Mitarbeiter mit ihren Rollen anlegen.

**Lokal ausprobieren:**

```bash
pip install -r requirements-web.txt
ERP_INSECURE_COOKIES=1 uvicorn erp.web.app:app --reload     # http://127.0.0.1:8000
```

---

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
| **Stammdaten** | Kunden (inkl. Kreditlimit, Zahlungsziel), Lieferanten, Teile/Zubehör (Preise, USt-Satz, Mindestbestand, Standardlieferant), automatische Nummernkreise |
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

* Windows: `ERPDesk-Setup-1.8.1.exe` (Installer mit Startmenü- und Desktop-Verknüpfung)
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
  services/            Geschäftslogik ohne UI (auth, masterdata, documents, inventory, finance, reports,
                       dealer = Autohaus)
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
