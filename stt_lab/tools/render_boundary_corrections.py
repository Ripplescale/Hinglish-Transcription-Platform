"""Render checked boundary-pilot corrections as a private offline readback.

No inference, normalization, transliteration or editing is performed here. Raw
model drafts are verified against the immutable pilot summary before embedding.
"""
from __future__ import annotations

import argparse
import base64
import copy
from html import escape
import json
from pathlib import Path
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json
from prepare_expanded_review import inside

PROFILES = {"apex": [15, 20, 30], "trelis": [15, 20]}


def metric_table(model: str, report: dict) -> str:
    rows = []
    for row in report["rows"]:
        score = row["aggregate"]
        count = score["reference_words"]
        cells = [f"{row['size']}s", f"{score['wer']:.1%}" if score["wer"] is not None else "Not scored",
                 str(count), str(score["deletions"]) if count else "—",
                 str(score["substitutions"]) if count else "—", str(score["insertions"]) if count else "—"]
        rows.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
    return ('<div class="table-wrap" tabindex="0" role="region" aria-label="' + model + ' score table">'
            '<table><caption class="sr-only">' + model + ' strict surface word error</caption><thead><tr>'
            '<th scope="col">Chunk</th><th scope="col">Surface WER</th><th scope="col">Ref. words</th>'
            '<th scope="col">Deleted</th><th scope="col">Changed</th><th scope="col">Added</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')


CSS = r"""
*{box-sizing:border-box}body{margin:0;background:#f4f2ec;color:#19342d;font:16px/1.65 system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:38px 28px 70px}h1{font-size:38px;line-height:1.18;margin:8px 0 18px}h2{font-size:23px;line-height:1.35;margin:0 0 10px}h3{font-size:18px;line-height:1.4;margin:0 0 12px}p{margin:10px 0}.eyebrow{color:#805a30;font-size:12px;letter-spacing:.1em;font-weight:750}.muted,.small{font-size:14px}.muted{color:#596c63}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{background:#fff;border:1px solid #d9ded4;border-radius:12px;padding:23px;min-width:0}.notice{background:#fff9e9;border-left:4px solid #b78c46;padding:12px 17px;margin:20px 0}.score-grid{margin:26px 0 18px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px;min-width:430px}th,td{padding:9px 7px;text-align:left;border-bottom:1px solid #d9e0d9}th{font-size:12px}details{background:#e9eee6;border-radius:8px;padding:16px 20px;margin:20px 0}summary{cursor:pointer;font-weight:650}.toolbar{display:flex;gap:12px;align-items:end;flex-wrap:wrap;padding:18px;background:#e4ece4;border-radius:10px;margin:30px 0 22px}.grow{flex:1;min-width:240px}label{font-size:13px;font-weight:650;display:block}select,button{border:1px solid #a5b7a8;background:white;color:#153a2d;font:inherit;border-radius:6px;padding:8px 10px}select{display:block;width:100%;margin-top:5px}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}:focus-visible{outline:3px solid #317daf;outline-offset:3px}.source{overflow-wrap:anywhere}.player{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:18px 0}audio{flex:1;min-width:200px;width:100%}.player select{width:110px}.transcript,.reference,.notes{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}.reference-card{margin:20px 0;background:#fffdf4}.reference{font-size:17px}.draft{background:#f4f7f1}.draft .transcript{margin-top:18px}.assessment{font-size:13px;padding:8px 10px;background:#e6eee4;border-radius:6px}.status{font-size:13px;color:#52695c}.metric{font-size:14px;color:#3f5b4b}.boundaries{font-size:12px;color:#5c6e62;overflow-wrap:anywhere}.badge{display:inline-block;border:1px solid #c4d4c5;background:#eef3e9;padding:3px 9px;border-radius:20px;font-size:12px;margin-top:8px}.hash{font-family:Consolas,monospace;font-size:12px;overflow-wrap:anywhere}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}.section{margin-top:30px}.checks{font-size:14px;white-space:pre-wrap}footer{margin-top:32px}.findings li{margin:7px 0}.intro p{max-width:95ch}@media(max-width:760px){main{padding:23px 13px 55px}h1{font-size:30px}.grid{grid-template-columns:1fr}.card{padding:18px 15px}.grow{min-width:0;width:100%;flex-basis:100%}.toolbar{padding:14px}.player audio{flex-basis:100%}.source,h2{overflow-wrap:anywhere}}@media print{body{background:white}main{padding:0}.toolbar,.player,button{display:none}.card{break-inside:avoid}.grid{display:block}.card{margin:15px 0}}
"""

JS = r"""
'use strict';
const D=JSON.parse(document.getElementById('study-data').textContent),el=id=>document.getElementById(id);
const profiles=['trelis:15','trelis:20','apex:15','apex:20','apex:30'];let current=0;
const checkLabels={looks_complete:'Looks complete after listening',missing_speech:'Missing speech',unclear:'Unclear',not_checked:'Not assessed'};
const kindLabels={native_mixed:'Mixed Devanagari / English reference',english:'English reference',roman_hinglish:'Roman Hinglish reference',pending:'Reference pending'};
function clock(s){const n=Math.round(s*1000);return String(Math.floor(n/60000)).padStart(2,'0')+':'+String(Math.floor(n%60000/1000)).padStart(2,'0')+'.'+String(n%1000).padStart(3,'0')}
function title(p){const [m,n]=p.split(':');return (m==='trelis'?'Trelis · original script':'Apex · Roman Hinglish')+' · '+n+' seconds'}
function drawSide(side){const s=D.samples[current],p=el(side+'-profile').value,d=s.drafts[p];el(side+'-text').textContent=d.text;el(side+'-status').textContent=(d.score_status||'not_scored').replaceAll('_',' ');const m=d.metrics;el(side+'-metric').textContent=m&&m.wer!==null?'Surface WER '+(m.wer*100).toFixed(1)+'% · '+m.deletions+' deleted · '+m.substitutions+' changed · '+m.insertions+' added · '+m.reference_words+' reference words':'No compatible checked reference for this draft. Output length is not a completeness measure.';el(side+'-assessment').textContent='Your listening check: '+(checkLabels[s.draft_checks?.[p]||'not_checked']||s.draft_checks[p])+'. This applies only to this profile.';el(side+'-boundaries').textContent='Audio cuts within this minute: '+(d.parts||[]).map(x=>clock(x.start_seconds)+'–'+clock(x.end_seconds)).join(' · ')}
function draw(){const s=D.samples[current];el('sample-select').value=s.id;el('sample-title').textContent='Minute '+(current+1)+' of '+D.samples.length;el('sample-source').textContent=s.call_title+' · '+clock(s.source_start_seconds)+'–'+clock(s.source_start_seconds+s.duration_seconds)+' in decoded recording';el('reference-kind').textContent=kindLabels[s.reference_kind]||s.reference_kind;el('reference-text').textContent=s.reference;el('reference-status').textContent=s.reviewed?'Marked reviewed in your uploaded file':'Not marked reviewed in your uploaded file';el('notes-text').textContent=s.notes||'No notes supplied for this minute.';el('all-checks').textContent=profiles.map(p=>title(p)+': '+(checkLabels[s.draft_checks?.[p]||'not_checked']||s.draft_checks[p])).join('\n');const origin=s.reference_provenance;el('reference-provenance').textContent=origin?.source==='copied_machine_draft'?'Reference started from '+title(origin.profile)+', then was saved with your edits and review status.':'Reference provenance: '+(origin?JSON.stringify(origin):'No machine-draft origin recorded.');const a=el('audio');a.pause();a.src=s.audio_uri;a.playbackRate=Number(el('speed').value);el('previous').disabled=current===0;el('next').disabled=current===D.samples.length-1;drawSide('left');drawSide('right')}
for(const [i,s] of D.samples.entries()){const o=document.createElement('option');o.value=s.id;o.textContent='Minute '+(i+1)+' · '+s.call_title;el('sample-select').append(o)}
for(const side of ['left','right']){for(const p of profiles){const o=document.createElement('option');o.value=p;o.textContent=title(p);el(side+'-profile').append(o)}el(side+'-profile').value=side==='left'?'trelis:15':'trelis:20';el(side+'-profile').addEventListener('change',()=>drawSide(side))}
el('sample-select').addEventListener('change',()=>{current=D.samples.findIndex(s=>s.id===el('sample-select').value);draw()});el('previous').addEventListener('click',()=>{current--;draw()});el('next').addEventListener('click',()=>{current++;draw()});el('back').addEventListener('click',()=>el('audio').currentTime=Math.max(0,el('audio').currentTime-5));el('forward').addEventListener('click',()=>el('audio').currentTime=Math.min(D.samples[current].duration_seconds,el('audio').currentTime+5));el('speed').addEventListener('change',()=>el('audio').playbackRate=Number(el('speed').value));draw();
"""


def panel(side: str) -> str:
    return f'''<article class="card draft"><label for="{side}-profile">Saved model output<select id="{side}-profile"></select></label>
<p class="status" id="{side}-status"></p><p class="metric" id="{side}-metric"></p>
<p class="assessment" id="{side}-assessment"></p><div class="transcript" id="{side}-text" dir="auto"></div>
<p class="boundaries" id="{side}-boundaries"></p></article>'''


def render(root: Path, output: Path, analysis: dict) -> None:
    root = root.resolve(strict=True)
    output = output.resolve()
    if output.exists() or not output.is_relative_to(root) or any(p.casefold().startswith('onedrive') for p in output.parts):
        raise ValueError('Choose a new private output filename inside the pilot, outside OneDrive')
    if analysis['profiles'] != PROFILES:
        raise ValueError('Unexpected model profiles')
    binding = analysis['source']['binding']
    for filename in ('manifest', 'summary'):
        if file_digest(root / f'{filename}.json') != binding[f'{filename}_sha256']:
            raise ValueError(f'Pilot {filename} binding changed')
    summary = read_json(root / 'summary.json')
    if summary['manifest_sha256'] != binding['manifest_sha256']:
        raise ValueError('Summary was built for another manifest')
    for model in PROFILES:
        digest = file_digest(root / 'results' / f'{model}.json')
        if digest != binding['model_results_sha256'][model] or summary['models'][model]['result_sha256'] != digest:
            raise ValueError(f'{model} results binding changed')
        if summary['models'][model]['state'] != 'complete':
            raise ValueError('Incomplete model results cannot be reviewed')
    originals = {s['id']: s for s in summary['samples'] if s['group'] == 'fresh'}
    if len(analysis['samples']) != 3 or len(originals) != 3 or {s['id'] for s in analysis['samples']} != set(originals):
        raise ValueError('Expected the exact three fresh pilot minutes')
    data = copy.deepcopy(analysis)
    for sample in data['samples']:
        original = originals[sample['id']]
        for key in ('call_title', 'source_start_seconds', 'duration_seconds', 'audio', 'audio_sha256'):
            if sample[key] != original[key]:
                raise ValueError(f'Changed sample metadata: {sample["id"]} / {key}')
        audio = inside(root, sample['audio'])
        if file_digest(audio) != sample['audio_sha256'] or sample['audio_sha256'] != binding['audio_sha256'][sample['id']]:
            raise ValueError('Audio binding changed')
        with wave.open(str(audio), 'rb') as stream:
            if abs(stream.getnframes() / stream.getframerate() - sample['duration_seconds']) > .001:
                raise ValueError('Audio duration mismatch')
        sample['audio_uri'] = 'data:audio/wav;base64,' + base64.b64encode(audio.read_bytes()).decode('ascii')
        for model, sizes in PROFILES.items():
            for size in sizes:
                key = f'{model}:{size}'
                saved = original['drafts'][key + ':aligned']
                if sample['drafts'][key]['text'] != saved['text']:
                    raise ValueError('Analysis draft differs from literal saved model output')
                sample['drafts'][key]['parts'] = saved['parts']
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for literal, escaped in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        encoded = encoded.replace(literal, escaped)
    findings = ''.join(f'<li>{escape(str(item))}</li>' for item in analysis.get('findings', []))
    findings_html = f'<section class="notice findings"><h2>What changed with your review</h2><ul>{findings}</ul></section>' if findings else ''
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src data:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Three checked minutes · Apex and Trelis</title><style>{CSS}</style></head><body><main>
<header class="intro"><div class="eyebrow">HINGLISH STT · YOUR NEW REVIEW</div><h1>Three checked minutes.<br>What the saved models got right and wrong.</h1>
<p>Your reference text, notes and per-profile listening checks appear exactly as saved in the uploaded correction file. Model drafts are unchanged. Trelis keeps its original Devanagari/English output.</p>
<span class="badge">Local read-only comparison · no new inference</span></header>{findings_html}
<section class="grid score-grid" aria-label="Separate model results"><article class="card" id="trelis-results"><h2>Trelis · original script</h2>
<p class="muted">{len(data['models']['trelis']['sample_ids'])} compatible checked references. These are strict surface word-error diagnostics.</p>{metric_table('Trelis', data['models']['trelis'])}</article>
<article class="card" id="apex-results"><h2>Apex · Roman Hinglish</h2><p class="muted">{len(data['models']['apex']['sample_ids'])} compatible checked references. Mixed-script references cannot fairly score Roman Hinglish drafts without separately checked Roman references.</p>{metric_table('Apex', data['models']['apex'])}</article></section>
<p class="notice small"><strong>Surface WER is not semantic accuracy.</strong> Script differences, alternative spellings and number words versus digits can all count as errors. Punctuation is excluded. Word-alignment deletions are not a percentage of spoken meaning omitted. Compare Trelis settings on the same panel; these tables do not rank Apex against Trelis.</p>
<details id="methods"><summary>What this comparison establishes</summary><p class="small">This report rescores existing aligned-cut predictions using your checked references. It does not rerun a model or change its decoding. The excerpts came from previously exposed development calls and were reviewed with machine drafts visible; they are not a blind or held-out test.</p>
<p class="small">Your “looks complete” check applies only to its named profile. It does not confirm every word, name or quantity. The reference and notes are reproduced literally; uncertain terms and spelling choices remain visible. No glossary correction, transliteration, number conversion, or automatic factual replacement has been applied.</p>
<p class="small">All five options process the same full minute in disjoint audio windows. The displayed cuts are relative to the minute; the recording range uses the existing decoded audio clock. Live latency, release accuracy and a universal optimum are not established here.</p></details>
<section class="section" aria-labelledby="listen-heading"><h2 id="listen-heading">Listen against your checked text</h2>
<div class="toolbar"><label class="grow" for="sample-select">Recording excerpt<select id="sample-select"></select></label><button id="previous" type="button">Previous minute</button><button id="next" type="button">Next minute</button></div>
<h2 id="sample-title"></h2><p class="source muted" id="sample-source"></p><div class="player"><audio id="audio" controls preload="metadata"></audio><button id="back" type="button" aria-label="Back five seconds">−5s</button><button id="forward" type="button" aria-label="Forward five seconds">+5s</button><label for="speed">Playback speed<select id="speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option></select></label></div>
<article class="card reference-card"><h3>Your checked reference</h3><p class="muted" id="reference-kind"></p><p class="status" id="reference-status"></p><div class="reference" id="reference-text" dir="auto"></div><p class="status" id="reference-provenance"></p></article>
<article class="card"><h3>Your notes</h3><div class="notes" id="notes-text" dir="auto"></div><details><summary>All saved listening checks</summary><div class="checks" id="all-checks"></div></details></article>
<div class="grid section">{panel('left')}{panel('right')}</div></section>
<footer><p class="muted">All three audio excerpts and fifteen model drafts are embedded. This file makes no network requests, changes no saved references, and uploads nothing. Keep the original editable review for future corrections.</p>
<details><summary>Source binding</summary><p class="small">Correction file: {escape(data['source']['corrections_filename'])}</p><p class="hash">Correction SHA-256: {escape(data['source']['corrections_sha256'])}</p><p class="hash">Manifest SHA-256: {escape(binding['manifest_sha256'])}</p><p class="hash">Summary SHA-256: {escape(binding['summary_sha256'])}</p></details></footer>
</main><script type="application/json" id="study-data">{encoded}</script><script>{JS}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(page)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.root, args.output, read_json(args.analysis))
    print(json.dumps({'html': str(args.output.resolve()), 'sha256': file_digest(args.output)}))
