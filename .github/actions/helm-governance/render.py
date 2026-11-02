#!/usr/bin/env python3
"""Lint and render each immediate child chart; never install into a cluster."""
from pathlib import Path
import subprocess
import sys


def render(source, output):
    charts = sorted(path.parent for path in source.glob('*/Chart.yaml'))
    if not charts:
        raise ValueError(f'No Helm charts found in {source}')
    if output.exists():
        raise ValueError('Rendered output already exists; refusing stale manifests')
    for chart in charts:
        if not chart.resolve().is_relative_to(source.resolve()):
            raise ValueError('Chart path escapes the Helm directory')
        subprocess.run(['helm', 'lint', '--strict', str(chart)], check=True)
        subprocess.run(['helm', 'template', chart.name, str(chart), '--namespace', 'governance-demo',
                        '--include-crds', '--output-dir', str(output / chart.name)], check=True)
    if not any(output.rglob('*.yaml')):
        raise ValueError('Helm produced no Kubernetes manifests')


if __name__ == '__main__':
    render(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
