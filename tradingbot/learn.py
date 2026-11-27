"""Selbst lernen, mit Sicherheitsregeln.

Ablauf (täglich per Zeitplan: python -m tradingbot learn):
1. Einmal pro Woche testet der Bot Varianten seiner aktuellen Einstellung auf dem
   letzten Jahr: Stop, Trailing, Seitwärts-Erkennung für RSI-2, je eine Strategie
   weglassen. Gerechnet wird getrennt nach Lernzeitraum (erste 2/3) und Testzeitraum
   (letztes 1/3).
2. Eine Variante wird nur übernommen, wenn sie in BEIDEN Zeiträumen klar besser ist
   als die laufende Einstellung, und zwar zuerst nur fürs Übungskonto.
3. Läuft sie dort mindestens 14 Tage besser als die Echtgeld-Einstellung, schlägt
   der Bot sie per Telegram fürs Echtgeld vor. Übernommen wird erst nach Bestätigung
   im Dashboard.
4. Jede Änderung steht im Verlauf und lässt sich im Dashboard zurückdrehen.

Gespeichert wird im Übungskonto (state):
  learn:paper     Einstellung fürs Übungskonto (fehlt = config.yaml)
  learn:live      Einstellung fürs Echtgeld (fehlt = config.yaml)
  learn:proposal  Vorschlag fürs Echtgeld, wartet auf Bestätigung
  learn:rejected  abgelehnter Vorschlag (wird nicht erneut vorgeschlagen)
  learn:history   Verlauf (JSON-Liste)
  learn:last_opt  Zeitpunkt der letzten Wochen-Prüfung
  learn:last      Ergebnis der letzten Prüfung (für das Dashboard)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time

import pandas as pd

from tradingbot.backtest import run as run_backtest
from tradingbot.config import Config
from tradingbot.strategy import StrategyParams, label as strategy_label, signals, with_btc

log = logging.getLogger("tradingbot")

WEEK = 7 * 86400
PAPER_DAYS = 14          # so lange muss eine neue Einstellung im Übungskonto laufen
MARGIN_PCT = 1.0         # mindestens so viel % (vom Startkapital) besser, in beiden Zeiträumen
MIN_TEST_PF = 1.1
ADX_CHOICES = (0.0, 20.0, 25.0)
STOP_RANGE = (1.5, 6.0)
TRAIL_RANGE = (2.0, 10.0)
HISTORY_MAX = 50


# ---- Einstellungen ----------------------------------------------------------

def _as_tuple(strategy) -> tuple[StrategyParams, ...]:
    return tuple(strategy) if isinstance(strategy, (list, tuple)) else (strategy,)


def settings_of(cfg: Config) -> dict:
    """Die lernbaren Teile einer Config als JSON-taugliches dict."""
    default = StrategyParams()
    strategies = [{k: v for k, v in dataclasses.asdict(sp).items() if k == "kind" or v != getattr(default, k)}
                  for sp in _as_tuple(cfg.strategy)]
    s = {"strategy": strategies, "stop_pct": cfg.risk.stop_pct, "trailing_stop_pct": cfg.risk.trailing_stop_pct}
    s["label"] = describe(s)
    return s


def describe(s: dict) -> str:
    strategies = tuple(StrategyParams(**x) for x in s["strategy"])
    return (f"{strategy_label(strategies)} · Stop {s['stop_pct']:g} % · Trail {s['trailing_stop_pct']:g} %"
            .replace(".", ","))


def same(a: dict | None, b: dict | None) -> bool:
    def core(s):
        return None if s is None else json.dumps({k: s[k] for k in ("strategy", "stop_pct", "trailing_stop_pct")}, sort_keys=True)
    return core(a) == core(b)


def apply(cfg: Config, s: dict | None) -> Config:
    """Config mit gelernter Einstellung (None = unverändert)."""
    if not s:
        return cfg
    strategies = tuple(StrategyParams(**x) for x in s["strategy"])
    risk = dataclasses.replace(cfg.risk, stop_pct=float(s["stop_pct"]), trailing_stop_pct=float(s["trailing_stop_pct"]))
    return dataclasses.replace(cfg, strategy=strategies if len(strategies) > 1 else strategies[0], risk=risk)


def _get(account, key: str):
    raw = account.get_state(f"learn:{key}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _put(account, key: str, value) -> None:
    if value is None:
        with account.conn:
            account.conn.execute("DELETE FROM state WHERE key = ?", (f"learn:{key}",))
    else:
        account.set_state(f"learn:{key}", json.dumps(value))


def effective(cfg: Config, account, slot: str) -> Config:
    """Config, mit der Übungskonto (slot='paper') bzw. Echtgeld (slot='live') gerade handeln."""
    return apply(cfg, _get(account, slot))


def effective_label(cfg: Config, account, slot: str) -> str:
    s = _get(account, slot)
    return s["label"] if s else settings_of(cfg)["label"] + " (Grundeinstellung)"


def history(account) -> list[dict]:
    return _get(account, "history") or []


def _log(account, text: str, now: float) -> None:
    h = history(account) + [{"time": int(now), "text": text}]
    _put(account, "history", h[-HISTORY_MAX:])
    log.info("Lernen: %s", text)


# ---- Varianten und Bewertung -----------------------------------------------

def _clamp(v: float, lo_hi: tuple[float, float]) -> float:
    return min(max(round(v * 2) / 2, lo_hi[0]), lo_hi[1])


def candidates(s: dict) -> list[dict]:
    """Varianten rund um die aktuelle Einstellung (die aktuelle selbst ist dabei)."""
    stop, trail = float(s["stop_pct"]), float(s["trailing_stop_pct"])
    stops = sorted({_clamp(stop * f, STOP_RANGE) for f in (0.8, 1.0, 1.25)})
    trails = sorted({_clamp(trail + d, TRAIL_RANGE) for d in (-1.0, 0.0, 1.0)})
    has_meanrev = any(x["kind"] == "meanrev" for x in s["strategy"])
    adxs = ADX_CHOICES if has_meanrev else (None,)
    out, seen = [], set()

    def add(strategies, st, tr):
        c = {"strategy": strategies, "stop_pct": st, "trailing_stop_pct": tr}
        key = json.dumps(c, sort_keys=True)
        if key not in seen:
            seen.add(key)
            c["label"] = describe(c)
            out.append(c)

    add(s["strategy"], stop, trail)
    for a in adxs:
        strategies = [dict(x, range_adx=a) if a is not None and x["kind"] == "meanrev" else dict(x) for x in s["strategy"]]
        strategies = [{k: v for k, v in x.items() if not (k == "range_adx" and not v)} for x in strategies]
        for st in stops:
            for tr in trails:
                add(strategies, st, tr)
    if len(s["strategy"]) > 1:  # je eine Strategie weglassen
        for i in range(len(s["strategy"])):
            add([x for j, x in enumerate(s["strategy"]) if j != i], stop, trail)
    return out


def evaluate(cfg: Config, frames: dict[str, pd.DataFrame], s: dict, split: int, start: int = 0,
             cache: dict | None = None) -> dict:
    """Ergebnis einer Einstellung über alle Coins, getrennt vor/ab split (Unix-Sekunden).
    Gezählt werden nur Trades, die ab start eröffnet wurden."""
    c = apply(cfg, s)
    bp = c.backtest_params()
    cache = {} if cache is None else cache
    skey = json.dumps(s["strategy"], sort_keys=True)
    train, test = [], []
    for coin, df in frames.items():
        if (coin, skey) not in cache:
            cache[(coin, skey)] = signals(df, c.strategy)
        for t in run_backtest(df, bp, sig=cache[(coin, skey)]).trades:
            if t.entry_time >= start:
                (train if t.entry_time < split else test).append(t.pnl)

    def pf(p):
        loss = -sum(x for x in p if x < 0)
        return round(sum(x for x in p if x > 0) / loss, 2) if loss else None

    return {"train": round(sum(train), 2), "test": round(sum(test), 2), "train_trades": len(train),
            "test_trades": len(test), "test_pf": pf(test)}


def pick(results: list[tuple[dict, dict]], margin: float) -> tuple[dict, dict] | None:
    """Beste Variante, die in Lern- UND Testzeitraum mindestens margin $ besser ist als
    die aktuelle (results[0]) und im Test einen Profitfaktor über MIN_TEST_PF hat."""
    (_, cur), best, score = results[0], None, 0.0
    for s, r in results[1:]:
        gain = min(r["train"] - cur["train"], r["test"] - cur["test"])
        if (gain >= margin and (r["test_pf"] or 0) >= MIN_TEST_PF
                and r["test_trades"] >= 0.6 * cur["test_trades"] and gain > score):
            best, score = (s, r), gain
    return best


# ---- Kursdaten --------------------------------------------------------------

def _resample(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    g = df.assign(time=df["time"] // seconds * seconds).groupby("time", as_index=False)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"))
    return out


def load_frames(cfg: Config, days: int = 365, market=None) -> dict[str, pd.DataFrame]:
    """Kerzen des letzten Jahres für alle Coins. Kann die Börse den Zeitrahmen nicht
    (Coinbase hat kein 4h), werden 1h-Kerzen geladen und zusammengefasst."""
    from tradingbot.market import Market

    market = market or Market(cfg.history_exchange)
    seconds = market.timeframe_seconds(cfg.timeframe)
    since_ms = int((time.time() - days * 86400) * 1000)
    frames = {}
    for coin, symbol in cfg.symbols.items():
        try:
            df = market.candles(symbol, cfg.timeframe, since_ms=since_ms)
        except Exception:
            df = _resample(market.candles(symbol, "1h", since_ms=since_ms), seconds)
        frames[coin] = df[df["time"] + seconds <= time.time()].reset_index(drop=True)  # nur fertige Kerzen
    if "BTC" in frames:
        frames = {coin: with_btc(df, frames["BTC"]) for coin, df in frames.items()}
    return frames


# ---- Ablauf -----------------------------------------------------------------

def weekly(cfg: Config, account, frames: dict[str, pd.DataFrame], now: float, notify_fn=None) -> str:
    """Varianten testen und eine klar bessere fürs Übungskonto übernehmen."""
    current = _get(account, "paper") or settings_of(cfg)
    t0 = max(int(df["time"].iloc[0]) for df in frames.values())
    t1 = min(int(df["time"].iloc[-1]) for df in frames.values())
    start = t0 + 60 * 86400                   # Anlaufzeit für die Indikatoren
    split = start + int((t1 - start) * 2 / 3)
    cache: dict = {}
    results = [(c, evaluate(cfg, frames, c, split, start, cache)) for c in candidates(current)]
    margin = MARGIN_PCT / 100 * cfg.start_equity
    best = pick(results, margin)
    cur = results[0][1]
    _put(account, "last_opt", int(now))
    if best is None:
        text = (f"Wochen-Prüfung: {len(results) - 1} Varianten getestet, keine klar besser. "
                f"Aktuell: Lernzeitraum {cur['train']:+.0f} $, Test {cur['test']:+.0f} $.")
        _put(account, "last", {"time": int(now), "text": text})
        _log(account, text, now)
        return text
    s, r = best
    new = dict(s, since=int(now), prev=current, result=r, before=cur)
    _put(account, "paper", new)
    _put(account, "proposal", None)  # alter Vorschlag passt nicht mehr zur neuen Einstellung
    text = (f"Übungskonto nutzt jetzt: {s['label']}. Lernzeitraum {cur['train']:+.0f} → {r['train']:+.0f} $, "
            f"Test {cur['test']:+.0f} → {r['test']:+.0f} $ (PF {r['test_pf']}). Echtgeld bleibt unverändert.")
    _put(account, "last", {"time": int(now), "text": text})
    _log(account, text, now)
    if notify_fn:
        notify_fn(account, "Bot hat dazugelernt (Übungskonto)", text, ["brain"])
    return text


def promotion(cfg: Config, account, frames: dict[str, pd.DataFrame], now: float, notify_fn=None) -> str | None:
    """Läuft die Übungskonto-Einstellung seit mindestens PAPER_DAYS besser als die
    Echtgeld-Einstellung, wird sie fürs Echtgeld vorgeschlagen."""
    paper, live = _get(account, "paper"), _get(account, "live") or settings_of(cfg)
    if not paper or same(paper, live) or same(paper, _get(account, "proposal")) or same(paper, _get(account, "rejected")):
        return None
    since = int(paper.get("since", now))
    if now - since < PAPER_DAYS * 86400:
        return None
    rp = evaluate(cfg, frames, paper, split=int(now) + 1, start=since)["train"]
    rl = evaluate(cfg, frames, live, split=int(now) + 1, start=since)["train"]
    days = int((now - since) // 86400)
    if rp <= rl or rp <= 0:
        return None
    _put(account, "proposal", dict(paper, proposed=int(now), paper_pnl=rp, live_pnl=rl))
    text = (f"Seit {days} Tagen läuft „{paper['label']}“ im Übungskonto besser als die Echtgeld-Einstellung "
            f"({rp:+.0f} $ statt {rl:+.0f} $ in derselben Zeit). Vorschlag: fürs Echtgeld übernehmen. "
            "Bestätigen oder ablehnen im Dashboard (Übersicht, Karte „Selbst lernen“).")
    _log(account, text, now)
    if notify_fn:
        notify_fn(account, "Vorschlag fürs Echtgeld", text, ["bulb"])
    return text


def daily(cfg: Config, account, frames_fn, now: float | None = None, force: bool = False, notify_fn=None) -> list[str]:
    """Einmal täglich: Echtgeld-Vorschlag prüfen, einmal pro Woche neu testen.
    frames_fn: Funktion, die die Kursdaten lädt (nur wenn nötig aufgerufen)."""
    now = time.time() if now is None else now
    out, frames = [], None
    last = int(account.get_state("learn:last_opt") or 0)
    paper = _get(account, "paper")
    need_promo = paper is not None and now - int(paper.get("since", now)) >= PAPER_DAYS * 86400
    if force or now - last >= WEEK - 3600 or need_promo:
        frames = frames_fn()
    if need_promo:
        msg = promotion(cfg, account, frames, now, notify_fn)
        if msg:
            out.append(msg)
    if force or now - last >= WEEK - 3600:
        out.append(weekly(cfg, account, frames, now, notify_fn))
    return out


# ---- Aktionen aus dem Dashboard ---------------------------------------------

def accept(account, now: float | None = None) -> str | None:
    """Vorschlag fürs Echtgeld übernehmen."""
    now = time.time() if now is None else now
    p = _get(account, "proposal")
    if not p:
        return None
    old = _get(account, "live")
    _put(account, "live", {k: v for k, v in p.items() if k in ("strategy", "stop_pct", "trailing_stop_pct", "label")}
         | {"since": int(now), "prev": old})
    _put(account, "proposal", None)
    _log(account, f"Echtgeld nutzt jetzt: {p['label']} (von dir bestätigt).", now)
    return p["label"]


def reject(account, now: float | None = None) -> None:
    now = time.time() if now is None else now
    p = _get(account, "proposal")
    if p:
        _put(account, "rejected", p)
        _put(account, "proposal", None)
        _log(account, f"Vorschlag „{p['label']}“ fürs Echtgeld abgelehnt.", now)


def revert(account, slot: str, now: float | None = None) -> None:
    """Übungskonto oder Echtgeld auf die vorherige Einstellung zurückdrehen."""
    now = time.time() if now is None else now
    cur = _get(account, slot)
    if not cur:
        return
    prev = cur.get("prev")
    _put(account, slot, prev if prev and prev.get("since") else None)
    name = "Übungskonto" if slot == "paper" else "Echtgeld"
    _log(account, f"{name} zurückgedreht auf: {(prev or {}).get('label') or 'Grundeinstellung (config.yaml)'}.", now)
    if slot == "paper":
        _put(account, "proposal", None)
        _put(account, "last_opt", int(now))  # nicht sofort wieder dieselbe Variante übernehmen
