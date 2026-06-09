from autocusis.ingest.availability_store import AvailabilityStore
from autocusis.models import Course, PrereqExpr, Term
from autocusis.profile import PriorityPin, Profile
from autocusis.requirements.engine import ElectiveDemand, ScheduleDemand
from autocusis.scheduler.solver import SchedulerInput, solve


def _course(code, prereq=None, units=3.0, excl=None):
    return Course(
        code=code,
        title_en=code,
        units=units,
        prerequisite=prereq or PrereqExpr.none(),
        exclusion_codes=excl or [],
    )


def _avail(codes_terms):
    av = AvailabilityStore()
    for code, terms in codes_terms.items():
        av.set_manual(code, terms)
    return av


def test_prereq_ordering_and_caps():
    catalog = {
        "C100": _course("C100"),
        "C200": _course("C200", PrereqExpr.course("C100")),
        "C300": _course("C300", PrereqExpr.course("C200")),
        "E1": _course("E1"),
        "E2": _course("E2"),
    }
    av = _avail(
        {
            "C100": [Term.TERM1, Term.TERM2],
            "C200": [Term.TERM1, Term.TERM2],
            "C300": [Term.TERM1],
            "E1": [Term.TERM1, Term.TERM2],
            "E2": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        mandatory=["C100", "C200", "C300"],
        electives=[ElectiveDemand(group_id="e", group_name="E", kind="credits_from", pool=["E1", "E2"], need_credits=6)],
    )
    profile = Profile(max_credits_per_term=6, max_credits_per_year=12, planning_horizon_years=4)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    slot = {c.code: c.planning_year * 10 + int(c.term) for s in plan.semesters for c in s.courses}
    assert slot["C100"] < slot["C200"] < slot["C300"]
    assert slot["C300"] % 10 == 1  # Term 1 only
    assert all(s.total_credits <= 6 for s in plan.semesters)


def test_aist_practicum_same_term2():
    """AIST2601 and AIST2602 are co-requisites offered in Term 2 only."""
    catalog = {
        "AIST2601": _course("AIST2601", units=2),
        "AIST2602": _course("AIST2602", units=1),
        "C100": _course("C100"),
    }
    av = _avail(
        {
            "AIST2601": [Term.TERM2],
            "AIST2602": [Term.TERM2],
            "C100": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(mandatory=["AIST2601", "AIST2602", "C100"])
    profile = Profile(max_credits_per_term=18, planning_horizon_years=3, planning_mode="spread")
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    sem_idx: dict[str, int] = {}
    term_by_code: dict[str, Term] = {}
    for i, sem in enumerate(plan.semesters):
        for c in sem.courses:
            if c.code in ("AIST2601", "AIST2602"):
                sem_idx[c.code] = i
                term_by_code[c.code] = c.term
    assert sem_idx["AIST2601"] == sem_idx["AIST2602"]
    assert term_by_code["AIST2601"] == Term.TERM2
    assert term_by_code["AIST2602"] == Term.TERM2


def test_gesh2011_before_gesh2012():
    """GESH2012 enrollment requires prior completion of GESH2011."""
    catalog = {
        "GESH2011": _course("GESH2011", units=1),
        "GESH2012": _course("GESH2012", PrereqExpr.course("GESH2011"), units=2),
    }
    av = _avail(
        {
            "GESH2011": [Term.TERM1],
            "GESH2012": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(mandatory=["GESH2011", "GESH2012"])
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    sem_idx: dict[str, int] = {}
    for i, sem in enumerate(plan.semesters):
        for c in sem.courses:
            if c.code in ("GESH2011", "GESH2012"):
                sem_idx[c.code] = i
    assert sem_idx["GESH2012"] == sem_idx["GESH2011"] + 1


def test_mutual_exclusion_infeasible():
    catalog = {"X1": _course("X1", excl=["X2"]), "X2": _course("X2")}
    av = _avail({"X1": [Term.TERM1, Term.TERM2], "X2": [Term.TERM1, Term.TERM2]})
    demand = ScheduleDemand(mandatory=["X1", "X2"])
    plan = solve(SchedulerInput(demand=demand, profile=Profile(), catalog=catalog, availability=av))[0]
    assert not plan.feasible


def test_pins_and_fillers():
    catalog = {"A100": _course("A100"), "A200": _course("A200")}
    av = _avail({"A100": [Term.TERM1, Term.TERM2], "A200": [Term.TERM1, Term.TERM2]})
    demand = ScheduleDemand(
        mandatory=["A100", "A200"],
        electives=[ElectiveDemand(group_id="free", group_name="Free", kind="credits_from", pool=[], need_credits=9)],
    )
    profile = Profile(planning_horizon_years=4, priority_pins=[PriorityPin(code="A200", year=2, term=Term.TERM2)])
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    fillers = [c for s in plan.semesters for c in s.courses if c.is_filler]
    assert len(fillers) == 3
    a200 = next(c for s in plan.semesters for c in s.courses if c.code == "A200")
    assert (a200.planning_year, int(a200.term)) == (2, 2)


def test_min_credits_per_active_term():
    catalog = {
        "C100": _course("C100", units=3),
        "C200": _course("C200", units=3),
        "C300": _course("C300", units=3),
    }
    av = _avail(
        {
            "C100": [Term.TERM1, Term.TERM2],
            "C200": [Term.TERM1, Term.TERM2],
            "C300": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(mandatory=["C100", "C200", "C300"])
    profile = Profile(max_credits_per_term=12, min_credits_per_term=9, planning_horizon_years=2)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    active = [s for s in plan.semesters if s.courses]
    assert active
    assert all(s.total_credits >= 9 for s in active)


def test_one_of_fyp_pairing():
    catalog = {
        "AIST4998": _course("AIST4998"),
        "AIST4999": _course("AIST4999", PrereqExpr.course("AIST4998")),
        "ESTR4998": _course("ESTR4998"),
        "ESTR4999": _course("ESTR4999", PrereqExpr.course("ESTR4998")),
    }
    av = _avail(
        {
            "AIST4998": [Term.TERM1, Term.TERM2],
            "AIST4999": [Term.TERM1, Term.TERM2],
            "ESTR4998": [Term.TERM1, Term.TERM2],
            "ESTR4999": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="research",
                group_name="Research",
                kind="one_of",
                tracks=[["AIST4998", "AIST4999"]],
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=4)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert taken == {"AIST4998", "AIST4999"}
    slot = {c.code: c.planning_year * 10 + int(c.term) for s in plan.semesters for c in s.courses}
    last_py = profile.planning_horizon_years
    penultimate = last_py * 10 + int(Term.TERM1)
    final_slot = last_py * 10 + int(Term.TERM2)
    assert slot["AIST4998"] == penultimate
    assert slot["AIST4999"] == final_slot


def test_prereq_exemption_skips_closure():
    """Waived foundation English satisfies ELTU3014 prereqs without pulling ELTU1001."""
    catalog = {
        "ELTU3014": _course(
            "ELTU3014",
            PrereqExpr.all_of(
                [
                    PrereqExpr.any_of(
                        [PrereqExpr.course("ELTU1001"), PrereqExpr.course("ELTU1002")]
                    ),
                    PrereqExpr.any_of(
                        [PrereqExpr.course("ELTU2005"), PrereqExpr.course("ELTU2014")]
                    ),
                ]
            ),
        ),
        "ELTU1001": _course("ELTU1001"),
    }
    av = _avail(
        {
            "ELTU3014": [Term.TERM1, Term.TERM2],
            "ELTU1001": [Term.TERM1, Term.TERM2],
        }
    )
    from autocusis.profile import CompletedCourse

    demand = ScheduleDemand(mandatory=["ELTU3014"])
    profile = Profile(
        completed=[CompletedCourse(code="ELTU2014")],
        prereq_satisfied=["ELTU1001", "ELTU1002"],
        max_credits_per_term=12,
        planning_horizon_years=2,
    )
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert "ELTU3014" in taken
    assert "ELTU1001" not in taken


def test_one_of_locked_track_after_thesis_i():
    catalog = {
        "AIST4998": _course("AIST4998"),
        "AIST4999": _course("AIST4999", PrereqExpr.course("AIST4998")),
        "ESTR4998": _course("ESTR4998"),
        "ESTR4999": _course("ESTR4999", PrereqExpr.course("ESTR4998")),
    }
    av = _avail(
        {
            "AIST4998": [Term.TERM1, Term.TERM2],
            "AIST4999": [Term.TERM1, Term.TERM2],
            "ESTR4998": [Term.TERM1, Term.TERM2],
            "ESTR4999": [Term.TERM1, Term.TERM2],
        }
    )
    from autocusis.profile import CompletedCourse

    demand = ScheduleDemand(mandatory=["AIST4999"])
    profile = Profile(
        completed=[CompletedCourse(code="AIST4998")],
        max_credits_per_term=6,
        planning_horizon_years=4,
    )
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert taken == {"AIST4999"}
    slot = {c.code: c.planning_year * 10 + int(c.term) for s in plan.semesters for c in s.courses}
    assert slot["AIST4999"] == profile.planning_horizon_years * 10 + int(Term.TERM2)


def test_fyp_spread_mode_in_final_year():
    catalog = {
        "AIST4998": _course("AIST4998"),
        "AIST4999": _course("AIST4999", PrereqExpr.course("AIST4998")),
        "C1": _course("C1"),
        "C2": _course("C2"),
        "C3": _course("C3"),
        "C4": _course("C4"),
    }
    av = _avail(
        {
            "AIST4998": [Term.TERM1, Term.TERM2],
            "AIST4999": [Term.TERM1, Term.TERM2],
            **{f"C{i}": [Term.TERM1, Term.TERM2] for i in range(1, 5)},
        }
    )
    demand = ScheduleDemand(
        mandatory=["C1", "C2", "C3", "C4"],
        electives=[
            ElectiveDemand(
                group_id="research",
                group_name="Research",
                kind="one_of",
                tracks=[["AIST4998", "AIST4999"]],
            )
        ],
    )
    profile = Profile(
        planning_mode="spread",
        current_year=3,
        current_term=Term.TERM1,
        max_credits_per_term=12,
        min_credits_per_term=9,
        planning_horizon_years=5,
    )
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]
    assert plan.feasible
    slot = {c.code: c.planning_year * 10 + int(c.term) for s in plan.semesters for c in s.courses}
    remaining_years = profile.planning_horizon_years - profile.current_year + 1
    assert slot["AIST4998"] == remaining_years * 10 + int(Term.TERM1)
    assert slot["AIST4999"] == remaining_years * 10 + int(Term.TERM2)


def test_undersupplied_elective_pool_reports_note():
    catalog = {"E1": _course("E1"), "E2": _course("E2")}
    av = _avail({"E1": [Term.TERM1], "E2": [Term.TERM1]})
    demand = ScheduleDemand(
        electives=[ElectiveDemand(group_id="e", group_name="Big", kind="credits_from", pool=["E1", "E2"], need_credits=99)],
    )
    plan = solve(SchedulerInput(demand=demand, profile=Profile(), catalog=catalog, availability=av))[0]
    assert any("Big" in n for n in plan.notes)


def test_spread_mode_uses_more_terms_than_fast():
    catalog = {
        f"C{i}": _course(f"C{i}", units=3) for i in range(1, 7)
    }
    av = _avail({f"C{i}": [Term.TERM1, Term.TERM2] for i in range(1, 7)})
    demand = ScheduleDemand(mandatory=[f"C{i}" for i in range(1, 7)])
    base = Profile(
        max_credits_per_term=18,
        min_credits_per_term=9,
        planning_horizon_years=3,
        current_year=1,
        current_term=Term.TERM1,
    )
    fast = solve(
        SchedulerInput(
            demand=demand,
            profile=base.model_copy(update={"planning_mode": "fast"}),
            catalog=catalog,
            availability=av,
        )
    )[0]
    spread = solve(
        SchedulerInput(
            demand=demand,
            profile=base.model_copy(update={"planning_mode": "spread"}),
            catalog=catalog,
            availability=av,
        )
    )[0]
    assert fast.feasible and spread.feasible
    assert spread.objective_terms_used > fast.objective_terms_used
    assert spread.peak_term_credits <= fast.peak_term_credits


def test_english_y3_max_one_course():
    catalog = {
        "ELTU3014": _course("ELTU3014", units=2.0),
        "ELTU3024": _course("ELTU3024", units=2.0),
        "FILL1": _course("FILL1", units=3.0),
        "FILL2": _course("FILL2", units=3.0),
        "FILL3": _course("FILL3", units=3.0),
    }
    av = _avail(
        {
            "ELTU3014": [Term.TERM1, Term.TERM2],
            "ELTU3024": [Term.TERM1, Term.TERM2],
            "FILL1": [Term.TERM1, Term.TERM2],
            "FILL2": [Term.TERM1, Term.TERM2],
            "FILL3": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="english_y3",
                group_name="English Y3",
                kind="credits_from",
                pool=["ELTU3014", "ELTU3024"],
                need_credits=2,
                max_credits=2,
                max_pool_count=1,
            ),
            ElectiveDemand(
                group_id="pad",
                group_name="Pad",
                kind="credits_from",
                pool=["FILL1", "FILL2", "FILL3"],
                need_credits=9,
            ),
        ],
    )
    profile = Profile(
        planning_mode="spread",
        max_credits_per_term=12,
        min_credits_per_term=9,
        planning_horizon_years=2,
        priority_pins=[PriorityPin(code="ELTU3014", year=1, term=Term.TERM2)],
    )
    plan = solve(
        SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av)
    )[0]
    assert plan.feasible
    eltu = [c.code for s in plan.semesters for c in s.courses if c.code.startswith("ELTU")]
    assert eltu == ["ELTU3014"]


def test_equivalence_tracks_at_most_one():
    catalog = {
        "AIST4010": _course("AIST4010", excl=["ESTR4140"]),
        "ESTR4140": _course("ESTR4140", excl=["AIST4010"]),
        "FILL": _course("FILL"),
    }
    av = _avail(
        {
            "AIST4010": [Term.TERM1, Term.TERM2],
            "ESTR4140": [Term.TERM1, Term.TERM2],
            "FILL": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="req",
                group_name="Required",
                kind="credits_from",
                pool=["AIST4010", "ESTR4140", "FILL"],
                need_credits=6,
                equivalence_tracks=[["AIST4010", "ESTR4140"]],
            )
        ],
    )
    plan = solve(
        SchedulerInput(
            demand=demand,
            profile=Profile(max_credits_per_term=12, planning_horizon_years=2),
            catalog=catalog,
            availability=av,
        )
    )[0]
    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert not ({"AIST4010", "ESTR4140"} <= taken)


def test_spread_prefers_csci_over_estr():
    catalog = {
        "AIST4010": _course("AIST4010", excl=["ESTR4140"]),
        "ESTR4140": _course("ESTR4140", excl=["AIST4010"]),
        "CSCI3130": _course("CSCI3130"),
    }
    av = _avail(
        {
            "AIST4010": [Term.TERM1, Term.TERM2],
            "ESTR4140": [Term.TERM1, Term.TERM2],
            "CSCI3130": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="req",
                group_name="Required",
                kind="credits_from",
                pool=["AIST4010", "ESTR4140"],
                need_credits=3,
                equivalence_tracks=[["AIST4010", "ESTR4140"]],
            ),
            ElectiveDemand(
                group_id="ele",
                group_name="Elective",
                kind="credits_from",
                pool=["CSCI3130"],
                need_credits=3,
            ),
        ],
    )
    profile = Profile(planning_mode="spread", max_credits_per_term=6, planning_horizon_years=2)
    plan = solve(
        SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av)
    )[0]
    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert "AIST4010" in taken
    assert "ESTR4140" not in taken


def test_elective_tiebreak_is_stable_across_runs():
    catalog = {
        "C100": _course("C100"),
        "E1": _course("E1"),
        "E2": _course("E2"),
        "E3": _course("E3"),
    }
    av = _avail(
        {
            "C100": [Term.TERM1, Term.TERM2],
            "E1": [Term.TERM1, Term.TERM2],
            "E2": [Term.TERM1, Term.TERM2],
            "E3": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        mandatory=["C100"],
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["E3", "E1", "E2"],
                need_credits=6,
                max_credits=6,
            )
        ],
    )
    profile = Profile(
        planning_mode="spread",
        max_credits_per_term=12,
        min_credits_per_term=6,
        planning_horizon_years=2,
    )
    inp = SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av)

    def signature():
        plan = solve(inp, max_plans=1, time_limit_s=10.0)[0]
        assert plan.feasible
        return tuple(sorted(c.code for s in plan.semesters for c in s.courses if not c.is_filler))

    assert signature() == signature()
    assert set(signature()) == {"C100", "E1", "E2"}


def test_total_credit_floor_beyond_group_minimums():
    """Plan must reach min_total_planned_credits, not only per-group floors."""
    catalog = {
        "C100": _course("C100"),
        "E1": _course("E1"),
        "E2": _course("E2"),
        "E3": _course("E3"),
    }
    av = _avail(
        {
            "C100": [Term.TERM1, Term.TERM2],
            "E1": [Term.TERM1, Term.TERM2],
            "E2": [Term.TERM1, Term.TERM2],
            "E3": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        mandatory=["C100"],
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["E1", "E2", "E3"],
                need_credits=3,
            )
        ],
        min_total_planned_credits=9,
    )
    profile = Profile(max_credits_per_term=9, planning_horizon_years=2)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    assert plan.total_planned_credits == 9
    planned = {c.code for s in plan.semesters for c in s.courses}
    assert "C100" in planned
    assert len(planned) >= 3  # group floor (3 cr) alone would be 2 courses; floor needs 3


def test_spread_mode_does_not_overshoot_degree_floor():
    catalog = {
        "C100": _course("C100"),
        "E1": _course("E1"),
        "E2": _course("E2"),
        "E3": _course("E3"),
    }
    av = _avail(
        {
            "C100": [Term.TERM1, Term.TERM2],
            "E1": [Term.TERM1, Term.TERM2],
            "E2": [Term.TERM1, Term.TERM2],
            "E3": [Term.TERM1, Term.TERM2],
        }
    )
    demand = ScheduleDemand(
        mandatory=["C100"],
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["E1", "E2", "E3"],
                need_credits=3,
            )
        ],
        min_total_planned_credits=9,
    )
    profile = Profile(
        planning_mode="spread",
        max_credits_per_term=12,
        min_credits_per_term=3,
        planning_horizon_years=2,
    )
    plan = solve(
        SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av),
        time_limit_s=10.0,
    )[0]
    assert plan.feasible
    assert plan.total_planned_credits == 9


def _stream_pool_setup():
    catalog = {c: _course(c) for c in ["A1", "A2", "B1", "B2"]}
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["A1", "A2", "B1", "B2"],
                need_credits=6,
                max_credits=6,
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    return catalog, av, demand, profile


def test_stream_preference_overrides_lexicographic_tiebreak():
    """A chosen stream beats the alphabetical tie-break (the BMEG-cluster fix)."""
    catalog, av, demand, profile = _stream_pool_setup()

    # No stream: the lexicographically earliest pair fills the pool.
    base = solve(
        SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av)
    )[0]
    assert base.feasible
    assert {c.code for s in base.semesters for c in s.courses} == {"A1", "A2"}

    # With a stream: in-stream courses win despite later codes.
    inp = SchedulerInput(
        demand=demand,
        profile=profile,
        catalog=catalog,
        availability=av,
        preferred_stream="s1",
        course_stream={"B1": "s1", "B2": "s1"},
    )
    plan = solve(inp)[0]
    assert plan.feasible
    assert {c.code for s in plan.semesters for c in s.courses} == {"B1", "B2"}


def test_stream_preference_is_soft_and_falls_back():
    """When the stream cannot supply enough credits, fill from outside it."""
    catalog = {c: _course(c) for c in ["A1", "A2", "B1"]}
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["A1", "A2", "B1"],
                need_credits=6,
                max_credits=6,
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    inp = SchedulerInput(
        demand=demand,
        profile=profile,
        catalog=catalog,
        availability=av,
        preferred_stream="s1",
        course_stream={"B1": "s1"},
    )
    plan = solve(inp)[0]
    assert plan.feasible
    taken = {c.code for s in plan.semesters for c in s.courses}
    assert "B1" in taken  # the single in-stream course is always picked
    assert len(taken) == 2  # fell back to exactly one out-of-stream course
    assert "A1" in taken  # lexicographic tie-break decides the fallback


def test_stream_preference_is_stable_across_runs():
    catalog = {c: _course(c) for c in ["A1", "A2", "B1", "B2", "B3"]}
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["A1", "A2", "B1", "B2", "B3"],
                need_credits=6,
                max_credits=6,
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    inp = SchedulerInput(
        demand=demand,
        profile=profile,
        catalog=catalog,
        availability=av,
        preferred_stream="s1",
        course_stream={"B1": "s1", "B2": "s1", "B3": "s1"},
    )

    def signature():
        plan = solve(inp, max_plans=1, time_limit_s=10.0)[0]
        assert plan.feasible
        return tuple(sorted(c.code for s in plan.semesters for c in s.courses))

    assert signature() == signature()
    # In-stream, then lexicographically earliest within the stream.
    assert set(signature()) == {"B1", "B2"}


def test_or_prereq_alternatives_not_scheduled_as_filler():
    """OR prereq arms must not enter the plan just to hit the credit floor."""
    from autocusis.profile import CompletedCourse

    catalog = {
        "C100": _course("C100"),
        "BASE": _course("BASE"),
        "E1": _course(
            "E1",
            PrereqExpr.any_of([PrereqExpr.course("ALT"), PrereqExpr.course("BASE")]),
        ),
        "ALT": _course("ALT", units=2),
        "E2": _course("E2"),
    }
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        mandatory=["C100"],
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["E1", "E2"],
                need_credits=3,
            )
        ],
        min_total_planned_credits=9,
    )
    profile = Profile(
        max_credits_per_term=9,
        planning_horizon_years=2,
        completed=[CompletedCourse(code="BASE", credits=3)],
    )
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    planned = {c.code for s in plan.semesters for c in s.courses}
    assert "ALT" not in planned


def test_out_of_pool_prereq_only_when_dependent_taken():
    """Out-of-pool AND prereqs may be taken only to unlock an in-pool dependent."""
    catalog = {
        "PRE": _course("PRE", units=2),
        "E1": _course("E1", PrereqExpr.course("PRE")),
        "E2": _course("E2"),
    }
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["E1", "E2"],
                need_credits=3,
                max_credits=3,
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    plan = solve(SchedulerInput(demand=demand, profile=profile, catalog=catalog, availability=av))[0]

    assert plan.feasible
    planned = {c.code for s in plan.semesters for c in s.courses}
    assert planned == {"E2"}
    assert "PRE" not in planned


def test_stream_penalty_ignores_neutral_unmapped_courses():
    """Neutral unmapped electives should not dilute cross-stream preference."""
    catalog = {c: _course(c) for c in ["N1", "S1", "X1"]}
    av = _avail({c: [Term.TERM1, Term.TERM2] for c in catalog})
    demand = ScheduleDemand(
        electives=[
            ElectiveDemand(
                group_id="e",
                group_name="Electives",
                kind="credits_from",
                pool=["N1", "S1", "X1"],
                need_credits=6,
                max_credits=6,
            )
        ],
    )
    profile = Profile(max_credits_per_term=6, planning_horizon_years=2)
    inp = SchedulerInput(
        demand=demand,
        profile=profile,
        catalog=catalog,
        availability=av,
        preferred_stream="multimedia",
        course_stream={"S1": "multimedia", "X1": "biomedical"},
    )
    plan = solve(inp)[0]
    assert plan.feasible
    assert {c.code for s in plan.semesters for c in s.courses} == {"N1", "S1"}
