import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('terraform_review', Path(__file__).resolve().parents[1] / 'review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
PATH = 'terraform/main.tf'


def finding(attribute='acl', message='Bucket must be private'):
    return {'ruleId': 'BUCKET_ACL', 'properties': {'resource': 'aws_s3_bucket.app', 'attribute': attribute},
            'message': {'text': message}, 'locations': [{'physicalLocation': {'artifactLocation': {'uri': PATH}}}]}


def existing(body, **changes):
    return {'id': 123, 'path': PATH, 'line': 2, 'commit_id': 'old-head',
            'user': {'login': 'github-actions[bot]'}, 'body': body, **changes}


class FindingIdentityTests(unittest.TestCase):
    def test_unchanged_finding_is_reused_after_a_new_commit_or_line_shift(self):
        item = finding()
        body = review.finding_body(review.finding_marker(PATH, [item]), [item])
        comments, updates = review.plan_comments({(PATH, 20): [item]}, [existing(body, line=None, original_line=2)])
        self.assertEqual(comments, [])
        self.assertEqual(updates, [])

    def test_message_changes_update_the_existing_comment(self):
        item = finding()
        body = review.finding_body(review.finding_marker(PATH, [item]), [item])
        item['message']['text'] = 'Updated remediation advice'
        comments, updates = review.plan_comments({(PATH, 2): [item]}, [existing(body)])
        self.assertEqual(comments, [])
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['id'], 123)
        self.assertIn('Updated remediation advice', updates[0]['body'])

    def test_legacy_comment_is_migrated_in_place(self):
        item = finding()
        legacy = review.finding_body('<!-- stackgen-governance-inline:old-commit-and-line-hash -->', [item])
        comments, updates = review.plan_comments({(PATH, 30): [item]}, [existing(legacy)])
        self.assertEqual(comments, [])
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['id'], 123)
        self.assertIn('stackgen-governance-inline:v2:', updates[0]['body'])

    def test_historical_duplicates_do_not_produce_another_comment(self):
        item = finding()
        legacy = review.finding_body('<!-- stackgen-governance-inline:legacy -->', [item])
        comments, updates = review.plan_comments({(PATH, 2): [item]}, [existing(legacy), existing(legacy, id=456)])
        self.assertEqual(comments, [])
        self.assertEqual([update['id'] for update in updates], [123])

    def test_user_comments_other_bots_and_replies_are_not_modified(self):
        item = finding()
        legacy = review.finding_body('<!-- stackgen-governance-inline:legacy -->', [item])
        for changes in [{'user': {'login': 'human'}}, {'user': {'login': 'other-bot[bot]'}}, {'in_reply_to_id': 456}]:
            with self.subTest(changes=changes):
                comments, updates = review.plan_comments({(PATH, 2): [item]}, [existing(legacy, **changes)])
                self.assertEqual(len(comments), 1)
                self.assertEqual(updates, [])

    def test_rule_resource_attribute_and_file_are_distinct_findings(self):
        original = finding()
        marker = review.finding_marker(PATH, [original])
        for field, value in [('ruleId', 'OTHER_RULE'), ('resource', 'aws_s3_bucket.other'), ('attribute', 'other_attribute')]:
            item = copy.deepcopy(original)
            target = item if field == 'ruleId' else item['properties']
            target[field] = value
            self.assertNotEqual(review.finding_marker(PATH, [item]), marker)
        self.assertNotEqual(review.finding_marker('terraform/other.tf', [original]), marker)

    def test_order_and_duplicate_locations_do_not_create_new_comments(self):
        items = [finding(), finding('other_attribute', 'Second finding')]
        self.assertEqual(review.finding_marker(PATH, items), review.finding_marker(PATH, items[::-1]))
        self.assertEqual(review.finding_body('marker', items), review.finding_body('marker', items[::-1]))
        comments, updates = review.plan_comments({(PATH, 2): items, (PATH, 20): items[::-1]}, [])
        self.assertEqual(len(comments), 1)
        self.assertEqual(updates, [])


class PublishingTests(unittest.TestCase):
    @patch.object(review, 'api')
    def test_main_patches_legacy_finding_without_posting_another_review(self, api):
        item = finding()
        legacy = review.finding_body('<!-- stackgen-governance-inline:legacy -->', [item])
        source = 'resource "aws_s3_bucket" "app" {\n  acl = "public-read"\n}\n'
        diff = '@@ -1,3 +1,3 @@\n resource "aws_s3_bucket" "app" {\n-  acl = "private"\n+  acl = "public-read"\n }'
        api.side_effect = [
            {'head': {'sha': 'current'}}, [[{'filename': PATH, 'patch': diff, 'status': 'modified'}]],
            {'content': base64.b64encode(source.encode()).decode()}, [[existing(legacy)]],
            {'head': {'sha': 'current'}}, {},
        ]
        with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as directory:
            root = Path(directory)
            (root / 'scan.sarif').write_text(json.dumps({'runs': [{'results': [item]}]}))
            (root / 'event.json').write_text(json.dumps({'pull_request': {'number': 3}}))
            env = {'GITHUB_WORKSPACE': str(root), 'OUTPUT_FILE': 'scan.json',
                   'GITHUB_EVENT_PATH': str(root / 'event.json'), 'PR_HEAD_SHA': 'current',
                   'GITHUB_REPOSITORY': 'example/demo'}
            with patch.dict(os.environ, env), patch.object(review.sys, 'argv', ['review.py']):
                review.main()
        self.assertEqual(api.call_args.args[0], 'repos/example/demo/pulls/comments/123')
        self.assertEqual(api.call_args.kwargs['method'], 'PATCH')
        self.assertFalse(any(call.args[0].endswith('/reviews') for call in api.call_args_list))


if __name__ == '__main__':
    unittest.main()
