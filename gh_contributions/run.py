"""Entry point: config -> monthly fetch -> compute -> metrics.json."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .fetch import _is_bucket_complete, _month_bounds, _months_between, fetch_repo
from .github_client import AuthError, GitHubClient
from .metrics import compute


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config_path = argv[0] if argv else "config.yml"

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var is required", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    today = now.date()
    run_id = now.strftime("%Y-%m-%dT%H%M%SZ")

    run_out = Path("out") / run_id
    run_out.mkdir(parents=True, exist_ok=True)
    raw_root = Path("out") / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    print(f"run dir: {run_out}", file=sys.stderr)
    print(f"raw cache: {raw_root}", file=sys.stderr)

    months = _months_between(cfg.since, today)
    if not months or not cfg.repos:
        if not cfg.repos:
            print("no repos configured; writing empty metrics.json", file=sys.stderr)
        else:
            print(f"since ({cfg.since}) is after today; writing empty metrics.json", file=sys.stderr)
        _write_metrics(run_out, compute(raw_root, cfg, today=today))
        return 0

    client = GitHubClient(token)
    for month in months:
        month_start, month_end = _month_bounds(month, today)
        month_dir = raw_root / month
        month_dir.mkdir(parents=True, exist_ok=True)
        for repo in cfg.repos:
            owner, name = repo.split("/", 1)
            bucket = month_dir / f"{owner}__{name}"
            if _is_bucket_complete(bucket):
                print(f"skip {month} {repo} (cached)", file=sys.stderr)
                continue
            print(f"fetching {month} {repo}", file=sys.stderr)
            try:
                fetch_repo(client, repo, month_start, month_end, month_dir)
            except AuthError as exc:
                print(f"auth failed: {exc}", file=sys.stderr)
                return 2

    result = compute(raw_root, cfg, today=today)
    _write_metrics(run_out, result)

    ok_count = sum(
        1 for r in result["repos"].values() if r.get("per_user") is not None
    )
    if ok_count == 0:
        print("no repos produced metrics", file=sys.stderr)
        return 1
    return 0


def _write_metrics(out_dir: Path, result: dict) -> None:
    path = out_dir / "metrics.json"
    path.write_text(json.dumps(result, indent=2))
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
