# AutoCUSIS

A command-line academic planner for CUHK (Hong Kong) study schemes. AutoCUSIS
ingests the public course catalog, tracks your graduation-requirement progress,
and uses a constraint solver to generate **optimal multi-semester study plans**
that respect prerequisites, term availability, credit caps, and any courses you
pin to a particular semester.

It was built for the AIST (Artificial Intelligence: Systems and Technologies)
program but the requirement DSL is general enough for any CUHK scheme.

## What it does

- **Ingests course details** from the public CSE catalog PDFs (course code,
  units, prerequisites as a boolean expression, exclusions, description in
  EN/ZH, components, learning outcomes) into an indexed SQLite catalog.
- **Tracks term availability** (which courses run in Term 1 / Term 2) via a
  hybrid of scraped Teaching-Timetable data and manual overrides.
- **Models graduation requirements** as a small YAML DSL (core courses,
  credit-based electives, count-based packages, free electives, total credits).
- **Computes progress**: what you've satisfied, what's outstanding, credit gaps.
- **Generates optimal plans** with an OR-Tools CP-SAT solver. The objective is
  lexicographic: fastest graduation first, then balanced credit load, then a
  gentle preference for taking things earlier.

## Why the hybrid data model

The CSE catalog PDFs (`https://www.cse.cuhk.edu.hk/.../Courses/AIST1110.pdf`)
are public and parse cleanly, but they **do not contain term/semester
availability** - only catalog metadata. The semester in which a course is
offered lives in CUHK's **Teaching Timetable**, which is gated by a verification
code for public users but open to a logged-in CUSIS session. AutoCUSIS
therefore treats availability as separate, user-controllable data.

## Install

Requires Python 3.11-3.13 (OR-Tools has no 3.14 wheels yet) and the
`pdftotext` binary (from `poppler`; `brew install poppler`).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `autocusis` command.

## Quick start

```bash
# 1. Ingest the courses you care about (CSE-hosted subjects work directly)
autocusis ingest course AIST1110 AIST3010 CSCI2100

# 2. Tell AutoCUSIS which terms a course is offered (manual override)
autocusis availability set AIST3010 --terms 1 --note "Term 1 only"

# 3. Set up your profile
autocusis profile init --cohort 2023
autocusis profile add-completed AIST1000 AIST1110 MATH1510
autocusis profile set --max-term-credits 18 --max-year-credits 39 --horizon-years 4

# 4. (Optional) pin a priority course to a specific planned slot
autocusis profile pin AIST3010 --year 2 --term 1

# 5. See where you stand
autocusis status

# 6. Generate an optimal plan (and alternatives / exports)
autocusis plan --count 3 --export-md plan.md --export-csv plan.csv

# Inspect a single course
autocusis course AIST1110
```

## Defining your graduation requirements

Edit `data/requirements/aist.yaml`. A starter template is provided. Each group
uses one of three rule kinds:

| kind | meaning |
| --- | --- |
| `all_of` | every listed course must be completed (core/required) |
| `credits_from` | at least `min_credits` credits from the `courses` pool |
| `count_from` | at least `min_count` courses from the `courses` pool |

A `credits_from` group with an empty `courses` list represents flexible free
electives; the planner fills them with generic placeholder slots so the timing
math is still correct.

## Getting term availability (the authenticated scrape)

Availability is the one input not present in the PDFs. Two ways to populate it:

1. **Manual** (fast, precise): `autocusis availability set CODE --terms 1,2`.

2. **Scrape the Teaching Timetable** (bulk):
   - Open the Teaching Timetable
     (`https://rgsntl.rgs.cuhk.edu.hk/rws_prd_applx2/Public/tt_dsp_timetable.aspx`)
     in a browser **logged into CUSIS/MyCUHK** (this skips the per-search
     verification code). The Cursor `cursor-ide-browser` MCP can drive this tab
     after you authenticate once.
   - Run a search for your department and a term, then **save the results page**
     as HTML.
   - Import it:
     ```bash
     autocusis availability sync --from-html term1.html --term 1 --year 2025-26 --subjects AIST,CSCI,MATH
     autocusis availability sync --from-html term2.html --term 2 --year 2025-26
     # or both at once:
     autocusis availability sync-multi --t1 term1.html --t2 term2.html --year 2025-26
     ```
   Scraped data never overwrites your manual overrides.

   An optional Playwright path (`fetch_timetable_live` in
   `autocusis/ingest/timetable_scraper.py`) is provided for headless re-runs;
   install with `pip install playwright && playwright install chromium`.

### Non-CSE courses

Subjects outside CSE (MATH, GE, languages, ...) are not published as PDFs on
the CSE host. Save their detail page from the public Course Catalog browser
(logged into CUSIS) and ingest it:

```bash
autocusis ingest html MATH1010 math1010.html
```

The same "Print Course Catalog Details" layout is reused, so all fields are
extracted as with PDFs.

## Community data refresh

Section-level scheduling uses published JSON from the CUHK developer community
(primarily [EagleZhen/another-cuhk-course-planner](https://github.com/EagleZhen/another-cuhk-course-planner)).

```bash
# Pull published JSON from GitHub and sync into sections.sqlite + availability.yaml
autocusis ingest update --term "2025-26 Term 2"

# Or sync from a local checkout
autocusis ingest sync-community --source eaglezhen \
  --data-path /path/to/web/public/data --term "2025-26 Term 2"

# Optional: run the external scraper first (requires AUTOCUSIS_SCRAPER_HOME checkout)
autocusis ingest update --term "2025-26 Term 2" --live-scrape
```

Check coverage: `autocusis data status`

## Agent-first workflow

AutoCUSIS is designed for use through an AI agent. Prefer JSON output over Rich tables:

```bash
# Full graduation plan with section assignments where data exists
autocusis plan --with-sections --export-json plan.json

# JSON Schema for agent tool definitions
autocusis schema plan

# Section schedules for one term
autocusis sections generate --term-label "2026-27 Term 1" \
  --courses AIST3010,CSCI2100 --export-json schedules.json

# Finalize deliverable (Markdown course sheet + HTML + SVG timetables + ICS)
autocusis plan --with-sections --export-json plan.json --export-report-dir ./report/

# Re-render report from saved JSON without re-solving
autocusis report --from plan.json --out ./report/
```

Report bundle output:

```
report/
  course-sheet.md       # term-organized plan with section tables
  index.html            # self-contained browser report
  timetables/*.svg      # calendar-style weekly grids per term
  calendars/*.ics       # import into Google/Apple Calendar (approximate dates)
```

Add `schedule_preferences` to `data/profile.yaml` (mode, pinned sections) for
agent-editable lifestyle constraints.

## Command reference

| Command | Purpose |
| --- | --- |
| `autocusis ingest course CODE...` | fetch & parse CSE catalog PDFs |
| `autocusis ingest html CODE FILE` | ingest a saved non-CSE catalog page |
| `autocusis ingest update --term T` | fetch community JSON and sync sections |
| `autocusis ingest sync-community` | sync local community JSON into stores |
| `autocusis ingest show CODE` | dump a stored course as JSON |
| `autocusis availability set CODE --terms 1,2` | manual availability |
| `autocusis availability sync --from-html FILE --term N` | import a timetable page |
| `autocusis availability list` | list availability records |
| `autocusis data status` | catalog + section data coverage |
| `autocusis status` | requirement progress and gaps |
| `autocusis profile init / show / add-completed / set / pin` | manage your profile |
| `autocusis plan [--with-sections] [--export-json] [--export-report-dir DIR]` | generate study plan(s) |
| `autocusis report --from plan.json --out DIR` | export Markdown/HTML/SVG/ICS from saved plan |
| `autocusis sections generate` | section-level timetable for one term |
| `autocusis schema plan` | dump SectionPlan JSON Schema |
| `autocusis course CODE` | show a course's details |
| `autocusis db-info` | catalog DB location & counts |

## How the solver works

For every outstanding course and every planning slot (year + term), a boolean
decision variable says whether the course is taken then. Constraints encode:

- **Prerequisites**: a course's prerequisite boolean expression must be
  satisfied by courses scheduled in *strictly earlier* slots (or already
  completed). Prerequisite courses are auto-included in the plan.
- **Exclusions**: mutually exclusive courses can't both be taken; a completed
  exclusion blocks its partner.
- **Availability**: a course is only schedulable in the terms it is offered.
- **Credit caps**: per-term (default 18) and per-year (default 39).
- **Pins**: a course is forced into an exact slot.
- **Requirements**: mandatory courses are taken; elective pools are filled to
  their credit/count floor; free-elective gaps are filled with placeholders.

The model is optimized in stages (lexicographic): minimize finishing term,
then minimize peak per-term load, then minimize total earliness index.

## Data & storage

```
data/
  catalog.sqlite          # indexed course catalog (generated)
  sections.sqlite         # term-scoped section bundles (from community sync)
  profile.yaml            # your completed courses + planning settings
  availability.yaml       # per-course term availability (editable)
  community/              # cached community JSON snapshots
  requirements/aist.yaml  # your graduation requirement definition
```

Set `AUTOCUSIS_HOME` to relocate the data root.

## Project layout

```
autocusis/
  cli.py                  # Typer entrypoint
  models.py db.py profile.py paths.py services.py
  reports/    markdown.py svg.py html.py ics.py bundle.py
  ingest/   pdf_fetcher.py pdf_parser.py prereq.py
            availability_store.py timetable_scraper.py catalog_scraper.py
            commands.py availability_commands.py
  requirements/ schema.py engine.py commands.py
  scheduler/ solver.py service.py plan.py commands.py
  sections/  db.py bundle_builder.py solver.py orchestrator.py
  calendar.py data_commands.py
```
