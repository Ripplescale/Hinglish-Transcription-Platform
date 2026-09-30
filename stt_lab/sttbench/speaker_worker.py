"""Optional isolated, offline Community-1 post-call worker.

No download, authentication, telemetry, audio upload or ASR text modification is
performed by this entrypoint. Its environment must be separate from STT workers.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import time
import wave

from sttbench.speaker_reconciliation import reconcile_speakers
from sttbench.speaker_supervision import job_lock, parent_watchdog

MODEL_ID = 'pyannote/speaker-diarization-community-1'


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_status(path, value):
    value['updated_at'] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix('.part')
    with temporary.open('w', encoding='utf-8') as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.flush(); os.fsync(target.fileno())
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.025 * (attempt + 1))


def inspect_model(config):
    root = Path(config['model_path']).resolve(strict=True)
    manifest = read_json(root / 'sttapp-model-manifest.json')
    if manifest.get('model_id') != MODEL_ID or manifest.get('revision') != config.get('model_revision'):
        raise ValueError('Speaker model identity does not match its installed manifest')
    if not re.fullmatch('[0-9a-f]{40}', str(manifest.get('revision', ''))):
        raise ValueError('Speaker model must be pinned to an exact revision')
    if not manifest.get('files') or 'config.yaml' not in manifest['files']:
        raise ValueError('Speaker model manifest is incomplete')
    for relative, expected in manifest['files'].items():
        target = (root / relative).resolve(strict=True)
        if not target.is_relative_to(root) or file_sha(target) != expected:
            raise ValueError('Speaker model file integrity check failed')
    return root, manifest


def prepare_request(config, request, request_path):
    session = Path(request['session_dir']).resolve(strict=True)
    session_metadata = read_json(session / 'session.json')
    if session.name != session_metadata['session_id']:
        raise ValueError('Capture identity does not match its folder')
    audio = (session / 'audio.wav').resolve(strict=True)
    if not audio.is_relative_to(session):
        raise ValueError('Audio leaves the recording folder')
    with wave.open(str(audio), 'rb') as source:
        if source.getnframes() <= 0:
            raise ValueError('Recording is empty')
    transcript_path = Path(request['transcription_status_path']).resolve(strict=True)
    transcript = read_json(transcript_path)
    if transcript.get('state') != 'complete' or transcript.get('job_id') != request['transcription_job_id']:
        raise ValueError('Post-call speakers require a complete matching transcription job')
    if Path(transcript['session_dir']).resolve() != session:
        raise ValueError('Transcript belongs to another recording')
    root, manifest = inspect_model(config)
    identity = {'request_sha256': file_sha(request_path), 'audio_sha256': file_sha(audio),
                'transcription_status_sha256': file_sha(transcript_path),
                'model_manifest_sha256': file_sha(root / 'sttapp-model-manifest.json')}
    previous = None
    if request.get('previous_speakers_path'):
        previous_path = Path(request['previous_speakers_path']).resolve(strict=True)
        previous = read_json(previous_path)
        previous = previous.get('result', previous)
        if previous.get('session_id') != session_metadata['session_id']:
            raise ValueError('Previous speaker labels belong to another recording')
        identity['previous_speakers_sha256'] = file_sha(previous_path)
    return session_metadata, audio, transcript, root, manifest, identity, previous


def disable_network():
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0')
    def blocked(*_args, **_kwargs):
        raise RuntimeError('Network access is disabled in the speaker worker')
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked


def parent_alive():
    pid = int(os.environ.get('STTAPP_PARENT_PID', '0'))
    if not pid:
        return True
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def run(config_path, request_path):
    request_path = Path(request_path).resolve(strict=True)
    # Acquire ownership before any status update; a duplicate child may not
    # overwrite another worker's progress or failure state.
    with job_lock(request_path.parent / 'worker.lock'), parent_watchdog():
        return run_locked(config_path, request_path)


def run_locked(config_path, request_path):
    request_path = Path(request_path).resolve(strict=True)
    status_path = request_path.parent / 'status.json'
    request = read_json(request_path)
    status = {'version': 1, 'kind': 'local_speaker_job', 'job_id': request['job_id'],
              'meeting_id': request['meeting_id'], 'session_dir': request['session_dir'],
              'transcription_job_id': request['transcription_job_id'], 'state': 'validating'}
    write_status(status_path, status)
    try:
        config = read_json(config_path)
        if config.get('device', 'cpu') != 'cpu':
            raise ValueError('This local speaker runtime currently supports CPU only')
        metadata, audio, transcript, model_root, manifest, identity, previous = prepare_request(config, request, request_path)
        status['source_identity'] = identity
        status['source_identity']['worker_sha256'] = file_sha(__file__)
        status['source_identity']['config_sha256'] = file_sha(config_path)
        status['state'] = 'loading_model'; write_status(status_path, status)
        disable_network()
        import torch
        from pyannote.audio import Pipeline
        torch.set_num_threads(max(1, min(8, int(config.get('threads', 4)))))
        pipeline = Pipeline.from_pretrained(str(model_root))
        if pipeline is None:
            raise RuntimeError('Local speaker pipeline could not be loaded')
        status['state'] = 'diarizing'; write_status(status_path, status)
        last_progress = 0.0
        def progress(*args, **kwargs):
            nonlocal last_progress
            if (request_path.parent / 'stop.request').exists() or not parent_alive():
                raise InterruptedError('Speaker processing stopped; recording and transcript remain saved')
            if time.monotonic()-last_progress >= 2:
                status['stage'] = str(args[0]) if args else 'diarizing'
                if isinstance(kwargs.get('completed'), (int, float)):
                    status['stage_completed'] = kwargs['completed']
                if isinstance(kwargs.get('total'), (int, float)):
                    status['stage_total'] = kwargs['total']
                write_status(status_path, status)
                last_progress = time.monotonic()
        progress()
        output = pipeline(str(audio), hook=progress)
        # Keep regular diarization, including overlapping speakers. Exclusive
        # diarization would hide overlap without adding missing word timestamps.
        turns = [{'start_seconds': float(turn.start), 'end_seconds': float(turn.end), 'model_speaker_label': str(speaker)}
                 for turn, speaker in output.speaker_diarization]
        status['state'] = 'reconciling'; write_status(status_path, status)
        result = reconcile_speakers(metadata['session_id'], turns, transcript.get('segments', []), previous)
        result.update(model_id=MODEL_ID, model_revision=manifest['revision'],
                      license='CC-BY-4.0', attribution_url='https://huggingface.co/' + MODEL_ID,
                      runtime={'pyannote.audio': importlib.metadata.version('pyannote.audio'), 'torch': torch.__version__})
        status.update(state='complete', result=result)
    except InterruptedError as error:
        status.update(state='stopped', error=str(error))
    except Exception as error:
        status.update(state='failed', error=f'{type(error).__name__}: {error}')
    write_status(status_path, status)
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    result = run(args.config, args.request)
    print(json.dumps({'job_id': result['job_id'], 'state': result['state']}))
    raise SystemExit(0 if result['state'] == 'complete' else 1)
