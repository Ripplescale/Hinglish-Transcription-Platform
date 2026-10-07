"""Prepare a verified OpenVINO candidate; activation is an explicit final step.

No network access, model inference, recording, or dependency installation occurs.
Use bootstrap_openvino.py to prepare the independent pinned environment first.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import digest, file_digest, read_json, write_json
from sttbench.runtime.assets import verify_model_assets, verify_openvino_assets
from install_local_worker import install_worker_source, install_speech_gate, activate_runtime, _write_new_json


PACKAGES={'openvino':'2026.4.0','optimum-intel':'2.2.0','optimum':'2.3.0',
          'transformers':'4.57.6','torch':'2.8.0+cpu','numpy':'2.2.6','tokenizers':'0.22.2'}
ENVIRONMENT_PACKAGES={**PACKAGES,'onnxruntime':'1.30.0'}
REVISION='eab1188fd2d0e91f2584229b32b3bfe1901c896c'


def _inspect_environment(root: Path, python: Path):
    expected=root/'lab/venvs/openvino-py312'
    if python.resolve(strict=True)!=(expected/'Scripts/python.exe').resolve(strict=True):
        raise ValueError('Use the independent data-root/lab/venvs/openvino-py312 Python')
    configuration=(expected/'pyvenv.cfg').read_text(encoding='utf-8').lower()
    if not re.search(r'include-system-site-packages\s*=\s*false',configuration):
        raise ValueError('OpenVINO environment must not include system site-packages')
    if (expected/'Lib/site-packages/base-readonly.pth').exists():
        raise ValueError('OpenVINO environment must not borrow the Whisper environment')
    code=('import sys,json,importlib.metadata as m; '
          'print(json.dumps({"python_version":".".join(map(str,sys.version_info[:3])), '
          '"prefix":sys.prefix,"paths":sys.path,"packages":{n:m.version(n) for n in '+repr(list(ENVIRONMENT_PACKAGES))+'}}))')
    process=subprocess.run([str(python),'-I','-B','-c',code],check=True,capture_output=True,text=True,
                           timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    info=json.loads(process.stdout.strip())
    if info['python_version']!='3.12.14' or Path(info['prefix']).resolve()!=expected.resolve():
        raise ValueError('OpenVINO Python identity does not match the independent 3.12.14 environment')
    if info['packages']!=ENVIRONMENT_PACKAGES:
        raise ValueError('OpenVINO environment package versions differ from the validated pins')
    for path in info['paths']:
        if 'site-packages' in path.lower() and not Path(path).resolve().is_relative_to(expected.resolve()):
            raise ValueError('External site-packages leaked into the OpenVINO environment')
    return info


def _conversion_receipt(spec: dict, source_verification: dict, export_manifest: Path):
    return {'schema_version':1,'model_id':spec['model_id'],'source_repo_id':spec['repo_id'],
            'source_revision':spec['source_revision'],
            'source_manifest_sha256':source_verification['manifest_sha256'],
            'export_manifest_sha256':file_digest(export_manifest),
            'converter':{'name':'optimum.exporters.openvino.main_export','version':'2.2.0',
                'packages':PACKAGES,'task':'automatic-speech-recognition-with-past','dtype':'fp16',
                'stateful':True,'convert_tokenizer':False,'load_in_8bit':False}}


def _copy_export(root: Path, export_source: Path, spec: dict):
    verification=verify_model_assets(spec)
    if not verification['ok'] or verification['integrity']!='manifest_files_verified':
        raise ValueError('Source model manifest must be fully verified: '+ '; '.join(verification['issues']))
    manifest_path=export_source/'export-complete.json'
    manifest=read_json(manifest_path)
    if Path(manifest['source']).resolve()!=Path(spec['artifact_path']).resolve():
        raise ValueError('Benchmark export source does not match the pinned original model directory')
    receipt=_conversion_receipt(spec,verification,manifest_path)
    target=root/'lab/converted/openvino/trelis'/spec['source_revision']/'fp16'
    if target.exists():
        copied=verify_openvino_assets(spec,target,verification)
        if not copied['ok'] or copied.get('metadata')!=receipt:
            raise ValueError('Existing durable export differs from this verified candidate: '+ '; '.join(copied['issues']))
        return target,copied
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prepare-openvino-',dir=target.parent) as temporary:
        staging=Path(temporary)/'fp16'
        staging.mkdir()
        files=manifest.get('files')
        if not isinstance(files,dict) or not files:
            raise ValueError('Export manifest must contain its file sizes and hashes')
        for name,entry in files.items():
            source=(export_source/name).resolve()
            destination=(staging/name).resolve()
            if not source.is_relative_to(export_source) or not destination.is_relative_to(staging):
                raise ValueError('Export manifest filename leaves its model directory')
            if (not source.is_file() or type(entry.get('size')) is not int
                    or source.stat().st_size!=entry['size'] or file_digest(source)!=entry.get('sha256')):
                raise ValueError(f'Export source size or SHA-256 mismatch: {name}')
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,destination)
        shutil.copyfile(manifest_path,staging/'export-complete.json')
        write_json(staging/'conversion-provenance.json',receipt)
        copied=verify_openvino_assets(spec,staging,verification)
        if not copied['ok']:
            raise ValueError('Copied OpenVINO export failed verification: '+ '; '.join(copied['issues']))
        # Existing published IR is immutable. On Windows rename also refuses a
        # concurrently created destination instead of replacing that directory.
        if target.exists():
            raise ValueError('Another process published the durable export; retry verification')
        staging.rename(target)
    copied=verify_openvino_assets(spec,target,verification)
    if not copied['ok']:
        raise ValueError('Published OpenVINO export failed verification: '+ '; '.join(copied['issues']))
    return target,copied


def prepare(root: Path, export_source: Path, python: Path | None=None, *, activate=False, speech_gate_model: Path | None=None):
    root=root.resolve(strict=True);export_source=export_source.resolve(strict=True)
    if any(part.lower().startswith('onedrive') for part in root.parts):
        raise ValueError('Keep runtime and models outside OneDrive')
    active=root/'runtime.json'
    active_sha=file_digest(active)
    current=read_json(active)
    python=(python or root/'lab/venvs/openvino-py312/Scripts/python.exe').resolve(strict=True)
    environment=_inspect_environment(root,python)
    registry=read_json(LAB/'models.json')
    spec=copy.deepcopy(next(model for model in registry['models'] if model['id']=='trelis'))
    if spec['source_revision']!=REVISION:
        raise ValueError('This candidate must use the acceleration study\'s qualified Trelis revision')
    spec['artifact_path']=current['models']['trelis']['artifact_path']
    target,verification=_copy_export(root,export_source,spec)
    worker=install_worker_source(root)
    cache=root/'lab/cache/openvino/trelis'/REVISION
    cache.mkdir(parents=True,exist_ok=True)
    candidate=copy.deepcopy(current)
    candidate.update(worker)
    candidate['speech_gate']=install_speech_gate(root,speech_gate_model)
    candidate['python_executable']=str(python)
    candidate['models']['trelis'].update(backend='openvino',export_path=str(target),cache_dir=str(cache),
                                        device='GPU',dtype='float16',threads=4,num_beams=1,
                                        max_new_tokens=440,timestamps=False)
    if candidate['models']['apex']!=current['models']['apex']:
        raise ValueError('Candidate unexpectedly changes the Apex configuration')
    path=root/'runtime'/f'candidate-openvino-{digest(candidate)[:16]}.json'
    if path.exists():
        if read_json(path)!=candidate:raise ValueError('Candidate runtime collision')
    else:
        _write_new_json(path,candidate)
    if file_digest(active)!=active_sha:
        raise ValueError('Active runtime changed while preparing the candidate; no activation performed')
    report={'candidate_runtime':str(path),'activated':False,'export_path':str(target),
            'worker_source_id':worker['worker_source_id'],'python_executable':str(python),
            'source_revision':REVISION,'source_manifest_sha256':verification['metadata']['source_manifest_sha256'],
            'export_manifest_sha256':verification['export_manifest_sha256'],
            'packages':environment['packages'],'original_runtime_sha256':active_sha}
    if activate:report.update(activate_runtime(root,path))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--export-source',type=Path,help='Existing benchmark export directory; remains unchanged')
    source.add_argument('--candidate',type=Path,help='Previously prepared candidate to activate')
    parser.add_argument('--python',type=Path,help='Independent openvino-py312 executable under the data root')
    parser.add_argument('--activate',action='store_true',help='Only after normal app shutdown: preserve legacy job runtimes and activate')
    parser.add_argument('--speech-gate-model',type=Path,help='Verified local Silero ONNX model; otherwise use the pinned Rust cache')
    arguments=parser.parse_args()
    if arguments.candidate:
        if not arguments.activate:parser.error('--candidate requires explicit --activate')
        result=activate_runtime(arguments.data_root,arguments.candidate)
    else:
        result=prepare(arguments.data_root,arguments.export_source,arguments.python,activate=arguments.activate,
                       speech_gate_model=arguments.speech_gate_model)
    print(json.dumps(result,ensure_ascii=False))
