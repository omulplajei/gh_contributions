"""Entry point: config -> fetch -> compute -> metrics.json."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .fetch import fetch_repo
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

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = Path("out") / run_id
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    print(f"run dir: {out_dir}", file=sys.stderr)

    if not cfg.repos:
        print("no repos configured; writing empty metrics.json", file=sys.stderr)
        _write_metrics(out_dir, compute(raw_dir, cfg))
        return 0

    client = GitHubClient(token)
    ok_count = 0
    for repo in cfg.repos:
        print(f"fetching {repo}", file=sys.stderr)
        try:
            fetch_repo(client, repo, cfg.since, cfg.until, raw_dir)
        except AuthError as exc:
            print(f"auth failed: {exc}", file=sys.stderr)
            return 2
        # Any other failure was recorded in the repo's _meta.json by fetch_repo.
        meta_path = raw_dir / f"{repo.replace('/', '__')}" / "_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if not meta.get("error"):
                    ok_count += 1
            except json.JSONDecodeError:
                pass

    result = compute(raw_dir, cfg)
    _write_metrics(out_dir, result)

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
