"""Parse meeting time strings from community data."""

from __future__ import annotations

import re
from datetime import datetime

_DAY_MAP = {
    "MO": "Monday",
    "MON": "Monday",
    "M": "Monday",
    "TU": "Tuesday",
    "TUE": "Tuesday",
    "T": "Tuesday",
    "WE": "Wednesday",
    "WED": "Wednesday",
    "W": "Wednesday",
    "TH": "Thursday",
    "THU": "Thursday",
    "H": "Thursday",
    "FR": "Friday",
    "FRI": "Friday",
    "F": "Friday",
    "SA": "Saturday",
    "SAT": "Saturday",
    "S": "Saturday",
    "SU": "Sunday",
    "SUN": "Sunday",
}

_EAGLEZHEN_TIME_RE = re.compile(
    r"^(?P<day>[A-Za-z]{1,3})\s+"
    r"(?P<start>\d{1,2}:\d{2}(?:AM|PM)?|\d{1,2}(?:AM|PM))\s*-\s*"
    r"(?P<end>\d{1,2}:\d{2}(?:AM|PM)?|\d{1,2}(?:AM|PM))",
    re.IGNORECASE,
)

_CUTOPIA_DAY_NUM = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}


def _to_24h(raw: str) -> str:
    raw = raw.strip().upper()
    for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return raw


def parse_eaglezhen_time(text: str) -> tuple[str, str, str] | None:
    m = _EAGLEZHEN_TIME_RE.match((text or "").strip())
    if not m:
        return None
    day_key = m.group("day").upper()
    day = _DAY_MAP.get(day_key)
    if not day:
        return None
    return day, _to_24h(m.group("start")), _to_24h(m.group("end"))


def cutopia_day(day_num: int) -> str:
    return _CUTOPIA_DAY_NUM.get(day_num, "Monday")


def infer_section_type(section_id: str) -> str:
    upper = section_id.upper()
    if "LEC" in upper or upper.startswith("L") and "LAB" not in upper and "TUT" not in upper:
        if re.search(r"\bLEC\b|^L\d|^--L[A-Z]?\s*\(", upper):
            return "Lecture"
    if "TUT" in upper or re.search(r"T\d{2}|TUT", upper):
        return "Tutorial"
    if "LAB" in upper:
        return "Lab"
    if "SEM" in upper:
        return "Seminar"
    if re.match(r"^[A-Z]$", section_id.strip()):
        return "Lecture"
    if re.match(r"^T\d+", section_id.strip(), re.IGNORECASE):
        return "Tutorial"
    return "Other"


def parse_section_meta(section_raw: str) -> tuple[str, str, int | None]:
    """Extract section id, type, and class number from EagleZhen/CUtopia labels."""
    text = (section_raw or "").strip()
    class_number = None
    m = re.search(r"\((\d+)\)\s*$", text)
    if m:
        class_number = int(m.group(1))
        text = text[: m.start()].strip()

    section_id = text
    if "--" in text:
        section_id = text.split("--", 1)[-1].strip()
    section_id = re.sub(r"^-+", "", section_id)
    for suffix in ("-LEC", "-LAB", "-TUT", "-SEM"):
        if section_id.upper().endswith(suffix):
            section_id = section_id[: -len(suffix)]
    section_id = section_id.strip() or text

    stype = infer_section_type(text)
    if stype == "Lecture" and len(section_id) == 1:
        pass
    elif stype == "Tutorial" and section_id.upper().startswith("L"):
        section_id = section_id[1:] if section_id.startswith("L") else section_id

    return section_id, stype, class_number


def resolve_parent_lecture(section_id: str, section_type: str) -> str | None:
    if section_type == "Lecture":
        return None
    if section_type == "Tutorial" and len(section_id) >= 2:
        if section_id[0].isalpha() and section_id[1:].isdigit():
            return section_id[0].upper()
    return None
