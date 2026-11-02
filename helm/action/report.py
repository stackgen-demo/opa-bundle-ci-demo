#!/usr/bin/env python3
"""Publish Helm findings without pretending generated YAML lines are Helm source lines."""
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys


MARKER = '<!-- stackgen-helm-governance-scan -->'


def convert(report):
    rules, results, indices = [], [], {}
    for violation in report['violations']:
        rule = violation.get('rule_id') or violation['policy_id']
        severity = violation['severity'].lower()
        if rule not in indices:
            indices[rule] = len(rules)
            rules.append({'id': rule, 'shortDescription': {'text': rule},
                          'properties': {'security-severity': {'critical': '9.0', 'high': '8.0', 'medium': '5.0', 'low': '2.0'}.get(severity, '0.0')}})
        address = violation.get('address', violation.get('resource_id', 'unknown resource'))
        attribute = violation.get('attribute', 'configuration')
        results.append({
            'ruleId': rule, 'ruleIndex': indices[rule],
            'level': {'critical': 'error', 'high': 'error', 'medium': 'warning'}.get(severity, 'note'),
            'message': {'text': f"{address} ({attribute}): {violation['message']}"},
            'partialFingerprints': {'stackgenHelmResource/v1': hashlib.sha256(f'{rule}:{address}:{attribute}'.encode()).hexdigest()},
        })
    return {'$schema': 'https://json.schemastore.org/sarif-2.1.0.json', 'version': '2.1.0',
            'runs': [{'tool': {'driver': {'name': 'stackgen360guard-helm', 'rules': rules}},
                      'results': results, 'properties': {'bundleRevision': report['bundle']['revision']}}]}


def cell(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('`', '&#96;').replace('@', '&#64;').replace('\n', ' ').replace('\r', ' ')


def comment_body(report, outcome):
    if report is None:
        return MARKER + '\n## Helm governance: ERROR\n\nRendering, bundle download, or evaluation failed. Check the workflow logs; this is not a passing scan.'
    summary = report['summary']
    status = 'PASS' if outcome == 'success' and summary['passed'] else 'FAIL' if summary['violations'] else 'ERROR'
    body = [MARKER, f'## Helm governance: {status}', '',
            'Helm charts were rendered and the generated Kubernetes manifests were evaluated against workspace governance.', '',
            '| Policies | Kubernetes resources | Violations |', '| ---: | ---: | ---: |',
            f"| {summary['policies_evaluated']} | {summary['resources_scanned']} | {summary['violations']} |", '']
    if report['violations']:
        body += ['| Rule | Resource | Attribute | Finding |', '| --- | --- | --- | --- |']
        for item in report['violations'][:30]:
            body.append('| ' + ' | '.join(cell(item.get(key, '')) for key in ['rule_id', 'address', 'attribute', 'message']) + ' |')
    body += ['', 'Rendered YAML and full JSON/SARIF reports are attached to the workflow run.',
             'Generated YAML locations are not attributed to Helm template or values lines.', '',
             'Bundle revision: `' + cell(report['bundle']['revision']) + '`']
    return '\n'.join(body)


def github_api(path, payload=None, paginate=False):
    args = ['gh', 'api', path]
    if paginate:
        args += ['--paginate', '--slurp']
    if payload is not None:
        args += ['--method', 'POST', '--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def comment():
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    path = (workspace / os.environ.get('OUTPUT_FILE', 'helm/reports/scan-results.json')).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Report path escapes checkout')
    report = json.loads(path.read_text()) if path.is_file() else None
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    number = event['pull_request']['number']
    repo = os.environ['GITHUB_REPOSITORY']
    head = os.environ['PR_HEAD_SHA']
    endpoint = f'repos/{repo}/pulls/{number}'
    if github_api(endpoint)['head']['sha'] != head:
        print('Skipping outdated Helm scan comment')
        return
    body = comment_body(report, os.environ.get('SCAN_OUTCOME', 'failure'))
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    body += f'\n\n[View workflow run]({run_url})\nCommit: `{head}`'
    pages = github_api(f'repos/{repo}/issues/{number}/comments?per_page=100', paginate=True)
    existing = next((item for page in pages for item in page
                     if item['user']['login'] == 'github-actions[bot]' and item['body'].startswith(MARKER)), None)
    if github_api(endpoint)['head']['sha'] != head:
        return
    if existing:
        subprocess.run(['gh', 'api', '--method', 'PATCH', f"repos/{repo}/issues/comments/{existing['id']}", '--input', '-', '--silent'],
                       input=json.dumps({'body': body[:60000]}), text=True, check=True)
    else:
        github_api(f'repos/{repo}/issues/{number}/comments', {'body': body[:60000]})
    print(f'Published Helm governance summary on PR #{number}')


if __name__ == '__main__':
    if sys.argv[1] == 'convert':
        report = json.loads(Path(sys.argv[2]).read_text())
        Path(sys.argv[3]).write_text(json.dumps(convert(report), indent=2) + '\n')
    elif sys.argv[1] == 'comment':
        comment()
    else:
        raise ValueError('Expected convert or comment')
