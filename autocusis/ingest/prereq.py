"""Parse CUHK prerequisite/exclusion strings into a boolean expression AST.

Grammar (precedence low -> high):
    orExpr    := andExpr ('or' andExpr)*
    andExpr   := slashExpr ('and' slashExpr)*
    slashExpr := atom ('/' atom)*          # '/' denotes equivalent courses
    atom      := '(' orExpr ')' | COURSE_CODE

Slash binds tightest because "ENGG1120/ESTR1005 and MATH1510" means
"(ENGG1120 or ESTR1005) and MATH1510". Any token that is not a course code,
keyword, slash, or parenthesis makes the whole string "unparseable" and we
fall back to a raw node (treated as satisfied, surfaced for manual review).
"""

from __future__ import annotations

import re

from ..models import CourseCode, PrereqExpr

COURSE_CODE_RE = re.compile(r"^[A-Z]{2,5}\d{3,4}[A-Z]?$")
_CODE_FINDALL_RE = re.compile(r"[A-Z]{2,5}\d{3,4}[A-Z]?")
_BARE_NUMBER_TOKEN_RE = re.compile(r"^\d{3,4}[A-Z]?$", re.IGNORECASE)
_PREREQ_LABEL_RE = re.compile(r"^(?:Pre-?requisites?|Preprequisite)\s*:\s*", re.IGNORECASE)
_SENIOR_WAIVED_RE = re.compile(
    r"[.\s]*For senior-year entrants, the prerequisite will be waived\.?$",
    re.IGNORECASE | re.DOTALL,
)
_ENTRANT_WAIVER_RE = re.compile(
    r"[.\s,]*For\s+(?:senior-year|2nd-year|\d+(?:st|nd|rd|th)-year)\s+entrants,?\s+"
    r"(?:the\s+)?pre-?requisites?\s+will\s+be\s+waived\.?",
    re.IGNORECASE,
)
_CONSENT_PHRASE_RE = re.compile(
    r"(?:,?\s*or\s+)?(?:with\s+the\s+)?consent\s+of\s+(?:the\s+)?(?:course\s+)?instructor",
    re.IGNORECASE,
)
_APPROVAL_RE = re.compile(
    r"\s+with the approval of (?:the )?course instructor\.?$",
    re.IGNORECASE,
)
_EQUIVALENT_SUFFIX_RE = re.compile(r"\s+or equivalent\.?$", re.IGNORECASE)
_TRAILING_LIST_MARKER_RE = re.compile(r"\.\s*\d+\s*$")
_EXEMPTION_PHRASE_RE = re.compile(
    r"\s+or\s+exemption\s+from\s+these\s+courses",
    re.IGNORECASE,
)


def extract_codes(text: str) -> list[str]:
    """Return all course codes mentioned in ``text`` (de-duplicated, in order)."""
    seen: list[str] = []
    for m in _CODE_FINDALL_RE.findall(text.upper()):
        if m not in seen:
            seen.append(m)
    return seen


def infer_default_subject(text: str, *, course_code: CourseCode | None = None) -> str | None:
    """Guess the subject prefix for bare course numbers like ``1130``."""
    counts: dict[str, int] = {}
    for code in extract_codes(text):
        prefix = "".join(ch for ch in code if ch.isalpha())
        counts[prefix] = counts.get(prefix, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    if course_code:
        i = 0
        while i < len(course_code) and course_code[i].isalpha():
            i += 1
        return course_code[:i] or None
    return None


def expand_bare_course_numbers(text: str, default_subject: str) -> str:
    """Expand standalone tokens like ``1130`` -> ``CSCI1130``."""
    spaced = text.replace("(", " ( ").replace(")", " ) ").replace("/", " / ")
    parts: list[str] = []
    for tok in spaced.split():
        if _BARE_NUMBER_TOKEN_RE.match(tok):
            parts.append(f"{default_subject.upper()}{tok.upper()}")
        else:
            parts.append(tok)
    return " ".join(parts)


def _wrap_and_groups(text: str) -> str:
    """Turn ELTU-style ``A or B AND C or D`` into ``(A or B) and (C or D)``."""
    parts = re.split(r"\s+AND\s+", text, flags=re.IGNORECASE)
    if len(parts) < 2:
        return text
    return " and ".join(f"({part.strip()})" for part in parts if part.strip())


def expand_compact_slash_chains(text: str, default_subject: str) -> str:
    """Expand ``ELTU2004/2005/2006`` and ``ELTU1001/1002`` before tokenization."""
    subject = default_subject.upper()

    def repl(match: re.Match[str]) -> str:
        parts = match.group(0).split("/")
        expanded: list[str] = [parts[0].upper()]
        for part in parts[1:]:
            token = part.strip().upper()
            if _BARE_NUMBER_TOKEN_RE.match(token):
                expanded.append(f"{subject}{token}")
            else:
                expanded.append(token)
        return " / ".join(expanded)

    return re.sub(
        rf"{subject}\d{{3,4}}[A-Z]?(?:/\d{{3,4}}[A-Z]?)+",
        repl,
        text,
        flags=re.IGNORECASE,
    )


def _cleanup_prereq_operators(text: str) -> str:
    """Remove dangling boolean operators left after stripping prose clauses."""
    text = re.sub(r"\(\s*or\s+", "(", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+or\s*\)", ")", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+or\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*or\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*\)", ")", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".,;")


def normalize_prereq_text(
    text: str,
    *,
    subject_prefix: str | None = None,
    course_code: CourseCode | None = None,
) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    text = _PREREQ_LABEL_RE.sub("", text)
    text = _EXEMPTION_PHRASE_RE.sub("", text)
    text = _EQUIVALENT_SUFFIX_RE.sub("", text)
    text = _TRAILING_LIST_MARKER_RE.sub("", text)
    text = _ENTRANT_WAIVER_RE.sub("", text)
    text = _SENIOR_WAIVED_RE.sub("", text)
    text = _CONSENT_PHRASE_RE.sub("", text)
    text = _APPROVAL_RE.sub("", text)
    subject = subject_prefix or infer_default_subject(text, course_code=course_code)
    if subject:
        text = expand_compact_slash_chains(text, subject)
        text = expand_bare_course_numbers(text, subject)
    text = _wrap_and_groups(text)
    text = _cleanup_prereq_operators(text)
    return text.strip().rstrip(".;")


def _tokenize(text: str) -> list[str]:
    spaced = text.replace("(", " ( ").replace(")", " ) ").replace("/", " / ")
    return spaced.split()


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0
        self.ok = True

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> str | None:
        tok = self._peek()
        if tok is not None:
            self.pos += 1
        return tok

    def parse(self) -> PrereqExpr:
        expr = self._or()
        if self.pos != len(self.tokens):
            self.ok = False
        return expr

    def _or(self) -> PrereqExpr:
        operands = [self._and()]
        while self._peek() and self._peek().lower() == "or":
            self._next()
            operands.append(self._and())
        return PrereqExpr.any_of(operands)

    def _and(self) -> PrereqExpr:
        operands = [self._slash()]
        while self._peek() and self._peek().lower() == "and":
            self._next()
            operands.append(self._slash())
        return PrereqExpr.all_of(operands)

    def _slash(self) -> PrereqExpr:
        operands = [self._atom()]
        while self._peek() == "/":
            self._next()
            operands.append(self._atom())
        return PrereqExpr.any_of(operands)

    def _atom(self) -> PrereqExpr:
        tok = self._next()
        if tok is None:
            self.ok = False
            return PrereqExpr.none()
        if tok == "(":
            inner = self._or()
            if self._peek() == ")":
                self._next()
            else:
                self.ok = False
            return inner
        up = tok.upper()
        if COURSE_CODE_RE.match(up):
            return PrereqExpr.course(up)
        self.ok = False
        return PrereqExpr.none()


def parse_prerequisite(
    text: str | None,
    *,
    subject_prefix: str | None = None,
    course_code: CourseCode | None = None,
) -> PrereqExpr:
    """Parse a prerequisite string into a ``PrereqExpr``.

    Returns ``none`` for empty input, a structured AST when fully parseable, or
    a ``raw`` node carrying the original text when it contains free-form
    language we cannot safely interpret.
    """
    if not text:
        return PrereqExpr.none()
    cleaned = normalize_prereq_text(
        text,
        subject_prefix=subject_prefix,
        course_code=course_code,
    )
    if not cleaned:
        return PrereqExpr.none()

    tokens = _tokenize(cleaned)
    if not tokens:
        return PrereqExpr.none()

    parser = _Parser(tokens)
    expr = parser.parse()
    if parser.ok and expr.kind != "none":
        return expr
    return PrereqExpr.raw(cleaned)
