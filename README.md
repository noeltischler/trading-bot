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
| 3 | Dauerbetrieb auf einem Server, Dashboard, täglicher Blockchain-Nachweis | ✅ |
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
python -m tradingbot dashboard  # Dashboard im Browser: http://localhost:8080
python -m tradingbot verify     # Trade-Protokoll gegen die Tagesnachweise prüfen
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
- `wedge` (Keil-/Dreieck-Ausbruch): Durch die Hoch- und Tiefpunkte der letzten `lookback`
  Kerzen wird je eine Trendlinie gelegt. Laufen sie zusammen, gibt es *Long*, wenn der
  Schlusskurs über die fallende obere Linie ausbricht (fallender Keil, absteigendes oder
  symmetrisches Dreieck), und *Short* beim Bruch der steigenden unteren Linie.
- `fvg` (Fair Value Gap, Smart Money Concepts): Eine Lücke entsteht, wenn das Tief einer
  Kerze über dem Hoch der vorletzten liegt (mindestens `fvg_min_atr` × ATR). *Long*, wenn
  der Kurs innerhalb von `lookback` Kerzen in die Lücke zurückläuft und über ihr schließt.
- `supertrend` (Trendfolge): *Long*, wenn der Supertrend (ATR `st_period`, Faktor
  `st_factor`) auf Aufwärtstrend dreht.
- `fib` (Fibonacci „Golden Pocket“): Rücksetzer der letzten Aufwärtsbewegung (`lookback`
  Kerzen) bis unter 61,8 %, Schluss aber über 78,6 % → *Long*.
- `sweep` (Liquiditäts-Sweep, „Turtle Soup“): Kerze sticht unter das Tief der letzten
  `lookback` Kerzen und schließt wieder darüber → *Long*.
- `squeeze` (Bollinger-Squeeze): Nach der engsten Bandbreite seit `lookback` Kerzen
  *Long* beim Ausbruch über das obere Bollinger-Band.
- `meanrev` mit `rsi_period: 2`, `rsi_low: 10`, `rsi_high: 90` ist die bekannte
  RSI-2-Strategie nach Larry Connors.
- **Bestätigung durch Indikatoren** (für jede Strategie, 0 = aus): `ema_filter` (Long nur
  über dieser EMA), `adx_min` (Mindest-Trendstärke), `macd_confirm`, `rsi_confirm`,
  `volume_ratio` (Volumen über dem Durchschnitt) und `btc_filter`: Long nur, wenn
  Bitcoin über seiner EMA dieser Länge liegt, Short nur darunter. Altcoins folgen
  meist Bitcoin, deshalb hilft das gegen Trades gegen den Gesamtmarkt.
- `fvg`, `fib`, `sweep` und `squeeze` handeln mit `use_trend: true` nur mit dem Trend (Trend-EMA).
- Mehrere Strategien zusammen: unter `strategy:` statt eines Blocks eine Liste von
  Blöcken eintragen (siehe `config.empfohlen.yaml`). Es zählt das Signal der ersten
  Strategie, die eines hat.
- *Short* jeweils spiegelverkehrt. Ein Gegensignal schließt die offene Position und dreht sie.

**Risiko:**
- Anfangs-Stop-Loss `stop_pct` vom Einstieg entfernt.
- Positionsgröße so, dass der Stop genau `risk_per_trade_pct` (0,5–1 %) des Kontos kostet.
- Nach jeder Kerze wird der Stop `trailing_stop_pct` hinter dem besten Kurs nachgezogen,
  aber nie zurückgesetzt. Bei Shorts entsprechend nach unten.

**Sicherheitsregeln** (unter `risk:`, 0 = aus):
- `stop_atr` / `trail_atr`: Stop und Trailing in ATR statt in %. Der Abstand passt sich
  der Schwankung des Coins an (DOGE schwankt stärker als BTC).
- `take_profit_r`: Gewinnziel als Vielfaches des Stop-Abstands (2 = doppelt so weit).
- `max_position_pct`: Positionswert höchstens so viel % vom Konto, auch bei engem Stop.
- `daily_loss_limit_pct`: Nach so viel % Verlust an einem Tag keine neuen Trades bis
  Mitternacht (UTC). Offene Positionen laufen mit ihrem Stop weiter.
- `drawdown_brake_pct`: Liegt das Konto so viel % unter seinem Höchststand, wird das
  Risiko pro Trade halbiert, bis es sich erholt hat.

- `event_pause_hours`: So viele Stunden vor und nach wichtigen Wirtschaftsterminen
  (US-Zinsentscheid, US-Inflation, US-Arbeitsmarkt, EZB-Zinsentscheid) keine neuen
  Trades. Die Termine stehen in `tradingbot/events.py` und müssen einmal im Jahr
  ergänzt werden (der Bot schreibt eine Warnung ins Protokoll, wenn die Liste endet).

**Marktstimmung:**
- Fear & Greed Index (alternative.me, 0 = extreme Angst, 100 = extreme Gier). `fetch`
  lädt den Verlauf für Backtests, live lädt der Bot ihn stündlich. Filter je Strategie:
  `fg_mode: contrarian` (bei extremer Angst kein Short, bei extremer Gier kein Long)
  oder `follow` (umgekehrt), Grenzen `fg_low` / `fg_high`. Es zählt immer der Wert des
  Vortags. Ist der Index nicht abrufbar, ist der Filter wirkungslos.
- Nachrichtenlage: Der Bot lädt alle 15 Minuten Schlagzeilen (CoinDesk, Cointelegraph,
  CNBC, Federal Reserve, EZB, tagesschau, BTC-ECHO), bewertet sie mit einer Stichwortliste
  (−3 bis +3) und zeigt sie im Dashboard. **Nur zur Beobachtung**: Die Nachrichten
  beeinflussen keine Trades, bis die gesammelten Daten zeigen, dass sie helfen.

**Handy-Benachrichtigungen** (`notify: true`, Standard): Bei jedem eröffneten und
geschlossenen Trade schickt der Bot eine Mitteilung über [ntfy](https://ntfy.sh)
(Coin, Long/Short, Kurs, Stop bzw. Ergebnis). Der Kanalname wird beim ersten Start
zufällig erzeugt und steht im Dashboard unter „Benachrichtigungen aufs Handy“.
In der ntfy-App diesen Kanal abonnieren, fertig.

`backtest` zeigt zusätzlich das **Kelly-Kriterium** (Kelly %): den rechnerisch besten
Einsatz aus Trefferquote und Gewinn/Verlust-Verhältnis. Negativ heißt: kein Vorteil.
Positiv: höchstens ein Viertel davon als `risk_per_trade_pct` nehmen.

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
aussehen (z. B. Futures statt Spot). `--kind wedge` testet nur eine Strategie,
`--exits` probiert zusätzlich Gewinnziele (2R, 3R) und ATR-Stops aus,
`--per-day 1` zeigt nur Kombinationen mit mindestens einem Trade pro Tag (alle Coins
zusammen). Mehr Coins unter `symbols` bedeuten bei gleicher Strategie mehr Trades.

Um eine Kombination zu übernehmen, in `config.yaml` `timeframe`, `strategy.kind` und
die passenden Werte (`fast`/`slow`, `lookback`, `rsi_low`/`rsi_high`, `fvg_min_atr`
oder `st_factor`), `risk.stop_pct` und `risk.trailing_stop_pct` (bzw. `stop_atr`,
`trail_atr`, `take_profit_r`) anpassen. Nach einer
Änderung von `timeframe` einmal `fetch` ausführen.

## Dauerbetrieb auf einem Server

Getestet für einen frischen Ubuntu-24.04-Server (z. B. Hetzner Cloud, kleinster Typ).

1. Auf dem Mac (Bot vorher mit Strg+C stoppen), IP-Adresse einsetzen:
   ```
   rsync -av --exclude .venv ~/Downloads/trading-bot-main/ root@IP-ADRESSE:/root/trading-bot/
   ```
2. Auf den Server einloggen und das Skript starten:
   ```
   ssh root@IP-ADRESSE
   bash /root/trading-bot/deploy/setup_server.sh
   ```
3. Das Skript zeigt am Ende die Dashboard-Adresse (`https://…sslip.io`) und das Passwort.
   Im Reiter **Chart** siehst du je Coin die Kerzen, den aktuellen Kurs, den Stop und
   wo der Bot ein- und ausgestiegen ist.

Bot und Dashboard laufen dann als Dienste, starten nach einem Neustart von selbst und
nach Abstürzen nach 30 Sekunden neu. Übungskonto und Kursdaten werden beim ersten Mal
vom Mac übernommen. Zum Aktualisieren den neuen Code genauso hochladen und das Skript
erneut ausführen; Übungskonto, `config.yaml` und Passwort bleiben erhalten.

## Blockchain-Nachweis

Der Bot bildet jeden Tag einen SHA-256-Fingerabdruck über alle bisherigen Trades,
verkettet mit dem Fingerabdruck des Vortags. `verify` erkennt, wenn ein Trade
nachträglich verändert wurde. Optional kommt der Fingerabdruck an Tagen mit Trades
auf die Ethereum-Blockchain (Testnetz Sepolia, kostenlos):

1. Eine **neue, nur dafür genutzte** Wallet anlegen (z. B. in MetaMask) und über
   einen Sepolia-Faucet etwas Test-ETH holen.
2. `pip install web3` (auf dem Server: `/opt/trading-bot/.venv/bin/pip install web3`).
3. In `/etc/tradingbot.env` (Server) bzw. als Umgebungsvariablen (Mac) eintragen:
   ```
   ANCHOR_RPC_URL=https://ethereum-sepolia-rpc.publicnode.com
   ANCHOR_PRIVATE_KEY=0x…
   ```
   und den Bot neu starten. Das Dashboard verlinkt dann jede Transaktion.

## Andere Börse

In `config.yaml` `exchange` ändern (z. B. `bitvavo`, `coinbase`, `kraken`) und die
Symbole anpassen. Möglich sind alle Börsen, die [ccxt](https://github.com/ccxt/ccxt)
unterstützt. Manche Börsen liefern nur einen kurzen Kursverlauf; `fetch` zeigt an,
ab wann Daten vorhanden sind.

## Tests

```
pytest
```
