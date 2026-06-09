from autocusis.profile import CompletedCourse, Profile


def test_effective_completed_includes_exemptions():
    profile = Profile(
        completed=[CompletedCourse(code="ELTU2014")],
        prereq_satisfied=["ELTU1001", "ELTU1002"],
    )
    assert profile.completed_codes() == {"ELTU2014"}
    assert profile.prereq_satisfied_codes() == {"ELTU1001", "ELTU1002"}
    assert profile.effective_completed_codes() == {"ELTU2014", "ELTU1001", "ELTU1002"}
