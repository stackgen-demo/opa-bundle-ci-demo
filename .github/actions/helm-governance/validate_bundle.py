#!/usr/bin/env python3
"""Fail closed when the selected bundle has no policies for the rendered workloads."""
import json
import sys
import tarfile


def validate(path, required_types):
    with tarfile.open(path, 'r:gz') as archive:
        member = archive.getmember('data.json')
        if member.size > 16 << 20:
            raise ValueError('Bundle metadata exceeds the size limit')
        metadata = json.load(archive.extractfile(member))['stackgen']['resource_config']['metadata']
    policies = metadata['policies']
    if not policies:
        raise ValueError('The workspace selector exported no Kubernetes policies')
    required = {value.strip() for value in required_types.split(',') if value.strip()}
    if not required:
        raise ValueError('At least one required-resource-types entry is required')
    covered = {policy['resourceType'] for policy in policies}
    missing = required - covered
    if missing:
        raise ValueError('No exported policies for: ' + ', '.join(sorted(missing)))
    print(f'Bundle contains {len(policies)} policies; required workload types are covered')


if __name__ == '__main__':
    validate(sys.argv[1], sys.argv[2])
