"""Validate a bound human review and rescore saved drafts into a new revision.

No inference, transliteration, reference cleanup, or historical overwrite occurs.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import read_json, write_json, file_digest
from sttbench.normalization import basic_tokens, word_errors

MODELS = ('apex', 'trelis')
ASSESSMENTS = {'not_checked', 'looks_complete', 'missing_speech', 'unclear'}


def local_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Input leaves the study directory')
    return path


def devanagari(text: str) -> bool:
    return any('\u0900' <= c <= '\u097f' for c in text)


def aggregate(metrics: list[dict]) -> dict:
    keys = ('reference_words', 'hypothesis_words', 'errors', 'substitutions', 'deletions', 'insertions')
    out = {k: sum(m[k] for m in metrics) for k in keys}
    n = out['reference_words']
    out.update(wer=out['errors'] / n if n else None,
               deletion_rate=out['deletions'] / n if n else None)
    return out


def validate(root: Path, source: Path) -> tuple[dict, dict, dict, dict]:
    manifest = read_json(root / 'manifest.json')
    review = read_json(source)
    results = {m: read_json(root / 'results' / f'{m}.json') for m in MODELS}
    binding = {
        'manifest_sha256': file_digest(root / 'manifest.json'),
        'summary_sha256': file_digest(root / 'summary.json'),
        'audio_sha256': {s['id']: s['audio_sha256'] for s in manifest['samples']},
        'model_results_sha256': {m: file_digest(root / 'results' / f'{m}.json') for m in MODELS},
    }
    if review.get('kind') != 'chunk_size_human_review' or review.get('version') != 1:
        raise ValueError('Expected a full chunk-size review export, version 1')
    if review.get('binding') != binding:
        raise ValueError('Review binding does not match the exact study and results')
    ids = [s['id'] for s in manifest['samples']]
    if len(set(ids)) != len(ids) or set(review.get('reviews', {})) != set(ids):
        raise ValueError('Review must contain exactly the study sample IDs')
    valid_checks = {f'{model}:{size}' for model in MODELS for size in manifest['sizes']}
    expected_chunks = set()
    for sample in manifest['samples']:
        if file_digest(local_file(root, sample['audio'])) != sample['audio_sha256']:
            raise ValueError('Parent audio hash changed')
        item = review['reviews'][sample['id']]
        if not isinstance(item, dict) or type(item.get('reviewed')) is not bool:
            raise ValueError('Invalid review state')
        if not all(isinstance(item.get(k), str) for k in ('reference', 'notes')):
            raise ValueError('Reference and notes must be text')
        if item['reviewed'] and not basic_tokens(item['reference']):
            raise ValueError('A reviewed reference must contain words')
        checks = item.get('draft_checks', {})
        if not isinstance(checks, dict) or any(k not in valid_checks or v not in ASSESSMENTS
                                              for k, v in checks.items()):
            raise ValueError('Unknown draft assessment or chunk setting')
        for size in manifest['sizes']:
            for part in sample['partitions'][str(size)]:
                if part['id'] in expected_chunks:
                    raise ValueError('Duplicate partition ID')
                expected_chunks.add(part['id'])
                if file_digest(local_file(root, part['audio'])) != part['audio_sha256']:
                    raise ValueError('Partition audio hash changed')
                for model in MODELS:
                    pred = results[model].get('chunks', {}).get(part['id'], {})
                    if (pred.get('status') != 'ok' or pred.get('audio_sha256') != part['audio_sha256']
                            or not isinstance(pred.get('text'), str)):
                        raise ValueError('Missing, failed, or unbound prediction')
    for model, result in results.items():
        if (result.get('state') != 'complete' or result.get('model_id') != model
                or result.get('manifest_sha256') != binding['manifest_sha256']
                or result.get('network_attempts') != []
                or set(result['chunks']) != expected_chunks):
            raise ValueError('Incomplete or inconsistent model results')
    return manifest, review, results, binding


def build_analysis(root: Path, source: Path) -> dict:
    manifest, review, results, binding = validate(root, source)
    out = {
        'version': 1, 'kind': 'chunk_size_corrected_evaluation',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': {'corrections_sha256': file_digest(source), 'corrections_filename': source.name,
                   'exported_at': review.get('exported_at'), 'binding': binding},
        'sizes': manifest['sizes'], 'samples': [], 'models': {},
        'trelis_romanization': False, 'held_out': False, 'release_qualified': False,
        'metric_policy': {
            'normalization': 'NFC, casefold, punctuation separation; preserve script and numeric representation. No equivalences.',
            'formula': '(substitutions + deletions + insertions) / reference words; pooled by word count.',
            'fixed_panels': 'Determine reference eligibility before examining predictions; keep the same clips at every size.',
            'script_policy': 'Roman Hinglish references score Apex; native mixed references score Trelis; English references score both. Output script variation is flagged and retained as a strict surface diagnostic.',
            'script_sensitivity': 'Exclude English references with Devanagari in any prediction at every size, never only the poor prediction.',
            'boundary_sensitivity': 'Exclude every clip with a reference scope note at every size.',
            'limits': 'Assisted development review, not blind or held-out. Strict word errors and deletion counts are not semantic completeness. Different model panels cannot rank models. Punctuation is unscored; number words and digits are not canonicalized.',
        },
        'code_sha256': {'scorer': file_digest(Path(__file__)),
                        'normalizer': file_digest(LAB / 'sttbench' / 'normalization.py')},
    }
    for sample in manifest['samples']:
        item = review['reviews'][sample['id']]
        prior = sample.get('reference_roman') if sample.get('reference_status') == 'user_reviewed' else None
        reference = item['reference'] if item['reviewed'] else prior
        reference_source = ('uploaded_review' if item['reviewed'] else
                            'prior_checked_reference' if prior is not None else 'pending')
        kind = ('pending' if reference is None else 'native_mixed' if devanagari(reference) else
                'english' if sample['trelis_decoding']['language'] == 'en' else 'roman_hinglish')
        eligible = [] if kind == 'pending' else list(MODELS) if kind == 'english' else [
            'trelis' if kind == 'native_mixed' else 'apex']
        row = {k: copy.deepcopy(sample[k]) for k in
               ('id', 'title', 'call_title', 'source_start_seconds', 'duration_seconds', 'audio', 'audio_sha256')}
        if sample['id'].startswith('section-') and item['reviewed']:
            row['title'] = ('Checked Hinglish minute' if kind == 'native_mixed' else 'Checked omission-case minute')
        row.update(reference=reference, previous_reference=prior,
                   reference_source=reference_source, reference_kind=kind,
                   reference_scope_note=sample.get('reference_scope_note', ''),
                   reference_provenance=copy.deepcopy(item.get('reference_provenance')),
                   notes=item['notes'], reviewed=reference is not None,
                   uploaded_reviewed=item['reviewed'], draft_checks=copy.deepcopy(item.get('draft_checks', {})),
                   unreviewed_reference_draft=item['reference'] if not item['reviewed'] else None,
                   eligible_models=eligible, drafts={})
        for model in MODELS:
            row['drafts'][model] = {}
            for size in manifest['sizes']:
                chunks = [results[model]['chunks'][p['id']] for p in sample['partitions'][str(size)]]
                text = '\n\n'.join(c['text'] for c in chunks)
                can_score = model in eligible
                surface_warning = can_score and kind == 'english' and devanagari(text)
                row['drafts'][model][str(size)] = {
                    'text': text, 'chunks': len(chunks),
                    'metrics': word_errors(basic_tokens(reference), basic_tokens(text)) if can_score else None,
                    'score_status': ('strict_surface_diagnostic' if can_score else
                                     'pending_human_review' if reference is None else 'incompatible_reference_script'),
                    'contains_devanagari': devanagari(text), 'script_surface_warning': surface_warning,
                }
        out['samples'].append(row)
    for model in MODELS:
        samples = [s for s in out['samples'] if model in s['eligible_models']]
        boundary_certain = [s for s in samples if not s['reference_scope_note']]
        script_stable = [s for s in samples if not any(d['script_surface_warning'] for d in s['drafts'][model].values())]
        rows = []
        for size in manifest['sizes']:
            def pooled(panel):
                return aggregate([s['drafts'][model][str(size)]['metrics'] for s in panel])
            rows.append({'size': size, 'aggregate': pooled(samples),
                         'boundary_certain_aggregate': pooled(boundary_certain),
                         'script_stable_aggregate': pooled(script_stable)})
        out['models'][model] = {
            'sample_ids': [s['id'] for s in samples],
            'boundary_certain_sample_ids': [s['id'] for s in boundary_certain],
            'script_stable_sample_ids': [s['id'] for s in script_stable],
            'model_spec': results[model]['model_spec'], 'runtime_config': results[model]['runtime_config'],
            'result_sha256': binding['model_results_sha256'][model], 'rows': rows,
        }
    return out


def ingest(root: Path, source: Path, output: Path) -> dict:
    root, source, output = root.resolve(), source.resolve(), output.resolve()
    if output == root or not output.is_relative_to(root) or output.exists():
        raise ValueError('Choose a new revision directory inside the study')
    if any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Keep private corrections outside OneDrive')
    analysis = build_analysis(root, source)
    output.mkdir(parents=True)
    copied = output / 'source-corrections.json'
    shutil.copyfile(source, copied)
    if file_digest(copied) != analysis['source']['corrections_sha256']:
        raise ValueError('Correction copy changed during intake')
    # The byte-for-byte source copy and immutable upstream files retain all prior evidence.
    write_json(output / 'analysis.json', analysis)
    write_json(output / 'effective-references.json', {
        'version': 1, 'kind': 'chunk_size_effective_references', 'source': analysis['source'],
        'samples': [{k: v for k, v in s.items() if k != 'drafts'} for s in analysis['samples']],
    })
    from render_chunk_corrections import render
    render(root, output / 'comparison.html', analysis)
    return analysis


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--corrections', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = ingest(args.root, args.corrections, args.output)
    print(json.dumps({'output': str(args.output), 'new_reviews': sum(s['uploaded_reviewed'] for s in result['samples']),
                      'retained_reviews': sum(s['reference_source'] == 'prior_checked_reference' for s in result['samples']),
                      'models': {m: {'sample_ids': r['sample_ids'], 'rows': r['rows']} for m, r in result['models'].items()}}))
