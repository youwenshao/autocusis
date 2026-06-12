"""ICS calendar export for projected weekly class schedules."""

from __future__ import annotations

from datetime import datetime, time, timezone

from .term_dates import approximate_term_dates, first_weekday_on_or_after, ics_day_abbr, ics_datetime
from .timetable_grid import TermTimetable, day_index

_ICS_DISCLAIMER = (
    "AutoCUSIS projected schedule. Term dates are approximate; "
    "verify against official CUHK timetable before registration."
)
_TZ = "Asia/Hong_Kong"


def render_term_ics(timetable: TermTimetable) -> str:
    if not timetable.has_meetings:
        return _empty_calendar(timetable.term_label)

    date_range = approximate_term_dates(timetable.term_label)
    if date_range is None:
        return _empty_calendar(timetable.term_label)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AutoCUSIS//Study Plan Export//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(timetable.term_label)}",
        f"X-WR-TIMEZONE:{_TZ}",
    ]

    uid_base = timetable.term_label.replace(" ", "-").lower()
    for i, meeting in enumerate(timetable.meetings):
        start_h, start_m = divmod(meeting.start_minutes, 60)
        end_h, end_m = divmod(meeting.end_minutes, 60)
        weekday = day_index(meeting.day)
        first_date = first_weekday_on_or_after(date_range.start, weekday)
        dt_start = datetime.combine(first_date, datetime.min.time().replace(hour=start_h, minute=start_m))
        dt_end = datetime.combine(first_date, datetime.min.time().replace(hour=end_h, minute=end_m))
        until = datetime.combine(date_range.end, time(23, 59, 59))

        summary = f"{meeting.course_code} {meeting.section_type} ({meeting.section_id})"
        desc_parts = [_ICS_DISCLAIMER]
        if meeting.location:
            desc_parts.append(f"Location: {meeting.location}")

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid_base}-{meeting.course_code}-{i}@autocusis",
                f"DTSTAMP:{ics_datetime(datetime.now(timezone.utc).replace(tzinfo=None))}Z",
                f"DTSTART;TZID={_TZ}:{ics_datetime(dt_start)}",
                f"DTEND;TZID={_TZ}:{ics_datetime(dt_end)}",
                f"RRULE:FREQ=WEEKLY;BYDAY={ics_day_abbr(meeting.day)};UNTIL={until.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{_escape(summary)}",
                f"DESCRIPTION:{_escape(' | '.join(desc_parts))}",
            ]
        )
        if meeting.location:
            lines.append(f"LOCATION:{_escape(meeting.location)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _empty_calendar(term_label: str) -> str:
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//AutoCUSIS//Study Plan Export//EN",
            f"X-WR-CALNAME:{_escape(term_label)} (empty)",
            "END:VCALENDAR",
            "",
        ]
    )


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
