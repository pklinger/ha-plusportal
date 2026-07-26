# PlusPortal → Home Assistant: Standalone-Client + HACS-Integration

## Context

Du willst die vom Messstellenbetreiber gemessenen Stromverbrauchswerte aus dem
PlusPortal (`https://<tenant>.plusportal.de/`) in
Home Assistant nutzen — inklusive centgenauer Abrechnungsprognose. Es gibt bisher **keine**
existierende HA-Integration dafür. Das Projektverzeichnis ist leer (Greenfield, noch kein Git-Repo).

Ich habe die Portal-API bereits vollständig reverse-engineered (Details in Anhang A) — inkl.
Login gegen den echten Account. Die relevanten Erkenntnisse ändern das Design an zwei Stellen:

1. ~~**Datengranularität ist tagesscharf, nicht 15-minütig.**~~ **Korrigiert am 25.07.2026 durch
   die Live-Verifikation — siehe Anhang C.** Der Zähler (iMSys, Quelle
   ROBOTRON) liefert sehr wohl den vollen 15-Minuten-Zählerstandsgang, allerdings über die
   `power`-Serie desselben Kanals, nicht über `consumption`. Die Integration nutzt daher
   15-Minuten-Werte als Basis. Werte kommen mit ~1 Tag Verzug.
2. **Das Portal liefert keinerlei Tarif-/Preisdaten.** Der Account hat exakt ein Feature:
   `energydataview`. `/msw/api/edv/billing` ist leer. Die Kostenprognose muss daher aus
   nutzerkonfigurierten Tarifdaten + gemessenen kWh berechnet werden.
3. **Jeder Messwert trägt ein Qualitätskennzeichen** (`state`): `W` = Wahrer Wert,
   `E` = Ersatzwert (abrechnungsfähig), `V` = Vorläufiger Wert (**nicht** abrechnungsrelevant).
   Für „centgenau" ist das zentral: abgerechnet werden nur `W` und `E`.
   `V`-Werte werden später durch `W` ersetzt → die Integration muss Korrekturen nachziehen.

**Ziel:** Ein Monorepo mit (a) einer eigenständigen, ohne Home Assistant testbaren
Python-Bibliothek `pyplusportal` inkl. CLI, und (b) einer dünnen HACS-Integration darauf.

### Getroffene Entscheidungen

| Thema | Entscheidung |
|---|---|
| Scope | Generisch für beliebige PlusPortal-Mandanten (Mandantennummer/Basis-URL = Config) |
| Kosten | Long-Term-Statistics + eigene Tarif-Optionen → Monats-, Jahres- und Nachzahlungsprognose |
| Struktur | Monorepo: Lib (PyPI) + CLI + `custom_components/plusportal/` |
| Sprache | Code/Docs Englisch, `translations/de.json` + `en.json` |

---

## Architektur

```
ha-plusportal/                           # Repo-Root (HACS erwartet custom_components/ hier)
├── src/pyplusportal/                    # ── Layer 1: reine Datenextraktion, kein HA-Import
│   ├── __init__.py                      #    öffentliche API: PlusPortalClient, Modelle, Fehler
│   ├── client.py                        #    async httpx-Client, Session-Handling, Retry/Relogin
│   ├── models.py                        #    MeterPoint, Taf, Channel, Reading, Overview, Session
│   ├── const.py                         #    Pfade, OBIS-Map, ValueState-Enum (W/E/V)
│   ├── exceptions.py                    #    AuthError, PortalUnavailable, RateLimited, ParseError
│   └── cli.py                           #    `python -m pyplusportal …` (Typer/argparse)
├── tests/                               # ── pytest, läuft komplett ohne HA
│   ├── fixtures/*.json                  #    aufgezeichnete, anonymisierte Responses
│   ├── test_auth.py / test_client.py / test_models.py / test_cli.py
│   └── test_live.py                     #    opt-in `-m live`, echte Credentials aus .env
├── custom_components/plusportal/        # ── Layer 2: dünner HA-Adapter
│   ├── manifest.json                    #    requirements: ["pyplusportal==X.Y.Z"]
│   ├── __init__.py  config_flow.py  coordinator.py
│   ├── sensor.py  statistics.py  cost.py  diagnostics.py  const.py
│   ├── strings.json  translations/{de,en}.json
├── tests_ha/                            # pytest-homeassistant-custom-component
├── brand/icon.png  hacs.json  pyproject.toml  README.md
└── .github/workflows/{ci,hassfest,hacs,release}.yml
```

**Abhängigkeitsrichtung ist einbahnig:** `custom_components/plusportal` → `pyplusportal`.
Die Bibliothek kennt Home Assistant nicht und ist damit isoliert testbar und per CLI
bedienbar (deine Anforderung).

---

## Layer 1 — `pyplusportal`

### Öffentliche API

```python
class PlusPortalClient:
    def __init__(self, base_url: str, username: str, password: str, *,
                 client: httpx.AsyncClient | None = None, timeout: float = 30.0) -> None: ...

    async def login(self) -> Session                       # POST /msw/api/auth
    async def get_session(self) -> Session | None          # GET  /msw/api/public/session
    async def logout(self) -> None                         # GET  /msw/api/auth/logout

    async def get_meter_points(self) -> list[MeterPoint]   # GET  /msw/api/account/getUserItemList
    async def get_overview(self) -> list[Overview]         # GET  /msw/api/edv/getOverview
    async def get_channels(self, mp: MeterPoint) -> list[Channel]      # getDiagramConfigById
    async def get_daily_readings(self, ch: Channel, start: date, end: date) -> list[Reading]
```

Kernmodell:

```python
class ValueState(StrEnum):          # aus dem Portal-Bundle verifiziert
    TRUE_VALUE = "W"                # Wahrer Wert — endgültig, Abrechnungsgrundlage
    SUBSTITUTE = "E"                # Ersatzwert — abrechnungsfähig
    PRELIMINARY = "V"               # Vorläufiger Wert — NICHT abrechnungsrelevant

@dataclass(frozen=True, slots=True)
class Reading:
    start: datetime                 # tz-aware, Europe/Berlin, Tagesbeginn
    value: Decimal                  # kWh — Decimal, nicht float (centgenau!)
    unit: str
    obis: str
    state: ValueState
    @property
    def billable(self) -> bool: return self.state in (W, E)
```

### Wichtige Implementierungsdetails

- **Session:** Cookie `TSGSID`, Gültigkeit ~66 min (`loginValidTo − loginValidFrom`),
  Idle-Timeout. `_request()` fängt 401/403 ab, loggt genau einmal neu ein und wiederholt.
  Kein Passwort-Caching über das Client-Objekt hinaus.
- **Chunking:** `get_daily_readings` zerlegt beliebige Zeiträume in Monatsfenster
  (`period=month` liefert Tageswerte) und setzt sie zusammen — der Aufrufer sieht nur `date`-Grenzen.
- **`Decimal` durchgängig**, `json.loads(..., parse_float=Decimal)`. Bei `float` summieren
  sich Rundungsfehler über ein Jahr Tageswerte sichtbar auf.
- **Defensives Parsen:** Response-Felder sind teils `null`, Feldnamen mehrfach vergeben
  (`bez` existiert doppelt im selben Objekt). Unbekannte Felder werden ignoriert,
  fehlende Pflichtfelder werfen `ParseError` mit Kontext.
- **Höflichkeit gegenüber dem Portal:** `User-Agent` identifiziert das Projekt,
  Concurrency-Limit 1, exponentielles Backoff bei 5xx, kein Polling < 1 h.

### CLI — das isolierte Test-Werkzeug

```
python -m pyplusportal --base-url https://123456.plusportal.de meters
python -m pyplusportal readings --from 2026-06-18 --to 2026-07-25 --format table|json|csv
python -m pyplusportal overview
python -m pyplusportal probe --out tests/fixtures/   # Rohantworten für Fixtures, redigiert
```

Credentials **ausschließlich** aus `PLUSPORTAL_USERNAME` / `PLUSPORTAL_PASSWORD` (bzw. `.env`),
nie als Argument — sonst landen sie in der Shell-History. `probe` redigiert `sessionId`,
Kunden-/Zählernummern und Namen automatisch, damit Fixtures committbar sind.

### Tests

- `respx` mockt httpx gegen die aufgezeichneten Fixtures → schnelle, deterministische Unit-Tests.
- Eigene Testfälle für: Relogin nach Session-Ablauf, Monats-Chunking über Jahresgrenze,
  DST-Wechsel (Ende März/Oktober — Tagesstempel verschieben sich von 22:00Z auf 23:00Z!),
  `V`→`W`-Korrektur, leere Kanäle, HTTP 500 vom Upstream-Proxy.
- `tests/test_live.py` mit `@pytest.mark.live`, in CI übersprungen, lokal gegen den echten
  Account — das ist der Smoke-Test, den du ohne HA fahren kannst.

---

## Layer 2 — HA-Integration `plusportal`

### Config Flow

Schritt 1: **Mandantennummer** (6-stellig, z. B. `123456`) *oder* vollständige Basis-URL,
**Benutzername** (E-Mail oder Kundennummer), **Passwort** → validiert per `login()` +
`get_meter_points()`. `unique_id` = `{mandant}-{user_id}`, verhindert Doppel-Einrichtung.
Reauth-Flow bei `AuthError`.

Options Flow (jederzeit änderbar, Tarifwechsel ohne Neu-Einrichtung):

| Option | Default | Zweck |
|---|---|---|
| Arbeitspreis (ct/kWh) | – | Kostenrechnung |
| Grundpreis (€/Jahr) | – | anteilig pro Tag |
| Monatlicher Abschlag (€) | – | Nachzahlungsprognose |
| Beginn Abrechnungsjahr (TT.MM.) | 01.01. | Prognosezeitraum |
| Update-Intervall (h) | 6 | Portal liefert 1×/Tag |

### Coordinator

`DataUpdateCoordinator`, Default 6 h. Holt pro Refresh:
`get_overview()` + Tageswerte der letzten **21 Tage** (rollierendes Fenster) + laufender Monat.
Das rollierende Fenster ist der Mechanismus, mit dem `V`-Werte nachträglich durch `W`
ersetzt werden — HA überschreibt Statistiken mit identischem `start` idempotent.

### Long-Term Statistics (`statistics.py`)

Der Kern für das Energy-Dashboard. Weil die Werte **rückdatiert** eintreffen, ist ein
normaler `state_class: total_increasing`-Sensor falsch — er würde alles auf den Abrufzeitpunkt
buchen. Stattdessen externe Statistiken:

- `statistic_id = "plusportal:{meter_id}_energy"`, `StatisticMetaData(has_sum=True,
  has_mean=False, source="plusportal", unit_of_measurement=kWh)`
- Beim ersten Lauf: Backfill ab `dtFirstValue` aus `getOverview` (aktuell 18.06.2026 —
  also unkritisch klein).
- Laufender Summenstand wird per `get_last_statistics()` fortgeschrieben; beim Re-Import
  des rollierenden Fensters wird ab dem ersten geänderten Tag neu aufsummiert.
- Zeitstempel sind bereits stundenaligned (lokale Mitternacht) → HA-Anforderung erfüllt,
  aber explizit asserten (DST!).
- Zweite Statistik `plusportal:{meter_id}_cost` in €, sobald Tarif konfiguriert ist →
  Energy-Dashboard zeigt direkt Euro.

### Sensoren (pro Zählpunkt)

| Entity | Einheit | Zweck |
|---|---|---|
| `letzter_tagesverbrauch` | kWh | letzter Tageswert; Attribute: Datum, `state`, `billable` |
| `verbrauch_aktueller_monat` | kWh | aus `getOverview.thisMonthSum` |
| `verbrauch_vormonat` | kWh | `prevMonthSum` |
| `letzte_messung` | timestamp | `dtLastValue` — Basis für „Daten veraltet"-Automationen |
| `datenqualitaet` | % | Anteil `W`-Werte im laufenden Monat (diagnostic) |
| `kosten_aktueller_monat` | € | nur bei konfiguriertem Tarif |
| `prognose_abrechnungsjahr` | € | Hochrechnung Ist + Erwartung Restzeitraum |
| `erwartete_nachzahlung` | € | Prognose − bereits geleistete Abschläge |

Alle unter **einem** `DeviceInfo` je Zählpunkt (Name = Zählernummer, `model` = TAF-Bezeichnung,
`manufacturer` = Mandantenname).

### Kostenlogik (`cost.py`) — bewusst separat und HA-frei testbar

Reine Funktionen über `list[Reading]` + `Tariff`, keine HA-Objekte:
`cost_for_period()`, `project_billing_year()`, `expected_settlement()`.
Grundpreis wird tagesanteilig verrechnet, Hochrechnung über den Mittelwert der
abrechnungsfähigen Tage (nur `W`/`E`), alles in `Decimal` mit `ROUND_HALF_UP` auf Cent.

---

## Umsetzungsreihenfolge

1. **Repo-Setup** — `git init`, `pyproject.toml` (hatch, Python ≥3.12), ruff + mypy strict,
   Design-Doc nach `docs/superpowers/specs/`, MIT-Lizenz, GitHub-Repo anlegen.
2. **Client + Modelle** — TDD gegen Fixtures. Erst `probe` gegen das echte Portal laufen
   lassen (dazu brauche ich einmal Credentials in `.env`) und dabei die in Anhang A offenen
   Parameterfragen empirisch klären.
3. **CLI** — ab hier ist die Datenextraktion vollständig ohne HA nutzbar und der erste
   echte Meilenstein.
4. **PyPI-Release 0.1.0** — nötig, damit `manifest.json` die Lib ziehen kann
   (Trusted Publishing via GitHub Actions).
5. **HA-Grundgerüst** — manifest, config_flow, coordinator, Basissensoren.
6. **Statistics-Backfill** — inkl. Korrekturfenster; Verifikation im Energy-Dashboard.
7. **Kostenmodul** — Tarif-Optionen + Prognosesensoren.
8. **Veröffentlichung** — README mit Screenshots, `hacs.json`, `brand/icon.png`,
   hassfest- und HACS-Action grün, PR gegen `home-assistant/brands`, danach PR gegen die
   HACS-Default-Liste.

---

## Verifikation

**Layer 1 (ohne HA):**
```bash
pytest                                  # Unit-Tests gegen Fixtures, Coverage-Gate ≥90 %
pytest -m live                          # gegen das echte Portal, Credentials aus .env
python -m pyplusportal readings --from 2026-07-01 --to 2026-07-24 --format table
```
Abnahmekriterium: Die Summe der Tageswerte für Juli 2026 stimmt **exakt** mit
`getOverview.thisMonthSum` (`0.757899 kWh`) und der Portal-UI (`0,76 kWh`) überein,
und die Einzelwerte matchen die Tabelle „Letzte Messwerte" (01.07. = 0,03; 10.07. = 0,04 …).

**Layer 2 (in HA):**
- Integration in einer HA-Dev-Instanz (`custom_components/` symlinken oder HACS „Custom repository") einrichten.
- Energy-Dashboard → Verbrauchsquelle `plusportal:…_energy` hinzufügen; Tagesbalken müssen
  den Portal-Werten entsprechen, auch **rückwirkend** ab dem ersten Datentag.
- Korrekturtest: Fixture mit `V`-Wert einspielen, danach denselben Tag als `W` mit anderem
  Wert → Statistik muss sich ändern, nicht duplizieren.
- DST-Test: Zeitraum über den 25.10.2026 (Zeitumstellung) abrufen — 24 Tageswerte,
  keine Lücke, kein doppelter Stempel.
- `hass --script check_config`, `hassfest`, `pytest tests_ha/`.

---

## Anhang A — Reverse-Engineering-Ergebnisse

**Plattform:** PlusPortal ist eine White-Label-Portallösung der **Thüga SmartService GmbH**
(React-SPA, nginx). Mandanten werden über eine sechsstellige Nummer in der Subdomain adressiert.
Backend-Präfix ist **`/msw/api/…`** (nicht `/api/…` — das liefert die SPA-Fallback-HTML aus).

Die JS-Bundles enthalten **vollständige Swagger-2.0-Specs** aller Backend-Services
(≈330 Endpunkte). Extrahiert und im Scratchpad abgelegt — dienen als Referenz, ersetzen
aber keine Verifikation gegen die Live-API.

**Auth**
| Endpoint | Bemerkung |
|---|---|
| `POST /msw/api/auth` | Body `{username, password, mandant?, stayLoggedIn?, custNo?, oneTimePassword?, deviceToken?}` → setzt Cookie `TSGSID` |
| `GET /msw/api/public/session` | `{id, username, sessionId, loginValidFrom, loginValidTo, userGroups, features}`; 403 wenn nicht angemeldet |
| `GET /msw/api/auth/deviceToken` | erzeugt Langzeit-Token → `GET /msw/api/auth/loginWithDeviceToken/{token}` (Option für später) |
| `GET /msw/api/auth/logout` | |

**Daten**
| Endpoint | Liefert |
|---|---|
| `GET /msw/api/account/getUserItemList?page=0` | Zählpunkte: `id`, `bez` (Zählernummer), `category` („Electricity"), `type` („gwa"), `sourceType` („ROBOTRON"), `tafs[]` mit `gwatafnr`/`gwataftype`/`kennzahl[]`/`status` |
| `GET /msw/api/edv/getOverview` | je Zählpunkt: `thisMonthSum`, `prevMonthSum`, `thisMonthAvg`, `dtFirstValue`, `dtLastValue`, `unit`, `kz` (OBIS + Anzeigename) |
| `GET /msw/api/edv/getDiagramConfigById/TAF/{tafNr}/{tafNr}?diagramSubType={userItemId}&sublocId=0` | verfügbare OBIS-Kanäle, `periodType` (hier `["DAY"]`), `begin`/`end`, `lineNr` |
| `GET /msw/api/edv/getDiagramResultList/gwa/{tafNr}/{endMs}?startDate={ms}&obis=1-0:1.8.0&period=month&diagramType=consumption&diagramSubType=100&kenzGrNr=110&statisticType=2&…` | **die Kernabfrage** — `data[0].values[]` mit `date` (ms, lokale Mitternacht), `value`, `unit`, `state`; dazu `statistic[0]` mit `sum`, `average`, `min`, `max1..3` |

Beispielwert: `{"date":1782856800000,"value":0.0312,"unitA":"kWh","state":"W"}` = 01.07.2026, 0,0312 kWh, wahrer Wert.

**Beobachteter Zähler:** Zähler- und Kundennummer sind hier bewusst nicht
festgehalten. userItemId `1000`, OBIS `1-0:1.8.0`
(Wirkarbeit Bezug T0), TAFs: `55789` (TAF-7 Zählerstandsgang), `6532` + `55788` (TAF-1
datensparsam). Historie beginnt 18.06.2026.

**Nicht verfügbar / Sackgassen (bereits geprüft):**
`loadprofile` (leer — kein RLM-Kunde), `billing` (leer), `getSplittedData` / `mewlData` /
`getAllDataForMewl` / `getLastValueForPriorizedObis` (404/500 — gehören zum IoT-Backend,
nicht zur ROBOTRON-Quelle), `getGwaTafDataMeter` (500). Die offizielle
`iot.plusportal.de` OpenAPI v2 existiert, ist aber kontingent-/tokenbasiert und für
Privatkunden nicht zugänglich.

**Offene Punkte, in Schritt 2 empirisch zu klären:** ob `kenzGrNr=110`,
`diagramSubType=100` und `statisticType=2` konstant sind oder aus der Config abgeleitet
werden müssen; ob `period=year` Monatsbuckets liefert; Verhalten bei Zeiträumen > 1 Monat.

## Anhang B — Rechtlicher Rahmen

Die Integration nutzt ausschließlich den Account und die Daten des jeweiligen Nutzers,
mit dessen eigenen Zugangsdaten, über dieselbe API wie die offizielle Web-Oberfläche.
README stellt klar: inoffiziell, keine Verbindung zur Thüga SmartService GmbH oder
zum jeweiligen Versorger, Nutzung auf eigene Verantwortung, Polling-Intervall bewusst konservativ.

---

## Anhang C — Live-Verifikation (25.07.2026)

Ausgeführt gegen eine reale Instanz mit echten Zugangsdaten (hier nicht benannt). Zwei Annahmen des
ursprünglichen Entwurfs haben sich als falsch erwiesen; beide sind hier korrigiert.

### C.1 — 15-Minuten-Daten sind verfügbar (korrigiert Punkt 1 oben)

Derselbe Kanal liefert zwei Serien:

| `diagramType` | Auflösung | Einheit | Zeitstempel |
|---|---|---|---|
| `consumption` | 1 Tag | kWh | **Beginn** des Tages (lokale Mitternacht) |
| `power` | 15 Minuten | kW | **Ende** des Intervalls |

Juli 2026: 2304 Intervalle = exakt 96/Tag. Gegenprobe über drei unabhängige Quellen:

```
Summe 15-Minuten-Werte : 0.757899 kWh
Summe Tageswerte       : 0.757899 kWh
getOverview.thisMonthSum: 0.757899 kWh
```

Ebenso pro Tag (20.07.2026): 96 Intervalle → 0.0351 kWh, Tagesserie sagt 0.0351 kWh.
Qualitätskennzeichen liegen je Intervall vor (Juli: 2267× `W`, 37× `E`).

### C.2 — Zeitstempel der `power`-Serie markieren das Intervall-ENDE

Ein Abruf für exakt den 20.07.2026 liefert 96 Werte von `00:15` bis `21.07. 00:00`.
Wer das als Startzeit liest, verschiebt den gesamten Lastgang um 15 Minuten — ein Fehler,
der in HA-Statistiken nie auffallen, aber jede stündliche Auswertung verfälschen würde.
`Reading.from_interval_api` rechnet deshalb `start = timestamp − Intervalldauer` und
`kWh = kW × Intervalldauer in Stunden`. Die Intervalldauer wird aus dem Abstand
aufeinanderfolgender Punkte abgeleitet, nicht auf 15 Minuten festverdrahtet.

### C.3 — Query-Parameter: welche wirklich nötig sind

| Parameter | Status |
|---|---|
| `diagramSubType` | **zwingend** — ohne ihn antwortet das Portal mit 404 |
| `allData`, `avgOnly`, `maxOnly`, `rawValues` | **zwingend für `power`** — sonst HTTP 500 |
| `kenzGrNr`, `statisticType` | optional, Ergebnis identisch |

### C.4 — `period` begrenzt den Zeitraum nicht

Ein einzelner Aufruf mit `period=month` spannt problemlos über Monatsgrenzen
(18.06.–24.07. → 37 Tageswerte). Das Monats-Chunking bleibt trotzdem bestehen, aber
nur noch als Größenbegrenzung: ein Jahr 15-Minuten-Daten wären ~35.000 Werte in einer
einzigen Antwort.

Wichtiger Nebenbefund: **nur `period=month` liefert Zeitstempel auf lokaler Mitternacht.**
Bei `day`, `week` und `year` kommen sie auf UTC-Mitternacht zurück — in Deutschland also
um 1 bzw. 2 Stunden verschoben. Deshalb wird `period=month` immer verwendet, unabhängig
vom angefragten Zeitraum.

### C.5 — Nicht verfügbar

`loadprofile` (leer — kein RLM-Kunde), `billing` (leer), `getSplittedData` / `mewlData` /
`getAllDataForMewl` / `getLastValueForPriorizedObis` (404/500 — gehören zum IoT-Backend,
nicht zur ROBOTRON-Quelle). Damit bleibt es dabei: **das Portal liefert keine Tarifdaten**,
die Kostenprognose braucht nutzerkonfigurierte Preise.
