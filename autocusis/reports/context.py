"""Context for rich report generation (profile, curriculum, catalog titles)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..profile import Profile
from ..requirements.engine import ProgressReport
from ..requirements.schema import Curriculum


@dataclass
class ReportContext:
    profile: Profile
    curriculum: Curriculum | None = None
    progress: ProgressReport | None = None
    title_fn: Callable[[str], str | None] = field(default=lambda _c: None)

    def course_title(self, code: str) -> str:
        title = self.title_fn(code)
        return title or ""

    def stream_name(self) -> str | None:
        if not self.curriculum or not self.profile.elective_stream:
            return None
        stream = self.curriculum.stream(self.profile.elective_stream)
        return stream.name if stream else self.profile.elective_stream
