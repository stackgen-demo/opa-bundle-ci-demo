#!/usr/bin/env bash
set +x
set -euo pipefail

: "${GH_TOKEN:?Missing GitHub token for PR comments}"
: "${GITHUB_REPOSITORY:?Missing GITHUB_REPOSITORY}"
: "${GITHUB_EVENT_PATH:?Missing GITHUB_EVENT_PATH}"
: "${GITHUB_WORKSPACE:?Missing GITHUB_WORKSPACE}"
: "${PR_HEAD_SHA:?Missing pull request head SHA}"

pr_number="$(jq -r '.pull_request.number // empty' "$GITHUB_EVENT_PATH")"
[[ "$pr_number" =~ ^[0-9]+$ ]] || exit 0
current_head="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$pr_number" --jq '.head.sha')"
if [[ "$current_head" != "$PR_HEAD_SHA" ]]; then
  printf 'Skipping comment for an outdated PR commit.\n'
  exit 0
fi

umask 077
temp_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/stackgen-comment.XXXXXX")"
trap 'rm -f -- "$temp_dir/body" "$temp_dir/payload.json" "$temp_dir/comments.json"; rmdir -- "$temp_dir"' EXIT
report_path="$(realpath -m -- "$GITHUB_WORKSPACE/${OUTPUT_FILE:-reports/scan-results.json}")"
[[ "$report_path" == "$(realpath -- "$GITHUB_WORKSPACE")/"* ]] || { printf 'Report path escapes checkout.\n' >&2; exit 3; }
run_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"

{
  printf '<!-- stackgen-governance-scan -->\n'
  if [[ -s "$report_path" ]] && jq -e '.summary and .bundle and (.violations | type == "array")' "$report_path" >/dev/null; then
    jq -r --arg outcome "${SCAN_OUTCOME:-failure}" --arg commit "$PR_HEAD_SHA" --arg run "$run_url" '
      def cell: tostring | gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;") |
        gsub("\\|"; "&#124;") | gsub("`"; "&#96;") | gsub("[\r\n]"; " ") | gsub("@"; "&#64;");
      (if .summary.violations > 0 then "FAIL"
       elif (.summary.resources_with_unknown_attributes // 0) > 0 then "INCOMPLETE"
       elif $outcome == "success" and .summary.passed then "PASS"
       else "ERROR" end) as $status |
      "## StackGen governance: \($status)", "",
      "Commit: `\($commit)`", "",
      "| Policies | Resources | Violations | Unknown resources |",
      "| ---: | ---: | ---: | ---: |",
      "| \(.summary.policies_evaluated) | \(.summary.resources_scanned) | \(.summary.violations) | \(.summary.resources_with_unknown_attributes // 0) |", "",
      "Bundle revision: `\(.bundle.revision | cell)`", "",
      (if (.violations | length) > 0 then
        "### Findings", "",
        "| Severity | Rule | Resource | Attribute | Message |",
        "| --- | --- | --- | --- | --- |",
        (.violations[:30][] | "| \(.severity | ascii_upcase | cell) | `\(.rule_id | cell)` | `\(.address | cell)` | `\(.attribute | cell)` | \(.message | cell | .[:300]) |"),
        (if (.violations | length) > 30 then "", "Showing the first 30 findings; the full JSON report is attached to the CI run." else empty end)
       else "No definite policy violations reported." end), "",
      (if $status == "INCOMPLETE" then "Unresolved configuration values prevent a compliant result.", "" else empty end),
      (if $status == "ERROR" then "The scan did not complete successfully; do not treat this as a passing result.", "" else empty end),
      "[View CI logs and download the full scan report](\($run))"
    ' "$report_path"
  else
    printf '## StackGen governance: ERROR\n\n'
    printf 'Commit: `%s`\n\n' "$PR_HEAD_SHA"
    printf 'The scan did not produce a valid report. Check authentication, bundle download, or evaluation errors. This is not a compliant result.\n\n'
    printf '[View CI logs](%s)\n' "$run_url"
  fi
} > "$temp_dir/body"

gh api --paginate --slurp "repos/$GITHUB_REPOSITORY/issues/$pr_number/comments?per_page=100" > "$temp_dir/comments.json"
comment_id="$(jq -r '[.[][] | select(.user.login == "github-actions[bot]" and (.body | startswith("<!-- stackgen-governance-scan -->")))][0].id // empty' "$temp_dir/comments.json")"
jq -Rs '{body: .}' "$temp_dir/body" > "$temp_dir/payload.json"
if [[ -n "$comment_id" ]]; then
  gh api --method PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$comment_id" --input "$temp_dir/payload.json" --silent
  printf 'Updated governance result comment on PR #%s.\n' "$pr_number"
else
  gh api --method POST "repos/$GITHUB_REPOSITORY/issues/$pr_number/comments" --input "$temp_dir/payload.json" --silent
  printf 'Created governance result comment on PR #%s.\n' "$pr_number"
fi
