import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / 'helm/action'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ACTION / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_bundle = load('validate_bundle')
render = load('render')
report = load('report')


class BundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP'))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'bundle.tar.gz'

    def bundle(self, policies):
        content = json.dumps({'stackgen': {'resource_config': {'metadata': {'policies': policies}}}}).encode()
        with tarfile.open(self.path, 'w:gz') as archive:
            member = tarfile.TarInfo('data.json')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    def test_accepts_deployment_policies(self):
        self.bundle([{'resourceType': 'k8s/apps/v1/Deployment'}])
        validate_bundle.validate(self.path, 'k8s/apps/v1/Deployment')

    def test_empty_bundle_is_not_a_passing_scan(self):
        self.bundle([])
        with self.assertRaisesRegex(ValueError, 'no Kubernetes policies'):
            validate_bundle.validate(self.path, 'k8s/apps/v1/Deployment')

    def test_unrelated_policies_do_not_cover_a_workload(self):
        self.bundle([{'resourceType': 'aws_s3_bucket'}, {'resourceType': 'k8s/v1/Pod'}])
        with self.assertRaisesRegex(ValueError, 'No exported policies for: k8s/apps/v1/Deployment'):
            validate_bundle.validate(self.path, 'k8s/apps/v1/Deployment')

    def test_every_required_workload_type_needs_policies(self):
        self.bundle([{'resourceType': 'k8s/apps/v1/Deployment'}])
        with self.assertRaisesRegex(ValueError, 'k8s/v1/Pod'):
            validate_bundle.validate(self.path, 'k8s/apps/v1/Deployment,k8s/v1/Pod')

    def test_cannot_disable_coverage_guard_with_empty_input(self):
        self.bundle([{'resourceType': 'k8s/apps/v1/Deployment'}])
        with self.assertRaisesRegex(ValueError, 'At least one'):
            validate_bundle.validate(self.path, '')

    def test_no_charts_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'No Helm charts'):
            render.render(Path(self.temp.name), Path(self.temp.name) / 'output')


class ReportTests(unittest.TestCase):
    def fixture(self):
        return {'bundle': {'revision': 'sha256:test'},
                'summary': {'policies_evaluated': 2, 'resources_scanned': 4, 'violations': 1, 'passed': False},
                'violations': [{'policy_id': 'policy', 'rule_id': 'RULE', 'severity': 'high',
                                'address': 'k8s/apps/v1/Deployment/demo/web',
                                'attribute': 'spec.template.spec.hostNetwork',
                                'message': '<script> | @everyone `unsafe`'}]}

    def test_sarif_does_not_invent_helm_source_locations(self):
        result = report.convert(self.fixture())
        self.assertEqual(result['runs'][0]['results'][0]['level'], 'error')
        self.assertNotIn('locations', result['runs'][0]['results'][0])

    def test_helm_comments_do_not_overwrite_terraform_comments(self):
        body = report.comment_body(self.fixture(), 'failure')
        self.assertTrue(body.startswith('<!-- stackgen-helm-governance-scan -->'))
        self.assertIn('Helm governance: FAIL', body)
        self.assertNotIn('<script>', body)
        self.assertNotIn('@everyone', body)
        self.assertIn('&#124;', body)

    def test_missing_report_is_not_a_pass(self):
        self.assertIn('Helm governance: ERROR', report.comment_body(None, 'failure'))


@unittest.skipUnless(shutil.which('helm') and os.environ.get('BUNDLE_FILE'), 'Set BUNDLE_FILE for real scanner checks')
class LiveBundleChartTests(unittest.TestCase):
    def test_rendered_chart_defaults_pass_and_unsafe_overrides_fail(self):
        binary = os.environ.get('SCANNER_BINARY', 'stackgen360guard')
        bundle = str(Path(os.environ['BUNDLE_FILE']).resolve())
        for chart in ('web', 'worker'):
            for values, violations in [([], 0), (['hostNetwork=true'], 1),
                                       (['securityContext.privileged=true'], 1),
                                       (['hostNetwork=true', 'securityContext.privileged=true'], 2)]:
                with self.subTest(chart=chart, values=values):
                    with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as directory:
                        temp = Path(directory)
                        command = ['helm', 'template', chart, str(ROOT / 'helm' / chart), '--namespace', 'governance-demo']
                        for value in values:
                            command += ['--set', value]
                        manifest = subprocess.check_output(command)
                        source = temp / 'rendered.yaml'
                        source.write_bytes(manifest)
                        output = temp / 'scan.json'
                        run = subprocess.run([binary, 'scan', '--input', str(source), '--input-type', 'k8s',
                                              '--bundle', bundle, '--fail-on', 'high', '--output', str(output)],
                                             capture_output=True, text=True, timeout=60)
                        self.assertEqual(run.returncode, 1 if violations else 0, run.stderr)
                        result = json.loads(output.read_text())
                        self.assertEqual(len(result['violations']), violations)
                        self.assertEqual(result['summary']['resources_scanned'], 2)
                        self.assertEqual(result['summary']['passed'], not violations)
                        for violation in result['violations']:
                            self.assertEqual(violation['resource_type'], 'k8s/apps/v1/Deployment')


if __name__ == '__main__':
    unittest.main()
