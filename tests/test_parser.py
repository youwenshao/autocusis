"""PDF parser tests. Skipped automatically if sample PDFs aren't cached."""

from pathlib import Path

import pytest

from autocusis.ingest.pdf_fetcher import extract_text
from autocusis.ingest.pdf_parser import parse_course_text

SAMPLE = Path("/tmp/AIST1110.pdf")


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample PDF not cached at /tmp/AIST1110.pdf")
def test_parse_aist1110():
    course = parse_course_text("AIST1110", extract_text(SAMPLE))
    assert course.code == "AIST1110"
    assert course.title_en == "Introduction to Computing using Python"
    assert course.title_zh and "Python" in course.title_zh
    assert course.units == 3.0
    assert course.prerequisite.referenced_codes() == {"ENGG1110", "ESTR1002"}
    assert "CSCI1120" in course.exclusion_codes
    assert "LEC" in course.components
    assert course.description_en and course.description_en.startswith("This course")
