"""Tests for academic calendar mapping."""

from autocusis.calendar import slot_label
from autocusis.models import Term
from autocusis.profile import Profile


def test_slot_label_anchor():
    profile = Profile(
        start_year_label="2026-27",
        current_year=3,
        current_term=Term.TERM1,
    )
    assert slot_label(profile, 1, Term.TERM1) == "2026-27 Term 1"
    assert slot_label(profile, 1, Term.TERM2) == "2026-27 Term 2"
    assert slot_label(profile, 2, Term.TERM1) == "2027-28 Term 1"
