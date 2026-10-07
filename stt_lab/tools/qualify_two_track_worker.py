"""Paced, saved-audio qualification of the real local worker and journal contract.

Never opens recording devices. Inputs, runtime snapshots and outputs stay in a
new private directory outside OneDrive. Two distinct excerpts are independent
ASR inputs, not evidence of synchronized microphone/system capture.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from qualify_live_worker import append, memory, percentile, wav
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.normalization import basic_tokens, word_errors

TRACKS = ('microphone', 'system')
RELATIONSHIPS = ('saved_separate_tracks', 'distinct_saved_excerpts', 'duplicated_load')


def slice_pcm(path, start, duration):
    if type(start) is not int or start < 0 or type(duration) is not int or duration <= 0:
        raise ValueError('Start and duration must be whole, nonnegative seconds; duration must be positive')
    with wave.open(str(path), 'rb') as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('Expected mono PCM16 16kHz WAV input')
        if source.getnframes() < (start + duration) * 16000:
            raise ValueError('Requested interval exceeds source audio')
        source.setpos(start * 16000)
        raw = source.readframes(duration * 16000)
    if len(raw) != duration * 32000:
        raise ValueError('Truncated source WAV')
    return raw


def prepare(output, config, microphone, system, duration, relationship,
            microphone_start=0, system_start=0):
    if output.exists():
        raise ValueError('Choose a new private output directory')
    if any(p.lower().startswith('onedrive') for p in output.resolve().parts):
        raise ValueError('Private audio must stay outside OneDrive')
    if relationship not in RELATIONSHIPS:
        raise ValueError('Declare the relationship between the two audio inputs')
    inputs = {'microphone': (microphone, microphone_start), 'system': (system, system_start)}
    raw = {track: slice_pcm(path, start, duration) for track, (path, start) in inputs.items()}
    identical = raw['microphone'] == raw['system']
    if identical != (relationship == 'duplicated_load'):
        raise ValueError('Identical input must be labelled duplicated_load; distinct input must not be')
    cfg = read_json(config)
    output.mkdir(parents=True)
    sources = {}
    for track, (path, start) in inputs.items():
        target = output / f'{track}.wav'
        wav(target, raw[track])
        sources[track] = {'file': target.name, 'sha256': file_digest(target),
                          'source_sha256': file_digest(path), 'source_start_seconds': start,
                          'source_path': str(path.resolve())}
    runtime = output / 'runtime'
    hashes = {}
    for source in sorted((LAB / 'sttbench').rglob('*.py')):
        rel = source.relative_to(LAB)
        dest = runtime / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        hashes[rel.as_posix()] = file_digest(dest)
    registry = Path(cfg['registry_path'])
    shutil.copyfile(registry, runtime / 'models.json')
    (runtime / 'worker.py').write_text("import runpy\nrunpy.run_module('sttbench.local_worker',run_name='__main__')\n", encoding='utf-8')
    for name in ('models.json', 'worker.py'):
        hashes[name] = file_digest(runtime / name)
    cfg.update(worker_script=str(runtime / 'worker.py'), registry_path=str(runtime / 'models.json'))
    for model in cfg['models'].values():
        if 'work_dir' in model:
            model['work_dir'] = str(output / 'adapter-work')
    write_json(output / 'runtime.json', cfg)
    doc = {'version': 1, 'kind': 'two_track_worker_qualification',
           'created_at': datetime.now(timezone.utc).isoformat(), 'duration_seconds': duration,
           'sources': sources, 'source_relationship': relationship,
           'source_snapshot_sha256': hashes, 'runtime_sha256': file_digest(output / 'runtime.json'),
           'source_runtime_sha256': file_digest(config),
           'limitations': ['Saved audio paced into durable journal, not physical capture or calling-application load.',
                          'Two excerpts or duplicated audio do not prove natural dual-channel turn taking.',
                          'Worker-status observation includes materialization, queueing and automatic retries; excludes native/UI polling.',
                          'Twenty-second windows cannot meet a fifteen-second oldest-audio deadline.',
                          'Fresh worker process; OS and OpenVINO compiled caches are not controlled.',
                          'No speaker processing, latency-qualified UI, or human accuracy judgment.']}
    write_json(output / 'manifest.json', doc)
    return doc


def load_input(output):
    manifest = read_json(output / 'manifest.json')
    if manifest.get('kind') != 'two_track_worker_qualification':
        raise ValueError('Wrong replay manifest kind')
    if file_digest(output / 'runtime.json') != manifest['runtime_sha256']:
        raise ValueError('Runtime changed after preparation')
    for rel, expected in manifest['source_snapshot_sha256'].items():
        path = (output / 'runtime' / rel).resolve(strict=True)
        if not path.is_relative_to((output / 'runtime').resolve()) or file_digest(path) != expected:
            raise ValueError('Worker snapshot changed or escaped its directory')
    sources = {}
    for track in TRACKS:
        item = manifest['sources'][track]
        path = (output / item['file']).resolve(strict=True)
        if not path.is_relative_to(output.resolve()) or file_digest(path) != item['sha256']:
            raise ValueError('Replay input changed or escaped its directory')
        sources[track] = slice_pcm(path, 0, manifest['duration_seconds'])
    return manifest, read_json(output / 'runtime.json'), sources


def commit_second(session, sources, index, elapsed):
    if elapsed + 1e-6 < index + 1:
        raise ValueError('Attempt to expose future audio')
    for track in TRACKS:
        raw = sources[track][index * 32000:(index + 1) * 32000]
        if len(raw) != 32000:
            raise ValueError('Incomplete replay second')
        relative = f'tracks/{track}/{index:08}.wav'
        path = session / relative
        partial = path.with_suffix('.part')
        wav(partial, raw)
        with partial.open('r+b') as handle:
            os.fsync(handle.fileno())
        partial.replace(path)
        append(session / 'timeline.jsonl', {'kind': 'audio_chunk', 'track': track, 'file': relative,
               'sequence': index, 'sample_rate': 16000, 'sample_count': 16000,
               'start_sample': index * 16000, 'end_sample': (index + 1) * 16000,
               'start_seconds': index, 'end_seconds': index + 1, 'sha256': file_digest(path)})


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def segment_key(segment):
    return segment['source_track'], segment['start_seconds'], segment['end_seconds']


def check_prefix(previous, segments):
    ids = [s['id'] for s in segments]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate segment IDs in worker checkpoint')
    current = [fingerprint(segment) for segment in segments]
    if current[:len(previous)] != previous:
        raise ValueError('Worker changed or removed a committed raw segment')
    for segment in segments:
        if segment['text'] != segment['recognition']['text']:
            raise ValueError('Raw display text differs from model recognition')
    return current


def coverage(status, duration, window=20):
    expected = {(track, start, min(start + window, duration))
                for track in TRACKS for start in range(0, duration, window)}
    segments = status.get('segments', [])
    actual = [segment_key(s) for s in segments]
    return {'expected_windows': len(expected), 'observed_windows': len(actual),
            'missing_windows': [list(key) for key in sorted(expected - set(actual))],
            'unexpected_windows': [list(key) for key in sorted(set(actual) - expected)],
            'duplicate_windows': len(actual) - len(set(actual)),
            'all_windows_covered': set(actual) == expected and len(actual) == len(expected),
            'cursors_reach_end': all(status.get('cursors', {}).get(t) == duration for t in TRACKS)}


def slope(points):
    if len(points) < 2:
        return None
    xs, ys = zip(*points)
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - xm) ** 2 for x in xs)
    return sum((x - xm) * (y - ym) for x, y in points) / denominator if denominator else None


def summarize(observations, status, duration, exit_code, first_status, finalized, ended, commits,
              lifecycle, integrity_error=None, backlog_samples=()):
    spoken = [r for r in observations if not r['digital_silence']]
    ends = [r['ready_elapsed_seconds'] - r['end_seconds'] for r in spoken]
    oldest = [r['ready_elapsed_seconds'] - r['start_seconds'] for r in spoken]
    per_track = {}
    for track in TRACKS:
        rows = [r for r in spoken if r['source_track'] == track]
        # Compare completed-window delay at the same source positions. Raw
        # backlog includes the next incomplete 20s window and is not queue lag.
        late = rows[max(0, len(rows) // 2):]
        per_track[track] = {'nonzero_windows': len(rows),
            'end_delay_p95_seconds': percentile([r['ready_elapsed_seconds'] - r['end_seconds'] for r in rows], .95),
            'late_window_delay_slope': slope([(r['end_seconds'], r['ready_elapsed_seconds'] - r['end_seconds']) for r in late])}
    cover = coverage(status, duration)
    grows = any(v['late_window_delay_slope'] is None or v['late_window_delay_slope'] > .02 for v in per_track.values())
    complete = status.get('state') == 'complete' and exit_code == 0 and cover['all_windows_covered'] and cover['cursors_reach_end']
    future = any(c['elapsed_seconds'] + 1e-6 < c['audio_end_seconds'] for c in commits)
    p95 = percentile(ends, .95)
    report = {'state': status.get('state'), 'worker_exit_code': exit_code, **cover,
        'complete': complete, 'integrity_error': integrity_error, 'raw_prefix_preserved': integrity_error is None,
        'source_end_delay_p50_seconds': percentile(ends, .5), 'source_end_delay_p95_seconds': p95,
        'oldest_audio_delay_p95_seconds': percentile(oldest, .95),
        'oldest_audio_15s_deadline_misses': sum(value > 15 for value in oldest),
        'nonzero_windows': len(spoken), 'per_track': per_track,
        'process_start_to_first_status_seconds': first_status,
        'process_start_to_first_nonzero_ready_seconds': spoken[0]['ready_elapsed_seconds'] if spoken else None,
        'model_startup': status.get('model_startup'),
        'model_startup_timing': status.get('model_startup', {}).get('timing', {}),
        'startup_timing_scope': 'Fresh worker process includes precompile before the first journal window; OS and compiled caches remain uncontrolled. First recognition may already reuse the precompiled model.',
        'first_nonzero_source_end_delay_seconds': ends[0] if ends else None,
        'after_first_source_end_delay_p95_seconds': percentile(ends[1:], .95),
        'first_primary_adapter_seconds': spoken[0]['adapter_timing'].get('total_seconds') if spoken else None,
        'reused_primary_adapter_p95_seconds': percentile([r['adapter_timing']['total_seconds'] for r in spoken if r.get('model_reused') is True and 'total_seconds' in r['adapter_timing']], .95),
        'model_reused_windows': sum(r.get('model_reused') is True for r in spoken),
        'maximum_completed_window_backlog_seconds': max((r['completed_window_backlog_seconds'] for r in [*observations, *backlog_samples]), default=0),
        'maximum_raw_backlog_seconds': max((r['backlog_seconds'] for r in [*observations, *backlog_samples]), default=0),
        'post_stop_drain_seconds': ended - finalized if complete and ended is not None and finalized is not None else None,
        'automatic_retry_windows': sum(r['has_alternative'] for r in observations),
        'failed_retry_windows': sum('retry_failed' in r.get('quality_flags', []) for r in observations),
        'future_audio_exposed': future, 'committed_seconds': len(commits),
        'max_commit_lateness_seconds': max((r['elapsed_seconds'] - r['audio_end_seconds'] for r in commits), default=0),
        'lifecycle_events': lifecycle,
        'bounded_paced_pass': complete and not integrity_error and not future and not lifecycle and p95 is not None and p95 <= 15 and not grows,
        'pass_scope': 'Completed windows ready within 15s at p95, no positive late lag slope above .02 s/s, full coverage, immutable raw prefix; unpaused saved-audio run only.',
        'oldest_audio_15s_target_qualified': False, 'physical_capture_qualified': False,
        'speaker_concurrency_qualified': False, 'ui_latency_qualified': False, 'accuracy_qualified': False}
    return report


def compare_status(candidate, baseline):
    """Same-window model agreement is a regression check, never accuracy proof."""
    left = {segment_key(s): s for s in baseline['segments']}
    right = {segment_key(s): s for s in candidate['segments']}
    if len(left) != len(baseline['segments']) or len(right) != len(candidate['segments']):
        raise ValueError('Cannot compare duplicate source windows')
    rows = []
    for key in sorted(left.keys() & right.keys()):
        a, b = left[key], right[key]
        if a['audio_provenance']['audio_sha256'] != b['audio_provenance']['audio_sha256']:
            raise ValueError('Cannot compare different source audio at the same timestamp')
        rows.append({'source_track': key[0], 'start_seconds': key[1], 'end_seconds': key[2],
                     'raw_text_identical': a['text'] == b['text'],
                     'baseline_text': a['text'], 'candidate_text': b['text'],
                     'script_preserving_word_difference': word_errors(basic_tokens(a['text']), basic_tokens(b['text']))})
    return {'same_window_set': left.keys() == right.keys(), 'windows': rows,
            'all_raw_text_identical': bool(rows) and left.keys() == right.keys() and all(r['raw_text_identical'] for r in rows),
            'accuracy_qualified': False, 'comparison_scope': 'Machine-output regression only; baseline is not a human reference.'}


def pause_is_due(elapsed, pause_at, observed_segments, pause_after_segments=0):
    """A warmup checkpoint cannot satisfy a requested completed-prefix pause."""
    return pause_at is not None and elapsed >= pause_at and observed_segments >= pause_after_segments


def run(output, run_name, profile='trelis-20', max_seconds=900, pause_at=None,
        resume_after=2.0, baseline=None, pause_after_segments=0):
    if profile not in ('apex-20', 'trelis-5', 'trelis-10', 'trelis-20'):
        raise ValueError('Choose Apex 20s or Trelis 5s, 10s or 20s')
    if not run_name or not run_name.replace('-', '').replace('_', '').isalnum():
        raise ValueError('Unsafe run name')
    manifest, cfg, sources = load_input(output)
    duration = manifest['duration_seconds']
    if max_seconds <= duration or resume_after < 0 or (pause_at is not None and not 0 < pause_at < duration):
        raise ValueError('Time bound must exceed audio duration; pause must be inside it')
    if type(pause_after_segments) is not int or pause_after_segments < 0:
        raise ValueError('Pause-after-segments must be a nonnegative integer')
    if pause_after_segments and pause_at is None:
        raise ValueError('Pause-after-segments requires a pause-at time')
    dest = output / run_name
    if dest.exists():
        raise ValueError('Choose a new run name; measured replay never overwrites or resumes an observer')
    session, job = dest / 'session', dest / 'job'
    session.mkdir(parents=True); job.mkdir()
    write_json(session / 'session.json', {'version': 1, 'id': str(uuid.uuid4()), 'synthetic_replay': True})
    (session / 'timeline.jsonl').write_bytes(b'')
    write_json(job / 'request.json', {'job_id': str(uuid.uuid4()), 'profile': profile,
               'language_mode': 'hinglish', 'project_id': 'qualification', 'session_dir': str(session)})
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', STTAPP_PARENT_PID=str(os.getpid()), PYTHONUTF8='1')
    command = [cfg['python_executable'], cfg['worker_script'], '--config', str(output / 'runtime.json'),
               '--request', str(job / 'request.json')]
    prefix, observations, commits, lifecycle, backlog_samples = [], [], [], [], []
    status, finalized, first_status, integrity_error = {}, None, None, None
    pause_requested, stopped_at, resumed, abort_reason = False, None, False, None
    before = memory(); started = time.perf_counter(); ended = None; last_checkpoint = -1
    def elapsed(): return time.perf_counter() - started
    with (job / 'worker.log').open('wb') as log:
        def launch():
            return subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        worker = launch()
        write_json(dest / 'run-identity.json', {'profile': profile, 'manifest_sha256': file_digest(output / 'manifest.json'),
                   'observer_sha256': file_digest(Path(__file__)), 'process_started_at': datetime.now(timezone.utc).isoformat(),
                   'source_relationship': manifest['source_relationship'], 'worker_pid': worker.pid,
                   'conditions': {'fresh_worker_process': True, 'os_file_cache_controlled': False,
                                  'compiled_cache_controlled': False, 'physical_capture': False,
                                  'speakers_running': False, 'ui_polling_included': False}})
        try:
            while True:
                now = elapsed()
                while len(commits) < duration and now >= len(commits) + 1 and abort_reason is None:
                    index = len(commits)
                    commit_second(session, sources, index, now)
                    commits.append({'audio_end_seconds': index + 1, 'elapsed_seconds': elapsed()})
                    now = elapsed()
                if (len(commits) == duration or abort_reason) and finalized is None:
                    append(session / 'timeline.jsonl', {'kind': 'capture_stopped', 'at_seconds': len(commits)})
                    append(session / 'timeline.jsonl', {'kind': 'capture_finalized'})
                    finalized = elapsed()
                if pause_is_due(now, pause_at, len(prefix), pause_after_segments) and not pause_requested:
                    (job / 'stop.request').write_text('Qualification graceful pause', encoding='utf-8')
                    lifecycle.append({'event': 'stop_requested', 'elapsed_seconds': elapsed(), 'prefix_segments': len(prefix)})
                    pause_requested = True
                if (job / 'status.json').exists():
                    try:
                        status = read_json(job / 'status.json')
                    except (PermissionError, FileNotFoundError):
                        time.sleep(.05); continue
                    if first_status is None:
                        first_status = elapsed()
                    segments = status.get('segments', [])
                    current = check_prefix(prefix, segments)
                    for segment in segments[len(prefix):]:
                        audio = segment['audio_provenance']; result = segment['recognition']
                        ready = elapsed()
                        observed = {'id': segment['id'], 'source_track': segment['source_track'],
                            'start_seconds': segment['start_seconds'], 'end_seconds': segment['end_seconds'],
                            'ready_elapsed_seconds': ready, 'backlog_seconds': status.get('backlog_seconds', 0),
                            'completed_window_backlog_seconds': max(max(0, (len(commits) // 20) * 20 - status.get('cursors', {}).get(t, 0)) for t in TRACKS),
                            'digital_silence': audio['digital_silence'], 'audio_sha256': audio['audio_sha256'],
                            'adapter_timing': result.get('timing', {}), 'model_reused': result.get('provenance', {}).get('model_reused'),
                            'quality_flags': segment['quality_flags'], 'has_alternative': segment.get('alternative') is not None,
                            'recognition_provenance': result.get('provenance', {}),
                            'raw_text_sha256': hashlib.sha256(segment['text'].encode('utf-8')).hexdigest(),
                            'devanagari_characters': sum('\u0900' <= char <= '\u097f' for char in segment['text'])}
                        observations.append(observed)
                        print(json.dumps({'track': observed['source_track'], 'end': observed['end_seconds'],
                              'ready_seconds': round(ready, 2), 'window_end_lag_seconds': round(ready - observed['end_seconds'], 2)}), flush=True)
                    prefix = current
                    if elapsed() - last_checkpoint >= 1:
                        cursors = status.get('cursors', {})
                        backlog_samples.append({'elapsed_seconds': elapsed(), 'committed_seconds': len(commits),
                            'backlog_seconds': max(len(commits) - cursors.get(t, 0) for t in TRACKS),
                            'completed_window_backlog_seconds': max(max(0, (len(commits) // 20) * 20 - cursors.get(t, 0)) for t in TRACKS)})
                        write_json(dest / 'observations.json', {'segments': observations, 'commits': commits,
                                   'lifecycle': lifecycle, 'backlog_samples': backlog_samples})
                        last_checkpoint = elapsed()
                code = worker.poll()
                if code is not None:
                    # A child can replace its final checkpoint between our
                    # snapshot read and poll; observe that final prefix first.
                    if status.get('state') not in ('complete', 'failed', 'stopped') and (job / 'status.json').exists():
                        status = read_json(job / 'status.json')
                        if status.get('state') in ('complete', 'failed', 'stopped'):
                            continue
                    if status.get('state') == 'stopped' and pause_requested and not resumed and abort_reason is None:
                        if stopped_at is None:
                            stopped_at = elapsed()
                            lifecycle.append({'event': 'process_stopped', 'elapsed_seconds': stopped_at, 'prefix_segments': len(prefix)})
                        if elapsed() >= stopped_at + resume_after:
                            (job / 'stop.request').unlink()
                            worker = launch(); resumed = True
                            lifecycle.append({'event': 'process_resumed', 'elapsed_seconds': elapsed(), 'worker_pid': worker.pid})
                    else:
                        ended = elapsed(); break
                if elapsed() > max_seconds and abort_reason is None:
                    abort_reason = 'Bounded qualification time exhausted'
                    (job / 'stop.request').write_text(abort_reason, encoding='utf-8')
                if elapsed() > max_seconds + 60:
                    worker.terminate(); worker.wait(timeout=30); ended = elapsed(); break
                time.sleep(.1)
        except BaseException as exc:
            integrity_error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            if worker.poll() is None:
                (job / 'stop.request').write_text('Qualification observer stopping', encoding='utf-8')
                try:
                    worker.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    worker.terminate(); worker.wait(timeout=30)
            report = summarize(observations, status, duration, worker.returncode, first_status,
                               finalized, ended, commits, lifecycle, integrity_error, backlog_samples)
            report.update(profile=profile, source_relationship=manifest['source_relationship'],
                          aborted_reason=abort_reason, memory_before=before, memory_after=memory(),
                          limitations=manifest['limitations'])
            if pause_at is not None:
                report.update(bounded_paced_pass=False, requested_pause_at_seconds=pause_at,
                              requested_pause_after_segments=pause_after_segments,
                              lifecycle_expectation_satisfied=pause_requested and resumed and stopped_at is not None)
            write_json(dest / 'observations.json', {'segments': observations, 'commits': commits,
                       'backlog_samples': backlog_samples, 'summary': report})
            write_json(dest / 'summary.json', report)
    if baseline is not None:
        write_json(dest / 'baseline-comparison.json', compare_status(status, read_json(baseline)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    for name in ('output', 'config', 'microphone', 'system'):
        prep.add_argument('--' + name, type=Path, required=True)
    prep.add_argument('--duration', type=int, required=True)
    prep.add_argument('--relationship', choices=RELATIONSHIPS, required=True)
    prep.add_argument('--microphone-start', type=int, default=0)
    prep.add_argument('--system-start', type=int, default=0)
    test = sub.add_parser('run')
    test.add_argument('--output', type=Path, required=True)
    test.add_argument('--run-name', required=True)
    test.add_argument('--profile', choices=('apex-20', 'trelis-5', 'trelis-10', 'trelis-20'), default='trelis-10')
    test.add_argument('--max-seconds', type=int, default=900)
    test.add_argument('--pause-at', type=float)
    test.add_argument('--pause-after-segments', type=int, default=0)
    test.add_argument('--resume-after', type=float, default=2)
    test.add_argument('--baseline', type=Path)
    args = vars(parser.parse_args()); action = args.pop('action')
    result = prepare(**args) if action == 'prepare' else run(**args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if action == 'prepare' or result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
