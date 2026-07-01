"""Load and validate config.yml for the contribution analyzer."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml


ALLOWED_METRICS = {"authoring", "collaboration", "team_share"}
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class ConfigError(ValueError):
    """Raised when config.yml fails validation."""


@dataclass(frozen=True)
class Config:
    usernames: list[str]
    repos: list[str]
    since: date
    until: date
    metrics: list[str]


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigError("config.yml top-level must be a mapping")

    usernames = _require_list_of_str(raw, "usernames")
    if not usernames:
        raise ConfigError("usernames must be a non-empty list")

    repos = _require_list_of_str(raw, "repos", allow_empty=True)
    for r in repos:
        if not _REPO_RE.match(r):
            raise ConfigError(f"repos entry must be 'owner/repo': got {r!r}")
    if not repos:
        print("warning: repos is empty; no repositories will be analyzed", file=sys.stderr)

    since = _require_date(raw, "since")
    until = _require_date(raw, "until")
    if until < since:
        raise ConfigError(f"until ({until}) must be >= since ({since})")

    metrics = _require_list_of_str(raw, "metrics")
    if not metrics:
        raise ConfigError("metrics must be a non-empty list")
    for m in metrics:
        if m not in ALLOWED_METRICS:
            raise ConfigError(
                f"metrics entry {m!r} not in allowed set "
                f"{sorted(ALLOWED_METRICS)}"
            )

    return Config(
        usernames=usernames,
        repos=repos,
        since=since,
        until=until,
        metrics=metrics,
    )


def _require_list_of_str(raw: dict, key: str, *, allow_empty: bool = False) -> list[str]:
    if key not in raw:
        raise ConfigError(f"missing required key: {key}")
    val = raw[key]
    if val is None and allow_empty:
        return []
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ConfigError(f"{key} must be a list of strings")
    return val


def _require_date(raw: dict, key: str) -> date:
    if key not in raw:
        raise ConfigError(f"missing required key: {key}")
    val = raw[key]
    if isinstance(val, date):
        return val
    raise ConfigError(f"{key} must be a YYYY-MM-DD date, got {val!r}")
