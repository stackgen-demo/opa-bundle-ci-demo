import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


COMMON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMMON))
import comment
import sarif


def fixture():
    return {'bundle': {'revision': 'sha256:test'},
            'summary': {'policies_evaluated': 2, 'resources_scanned': 4, 'violations': 1, 'passed': False},
            'violations': [{'policy_id': 'policy', 'rule_id': 'RULE', 'severity': 'high',
                            'address': 'aws_db_instance.app', 'attribute': 'storage_encrypted',
                            'message': 'Enable encryption'}]}


class SarifTests(unittest.TestCase):
    def test_preserves_tool_names_and_fingerprints(self):
        digest = hashlib.sha256(b'RULE:aws_db_instance.app:storage_encrypted').hexdigest()
        for kind, tool, key in [('terraform', 'stackgen360guard', 'stackgenResource/v1'),
                               ('helm', 'stackgen360guard-helm', 'stackgenHelmResource/v1')]:
            with self.subTest(kind=kind):
                result = sarif.convert(fixture(), kind=kind)['runs'][0]
                self.assertEqual(result['tool']['driver']['name'], tool)
                self.assertEqual(result['results'][0]['partialFingerprints'], {key: digest})

    def test_terraform_retains_hints_locations_and_inline_review_properties(self):
        location = {'artifactLocation': {'uri': 'terraform/main.tf'}, 'region': {'startLine': 10}}
        result = sarif.convert(fixture(), kind='terraform', hints={'RULE': 'Set encryption to true'},
                               locations={'aws_db_instance.app': location})['runs'][0]
        finding = result['results'][0]
        self.assertEqual(finding['locations'], [{'physicalLocation': location}])
        self.assertEqual(finding['properties'], {'resource': 'aws_db_instance.app', 'attribute': 'storage_encrypted', 'policyId': 'policy'})
        self.assertIn('Hint: Set encryption to true', finding['message']['text'])
        self.assertEqual(result['tool']['driver']['rules'][0]['help']['text'], 'Set encryption to true')

    def test_helm_does_not_invent_template_locations(self):
        result = sarif.convert(fixture(), kind='helm')['runs'][0]
        self.assertNotIn('locations', result['results'][0])
        self.assertNotIn('properties', result['results'][0])
        self.assertNotIn('help', result['tool']['driver']['rules'][0])

    def test_rules_are_deduplicated_and_severity_mapping_is_shared(self):
        data = fixture()
        data['violations'] *= 2
        for severity, level, score in [('critical', 'error', '9.0'), ('high', 'error', '8.0'),
                                       ('medium', 'warning', '5.0'), ('low', 'note', '2.0')]:
            data['violations'][0]['severity'] = severity
            result = sarif.convert(data, kind='terraform')['runs'][0]
            self.assertEqual(len(result['tool']['driver']['rules']), 1)
            self.assertEqual(result['results'][0]['level'], level)
            self.assertEqual(result['tool']['driver']['rules'][0]['properties']['security-severity'], score)

    def test_unknown_resources_preserve_terraform_execution_warning(self):
        data = fixture()
        data['summary']['resources_with_unknown_attributes'] = 1
        invocation = sarif.convert(data, kind='terraform')['runs'][0]['invocations'][0]
        self.assertFalse(invocation['executionSuccessful'])
        self.assertEqual(invocation['toolExecutionNotifications'][0]['level'], 'error')

    def test_empty_report_has_a_valid_empty_result_collection(self):
        data = fixture()
        data['violations'] = []
        self.assertEqual(sarif.convert(data, kind='helm')['runs'][0]['results'], [])


class CommentTests(unittest.TestCase):
    def test_statuses_are_consistent_for_both_actions(self):
        data = fixture()
        self.assertEqual(comment.scan_status(data, 'failure'), 'FAIL')
        data['summary']['violations'] = 0
        data['violations'] = []
        data['summary']['passed'] = True
        self.assertEqual(comment.scan_status(data, 'success'), 'PASS')
        self.assertEqual(comment.scan_status(data, 'failure'), 'ERROR')
        data['summary']['resources_with_unknown_attributes'] = 1
        self.assertEqual(comment.scan_status(data, 'success'), 'INCOMPLETE')
        self.assertEqual(comment.scan_status(None, 'success'), 'ERROR')

    def test_preserves_separate_existing_markers(self):
        self.assertTrue(comment.comment_body(fixture(), 'failure', kind='terraform').startswith('<!-- stackgen-governance-scan -->'))
        self.assertTrue(comment.comment_body(fixture(), 'failure', kind='helm').startswith('<!-- stackgen-helm-governance-scan -->'))

    def test_untrusted_findings_are_escaped_and_truncated(self):
        data = fixture()
        data['violations'][0]['message'] = '<script> | @everyone `unsafe`\n' + 'x' * 1000
        body = comment.comment_body(data, 'failure', kind='helm')
        self.assertNotIn('<script>', body)
        self.assertNotIn('@everyone', body)
        self.assertNotIn('x' * 301, body)
        self.assertIn('&#124;', body)

    def test_bad_or_missing_reports_are_errors_and_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as directory:
            root = Path(directory).resolve()
            self.assertIsNone(comment.load_report(root, 'missing.json'))
            for value in ['not json', '{}', '[]', json.dumps({'summary': []})]:
                (root / 'report.json').write_text(value)
                self.assertIsNone(comment.load_report(root, 'report.json'))
            (root / 'report.json').write_text(json.dumps(fixture()))
            self.assertEqual(comment.load_report(root, 'report.json'), fixture())
            with self.assertRaisesRegex(ValueError, 'escapes checkout'):
                comment.load_report(root, '../report.json')

    def test_app_link_rejects_unsafe_urls(self):
        for url in ['javascript:alert(1)', 'https://user:password@example.com', 'https://example.com/<script>', 'https://[bad']:
            self.assertEqual(comment.safe_app_url(url), '')
        self.assertEqual(comment.safe_app_url('https://example.com/app'), 'https://example.com/app')

    def test_links_share_a_line_and_metadata_is_collapsed(self):
        footer = comment.comment_footer(fixture(), head='abcdef123456', run_url='https://github.com/run',
                                        commit_url='https://github.com/commit/abcdef123456', app_url='https://example.com/app')
        self.assertIn('[View workflow run](https://github.com/run) | [Open StackGen](<https://example.com/app>)', footer.splitlines())
        self.assertIn('<details>\n<summary>Scan details</summary>', footer)
        self.assertNotIn('<details open', footer)
        visible, collapsed = footer.split('<details>', 1)
        self.assertNotIn('Commit:', visible)
        self.assertNotIn('Bundle revision:', visible)
        self.assertIn('Commit: [abcdef1]', collapsed)
        self.assertIn('Bundle revision: `sha256:test`', collapsed)
        self.assertTrue(collapsed.rstrip().endswith('</details>'))
        self.assertNotIn('Bundle revision:', comment.comment_body(fixture(), 'failure', kind='helm'))


class PublishingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP'))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        event = self.root / 'event.json'
        event.write_text(json.dumps({'pull_request': {'number': 7}}))
        (self.root / 'report.json').write_text(json.dumps(fixture()))
        env = {'GH_TOKEN': 'test-only', 'GITHUB_WORKSPACE': str(self.root), 'OUTPUT_FILE': 'report.json',
               'GITHUB_EVENT_PATH': str(event), 'GITHUB_REPOSITORY': 'example/demo', 'PR_HEAD_SHA': 'current',
               'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_RUN_ID': '123', 'SCAN_OUTCOME': 'failure'}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(comment, 'api')
    def test_outdated_commit_is_skipped_before_writing(self, api):
        api.return_value = {'head': {'sha': 'newer'}}
        comment.publish('helm')
        self.assertEqual(api.call_count, 1)

    @patch.object(comment, 'api')
    def test_midrun_commit_change_is_skipped(self, api):
        api.side_effect = [{'head': {'sha': 'current'}}, [], {'head': {'sha': 'newer'}}]
        comment.publish('helm')
        self.assertEqual(api.call_count, 3)

    @patch.object(comment, 'api')
    def test_matching_bot_comment_is_updated_without_touching_other_action(self, api):
        existing = [[{'id': 1, 'user': {'login': 'github-actions[bot]'}, 'body': '<!-- stackgen-governance-scan -->'},
                     {'id': 2, 'user': {'login': 'github-actions[bot]'}, 'body': '<!-- stackgen-helm-governance-scan -->'}]]
        api.side_effect = [{'head': {'sha': 'current'}}, existing, {'head': {'sha': 'current'}}, {}]
        comment.publish('helm')
        args, kwargs = api.call_args
        self.assertEqual(args[0], 'repos/example/demo/issues/comments/2')
        self.assertEqual(kwargs['method'], 'PATCH')
        self.assertIn('Commit: [current]', args[1]['body'])

    @patch.object(comment, 'api')
    def test_user_comments_are_not_modified(self, api):
        existing = [[{'id': 1, 'user': {'login': 'human'}, 'body': '<!-- stackgen-governance-scan -->'}]]
        api.side_effect = [{'head': {'sha': 'current'}}, existing, {'head': {'sha': 'current'}}, {}]
        comment.publish('terraform')
        self.assertEqual(api.call_args.args[0], 'repos/example/demo/issues/7/comments')


if __name__ == '__main__':
    unittest.main()
