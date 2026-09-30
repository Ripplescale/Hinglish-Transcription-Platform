"""Prepare private audio slices and measure fixed-clock, single-worker ASR replay.

Paced prerecorded audio is not microphone capture, speaker attribution, or UI
latency. Capacity mode records service times only; it makes no paced claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.runtime import transcribe, reset_runtime_cache


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite positive number')
    return value


def child(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Audio path escapes its private manifest directory')
    return path


def pcm_info(path):
    with wave.open(str(path), 'rb') as src:
        if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('Expected mono 16kHz 16-bit PCM')
        frames = src.getnframes()
        if not frames or len(src.readframes(frames)) != frames * 2:
            raise ValueError('Empty or truncated PCM audio')
        return frames


def prepare(screening, call_id, start, duration, chunk_seconds, output):
    positive(duration, 'duration'); positive(chunk_seconds, 'chunk_seconds')
    if chunk_seconds > 30 or start < 0 or not math.isfinite(start):
        raise ValueError('Start must be nonnegative and chunks at most 30 seconds')
    if output.exists():
        raise ValueError('Choose a new output directory')
    source_doc = read_json(screening)
    call = next((c for c in source_doc['calls'] if c['id'] == call_id), None)
    if call is None or start + duration > call['audio_seconds']:
        raise ValueError('Requested interval is outside the source call')
    source = child(screening.parent, call['original'])
    if file_digest(source) != call['source_sha256']:
        raise ValueError('Original recording hash changed')
    import imageio_ffmpeg
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    output.mkdir(parents=True)
    decoded = output / 'source-window.wav'
    # Honor audio packet timestamps before selecting the source interval. AAC
    # frame counts alone can drift from the MP4 timeline on long recordings.
    filters = (f'aresample=16000:async=1:first_pts=0,atrim=start={start}:end={start+duration},'
               f'asetpts=PTS-STARTPTS,apad=whole_dur={duration},atrim=end={duration}')
    command = [executable, '-nostdin', '-v', 'error', '-i', str(source),
               '-af', filters, '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
               '-c:a', 'pcm_s16le', str(decoded)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Local audio decode failed: ' + result.stderr[-1000:])
    frames = pcm_info(decoded)
    if abs(frames / 16000 - duration) > .05:
        raise ValueError('Decoded interval does not match requested duration')
    chunk_frames = round(chunk_seconds * 16000)
    chunks = []
    with wave.open(str(decoded), 'rb') as audio:
        for index, frame_start in enumerate(range(0, frames, chunk_frames), 1):
            raw = audio.readframes(min(chunk_frames, frames - frame_start))
            clip = output / f'chunk-{index:04d}.wav'
            with wave.open(str(clip), 'wb') as dst:
                dst.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                dst.writeframes(raw)
            count = len(raw) // 2
            chunks.append({'id': f'chunk-{index:04d}', 'audio': clip.name,
                           'start_seconds': frame_start / 16000,
                           'end_seconds': (frame_start + count) / 16000,
                           'audio_sha256': file_digest(clip)})
    document = {'version': 1, 'kind': 'sttbench_paced_replay_input',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source_screening_sha256': file_digest(screening), 'source_call_id': call_id,
                'source_original_sha256': call['source_sha256'], 'source_title': call['title'],
                'source_start_seconds': start, 'audio_seconds': frames / 16000,
                'chunk_seconds': chunk_seconds, 'decoded_sha256': file_digest(decoded),
                'decode_command': command,
                'timeline_policy': 'Honor packet timestamps with FFmpeg aresample async=1; exact requested interval including silence; pad only to declared interval length',
                'source_exposure': 'Previously screened development call; not held-out accuracy evidence',
                'chunk_policy': 'Contiguous disjoint PCM slices; silence retained; no lookahead or prompt context',
                'chunks': chunks}
    write_json(output / 'replay.json', document)
    return document


def percentile(values, quantile=.95):
    """Nearest-rank quantile, explicit for small runs and deadline statistics."""
    return sorted(values)[max(0, math.ceil(quantile * len(values)) - 1)] if values else None


def summary(rows, mode, expected_chunks, audio_seconds):
    good = [r for r in rows if r['result']['status'] == 'ok']
    report = {'mode': mode, 'expected_chunks': expected_chunks, 'attempted_chunks': len(rows),
              'unattempted_chunks': expected_chunks-len(rows),
              'successful_chunks': len(good), 'failed_chunks': len(rows) - len(good),
              'all_chunks_attempted': len(rows) == expected_chunks,
              'complete': len(rows) == expected_chunks and len(good) == expected_chunks, 'planned_audio_seconds': audio_seconds,
              'processed_audio_seconds': sum(r['source_end_seconds'] - r['source_start_seconds'] for r in rows),
              'service_p95_seconds': percentile([r['service_seconds'] for r in rows]),
              'cold_first_service_seconds': rows[0]['service_seconds'] if rows else None,
              'after_first_service_p95_seconds': percentile([r['service_seconds'] for r in rows[1:]]),
              'model_cache_reused_chunks': sum(r['result'].get('provenance', {}).get('model_reused') is True for r in rows),
              'model_cache_not_reused_chunks': sum(r['result'].get('provenance', {}).get('model_reused') is False for r in rows),
              'cache_reused_service_p95_seconds': percentile([r['service_seconds'] for r in rows if r['result'].get('provenance', {}).get('model_reused') is True]),
              'live_application_latency_verified': False, 'accuracy_verified': False}
    if mode == 'paced':
        delays = [r['source_end_delay_seconds'] for r in rows]
        oldest = [r['oldest_audio_delay_seconds'] for r in rows]
        queues = [r['queue_delay_seconds'] for r in rows]
        gaps = [rows[i]['finished_seconds'] - rows[i-1]['finished_seconds'] for i in range(1, len(rows))]
        tail = rows[max(0, len(rows) * 3 // 4):]
        slope = None
        if len(tail) >= 2:
            xs = [r['source_end_seconds'] for r in tail]; ys = [r['queue_delay_seconds'] for r in tail]
            xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
            denominator = sum((x-xm)**2 for x in xs)
            slope = sum((x-xm)*(y-ym) for x, y in zip(xs, ys)) / denominator if denominator else None
        late = sum(r['result']['status'] != 'ok' or r['oldest_audio_delay_seconds'] > 15 for r in rows)
        report.update(source_end_delay_p95_seconds=percentile(delays), oldest_audio_delay_p95_seconds=percentile(oldest),
                      maximum_queue_seconds=max(queues, default=0), end_queue_seconds=queues[-1] if queues else None,
                      end_source_delay_seconds=delays[-1] if delays else None, inter_completion_gap_p95_seconds=percentile(gaps),
                      late_run_queue_growth_seconds_per_audio_second=slope,
                      oldest_audio_deadline_misses=late,
                      deadline_fraction_scope='attempted chunks only; stopped runs do not qualify the planned interval',
                      oldest_audio_deadline_miss_fraction=late/len(rows) if rows else None,
                      after_first_oldest_audio_delay_p95_seconds=percentile(oldest[1:]))
    return report


def measure_chunks(chunks, invoke, mode='paced', clock=time.perf_counter, sleep=time.sleep,
                   checkpoint=lambda rows: None, abort_backlog_seconds=60, deadline_miss_budget=None):
    """Keep one fixed origin; do not hide queue growth by resetting the clock."""
    origin = clock(); rows = []; stop_reason = None
    for item in chunks:
        ready = item['end_seconds']
        if mode == 'paced':
            while (remaining := origin + ready - clock()) > 0:
                sleep(min(remaining, 1))
        started = clock() - origin
        result = invoke(item)
        finished = clock() - origin
        row = {'chunk_id': item['id'], 'source_start_seconds': item['start_seconds'],
               'source_end_seconds': ready, 'started_seconds': started, 'finished_seconds': finished,
               'service_seconds': finished - started, 'result': result}
        if mode == 'paced':
            row.update(ready_seconds=ready, queue_delay_seconds=max(0, started-ready),
                       source_end_delay_seconds=finished-ready,
                       oldest_audio_delay_seconds=finished-item['start_seconds'])
        rows.append(row); checkpoint(rows)
        if result.get('status') != 'ok':
            stop_reason = 'worker_failure'; break
        if mode == 'paced' and finished - ready > abort_backlog_seconds:
            stop_reason = 'source_end_delay_exceeded_abort_limit'; break
        if mode == 'paced' and deadline_miss_budget is not None:
            misses = sum(r['oldest_audio_delay_seconds'] > 15 for r in rows)
            if misses > deadline_miss_budget:
                stop_reason = 'planned_interval_cannot_meet_95_percent_oldest_audio_deadline'; break
    return rows, stop_reason


def run(manifest, model_id, runtime, output, mode='paced', abort_backlog_seconds=60):
    if mode not in ('paced', 'capacity'):
        raise ValueError('Unknown replay mode')
    positive(abort_backlog_seconds, 'abort_backlog_seconds')
    if output.exists():
        raise ValueError('Replay timing cannot resume or overwrite an existing run')
    document = read_json(manifest)
    if document.get('kind') != 'sttbench_paced_replay_input' or not document.get('chunks'):
        raise ValueError('Expected prepared replay input')
    previous_end = 0
    if len({item['id'] for item in document['chunks']}) != len(document['chunks']):
        raise ValueError('Replay chunk IDs must be unique')
    for item in document['chunks']:
        audio = child(manifest.parent, item['audio'])
        if item['start_seconds'] != previous_end or not 0 < item['end_seconds']-previous_end <= 30:
            raise ValueError('Chunks must be contiguous, ordered and at most 30 seconds')
        if file_digest(audio) != item['audio_sha256'] or abs(pcm_info(audio)/16000-(item['end_seconds']-previous_end)) > 1/16000:
            raise ValueError('Replay audio integrity or duration mismatch')
        previous_end = item['end_seconds']
    if previous_end != document['audio_seconds']:
        raise ValueError('Replay chunks do not cover the declared source interval')
    registry = read_json(LAB / 'models.json')
    spec = dict(next(m for m in registry['models'] if m['id'] == model_id))
    spec['artifact_path'] = runtime.get('artifact_path', str(Path(os.environ['LOCALAPPDATA']) / 'STTApp/lab/models' / model_id / spec['source_revision']))
    config = {'backend': 'transformers', 'device': 'cpu', 'dtype': 'float32', 'threads': 4, 'timestamps': False, **runtime}
    reset_runtime_cache()
    output.mkdir(parents=True)
    report = {'version': 1, 'kind': 'sttbench_paced_replay_result', 'state': 'running', 'mode': mode,
              'started_at': datetime.now(timezone.utc).isoformat(), 'input_sha256': file_digest(manifest),
              'input_manifest': str(manifest), 'source_exposure': document['source_exposure'],
              'model_spec': spec, 'runtime_config': config, 'abort_backlog_seconds': abort_backlog_seconds,
              'deadline_miss_budget': len(document['chunks'])//20 if mode == 'paced' else None,
              'early_stop_policy': 'Stop once 15-second oldest-audio misses exceed 5% of the entire planned interval, even if every remaining chunk would pass; this does not claim a completed sustained run',
              'environment': {'python': sys.version, 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                              'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata.get('Name')}},
              'code_sha256': {name: file_digest(LAB / name) for name in ('tools/sustained_replay.py', 'sttbench/runtime/api.py', 'sttbench/runtime/assets.py')},
              'conditions': {'model_cache_reset_before_run': True, 'os_file_cache_controlled': False,
                             'preloaded_model': False, 'capture_worker_running': False,
                             'diarization_running': False, 'calling_application_load_controlled': False,
                             'ui_rendering_included': False, 'network_guard': 'Python socket calls only; not an OS firewall',
                             'timed_scope': 'Per-call wall time includes asset verification, model loading and ASR; WAV slices prepared before replay; result availability is measured before checkpoint serialization, not UI display'},
              'limitations': ['Previously recorded audio; no microphone, system capture, diarization or UI timing.',
                             'Ten-second chunks impose up to ten seconds of batching delay before inference.',
                             'No word-level timing or reviewed references for these contiguous slices.',
                             'Capacity mode has no real-time input clock; no latency or backlog qualification.'],
              'network_attempts': [], 'rows': []}
    def checkpoint(rows):
        report['rows'] = rows
        report['summary'] = summary(rows, mode, len(document['chunks']), document['audio_seconds'])
        write_json(output / 'results.json', report)
        row = rows[-1]
        print(json.dumps({'model': model_id, 'chunk': row['chunk_id'], 'status': row['result']['status'],
                          'service_seconds': round(row['service_seconds'], 3),
                          'source_end_delay_seconds': row.get('source_end_delay_seconds')}), flush=True)
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def denied(*args, **kwargs):
        report['network_attempts'].append('blocked Python socket connection')
        raise OSError('Network disabled during private replay')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    write_json(output / 'results.json', report)
    try:
        def invoke(item):
            return transcribe(spec, child(manifest.parent, item['audio']), config)
        rows, reason = measure_chunks(document['chunks'], invoke, mode=mode, checkpoint=checkpoint,
                                      abort_backlog_seconds=abort_backlog_seconds,
                                      deadline_miss_budget=report['deadline_miss_budget'])
        report.update(state='complete' if reason is None and not report['network_attempts'] else 'stopped', stop_reason=reason)
    except BaseException as exc:
        report.update(state='interrupted', stop_reason=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        if 'summary' in report:
            report['summary']['complete'] = report['summary']['complete'] and report['state'] == 'complete'
        write_json(output / 'results.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--screening', type=Path, required=True); p.add_argument('--call', required=True)
    p.add_argument('--start', type=float, default=30); p.add_argument('--duration', type=float, default=1200)
    p.add_argument('--chunk-seconds', type=float, default=10); p.add_argument('--output', type=Path, required=True)
    r = commands.add_parser('run')
    r.add_argument('--input', type=Path, required=True); r.add_argument('--model', choices=('apex', 'trelis'), required=True)
    r.add_argument('--runtime-config', type=Path); r.add_argument('--output', type=Path, required=True)
    r.add_argument('--mode', choices=('paced', 'capacity'), default='paced')
    r.add_argument('--abort-backlog-seconds', type=float, default=60)
    args = parser.parse_args()
    if args.command == 'prepare':
        d = prepare(args.screening.resolve(), args.call, args.start, args.duration, args.chunk_seconds, args.output.resolve())
        print(json.dumps({'chunks': len(d['chunks']), 'audio_seconds': d['audio_seconds']}))
    else:
        d = run(args.input.resolve(), args.model, read_json(args.runtime_config) if args.runtime_config else {},
                args.output.resolve(), args.mode, args.abort_backlog_seconds)
        print(json.dumps(d.get('summary', {}), indent=2))
        return 0 if d['state'] == 'complete' else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
