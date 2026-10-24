"""Nachrichtenlage: Schlagzeilen aus US-, EU- und deutschen Quellen (RSS) sammeln
und mit einem einfachen Stimmungswert versehen.

Vorerst NUR zum Beobachten: Die Schlagzeilen und ihr Wert werden gespeichert und im
Dashboard gezeigt, beeinflussen aber keine Trades. Nach einigen Wochen lässt sich
mit den gesammelten Daten prüfen, ob die Stimmung die Kurse vorhergesagt hätte.

Der Stimmungswert kommt aus einer Stichwortliste (Deutsch und Englisch):
+1 je positivem, −1 je negativem Stichwort, begrenzt auf −3 bis +3.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = {
    "CoinDesk (US)": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph (US)": "https://cointelegraph.com/rss",
    "CNBC Wirtschaft (US)": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Federal Reserve (US)": "https://www.federalreserve.gov/feeds/press_all.xml",
    "EZB (EU)": "https://www.ecb.europa.eu/rss/press.html",
    "tagesschau Wirtschaft (DE)": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "BTC-ECHO (DE)": "https://www.btc-echo.de/feed/",
}

POSITIVE = [
    # Englisch
    "surge", "soar", "rally", "jump", "record high", "all-time high", "bullish", "approval", "approved",
    "inflows", "adoption", "rate cut", "cuts rates", "eases", "easing", "beats expectations", "rebound",
    "partnership", "launches etf", "etf approval", "gains", "upgrade", "breakout",
    # Deutsch
    "steigt", "steigen", "rekord", "allzeithoch", "zinssenkung", "senkt den leitzins", "erholung",
    "zuflüsse", "zulassung", "genehmigt", "kursgewinne", "aufwärts", "optimismus",
]
NEGATIVE = [
    # Englisch
    "crash", "plunge", "tumble", "slump", "sell-off", "selloff", "bearish", "hack", "hacked", "exploit",
    "ban", "bans", "lawsuit", "sues", "fraud", "bankruptcy", "insolvency", "liquidation", "outflows",
    "rate hike", "raises rates", "hawkish", "inflation rises", "recession", "sanctions", "crackdown",
    "delisting", "investigation", "default", "tariff",
    # Deutsch
    "fällt", "fallen", "einbruch", "absturz", "verbot", "klage", "betrug", "insolvenz",
    "pleite", "zinserhöhung", "erhöht den leitzins", "rezession", "sanktionen", "hackerangriff",
    "abflüsse", "kursverluste", "krieg", "zölle", "ermittlungen",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id        TEXT PRIMARY KEY,   -- Hash aus Quelle und Link
    published INTEGER NOT NULL,   -- Unix-Sekunden, UTC
    source    TEXT NOT NULL,
    title     TEXT NOT NULL,
    link      TEXT NOT NULL,
    score     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS news_published ON news (published);
"""


def score(title: str) -> int:
    """Stimmungswert einer Schlagzeile von −3 (sehr negativ) bis +3 (sehr positiv)."""
    t = title.lower()

    def hits(words):
        return sum(1 for w in words if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t))

    return max(-3, min(3, hits(POSITIVE) - hits(NEGATIVE)))


def _text(el, *names) -> str:
    for n in names:
        found = el.find(n)
        if found is not None:
            return (found.text or found.get("href") or "").strip()
    return ""


def _time(raw: str) -> int | None:
    if not raw:
        return None
    try:
        return int(parsedate_to_datetime(raw).timestamp())  # RSS: RFC 822
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())  # Atom: ISO 8601
    except ValueError:
        return None


def parse_feed(xml_bytes: bytes, source: str, now: int | None = None) -> list[dict]:
    """Liest RSS 2.0, RSS 1.0 (RDF) oder Atom. Gibt Einträge mit title, link, published zurück."""
    now = int(now if now is not None else time.time())
    root = ET.fromstring(xml_bytes)
    atom = "{http://www.w3.org/2005/Atom}"
    rss1 = "{http://purl.org/rss/1.0/}"
    items = root.findall(".//item") or root.findall(f".//{rss1}item") or root.findall(f".//{atom}entry")
    out = []
    for it in items:
        title = _text(it, "title", f"{rss1}title", f"{atom}title")
        link = _text(it, "link", f"{rss1}link", f"{atom}link")
        raw_time = _text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date", f"{atom}updated", f"{atom}published")
        if not title:
            continue
        published = _time(raw_time) or now
        out.append({"source": source, "title": re.sub(r"\s+", " ", title), "link": link,
                    "published": min(published, now), "score": score(title)})
    return out


def fetch_all(feeds: dict[str, str] = FEEDS, timeout: float = 15.0, log=None) -> list[dict]:
    """Holt alle Feeds; ein kaputter Feed stoppt die anderen nicht."""
    items = []
    for source, url in feeds.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tradingbot)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                items += parse_feed(resp.read(), source)
        except Exception as exc:
            if log:
                log.warning("Nachrichten von %s nicht abrufbar: %s", source, exc)
    return items


def save(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Speichert neue Schlagzeilen (bekannte werden übersprungen). Gibt die Zahl neuer zurück."""
    conn.executescript(_SCHEMA)
    before = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?)",
            [(hashlib.sha1(f"{i['source']}|{i['link'] or i['title']}".encode()).hexdigest(),
              i["published"], i["source"], i["title"], i["link"], i["score"]) for i in items],
        )
    return conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] - before


def recent(conn: sqlite3.Connection, since: int, limit: int = 200) -> list[dict]:
    conn.executescript(_SCHEMA)
    cur = conn.execute("SELECT published, source, title, link, score FROM news WHERE published >= ? "
                       "ORDER BY published DESC LIMIT ?", (since, limit))
    return [dict(zip(("published", "source", "title", "link", "score"), r)) for r in cur.fetchall()]


def mood(items: list[dict]) -> float | None:
    """Durchschnittliche Stimmung der Schlagzeilen mit Wert ≠ 0 (None ohne solche)."""
    scored = [i["score"] for i in items if i["score"]]
    return round(sum(scored) / len(scored), 2) if scored else None
