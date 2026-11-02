#!/usr/bin/env bash
set +x
set -euo pipefail

die() {
  printf 'Governance scan failed: %s\n' "$*" >&2
  exit 3
}

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || die "The demo binary requires Linux amd64"
for dependency in jq sha256sum realpath timeout; do
  command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
: "${STACKGEN_API_URL:?Missing STACKGEN_API_URL}"
: "${STACKGEN_WORKSPACE_ID:?Missing STACKGEN_WORKSPACE_ID}"
: "${STACKGEN_TOKEN:?Missing STACKGEN_TOKEN}"

action_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
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

(cd -- "$action_repo_root/bin" && sha256sum --check SHA256SUMS)
binary="$action_repo_root/bin/stackgen360guard-linux-amd64"
[[ -x "$binary" ]] || die "Scanner binary is not executable"
env -u STACKGEN_TOKEN "$binary" capabilities | jq -e '.supported_bundle_contracts | index("stackgen.resource-config/v1") != null' >/dev/null

umask 077
temp_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/stackgen-governance.XXXXXX")"
trap 'rm -f -- "$temp_dir/token"; rmdir -- "$temp_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' "$STACKGEN_TOKEN" > "$temp_dir/token"
unset STACKGEN_TOKEN

mkdir -p -- "$(dirname -- "$report_path")"
bundle_url="$api_url/iac-gen/v1/workspaces/$STACKGEN_WORKSPACE_ID/policy-bundle?target=resource-config&orgId=$STACKGEN_WORKSPACE_ID"
printf 'Scanning %s against workspace %s\n' "${source_path#"$workspace_root/"}" "$STACKGEN_WORKSPACE_ID"
scan_exit=0
timeout 180s "$binary" scan \
  --input "$source_path" --input-type terraform-hcl \
  --bundle-url "$bundle_url" --bundle-token-file "$temp_dir/token" \
  --fail-on high --unknown-values fail --output "$report_path" || scan_exit=$?

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
