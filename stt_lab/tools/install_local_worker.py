"""Install a versioned worker source copy and existing local model configurations.

No models, summaries, system Python or training datasets are downloaded.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import read_json, write_json, file_digest, digest


def install(root: Path, study: Path, python: Path):
    root=root.resolve();study=study.resolve();python=python.resolve(strict=True)
    if any(p.lower().startswith('onedrive') for p in root.parts):
        raise ValueError('Keep the runtime outside OneDrive')
    source=LAB/'sttbench'
    files={str(p.relative_to(source)):file_digest(p) for p in source.rglob('*.py')}
    registry_sha=file_digest(LAB/'models.json')
    identity=digest({'files':files,'registry_sha256':registry_sha})[:16]
    target=root/'runtime'/identity
    if not target.exists():
        target.mkdir(parents=True)
        for relative in files:
            destination=target/'sttbench'/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/relative,destination)
        shutil.copyfile(LAB/'models.json',target/'models.json')
        (target/'worker.py').write_text("import runpy\nrunpy.run_module('sttbench.local_worker', run_name='__main__')\n",encoding='utf-8')
        write_json(target/'source-manifest.json',{'files':files,'installed_at':datetime.now(timezone.utc).isoformat()})
    for relative,sha in files.items():
        if file_digest(target/'sttbench'/relative)!=sha:
            raise ValueError('Versioned worker copy differs from source')
    if file_digest(target/'models.json')!=registry_sha:
        raise ValueError('Versioned model registry differs from source')
    configs={m:read_json(study/'configs'/f'{m}.json') for m in ('apex','trelis')}
    for config in configs.values():
        if not Path(config['artifact_path']).exists():raise ValueError('Local model unavailable')
    value={'version':1,'python_executable':str(python),'worker_script':str(target/'worker.py'),
           'registry_path':str(target/'models.json'),'models':configs,'worker_source_id':identity,
           'summaries_enabled':False,'network_inference':False}
    prior=root/'runtime.json'
    if prior.exists():
        backup=root/'runtime'/f'previous-{file_digest(prior)[:16]}.json'
        if not backup.exists():shutil.copyfile(prior,backup)
    write_json(prior,value)
    vault=root/'vaults/tapf'
    if not vault.exists():
        write_json(vault/'revision-000000000001.json',{'version':1,'id':'tapf','name':'TAPF Vault','revision':1,
                   'entries':[],'relationships':[],'updated_at':datetime.now(timezone.utc).isoformat()})
    return {'runtime':str(prior),'source_id':identity,'vault':str(vault),'model_downloads':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root',type=Path,required=True);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--python',type=Path,required=True)
    a=p.parse_args();print(json.dumps(install(a.data_root,a.study,a.python)))
