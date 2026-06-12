from autocusis.ingest.enrollment import resolve_prerequisite_fields, split_enrollment
from autocusis.ingest.prereq import parse_prerequisite


def test_split_estr4999():
    text = (
        "Not for students who have taken BMEG4999 or CENG4999 or CSCI4999 or FTEC4999 "
        "or ELEG4999 or EEEN4999 or IERG4999 or MAEG4999 or SEEM4999. "
        "Pre-requisite: ESTR4998. Course Attributes Capstone Course"
    )
    prereq, exclusion = split_enrollment(text)
    assert prereq == "ESTR4998"
    assert "BMEG4999" in exclusion
    assert "SEEM4999" in exclusion


def test_split_estr3108():
    text = (
        "Not for students who have taken CSCI3230. "
        "Pre-requisite: CSCI2100 or CSCI2520 or ESTR2102 or equivalent."
    )
    prereq, exclusion = split_enrollment(text)
    assert exclusion == "CSCI3230"
    parsed = parse_prerequisite(prereq)
    assert parsed.kind == "or"
    assert parsed.referenced_codes() == {"CSCI2100", "CSCI2520", "ESTR2102"}


def test_split_gesh2012_sequential():
    text = (
        "For students who have already taken GESH2011 only. "
        "Not for students who have taken GESH4010."
    )
    prereq, exclusion = split_enrollment(text)
    assert prereq == "GESH2011"
    assert exclusion == "GESH4010"


def test_split_exclusion_only():
    text = "Not for students who have taken ESTR2540."
    prereq, exclusion = split_enrollment(text)
    assert prereq is None
    assert exclusion == "ESTR2540"


def test_split_gesh2011_college_restriction_only():
    text = (
        "1. For S.H. Ho College Year 2 or above students only. "
        "2. Not for students who have taken GESH4010. Course Attributes SDG-GE"
    )
    prereq, exclusion = split_enrollment(text)
    assert prereq is None
    assert exclusion == "GESH4010"


def test_csci2100_bare_numbers():
    text = (
        "Pre-requisite: AIST1110 or CSCI1120 or 1130 or 1510 or 1520 or 1530 or "
        "1540 or 1550 or ESTR1100 or ESTR1102 or ESTR2306 or IERG2080. "
        "For senior-year entrants, the prerequisite will be waived"
    )
    parsed = parse_prerequisite(text, subject_prefix="CSCI", course_code="CSCI2100")
    assert parsed.kind == "or"
    assert "CSCI1130" in parsed.referenced_codes()
    assert "CSCI1510" in parsed.referenced_codes()


def test_eltu3014_typo_and_exemptions():
    text = (
        "Preprequisite: (ELTU1001 or ELTU1002 or exemption from these courses) and "
        "(ELTU2005 or ELTU2014 or ELTU2024 or exemption from these courses)"
    )
    parsed = parse_prerequisite(text, course_code="ELTU3014")
    assert parsed.kind == "and"
    assert parsed.is_satisfied({"ELTU1001", "ELTU2014"})
    assert parsed.is_satisfied({"ELTU1002", "ELTU2024"})


def test_eltu3415_slash_and_exemption():
    text = (
        "ELTU1001/1002 or exemption from these courses AND "
        "ELTU2004/2005/2006/2011/2013/2014/2016/2017/2018/2019/2020/2022/2023/2024/2026/2406/2412 "
        "or exemption from these courses"
    )
    parsed = parse_prerequisite(text, course_code="ELTU3415")
    assert parsed.kind == "and"
    assert parsed.is_satisfied({"ELTU1001", "ELTU2014"})
    assert parsed.is_satisfied({"ELTU1002", "ELTU2024"})


def test_eltu3024_slash_groups():
    text = (
        "ELTU1001 / 1002 or exemption from these courses AND "
        "ELTU2005 / 2014 / 2024 or exemption from these courses"
    )
    parsed = parse_prerequisite(text, course_code="ELTU3024")
    assert parsed.kind == "and"
    assert parsed.is_satisfied({"ELTU1001", "ELTU2014"})


def test_eltu3414_or_bare_numbers():
    text = (
        "ELTU1001 or 1002 or exemption from these courses AND "
        "ELTU2004 or 2005 or 2014 or exemption from these courses"
    )
    parsed = parse_prerequisite(text, course_code="ELTU3414")
    assert parsed.kind == "and"
    assert parsed.is_satisfied({"ELTU1001", "ELTU2014"})


def test_seem3500_nested_expression():
    text = (
        "Prerequisites: SEEM2430 or ENGG2430 or ENGG2450 or ESTR2002 or ESTR2005 or "
        "(ENGG2760/ESTR2018 and ENGG2780/ESTR2020) with the approval of the course instructor"
    )
    parsed = parse_prerequisite(text)
    assert parsed.kind == "or"
    assert parsed.is_satisfied({"ENGG2760", "ENGG2780"})
    assert parsed.is_satisfied({"SEEM2430"})


def test_resolve_from_legacy_pdf_fields():
    prereq_raw, parsed, exclusion_raw, exclusion_codes = resolve_prerequisite_fields(
        course_code="CSCI2100",
        prereq_raw=(
            "AIST1110 or CSCI1120 or 1130 or 1510 or 1520 or 1530 or 1540 or 1550 "
            "or ESTR1100 or ESTR1102 or ESTR2306 or IERG2080"
        ),
        exclusion_raw=(
            "ESTR2102 or CSCI2520; Pre-requisite: AIST1110 or CSCI1120 or 1130 or 1510 "
            "or 1520 or 1530 or 1540 or 1550 or ESTR1100 or ESTR1102 or ESTR2306 or IERG2080. "
            "For senior-year entrants, the prerequisite will be waived"
        ),
    )
    assert parsed.kind == "or"
    assert "CSCI1130" in parsed.referenced_codes()
    assert set(exclusion_codes) == {"ESTR2102", "CSCI2520"}


def test_csci4430_numbered_enrollment_list():
    text = (
        "1. Prerequisite: CENG3150 or CSCI3150 or ESTR3102.\n"
        "2. Not for students who have taken ESTR3310 or ESTR4120 or IERG3310."
    )
    prereq_raw, parsed, exclusion_raw, exclusion_codes = resolve_prerequisite_fields(
        course_code="CSCI4430",
        enrollment=text,
    )
    assert prereq_raw == "CENG3150 or CSCI3150 or ESTR3102"
    assert parsed.kind == "or"
    assert parsed.referenced_codes() == {"CENG3150", "CSCI3150", "ESTR3102"}
    assert set(exclusion_codes) == {"ESTR3310", "ESTR4120", "IERG3310"}


def test_csci4430_pdf_truncation_tail():
    parsed = parse_prerequisite("CENG3150 or CSCI3150 or ESTR3102. 2")
    assert parsed.kind == "or"
    assert parsed.referenced_codes() == {"CENG3150", "CSCI3150", "ESTR3102"}


def test_split_aist4010_exclusion_before_prerequisite():
    text = (
        "Not for students who have taken ESTR4140\n"
        "Prerequisite: CSCI3230 or CSCI3320 or ESTR3108"
    )
    prereq, exclusion = split_enrollment(text)
    assert exclusion == "ESTR4140"
    assert "CSCI3230" in prereq
    _, parsed, _, exclusion_codes = resolve_prerequisite_fields(
        course_code="AIST4010",
        enrollment=text,
    )
    assert exclusion_codes == ["ESTR4140"]
    assert parsed.referenced_codes() == {"CSCI3230", "CSCI3320", "ESTR3108"}
