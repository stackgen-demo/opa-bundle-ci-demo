#!/usr/bin/env bash
set +x
set -euo pipefail

die() { printf 'Helm governance failed: %s\n' "$*" >&2; exit 3; }
for dependency in helm curl jq python3 timeout; do
  command -v "$dependency" >/dev/null || die "Missing dependency: $dependency"
done
: "${STACKGEN_API_URL:?Missing STACKGEN_API_URL}"
: "${STACKGEN_WORKSPACE_ID:?Missing STACKGEN_WORKSPACE_ID}"
: "${STACKGEN_TOKEN:?Missing STACKGEN_TOKEN}"

action_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workspace_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${GITHUB_WORKSPACE:-$PWD}")"
source_path="$(python3 -c 'from pathlib import Path; import sys; print((Path(sys.argv[1])/sys.argv[2]).resolve())' "$workspace_root" "${HELM_PATH:-helm}")"
report_path="$(python3 -c 'from pathlib import Path; import sys; print((Path(sys.argv[1])/sys.argv[2]).resolve())' "$workspace_root" "${OUTPUT_FILE:-helm/reports/scan-results.json}")"
[[ "$source_path" == "$workspace_root/"* && -d "$source_path" ]] || die "Helm path must be a directory inside the checkout"
[[ "$report_path" == "$workspace_root/"* && "$report_path" == *.json ]] || die "Report must be a JSON file inside the checkout"
[[ ! -e "$report_path" ]] || die "Report already exists; choose a fresh output-file"
[[ "$STACKGEN_WORKSPACE_ID" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || die "Workspace ID must be a UUID"
api_url="${STACKGEN_API_URL%/}"
[[ "$api_url" == https://* && "$api_url" != *\?* && "$api_url" != *\#* && "$api_url" != *@* ]] || die "API URL must use HTTPS without query parameters or credentials"

umask 077
temp_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/stackgen-helm.XXXXXX")"
container_name="$(basename -- "$temp_dir")"
cleanup() {
  if [[ -z "${SCANNER_BINARY:-}" ]]; then docker rm --force "$container_name" >/dev/null 2>&1 || true; fi
  rm -f -- "$temp_dir/auth-header" "$temp_dir/bundle.tar.gz"
  rmdir -- "$temp_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ "$STACKGEN_TOKEN" != *$'\r'* && "$STACKGEN_TOKEN" != *$'\n'* ]] || die "Invalid token"
printf 'Authorization: Bearer %s' "$STACKGEN_TOKEN" > "$temp_dir/auth-header"
unset STACKGEN_TOKEN

bundle_url="$api_url/iac-gen/v1/policy-bundle?orgId=$STACKGEN_WORKSPACE_ID&target=kubernetes&format=opa-bundle"
curl --fail --silent --show-error --connect-timeout 10 --max-time 60 --retry 2 \
  --max-filesize 16777216 --header "@$temp_dir/auth-header" --output "$temp_dir/bundle.tar.gz" -- "$bundle_url"
rm -f -- "$temp_dir/auth-header"
python3 "$action_dir/validate_bundle.py" "$temp_dir/bundle.tar.gz" "${REQUIRED_RESOURCE_TYPES:-k8s/apps/v1/Deployment}"

report_dir="$(dirname -- "$report_path")"
rendered_path="$report_dir/rendered"
mkdir -p -- "$report_dir"
python3 "$action_dir/render.py" "$source_path" "$rendered_path"

scan_exit=0
if [[ -n "${SCANNER_BINARY:-}" ]]; then
  # Local verification can use the installed binary; CI uses the public image below.
  "$SCANNER_BINARY" capabilities | jq -e '.supported_bundle_contracts | index("stackgen.resource-config/v1") != null' >/dev/null
  timeout 180s "$SCANNER_BINARY" scan --input "$rendered_path" --input-type k8s \
    --bundle "$temp_dir/bundle.tar.gz" --fail-on high --unknown-values fail --output "$report_path" || scan_exit=$?
else
  command -v docker >/dev/null || die "Missing dependency: docker"
  scanner_image="ghcr.io/stackgenhq/stackgen360guard:latest"
  timeout 120s docker pull "$scanner_image"
  timeout 30s docker run --rm "$scanner_image" capabilities | jq -e '.supported_bundle_contracts | index("stackgen.resource-config/v1") != null' >/dev/null
  timeout --kill-after=10s 180s docker run --rm --name "$container_name" \
    --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$rendered_path,target=/input,readonly" \
    --mount "type=bind,source=$temp_dir/bundle.tar.gz,target=/run/governance/bundle.tar.gz,readonly" \
    --mount "type=bind,source=$report_dir,target=/reports" \
    "$scanner_image" scan --input /input --input-type k8s \
    --bundle /run/governance/bundle.tar.gz --fail-on high --unknown-values fail \
    --output "/reports/$(basename -- "$report_path")" || scan_exit=$?
fi

[[ -s "$report_path" ]] || die "Scanner produced no report (exit $scan_exit)"
jq -e '.summary.policies_evaluated > 0 and .summary.resources_scanned > 0' "$report_path" >/dev/null || die "No policies or resources evaluated"
sarif_path="${report_path%.json}.sarif"
python3 "$action_dir/report.py" convert "$report_path" "$sarif_path"
jq '{bundle, summary, violations}' "$report_path"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'report-path=%s\n' "$report_path"
    printf 'sarif-path=%s\n' "$sarif_path"
    printf 'bundle-revision=%s\n' "$(jq -r '.bundle.revision' "$report_path")"
  } >> "$GITHUB_OUTPUT"
fi
exit "$scan_exit"
