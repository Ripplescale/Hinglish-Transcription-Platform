"""Download Community-1 after the user accepts its gate and logs into HF locally.

No token is accepted on the command line, printed, or saved in model manifests.
Audio and transcripts are never read by this setup tool.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

MODEL_ID = 'pyannote/speaker-diarization-community-1'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-access', action='store_true')
    parser.add_argument('--revision', help='Exact 40-character upstream commit required for download')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from huggingface_hub import HfApi, get_token, snapshot_download
    token = get_token()
    if not token:
        print(json.dumps({'ready': False, 'reason': 'Sign into Hugging Face locally with hf auth login after accepting the Community-1 conditions.'}))
        return 2
    api = HfApi(token=token)
    try:
        api.auth_check(repo_id=MODEL_ID, repo_type='model')
        info = api.model_info(MODEL_ID, revision=args.revision or 'main')
    except Exception as error:
        # Do not print network exception payloads or headers containing credentials.
        print(json.dumps({'ready': False, 'reason': 'Model access could not be verified. Check login, accepted model conditions, and connectivity.', 'error_type': type(error).__name__}))
        return 2
    if args.check_access:
        print(json.dumps({'ready': True, 'model_id': MODEL_ID, 'revision': info.sha, 'license': 'CC-BY-4.0'}))
        return 0
    if not args.revision or not re.fullmatch('[0-9a-f]{40}', args.revision) or info.sha != args.revision:
        raise SystemExit('Use --check-access, then provide its exact --revision for reproducible download.')
    if not args.output:
        raise SystemExit('--output is required for download.')
    output = args.output.resolve()
    if 'onedrive' in str(output).lower():
        raise SystemExit('Install the speaker model outside OneDrive.')
    output.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(MODEL_ID, revision=args.revision, local_dir=str(output), token=token)
    except Exception as error:
        print(json.dumps({'ready': False, 'reason': 'Model download did not finish; the local cache supports retry.', 'error_type': type(error).__name__}))
        return 2
    files = {path.relative_to(output).as_posix(): sha(path) for path in output.rglob('*')
             if path.is_file() and '.cache' not in path.relative_to(output).parts and path.name != 'sttapp-model-manifest.json'}
    if 'config.yaml' not in files:
        raise SystemExit('Downloaded model is missing config.yaml.')
    manifest = {'version': 1, 'model_id': MODEL_ID, 'revision': args.revision,
                'license': 'CC-BY-4.0', 'attribution_url': 'https://huggingface.co/' + MODEL_ID, 'files': files}
    (output / 'sttapp-model-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'ready': True, 'model_id': MODEL_ID, 'revision': args.revision, 'files_verified': len(files)}))
    return 0


if __name__ == '__main__':
    from windows_https import verified_windows_https
    with verified_windows_https():
        raise SystemExit(main())
