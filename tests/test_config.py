import io
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from gh_contributions.config import Config, ConfigError, load_config


VALID_YAML = textwrap.dedent("""\
    usernames:
      - alice
      - bob
    repos:
      - acme/api
    since: 2026-01-01
    until: 2026-06-30
    metrics:
      - authoring
      - collaboration
      - team_share
""")


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "config.yml"
    p.write_text(body)
    return str(p)


def test_load_happy_path(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg == Config(
        usernames=["alice", "bob"],
        repos=["acme/api"],
        since=date(2026, 1, 1),
        until=date(2026, 6, 30),
        metrics=["authoring", "collaboration", "team_share"],
    )


def test_empty_usernames_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("usernames:\n  - alice\n  - bob\n", "usernames: []\n")
    with pytest.raises(ConfigError, match="usernames"):
        load_config(_write(tmp_path, body))


def test_empty_metrics_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace(
        "metrics:\n  - authoring\n  - collaboration\n  - team_share\n",
        "metrics: []\n",
    )
    with pytest.raises(ConfigError, match="metrics"):
        load_config(_write(tmp_path, body))


def test_unknown_metric_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("- authoring", "- bogus_metric")
    with pytest.raises(ConfigError, match="bogus_metric"):
        load_config(_write(tmp_path, body))


def test_until_before_since_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("since: 2026-01-01", "since: 2026-07-01")
    with pytest.raises(ConfigError, match="until"):
        load_config(_write(tmp_path, body))


def test_malformed_repo_errors(tmp_path: Path) -> None:
    body = VALID_YAML.replace("- acme/api", "- not-a-repo")
    with pytest.raises(ConfigError, match="not-a-repo"):
        load_config(_write(tmp_path, body))


def test_empty_repos_warns_not_errors(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    body = VALID_YAML.replace("repos:\n  - acme/api\n", "repos: []\n")
    cfg = load_config(_write(tmp_path, body))
    assert cfg.repos == []
    captured = capsys.readouterr()
    assert "repos" in captured.err.lower()
