"""Compare a converted local model against frozen reviewed pilot inputs.

Reference text is used only after each inference result. Cross-runtime agreement
is distinct from word error against human references and timestamp correctness.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.normalization import Normalizer, word_errors, entity_counts
from sttbench.runtime import transcribe


def run(screening_path, baseline_path, model_id, config_path, output):
    if output.exists():
        raise ValueError('Use a new converted-profile output directory')
    screening, baseline = read_json(screening_path), read_json(baseline_path)
    if baseline['screening_sha256'] != file_digest(screening_path):
        raise ValueError('Baseline belongs to another screening manifest')
    bound_inputs = {}
    for key in ('references', 'policy'):
        bound = (screening_path.parent / baseline['inputs'][key]).resolve()
        if not bound.is_relative_to(screening_path.parent.resolve()) or file_digest(bound) != baseline[key+'_sha256']:
            raise ValueError('Frozen reviewed reference or scoring policy changed')
        bound_inputs[key] = read_json(bound)
    if baseline.get('word_equivalences'):
        raise ValueError('This probe expects the frozen strict normalization policy')
    reference_model = baseline['models'][model_id]
    source_result = screening_path.parent / 'results' / f'{model_id}.json'
    if file_digest(source_result) != reference_model['source_results_sha256']:
        raise ValueError('Original inference results changed')
    original = read_json(source_result)
    for key in ('model_id', 'repo_id', 'source_revision'):
        if original['model_spec'][key] != reference_model['model_spec'][key]:
            raise ValueError('Baseline model identity differs from original results')
    config = read_json(config_path)
    if config.get('backend') != 'whisper_cpp':
        raise ValueError('This probe requires explicit whisper_cpp settings')
    spec = {**reference_model['model_spec'], 'artifact_path': config['artifact_path']}
    clips = {c['id']: c for c in screening['clips']}
    references = {s['sample_id']: s for s in bound_inputs['references']['samples']}
    policy = bound_inputs['policy']
    if bound_inputs['references']['screening_sha256'] != file_digest(screening_path) or policy['references_sha256'] != baseline['references_sha256']:
        raise ValueError('Reference-to-screening or policy-to-reference binding differs')
    if {s['sample_id'] for s in reference_model['samples']} != set(references) or len(reference_model['samples']) != len(references):
        raise ValueError('Baseline must contain every reviewed reference exactly once')
    for sample in reference_model['samples']:
        sample_id = sample['sample_id']; ref = references[sample_id]
        expected_inclusion = sample_id not in policy.get('excluded_samples', {})
        annotations = [{k:v for k,v in item.items() if k != 'matched'} for item in sample.get('entity_details', [])]
        if (ref.get('review_completed') is not True or sample['reference'] != ref['reference_roman']
                or sample['hypothesis'] != original['clips'][sample_id]['text']
                or sample['included_in_headline'] != expected_inclusion
                or sample['audio_sha256'] != clips[sample_id]['audio_sha256']
                or annotations != policy['entity_annotations'].get(sample_id, [])):
            raise ValueError('Baseline row differs from its bound original/reference/policy/audio')
    selected = [s for s in reference_model['samples'] if s['included_in_headline']]
    if not selected:
        raise ValueError('No reviewed excerpts selected for conversion comparison')
    for sample in selected:
        c = clips[sample['sample_id']]
        audio = (screening_path.parent / c['audio']).resolve()
        if not audio.is_relative_to(screening_path.parent.resolve()) or file_digest(audio) != c['audio_sha256']:
            raise ValueError('Audio path or hash changed')
    output.mkdir(parents=True)
    report = {'version': 1, 'kind': 'sttbench_conversion_probe', 'state': 'running',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model_id': model_id,
              'screening_sha256': file_digest(screening_path), 'baseline_sha256': file_digest(baseline_path),
              'runtime_config': config, 'runtime_config_sha256': file_digest(config_path), 'model_spec': spec,
              'source_results_sha256': reference_model['source_results_sha256'],
              'reference_policy': 'Frozen reviewed development pilot; no per-model spelling aliases or reference prompts',
              'network_attempts': [], 'samples': [], 'live_latency_verified': False,
              'code_sha256': {name: file_digest(LAB / name) for name in ('tools/compare_converted.py', 'sttbench/runtime/api.py', 'sttbench/runtime/assets.py')}}
    normalizer = Normalizer()
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def deny(*args, **kwargs):
        report['network_attempts'].append('blocked Python socket connection')
        raise OSError('Network disabled during private model conversion probe')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    try:
        for sample in selected:
            clip = clips[sample['sample_id']]
            print(f"{model_id}: {sample['sample_id']} starting", flush=True)
            result = transcribe(spec, screening_path.parent / clip['audio'], config)
            row = {'sample_id': sample['sample_id'], 'audio_sha256': clip['audio_sha256'], 'result': result,
                   'original_text': sample['hypothesis'], 'human_reference': sample['reference']}
            if result['status'] == 'ok':
                row['reference_word_errors'] = word_errors(normalizer.tokens(sample['reference']), normalizer.tokens(result['text']))
                row['baseline_text_changes'] = word_errors(normalizer.tokens(sample['hypothesis']), normalizer.tokens(result['text']))
                row['normalized_text_identical'] = normalizer.tokens(sample['hypothesis']) == normalizer.tokens(result['text'])
                row['entity_counts'] = entity_counts(sample.get('entity_details', []), result['text'], normalizer)
                segments = result['segments']
                row['timestamp_sanity'] = {'requested': config.get('timestamps') is not False, 'nonempty': bool(segments),
                    'ordered': all(a['start'] <= b['start'] and a['end'] <= b['end'] for a,b in zip(segments,segments[1:])),
                    'inside_audio': all(0 <= s['start'] <= s['end'] <= clip['duration_seconds']+.1 for s in segments),
                    'alignment_accuracy_verified': False}
            # A timestamp rejection does not erase the raw decoder text as
            # evidence. Its separate diagnostic score never admits that result.
            diagnostic_text = result['text'] if result['status'] == 'ok' else (
                result.get('diagnostics', {}).get('rejected_text')
                if result.get('error', {}).get('code') == 'timestamp_output_invalid' else None)
            if isinstance(diagnostic_text, str):
                row['diagnostic_raw_text'] = diagnostic_text
                row['diagnostic_reference_word_errors'] = word_errors(normalizer.tokens(sample['reference']), normalizer.tokens(diagnostic_text))
            report['samples'].append(row)
            write_json(output / 'results.json', report)
            print(json.dumps({'sample': sample['sample_id'], 'status': result['status'],
                              'seconds': result['timing']['total_seconds'], 'error': result.get('error')}), flush=True)
    except BaseException:
        report['state'] = 'interrupted'
        raise
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved
        rows = report['samples']; good = [r for r in rows if r['result']['status'] == 'ok']
        errors = sum(r['reference_word_errors']['errors'] for r in good)
        words = sum(r['reference_word_errors']['reference_words'] for r in good)
        complete = len(good) == len(selected) and not report['network_attempts']
        diagnostic = [r for r in rows if 'diagnostic_reference_word_errors' in r]
        diagnostic_words = sum(r['diagnostic_reference_word_errors']['reference_words'] for r in diagnostic)
        diagnostic_errors = sum(r['diagnostic_reference_word_errors']['errors'] for r in diagnostic)
        report['summary'] = {'complete': complete, 'expected_samples': len(selected), 'successful_samples': len(good),
            'reference_words': words, 'reference_errors': errors, 'reference_wer': errors/words if words and complete else None,
            'entities': {kind: {key: sum(r['entity_counts'][kind][key] for r in good) for key in ('matched','reference')} for kind in ('name','number')},
            'diagnostic_raw_text_samples': len(diagnostic),
            'diagnostic_raw_text_wer': diagnostic_errors/diagnostic_words if diagnostic_words and len(diagnostic)==len(selected) else None,
            'diagnostic_raw_text_includes_timestamp_rejections': len(diagnostic)>len(good),
            'identical_normalized_samples': sum(r['normalized_text_identical'] for r in good),
            'timestamps_requested': config.get('timestamps') is not False,
            'all_timestamp_sanity_checks_pass': (complete and all(all(r['timestamp_sanity'][k] for k in ('nonempty','ordered','inside_audio')) for r in good)) if config.get('timestamps') is not False else None,
            'timestamp_alignment_accuracy_verified': False, 'qualified_for_release': False}
        if report['state'] != 'interrupted': report['state'] = 'complete' if complete else 'failed'
        write_json(output / 'results.json', report)
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--screening', required=True, type=Path); p.add_argument('--baseline-scores', required=True, type=Path)
    p.add_argument('--model', required=True, choices=('apex','swift')); p.add_argument('--runtime-config', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a=p.parse_args()
    r=run(a.screening.resolve(),a.baseline_scores.resolve(),a.model,a.runtime_config.resolve(),a.output.resolve())
    print(json.dumps(r['summary'],indent=2))
    raise SystemExit(0 if r['state']=='complete' else 1)
