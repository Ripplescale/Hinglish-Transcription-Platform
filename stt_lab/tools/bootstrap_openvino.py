"""Prepare an independent pinned OpenVINO environment; never activate an app runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from windows_https import verified_windows_https

LOCK = Path(__file__).with_name('openvino-windows-py312.lock.txt')


def sha256_file(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def run(command, environment, *, capture=False):
    return subprocess.run(command, env=environment, check=True, text=True,
                          capture_output=capture)


def prepare(root: Path, base_python: Path, pip_cache: Path | None, direct: bool):
    root = root.resolve()
    base_python = base_python.resolve(strict=True)
    if any(part.lower().startswith('onedrive') for part in root.parts):
        raise ValueError('Keep models and environments outside OneDrive')
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                       HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                       NNCF_TELEMETRY_DISABLED='1', OPENVINO_TELEMETRY_DISABLED='1')
    if direct:
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            environment.pop(key, None)
    environment['PIP_CACHE_DIR'] = str(pip_cache or root / 'lab/cache/pip')
    folder = root / 'lab/venvs/openvino-py312'
    python = folder / 'Scripts/python.exe'
    version = run([str(base_python), '-c', 'import sys; print(".".join(map(str,sys.version_info[:3])))'],
                  environment, capture=True).stdout.strip()
    if version != '3.12.14':
        raise ValueError(f'This environment is pinned to Python 3.12.14, found {version}')
    if not python.is_file():
        run([str(base_python), '-m', 'venv', str(folder)], environment)
    if (folder / 'Lib/site-packages/base-readonly.pth').exists():
        raise ValueError('The production environment must not borrow another environment')
    wheelhouse = root / 'lab/wheels/openvino-py312'
    wheelhouse.mkdir(parents=True, exist_ok=True)
    requirements = [line.strip() for line in LOCK.read_text().splitlines()
                    if line.strip() and not line.startswith('#')]
    expected = dict(line.split('==', 1) for line in requirements)
    installed = json.loads(run([str(python), '-m', 'pip', 'list', '--format=json'],
                              environment, capture=True).stdout)
    versions = {row['name'].lower().replace('_', '-'): row['version'] for row in installed}
    if any(versions.get(name.lower().replace('_', '-')) != version for name, version in expected.items()):
        # Download the two CPU Torch wheels exclusively from PyTorch. All other
        # exact-version wheels come from PyPI, then installation is offline.
        torch = [value for value in requirements if value.startswith(('torch==', 'torchvision=='))]
        rest = [value for value in requirements if value not in torch]
        with verified_windows_https():
            network = environment.copy()
            for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'):
                if os.environ.get(key):
                    network[key] = os.environ[key]
            if network.get('SSL_CERT_FILE'):
                network['PIP_CERT'] = network['SSL_CERT_FILE']
            for index, packages in (('https://download.pytorch.org/whl/cpu', torch),
                                    ('https://pypi.org/simple', rest)):
                run([str(python), '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
                     '--index-url', index, '--dest', str(wheelhouse), *packages], network)
        run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
             '--find-links', str(wheelhouse), '-r', str(LOCK)], environment)
    run([str(python), '-m', 'pip', 'check'], environment)
    probe = run([str(python), '-c',
                 'import sys,json,importlib.metadata as m; import torch,transformers,numpy,scipy,openvino; '
                 'from optimum.intel.openvino import OVModelForSpeechSeq2Seq; '
                 'print(json.dumps({"prefix":sys.prefix,"base_prefix":sys.base_prefix,"paths":sys.path,'
                 '"gpu_devices":openvino.Core().available_devices,'
                 '"packages":{n:m.version(n) for n in ["torch","transformers","numpy","scipy","openvino","optimum-intel","optimum"]}}))'],
                environment, capture=True)
    report = json.loads(probe.stdout.strip().splitlines()[-1])
    for path in report['paths']:
        if 'site-packages' in path.lower() and not Path(path).resolve().is_relative_to(folder):
            raise ValueError('An external site-packages directory leaked into the independent environment')
    if not any(device.startswith('GPU') for device in report['gpu_devices']):
        raise ValueError('No OpenVINO GPU device is available; the app runtime was not changed')
    report.update(python_executable=str(python), activated=False,
                  lock_sha256=hashlib.sha256(LOCK.read_bytes()).hexdigest(),
                  wheel_sha256={file.name: sha256_file(file)
                                for file in wheelhouse.glob('*.whl')})
    destination = folder / 'setup-report.json'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'python_executable': str(python), 'report': str(destination),
                      'gpu_devices': report['gpu_devices'], 'activated': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--base-python', type=Path, required=True)
    parser.add_argument('--pip-cache', type=Path)
    parser.add_argument('--direct', action='store_true', help='Use direct HTTPS only in dependency-download subprocesses')
    arguments = parser.parse_args()
    prepare(arguments.data_root, arguments.base_python, arguments.pip_cache, arguments.direct)
