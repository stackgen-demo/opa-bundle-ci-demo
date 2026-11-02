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
commit_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/$PR_HEAD_SHA"
stackgen_url="${STACKGEN_APP_URL:-}"
if [[ -n "$stackgen_url" && ( "$stackgen_url" != https://* || "$stackgen_url" == *'<'* || "$stackgen_url" == *'>'* || "$stackgen_url" == *$'\r'* || "$stackgen_url" == *$'\n'* ) ]]; then
  printf 'Skipping invalid StackGen browser link.\n' >&2
  stackgen_url=""
fi
valid_report=false
if [[ -s "$report_path" ]] && jq -e '.summary and .bundle and (.violations | type == "array")' "$report_path" >/dev/null 2>&1; then
  valid_report=true
fi

{
  printf '<!-- stackgen-governance-scan -->\n'
  if [[ "$valid_report" == true ]]; then
    jq -r --arg outcome "${SCAN_OUTCOME:-failure}" '
      def cell: tostring | gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;") |
        gsub("\\|"; "&#124;") | gsub("`"; "&#96;") | gsub("[\r\n]"; " ") | gsub("@"; "&#64;");
      (if .summary.violations > 0 then "FAIL"
       elif (.summary.resources_with_unknown_attributes // 0) > 0 then "INCOMPLETE"
       elif $outcome == "success" and .summary.passed then "PASS"
       else "ERROR" end) as $status |
      (if $status == "PASS" then
         "## StackGen Governance: Looking good!", "",
         "Your changes passed all evaluated governance policies for this workspace. Nice work!"
       elif $status == "FAIL" then
         "## StackGen Governance: Let\u0027s get this ready to ship", "",
         "Review the findings below, make the updates, and push a new commit to check again."
       elif $status == "INCOMPLETE" then
         "## StackGen Governance: A little more information, then we can finish", "",
        "Some configuration values are unresolved, so we can\u0027t confirm compliance yet. Rerun with resolved values to complete the check."
       else
         "## StackGen Governance: We couldn\u0027t complete this check just yet", "",
        "This run did not finish successfully, so it isn\u0027t a passing governance check. Check the CI logs, then try again."
       end), "",
      "| Policies | Resources | Violations | Unknown resources |",
      "| ---: | ---: | ---: | ---: |",
      "| \(.summary.policies_evaluated) | \(.summary.resources_scanned) | \(.summary.violations) | \(.summary.resources_with_unknown_attributes // 0) |", "",
      (if (.violations | length) > 0 then
        "### Findings", "",
        "| Severity | Rule | Resource | Attribute | Message |",
        "| --- | --- | --- | --- | --- |",
        (.violations[:30][] | "| \(.severity | ascii_upcase | cell) | `\(.rule_id | cell)` | `\(.address | cell)` | `\(.attribute | cell)` | \(.message | cell | .[:300]) |"),
        (if (.violations | length) > 30 then "", "Showing the first 30 findings; the full JSON report is attached to the CI run." else empty end)
       elif $status == "PASS" then "No policy violations found in the evaluated resources."
       else empty end)
    ' "$report_path"
  else
    printf "## StackGen Governance: We couldn't complete this check just yet\n\n"
    printf "Your change hasn't been validated yet. Check the CI logs for authentication, bundle download, or evaluation errors, then try again.\n"
  fi
  printf '\n[View CI logs and full report](%s)' "$run_url"
  if [[ -n "$stackgen_url" ]]; then
    printf ' | [Open StackGen](<%s>)' "$stackgen_url"
  fi
  printf '\n\n<details>\n<summary>Scan details</summary>\n\n'
  printf 'Commit: [%s](%s)\n\n' "${PR_HEAD_SHA:0:7}" "$commit_url"
  if [[ "$valid_report" == true ]]; then
    jq -r '(.bundle.revision // "unavailable" | tostring | gsub("`"; "&#96;") | gsub("[\r\n]"; " ")) as $revision |
      "Bundle revision: `\($revision)`"' "$report_path"
  fi
  printf '\n</details>\n'
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
