"""Benachrichtigungen aufs Handy über ntfy (https://ntfy.sh) und Telegram.

ntfy: Kein Konto nötig. Der Bot legt beim ersten Start einen zufälligen, nicht
erratbaren Kanalnamen an und speichert ihn im Übungskonto. Das Dashboard zeigt ihn
zum Abonnieren. Der Kanalname wirkt wie ein Passwort: nicht teilen.

Telegram (zuverlässiger auf dem iPhone): Stehen TELEGRAM_TOKEN und TELEGRAM_CHAT_ID
in der Umgebung (auf dem Server in /etc/tradingbot.env), geht jede Nachricht
zusätzlich an diesen Chat. Einrichten: python -m tradingbot telegram
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import urllib.request

SERVER = "https://ntfy.sh"
STATE_KEY = "ntfy_topic"
TELEGRAM_API = "https://api.telegram.org"
ENV_FILE = "/etc/tradingbot.env"

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


def _post_json(url: str, data: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def telegram_config(env: dict | None = None) -> tuple[str, str]:
    """(Token, Chat-ID) aus der Umgebung, leer wenn nicht eingerichtet."""
    env = os.environ if env is None else env
    return env.get("TELEGRAM_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10.0) -> bool:
    try:
        return bool(_post_json(f"{TELEGRAM_API}/bot{token}/sendMessage",
                               {"chat_id": chat_id, "text": text}, timeout).get("ok"))
    except Exception as exc:  # die Meldung enthält den Token nicht, nur den Fehler
        log.warning("Telegram-Nachricht nicht gesendet: %s", exc)
        return False


def telegram_chats(token: str, timeout: float = 10.0) -> list[tuple[str, str]]:
    """Chats, die dem Bot zuletzt geschrieben haben: (Chat-ID, Name)."""
    data = _post_json(f"{TELEGRAM_API}/bot{token}/getUpdates", {}, timeout)
    chats = {}
    for upd in data.get("result", []):
        chat = (upd.get("message") or upd.get("my_chat_member") or {}).get("chat") or {}
        if "id" in chat:
            chats[str(chat["id"])] = chat.get("first_name") or chat.get("title") or ""
    return list(chats.items())


def send(account, title: str, message: str, tags: list[str] | None = None,
         priority: int = 3, timeout: float = 10.0) -> bool:
    """Schickt eine Mitteilung an ntfy und, falls eingerichtet, an Telegram.
    True, wenn mindestens ein Weg geklappt hat. Fehler werden nur protokolliert,
    der Bot läuft weiter."""
    ok = False
    try:
        _post_json(SERVER, {"topic": topic(account), "title": title, "message": message,
                            "tags": tags or [], "priority": priority}, timeout)
        ok = True
    except Exception as exc:
        log.warning("Handy-Benachrichtigung nicht gesendet: %s", exc)
    token, chat_id = telegram_config()
    if token and chat_id:
        ok = send_telegram(token, chat_id, f"{title}\n{message}", timeout) or ok
    return ok


def _price(p: float) -> str:
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if p >= 1 else f"{p:.5f}".replace(".", ",")


def opened_text(coin: str, long: bool, price: float, stop: float, risk_usd: float,
                strategy: str = "") -> tuple[str, str, list[str]]:
    side = "LONG (Kauf)" if long else "SHORT (Leerverkauf)"
    title = f"{coin}: {side} eröffnet"
    msg = (f"Einstieg {_price(price)} $\nStop {_price(stop)} $\n"
           f"Risiko etwa {risk_usd:.2f} $ (Übungskonto)" + (f"\nStrategie: {strategy}" if strategy else ""))
    return title, msg, ["chart_with_upwards_trend" if long else "chart_with_downwards_trend"]


def closed_text(coin: str, long: bool, exit_price: float, pnl: float, reason: str, balance: float) -> tuple[str, str, list[str]]:
    reasons = {"stop": "Stop", "signal": "Gegensignal", "target": "Gewinnziel"}
    title = f"{coin}: {'Long' if long else 'Short'} geschlossen, {pnl:+.2f} $".replace(".", ",")
    msg = f"Ausstieg {_price(exit_price)} $ ({reasons.get(reason, reason)})\nKontostand {_price(balance)} $"
    return title, msg, ["white_check_mark" if pnl > 0 else "x"]
