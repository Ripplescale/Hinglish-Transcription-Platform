"""Install a versioned worker source copy and existing local ASR configurations.

Setup can explicitly download the pinned Silero asset; inference stays offline.
No ASR models, summaries, Python packages or training datasets are downloaded.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import read_json, write_json, file_digest, digest

SILERO_VERSION='6.2.3'
SILERO_SHA256='1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'
SILERO_WHEEL_SHA256='7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8'
SILERO_WHEEL_URL=('https://files.pythonhosted.org/packages/84/ef/'
                  '9099037ed6f180ea33220178df4107112c0ce2bf5fb4d6f6ab19db2844ed/'
                  'silero_vad-6.2.3-py3-none-any.whl')
SILERO_MODEL_MEMBER='silero_vad/data/silero_vad.onnx'
SILERO_MAX_WHEEL_BYTES=16*1024*1024
SILERO_MAX_MODEL_BYTES=4*1024*1024


def _download_silero_model():
    """Read only a pinned model member, never execute or install wheel contents."""
    request=urllib.request.Request(SILERO_WHEEL_URL,
                                  headers={'User-Agent':'local-stt-silero-setup/'+SILERO_VERSION})
    with urllib.request.urlopen(request,timeout=30) as response:
        final=urllib.parse.urlparse(response.geturl())
        if final.scheme!='https' or final.hostname!='files.pythonhosted.org':
            raise ValueError('Silero download redirected outside the pinned HTTPS host')
        wheel=response.read(SILERO_MAX_WHEEL_BYTES+1)
    if len(wheel)>SILERO_MAX_WHEEL_BYTES:
        raise ValueError('Silero wheel exceeds the setup size limit')
    if hashlib.sha256(wheel).hexdigest()!=SILERO_WHEEL_SHA256:
        raise ValueError('Silero wheel does not match the pinned SHA-256')
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        member=archive.getinfo(SILERO_MODEL_MEMBER)
        if member.file_size>SILERO_MAX_MODEL_BYTES:
            raise ValueError('Silero model exceeds the setup size limit')
        model=archive.read(member)
    if hashlib.sha256(model).hexdigest()!=SILERO_SHA256:
        raise ValueError('Silero model does not match the pinned SHA-256')
    return model


def install_speech_gate(root: Path, source: Path | None=None, *, download=False):
    """Provision a verified VAD asset without relying on the Rust capture cache.

    Default setup is offline: reuse the hash-addressed model or copy an explicit
    source. An explicit download verifies both the official wheel and its ONNX
    member. Missing assets keep the worker's unavailable-gate speech fallback.
    """
    target=root/'lab/models/silero'/SILERO_SHA256/'silero_vad.onnx'
    if target.exists() and file_digest(target)!=SILERO_SHA256:
        raise ValueError('Installed Silero model differs from the pinned SHA-256')
    model_bytes=None
    if source is not None:
        source=source.resolve(strict=True)
        if file_digest(source)!=SILERO_SHA256:
            raise ValueError('Silero model does not match the pinned SHA-256')
    elif not target.exists() and download:
        model_bytes=_download_silero_model()
    if not target.exists() and (source is not None or model_bytes is not None):
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.silero-',dir=target.parent) as temporary:
            staged=Path(temporary)/target.name
            if source is not None:
                shutil.copyfile(source,staged)
            else:
                staged.write_bytes(model_bytes)
            if file_digest(staged)!=SILERO_SHA256:
                raise ValueError('Copied Silero model failed verification')
            try:
                os.link(staged,target)
            except FileExistsError:
                # A concurrent verified setup may have published first.
                if file_digest(target)!=SILERO_SHA256:
                    raise ValueError('Installed Silero model differs from the pinned SHA-256')
    if target.exists() and file_digest(target)!=SILERO_SHA256:
        raise ValueError('Installed Silero model differs from the pinned SHA-256')
    return {'model_path':str(target.resolve()),'model_sha256':SILERO_SHA256,
            'model_version':SILERO_VERSION,'model_source_url':SILERO_WHEEL_URL,
            'model_package_sha256':SILERO_WHEEL_SHA256,
            'threshold':0.15,'boost_peak':0.25,'max_gain':1000}


def install_worker_source(root: Path):
    """Install an immutable, verified worker copy without activating it."""
    root=root.resolve()
    if any(p.lower().startswith('onedrive') for p in root.parts):
        raise ValueError('Keep the runtime outside OneDrive')
    source=LAB/'sttbench'
    files={str(p.relative_to(source)):file_digest(p) for p in source.rglob('*.py')}
    registry_sha=file_digest(LAB/'models.json')
    wrapper="import runpy\nrunpy.run_module('sttbench.local_worker', run_name='__main__')\n"
    wrapper_sha=hashlib.sha256(wrapper.encode('utf-8')).hexdigest()
    identity=digest({'files':files,'registry_sha256':registry_sha,'worker_sha256':wrapper_sha})[:16]
    target=root/'runtime'/identity
    if not target.exists():
        target.mkdir(parents=True)
        for relative in files:
            destination=target/'sttbench'/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/relative,destination)
        shutil.copyfile(LAB/'models.json',target/'models.json')
        (target/'worker.py').write_bytes(wrapper.encode('utf-8'))
        write_json(target/'source-manifest.json',{'files':files,'registry_sha256':registry_sha,
                   'worker_sha256':wrapper_sha,'installed_at':datetime.now(timezone.utc).isoformat()})
    for relative,sha in files.items():
        if file_digest(target/'sttbench'/relative)!=sha:
            raise ValueError('Versioned worker copy differs from source')
    if file_digest(target/'models.json')!=registry_sha:
        raise ValueError('Versioned model registry differs from source')
    if file_digest(target/'worker.py')!=wrapper_sha:
        raise ValueError('Versioned worker entry point differs from source')
    return {'worker_script':str(target/'worker.py'),'registry_path':str(target/'models.json'),
            'worker_source_id':identity}


def _write_new_json(path: Path, value):
    """Atomic no-clobber snapshot publication, including on Windows."""
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor, name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as handle:
            json.dump(value,handle,ensure_ascii=False,indent=2,allow_nan=False)
            handle.write('\n');handle.flush();os.fsync(handle.fileno())
        # A hard link publishes the complete file atomically and refuses an
        # existing destination. Never fall back to replacing a saved snapshot.
        os.link(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def _worker_hash(runtime):
    path=Path(runtime['worker_script']).resolve(strict=True).parent/'sttbench/local_worker.py'
    return file_digest(path)


def _job_model(runtime, request):
    model=request['profile'].split('-',1)[0]
    if model not in ('apex','trelis'):
        raise ValueError(f'Unsupported historical job profile: {request["profile"]}')
    registry=read_json(Path(runtime['registry_path']))
    spec=copy.deepcopy(next(item for item in registry['models'] if item['id']==model))
    if model=='trelis':
        spec['decoding'].update(language='en' if request['language_mode']=='english' else 'hi',
                                mixed_code=request['language_mode']!='english')
    return spec,runtime['models'][model]


def _validate_runtime_files(runtime):
    for field in ('python_executable','worker_script','registry_path'):
        if not Path(runtime[field]).is_file():
            raise ValueError(f'Runtime {field} is missing: {runtime[field]}')
    _worker_hash(runtime)


def _legacy_snapshot_plan(root: Path, old_runtime: dict):
    """Plan every legacy snapshot before writing any or replacing runtime.json."""
    candidates=[('current',old_runtime)]
    for previous in sorted((root/'runtime').glob('previous-*.json')):
        try:
            candidates.append((str(previous),read_json(previous)))
        except (OSError,ValueError):
            # Corrupt unrelated history is not admitted as a matching runtime.
            continue
    plan=[]
    retained=0
    for request_path in sorted((root/'jobs').glob('*/request.json')):
        snapshot=request_path.parent/'runtime-snapshot.json'
        if snapshot.exists():
            retained+=1
            continue
        request=read_json(request_path)
        status_path=request_path.parent/'status.json'
        status=read_json(status_path) if status_path.exists() else {}
        identity=status.get('identity')
        if identity:
            if not isinstance(identity,dict) or status.get('identity_sha256')!=digest(identity):
                raise ValueError(f'Historical job {request_path.parent.name} has an invalid identity digest')
            if identity.get('request_sha256')!=file_digest(request_path):
                raise ValueError(f'Historical job {request_path.parent.name} request changed')
            session=Path(request['session_dir'])/'session.json'
            if identity.get('session_sha256')!=file_digest(session):
                raise ValueError(f'Historical job {request_path.parent.name} recording identity changed')
            matches=[]
            for label,runtime in candidates:
                try:
                    spec,model_runtime=_job_model(runtime,request)
                    if (spec==identity.get('spec') and model_runtime==identity.get('runtime')
                            and _worker_hash(runtime)==identity.get('worker_sha256')):
                        if 'speech_gate' in identity:
                            gate_worker=Path(runtime['worker_script']).parent/'sttbench/speech_gate.py'
                            if (runtime.get('speech_gate')!=identity['speech_gate']
                                    or file_digest(gate_worker)!=identity.get('speech_gate_worker_sha256')):
                                continue
                        _validate_runtime_files(runtime)
                        matches.append((label,runtime))
                except (OSError,ValueError,KeyError,TypeError,StopIteration):
                    continue
            if not matches:
                raise ValueError(f'No verified historical runtime matches job {request_path.parent.name}; activation stopped')
            # Several whole-app configs may share the exact job-specific model,
            # registry and worker identity. Prefer current, then sorted history.
            label,selected=matches[0]
        else:
            if status.get('segments') or status.get('cursors') or status.get('processed_audio_seconds',0):
                raise ValueError(f'Historical job {request_path.parent.name} has output without a verifiable identity')
            _validate_runtime_files(old_runtime)
            _job_model(old_runtime,request)
            label,selected='current-unstarted',old_runtime
        plan.append({'snapshot':snapshot,'runtime':selected,'matched_runtime':label,
                     'request_path':request_path,'request_sha256':file_digest(request_path),
                     'status_path':status_path,'status_sha256':file_digest(status_path) if status_path.exists() else None})
    return plan,retained


def activate_runtime(root: Path, candidate_path: Path):
    """Preserve historical job runtimes, then atomically activate a candidate.

    The caller must stop the app normally and ensure model workers are idle.
    This function never modifies requests, checkpoints, existing snapshots,
    recordings, or transcripts.
    """
    root=root.resolve();candidate_path=candidate_path.resolve(strict=True)
    candidate=read_json(candidate_path)
    _validate_runtime_files(candidate)
    active=root/'runtime.json'
    previous_sha=file_digest(active) if active.exists() else None
    previous=read_json(active) if active.exists() else None
    if previous is None and any((root/'jobs').glob('*/request.json')):
        raise ValueError('Cannot preserve existing jobs without the previous active runtime')
    plan,retained=_legacy_snapshot_plan(root,previous) if previous else ([],0)
    # Recheck all evidence before publishing any snapshot. The app must remain
    # idle; this guard catches accidental worker activity or concurrent edits.
    for entry in plan:
        status=entry['status_path']
        if (file_digest(entry['request_path'])!=entry['request_sha256']
                or (file_digest(status) if status.exists() else None)!=entry['status_sha256']):
            raise ValueError('A legacy job changed during migration; activation stopped')
    if (file_digest(active) if active.exists() else None)!=previous_sha:
        raise ValueError('The active runtime changed during migration; activation stopped')
    if previous is not None:
        backup=root/'runtime'/f'previous-{previous_sha[:16]}.json'
        if not backup.exists():
            _write_new_json(backup,previous)
        elif read_json(backup)!=previous:
            raise ValueError('Runtime backup collision; activation stopped')
    for entry in plan:
        _write_new_json(entry['snapshot'],entry['runtime'])
    if (file_digest(active) if active.exists() else None)!=previous_sha:
        raise ValueError('The active runtime changed before activation; snapshots retained safely')
    write_json(active,candidate)
    return {'runtime':str(active),'activated':True,'snapshots_created':len(plan),'snapshots_retained':retained}


def update_existing(root: Path, *, activate=False, speech_gate_model: Path | None=None,
                    download_speech_gate=False):
    """Stage the current worker and gate without re-exporting or changing ASR models."""
    root=root.resolve(strict=True)
    active=root/'runtime.json'
    original_sha=file_digest(active)
    value=copy.deepcopy(read_json(active))
    value.update(install_worker_source(root))
    gate_missing=not (root/'lab/models/silero'/SILERO_SHA256/'silero_vad.onnx').is_file()
    value['speech_gate']=install_speech_gate(root,speech_gate_model,download=download_speech_gate)
    _validate_runtime_files(value)
    candidate=root/'runtime'/f'candidate-worker-{digest(value)[:16]}.json'
    if not candidate.exists():_write_new_json(candidate,value)
    elif read_json(candidate)!=value:raise ValueError('Candidate runtime collision')
    if file_digest(active)!=original_sha:
        raise ValueError('Active runtime changed during worker preparation; no activation performed')
    outcome=activate_runtime(root,candidate) if activate else {'runtime':str(candidate),'activated':False}
    return {**outcome,'candidate_runtime':str(candidate),'source_id':value['worker_source_id'],
            'speech_gate_available':Path(value['speech_gate']['model_path']).is_file(),
            'original_runtime_sha256':original_sha,'asr_model_downloads':False,
            'model_downloads':bool(download_speech_gate and speech_gate_model is None and gate_missing)}


def install(root: Path, study: Path, python: Path, *, activate=True, speech_gate_model: Path | None=None,
            download_speech_gate=False):
    root=root.resolve();study=study.resolve();python=python.resolve(strict=True)
    worker=install_worker_source(root)
    configs={m:read_json(study/'configs'/f'{m}.json') for m in ('apex','trelis')}
    for config in configs.values():
        if not Path(config['artifact_path']).exists():raise ValueError('Local model unavailable')
    gate_missing=not (root/'lab/models/silero'/SILERO_SHA256/'silero_vad.onnx').is_file()
    value={'version':1,'python_executable':str(python),**worker,'models':configs,
           'speech_gate':install_speech_gate(root,speech_gate_model,download=download_speech_gate),
           'summaries_enabled':False,'network_inference':False}
    candidate=root/'runtime'/f'candidate-{digest(value)[:16]}.json'
    if not candidate.exists():_write_new_json(candidate,value)
    elif read_json(candidate)!=value:raise ValueError('Candidate runtime collision')
    outcome=activate_runtime(root,candidate) if activate else {'runtime':str(candidate),'activated':False}
    vault=root/'vaults/tapf'
    if activate and not vault.exists():
        write_json(vault/'revision-000000000001.json',{'version':1,'id':'tapf','name':'TAPF Vault','revision':1,
                   'entries':[],'relationships':[],'updated_at':datetime.now(timezone.utc).isoformat()})
    return {**outcome,'candidate_runtime':str(candidate),'source_id':worker['worker_source_id'],
            'vault':str(vault),'asr_model_downloads':False,
            'model_downloads':bool(download_speech_gate and speech_gate_model is None and gate_missing)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root',type=Path,required=True)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--study',type=Path)
    source.add_argument('--update-existing',action='store_true',help='Reuse the active ASR model/environment configuration')
    p.add_argument('--python',type=Path,help='Required with --study')
    p.add_argument('--no-activate',action='store_true',help='Prepare a candidate without replacing runtime.json')
    gate=p.add_mutually_exclusive_group()
    gate.add_argument('--speech-gate-model',type=Path,help='Verified local Silero 6.2.3 ONNX model for offline setup')
    gate.add_argument('--download-speech-gate',action='store_true',help='Download and verify the pinned Silero asset during setup only')
    a=p.parse_args()
    if a.update_existing:
        result=update_existing(a.data_root,activate=not a.no_activate,speech_gate_model=a.speech_gate_model,
                               download_speech_gate=a.download_speech_gate)
    else:
        if not a.python:p.error('--study requires --python')
        result=install(a.data_root,a.study,a.python,activate=not a.no_activate,speech_gate_model=a.speech_gate_model,
                       download_speech_gate=a.download_speech_gate)
    print(json.dumps(result))
