"""Täglicher Fälschungsnachweis für das Trade-Protokoll.

Für jeden Tag wird ein SHA-256-Fingerabdruck über alle bis Tagesende
abgeschlossenen Trades gebildet, verkettet mit dem Fingerabdruck des Vortags.
Wird später auch nur ein Trade verändert, passen alle folgenden Fingerabdrücke
nicht mehr ('python -m tradingbot verify' prüft das).

Optional wird der Fingerabdruck auf eine Ethereum-Blockchain geschrieben
(Standard: Testnetz Sepolia, kostenlos). Dafür in der Umgebung setzen:
  ANCHOR_RPC_URL       z. B. https://ethereum-sepolia-rpc.publicnode.com
  ANCHOR_PRIVATE_KEY   Schlüssel einer EIGENEN, nur dafür genutzten Wallet
  ANCHOR_EXPLORER      optional, Standard https://sepolia.etherscan.io/tx/
und 'pip install web3' ausführen. Ohne diese Angaben bleibt der Nachweis lokal.
Veröffentlicht wird nur an Tagen mit neuen Trades, damit keine unnötigen
Transaktionen entstehen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from tradingbot.paper import PaperAccount

log = logging.getLogger("tradingbot")
GENESIS = "0" * 64


def _day_end(day: str) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((d + timedelta(days=1)).timestamp())


def digest(trades: list[dict], day: str, prev: str) -> str:
    """Fingerabdruck über alle Trades, die vor Ende von `day` geschlossen wurden."""
    rows = [t for t in trades if t["closed_at"] < _day_end(day)]
    payload = json.dumps({"day": day, "prev": prev, "trades": rows}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def publish(hex_digest: str) -> str | None:
    """Schreibt den Fingerabdruck als Daten einer 0-ETH-Transaktion an sich selbst.
    Gibt den Link zur Transaktion zurück oder None, wenn nicht konfiguriert."""
    rpc, key = os.getenv("ANCHOR_RPC_URL"), os.getenv("ANCHOR_PRIVATE_KEY")
    if not rpc or not key:
        return None
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(key)
    tx = {
        "to": acct.address,
        "value": 0,
        "data": "0x" + hex_digest,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
    }
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    explorer = os.getenv("ANCHOR_EXPLORER", "https://sepolia.etherscan.io/tx/")
    return explorer + "0x" + tx_hash.hex().removeprefix("0x")


def anchor_due_days(account: PaperAccount, today: date | None = None) -> None:
    """Legt für jeden abgeschlossenen Tag seit dem letzten Nachweis einen an."""
    today = today or datetime.now(timezone.utc).date()
    anchors = account.anchors()
    trades = account.trades()
    if anchors:
        day = date.fromisoformat(anchors[-1]["day"]) + timedelta(days=1)
        prev = anchors[-1]["hash"]
    elif trades:
        day = datetime.fromtimestamp(trades[0]["closed_at"], timezone.utc).date()
        prev = GENESIS
    else:
        return
    while day < today:
        key = day.isoformat()
        h = digest(trades, key, prev)
        start, end = _day_end(key) - 86400, _day_end(key)
        traded = any(start <= t["closed_at"] < end for t in trades)
        try:
            # Nur Tage mit neuen Trades kommen auf die Blockchain; jeder Eintrag
            # deckt über die Verkettung auch alle Tage davor ab.
            url = publish(h) if traded else None
        except Exception:
            log.exception("Blockchain-Veröffentlichung für %s fehlgeschlagen, Nachweis bleibt lokal", key)
            url = None
        account.add_anchor(key, h, prev, url)
        log.info("Nachweis %s: %s %s", key, h[:16], url or "(lokal)")
        prev = h
        day += timedelta(days=1)


def verify(account: PaperAccount) -> list[str]:
    """Prüft die Kette. Gibt eine Liste von Problemen zurück (leer = alles in Ordnung)."""
    problems = []
    trades = account.trades()
    prev = GENESIS
    for a in account.anchors():
        if a["prev"] != prev:
            problems.append(f"{a['day']}: Kette unterbrochen")
        if digest(trades, a["day"], a["prev"]) != a["hash"]:
            problems.append(f"{a['day']}: Trades wurden nachträglich verändert")
        prev = a["hash"]
    return problems
