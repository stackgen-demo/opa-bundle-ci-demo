#!/usr/bin/env bash
# Local wrapper for the GitHub governance scans. Holds no environment values.
# Usage: governance-scan.sh terraform|helm [path-relative-to-repo]
#
# Settings resolve in this order; the first source that sets a value wins:
#   1. Variables already exported in the shell
#   2. The env file named by STACKGEN_ENV_FILE, else <repo>/.env.stackgen (gitignored by .env.*)
#   3. GitHub repository variables (gh variable get), the same values CI uses
# Required: STACKGEN_TOKEN (shell or env file only), STACKGEN_API_URL, STACKGEN_WORKSPACE_ID.
set -euo pipefail

fail() { printf 'Governance scan setup failed: %s\n' "$*" >&2; exit 3; }

kind="${1:-}"
case "$kind" in
  terraform) script=".github/actions/tf-governance/scan.sh"; path_var=TERRAFORM_PATH; default_path=terraform; reports=reports ;;
  helm) script=".github/actions/helm-governance/scan.sh"; path_var=HELM_PATH; default_path=helm; reports=helm/reports ;;
  *) fail "first argument must be terraform or helm" ;;
esac

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "run inside the repository"
cd "$repo_root"
[[ -f "$script" ]] || fail "missing $script"

scan_path="${2:-${!path_var:-$default_path}}"
[[ "$scan_path" != /* && "$scan_path" != *..* && -d "$scan_path" ]] || fail "scan path must be a directory inside the repository: $scan_path"

env_file="${STACKGEN_ENV_FILE:-$repo_root/.env.stackgen}"
if [[ -f "$env_file" ]]; then
  # Keep values exported in the shell; the env file only fills gaps.
  saved_url="${STACKGEN_API_URL:-}" saved_ws="${STACKGEN_WORKSPACE_ID:-}" saved_token="${STACKGEN_TOKEN:-}"
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
  [[ -n "$saved_url" ]] && STACKGEN_API_URL="$saved_url"
  [[ -n "$saved_ws" ]] && STACKGEN_WORKSPACE_ID="$saved_ws"
  [[ -n "$saved_token" ]] && STACKGEN_TOKEN="$saved_token"
fi

from_github() {
  command -v gh >/dev/null 2>&1 || return 0
  gh variable get "$1" 2>/dev/null || true
}
[[ -n "${STACKGEN_API_URL:-}" ]] || STACKGEN_API_URL="$(from_github STACKGEN_API_URL)"
[[ -n "${STACKGEN_WORKSPACE_ID:-}" ]] || STACKGEN_WORKSPACE_ID="$(from_github STACKGEN_WORKSPACE_ID)"

[[ -n "${STACKGEN_API_URL:-}" ]] || fail "STACKGEN_API_URL is not set (shell, $env_file, or GitHub repo variable)"
[[ -n "${STACKGEN_WORKSPACE_ID:-}" ]] || fail "STACKGEN_WORKSPACE_ID is not set (shell, $env_file, or GitHub repo variable)"
[[ -n "${STACKGEN_TOKEN:-}" ]] || fail "STACKGEN_TOKEN is not set; add it to $env_file (mode 600) in your own terminal"

# Docker under Colima can only bind-mount paths below the home directory.
temp_root="${STACKGEN_TEMP_DIR:-$HOME/.cache/stackgen-governance}"
mkdir -p "$temp_root"
export TMPDIR="$temp_root" RUNNER_TEMP="$temp_root"

# tf-governance/scan.sh needs GNU realpath -m; macOS realpath lacks it.
if [[ "$kind" == terraform ]] && ! realpath -m / >/dev/null 2>&1; then
  gnubin=""
  command -v brew >/dev/null 2>&1 && gnubin="$(brew --prefix coreutils 2>/dev/null)/libexec/gnubin"
  [[ -n "$gnubin" && -x "$gnubin/realpath" ]] || fail "GNU realpath not found; run: brew install coreutils"
  export PATH="$gnubin:$PATH"
fi

# Report every missing tool at once instead of scan.sh stopping at the first.
required=(git docker jq curl python3 timeout)
[[ "$kind" == helm ]] && required+=(helm)
missing=()
for tool in "${required[@]}"; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
[[ ${#missing[@]} -eq 0 ]] || fail "missing tools: ${missing[*]} (see the skill Requirements section)"

docker info >/dev/null 2>&1 || fail "Docker is installed but not reachable; start your runtime (for Colima: colima start)"

# Warn when local Helm differs in major version from the version CI pins in action.yml.
if [[ "$kind" == helm ]]; then
  ci_helm="$(sed -nE 's/^[[:space:]]*version:[[:space:]]*v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' .github/actions/helm-governance/action.yml | head -1)"
  local_helm="$(helm version --template '{{.Version}}' 2>/dev/null | sed -E 's/^v//')"
  if [[ -n "$ci_helm" && -n "$local_helm" && "${ci_helm%%.*}" != "${local_helm%%.*}" ]]; then
    printf 'WARNING: local Helm %s differs from CI Helm %s (major version); rendered manifests may differ from CI.\n' "$local_helm" "$ci_helm" >&2
  fi
fi

rm -rf -- "$reports"
export GITHUB_WORKSPACE="$repo_root" STACKGEN_API_URL STACKGEN_WORKSPACE_ID STACKGEN_TOKEN
export "$path_var=$scan_path"
printf 'Scanning %s (%s) against %s workspace %s\n' "$scan_path" "$kind" "$STACKGEN_API_URL" "$STACKGEN_WORKSPACE_ID"
exec bash "$script"
