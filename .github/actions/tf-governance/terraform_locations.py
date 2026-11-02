#!/usr/bin/env python3
"""Terraform-only source locations and remediation hints for shared SARIF conversion."""

import re
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
