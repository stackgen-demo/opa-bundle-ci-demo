"""Convert scanner JSON using stable Terraform/Helm identities and optional locations."""
import hashlib


PROFILES = {
    'terraform': ('stackgen360guard', 'stackgenResource/v1'),
    'helm': ('stackgen360guard-helm', 'stackgenHelmResource/v1'),
}


def convert(report, *, kind, hints=None, locations=None):
    tool, fingerprint = PROFILES[kind]
    hints, locations = hints or {}, locations or {}
    rules, indices, results = [], {}, []
    for violation in report['violations']:
        rule_id = violation.get('rule_id') or violation['policy_id']
        severity = violation['severity'].lower()
        hint = hints.get(rule_id, violation['message'])
        if rule_id not in indices:
            indices[rule_id] = len(rules)
            rule = {'id': rule_id, 'shortDescription': {'text': rule_id},
                    'properties': {'security-severity': {'critical': '9.0', 'high': '8.0', 'medium': '5.0', 'low': '2.0'}.get(severity, '0.0')}}
            if kind == 'terraform':
                rule['help'] = {'text': hint}
            rules.append(rule)
        address = violation.get('address') or violation.get('resource_id') or 'unknown resource'
        attribute = violation.get('attribute') or 'configuration'
        text = f"{address} ({attribute}): {violation['message']}"
        if hint != violation['message']:
            text += f'\nHint: {hint}'
        result = {
            'ruleId': rule_id, 'ruleIndex': indices[rule_id],
            'level': {'critical': 'error', 'high': 'error', 'medium': 'warning'}.get(severity, 'note'),
            'message': {'text': text},
            'partialFingerprints': {fingerprint: hashlib.sha256(f'{rule_id}:{address}:{attribute}'.encode()).hexdigest()},
        }
        if kind == 'terraform':
            # review.py consumes these properties to locate changed Terraform lines.
            result['properties'] = {'resource': address, 'attribute': attribute, 'policyId': violation['policy_id']}
        if address in locations:
            result['locations'] = [{'physicalLocation': locations[address]}]
        results.append(result)
    run = {'tool': {'driver': {'name': tool, 'rules': rules}}, 'results': results,
           'properties': {'bundleRevision': report['bundle']['revision']}}
    if kind == 'terraform':
        incomplete = report['summary'].get('resources_with_unknown_attributes', 0) > 0
        run['invocations'] = [{
            'executionSuccessful': not incomplete,
            'toolExecutionNotifications': ([{
                'level': 'error', 'message': {'text': 'Unresolved configuration values prevent a complete governance evaluation; check the JSON report.'},
            }] if incomplete else []),
        }]
    return {'$schema': 'https://json.schemastore.org/sarif-2.1.0.json', 'version': '2.1.0', 'runs': [run]}
