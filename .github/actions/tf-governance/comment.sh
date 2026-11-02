#!/usr/bin/env bash
set +x
set -euo pipefail

action_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$action_dir/../governance-common/comment.py" terraform
