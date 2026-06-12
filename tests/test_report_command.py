"""CLI smoke tests for autocusis report."""

from pathlib import Path

from typer.testing import CliRunner

from autocusis.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "plan_section_snippet.json"


def test_report_command(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["report", "--from", str(FIXTURE), "--out", str(tmp_path / "report")],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report" / "course-sheet.md").exists()
    assert (tmp_path / "report" / "index.html").exists()
