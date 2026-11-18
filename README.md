# opa-bundle-ci-demo

Checks Terraform and Helm changes against the governance policies of a StackGen workspace. The same scan runs in two places:

- **GitHub Actions** on pull requests and pushes to `main`.
- **Locally from Kiro**, through two skills that run the same scan scripts as CI.

Both download the workspace `resource-config` OPA policy bundle from StackGen and evaluate it with the `stackgen360guard` scanner image. A high-severity finding fails the scan.

## Repository layout

| Path | Purpose |
|---|---|
| `terraform/` | Terraform sample scanned by `tf-governance` |
| `helm/` | Helm charts (`web`, `worker`) scanned by `helm-governance` |
| `.github/workflows/tf-governance.yml` | CI workflow for Terraform |
| `.github/workflows/helm-governance.yml` | CI workflow for Helm |
| `.github/actions/tf-governance/` | Terraform scan action (`scan.sh`, PR comment, SARIF) |
| `.github/actions/helm-governance/` | Helm scan action (render, bundle check, scan, report) |
| `.kiro/skills/terraform-policy-scanner/` | Kiro skill: local Terraform scan |
| `.kiro/skills/helm-policy-scanner/` | Kiro skill: local Helm scan |
| `.kiro/scripts/governance-scan.sh` | Local wrapper both skills run |

## StackGen workspace prerequisites

The workspace must have a governance configuration assigned to it. Without one, the bundle download fails with HTTP 503.

- The governance must pin OPA policies with target `resource`. Module policies are not exported.
- For Helm, the governance must pin at least one policy for each type in `required-resource-types` (default `k8s/apps/v1/Deployment`). There are no built-in Kubernetes resource policies, so import custom ones.
- The bundle target is always `resource-config`. There is no `kubernetes` target; Kubernetes policies ship inside the `resource-config` bundle.
- The token needs access to the workspace (at least `createAppStacks`). Otherwise the API returns HTTP 403.

## GitHub Actions

| Workflow | Runs on | Token secret |
|---|---|---|
| Terraform governance | PRs and pushes to `main` that change `terraform/**`, or manual run | `STACKGEN_TOKEN` |
| Helm governance | PRs and pushes to `main` that change `helm/**`, or manual run | `STACKGEN_KUBERNETES_TOKEN` |

Repository variables: `STACKGEN_API_URL`, `STACKGEN_WORKSPACE_ID`.

Each run comments on the PR, uploads SARIF to code scanning (needs GitHub Advanced Security on private repos), and uploads the JSON report as an artifact. Workflow permissions: `contents: read`, `pull-requests: write`, `security-events: write`.

To check a branch that does not touch `terraform/` or `helm/`, run the workflow by hand from the Actions tab.

## Local scans with Kiro

### 1. Install requirements (macOS)

| Need | Install |
|---|---|
| Docker runtime | `brew install colima docker`, then `colima start` (or Docker Desktop) |
| Helm 3 (Helm scans) | `brew install helm@3`. CI pins the version in `.github/actions/helm-governance/action.yml`. |
| GNU coreutils (Terraform scans) | `brew install coreutils` |
| python3, git | Xcode command line tools, or `brew install python git` |
| jq, curl, timeout | Included in recent macOS; else `brew install jq curl coreutils` |
| gh (optional) | `brew install gh`, then `gh auth login` |

`brew install colima` does not install the Docker CLI; install `docker` as well.

### 2. Configure settings

The wrapper holds no environment values. It reads each setting from the first source that has it:

1. Variables exported in your shell.
2. The env file named by `STACKGEN_ENV_FILE`, else `.env.stackgen` at the repo root.
3. GitHub repository variables (`gh variable get`), for the URL and workspace only.

Create `.env.stackgen` in your own terminal. It is gitignored by `.env.*`; never commit it.

```bash
umask 077
cat > .env.stackgen <<'EOF'
STACKGEN_API_URL=https://<your-stackgen-host>
STACKGEN_WORKSPACE_ID=<workspace-uuid>
STACKGEN_TOKEN=<token>
EOF
```

To keep the token in a separate file, replace the `STACKGEN_TOKEN` line with one that sources that file, for example `. "$HOME/.config/stackgen/<name>.env"`.

Never paste a token into Kiro chat. If you do, revoke it.

### 3. Ask Kiro

Start a new Kiro chat after pulling skill changes, so Kiro loads the current version.

| Goal | Ask Kiro |
|---|---|
| Scan Terraform | `Run the terraform-policy-scanner skill to scan the terraform folder against the governance bundle.` |
| Scan Helm | `Run the helm-policy-scanner skill to scan the helm folder against the governance bundle.` |
| Scan another folder | `Run the terraform-policy-scanner skill on infra/tf.` or `Run the helm-policy-scanner skill on charts.` |
| Explain a finding | `Why did STACKGEN_AWS_SECURITY_GROUP_046 fire? Read the policy Rego and explain what it checks.` |
| Check setup | `What does the helm-policy-scanner skill need to run?` |

Plain requests such as "scan the helm folder" also work; each skill description says which files it covers.

Kiro runs one command for you. You can run it yourself from the repo root:

```bash
bash .kiro/scripts/governance-scan.sh terraform
bash .kiro/scripts/governance-scan.sh helm
bash .kiro/scripts/governance-scan.sh helm charts   # another folder, relative to the repo root
```

### 4. Read the result

| Exit | Meaning |
|---|---|
| 0 | Scan ran; no high finding (medium and low findings may still be listed) |
| 1 | Scan ran; at least one high finding. CI fails the same way. |
| 3 | Scan did not run. Read the `Governance scan setup failed:` or `Governance scan failed:` line. |

Reports:

| Scan | JSON | SARIF |
|---|---|---|
| Terraform | `reports/scan-results.json` | `reports/scan-results.sarif` |
| Helm | `helm/reports/scan-results.json` | `helm/reports/scan-results.sarif` |

The first output line, `Scanning <path> (<kind>) against <url> workspace <id>`, shows which workspace was used.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bundle download returned HTTP 503` | No governance assigned to the workspace | Assign a governance configuration that pins OPA resource policies |
| HTTP 403 on the bundle | Token has no access to the workspace | Use a token for that workspace, or change `STACKGEN_WORKSPACE_ID` |
| HTTP 400 `invalid value 'kubernetes'` | Something requested `target=kubernetes` | Use `target=resource-config` |
| `No exported policies for: k8s/apps/v1/Deployment` | Governance pins no policy for that type | Pin Kubernetes resource policies in the governance |
| `missing tools: ...` | Tool not installed or not on `PATH` | Install the listed tools (see requirements) |
| `Docker is installed but not reachable` | Runtime stopped | `colima start`, or start Docker Desktop |
| `bind source path does not exist` | Temp files outside `$HOME` under Colima | Run through the wrapper; it keeps temp files in `~/.cache/stackgen-governance` |
| `WARNING: local Helm ... differs from CI Helm` | Local Helm major version differs from CI | Install the Helm version CI pins |
| `[WARN] missing feature flag key` | Scanner log noise | Ignore |
