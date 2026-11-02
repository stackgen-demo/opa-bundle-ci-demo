#!/usr/bin/env bash
set +x
set -euo pipefail

die() {
  printf 'Governance scan failed: %s\n' "$*" >&2
  exit 3
}

for dependency in docker jq realpath timeout; do
  command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
: "${STACKGEN_API_URL:?Missing STACKGEN_API_URL}"
: "${STACKGEN_WORKSPACE_ID:?Missing STACKGEN_WORKSPACE_ID}"
: "${STACKGEN_TOKEN:?Missing STACKGEN_TOKEN}"

workspace_root="$(realpath -- "${GITHUB_WORKSPACE:-$PWD}")"
source_path="$(realpath -- "$workspace_root/${TERRAFORM_PATH:-terraform}")"
report_path="$(realpath -m -- "$workspace_root/${OUTPUT_FILE:-reports/scan-results.json}")"
[[ "$source_path" == "$workspace_root" || "$source_path" == "$workspace_root/"* ]] || die "Terraform path escapes the checkout"
[[ -d "$source_path" ]] || die "Terraform directory does not exist"
[[ "$report_path" == "$workspace_root/"* ]] || die "Report path escapes the checkout"
[[ ! -e "$report_path" ]] || die "Report already exists; choose a fresh output-file"

api_url="${STACKGEN_API_URL%/}"
[[ "$api_url" == https://* && "$api_url" != *\?* && "$api_url" != *\#* ]] || die "API URL must use HTTPS without query parameters"
authority="${api_url#https://}"
authority="${authority%%/*}"
[[ -n "$authority" && "$authority" != *@* ]] || die "Invalid API URL or embedded credentials"
[[ "$STACKGEN_WORKSPACE_ID" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || die "Workspace ID must be a UUID"

# v0.1.0, pinned to its multi-architecture manifest for reproducible scans.
scanner_image="ghcr.io/appcd-dev/stackgen-policy-360:v0.1.0@sha256:d6cb29b6895e4238375387a827e2340ebb871ccf2e0a6c89a8a739f49f9f869c"
env -u STACKGEN_TOKEN timeout 120s docker pull "$scanner_image" || die "Could not pull the public scanner image"
env -u STACKGEN_TOKEN timeout 30s docker run --rm "$scanner_image" capabilities | jq -e '.supported_bundle_contracts | index("stackgen.resource-config/v1") != null' >/dev/null

umask 077
temp_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/stackgen-governance.XXXXXX")"
container_name="$(basename -- "$temp_dir")"
cleanup() {
  docker rm --force "$container_name" >/dev/null 2>&1 || true
  rm -f -- "$temp_dir/token"
  rmdir -- "$temp_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' "$STACKGEN_TOKEN" > "$temp_dir/token"
unset STACKGEN_TOKEN

mkdir -p -- "$(dirname -- "$report_path")"
bundle_url="$api_url/iac-gen/v1/policy-bundle?orgId=$STACKGEN_WORKSPACE_ID&target=resource-config&format=opa-bundle"
printf 'Scanning %s against workspace %s\n' "${source_path#"$workspace_root/"}" "$STACKGEN_WORKSPACE_ID"
scan_exit=0
timeout --kill-after=10s 180s docker run --rm --name "$container_name" \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$source_path,target=/input,readonly" \
  --mount "type=bind,source=$temp_dir/token,target=/run/secrets/stackgen-token,readonly" \
  --mount "type=bind,source=$(dirname -- "$report_path"),target=/reports" \
  --workdir /input \
  "$scanner_image" scan \
  --input /input --input-type terraform-hcl \
  --bundle-url "$bundle_url" --bundle-token-file /run/secrets/stackgen-token \
  --fail-on high --unknown-values fail --output "/reports/$(basename -- "$report_path")" || scan_exit=$?

[[ -s "$report_path" ]] || die "Scanner produced no report (exit $scan_exit); inspect download/evaluation errors above"
jq . "$report_path"
jq -e '.summary.policies_evaluated > 0 and .summary.resources_scanned > 0' "$report_path" >/dev/null || die "No policies or resources were evaluated"

if [[ "${GITHUB_ACTIONS:-false}" == true ]]; then
  jq -r '
    def escape: gsub("%"; "%25") | gsub("\r"; "%0D") | gsub("\n"; "%0A");
    .violations[] | "::error title=Governance policy failed::" +
      ((.rule_id + ": " + .message + " (" + .address + ", " + .attribute + ")") | escape)
  ' "$report_path"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'report-path=%s\n' "$report_path"
    printf 'bundle-revision=%s\n' "$(jq -r '.bundle.revision' "$report_path")"
    printf 'violations=%s\n' "$(jq -r '.summary.violations' "$report_path")"
  } >> "$GITHUB_OUTPUT"
fi
exit "$scan_exit"
