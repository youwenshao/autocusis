from autocusis.ingest.pdf_fetcher import pdf_url_for, subject_prefix


def test_subject_prefix():
    assert subject_prefix("ENGG2780") == "ENGG"
    assert subject_prefix("GESH2011") == "GESH"


def test_pdf_url_for_cse_and_engg():
    assert pdf_url_for("AIST1110").endswith("AIST1110.pdf")
    assert pdf_url_for("ENGG2780").endswith("ENGG2780.pdf")


def test_pdf_url_for_non_pdf_subjects():
    assert pdf_url_for("GESH2011") is None
    assert pdf_url_for("MATH1510") is None
