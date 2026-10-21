# KI-Trading-Bot (Krypto, MetaTrader 5, Paper-Trading)

Ein Bot, der BTC, ETH, SOL und weitere große Coins selbstständig analysiert und
über ein **MetaTrader-5-Demokonto** handelt. Echtes Geld wird nicht eingesetzt:
Der Bot verweigert den Start, wenn er mit einem Echtgeldkonto verbunden ist.

## Fahrplan

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Grundgerüst, MT5-Anbindung, Kursdaten speichern | ✅ |
| 2 | Strategie (Long/Short), 0,5–1 % Risiko pro Trade, nachgezogener Stop-Loss, Backtest | ✅ |
| 3 | Dauerbetrieb auf Windows-VPS, Trade-Protokoll, täglicher Blockchain-Hash | offen |
| 4 | Lernen: Parameter anhand der Ergebnisse optimieren | später |

## Einrichtung (Windows)

1. **MetaTrader 5** installieren und bei einem Broker, der Krypto-CFDs anbietet,
   ein **Demokonto** eröffnen. In MT5 unter *Extras → Optionen → Expert Advisors*
   „Algorithmischen Handel erlauben“ aktivieren.
2. **Python 3.11 oder neuer** installieren (bei der Installation „Add to PATH“ ankreuzen).
3. In diesem Ordner eine Eingabeaufforderung öffnen und ausführen:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   copy config.example.yaml config.yaml
   ```
4. In `.env` Login, Passwort und Server des Demokontos eintragen.

## Benutzung

```
python -m tradingbot check   # Verbindung prüfen, Krypto-Symbole des Brokers anzeigen
python -m tradingbot fetch   # Kursdaten aller Coins abrufen und in data/market.db speichern
python -m tradingbot backtest   # Strategie an den gespeicherten Kursdaten testen
```

Die Symbolnamen heißen bei jedem Broker etwas anders (z. B. `BTCUSD`, `BTCUSD.a`).
`check` zeigt dir die richtigen Namen, die du dann unter `symbols` in `config.yaml` einträgst.

## Strategie (Schritt 2)

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

**Backtest:** Einstieg zur Eröffnung der nächsten Kerze, Stop-Ausführung bei Kurslücken
zum schlechteren Kurs, Kosten pro Seite (`fee_pct`). Ausgabe: Anzahl Trades, Trefferquote,
Rendite, maximaler Rückgang (Max DD) und Profitfaktor (PF, über 1 = profitabel).

Ein Backtest ist kein Versprechen für die Zukunft. Erst wenn die Strategie auf mehreren
Coins und Zeiträumen stabil ist, lohnt sich der Dauerbetrieb auf dem Demokonto.

## Tests

```
pytest
```

Die Tests laufen auch ohne MetaTrader 5 (z. B. unter Linux oder macOS).
