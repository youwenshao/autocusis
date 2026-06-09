# Community Data Adapter — Design Spike

**Status:** Design only (not implemented)  
**Related:** `docs/competitor-analysis.md` §8 P0  
**Goal:** Replace manual `availability.yaml` curation with automated ingestion from community course datasets while preserving AutoCUSIS's separation of catalog metadata (PDFs/SQLite) from term/section data.

---

## 1. Problem

AutoCUSIS currently populates term availability via:

1. Manual `autocusis availability set`
2. Saved Teaching Timetable HTML (`availability sync --from-html`)
3. Incomplete Playwright stub

Meanwhile, community repos maintain richer, term-scoped data:

| Source | Path (cloned) | Strengths | Weaknesses |
|--------|---------------|-----------|------------|
| **EagleZhen v2** | `/Users/youwen/Projects/CLONED/another-cuhk-course-planner/web/public/data/*.json` | Seat counts, meeting times, enrollment text, active scraper | Prereqs as raw text only |
| **CUtopia legacy** | `/Users/youwen/Projects/CLONED/cuhk-course-data/courses/*.json` | Broad subject coverage, submodule workflow | No seats; legacy nested dict schema |
| **Queuesis snapshot** | `/Users/youwen/Projects/CLONED/Queuesis/data/courses-2025-26-T2.json` | Normalized flat schema, seat data | Per-term file; T1/T2 from Excel not in repo |

Queuesis already maps EagleZhen → its schema in `scripts/sync-github-courses.ts`. AutoCUSIS should do a thinner mapping: **course code → term availability flags** plus optional section storage.

---

## 2. Proposed CLI

```
autocusis ingest sync-community \
  --source eaglezhen|cutopia|queuesis \
  --data-path /path/to/data \
  --term "2025-26 Term 2" \
  [--subjects AIST,CSCI,MATH] \
  [--dry-run] \
  [--sections-db]
```

### Flags

| Flag | Purpose |
|------|---------|
| `--source` | Which JSON schema to parse |
| `--data-path` | Directory (eaglezhen/cutopia) or file (queuesis JSON) |
| `--term` | Academic term filter (see mapping table below) |
| `--subjects` | Optional subject prefix filter |
| `--dry-run` | Print stats without writing |
| `--sections-db` | Also populate `data/sections.sqlite` (optional phase 2) |

Respects existing precedence in `availability_store.py`: **manual > community/timetable > default**.

---

## 3. Term Name Mapping

Community repos use inconsistent term strings. Normalizer:

| Input pattern | AutoCUSIS `Term` |
|---------------|------------------|
| `2025-26 Term 1`, `2025-26-T1`, term_code `2380` | `1` |
| `2025-26 Term 2`, `2025-26-T2`, term_code `2390` | `2` |
| `2025-26 Summer`, `Summer` | `3` |

Implementation: `autocusis/ingest/community_sync.py` with a `normalize_term(name: str) -> Term | None` function and a lookup table extensible per academic year.

---

## 4. Per-Source Adapters

### 4.1 EagleZhen (`--source eaglezhen`)

**Input:** `web/public/data/{SUBJECT}.json` (v2 wrapped format)

**Parse logic:**

```python
def iter_eaglezhen_courses(path: Path, term_filter: str):
  data = json.loads(path.read_text())
  for course in data.get("courses", []):
    subject = course.get("subject", "")
    code = f"{subject}{course['course_code']}".upper()
    for term in course.get("terms", []):
      if term_filter not in term.get("term_name", ""):
        continue
      yield code, term  # has schedule[] with sections
```

**Availability output:** For each `code`, union term number into `availability.yaml` entry with `source: "community"` (new precedence level between `timetable` and `manual`, or reuse `timetable`).

**Merge rule:** If course already has `source: manual`, skip. If existing `terms` list, union new term (dedupe).

### 4.2 CUtopia (`--source cutopia`)

**Input:** `courses/{SUBJECT}.json` (legacy array format)

**Parse logic:**

```python
def iter_cutopia_courses(path: Path, term_filter: str):
  for course in json.loads(path.read_text()):
    subject = path.stem  # e.g. CSCI
    code = f"{subject}{course['code']}".upper()
    for term_name in course.get("terms", {}):
      if term_filter not in term_name:
        continue
      yield code, term_name
```

**Note:** CUtopia files can be large (250KB+ per subject). Stream-parse or filter by subject directory listing.

### 4.3 Queuesis (`--source queuesis`)

**Input:** Single flat JSON array (`courses-2025-26-T2.json`)

**Parse logic:**

```python
def iter_queuesis_courses(path: Path):
  for course in json.loads(path.read_text()):
    yield course["courseCode"].upper(), course.get("term", "")
```

Term is implicit in filename or `course.term` field (`2025-26-T2`).

---

## 5. Availability YAML Output

Uses existing `AvailabilityStore.merge()` API. Proposed extension to `_SOURCE_PRECEDENCE`:

```python
_SOURCE_PRECEDENCE = {
    "default": 0,
    "timetable": 1,
    "community": 1,  # same rank as timetable; manual still wins
    "manual": 2,
}
```

**Sample merged record:**

```yaml
courses:
  CSCI2100:
    terms: [1, 2]
    source: community
    year: "2025-26"
    note: "synced from eaglezhen 2025-26 Term 2"
```

**Stats printed on sync:**

```
Synced 1,847 courses from eaglezhen (2025-26 Term 2)
  312 new availability records
  89 terms unioned into existing records
  41 skipped (manual override)
  12 codes not in catalog.sqlite (warn)
```

---

## 6. Optional `sections.sqlite` Schema (Phase 2)

Only created with `--sections-db`. Does **not** replace `catalog.sqlite`; holds term-scoped section schedules for future `autocusis sections` or Queuesis export.

```sql
CREATE TABLE sections (
  id INTEGER PRIMARY KEY,
  course_code TEXT NOT NULL,
  term TEXT NOT NULL,           -- "2025-26-T2"
  section_id TEXT NOT NULL,     -- "D" or "--LEC (6161)"
  section_type TEXT,            -- Lecture/Tutorial/Lab
  day TEXT,
  start_time TEXT,              -- "09:30" HH:MM
  end_time TEXT,
  location TEXT,
  instructor TEXT,
  quota INTEGER,
  enrolled INTEGER,
  seats_remaining INTEGER,
  source TEXT NOT NULL,
  UNIQUE(course_code, term, section_id, day, start_time)
);

CREATE INDEX idx_sections_course_term ON sections(course_code, term);
```

**EagleZhen → sections mapping:**

- `schedule[].section` → `section_id`
- Parse `meetings[].time` (`"Th 1:30PM - 2:15PM"`) → `day`, `start_time`, `end_time`
- `availability.capacity/enrolled/available_seats` → integers

**Queuesis → sections:** Direct field mapping from `sections[]` array.

**CUtopia → sections:** Map `days[]` (0=Sun..6=Sat) + `startTimes[]`/`endTimes[]`.

---

## 7. Plan Export Bridge (Phase 1b)

Separate from sync; implements P1 from competitor analysis.

```
autocusis plan --export-term-courses queuesis.json --plan-slot 1 --term 2025-26-T2
```

**Output:** Minimal JSON array of course codes from the selected plan semester, compatible with Queuesis course search (user pastes or imports):

```json
{
  "source": "autocusis",
  "term": "2025-26-T2",
  "courses": ["AIST3010", "CSCI2100", "MATH2040"],
  "plan_notes": ["Prereq warning: AIST3010 has raw prereq 'consent of instructor'"]
}
```

Full Queuesis `Course[]` export would require joining against `sections.sqlite` or community JSON—not needed for v1 bridge (user selects courses in Queuesis UI).

---

## 8. Module Layout (Proposed)

```
autocusis/ingest/
  community_sync.py       # CLI orchestration, term normalization
  adapters/
    eaglezhen.py        # v2 wrapped JSON
    cutopia.py          # legacy array JSON
    queuesis.py         # flat Course[] JSON
  sections_db.py        # optional sections.sqlite (phase 2)
```

Register in `autocusis/ingest/commands.py`:

```python
@ingest_app.command("sync-community")
def sync_community(...):
    ...
```

---

## 9. Testing Strategy

| Test | Input | Assert |
|------|-------|--------|
| `test_eaglezhen_term_filter` | Fixture `CSCI.json` snippet | Only T2 courses; code `CSCI1020` |
| `test_cutopia_term_keys` | Fixture legacy format | Term name normalization |
| `test_merge_respects_manual` | Existing manual record | Community sync does not overwrite |
| `test_term_union` | Course in T1 and T2 files | `terms: [1, 2]` |
| `test_queuesis_flat` | Small array fixture | All codes extracted |

Use trimmed fixtures under `tests/fixtures/community/` copied from cloned repos (do not commit full 114k-line JSON).

---

## 10. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Community data stale or wrong | Print `scraped_at` / `lastUpdated` in sync summary; never overwrite `manual` |
| Schema drift | Adapter per source; version check on `metadata.scraper_version` |
| Code normalization (`CSCI1020` vs `1020`) | Always prefix with subject from filename/metadata |
| AGPL contamination | **Read** EagleZhen JSON only; do not copy Queuesis TypeScript |
| CUTS API | **Do not use** — unofficial third-party |

---

## 11. Implementation Order

1. **MVP:** `eaglezhen` adapter → `availability.yaml` only, `--dry-run`
2. **cutopia** adapter (same output path)
3. **queuesis** adapter (single-file input)
4. `sync-community` CLI command + tests
5. `--sections-db` + `sections.sqlite` schema
6. `plan --export-term-courses` bridge

Estimated MVP effort: ~200–300 lines Python + 5–8 tests.
