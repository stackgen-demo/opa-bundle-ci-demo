---
name: helm-policy-scanner
description: Helm and Kubernetes only. Render Helm charts and scan the resulting Kubernetes manifests with the same StackGen governance scanner the GitHub helm-governance workflow uses. Use when the user asks to scan Helm, run a Helm policy check, check Kubernetes governance, or run the local equivalent of that workflow. For Terraform files, use policy-scanner instead.
---

# Helm policy scanner

This file is the only source of truth for this skill. Answer questions about its setup, dependencies, or behavior from the current text of this file. Ignore conclusions from earlier sessions or older versions of this skill.

Run this only after the user asks for a Helm policy scan in this turn. Do not scan on file edits, saves, or unrelated prompts. Do not scan Terraform; use `policy-scanner` for that. Do not run `helm install` or `helm upgrade`. Do not post a pull request comment or upload SARIF.

The scan is the same step as `.github/workflows/helm-governance.yml`. It runs `.github/actions/helm-governance/scan.sh` through `.kiro/scripts/governance-scan.sh`, which renders each chart under the scan path with `helm template`, downloads the workspace `resource-config` OPA bundle, and fails on a high finding.

## Bundle target

StackGen has one bundle target, `resource-config`. There is no `kubernetes` target; the API answers it with HTTP 400 `invalid value 'kubernetes' for PolicyBundleTarget`. Kubernetes policies (resource type `k8s/apps/v1/Deployment` and similar) ship inside the `resource-config` bundle when the workspace governance pins them.

- Do not call the bundle endpoint yourself before the scan. Run the command below.
- Ignore earlier sessions that said a separate Kubernetes bundle must be published.
- If `scan.sh` requests `target=kubernetes`, stop and tell the user to fix that line. Do not edit it.
- If `validate_bundle.py` prints `No exported policies for: <type>`, the workspace governance pins no policy for that type. Report it as exit 3 and name the missing type.

## Requirements

The script checks these before scanning and lists any that are missing.

| Need | macOS install | Notes |
|---|---|---|
| Docker runtime | `brew install colima docker`, then `colima start`; or Docker Desktop | Any runtime works. `brew install colima` does not install the Docker CLI. |
| Helm 3 | `brew install helm@3`, or a release matching CI | CI pins the version in `.github/actions/helm-governance/action.yml`. The script warns when the local major version differs; rendered manifests may then differ from CI. |
| python3 | `brew install python`, or Xcode command line tools | Standard library only. Renders charts and validates the bundle. |
| git | Xcode command line tools, or `brew install git` | Locates the repo root. |
| jq, curl, timeout | Included in recent macOS; else `brew install jq curl coreutils` | |
| gh (optional) | `brew install gh`, then `gh auth login` | Only for the GitHub repo variable fallback. |

GNU coreutils is not required for this scan unless `timeout` is missing.

Network: outbound HTTPS to `ghcr.io` (scanner image) and to the StackGen API URL (bundle download with `curl`).

StackGen: a token with access to the workspace, and governance assigned to that workspace that pins at least one policy for each type in `REQUIRED_RESOURCE_TYPES` (default `k8s/apps/v1/Deployment`).

## Configuration

This skill holds no environment values. Do not write a URL, workspace ID, token, or token path into a command. The script resolves each setting in this order:

1. Variables already exported in the shell.
2. The env file named by `STACKGEN_ENV_FILE`, else `.env.stackgen` at the repo root (gitignored).
3. GitHub repository variables `STACKGEN_API_URL` and `STACKGEN_WORKSPACE_ID` (`gh variable get`), the same values CI uses.

Required: `STACKGEN_API_URL`, `STACKGEN_WORKSPACE_ID`, `STACKGEN_TOKEN`. The token comes only from the shell or the env file. `REQUIRED_RESOURCE_TYPES` is optional and passes through to `scan.sh`.

Never print the token. Never read it from the chat, `mcp.json`, or `~/.stackgen/config.yaml`. If the user pastes a token into chat, stop and tell them to revoke it.

## Command

Run one line from the repo root. Do not split it across lines or prepend exports.

```bash
bash .kiro/scripts/governance-scan.sh helm
```

If the user names another directory, pass it as the second argument, relative to the repo root: `bash .kiro/scripts/governance-scan.sh helm charts`. Refuse a path that leaves the repo.

Do not edit `.github/actions/helm-governance/scan.sh`. Do not set `GITHUB_ACTIONS` or `GITHUB_TOKEN`. Do not run `report.py comment`.

## What to tell the user

Read `helm/reports/scan-results.json`. Lead with the exit code and the summary counts: `policies_evaluated`, `resources_scanned`, `violations`, and `passed`. Name the API URL and workspace the script printed on its `Scanning ...` line.

For each violation, give severity, `rule_id`, address, attribute, and message. Point at `helm/reports/scan-results.json` and `helm/reports/scan-results.sarif`.

Report each message as written. Do not guess why a policy exists, and do not recommend removing workloads or settings to satisfy it. If the user asks why a rule fired, read that policy's Rego (in the downloaded bundle, or from the StackGen policy API) and explain what it actually checks.

- Exit 0 means no high finding. Still list any medium or low findings.
- Exit 1 means at least one high finding. That is the same failure the GitHub job uses.
- Exit 3 means the scan did not run. Quote the `Governance scan setup failed:` or `Governance scan failed:` line. Do not invent a policy result. For a missing setting, tell the user which variable to set and where; do not guess a value.

This scan renders Helm templates and evaluates the resulting Kubernetes manifests. It does not apply anything to a cluster.
