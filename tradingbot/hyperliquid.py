"""Echtgeld-Handel auf Hyperliquid.

Sicherheitsprinzip: Der Server kennt nur einen eigenen Bot-Schlüssel (Agent). Die
Wallet des Nutzers gibt ihn einmal per Unterschrift frei (approveAgent). Ein Agent
darf auf Hyperliquid nur handeln, nie auszahlen oder überweisen. Der Schlüssel der
Wallet selbst kommt nie auf den Server.

LiveAccount verhält sich wie das Übungskonto (gleiche Methoden), damit live.step
mit denselben Regeln arbeitet. Jede Eröffnung, jedes Nachziehen des Stops und jedes
Schließen wird zusätzlich an Hyperliquid geschickt. Der Stop liegt immer auch als
Order an der Börse, damit er greift, selbst wenn der Server ausfällt.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path

from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.strategy import LONG

log = logging.getLogger("tradingbot")

API_URL = {"mainnet": "https://api.hyperliquid.xyz", "testnet": "https://api.hyperliquid-testnet.xyz"}
COIN_MAP = {"GOLD": "PAXG"}   # Coin im Bot → Markt auf Hyperliquid
FEE_PCT = 0.045               # Taker-Gebühr auf Hyperliquid je Seite
MIN_NOTIONAL = 10.0           # kleinste Order auf Hyperliquid in $
LEVERAGE = 5                  # Cross-Hebel: reicht als Puffer für mehrere Positionen gleichzeitig
SLIPPAGE = 0.01               # Market-Order darf höchstens 1 % vom Mittelkurs abweichen
STOP_SLIPPAGE = 0.10          # Stop-Order wird bei Auslösung als Market ausgeführt


def network() -> str:
    net = (os.getenv("HL_NETWORK") or "testnet").strip().lower()
    return net if net in API_URL else "testnet"


def hl_coin(coin: str) -> str:
    return COIN_MAP.get(coin, coin)


# ---- Bot-Schlüssel (Agent) --------------------------------------------------

def agent_key_path(live_db: str | Path) -> Path:
    return Path(live_db).with_name("hl-agent.key")


def agent_key(live_db: str | Path, create: bool = False) -> str | None:
    """Privater Schlüssel des Bots. Wird beim ersten Mal erzeugt und nur lesbar für
    den Dienst-Benutzer gespeichert. Er kann handeln, aber nichts auszahlen."""
    path = agent_key_path(live_db)
    if path.exists():
        return path.read_text().strip()
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = "0x" + secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


def agent_address(live_db: str | Path, create: bool = False) -> str | None:
    key = agent_key(live_db, create)
    if not key:
        return None
    from eth_account import Account
    return Account.from_key(key).address


def make_client(live_db: str | Path, account_address: str):
    """Echte Verbindung (hyperliquid-python-sdk). In Tests wird ein Ersatz übergeben."""
    import eth_account
    from hyperliquid.exchange import Exchange

    key = agent_key(live_db)
    if not key:
        raise RuntimeError("Kein Bot-Schlüssel vorhanden. Bitte im Dashboard unter Wallet den Bot freigeben.")
    return Exchange(eth_account.Account.from_key(key), API_URL[network()], account_address=account_address)


# ---- Rundung nach Hyperliquid-Regeln ---------------------------------------

def round_size(sz: float, sz_decimals: int) -> float:
    factor = 10 ** sz_decimals
    return int(sz * factor) / factor  # abrunden: nie mehr als geplant


def round_price(px: float, sz_decimals: int) -> float:
    """Höchstens 5 signifikante Stellen und 6 - szDecimals Nachkommastellen (Perps)."""
    return round(float(f"{px:.5g}"), 6 - sz_decimals)


# ---- Konto ------------------------------------------------------------------

class LiveAccount(PaperAccount):
    """Echtgeld-Konto: Buchführung in eigener Datenbank (data/live.db), Ausführung auf
    Hyperliquid. Kontostand = Guthaben auf Hyperliquid ohne offene Gewinne/Verluste."""

    def __init__(self, path: str | Path, client=None, account_address: str | None = None):
        super().__init__(path, start_equity=0.0, fee_pct=FEE_PCT)
        self.path = str(path)
        self.address = account_address or self.get_state("live:account")
        self._client = client
        self._state_cache: tuple[float, dict] | None = None

    # -- Verbindung
    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.path, self.address)
        return self._client

    def user_state(self, fresh: bool = False) -> dict:
        if fresh or self._state_cache is None or time.time() - self._state_cache[0] > 5:
            self._state_cache = (time.time(), self.client.info.user_state(self.address))
        return self._state_cache[1]

    def exchange_positions(self) -> dict[str, dict]:
        out = {}
        for p in self.user_state().get("assetPositions", []):
            item = p.get("position", p)
            if float(item.get("szi", 0)):
                out[item["coin"]] = item
        return out

    # -- Kontostand
    @property
    def balance(self) -> float:
        st = self.user_state()
        value = float(st.get("marginSummary", {}).get("accountValue", 0))
        upnl = sum(float(p.get("unrealizedPnl", 0)) for p in self.exchange_positions().values())
        return value - upnl

    @property
    def start_equity(self) -> float:
        raw = self.get_state("live:start_equity")
        return float(raw) if raw else self.balance

    # -- Hilfen
    def _sz_decimals(self, coin: str) -> int:
        info = self.client.info
        return info.asset_to_sz_decimals[info.coin_to_asset[hl_coin(coin)]]

    def _check(self, resp, what: str) -> dict:
        """Antwort einer Order prüfen; Fehler werden als Ausnahme gemeldet."""
        if not isinstance(resp, dict) or resp.get("status") != "ok":
            raise RuntimeError(f"Hyperliquid lehnt {what} ab: {resp}")
        statuses = resp.get("response", {}).get("data", {}).get("statuses", [{}])
        st = statuses[0] if statuses else {}
        if "error" in st:
            raise RuntimeError(f"Hyperliquid lehnt {what} ab: {st['error']}")
        return st

    def _place_stop(self, pos: Position) -> int | None:
        coin, dec = hl_coin(pos.coin), self._sz_decimals(pos.coin)
        is_buy = pos.side != LONG
        trigger = round_price(pos.stop, dec)
        limit = round_price(pos.stop * (1 + STOP_SLIPPAGE if is_buy else 1 - STOP_SLIPPAGE), dec)
        st = self._check(self.client.order(coin, is_buy, pos.qty, limit,
                                           {"trigger": {"triggerPx": trigger, "isMarket": True, "tpsl": "sl"}},
                                           reduce_only=True), f"Stop für {pos.coin}")
        oid = st.get("resting", {}).get("oid") or st.get("filled", {}).get("oid")
        self.set_state(f"live:stop:{pos.coin}", f"{oid}:{trigger}")
        return oid

    def _cancel_stop(self, coin: str) -> None:
        raw = self.get_state(f"live:stop:{coin}")
        if raw and raw.split(":")[0] not in ("", "None"):
            try:
                self.client.cancel(hl_coin(coin), int(raw.split(":")[0]))
            except Exception as exc:  # schon ausgelöst oder weg: egal
                log.info("Stop-Order %s für %s nicht mehr da: %s", raw, coin, exc)
        with self.conn:
            self.conn.execute("DELETE FROM state WHERE key = ?", (f"live:stop:{coin}",))

    # -- Positionen
    def open(self, pos: Position) -> None:
        if self.position(pos.coin):
            raise ValueError(f"Für {pos.coin} ist bereits eine Position offen.")
        coin, dec = hl_coin(pos.coin), self._sz_decimals(pos.coin)
        qty = round_size(pos.qty, dec)
        if qty * pos.entry < MIN_NOTIONAL:
            raise ValueError(f"{pos.coin}: Position {qty * pos.entry:.2f} $ liegt unter der Mindestgröße von "
                             f"{MIN_NOTIONAL:.0f} $ auf Hyperliquid. Mehr Guthaben einzahlen.")
        if self.get_state(f"live:lev:{coin}") != str(LEVERAGE):
            self.client.update_leverage(LEVERAGE, coin, True)
            self.set_state(f"live:lev:{coin}", str(LEVERAGE))
        st = self._check(self.client.market_open(coin, pos.side == LONG, qty, None, SLIPPAGE), f"Kauf {pos.coin}")
        filled = st.get("filled")
        if not filled or not float(filled.get("totalSz", 0)):
            raise RuntimeError(f"{pos.coin}: Order nicht ausgeführt ({st}).")
        fill, ratio = float(filled["avgPx"]), float(filled["avgPx"]) / pos.entry
        # Stop, Ziel und Bestwert behalten ihren Abstand, bezogen auf den echten Kaufkurs
        pos.entry, pos.qty, pos.best = fill, float(filled["totalSz"]), fill
        pos.stop *= ratio
        pos.target *= ratio
        if self.get_state("live:start_equity") is None:
            self.set_state("live:start_equity", f"{self.balance:.2f}")
        PaperAccount.open(self, pos)
        self._place_stop(pos)
        self._state_cache = None

    def save_position(self, pos: Position) -> None:
        old = self.position(pos.coin)
        PaperAccount.save_position(self, pos)
        raw = self.get_state(f"live:stop:{pos.coin}")
        placed = float(raw.split(":")[1]) if raw else None
        if old is not None and placed != round_price(pos.stop, self._sz_decimals(pos.coin)):
            # Stop nachgezogen: neuen Stop zuerst setzen, dann den alten löschen
            old_raw = raw
            self._place_stop(pos)
            if old_raw and old_raw.split(":")[0] not in ("", "None"):
                try:
                    self.client.cancel(hl_coin(pos.coin), int(old_raw.split(":")[0]))
                except Exception as exc:
                    log.info("Alter Stop für %s nicht mehr da: %s", pos.coin, exc)

    def close(self, coin: str, price: float, reason: str, when: int | None = None) -> float:
        pos = self.position(coin)
        if pos is None:
            raise ValueError(f"Für {coin} ist keine Position offen.")
        if hl_coin(coin) in self.exchange_positions():
            st = self._check(self.client.market_close(hl_coin(coin), None, None, SLIPPAGE), f"Verkauf {coin}")
            filled = st.get("filled") or {}
            price = float(filled.get("avgPx") or price)
        else:  # an der Börse schon geschlossen (Stop ausgelöst): echten Kurs aus den Ausführungen holen
            price = self._last_close_fill(coin, pos) or price
        self._cancel_stop(coin)
        self._state_cache = None
        return PaperAccount.close(self, coin, price, reason, when)

    def _last_close_fill(self, coin: str, pos: Position) -> float | None:
        try:
            fills = self.client.info.user_fills(self.address)
        except Exception:
            return None
        mine = [f for f in fills if f.get("coin") == hl_coin(coin) and int(f.get("time", 0)) >= pos.opened_at * 1000
                and str(f.get("dir", "")).startswith("Close")]
        return float(max(mine, key=lambda f: int(f["time"]))["px"]) if mine else None

    def sync(self, coins=None) -> list[tuple[str, Position, float, float]]:
        """Positionen, die an der Börse schon geschlossen wurden (Stop ausgelöst),
        auch hier schließen. Gibt (Coin, Position, Kurs, Ergebnis) zurück.
        coins: nur diese Coins (jeder Bot kümmert sich um seine eigenen)."""
        self._state_cache = None
        on_exchange = self.exchange_positions()
        done = []
        for coin, pos in self.positions().items():
            if coins is not None and coin not in coins:
                continue
            if hl_coin(coin) not in on_exchange:
                price = self._last_close_fill(coin, pos) or pos.stop
                self._cancel_stop(coin)
                pnl = PaperAccount.close(self, coin, price, "stop")
                done.append((coin, pos, price, pnl))
        return done

    def close_all(self, reason: str = "notaus") -> list[tuple[str, float]]:
        out = []
        for coin in list(self.positions()):
            try:
                out.append((coin, self.close(coin, 0.0, reason)))
            except Exception:
                log.exception("Not-Aus: %s konnte nicht geschlossen werden", coin)
        return out


# ---- Schalter (vom Dashboard gesetzt, vom Bot gelesen) ---------------------

def is_enabled(acc: PaperAccount) -> bool:
    return acc.get_state("live:enabled") == "1" and bool(acc.get_state("live:account"))
