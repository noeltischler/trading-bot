"""Wichtige Wirtschaftstermine, rund um die der Kurs oft heftig springt.

Rund um diese Termine eröffnet der Bot keine neuen Trades (risk.event_pause_hours).
Offene Positionen laufen mit ihrem Stop weiter.

Quellen: Federal Reserve (FOMC-Kalender), U.S. Bureau of Labor Statistics
(CPI- und Arbeitsmarkt-Kalender, inkl. der Verschiebungen durch den Shutdown 2025)
und EZB (Sitzungskalender). Die Liste muss einmal im Jahr ergänzt werden;
upcoming_gap() meldet, wenn sie bald endet.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FRANKFURT = ZoneInfo("Europe/Berlin")

# Uhrzeit der Veröffentlichung in der jeweiligen Ortszeit
KINDS = {
    "fed": ("US-Zinsentscheid (Fed)", time(14, 0), NY),
    "cpi": ("US-Inflation (CPI)", time(8, 30), NY),
    "jobs": ("US-Arbeitsmarkt", time(8, 30), NY),
    "ecb": ("EZB-Zinsentscheid", time(14, 15), FRANKFURT),
}

_DATES = {
    "fed": [
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17",
        "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
        "2026-10-28", "2026-12-09",
    ],
    "cpi": [
        "2025-10-24", "2025-12-18",
        "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10",
        "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
        "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
        "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10",
    ],
    "jobs": [
        "2025-11-20", "2025-12-16",
        "2026-01-09", "2026-02-11", "2026-03-06", "2026-04-03", "2026-05-08", "2026-06-05",
        "2026-07-02", "2026-08-07", "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
    ],
    "ecb": [
        "2025-10-30", "2025-12-18",
        "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-11", "2026-07-23", "2026-09-10",
        "2026-10-29", "2026-12-17",
    ],
}


def _events() -> list[tuple[int, str]]:
    out = []
    for kind, days in _DATES.items():
        _, at, tz = KINDS[kind]
        for d in days:
            ts = datetime.combine(date.fromisoformat(d), at, tzinfo=tz).timestamp()
            out.append((int(ts), kind))
    return sorted(out)


EVENTS = _events()


def event_near(ts: float, hours_before: float, hours_after: float) -> str | None:
    """Name des Termins, wenn ts im Zeitfenster um einen Termin liegt, sonst None."""
    for when, kind in EVENTS:
        if when - hours_before * 3600 <= ts <= when + hours_after * 3600:
            return KINDS[kind][0]
    return None


def next_events(now: float, n: int = 5) -> list[tuple[int, str]]:
    """Die nächsten n Termine ab now: (Zeit, Name)."""
    return [(when, KINDS[kind][0]) for when, kind in EVENTS if when >= now][:n]


def upcoming_gap(now: float, days: int = 75) -> bool:
    """True, wenn für die nächsten `days` Tage kein Fed- oder EZB-Termin mehr in der
    Liste steht, die Liste also ergänzt werden muss."""
    horizon = now + days * 86400
    return not any(now <= when <= horizon for when, kind in EVENTS if kind in ("fed", "ecb"))
