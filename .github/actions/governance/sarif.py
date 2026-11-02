#!/usr/bin/env python3
"""Convert the existing JSON scan into SARIF without fetching another bundle."""

import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote


HINTS = {
    "STACKGEN_AWS_TENANT_OPA_SMOKE_S3_001": "Set block_public_acls, block_public_policy, ignore_public_acls and restrict_public_buckets to true.",
    "STACKGEN_AWS_TENANT_OPA_SMOKE_SG_001": "Restrict SSH ingress to trusted CIDRs; remove 0.0.0.0/0 and ::/0 from rules allowing port 22 or all protocols.",
    "STACKGEN_AWS_TENANT_OPA_SMOKE_RDS_001": "Set storage_encrypted = true. Existing databases may require an encrypted snapshot migration.",
    "STACKGEN_AWS_TENANT_OPA_SMOKE_RDS_002": "Set publicly_accessible = false and use private database subnets.",
    "STACKGEN_AWS_TENANT_OPA_SMOKE_RDS_003": "Set backup_retention_period to at least 7 days.",
    "STACKGEN_AWS_TENANT_OPA_SMOKE_RDS_004": "Set deletion_protection = true.",
}


def terraform_locations(source, workspace):
    # The current scanner report omits HCL file/line provenance. Locate resource
    # declarations in this demo; do not invent a location for ambiguous matches.
    declarations = {}
    pattern = re.compile(r'(?m)^[ \t]*resource\s+"([\w-]+)"\s+"([\w-]+)"\s*\{')
    for path in sorted(source.rglob("*.tf")):
        if ".terraform" in path.parts or ".git" in path.parts:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace):
            continue
        text = path.read_text()
        for match in pattern.finditer(text):
            address = f"{match[1]}.{match[2]}"
            declarations.setdefault(address, []).append({
                "artifactLocation": {
                    "uri": quote(resolved.relative_to(workspace).as_posix(), safe="/"),
                    "uriBaseId": "%SRCROOT%",
                },
                "region": {"startLine": text.count("\n", 0, match.start()) + 1},
            })
    return {address: locations[0] for address, locations in declarations.items() if len(locations) == 1}


def convert(report, workspace, source):
    locations = terraform_locations(source, workspace)
    rules = []
    rule_indices = {}
    results = []
    for violation in report["violations"]:
        rule_id = violation.get("rule_id") or violation["policy_id"]
        severity = violation["severity"].lower()
        hint = HINTS.get(rule_id, violation["message"])
        if rule_id not in rule_indices:
            rule_indices[rule_id] = len(rules)
            rules.append({
                "id": rule_id,
                "shortDescription": {"text": rule_id},
                "help": {"text": hint},
                "properties": {"security-severity": {"critical": "9.0", "high": "8.0", "medium": "5.0", "low": "2.0"}.get(severity, "0.0")},
            })
        address = violation.get("address") or violation.get("resource_id") or "unknown resource"
        attribute = violation.get("attribute") or "configuration"
        text = f"{address} ({attribute}): {violation['message']}"
        if hint != violation["message"]:
            text += f"\nHint: {hint}"
        result = {
            "ruleId": rule_id,
            "ruleIndex": rule_indices[rule_id],
            "level": {"critical": "error", "high": "error", "medium": "warning"}.get(severity, "note"),
            "message": {"text": text},
            "partialFingerprints": {
                "stackgenResource/v1": hashlib.sha256(f"{rule_id}:{address}:{attribute}".encode()).hexdigest(),
            },
            "properties": {"resource": address, "attribute": attribute, "policyId": violation["policy_id"]},
        }
        if address in locations:
            result["locations"] = [{"physicalLocation": locations[address]}]
        results.append(result)

    summary = report["summary"]
    incomplete = summary.get("resources_with_unknown_attributes", 0) > 0
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "stackgen360guard", "rules": rules}},
            "results": results,
            "invocations": [{
                "executionSuccessful": not incomplete,
                "toolExecutionNotifications": ([{
                    "level": "error",
                    "message": {"text": "Unresolved configuration values prevent a complete governance evaluation; check the JSON report."},
                }] if incomplete else []),
            }],
            "properties": {"bundleRevision": report["bundle"]["revision"]},
        }],
    }


if __name__ == "__main__":
    report_path, output_path, workspace, source = map(Path, sys.argv[1:])
    report = json.loads(report_path.read_text())
    sarif = convert(report, workspace.resolve(), source.resolve())
    output_path.write_text(json.dumps(sarif, indent=2) + "\n")
    print(f"SARIF report: {output_path} ({len(sarif['runs'][0]['results'])} findings)")
