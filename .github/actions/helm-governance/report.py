#!/usr/bin/env python3
"""Helm adapter for shared summaries and SARIF, with no invented source locations."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'governance-common'))
from comment import comment_body as common_comment_body, publish
from sarif import convert as common_convert


def convert(report):
    return common_convert(report, kind='helm')


def comment_body(report, outcome):
    return common_comment_body(report, outcome, kind='helm')


if __name__ == '__main__':
    if sys.argv[1] == 'convert':
        report = json.loads(Path(sys.argv[2]).read_text())
        Path(sys.argv[3]).write_text(json.dumps(convert(report), indent=2) + '\n')
    elif sys.argv[1] == 'comment':
        publish('helm')
    else:
        raise ValueError('Expected convert or comment')
