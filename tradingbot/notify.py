"""Benachrichtigungen aufs Handy über ntfy (https://ntfy.sh).

Kein Konto nötig: Der Bot legt beim ersten Start einen zufälligen, nicht erratbaren
Kanalnamen an und speichert ihn im Übungskonto. Das Dashboard zeigt einen Link zum
Abonnieren. Wer die ntfy-App installiert und den Kanal abonniert, bekommt jede
Nachricht als Push-Mitteilung. Der Kanalname wirkt wie ein Passwort: nicht teilen.
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.request

SERVER = "https://ntfy.sh"
STATE_KEY = "ntfy_topic"

log = logging.getLogger("tradingbot")


def topic(account) -> str:
    """Kanalname aus dem Übungskonto, beim ersten Aufruf neu und zufällig erzeugt."""
    t = account.get_state(STATE_KEY)
    if not t:
        t = "tradingbot-" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
        account.set_state(STATE_KEY, t)
    return t


def subscribe_url(account) -> str:
    return f"{SERVER}/{topic(account)}"


def send(account, title: str, message: str, tags: list[str] | None = None,
         priority: int = 3, timeout: float = 10.0) -> bool:
    """Schickt eine Mitteilung. Fehler werden nur protokolliert, der Bot läuft weiter."""
    body = json.dumps({"topic": topic(account), "title": title, "message": message,
                       "tags": tags or [], "priority": priority}).encode("utf-8")
    req = urllib.request.Request(SERVER, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception as exc:
        log.warning("Handy-Benachrichtigung nicht gesendet: %s", exc)
        return False


def _price(p: float) -> str:
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if p >= 1 else f"{p:.5f}".replace(".", ",")


def opened_text(coin: str, long: bool, price: float, stop: float, risk_usd: float) -> tuple[str, str, list[str]]:
    side = "LONG (Kauf)" if long else "SHORT (Leerverkauf)"
    title = f"{coin}: {side} eröffnet"
    msg = (f"Einstieg {_price(price)} $\nStop {_price(stop)} $\n"
           f"Risiko etwa {risk_usd:.2f} $ (Übungskonto)")
    return title, msg, ["chart_with_upwards_trend" if long else "chart_with_downwards_trend"]


def closed_text(coin: str, long: bool, exit_price: float, pnl: float, reason: str, balance: float) -> tuple[str, str, list[str]]:
    reasons = {"stop": "Stop", "signal": "Gegensignal", "target": "Gewinnziel"}
    title = f"{coin}: {'Long' if long else 'Short'} geschlossen, {pnl:+.2f} $".replace(".", ",")
    msg = f"Ausstieg {_price(exit_price)} $ ({reasons.get(reason, reason)})\nKontostand {_price(balance)} $"
    return title, msg, ["white_check_mark" if pnl > 0 else "x"]
