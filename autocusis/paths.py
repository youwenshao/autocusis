"""Centralized filesystem paths for AutoCUSIS data and config.

All paths are resolved relative to the project root (the directory that
contains the ``data`` folder) so the CLI works regardless of the current
working directory. Override the root with the ``AUTOCUSIS_HOME`` env var.
"""

from __future__ import annotations

import os
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent


def home() -> Path:
    """Return the AutoCUSIS data root, honoring ``AUTOCUSIS_HOME``."""
    env = os.environ.get("AUTOCUSIS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _PROJECT_ROOT


def data_dir() -> Path:
    d = home() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def requirements_dir() -> Path:
    d = data_dir() / "requirements"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_cache_dir() -> Path:
    d = data_dir() / "pdf_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def html_cache_dir() -> Path:
    """Saved Course Catalog detail pages for non-PDF subjects (GESH, MATH, ...)."""
    d = data_dir() / "catalog_html"
    d.mkdir(parents=True, exist_ok=True)
    return d


def catalog_db_path() -> Path:
    return data_dir() / "catalog.sqlite"


def profile_path() -> Path:
    return data_dir() / "profile.yaml"


def availability_path() -> Path:
    return data_dir() / "availability.yaml"


def default_requirements_path() -> Path:
    return requirements_dir() / "aist.yaml"


def sections_db_path() -> Path:
    return data_dir() / "sections.sqlite"


def community_data_dir() -> Path:
    d = data_dir() / "community"
    d.mkdir(parents=True, exist_ok=True)
    return d
