"""Calendar-style weekly timetable SVG renderer."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

from .timetable_grid import (
    DAY_ABBREV,
    GridMeeting,
    TermTimetable,
    course_color,
    format_time_minutes,
)

_WIDTH = 900
_TIME_GUTTER = 56
_HEADER = 48
_LEGEND_H = 36
_ROW_H = 28
_LANE_GAP = 4


def render_term_svg(timetable: TermTimetable) -> str:
    if not timetable.has_meetings:
        return _empty_svg(timetable.term_label, timetable.section_status)

    days = timetable.days_present or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    n_days = len(days)
    grid_w = _WIDTH - _TIME_GUTTER
    col_w = grid_w / n_days
    span = max(timetable.day_end - timetable.day_start, 60)
    body_h = int((span / 30) * (_ROW_H / 2))
    body_h = max(body_h, 200)
    legend_rows = len({m.course_code for m in timetable.meetings})
    legend_h = _LEGEND_H + max(0, legend_rows - 1) * 18
    height = _HEADER + body_h + legend_h + 16

    lanes = _assign_lanes(timetable.meetings)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{height}" '
        f'viewBox="0 0 {_WIDTH} {height}" role="img">',
        f'<title>{html.escape(timetable.term_label)} timetable</title>',
        f'<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="16" y="28" font-family="system-ui,sans-serif" font-size="16" font-weight="600" fill="#111">'
        f'{html.escape(timetable.term_label)}</text>',
        f'<text x="16" y="44" font-family="system-ui,sans-serif" font-size="11" fill="#666">'
        f'sections: {html.escape(timetable.section_status)}</text>',
    ]

    y0 = _HEADER
    for tick in range(timetable.day_start, timetable.day_end + 1, 30):
        y = y0 + _minutes_to_y(tick, timetable.day_start, timetable.day_end, body_h)
        parts.append(
            f'<line x1="{_TIME_GUTTER}" y1="{y:.1f}" x2="{_WIDTH}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="8" y="{y + 4:.1f}" font-family="system-ui,sans-serif" font-size="10" fill="#6b7280">'
            f'{format_time_minutes(tick)}</text>'
        )

    for i, day in enumerate(days):
        x = _TIME_GUTTER + i * col_w
        parts.append(
            f'<text x="{x + col_w / 2:.1f}" y="{y0 - 8}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#374151">'
            f'{DAY_ABBREV.get(day, day[:3])}</text>'
        )
        parts.append(
            f'<line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0 + body_h}" stroke="#d1d5db" stroke-width="1"/>'
        )

    parts.append(
        f'<line x1="{_WIDTH}" y1="{y0}" x2="{_WIDTH}" y2="{y0 + body_h}" stroke="#d1d5db" stroke-width="1"/>'
    )

    for meeting in timetable.meetings:
        if meeting.day not in days:
            continue
        col = days.index(meeting.day)
        lane = lanes.get(id(meeting), 0)
        n_lanes = _lane_count(meeting, lanes)
        lane_w = (col_w - _LANE_GAP) / max(n_lanes, 1)
        x = _TIME_GUTTER + col * col_w + _LANE_GAP / 2 + lane * lane_w
        y = y0 + _minutes_to_y(meeting.start_minutes, timetable.day_start, timetable.day_end, body_h)
        h = _minutes_to_y(meeting.end_minutes, timetable.day_start, timetable.day_end, body_h) - y
        h = max(h, 14)
        fill, stroke = course_color(meeting.course_code)
        label = f"{meeting.course_code}"
        type_abbr = meeting.section_type[:3]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{lane_w - 2:.1f}" height="{h:.1f}" '
            f'rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.5">'
            f'<title>{html.escape(meeting.course_code)} {html.escape(meeting.section_type)} '
            f'{html.escape(meeting.section_id)} {format_time_minutes(meeting.start_minutes)}-'
            f'{format_time_minutes(meeting.end_minutes)} {html.escape(meeting.location or "")}</title>'
            f"</rect>"
        )
        if h >= 22:
            parts.append(
                f'<text x="{x + 4:.1f}" y="{y + 14:.1f}" font-family="system-ui,sans-serif" '
                f'font-size="10" font-weight="600" fill="{stroke}">{html.escape(label)}</text>'
            )
            if h >= 34:
                parts.append(
                    f'<text x="{x + 4:.1f}" y="{y + 26:.1f}" font-family="system-ui,sans-serif" '
                    f'font-size="9" fill="#374151">{html.escape(type_abbr)}</text>'
                )

    legend_y = y0 + body_h + 20
    parts.append(
        f'<text x="16" y="{legend_y}" font-family="system-ui,sans-serif" font-size="11" '
        f'font-weight="600" fill="#374151">Courses</text>'
    )
    codes = sorted({m.course_code for m in timetable.meetings})
    for i, code in enumerate(codes):
        fill, stroke = course_color(code)
        lx = 16 + (i % 4) * 210
        ly = legend_y + 18 + (i // 4) * 18
        parts.append(f'<rect x="{lx}" y="{ly - 10}" width="12" height="12" rx="2" fill="{fill}" stroke="{stroke}"/>')
        parts.append(
            f'<text x="{lx + 18}" y="{ly}" font-family="system-ui,sans-serif" font-size="10" fill="#374151">'
            f'{html.escape(code)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _empty_svg(term_label: str, section_status: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="80" viewBox="0 0 {_WIDTH} 80">'
        f'<title>{html.escape(term_label)}</title>'
        f'<text x="16" y="32" font-family="system-ui,sans-serif" font-size="14" fill="#374151">'
        f'{html.escape(term_label)} — no timetable data ({html.escape(section_status)})</text>'
        f"</svg>"
    )


def _minutes_to_y(minutes: int, start: int, end: int, body_h: int) -> float:
    span = max(end - start, 1)
    return (minutes - start) / span * body_h


def _meeting_key(m: GridMeeting) -> tuple[str, int, int]:
    return (m.day, m.start_minutes, m.end_minutes)


def _overlaps(a: GridMeeting, b: GridMeeting) -> bool:
    if a.day != b.day:
        return False
    return a.start_minutes < b.end_minutes and b.start_minutes < a.end_minutes


def _assign_lanes(meetings: list[GridMeeting]) -> dict[int, int]:
    lanes: dict[int, int] = {}
    by_day: dict[str, list[GridMeeting]] = {}
    for m in meetings:
        by_day.setdefault(m.day, []).append(m)
    for day_meetings in by_day.values():
        day_meetings.sort(key=lambda m: (m.start_minutes, m.end_minutes))
        active: list[tuple[GridMeeting, int]] = []
        for m in day_meetings:
            active = [(other, lane) for other, lane in active if _overlaps(other, m)]
            used = {lane for _, lane in active}
            lane = 0
            while lane in used:
                lane += 1
            lane = min(lane, 1)
            lanes[id(m)] = lane
            active.append((m, lane))
    return lanes


def _lane_count(meeting: GridMeeting, lanes: dict[int, int]) -> int:
    return 2 if lanes.get(id(meeting), 0) > 0 else 1


def validate_svg(svg: str) -> bool:
    try:
        ET.fromstring(svg)
        return True
    except ET.ParseError:
        return False
