"""Private, resumable boundary-shift pilot and three fresh development minutes.

Reuses immutable aligned predictions; new requests run serially and offline.
No overlap merging, text correction, transliteration, or live latency claim.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import sys
import time
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import read_json, write_json, file_digest, digest
from sttbench.normalization import basic_tokens, word_errors
from sttbench.runtime import transcribe, reset_runtime_cache
from prepare_expanded_review import pcm_slice, inside
from chunk_size_sweep import pcm, windows
from score_chunk_corrections import aggregate, devanagari

PROFILES = {'apex': (15, 20, 30), 'trelis': (15, 20)}
SHIFT = 5


def shifted_windows(frames, size, phase=SHIFT, rate=16000):
    if not 0 < phase < size or frames <= 0:
        raise ValueError('Phase must be positive and smaller than the nominal size')
    first = min(round(phase * rate), frames)
    cuts = [(0, first)]
    if first < frames:
        cuts += [(a + first, b + first) for a, b in windows(frames - first, size, rate)]
    return cuts


def fresh_start(duration, excluded):
    """Predeclared .45 position then 10-second grid; no ASR selects the minute."""
    candidates = [round(duration * .45 - 30), *range(10, int(duration) - 69, 10)]
    for start in candidates:
        end = start + 60
        if start < 2 or end > duration - 2:
            continue
        if not any(start < stop + 2 and end > begin - 2 for begin, stop in excluded):
            return start
    raise ValueError('No fresh minute outside previous exposure with 2s padding')


def prepare(study, output):
    if output.exists() or any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Choose a new private output directory')
    old = read_json(study / 'manifest.json')
    ref_path = study / 'reviewed-20260929/effective-references.json'
    refs = read_json(ref_path)
    old_results = {m: read_json(study / 'results' / f'{m}.json') for m in PROFILES}
    binding = refs['source']['binding']
    if binding['manifest_sha256'] != file_digest(study / 'manifest.json'):
        raise ValueError('Corrected references no longer bind to original study')
    for model in PROFILES:
        if (binding['model_results_sha256'][model] != file_digest(study / 'results' / f'{model}.json')
                or old_results[model]['state'] != 'complete'):
            raise ValueError('Original predictions changed or incomplete')
    bench = study.parent
    screen_path = bench / 'screening-20260918-six-calls/screening.json'
    expanded_path = bench / 'expanded-20260922-batch1/screening.json'
    minutes_path = bench / 'minute-review-20260922/review-manifest.json'
    screen, expanded, minutes = map(read_json, (screen_path, expanded_path, minutes_path))
    evidence_paths = [screen_path, expanded_path, minutes_path]
    excluded = {c['id']: [] for c in screen['calls']}
    for doc, key in ((screen, 'clips'), (expanded, 'clips'), (minutes, 'sections')):
        for row in doc[key]:
            excluded[row['call_id']].append((row['start_seconds'], row['start_seconds'] + row['duration_seconds']))
    for path in sorted(bench.glob('paced*/replay.json')):
        replay = read_json(path)
        excluded[replay['source_call_id']].append((replay['source_start_seconds'], replay['source_start_seconds'] + replay['audio_seconds']))
        evidence_paths.append(path)
    samples = []
    by_ref = {r['id']: r for r in refs['samples']}
    for source in old['samples']:
        row = copy.deepcopy(by_ref[source['id']])
        row.update(group='checked', source_audio_path=str(inside(study, source['audio'])),
                   trelis_decoding=source['trelis_decoding'])
        samples.append(row)
    for call_id in ('call-03', 'call-05', 'call-06'):
        call = next(c for c in expanded['calls'] if c['id'] == call_id)
        source = inside(expanded_path.parent, call['analysis'])
        if file_digest(source) != call['analysis_sha256']:
            raise ValueError('Preserved decoded source changed')
        with wave.open(str(source), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
        start = fresh_start(duration, excluded[call_id])
        samples.append({'id': f'fresh-{call_id}', 'title': f'Fresh minute {len(samples)-5}',
            'group': 'fresh', 'call_id': call_id, 'call_title': call['title'],
            'source_audio_path': str(source), 'source_audio_sha256': call['analysis_sha256'],
            'source_start_seconds': start, 'duration_seconds': 60, 'reference': None,
            'reference_kind': 'pending', 'reference_scope_note': '', 'eligible_models': [],
            'notes': '', 'expected_language': 'English' if call_id == 'call-06' else 'Hinglish',
            'language_human_confirmed': False,
            'trelis_decoding': {'language': 'en', 'mixed_code': False} if call_id == 'call-06'
                               else {'language': 'hi', 'mixed_code': True},
            'excluded_source_intervals': excluded[call_id]})
    output.mkdir(parents=True)
    for name in ('audio', 'chunks', 'configs', 'upstream', 'results'):
        (output / name).mkdir()
    shutil.copyfile(ref_path, output / 'upstream/effective-references.json')
    shutil.copyfile(study / 'manifest.json', output / 'upstream/manifest.json')
    shutil.copyfile(LAB / 'sttbench/runtime/api.py', output / 'upstream/api.py')
    shutil.copyfile(LAB / 'sttbench/normalization.py', output / 'upstream/normalization.py')
    for model in PROFILES:
        shutil.copyfile(study / 'results' / f'{model}.json', output / 'upstream' / f'{model}.json')
        write_json(output / 'configs' / f'{model}.json', old_results[model]['runtime_config'])
    for sample in samples:
        parent = output / 'audio' / f"{sample['id']}.wav"
        if sample['group'] == 'checked':
            shutil.copyfile(sample['source_audio_path'], parent)
            if file_digest(parent) != sample['audio_sha256']:
                raise ValueError('Checked parent copy changed')
        else:
            pcm_slice(Path(sample['source_audio_path']), parent, sample['source_start_seconds'], sample['source_start_seconds'] + 60)
        sample.update(audio=parent.relative_to(output).as_posix(), audio_sha256=file_digest(parent), partitions={})
        raw = pcm(parent)
        for size in (15, 20, 30):
            conditions = ('aligned', 'shift5') if sample['group'] == 'checked' else ('aligned',)
            for condition in conditions:
                cuts = windows(len(raw)//2, size) if condition == 'aligned' else shifted_windows(len(raw)//2, size)
                rows = []
                joined = []
                for index, (start, end) in enumerate(cuts, 1):
                    target = output / 'chunks' / f"{sample['id']}-{size}-{condition}-{index}.wav"
                    pcm_slice(parent, target, start/16000, end/16000)
                    joined.append(pcm(target))
                    rows.append({'id': f"{sample['id']}:{size}:{condition}:{index}",
                        'audio': target.relative_to(output).as_posix(), 'audio_sha256': file_digest(target),
                        'start_seconds': start/16000, 'end_seconds': end/16000,
                        'duration_seconds': (end-start)/16000})
                if b''.join(joined) != raw:
                    raise ValueError('Partitions do not exactly reconstruct parent')
                sample['partitions'][f'{size}:{condition}'] = rows
    manifest = {'version': 1, 'kind': 'boundary_pilot', 'created_at': datetime.now(timezone.utc).isoformat(),
        'profiles': {m: list(v) for m, v in PROFILES.items()}, 'samples': samples,
        'upstream_sha256': {p.name: file_digest(p) for p in (output / 'upstream').iterdir()},
        'exclusion_evidence': [{'path': str(p), 'sha256': file_digest(p)} for p in evidence_paths],
        'selection_policy': 'One60s minute per previously exposed call03/05/06. Try45% midpoint then ascending10s grid, excluding screening, expanded/minute reviews and paced replay with2s padding. No ASR selects windows.',
        'clock_policy': 'Existing decoded PCM sample clock, not validated MP4 timestamps. Paced replay exclusion conservatively includes the documented source interval.',
        'partition_policy': 'Aligned or initial5s partial then nominal windows. Rebalance sub1s tails. Exact fullparent coverage; literal joins, no overlap/dedup.',
        'timing_policy': 'Observed serial batch service times only, not live latency. Existing aligned results were recorded under different load.',
        'held_out': False, 'trelis_romanization': False, 'release_qualified': False}
    write_json(output / 'manifest.json', manifest)
    print(json.dumps({'output': str(output), 'jobs': {m: sum(len(parts) for s in samples for key, parts in s['partitions'].items() if int(key.split(':')[0]) in sizes and (s['group']=='fresh' or key.endswith('shift5'))) for m,sizes in PROFILES.items()},
        'fresh': [{'id': s['id'], 'start': s['source_start_seconds']} for s in samples if s['group']=='fresh']}))


def infer(root, model):
    manifest = read_json(root / 'manifest.json')
    upstream_path = root / 'upstream' / f'{model}.json'
    if file_digest(upstream_path) != manifest['upstream_sha256'][f'{model}.json']:
        raise ValueError('Upstream predictions changed')
    upstream = read_json(upstream_path)
    spec = copy.deepcopy(upstream['model_spec'])
    config = read_json(root / 'configs' / f'{model}.json')
    if config != upstream['runtime_config']:
        raise ValueError('Runtime changed from aligned baseline')
    identity = {'manifest_sha256': file_digest(root / 'manifest.json'), 'model_spec': spec,
        'runtime_config': config, 'runner_sha256': file_digest(Path(__file__)),
        'adapter_sha256': file_digest(LAB / 'sttbench/runtime/api.py')}
    if identity['adapter_sha256'] != upstream['adapter_sha256']:
        raise ValueError('Runtime adapter differs from aligned baseline')
    output = root / 'results' / f'{model}.json'
    report = {'version': 1, 'kind': 'boundary_pilot_results', 'model_id': model, **identity,
        'state': 'running', 'chunks': {}, 'network_attempts': [], 'sessions': []}
    if output.exists():
        report = read_json(output)
        if any(report.get(k) != v for k,v in identity.items()):
            raise ValueError('Cannot resume changed inputs/code')
    # One worker per study prevents duplicate model jobs. A crashed lock is never
    # removed automatically: inspect its PID before explicitly clearing it.
    import os
    lock = root / 'inference.lock'
    with lock.open('x', encoding='utf8') as stream:
        json.dump({'pid': os.getpid(), 'model': model}, stream)
    started = time.perf_counter()
    report['sessions'].append({'started_at': datetime.now(timezone.utc).isoformat(), 'model': model})
    prior = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def denied(*args, **kwargs):
        report['network_attempts'].append('Blocked Python network call')
        raise OSError('Private inference is offline')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    cache = {r['request_sha256']: r for r in upstream['chunks'].values() if r['status'] == 'ok'}
    cache.update({r['request_sha256']: r for r in report['chunks'].values() if r['status'] == 'ok'})
    try:
        # Fresh minutes first so their review artifact can be inspected sooner.
        for sample in sorted(manifest['samples'], key=lambda s: s['group'] != 'fresh'):
            effective = copy.deepcopy(spec)
            if model == 'trelis':
                effective['decoding'].update(sample['trelis_decoding'])
            for size in manifest['profiles'][model]:
                for condition in ('aligned', 'shift5'):
                    for part in sample['partitions'].get(f'{size}:{condition}', []):
                        path = inside(root, part['audio'])
                        if file_digest(path) != part['audio_sha256']:
                            raise ValueError('Chunk audio changed')
                        if report['chunks'].get(part['id'], {}).get('status') == 'ok':
                            continue
                        key = digest({'audio_sha256': part['audio_sha256'], 'spec': effective, 'config': config})
                        if key in cache:
                            result = copy.deepcopy(cache[key])
                            result['reused_identical_request'] = True
                        else:
                            result = transcribe(effective, path, config)
                            result['reused_identical_request'] = False
                        result.update(request_sha256=key, audio_sha256=part['audio_sha256'], sample_id=sample['id'],
                                      size=size, condition=condition, start_seconds=part['start_seconds'], end_seconds=part['end_seconds'])
                        report['chunks'][part['id']] = result
                        write_json(output, report)
                        print(json.dumps({'model': model, 'chunk': part['id'], 'status': result['status'],
                            'reused': result['reused_identical_request'], 'seconds': result.get('timing',{}).get('total_seconds')}), flush=True)
                        if result['status'] != 'ok':
                            raise ValueError(f"Inference failed: {result.get('error')}")
                        cache[key] = copy.deepcopy(result)
        report['state'] = 'complete'
    finally:
        report['sessions'][-1]['wall_seconds'] = time.perf_counter() - started
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = prior
        write_json(output, report)
        reset_runtime_cache()
        lock.unlink()


def summarize(root):
    manifest = read_json(root / 'manifest.json')
    reports = {m: read_json(root / 'results' / f'{m}.json') if (root/'results'/f'{m}.json').exists() else None for m in PROFILES}
    out = {'version': 1, 'kind': 'boundary_pilot_summary', 'manifest_sha256': file_digest(root/'manifest.json'),
           'held_out': False, 'release_qualified': False, 'models': {}, 'samples': []}
    for sample in manifest['samples']:
        row = copy.deepcopy(sample)
        row.pop('partitions')
        row['drafts'] = {}
        for model, sizes in manifest['profiles'].items():
            result = reports[model]
            if result and (result['manifest_sha256'] != out['manifest_sha256'] or result['network_attempts']):
                raise ValueError('Unbound results or attempted network access')
            for size in sizes:
                for condition in ('aligned', 'shift5'):
                    parts = sample['partitions'].get(f'{size}:{condition}')
                    if not parts:
                        continue
                    preds = [result['chunks'].get(p['id']) if result else None for p in parts]
                    complete = all(p and p['status']=='ok' and p['audio_sha256']==part['audio_sha256'] for p,part in zip(preds,parts))
                    text = '\n\n'.join(p['text'] for p in preds) if complete else None
                    metrics = word_errors(basic_tokens(sample['reference']), basic_tokens(text)) if complete and model in sample['eligible_models'] else None
                    row['drafts'][f'{model}:{size}:{condition}'] = {'text': text, 'complete': complete, 'metrics': metrics,
                        'script_surface_warning': bool(complete and sample['reference_kind']=='english' and devanagari(text)),
                        'parts': [{'start_seconds': p['start_seconds'], 'end_seconds': p['end_seconds']} for p in parts]}
        out['samples'].append(row)
    for model, sizes in manifest['profiles'].items():
        panel = [s for s in out['samples'] if model in s['eligible_models']]
        rows=[]
        for size in sizes:
            for condition in ('aligned','shift5'):
                key=f'{model}:{size}:{condition}'
                complete=all(s['drafts'][key]['metrics'] is not None for s in panel)
                rows.append({'size':size,'condition':condition,'complete':complete,
                    'aggregate':aggregate([s['drafts'][key]['metrics'] for s in panel]) if complete else None,
                    'boundary_certain_aggregate':aggregate([s['drafts'][key]['metrics'] for s in panel if not s['reference_scope_note']]) if complete else None})
        actual=[r for r in (reports[model] or {}).get('chunks',{}).values() if not r['reused_identical_request'] and r['status']=='ok']
        out['models'][model]={'sample_ids':[s['id'] for s in panel], 'rows':rows,
            'state': (reports[model] or {}).get('state','pending'),
            'result_sha256':file_digest(root/'results'/f'{model}.json') if reports[model] else None,
            'observed_new_requests':len(actual),
            'observed_service_seconds':sum(r['timing']['total_seconds'] for r in actual),
            'observed_audio_seconds':sum(r['end_seconds']-r['start_seconds'] for r in actual)}
    write_json(root/'summary.json',out)
    return out


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('infer');p.add_argument('--root',type=Path,required=True);p.add_argument('--model',choices=PROFILES,required=True)
    p=sub.add_parser('summarize');p.add_argument('--root',type=Path,required=True)
    a=parser.parse_args()
    if a.command=='prepare':prepare(a.study.resolve(),a.output.resolve())
    elif a.command=='infer':infer(a.root.resolve(),a.model)
    else:summarize(a.root.resolve())
