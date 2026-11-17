# Einspeisevergütung: Satz eintragen, Erlös sehen

Freigegeben am 15.11.2026.

## Warum

Das Portal veröffentlicht keinen Vergütungssatz, und die Integration bot bisher kein Feld
dafür an. Die README empfahl, den Satz im Energie-Dashboard als festen Preis einzutragen.
Das funktioniert nicht: Home Assistant legt einen Kosten- oder Vergütungssensor nur an, wenn
die Energiequelle eine Entität ist (`homeassistant/components/energy/sensor.py`, geprüft
gegen 2026.2.3: `if not valid_entity_id(config[adapter.stat_energy_key]) … return`). Die
Einspeisereihe ist eine externe Statistik. Ohne eigene Vergütungsstatistik zeigt das
Dashboard für die Einspeisung also nie einen Geldwert.

## Ziele

- Ein optionales Feld „Einspeisevergütung (EUR/kWh)“, bei der Einrichtung und unter
  Konfigurieren, unabhängig vom Arbeitspreis.
- Eine Statistik `<Zähler> feed-in compensation`
  (`plusportal:<konto>_<zähler>_export_compensation`), die im Energie-Dashboard bei
  „Einspeisung ins Netz“ als Gesamtvergütung gewählt wird. Stündlich Einspeisung mal Satz,
  nur abrechenbare Werte.
- Der Satz als Diagnose-Sensor und drei EUR-Sensoren neben den kWh-Sensoren: letzter Tag,
  dieser Monat, Vormonat. Jeder ist sein kWh-Nachbar mal Satz, kaufmännisch auf den Cent
  gerundet.
- Die README-Stellen zum Energie-Dashboard stimmen wieder, für Bezug und Einspeisung.

## Nicht-Ziele

- Die CLI: `pyplusportal cost` bleibt unverändert.
- Zeitabhängige Sätze. Es gilt ein fester Satz für die ganze Historie, wie bei der
  EEG-Vergütung.

## Anti-Ziele

- Die Vergütung wird nie mit Kosten, Kostenprognose oder Abrechnungsprognose verrechnet.
  Sie kommt in der Regel vom Netzbetreiber, getrennt von der Stromrechnung; eine Prognose,
  die sie abzieht, kündigt eine Erstattung an, die der Lieferant nie zahlt.
- Keine Entität, die nur „unbekannt“ sein kann: Vergütungsentitäten gibt es nur, wenn der
  Zähler einspeist **und** ein Satz gesetzt ist (wie PP-HA-011 und PP-HA-031).

## Wege

1. **Eigenes Feld plus eigene Statistik (gewählt).** Der einzige Weg, auf dem das
   Energie-Dashboard die Vergütung einer externen Statistik anzeigen kann.
2. **Nur dokumentieren, dass der Nutzer den Satz im Dashboard einträgt.** Der bisherige
   Stand; scheitert an Home Assistant selbst, siehe oben.
3. **Den Satz ins `Tariff`-Modell der Bibliothek aufnehmen.** `Tariff` verlangt einen
   Arbeitspreis; die Vergütung wäre dann an den Bezugstarif gekoppelt und läge einen
   Schritt neben der Abrechnungsprognose, mit der sie nichts zu tun haben darf.

## Entscheidungen

- Optionsschlüssel `feed_in_price_eur_per_kwh`. Leer heißt „kein Satz“, 0 ist ein Satz.
- Unbrauchbare gespeicherte Werte (keine Zahl, negativ) ergeben „kein Satz“ statt eines
  Fehlers beim Start, wie beim Tarif.
- Entitätsschlüssel `feed_in_price`, `feed_in_compensation_last_day`,
  `feed_in_compensation_this_month`, `feed_in_compensation_previous_month`.
- Die Monatswerte übernehmen die Monatssummen des Portals, wie ihre kWh-Nachbarn. Sie
  können sich bewegen, solange das Portal nachliefert. Die Statistik enthält nur
  abrechenbare Werte.
- Version 0.5.0, Branch `feat/feed-in-compensation`.

## Dem Umsetzenden überlassen

Namen der Funktionen, Aufteilung auf Module, genaue Formulierung der Übersetzungen
(Entitätsnamen höchstens 26 Zeichen, PP-HA-020).
