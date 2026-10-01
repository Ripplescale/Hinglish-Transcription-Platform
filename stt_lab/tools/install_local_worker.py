"""Install a versioned worker source copy and existing local model configurations.

No models, summaries, system Python or training datasets are downloaded.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import read_json, write_json, file_digest, digest


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


def install(root: Path, study: Path, python: Path, *, activate=True):
    root=root.resolve();study=study.resolve();python=python.resolve(strict=True)
    worker=install_worker_source(root)
    configs={m:read_json(study/'configs'/f'{m}.json') for m in ('apex','trelis')}
    for config in configs.values():
        if not Path(config['artifact_path']).exists():raise ValueError('Local model unavailable')
    value={'version':1,'python_executable':str(python),**worker,'models':configs,
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
            'vault':str(vault),'model_downloads':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root',type=Path,required=True);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--python',type=Path,required=True)
    p.add_argument('--no-activate',action='store_true',help='Prepare a candidate without replacing runtime.json')
    a=p.parse_args();print(json.dumps(install(a.data_root,a.study,a.python,activate=not a.no_activate)))
