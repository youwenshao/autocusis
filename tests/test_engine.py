from autocusis.profile import CompletedCourse, Profile
from autocusis.requirements.engine import build_demand, evaluate, one_of_gap_summary
from autocusis.requirements.schema import Curriculum, RequirementGroup


def _curriculum():
    return Curriculum(
        program="AIST",
        total_credits_required=30,
        groups=[
            RequirementGroup(id="core", name="Core", kind="all_of", courses=["A100", "A200"]),
            RequirementGroup(
                id="ele", name="Electives", kind="credits_from", min_credits=6,
                courses=["E1", "E2", "E3"],
            ),
            RequirementGroup(
                id="pkg", name="Package", kind="count_from", min_count=2,
                courses=["P1", "P2", "P3"],
            ),
        ],
    )


def test_progress_and_demand():
    profile = Profile(completed=[CompletedCourse(code="A100"), CompletedCourse(code="E1")])
    report = evaluate(_curriculum(), profile, credit_fn=lambda c: 3.0)

    core = next(g for g in report.groups if g.id == "core")
    assert not core.satisfied
    assert core.outstanding_required == ["A200"]

    ele = next(g for g in report.groups if g.id == "ele")
    assert ele.credits_done == 3.0
    assert ele.credits_remaining == 3.0

    demand = build_demand(report)
    assert demand.mandatory == ["A200"]
    assert demand.min_total_planned_credits == report.total_credits_remaining
    kinds = {e.group_id: e for e in demand.electives}
    assert kinds["ele"].need_credits == 3.0
    assert kinds["pkg"].need_count == 2


def test_all_satisfied():
    completed = [CompletedCourse(code=c) for c in ["A100", "A200", "E1", "E2", "P1", "P2", "X1", "X2", "X3", "X4"]]
    profile = Profile(completed=completed)
    report = evaluate(_curriculum(), profile, credit_fn=lambda c: 3.0)
    assert report.total_credits_done >= report.total_credits_required
    assert all(g.satisfied for g in report.groups)


def _fyp_curriculum():
    return Curriculum(
        program="AIST",
        total_credits_required=6,
        groups=[
            RequirementGroup(
                id="research",
                name="Research",
                kind="one_of",
                tracks=[["AIST4998", "AIST4999"], ["ESTR4998", "ESTR4999"]],
            ),
        ],
    )


def test_one_of_mixed_tracks_not_satisfied():
    profile = Profile(completed=[CompletedCourse(code="AIST4998"), CompletedCourse(code="ESTR4998")])
    report = evaluate(_fyp_curriculum(), profile, credit_fn=lambda c: 3.0)
    research = next(g for g in report.groups if g.id == "research")
    assert not research.satisfied
    assert not research.tracks_viable
    assert "Mixed tracks" in (research.note or "")


def test_one_of_thesis_i_only_not_satisfied():
    profile = Profile(completed=[CompletedCourse(code="AIST4998")])
    report = evaluate(_fyp_curriculum(), profile, credit_fn=lambda c: 3.0)
    research = next(g for g in report.groups if g.id == "research")
    assert not research.satisfied
    demand = build_demand(report)
    assert demand.mandatory == ["AIST4999"]
    assert not demand.electives


def test_one_of_complete_track_satisfied():
    profile = Profile(
        completed=[CompletedCourse(code="ESTR4998"), CompletedCourse(code="ESTR4999")],
    )
    report = evaluate(_fyp_curriculum(), profile, credit_fn=lambda c: 3.0)
    research = next(g for g in report.groups if g.id == "research")
    assert research.satisfied
    demand = build_demand(report)
    assert demand.mandatory == []
    assert demand.electives == []


def test_one_of_unstarted_builds_track_demand():
    profile = Profile()
    report = evaluate(_fyp_curriculum(), profile, credit_fn=lambda c: 3.0)
    demand = build_demand(report)
    assert demand.mandatory == []
    assert len(demand.electives) == 1
    assert demand.electives[0].kind == "one_of"
    assert demand.electives[0].tracks == [["AIST4998", "AIST4999"]]
    assert demand.electives[0].pool == []
    assert demand.electives[0].need_count == 0

    research = next(g for g in report.groups if g.id == "research")
    summary = one_of_gap_summary(research)
    assert summary is not None
    assert "pick one of 2 tracks" in summary
    assert "AIST4998 + AIST4999" in summary
    assert "ESTR4998 + ESTR4999" in summary


def test_credits_from_caps_and_equivalence_in_demand():
    curriculum = Curriculum.load(
        __import__("autocusis.paths", fromlist=["default_requirements_path"]).default_requirements_path()
    )
    english = curriculum.group("english_y3")
    assert english is not None
    assert english.max_credits == 2
    assert english.max_pool_count == 1

    aist_req = curriculum.group("aist_required")
    assert aist_req is not None
    assert len(aist_req.normalized_equivalence_tracks()) == 4

    profile = Profile()
    report = evaluate(curriculum, profile, credit_fn=lambda c: 3.0)
    demand = build_demand(report)
    english_d = next(e for e in demand.electives if e.group_id == "english_y3")
    assert english_d.max_credits == 2
    assert english_d.max_pool_count == 1

    req_d = next(e for e in demand.electives if e.group_id == "aist_required")
    assert any("ESTR4140" in track for track in req_d.equivalence_tracks)


def _real_curriculum() -> Curriculum:
    return Curriculum.load(
        __import__(
            "autocusis.paths", fromlist=["default_requirements_path"]
        ).default_requirements_path()
    )


def test_elective_streams_parse_and_lookup():
    curriculum = _real_curriculum()
    ids = {s.id for s in curriculum.elective_streams}
    assert ids == {
        "large_scale_ai",
        "multimedia",
        "manufacturing_robotics",
        "biomedical",
    }
    assert curriculum.stream("biomedical") is not None
    assert curriculum.stream("does_not_exist") is None


def test_elective_stream_mapping_is_clean():
    """Every pool course maps to one stream (bar a few neutral generics)."""
    curriculum = _real_curriculum()
    pool = set(curriculum.group("major_electives").normalized_courses())
    c2s = curriculum.course_to_stream()
    mapped = set(c2s)

    # No stream references a course outside the elective pool.
    assert mapped <= pool

    # No course is claimed by two streams.
    from collections import Counter

    counts = Counter(
        code for s in curriculum.elective_streams for code in s.normalized_courses()
    )
    assert [code for code, n in counts.items() if n > 1] == []

    # Only the intentional neutral fallbacks are left unmapped.
    assert pool - mapped == {"ENGG1820", "ENGG2720", "ESTR2014"}

    # All biomedical-pool BMEG courses live in the biomedical stream.
    assert all(
        c2s[c] == "biomedical" for c in pool if c.startswith("BMEG")
    )
