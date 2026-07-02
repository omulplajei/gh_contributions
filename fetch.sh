#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "fetch.sh: .env not found at $(pwd)/.env; create it with:" >&2
  echo "  export GITHUB_TOKEN=<personal access token with repo:read>" >&2
  exit 2
fi

# shellcheck disable=SC1091
source .env

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "fetch.sh: GITHUB_TOKEN not set after sourcing .env" >&2
  exit 2
fi

exec python3 -m gh_contributions.run "$@"
