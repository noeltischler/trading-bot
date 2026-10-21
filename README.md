# KI-Trading-Bot (Krypto, Paper-Trading)

Ein Bot, der BTC, ETH, SOL und weitere große Coins selbstständig analysiert und
auf einem **eigenen Übungskonto** handelt. Die Kurse kommen live von einer echten
Börse (Standard: Kraken), Orders werden aber nur simuliert. Es wird kein
Börsenkonto und kein echtes Geld gebraucht.

## Fahrplan

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Grundgerüst, Kursdaten laden und speichern | ✅ |
| 2 | Strategie (Long/Short), 0,5–1 % Risiko pro Trade, nachgezogener Stop-Loss, Backtest | ✅ |
| 2b | Live-Übungskonto mit echten Kursen (`run`, `status`) | ✅ |
| 3 | Dauerbetrieb auf einem Server, Dashboard, täglicher Blockchain-Nachweis | offen |
| 4 | Lernen: Parameter anhand der Ergebnisse optimieren | später |

## Einrichtung

Funktioniert unter Windows, macOS und Linux.

1. **Python 3.11 oder neuer** installieren (unter Windows „Add Python to PATH“ ankreuzen).
2. Den Code herunterladen (grüner Knopf „Code“ → „Download ZIP“) und entpacken.
3. Im Ordner ein Terminal öffnen und ausführen:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Windows)
   source .venv/bin/activate       (macOS/Linux)
   pip install -r requirements.txt
   copy config.example.yaml config.yaml   (macOS/Linux: cp statt copy)
   ```

## Benutzung

```
python -m tradingbot fetch      # Kursverlauf der letzten 90 Tage laden
python -m tradingbot backtest   # Strategie an diesem Verlauf testen
python -m tradingbot run        # Bot dauerhaft laufen lassen (Strg+C beendet)
python -m tradingbot status     # Kontostand, offene Positionen, letzte Trades
```

`run` prüft alle 30 Sekunden, ob eine neue Kerze abgeschlossen ist. Alles, was der
Bot tut, steht im Terminal und in `bot.log`. Übungskonto und Trades liegen in
`data/paper.db` und bleiben bei einem Neustart erhalten.

## Strategie

**Einstieg:** Trendfolge auf Kerzenschluss.
- *Long*, wenn die schnelle EMA (20) über die langsame EMA (50) kreuzt, der Kurs über
  der Trend-EMA (200) liegt und der RSI unter 70 ist.
- *Short* spiegelverkehrt (Kreuzung nach unten, Kurs unter EMA 200, RSI über 30).
- Ein Gegensignal schließt die offene Position und dreht sie.

**Risiko:**
- Anfangs-Stop-Loss `stop_pct` vom Einstieg entfernt.
- Positionsgröße so, dass der Stop genau `risk_per_trade_pct` (0,5–1 %) des Kontos kostet.
- Nach jeder Kerze wird der Stop `trailing_stop_pct` hinter dem besten Kurs nachgezogen,
  aber nie zurückgesetzt. Bei Shorts entsprechend nach unten.

**Backtest und Live-Betrieb nutzen dieselben Regeln** (`tradingbot/position.py`):
Einstieg zur Eröffnung der nächsten Kerze, Stop-Ausführung bei Kurslücken zum
schlechteren Kurs, Kosten pro Seite (`fee_pct`). Kennzahlen: Trades, Trefferquote,
Rendite, maximaler Rückgang (Max DD) und Profitfaktor (PF, über 1 = profitabel).

Ein Backtest ist kein Versprechen für die Zukunft. Erst wenn die Strategie auf mehreren
Coins und Zeiträumen stabil ist, lohnt sich der Schritt zu echtem Geld.

## Andere Börse

In `config.yaml` `exchange` ändern (z. B. `bitvavo`, `coinbase`, `kraken`) und die
Symbole anpassen. Möglich sind alle Börsen, die [ccxt](https://github.com/ccxt/ccxt)
unterstützt. Manche Börsen liefern nur einen kurzen Kursverlauf; `fetch` zeigt an,
ab wann Daten vorhanden sind.

## Tests

```
pytest
```
