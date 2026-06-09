"""Download course-catalog PDFs and extract their raw text.

CSE-hosted subjects (AIST, CSCI, ESTR, SEEM, CENG, ...) publish per-course
catalog PDFs at a predictable URL. Non-CSE subjects are not on this host and
must be sourced via the authenticated catalog scraper (see Phase 2).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import requests

from ..paths import pdf_cache_dir

# Subjects whose per-course catalog PDFs are hosted on the CSE department site
# (includes cross-faculty codes like ENGG/MATH-adjacent foundation courses).
CSE_SUBJECTS = {"AIST", "CSCI", "ESTR", "SEEM", "CENG", "CEng", "ENGG"}

CSE_PDF_TEMPLATE = (
    "https://www.cse.cuhk.edu.hk/wp-content/uploads/academics/ug/Courses/{code}.pdf"
)

_USER_AGENT = "Mozilla/5.0 (compatible; AutoCUSIS/0.1; academic planning tool)"

_CODE_RE = re.compile(r"^([A-Z]{2,5})(\d{3,4}[A-Z]?)$")


class PdfFetchError(RuntimeError):
    """Raised when a course PDF cannot be downloaded."""


def subject_prefix(code: str) -> str:
    m = _CODE_RE.match(code.upper())
    return m.group(1) if m else ""


def pdf_url_for(code: str) -> Optional[str]:
    """Return the public PDF URL for a course code, or None if no known host."""
    code = code.upper()
    if subject_prefix(code) in CSE_SUBJECTS:
        return CSE_PDF_TEMPLATE.format(code=code)
    return None


def fetch_pdf(code: str, force: bool = False) -> Path:
    """Download the catalog PDF for ``code`` into the cache and return its path."""
    code = code.upper()
    url = pdf_url_for(code)
    if not url:
        raise PdfFetchError(
            f"No known public PDF host for subject '{subject_prefix(code)}' "
            f"({code}). Use the catalog scraper for non-CSE subjects."
        )
    dest = pdf_cache_dir() / f"{code}.pdf"
    if dest.exists() and not force:
        return dest
    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    if resp.status_code != 200 or "application/pdf" not in resp.headers.get(
        "Content-Type", ""
    ):
        raise PdfFetchError(
            f"Failed to fetch {code}: HTTP {resp.status_code} "
            f"({resp.headers.get('Content-Type')}) at {url}"
        )
    dest.write_bytes(resp.content)
    return dest


def extract_text(pdf_path: Path) -> str:
    """Extract layout-preserving text from a PDF.

    Prefers the ``pdftotext -layout`` binary (best column fidelity); falls back
    to pdfplumber if the binary is unavailable.
    """
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        out = subprocess.run(
            [pdftotext, "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    # Fallback: pdfplumber
    import pdfplumber  # local import to keep startup fast

    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text(layout=True) or "")
    return "\n".join(parts)
