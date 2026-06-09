"""Tests for academic-term extrapolation."""

from autocusis.ingest.term_extrapolate import shift_term_label


def test_shift_term_label_forward():
    assert shift_term_label("2025-26 Term 1", 1) == "2026-27 Term 1"
    assert shift_term_label("2025-26 Term 2", 2) == "2027-28 Term 2"
