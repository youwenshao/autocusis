from autocusis.ingest.prereq import extract_codes, parse_prerequisite


def test_bare_numbers_expand_with_subject():
    e = parse_prerequisite(
        "CSCI1120 or 1130 or 1510",
        subject_prefix="CSCI",
        course_code="CSCI2100",
    )
    assert e.kind == "or"
    assert e.referenced_codes() == {"CSCI1120", "CSCI1130", "CSCI1510"}


def test_or_of_courses():
    e = parse_prerequisite("ENGG1110 or ESTR1002")
    assert e.kind == "or"
    assert e.referenced_codes() == {"ENGG1110", "ESTR1002"}
    assert e.is_satisfied({"ESTR1002"})
    assert not e.is_satisfied({"XXXX1000"})


def test_slash_is_alternative():
    e = parse_prerequisite("ENGG1120/ESTR1005 or MATH1510")
    assert e.is_satisfied({"ESTR1005"})
    assert e.is_satisfied({"MATH1510"})
    assert not e.is_satisfied({"ENGG1130"})


def test_slash_binds_tighter_than_and():
    e = parse_prerequisite("CSCI1120/ESTR1100 and MATH1510")
    assert e.kind == "and"
    assert e.is_satisfied({"CSCI1120", "MATH1510"})
    assert not e.is_satisfied({"CSCI1120"})


def test_consent_only_becomes_no_prereq():
    e = parse_prerequisite("Consent of instructor")
    assert e.kind == "none"


def test_consent_phrase_stripping():
    cases = [
        ("BMEG3320 or ENGG2030 or with the consent of the instructor", {"BMEG3320"}),
        ("ELEG3503 or consent of the instructor", {"ELEG3503"}),
        ("BMEG3320 or with the consent of the course instructor", {"BMEG3320"}),
    ]
    for text, satisfied in cases:
        parsed = parse_prerequisite(text)
        assert parsed.kind != "raw", text
        assert parsed.is_satisfied(satisfied), text


def test_entrant_waiver_stripping():
    cases = [
        (
            "CSCI2100 or CSCI2520 or ESTR2102. For 2nd-year entrants, the prerequisite will be waived",
            {"CSCI2100"},
        ),
        (
            "AIST1110 or CSCI1120 or ESTR1130. For senior-year entrants, the pre-requisite will be waived",
            {"CSCI1120"},
        ),
    ]
    for text, satisfied in cases:
        parsed = parse_prerequisite(text, course_code="CSCI3170")
        assert parsed.kind == "or", text
        assert parsed.is_satisfied(satisfied), text


def test_paren_slash_and_with_consent():
    text = (
        "(EEEN2040 / ESTR2404) and (MAEG2030 / ESTR2402 or with the consent of the course instructor)"
    )
    parsed = parse_prerequisite(text)
    assert parsed.kind == "and"
    assert parsed.is_satisfied({"EEEN2040", "MAEG2030"})
    assert parsed.is_satisfied({"ESTR2404", "ESTR2402"})


def test_extract_codes():
    assert extract_codes("Not for students who have taken CSCI1120 or ESTR1100") == [
        "CSCI1120",
        "ESTR1100",
    ]
