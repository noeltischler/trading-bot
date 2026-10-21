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

1. **Python 3.13** installieren (unter Windows „Add Python to PATH“ ankreuzen).
   Python 3.14/3.15 bitte nicht: Für einige Pakete gibt es dort noch keine fertigen Downloads.
   Auf dem Mac danach im Ordner „Python 3.13“ einmal „Install Certificates.command“ öffnen.
2. Den Code herunterladen (grüner Knopf „Code“ → „Download ZIP“) und entpacken.
3. Im Ordner ein Terminal öffnen und ausführen:
   ```
   python3.13 -m venv .venv
   .venv\Scripts\activate          (Windows)
   source .venv/bin/activate       (macOS/Linux)
   pip install -r requirements.txt
   copy config.example.yaml config.yaml   (macOS/Linux: cp statt copy)
   ```

## Benutzung

```
python -m tradingbot fetch      # Kursverlauf der letzten 365 Tage laden
python -m tradingbot backtest   # Strategie an diesem Verlauf testen
python -m tradingbot optimize   # Zeitrahmen, Stops und Trailing automatisch durchtesten
python -m tradingbot run        # Bot dauerhaft laufen lassen (Strg+C beendet)
python -m tradingbot status     # Kontostand, offene Positionen, letzte Trades
```

`run` prüft alle 30 Sekunden, ob eine neue Kerze abgeschlossen ist. Alles, was der
Bot tut, steht im Terminal und in `bot.log`. Übungskonto und Trades liegen in
`data/paper.db` und bleiben bei einem Neustart erhalten.

## Strategie

**Einstieg** (auf Kerzenschluss, wählbar mit `strategy.kind`):
- `ema` (Trendfolge): *Long*, wenn die schnelle EMA über die langsame kreuzt, der Kurs
  über der Trend-EMA (200) liegt und der RSI unter 70 ist.
- `breakout` (Ausbruch): *Long*, wenn der Schlusskurs das Hoch der letzten `lookback`
  Kerzen überschreitet und der Kurs über der Trend-EMA liegt.
- `meanrev` (Gegenbewegung): *Long*, wenn der RSI unter `rsi_low` fällt, während der
  Kurs über der Trend-EMA liegt (Rücksetzer im Aufwärtstrend).
- *Short* jeweils spiegelverkehrt. Ein Gegensignal schließt die offene Position und dreht sie.

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

## Optimieren

`optimize` teilt den Kursverlauf jedes Coins: Auf den ersten zwei Dritteln sucht es
die beste Kombination aus Zeitrahmen (15m/1h/4h), Strategie, Stop und Trailing, auf
dem letzten Drittel wird nur geprüft. Eine Kombination taugt erst, wenn sie **auch
im Test** im Plus ist (Test % > 0, PF > 1) und das bei möglichst allen Coins.

Mit `--fee 0.04` lässt sich prüfen, wie die Ergebnisse bei niedrigeren Gebühren
aussehen (z. B. Futures statt Spot).

Um eine Kombination zu übernehmen, in `config.yaml` `timeframe`, `strategy.kind` und
die passenden Werte (`fast`/`slow`, `lookback` oder `rsi_low`/`rsi_high`),
`risk.stop_pct` und `risk.trailing_stop_pct` anpassen. Nach einer
Änderung von `timeframe` einmal `fetch` ausführen.

## Andere Börse

In `config.yaml` `exchange` ändern (z. B. `bitvavo`, `coinbase`, `kraken`) und die
Symbole anpassen. Möglich sind alle Börsen, die [ccxt](https://github.com/ccxt/ccxt)
unterstützt. Manche Börsen liefern nur einen kurzen Kursverlauf; `fetch` zeigt an,
ab wann Daten vorhanden sind.

## Tests

```
pytest
```
