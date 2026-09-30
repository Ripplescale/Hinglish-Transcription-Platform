"""Import bound human references for the three fresh boundary-pilot minutes.

Keep source bytes and prior results immutable. No inference, transliteration,
numeric repair, glossary update or change to application defaults is performed.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import read_json, write_json, file_digest
from sttbench.normalization import basic_tokens, word_errors
from score_chunk_corrections import aggregate, devanagari, local_file, ASSESSMENTS

PROFILES = {'apex':[15,20,30], 'trelis':[15,20]}


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1,2,16000):
            raise ValueError('Expected mono PCM16 at 16kHz')
        return audio.readframes(audio.getnframes())


def validate(root: Path, source: Path):
    manifest = read_json(root/'manifest.json')
    summary = read_json(root/'summary.json')
    review = read_json(source)
    results = {m:read_json(root/'results'/f'{m}.json') for m in PROFILES}
    if manifest.get('kind') != 'boundary_pilot' or manifest.get('profiles') != PROFILES:
        raise ValueError('Unexpected pilot or active profiles')
    fresh = [s for s in manifest['samples'] if s['group']=='fresh']
    ids = [s['id'] for s in fresh]
    if len(ids)!=len(set(ids)) or len(ids)!=3:
        raise ValueError('Expected three unique fresh minutes')
    binding = {'manifest_sha256':file_digest(root/'manifest.json'),
        'summary_sha256':file_digest(root/'summary.json'),
        'audio_sha256':{s['id']:s['audio_sha256'] for s in fresh},
        'model_results_sha256':{m:file_digest(root/'results'/f'{m}.json') for m in PROFILES}}
    if review.get('kind')!='boundary_pilot_human_review' or review.get('version')!=1:
        raise ValueError('Expected a boundary-pilot review export, version 1')
    if review.get('binding')!=binding or summary.get('manifest_sha256')!=binding['manifest_sha256']:
        raise ValueError('Review does not match the exact pilot and results')
    if not isinstance(review.get('reviews'),dict) or set(review['reviews'])!=set(ids):
        raise ValueError('Review must include exactly the fresh sample IDs')
    valid_checks = {f'{m}:{s}' for m,sizes in PROFILES.items() for s in sizes}
    summary_samples = {s['id']:s for s in summary['samples']}
    if len(summary_samples)!=len(summary['samples']): raise ValueError('Duplicate summary sample')
    for model,result in results.items():
        expected = {p['id'] for s in manifest['samples'] for key,parts in s['partitions'].items()
                    if int(key.split(':')[0]) in PROFILES[model] for p in parts}
        if (result.get('kind')!='boundary_pilot_results' or result.get('state')!='complete'
            or result.get('model_id')!=model or result.get('manifest_sha256')!=binding['manifest_sha256']
            or result.get('network_attempts')!=[] or set(result.get('chunks',{}))!=expected
            or summary['models'][model].get('state')!='complete'
            or summary['models'][model].get('result_sha256')!=binding['model_results_sha256'][model]):
            raise ValueError('Incomplete or inconsistent model results')
    for sample in fresh:
        item = review['reviews'][sample['id']]
        if not isinstance(item,dict) or type(item.get('reviewed')) is not bool:
            raise ValueError('Invalid review state')
        if not all(isinstance(item.get(k),str) for k in ('reference','notes')):
            raise ValueError('Reference and notes must be text')
        if item['reviewed'] and not basic_tokens(item['reference']):
            raise ValueError('A reviewed reference must contain words')
        checks = item.get('draft_checks',{})
        if not isinstance(checks,dict) or any(k not in valid_checks or v not in ASSESSMENTS for k,v in checks.items()):
            raise ValueError('Unknown draft assessment or profile')
        parent = local_file(root,sample['audio'])
        if file_digest(parent)!=sample['audio_sha256']: raise ValueError('Parent audio changed')
        original_pcm = pcm(parent)
        for model,sizes in PROFILES.items():
            for size in sizes:
                parts = sample['partitions'][f'{size}:aligned']
                joined, texts, cursor = [], [], 0
                for part in parts:
                    audio = local_file(root,part['audio'])
                    raw = pcm(audio)
                    if file_digest(audio)!=part['audio_sha256']: raise ValueError('Partition audio changed')
                    start, end = round(part['start_seconds']*16000), round(part['end_seconds']*16000)
                    if start!=cursor or end<=start or len(raw)!=2*(end-start):
                        raise ValueError('Partition time/length mismatch')
                    cursor=end; joined.append(raw)
                    pred=results[model]['chunks'][part['id']]
                    if (pred.get('status')!='ok' or pred.get('audio_sha256')!=part['audio_sha256']
                        or pred.get('sample_id')!=sample['id'] or pred.get('size')!=size
                        or pred.get('condition')!='aligned' or not isinstance(pred.get('text'),str)):
                        raise ValueError('Missing or unbound model output')
                    texts.append(pred['text'])
                if b''.join(joined)!=original_pcm: raise ValueError('Partition PCM does not reconstruct parent')
                displayed=summary_samples[sample['id']]['drafts'][f'{model}:{size}:aligned']
                if displayed.get('complete') is not True or displayed.get('text')!='\n\n'.join(texts):
                    raise ValueError('Summary differs from raw recognition output')
    return manifest,summary,review,results,binding


def token_alignment(reference, hypothesis):
    """Deterministic minimum-edit alignment with the same S/D/I tie order as WER."""
    costs=[[j for j in range(len(hypothesis)+1)]]
    for i,a in enumerate(reference,1):
        row=[i]
        for j,b in enumerate(hypothesis,1):
            row.append(costs[i-1][j-1] if a==b else min(costs[i-1][j-1],costs[i-1][j],row[-1])+1)
        costs.append(row)
    edits=[]; i=len(reference); j=len(hypothesis)
    while i or j:
        if i and j and reference[i-1]==hypothesis[j-1] and costs[i][j]==costs[i-1][j-1]:
            kind='equal'; a=reference[i-1]; b=hypothesis[j-1]; i-=1;j-=1
        elif i and j and costs[i][j]==costs[i-1][j-1]+1:
            kind='substitute';a=reference[i-1];b=hypothesis[j-1];i-=1;j-=1
        elif i and costs[i][j]==costs[i-1][j]+1:
            kind='delete';a=reference[i-1];b=None;i-=1
        else:
            kind='insert';a=None;b=hypothesis[j-1];j-=1
        edits.append({'operation':kind,'reference':a,'hypothesis':b})
    return list(reversed(edits))


def literal_count(text, target):
    tokens, term=basic_tokens(text),basic_tokens(target)
    return sum(tokens[i:i+len(term)]==term for i in range(len(tokens)-len(term)+1)) if term else 0


def build_analysis(root: Path, source: Path):
    manifest,summary,review,results,binding=validate(root,source)
    out={'version':1,'kind':'boundary_pilot_corrected_evaluation','created_at':datetime.now(timezone.utc).isoformat(),
        'source':{'corrections_sha256':file_digest(source),'corrections_filename':source.name,
                  'exported_at':review.get('exported_at'),'binding':binding},
        'profiles':PROFILES,'samples':[],'models':{},'held_out':False,'release_qualified':False,'trelis_romanization':False,
        'metric_policy':{'normalization':'NFC, casefold, punctuation separation; preserve script and numeric representation.',
            'panels':'Mixed Devanagari/Latin references score Trelis; Latin-only references score Apex. No inferred translation or transliteration.',
            'limits':'Strict surface WER is not semantic accuracy/completeness. Names can be correct in another script. Digits and number words differ. Assisted development review, not blind held-out validation.',
            'draft_checks':'User listening assessments remain independent of reference-alignment diagnostics.',
            'entities':'Literal occurrence counts for names explicitly reported in review notes; no alias expansion, no semantic entity accuracy claim.'},
        'code_sha256':{'scorer':file_digest(Path(__file__)),'normalizer':file_digest(LAB/'sttbench/normalization.py')},
        'validation':{'bindings_match':True,'all_fresh_pcm_partitions_exact':True,'all_displayed_drafts_match_raw_outputs':True}}
    summaries={s['id']:s for s in summary['samples']}
    for sample in manifest['samples']:
        if sample['group']!='fresh':continue
        item=review['reviews'][sample['id']]
        reference=item['reference'] if item['reviewed'] else None
        kind='pending' if reference is None else 'native_mixed' if devanagari(reference) else 'latin_script'
        eligible=[] if reference is None else ['trelis' if kind=='native_mixed' else 'apex']
        row={k:copy.deepcopy(sample[k]) for k in ('id','call_title','source_start_seconds','duration_seconds','audio','audio_sha256')}
        row.update(reference=reference,reference_kind=kind,reference_source='uploaded_review' if item['reviewed'] else 'pending',
            reviewed=item['reviewed'],reference_provenance=copy.deepcopy(item.get('reference_provenance')),
            unreviewed_reference_draft=None if item['reviewed'] else item['reference'],notes=item['notes'],
            draft_checks=copy.deepcopy(item.get('draft_checks',{})),eligible_models=eligible,drafts={})
        for model,sizes in PROFILES.items():
            for size in sizes:
                key=f'{model}:{size}'
                text=summaries[sample['id']]['drafts'][key+':aligned']['text']
                metrics=word_errors(basic_tokens(reference),basic_tokens(text)) if model in eligible else None
                row['drafts'][key]={'text':text,'metrics':metrics,'contains_devanagari':devanagari(text),
                    'score_status':'strict_surface_diagnostic' if metrics else 'pending_human_review' if reference is None else 'incompatible_reference_script',
                    'alignment':token_alignment(basic_tokens(reference),basic_tokens(text)) if metrics else None,
                    'chunks':[{'id':p['id'],'start_seconds':p['start_seconds'],'end_seconds':p['end_seconds'],
                               'text':results[model]['chunks'][p['id']]['text']} for p in sample['partitions'][f'{size}:aligned']]}
        reported=re.search(r'Names (?:incorrect/missing|missing/incorrect)\s*-\s*([^\n]+)',item['notes'],re.I)
        row['reported_name_targets']=[{'text':name.strip(),'source':'review_notes',
            'literal_counts':{key:literal_count(d['text'],name.strip()) for key,d in row['drafts'].items()}}
            for name in reported.group(1).split(',') if name.strip()] if reported else []
        out['samples'].append(row)
    for model,sizes in PROFILES.items():
        panel=[s for s in out['samples'] if model in s['eligible_models']]
        prior=[s for s in summary['samples'] if s.get('group')=='checked' and model in s.get('eligible_models',[])]
        combined=[]
        for size in sizes:
            prior_metrics=[]
            for sample in prior:
                draft=sample['drafts'][f'{model}:{size}:aligned']
                metric=word_errors(basic_tokens(sample['reference']),basic_tokens(draft['text']))
                if metric!=draft['metrics']: raise ValueError('Prior reference metric uses a different policy')
                prior_metrics.append(metric)
            combined.append({'size':size,'aggregate':aggregate(prior_metrics+[s['drafts'][f'{model}:{size}']['metrics'] for s in panel])})
        out['models'][model]={'sample_ids':[s['id'] for s in panel],
            'model_spec':results[model]['model_spec'],'runtime_config':results[model]['runtime_config'],
            'rows':[{'size':size,'aggregate':aggregate([s['drafts'][f'{model}:{size}']['metrics'] for s in panel])} for size in sizes],
            'combined_aligned':{'sample_ids':[s['id'] for s in prior]+[s['id'] for s in panel],
                'prior_sample_ids':[s['id'] for s in prior],'rows':combined,
                'scope':'Prior checked + new reviewed passages, aligned cuts only; previously exposed development calls.'}}
    out['findings']=['The checked references and listening assessments are preserved independently. A looks_complete selection does not override missing words found against the checked reference.',
        'Every input chunk reconstructs its exact parent audio, and every displayed draft matches raw recognition output. Missing text and repetition in these drafts therefore precede assembly/display.']
    for model,report in out['models'].items():
        if report['sample_ids']:
            figures=', '.join(f"{r['size']}s: {r['aggregate']['wer']:.2%}" for r in report['rows'])
            out['findings'].append(f"Fresh {model.title()} strict surface word error over {len(report['sample_ids'])} reviewed passages: {figures}.")
            combined=report['combined_aligned']
            if combined['prior_sample_ids']:
                figures=', '.join(f"{r['size']}s: {r['aggregate']['wer']:.2%}" for r in combined['rows'])
                out['findings'].append(f"Including prior checked aligned passages, {model.title()} has {len(combined['sample_ids'])} passages: {figures}. This is not an untouched test set or proof of a universal optimum.")
    return out


def ingest(root: Path, source: Path, output: Path):
    root,source,output=root.resolve(),source.resolve(),output.resolve()
    if output==root or not output.is_relative_to(root) or output.exists():
        raise ValueError('Choose a new revision directory inside the pilot')
    if any(p.lower().startswith('onedrive') for p in output.parts):
        raise ValueError('Keep private review data outside OneDrive')
    analysis=build_analysis(root,source)
    output.mkdir(parents=True)
    copied=output/'source-corrections.json';shutil.copyfile(source,copied)
    if file_digest(copied)!=analysis['source']['corrections_sha256']: raise ValueError('Source changed during copy')
    write_json(output/'analysis.json',analysis)
    write_json(output/'effective-references.json',{'version':1,'kind':'boundary_pilot_effective_references',
        'source':analysis['source'],'samples':[{k:v for k,v in s.items() if k!='drafts'} for s in analysis['samples']]})
    # Keep exact reproduction sources alongside evidence rather than depending
    # on a future mutable checkout to match today's source hashes.
    reproduction=output/'reproduction';reproduction.mkdir()
    for path in (Path(__file__),LAB/'tools/score_chunk_corrections.py',LAB/'tools/render_boundary_corrections.py',
                 LAB/'sttbench/manifest.py',LAB/'sttbench/normalization.py'):
        shutil.copyfile(path,reproduction/path.name)
    write_json(reproduction/'source-hashes.json',{p.name:file_digest(p) for p in reproduction.iterdir() if p.is_file()})
    from render_boundary_corrections import render
    render(root,output/'comparison.html',analysis)
    return analysis


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--corrections',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report=ingest(a.root,a.corrections,a.output)
    import json
    print(json.dumps({'output':str(a.output),'reviewed':sum(s['reviewed'] for s in report['samples']),
        'models':{m:{'sample_ids':r['sample_ids'],'rows':r['rows']} for m,r in report['models'].items()}}))
