"""Tests for community data ingestion."""

from pathlib import Path

import pytest

from autocusis.ingest.adapters.eaglezhen import iter_eaglezhen_file
from autocusis.ingest.community_sync import sync_community
from autocusis.ingest.term_normalize import normalize_term
from autocusis.ingest.availability_store import AvailabilityStore
from autocusis.models import CourseAvailability, Term
from autocusis.sections.db import SectionsDB

FIXTURES = Path(__file__).parent / "fixtures" / "community"


def test_normalize_term():
    n = normalize_term("2025-26 Term 2")
    assert n is not None
    assert n.term == Term.TERM2
    assert n.year_label == "2025-26"


def test_eaglezhen_iter():
    records = list(
        iter_eaglezhen_file(
            FIXTURES / "eaglezhen_csci_snippet.json",
            term_filter="2025-26 Term 2",
        )
    )
    assert len(records) == 1
    assert records[0].course_code == "CSCI1020"
    assert len(records[0].sections) == 2


def test_sync_writes_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "autocusis.paths.sections_db_path",
        lambda: tmp_path / "sections.sqlite",
    )
    monkeypatch.setattr(
        "autocusis.paths.availability_path",
        lambda: tmp_path / "availability.yaml",
    )
    stats = sync_community(
        "eaglezhen",
        FIXTURES / "eaglezhen_csci_snippet.json",
        "2025-26 Term 2",
    )
    assert stats.courses_written == 1
    assert stats.bundles_written >= 1
    db = SectionsDB(tmp_path / "sections.sqlite")
    bundles = db.load_bundles("CSCI1020", "2025-26 Term 2")
    assert bundles


def test_manual_availability_not_overwritten(tmp_path, monkeypatch):
    avail_path = tmp_path / "availability.yaml"
    monkeypatch.setattr(
        "autocusis.paths.availability_path",
        lambda: avail_path,
    )
    monkeypatch.setattr(
        "autocusis.paths.sections_db_path",
        lambda: tmp_path / "sections.sqlite",
    )
    store = AvailabilityStore(
        {
            "CSCI1020": CourseAvailability(
                code="CSCI1020",
                terms=[Term.TERM1],
                source="manual",
                note="pinned",
            )
        }
    )
    store.save(avail_path)

    sync_community(
        "eaglezhen",
        FIXTURES / "eaglezhen_csci_snippet.json",
        "2025-26 Term 2",
    )
    loaded = AvailabilityStore.load(avail_path)
    av = loaded.records["CSCI1020"]
    assert av.source == "manual"
    assert av.terms == [Term.TERM1]
