"""Prepare isolated synthetic fixtures for the native dual-pass IPC checks.

No real audio, production database, weights, or model inference are used. The
tiny worker writes deterministic checkpoints so this checks native orchestration
and storage independently of model quality. Always choose a new output folder.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
import wave


WORKER = r'''
import argparse, json, os, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--config', required=True)
p.add_argument('--request', required=True)
a = p.parse_args()
request_path = Path(a.request)
request = json.loads(request_path.read_text(encoding='utf-8'))
root = Path(a.config).parent
with (root / 'synthetic-worker-starts.jsonl').open('a', encoding='utf-8') as out:
    out.write(json.dumps({'job_id': request['job_id'], 'role': request.get('workflow_role'), 'pid': os.getpid()}) + '\n')
role = request.get('workflow_role') or 'comparison'
texts = ['Synthetic Apex raw draft: Mira counted 16 pencils.', 'Synthetic Apex raw draft: Willow requested 24 notebooks.'] if role == 'live-draft' else ['Synthetic Trelis raw final: मीरा ने 17 पेंसिल गिनीं।', 'Synthetic Trelis raw final: Willow ने 25 notebooks माँगीं।']
segments = [{'id': request['job_id'] + '-' + str(i), 'text': text, 'start_seconds': i * 10, 'end_seconds': (i + 1) * 10, 'source_track': 'system', 'final': True} for i, text in enumerate(texts)]
status = {'job_id': request['job_id'], 'profile': request['profile'], 'session_dir': request['session_dir'], 'state': 'complete', 'segments': segments, 'processed_audio_seconds': 20, 'available_audio_seconds': 20, 'backlog_seconds': 0, 'synthetic_checkpoint': True}
temporary = request_path.parent / 'status.tmp'
temporary.write_text(json.dumps(status, ensure_ascii=False), encoding='utf-8')
temporary.replace(request_path.parent / 'status.json')
# Keep the process alive after its checkpoint to exercise native teardown gating.
time.sleep(0.5)
'''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(python, output):
    python = python.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError('Choose a new, empty native QA root')
    if any(part.lower().startswith('onedrive') for part in output.parts):
        raise ValueError('Use a local temporary folder outside OneDrive')
    output.mkdir(parents=True)
    # Explicit fresh DB prevents legacy user-data migration on app startup.
    with sqlite3.connect(output / 'meeting_minutes.sqlite') as database:
        database.execute('VACUUM')
    worker = output / 'synthetic_worker.py'
    worker.write_text(WORKER, encoding='utf-8')
    registry = output / 'synthetic_registry.json'
    write_json(registry, {'synthetic_only': True})
    artifact = output / 'synthetic-model-marker.txt'
    artifact.write_text('Not model weights. Native orchestration fixture only.', encoding='utf-8')
    write_json(output / 'runtime.json', {
        'python_executable': str(python), 'worker_script': str(worker),
        'registry_path': str(registry),
        'models': {model: {'artifact_path': str(artifact)} for model in ('apex', 'trelis')},
        'synthetic_only': True,
    })
    sessions = []
    for finalized in (True, False):
        sid = str(uuid.uuid4())
        session = output / 'recordings' / sid
        session.mkdir(parents=True)
        write_json(session / 'session.json', {
            'schema_version': 1, 'session_id': sid,
            'meeting_name': 'Synthetic dual-pass QA' if finalized else 'Synthetic unfinished capture QA',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'devices': {'microphone': None, 'system': 'Generated silence fixture'},
            'format': 'pcm_s16le_mono_wav', 'chunk_target_seconds': 1,
            'timeline_clock': 'saved_audio_seconds', 'transcription_required': False,
        })
        rows = [{'kind': 'capture_stopped', 'at_seconds': 20}]
        if finalized:
            rows.append({'kind': 'capture_finalized', 'tracks': ['system'], 'dropped_samples': 0})
        (session / 'timeline.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        with wave.open(str(session / 'audio.wav'), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x00\x00' * 16000 * 20)
        sessions.append({'session_id': sid, 'session_dir': str(session)})
    evidence = {'data_root': str(output), **sessions[0], 'unfinished_session': sessions[1],
                'synthetic_checkpoint': True, 'model_inference': False,
                'device_recording': False, 'mocked_ipc': False}
    write_json(output / 'fixture.json', evidence)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.python, args.output)))
