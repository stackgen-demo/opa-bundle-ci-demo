#!/usr/bin/env python3
"""Terraform adapter: retain source mapping and hints around shared SARIF conversion."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terraform_locations import HINTS, terraform_locations

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'governance-common'))
from sarif import convert as common_convert


def convert(report, workspace, source):
    return common_convert(report, kind='terraform', hints=HINTS,
                          locations=terraform_locations(source, workspace))


if __name__ == '__main__':
    report_path, output_path, workspace, source = map(Path, sys.argv[1:])
    result = convert(json.loads(report_path.read_text()), workspace.resolve(), source.resolve())
    output_path.write_text(json.dumps(result, indent=2) + '\n')
    print(f"SARIF report: {output_path} ({len(result['runs'][0]['results'])} findings)")
