"""Approximate CUHK academic term date ranges for ICS export."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class TermDateRange:
    start: date
    end: date


def parse_academic_term_label(term_label: str) -> tuple[int, int] | None:
    """Return (start_year, term_number) from e.g. '2026-27 Term 1'."""
    m = re.match(r"(\d{4})-\d{2}\s+Term\s+(\d)", term_label.strip(), re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def approximate_term_dates(term_label: str) -> TermDateRange | None:
    parsed = parse_academic_term_label(term_label)
    if parsed is None:
        return None
    year, term_num = parsed
    if term_num == 1:
        return TermDateRange(date(year, 9, 1), date(year, 12, 15))
    if term_num == 2:
        return TermDateRange(date(year + 1, 1, 15), date(year + 1, 5, 15))
    if term_num == 3:
        return TermDateRange(date(year + 1, 6, 1), date(year + 1, 7, 31))
    return None


def first_weekday_on_or_after(d: date, weekday: int) -> date:
    """Return first ``weekday`` (Mon=0) on or after ``d``."""
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta)


def ics_day_abbr(day_name: str) -> str:
    mapping = {
        "Monday": "MO",
        "Tuesday": "TU",
        "Wednesday": "WE",
        "Thursday": "TH",
        "Friday": "FR",
        "Saturday": "SA",
        "Sunday": "SU",
    }
    return mapping.get(day_name, "MO")


def ics_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")
