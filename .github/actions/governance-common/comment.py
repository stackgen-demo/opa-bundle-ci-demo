"""Shared safe summaries and idempotent, current-commit-only PR comment publishing."""
import html
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


PROFILES = {
    'terraform': {'marker': '<!-- stackgen-governance-scan -->', 'report': 'reports/scan-results.json',
                  'title': 'StackGen Governance', 'resources': 'Resources',
                  'description': 'Terraform resources were evaluated against workspace governance.'},
    'helm': {'marker': '<!-- stackgen-helm-governance-scan -->', 'report': 'helm/reports/scan-results.json',
             'title': 'Helm governance', 'resources': 'Kubernetes resources',
             'description': 'Helm charts were rendered and the generated Kubernetes manifests were evaluated against workspace governance.'},
}


def api(endpoint, payload=None, paginate=False, method=None):
    args = ['gh', 'api', endpoint]
    if paginate:
        args += ['--paginate', '--slurp']
    if payload is not None:
        args += ['--method', method or 'POST', '--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def cell(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('`', '&#96;').replace('@', '&#64;').replace('\n', ' ').replace('\r', ' ')


def scan_status(report, outcome):
    if report is None:
        return 'ERROR'
    summary = report['summary']
    if summary['violations']:
        return 'FAIL'
    if summary.get('resources_with_unknown_attributes', 0):
        return 'INCOMPLETE'
    return 'PASS' if outcome == 'success' and summary['passed'] else 'ERROR'


def comment_body(report, outcome, *, kind):
    profile = PROFILES[kind]
    status = scan_status(report, outcome)
    body = [profile['marker'], f"## {profile['title']}: {status}", '']
    if report is None:
        return '\n'.join(body + ['Rendering, bundle download, or evaluation failed. Check the workflow logs; this is not a passing scan.'])
    summary = report['summary']
    body += [profile['description'], '',
             f"| Policies | {profile['resources']} | Violations | Unknown resources |",
             '| ---: | ---: | ---: | ---: |',
             f"| {summary['policies_evaluated']} | {summary['resources_scanned']} | {summary['violations']} | {summary.get('resources_with_unknown_attributes', 0)} |", '']
    if report['violations']:
        body += ['| Severity | Rule | Resource | Attribute | Finding |', '| --- | --- | --- | --- | --- |']
        for item in report['violations'][:30]:
            values = [str(item.get('severity', '')).upper(), item.get('rule_id') or item.get('policy_id', ''),
                      item.get('address') or item.get('resource_id', ''), item.get('attribute', ''), str(item.get('message', ''))[:300]]
            body.append('| ' + ' | '.join(cell(value) for value in values) + ' |')
        if len(report['violations']) > 30:
            body += ['', 'Showing the first 30 findings; the full JSON report is attached to the workflow run.']
    elif status == 'PASS':
        body.append('No policy violations found in the evaluated resources.')
    elif status == 'INCOMPLETE':
        body.append('Some configuration values are unresolved; this is not a passing governance check.')
    else:
        body.append('This scan did not finish successfully; check the workflow logs.')
    body += ['', 'Full JSON/SARIF reports are attached to the workflow run.']
    if kind == 'helm':
        body += ['Rendered YAML is also attached. Generated YAML locations are not attributed to Helm template or values lines.']
    return '\n'.join(body)


def load_report(workspace, relative_path):
    path = (workspace / relative_path).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Report path escapes checkout')
    try:
        report = json.loads(path.read_text())
        summary = report['summary']
        counts = [summary[key] for key in ['policies_evaluated', 'resources_scanned', 'violations']]
        counts.append(summary.get('resources_with_unknown_attributes', 0))
        if not all(type(value) is int and value >= 0 for value in counts):
            return None
        if not isinstance(summary['passed'], bool) or not isinstance(report['bundle']['revision'], str):
            return None
        if not isinstance(report['violations'], list) or not all(isinstance(item, dict) for item in report['violations']):
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


def safe_app_url(value):
    if any(character in value for character in '<>\r\n'):
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    return value if parsed.scheme == 'https' and parsed.netloc and not parsed.username else ''


def comment_footer(report, *, head, run_url, commit_url, app_url=''):
    links = f'[View workflow run]({run_url})'
    if app_url:
        links += f' | [Open StackGen](<{app_url}>)'
    details = ['<details>', '<summary>Scan details</summary>', '',
               f'Commit: [{head[:7]}]({commit_url})']
    if report is not None:
        details += ['', 'Bundle revision: `' + cell(report['bundle']['revision']) + '`']
    details += ['', '</details>']
    return '\n\n' + links + '\n\n' + '\n'.join(details)


def publish(kind):
    profile = PROFILES[kind]
    if not os.environ.get('GH_TOKEN'):
        raise ValueError('Missing GH_TOKEN for PR comments')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    report = load_report(workspace, os.environ.get('OUTPUT_FILE', profile['report']))
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    number = event.get('pull_request', {}).get('number')
    if not isinstance(number, int) or number <= 0:
        return
    repo, head = os.environ['GITHUB_REPOSITORY'], os.environ['PR_HEAD_SHA']
    endpoint = f'repos/{repo}/pulls/{number}'
    if api(endpoint)['head']['sha'] != head:
        print('Skipping comment for an outdated PR commit')
        return
    body = comment_body(report, os.environ.get('SCAN_OUTCOME', 'failure'), kind=kind)
    run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    commit_url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/commit/{head}"
    app_url = safe_app_url(os.environ.get('STACKGEN_APP_URL', ''))
    body += comment_footer(report, head=head, run_url=run_url, commit_url=commit_url, app_url=app_url)
    pages = api(f'repos/{repo}/issues/{number}/comments?per_page=100', paginate=True)
    existing = next((item for page in pages for item in page
                     if item['user']['login'] == 'github-actions[bot]' and item['body'].startswith(profile['marker'])), None)
    if api(endpoint)['head']['sha'] != head:
        print('Skipping comment because the PR head changed')
        return
    payload = {'body': body[:60000]}
    if existing:
        api(f"repos/{repo}/issues/comments/{existing['id']}", payload, method='PATCH')
    else:
        api(f'repos/{repo}/issues/{number}/comments', payload)
    print(f'Published {kind} governance summary on PR #{number}')


if __name__ == '__main__':
    publish(sys.argv[1])
