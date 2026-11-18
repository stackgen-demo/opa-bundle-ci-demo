---
name: policy-scanner
description: Terraform only. Scan Terraform (.tf) files with the same StackGen governance scanner the GitHub tf-governance workflow uses. Use when the user asks to scan Terraform or run the local tf-governance check. For Helm charts or Kubernetes manifests, use helm-policy-scanner instead.
---

# Policy scanner (Terraform)

This file is the only source of truth for this skill. Answer questions about its setup, dependencies, or behavior from the current text of this file. Ignore conclusions from earlier sessions or older versions of this skill.

Run this only after the user asks for a Terraform policy scan in this turn. Do not scan on file edits, saves, or unrelated prompts. Do not scan Helm; use `helm-policy-scanner` for that. Do not run `terraform plan` or `terraform apply`. Do not post a pull request comment or upload SARIF.

The scan is the same step as `.github/workflows/tf-governance.yml`. It runs `.github/actions/tf-governance/scan.sh` through `.kiro/scripts/governance-scan.sh`, which reads Terraform source as `terraform-hcl`, downloads the workspace `resource-config` bundle, and fails on a high finding.

## Requirements

The script checks these before scanning and lists any that are missing.

| Need | macOS install | Notes |
|---|---|---|
| Docker runtime | `brew install colima docker`, then `colima start`; or Docker Desktop | Any runtime works. `brew install colima` does not install the Docker CLI. |
| GNU coreutils | `brew install coreutils` | `scan.sh` calls `realpath -m`; the script finds it with `brew --prefix coreutils`. |
| python3 | `brew install python`, or Xcode command line tools | Standard library only. |
| git | Xcode command line tools, or `brew install git` | Locates the repo root. |
| jq, curl, timeout | Included in recent macOS; else `brew install jq curl coreutils` | |
| gh (optional) | `brew install gh`, then `gh auth login` | Only for the GitHub repo variable fallback. |

Network: outbound HTTPS to `ghcr.io` (scanner image) and to the StackGen API URL. The scanner container downloads the bundle itself, so the container also needs that access.

StackGen: a token with access to the workspace, and governance assigned to that workspace that pins OPA `resource` policies.

## Configuration

This skill holds no environment values. Do not write a URL, workspace ID, token, or token path into a command. The script resolves each setting in this order:

1. Variables already exported in the shell.
2. The env file named by `STACKGEN_ENV_FILE`, else `.env.stackgen` at the repo root (gitignored).
3. GitHub repository variables `STACKGEN_API_URL` and `STACKGEN_WORKSPACE_ID` (`gh variable get`), the same values CI uses.

Required: `STACKGEN_API_URL`, `STACKGEN_WORKSPACE_ID`, `STACKGEN_TOKEN`. The token comes only from the shell or the env file.

Never print the token. Never read it from the chat, `mcp.json`, or `~/.stackgen/config.yaml`. If the user pastes a token into chat, stop and tell them to revoke it.

## Command

Run one line from the repo root. Do not split it across lines or prepend exports.

```bash
bash .kiro/scripts/governance-scan.sh terraform
```

If the user names another directory, pass it as the second argument, relative to the repo root: `bash .kiro/scripts/governance-scan.sh terraform infra/tf`. Refuse a path that leaves the repo.

Do not edit `.github/actions/tf-governance/scan.sh`. Do not set `GITHUB_ACTIONS` or `GITHUB_TOKEN`. Do not run `comment.sh` or `review.py`. Do not call the bundle endpoint yourself before the scan.

## What to tell the user

Read `reports/scan-results.json`. Lead with the exit code and the summary counts: `policies_evaluated`, `resources_scanned`, `violations`, and `passed`. Name the API URL and workspace the script printed on its `Scanning ...` line.

For each violation, give severity, `rule_id`, address, attribute, and message. Point at `reports/scan-results.json` and `reports/scan-results.sarif`.

Report each message as written. Do not guess why a policy exists, and do not recommend removing infrastructure to satisfy it. If the user asks why a rule fired, read that policy's Rego (in the downloaded bundle, or from the StackGen policy API) and explain what it actually checks.

- Exit 0 means no high finding. Still list any medium or low findings.
- Exit 1 means at least one high finding. That is the same failure the GitHub job uses.
- Exit 3 means the scan did not run. Quote the `Governance scan setup failed:` or `Governance scan failed:` line. Do not invent a policy result. For a missing setting, tell the user which variable to set and where; do not guess a value.

This scan reads `.tf` files. It does not resolve a Terraform plan.
