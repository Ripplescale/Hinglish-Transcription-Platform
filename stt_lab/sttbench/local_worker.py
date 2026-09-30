"""Restartable offline worker over committed recorder audio, never the capture callback.

Capture owns source audio. This worker owns only derived windows, raw recognition
and review flags. Window times are not word alignment or speaker identity.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import time
import wave
import contextlib

from sttbench.manifest import digest, file_digest, read_json, write_json
from sttbench.normalization import basic_tokens
from sttbench.runtime import transcribe

PROFILES = {'apex-15': ('apex', 15), 'apex-20': ('apex', 20), 'apex-30': ('apex', 30),
            'trelis-15': ('trelis', 15), 'trelis-20': ('trelis', 20)}
TRACKS = ('microphone', 'system')


def contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Committed audio path leaves the recording')
    return path


def journal_snapshot(session: Path):
    raw = (session / 'timeline.jsonl').read_bytes()
    # Ignore a live writer's partial last record; complete malformed records fail.
    complete = raw[:raw.rfind(b'\n') + 1]
    rows = [json.loads(line) for line in complete.splitlines() if line.strip()]
    chunks = {track: [] for track in TRACKS}
    finalized = False
    final_time = 0.0
    for row in rows:
        if row.get('kind') == 'audio_chunk':
            track = row.get('track')
            if track not in chunks:
                raise ValueError('Unknown capture track')
            start, end = row.get('start_seconds'), row.get('end_seconds')
            if not all(type(t) in (int, float) and math.isfinite(t) for t in (start, end)) or not 0 <= start < end:
                raise ValueError('Invalid committed audio timeline')
            if row['sequence'] != len(chunks[track]):
                raise ValueError('Missing or non-monotonic committed audio sequence')
            if row['sample_count'] <= 0 or row['sample_rate'] <= 0 or abs((end-start) - row['sample_count']/row['sample_rate']) > .002:
                raise ValueError('Committed sample count differs from timeline')
            chunks[track].append(row)
            final_time = max(final_time, end)
        elif row.get('kind') == 'capture_stopped':
            final_time = max(final_time, float(row.get('at_seconds', 0)))
        elif row.get('kind') == 'capture_finalized':
            finalized = True
    # Crash recovery keeps the original journal immutable. Only a recovery
    # report bound to those exact bytes may finalize a missing tail marker.
    if not finalized and (session/'metadata.json').is_file():
        metadata = read_json(session/'metadata.json')
        report_path = session/'recovery-verified.json'
        if metadata.get('status') == 'recovered' and report_path.is_file():
            report = read_json(report_path)
            if report.get('verified') is not True or report.get('source_journal_sha256') != hashlib.sha256(raw).hexdigest():
                raise ValueError('Recovered recording no longer matches its verified journal')
            duration = report.get('duration_seconds')
            if type(duration) not in (int,float) or not math.isfinite(duration) or duration < final_time:
                raise ValueError('Invalid recovered duration')
            finalized = True
            final_time = duration
    return chunks, finalized, final_time, rows


def materialize(session: Path, rows: list[dict], start: float, end: float, target: Path) -> dict:
    import numpy as np
    from scipy.signal import resample_poly
    n = round((end - start) * 16000)
    values = np.zeros(n, dtype=np.float32)
    covered = np.zeros(n, dtype=np.bool_)
    bindings, overlaps, spans = [], 0, []
    for row in rows:
        if row['end_seconds'] <= start or row['start_seconds'] >= end:
            continue
        path = contained(session, row['file'])
        actual_hash = file_digest(path)
        expected_hash = row.get('sha256') or row.get('audio_sha256')
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError('Committed audio digest does not match')
        with wave.open(str(path), 'rb') as wav:
            rate = wav.getframerate()
            if (wav.getnchannels(), wav.getsampwidth(), rate, wav.getnframes()) != (1, 2, row['sample_rate'], row['sample_count']):
                raise ValueError('Committed WAV metadata differs from journal')
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768
        # Resample adjacent source chunks together: resetting the filter at every
        # recorder file boundary would add avoidable artifacts each second.
        if spans and rate == spans[-1]['rate'] and abs(row['start_seconds'] - spans[-1]['end']) <= .5/rate:
            spans[-1]['parts'].append(audio)
            spans[-1]['end'] = row['end_seconds']
        else:
            spans.append({'rate':rate,'start':row['start_seconds'],'end':row['end_seconds'],'parts':[audio]})
        bindings.append({'file': row['file'], 'sha256': actual_hash, 'sequence': row['sequence']})
    for span in spans:
        rate = span['rate']
        audio = np.concatenate(span['parts'])
        divisor = math.gcd(rate, 16000)
        converted = resample_poly(audio, 16000 // divisor, rate // divisor) if rate != 16000 else audio
        offset = round((span['start'] - start) * 16000)
        left, right = max(0, offset), min(n, offset + len(converted))
        if right <= left:
            continue
        segment = converted[left-offset:right-offset]
        occupied = covered[left:right]
        overlaps += int(occupied.sum())
        # Do not double-amplify callback jitter; earliest committed audio wins.
        view = values[left:right]
        view[~occupied] = segment[~occupied]
        covered[left:right] = True
    pcm = np.clip(np.rint(values * 32768), -32768, 32767).astype('<i2')
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(pcm.tobytes())
    # Energy only selects review/retry candidates; it is not measured VAD.
    active = sum(float(np.sqrt(np.mean(values[i:i+320] ** 2))) > .006 for i in range(0, n, 320)) * .02
    return {'audio_sha256': file_digest(target), 'source_chunks': bindings,
            'uncovered_seconds': float((~covered).sum()) / 16000,
            'overlap_seconds': overlaps / 16000, 'active_energy_seconds': active,
            'digital_silence': not bool(pcm.any()), 'resampler': 'scipy.signal.resample_poly'}


def review_flags(text: str, audio: dict, duration: float) -> list[str]:
    words = basic_tokens(text)
    flags = []
    if audio['uncovered_seconds'] > .1:
        flags.append('capture_gap')
    if audio['overlap_seconds'] > .1:
        flags.append('capture_clock_overlap')
    if words and audio['active_energy_seconds'] == 0:
        # Quiet nonzero input can still produce a fluent or spurious draft.
        # Keep every recognized word: energy is a review cue, not proof that
        # nobody spoke and not a reason to erase a soft voice or short name.
        flags.append('very_quiet_audio')
    if duration >= 10 and audio['active_energy_seconds'] >= 8 and len(words) < audio['active_energy_seconds'] * .4:
        flags.append('suspiciously_sparse_text')
    if len(words) >= 12 and any(words[i:i+4] == words[i+4:i+8] == words[i+8:i+12] for i in range(len(words)-11)):
        flags.append('repeated_phrase')
    return flags


def evaluate_window(spec, config, session, rows, start, end, target, infer=transcribe):
    audio = materialize(session, rows, start, end, target)
    result = ({'status': 'ok', 'text': '', 'segments': [], 'warnings': ['Verified all-zero PCM; no speech inferred.'],
               'provenance': {'digital_silence': True}, 'timing': {}} if audio['digital_silence'] else infer(spec, target, config))
    if result['status'] != 'ok':
        raise RuntimeError(result.get('error', 'Local model failed'))
    flags = review_flags(result['text'], audio, end-start)
    alternative = None
    if 'suspiciously_sparse_text' in flags and end-start > 10:
        # One bounded retry with disjoint <=10s windows; no lexical replacement.
        pieces = []
        for index, cursor in enumerate(range(round(start*16000), round(end*16000), 160000)):
            left, right = cursor / 16000, min(end, (cursor + 160000) / 16000)
            retry = target.with_name(target.stem + f'-retry-{index}.wav')
            materialize(session, rows, left, right, retry)
            candidate = infer(spec, retry, config)
            pieces.append({'start_seconds': left, 'end_seconds': right, 'result': candidate})
        if all(p['result']['status'] == 'ok' for p in pieces):
            alternative = {'text': '\n\n'.join(p['result']['text'] for p in pieces), 'chunks': pieces,
                           'requires_review': True, 'reason': 'Energy/text heuristic, not proof of missing speech'}
        flags.append('retry_available' if alternative else 'retry_failed')
    return {'text': result['text'], 'result': result, 'audio': audio, 'quality_flags': flags, 'alternative': alternative}


@contextlib.contextmanager
def job_lock(path: Path):
    handle = path.open('a+b')
    if not path.stat().st_size:
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def parent_alive():
    pid = int(os.environ.get('STTAPP_PARENT_PID', '0'))
    if not pid:
        return True
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        dll = ctypes.WinDLL('kernel32', use_last_error=True)
        dll.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        dll.OpenProcess.restype = wintypes.HANDLE
        dll.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        dll.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = dll.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(dll.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            dll.CloseHandle(handle)
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False


def run(config_path: Path, request_path: Path):
    with job_lock(request_path.parent / 'worker.lock'):
        return run_locked(config_path, request_path)


def run_locked(config_path: Path, request_path: Path):
    config = read_json(config_path)
    request = read_json(request_path)
    session = Path(request['session_dir']).resolve(strict=True)
    job = request_path.parent.resolve()
    model, seconds = PROFILES[request['profile']]
    registry = read_json(Path(config['registry_path']))
    spec = copy.deepcopy(next(m for m in registry['models'] if m['id'] == model))
    runtime = copy.deepcopy(config['models'][model])
    if model == 'trelis':
        spec['decoding'].update(language='en' if request['language_mode'] == 'english' else 'hi',
                                mixed_code=request['language_mode'] != 'english')
    identity = {'request_sha256': file_digest(request_path), 'session_sha256': file_digest(session/'session.json'),
                'spec': spec, 'runtime': runtime, 'worker_sha256': file_digest(Path(__file__))}
    identity_hash = digest(identity)
    status_path = job/'status.json'
    status = read_json(status_path) if status_path.exists() else {
        'version': 1, 'job_id': request['job_id'], 'session_dir': str(session), 'profile': request['profile'],
        'state': 'running', 'segments': [], 'cursors': {}, 'identity': identity, 'identity_sha256': identity_hash,
        'processed_audio_seconds': 0, 'available_audio_seconds': 0, 'backlog_seconds': 0,
        'live_qualified': False, 'speaker_identification': 'unavailable', 'timestamp_kind': 'audio_window'}
    if status.get('identity_sha256') != identity_hash:
        raise ValueError('Cannot resume changed model, input or worker identity')
    # Network calls are never needed by this app worker. Native CLI also has no service URL.
    def no_network(*args, **kwargs):
        raise OSError('Local transcription worker cannot connect to a network')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = no_network
    stop = job/'stop.request'
    status.pop('error', None)
    try:
        while True:
            if stop.exists() or not parent_alive():
                status['state'] = 'stopped'; break
            rows, finalized, final_time, events = journal_snapshot(session)
            progressed = False
            available = {track: (final_time if finalized and rows[track] else max((r['end_seconds'] for r in rows[track]), default=0)) for track in TRACKS}
            status['available_audio_seconds'] = max(available.values())
            status['capture_finalized'] = finalized
            status['capture_event_count'] = len(events)
            # Round-robin tracks; one bounded window per track per pass.
            for track in TRACKS:
                cursor = status['cursors'].get(track, 0)
                limit = available[track]
                if limit - cursor < seconds - .001 and not finalized:
                    continue
                if limit <= cursor + 1/16000:
                    continue
                end = min(cursor + seconds, limit)
                segment_id = f"{request['job_id']}-{track}-{round(cursor*16000)}"
                target = job/'windows'/f'{track}-{round(cursor*16000)}.wav'
                status['state'] = 'running'
                write_json(status_path, status)
                evaluated = evaluate_window(spec, runtime, session, rows[track], cursor, end, target)
                status['segments'].append({'id': segment_id, 'text': evaluated['text'], 'start_seconds': cursor,
                    'end_seconds': end, 'source_track': track, 'timestamp_kind': 'audio_window', 'speaker': None,
                    'model_id': model, 'profile': request['profile'], 'quality_flags': evaluated['quality_flags'],
                    'alternative': evaluated['alternative'], 'audio_provenance': evaluated['audio'],
                    'recognition': evaluated['result']})
                status['cursors'][track] = end
                # Inference can be slower than capture; refresh the backlog clock.
                fresh_rows, fresh_finalized, fresh_end, _ = journal_snapshot(session)
                fresh_available = {t: (fresh_end if fresh_finalized and fresh_rows[t] else max((r['end_seconds'] for r in fresh_rows[t]), default=0)) for t in TRACKS}
                status['available_audio_seconds'] = max(fresh_available.values())
                status['processed_audio_seconds'] = min((status['cursors'].get(t,0) for t in TRACKS if fresh_available[t]), default=0)
                status['backlog_seconds'] = max((fresh_available[t]-status['cursors'].get(t,0) for t in TRACKS), default=0)
                status['updated_at'] = datetime.now(timezone.utc).isoformat()
                write_json(status_path, status)
                progressed = True
                if stop.exists() or not parent_alive(): break
            if finalized and all(status['cursors'].get(t, 0) >= available[t]-1/16000 for t in TRACKS):
                status['state'] = 'complete'; break
            if not progressed:
                status['state'] = 'waiting_for_audio'; write_json(status_path, status); time.sleep(.5)
    except Exception as exc:
        status.update(state='failed', error=str(exc))
    finally:
        status['updated_at'] = datetime.now(timezone.utc).isoformat()
        write_json(status_path, status)
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.request)
    print(json.dumps({'job_id': report['job_id'], 'state': report['state']}), flush=True)
    raise SystemExit(1 if report['state'] == 'failed' else 0)
