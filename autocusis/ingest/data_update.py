"""Fetch community course data from GitHub or local paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests

from ..paths import community_data_dir
from .community_sync import SourceKind, sync_community

GITHUB_REPO = "EagleZhen/another-cuhk-course-planner"
GITHUB_DATA_PATH = "web/public/data"
GITHUB_API = "https://api.github.com"


def _github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "autocusis-data-update",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_github_data(
    dest: Path | None = None,
    *,
    subjects: set[str] | None = None,
) -> Path:
    """Download published JSON files from EagleZhen repo into dest."""
    dest = dest or community_data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_PATH}"
    resp = requests.get(url, headers=_github_headers(), timeout=60)
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected GitHub API response: {items!r}")

    for item in items:
        if item.get("type") != "file" or not item.get("name", "").endswith(".json"):
            continue
        name = item["name"]
        subject = name.replace(".json", "").upper()
        if subjects and subject not in subjects:
            continue
        download_url = item.get("download_url")
        if not download_url:
            continue
        out = dest / name
        file_resp = requests.get(download_url, headers=_github_headers(), timeout=120)
        file_resp.raise_for_status()
        out.write_bytes(file_resp.content)
    return dest


def run_live_scrape(
    scraper_home: Path,
    *,
    subjects: str | None = None,
) -> Path:
    """Run EagleZhen scraper subprocess; return path to published data."""
    scrape_script = scraper_home / "scripts" / "scrape_all_subjects.py"
    publish_script = scraper_home / "scripts" / "publish_course_data.py"
    if not scrape_script.exists():
        raise FileNotFoundError(f"Scraper not found at {scrape_script}")

    cmd = ["poetry", "run", "python", str(scrape_script)]
    if subjects:
        cmd.append(subjects)
    subprocess.run(cmd, cwd=scraper_home, check=True)

    subprocess.run(
        ["poetry", "run", "python", str(publish_script)],
        cwd=scraper_home,
        check=True,
    )
    return scraper_home / "web" / "public" / "data"


def update_data(
    *,
    source: str = "github",
    term_filter: str,
    subjects: str | None = None,
    live_scrape: bool = False,
    dry_run: bool = False,
    adapter_source: SourceKind = "eaglezhen",
) -> tuple[Path, object]:
    """Fetch data then sync into AutoCUSIS stores."""
    subject_set = None
    if subjects:
        subject_set = {s.strip().upper() for s in subjects.split(",") if s.strip()}

    if live_scrape:
        home = Path(
            os.environ.get(
                "AUTOCUSIS_SCRAPER_HOME",
                os.path.expanduser("~/Projects/CLONED/another-cuhk-course-planner"),
            )
        )
        data_path = run_live_scrape(home, subjects=subjects)
    elif source == "github":
        data_path = fetch_github_data(subjects=subject_set)
    else:
        env_path = os.environ.get("AUTOCUSIS_COMMUNITY_DATA")
        data_path = Path(env_path) if env_path else community_data_dir()

    stats = sync_community(
        adapter_source,
        data_path,
        term_filter,
        subjects=subjects,
        dry_run=dry_run,
    )
    return data_path, stats
