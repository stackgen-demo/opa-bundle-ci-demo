#!/usr/bin/env python3
"""Temporary demo runner: use the committed Linux CLI, not a local Go build."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required CI setting: {name}")
    return value


def main():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise ValueError("The demo binary requires Linux amd64; run this workflow on ubuntu-latest")

    api = required("STACKGEN_API_URL").rstrip("/")
    parsed = urlsplit(api)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("STACKGEN_API_URL must be an HTTPS base URL without credentials or query parameters")
    workspace = required("STACKGEN_WORKSPACE_ID")
    token = required("STACKGEN_TOKEN")
    source = ROOT / "terraform"

    candidates = [ROOT / "bin/stackgen360guard-linux-amd64", ROOT / "bin/stackgen360guard-linux-amd64.gz"]
    artifacts = [path for path in candidates if path.is_file()]
    if len(artifacts) != 1:
        raise ValueError("Expected exactly one committed Linux scanner binary or gzip archive")
    artifact = artifacts[0]
    checksums = {}
    for line in (ROOT / "bin/SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.strip()] = digest
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if checksums.get(artifact.name) != digest:
        raise ValueError("Scanner binary checksum mismatch")
    print(f"Scanner checksum verified: {digest}", flush=True)

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    result_file = reports / "scan-results.json"
    result_file.unlink(missing_ok=True)
    query = urlencode({"target": "resource-config", "orgId": workspace})
    bundle_url = f"{api}/iac-gen/v1/workspaces/{workspace}/policy-bundle?{query}"

    with tempfile.TemporaryDirectory(prefix="stackgen-governance-", dir=os.environ.get("RUNNER_TEMP")) as temp:
        temp = Path(temp)
        executable = temp / "stackgen360guard"
        if artifact.suffix == ".gz":
            with gzip.open(artifact, "rb") as source_file, executable.open("xb") as target:
                shutil.copyfileobj(source_file, target)
        else:
            shutil.copyfile(artifact, executable)
        executable.chmod(0o700)
        token_file = temp / "token"
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(token)
        child_env = {key: value for key, value in os.environ.items() if key != "STACKGEN_TOKEN"}

        capabilities = subprocess.run([str(executable), "capabilities"], env=child_env, capture_output=True, text=True, check=True)
        if "stackgen.resource-config/v1" not in json.loads(capabilities.stdout).get("supported_bundle_contracts", []):
            raise ValueError("Committed scanner does not support workspace resource-config bundles")

        command = [str(executable), "scan", "--input", str(source), "--input-type", "terraform-hcl",
                   "--bundle-url", bundle_url, "--bundle-token-file", str(token_file),
                   "--fail-on", "high", "--unknown-values", "fail", "--output", str(result_file)]
        print(f"Scanning {source.relative_to(ROOT)} against workspace {workspace}", flush=True)
        result = subprocess.run(command, env=child_env, capture_output=True, text=True, timeout=180)
        for output in (result.stdout, result.stderr):
            if output:
                print(output.replace(token, "[REDACTED]"), end="", flush=True)
        if not result_file.is_file():
            raise ValueError(f"Scanner produced no report (exit {result.returncode}); inspect download or evaluation errors above")
        report = json.loads(result_file.read_text())
        print(json.dumps(report, indent=2), flush=True)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            for finding in report.get("violations", []):
                message = f"{finding['rule_id']}: {finding['message']} ({finding['attribute']})"
                message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error file=terraform/main.tf,title=Governance policy failed::{message}", flush=True)
        if not report.get("summary", {}).get("policies_evaluated"):
            raise ValueError("No governance policies were evaluated; refusing an unenforced green check")
        if not report.get("summary", {}).get("resources_scanned"):
            raise ValueError("No Terraform resources were scanned")
        return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Governance scan failed: {error}", file=sys.stderr)
        sys.exit(3)
