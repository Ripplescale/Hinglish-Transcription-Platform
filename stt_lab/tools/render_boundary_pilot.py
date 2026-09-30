"""Render a completed pilot into an offline, editable three-minute review."""
from __future__ import annotations
import argparse
import base64
import html
import json
from pathlib import Path
import sys

LAB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(LAB))
from sttbench.manifest import read_json, file_digest
from prepare_expanded_review import inside


def render(root, output):
    summary=read_json(root/'summary.json')
    manifest=read_json(root/'manifest.json')
    if summary['manifest_sha256']!=file_digest(root/'manifest.json'):
        raise ValueError('Summary does not match manifest')
    if output.exists() or not output.resolve().is_relative_to(root.resolve()):
        raise ValueError('Choose a new HTML filename inside the private pilot')
    for model,report in summary['models'].items():
        if report['state']!='complete' or report['result_sha256']!=file_digest(root/'results'/f'{model}.json'):
            raise ValueError('Only completed, immutable model results can be reviewed')
    fresh=[s for s in summary['samples'] if s['group']=='fresh']
    binding={'manifest_sha256':file_digest(root/'manifest.json'), 'summary_sha256':file_digest(root/'summary.json'),
             'audio_sha256':{s['id']:s['audio_sha256'] for s in fresh},
             'model_results_sha256':{m:r['result_sha256'] for m,r in summary['models'].items()}}
    rows=[]
    for model,report in summary['models'].items():
        for row in report['rows']:
            score=row['aggregate']
            rows.append(f"<tr><td>{model.title()} {row['size']}s</td><td>{row['condition']}</td><td>{score['wer']:.1%}</td><td>{score['deletions']}</td><td>{score['substitutions']}</td><td>{score['insertions']}</td></tr>")
    cards=[]
    def clock(sec):return f'{int(sec)//60:02}:{int(sec)%60:02}'
    for index,sample in enumerate(fresh,1):
        path=inside(root,sample['audio'])
        if file_digest(path)!=sample['audio_sha256']:raise ValueError('Review audio changed')
        audio=base64.b64encode(path.read_bytes()).decode('ascii')
        cards.append(f'''<section class="minute" id="{sample['id']}" data-id="{sample['id']}">
<div class="eyebrow">MINUTE {index} OF 3 · UNREVIEWED MACHINE DRAFTS</div>
<h2>{html.escape(sample['call_title'])}</h2><p class="meta">Recording time {clock(sample['source_start_seconds'])}–{clock(sample['source_start_seconds']+60)} · expected {html.escape(sample['expected_language'])}, needs confirmation</p>
<audio controls preload="metadata" src="data:audio/wav;base64,{audio}"></audio>
<div class="compare"><article class="draft" data-side="left"><label>Compare output <select class="profile"></select></label><div class="transcript"></div><label>Listening assessment <select class="assessment"><option value="not_checked">Not checked</option><option value="looks_complete">Looks complete</option><option value="missing_speech">Missing speech</option><option value="unclear">Unclear</option></select></label><button class="use-draft" type="button">Copy into reference draft</button></article>
<article class="draft" data-side="right"><label>Compare output <select class="profile"></select></label><div class="transcript"></div><label>Listening assessment <select class="assessment"><option value="not_checked">Not checked</option><option value="looks_complete">Looks complete</option><option value="missing_speech">Missing speech</option><option value="unclear">Unclear</option></select></label><button class="use-draft" type="button">Copy into reference draft</button></article></div>
<label class="reference-label">What was actually said — preserve whichever script you prefer<textarea class="reference" rows="8" placeholder="Listen, then write or correct the reference here. A copied draft is not a checked reference."></textarea></label>
<label>Names, quantities, interruptions, punctuation, or uncertain words<textarea class="notes" rows="3"></textarea></label>
<label class="check"><input class="reviewed" type="checkbox"> I listened and completed this reference</label>
</section>''')
    data={'binding':binding,'samples':fresh,'profiles':['trelis:15','trelis:20','apex:15','apex:20','apex:30']}
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src data:; img-src data:; connect-src 'none'"><title>Three fresh minutes · Apex and Trelis</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f3ed;color:#182c2b;font:16px/1.65 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:36px 28px 80px}header{margin-bottom:28px}h1{font-size:38px;line-height:1.15;margin:8px 0 18px}h2{font-size:21px;line-height:1.35;margin:10px 0}p{max-width:85ch}.eyebrow{font-size:12px;font-weight:750;letter-spacing:.1em;color:#76502c}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:#e4ece6;padding:16px;border-radius:10px;position:sticky;top:0;z-index:2}.toolbar a{color:#164b43}button,.file-label{border:1px solid #a7b8ad;background:white;color:#183d36;padding:9px 13px;border-radius:6px;cursor:pointer;font:inherit}button.primary{background:#20574c;color:white}.minute{background:white;padding:28px;margin-top:28px;border-radius:14px;border:1px solid #d9ded4;scroll-margin-top:100px}.meta{color:#61716b;font-size:14px}audio{width:100%;margin:8px 0 22px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:18px}.draft{background:#f3f6f1;padding:18px;border:1px solid #d8e0d8;border-radius:10px}.draft label{font-size:13px}.transcript{white-space:pre-wrap;line-height:1.85;min-height:180px;margin:18px 0}.draft button{font-size:13px;margin-top:10px}select{width:100%;font:inherit;background:white;border:1px solid #a5b5ab;border-radius:6px;padding:8px}label{display:block;font-weight:600}textarea{display:block;width:100%;margin:8px 0 20px;border:1px solid #a5b5ab;border-radius:6px;padding:13px;resize:vertical;font:16px/1.75 system-ui,sans-serif;color:#152f28}.reference-label{margin-top:24px}.check{display:flex;gap:10px;align-items:center}.check input{width:19px;height:19px}.status{font-size:14px;color:#375749}.notice{border-left:4px solid #b98944;padding:8px 16px;background:#fff9ea}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}th,td{text-align:left;border-bottom:1px solid #d8ded5;padding:8px}summary{cursor:pointer;font-weight:650}details{background:#ecf0e7;padding:18px;border-radius:10px;margin:22px 0}a{color:#1c6555}.file-label input{display:none}@media(max-width:700px){main{padding:22px 12px 60px}h1{font-size:30px}.minute{padding:18px 14px}.compare{grid-template-columns:1fr}.toolbar{position:static}.transcript{min-height:0}h2{overflow-wrap:anywhere}}</style></head><body><main>
<header><div class="eyebrow">PRIVATE LOCAL REVIEW · 30 SEPTEMBER 2026</div><h1>Three fresh minutes.<br>Five processing options.</h1><p>Listen to one minute at a time. Trelis keeps its original Hindi/English script. Compare Trelis 15/20 seconds and Apex 15/20/30 seconds using the selectors. Your checked reference helps validate automatic processing; it is not a requirement to manually rewrite every future call.</p><p class="notice">These are new excerpts from previously exposed development calls, not an untouched final test. Language mix, names, quantities and interruptions still need listening confirmation. Times use the existing decoded PCM sample clock.</p></header>
<div class="toolbar"><button class="primary" id="export">Export corrections JSON</button><label class="file-label">Import saved corrections<input id="import" type="file" accept="application/json,.json"></label><a href="#fresh-call-03">Minute1</a><a href="#fresh-call-05">Minute2</a><a href="#fresh-call-06">Minute3</a><span id="status" class="status" role="status"></span></div>
<details><summary>Boundary-shift development results</summary><p>Aligned cuts are compared with a five-second initial partial chunk followed by nominal windows. Every setting covers the exact same complete parent audio with no overlap, deduplication or rewriting. The reference panels differ: Apex has five checked passages; Trelis has three. Compare settings within a model only. The first short excerpt has a user-assumed cutoff; sensitivity results excluding it are preserved in summary.json. English references containing a Devanagari name are script-sensitive surface scores.</p><div class="table-wrap"><table><thead><tr><th>Profile</th><th>Cut alignment</th><th>Word error</th><th>Deletions</th><th>Substitutions</th><th>Insertions</th></tr></thead><tbody>__ROWS__</tbody></table></div><p>Punctuation is excluded from word error. Deletions are word-alignment diagnostics, not semantic completeness. Observed serial processing times are not live update latency.</p></details>
__CARDS__<p class="meta">All audio and drafts are embedded. No network requests or uploads. Download the corrections JSON as your durable copy; browser storage is only a convenience.</p></main>
<script id="data" type="application/json">__DATA__</script>
<script>
'use strict';const D=JSON.parse(document.getElementById('data').textContent);const byId=Object.fromEntries(D.samples.map(s=>[s.id,s]));const checks=new Set(['not_checked','looks_complete','missing_speech','unclear']);const key='boundary-pilot:'+D.binding.manifest_sha256+':'+D.binding.summary_sha256;const status=document.getElementById('status');let state=Object.fromEntries(D.samples.map(s=>[s.id,{reference:'',notes:'',reviewed:false,draft_checks:{},reference_provenance:null}]));
const canonical=x=>JSON.stringify(sortObject(x));function sortObject(x){if(Array.isArray(x))return x.map(sortObject);if(x&&typeof x==='object')return Object.fromEntries(Object.keys(x).sort().map(k=>[k,sortObject(x[k])]));return x}
function validate(v){if(v.kind!=='boundary_pilot_human_review'||v.version!==1||canonical(v.binding)!==canonical(D.binding))throw Error('This export belongs to different audio or model results.');if(canonical(Object.keys(v.reviews).sort())!==canonical(Object.keys(byId).sort()))throw Error('Incorrect review IDs.');for(const r of Object.values(v.reviews)){if(typeof r.reference!=='string'||typeof r.notes!=='string'||typeof r.reviewed!=='boolean'||(r.reviewed&&!r.reference.trim()))throw Error('Invalid reference state.');if(!r.draft_checks||typeof r.draft_checks!=='object'||Array.isArray(r.draft_checks)||Object.entries(r.draft_checks).some(([k,v])=>!D.profiles.includes(k)||!checks.has(v)))throw Error('Invalid draft assessment.');}return v.reviews}
function doc(){return{kind:'boundary_pilot_human_review',version:1,binding:D.binding,exported_at:new Date().toISOString(),reviews:state}}
function save(){try{localStorage.setItem(key,JSON.stringify(doc()));status.textContent='Saved in this browser'}catch(e){status.textContent='Browser storage unavailable — export JSON to save.'}}
function populate(){for(const section of document.querySelectorAll('.minute')){const r=state[section.dataset.id];section.querySelector('.reference').value=r.reference;section.querySelector('.notes').value=r.notes;section.querySelector('.reviewed').checked=r.reviewed;for(const card of section.querySelectorAll('.draft'))showDraft(section,card)}}
function showDraft(section,card){const p=card.querySelector('.profile').value;card.querySelector('.transcript').textContent=byId[section.dataset.id].drafts[p+':aligned'].text;card.querySelector('.assessment').value=state[section.dataset.id].draft_checks[p]||'not_checked'}
for(const section of document.querySelectorAll('.minute')){const id=section.dataset.id;for(const card of section.querySelectorAll('.draft')){const select=card.querySelector('.profile');for(const p of D.profiles){const o=document.createElement('option');o.value=p;const [m,s]=p.split(':');o.textContent=(m==='trelis'?'Trelis · original script':'Apex · Roman Hinglish')+' · '+s+' seconds';select.append(o)}select.value=card.dataset.side==='left'?'trelis:15':'trelis:20';select.addEventListener('change',()=>showDraft(section,card));card.querySelector('.assessment').addEventListener('change',e=>{state[id].draft_checks[select.value]=e.target.value;save()});card.querySelector('.use-draft').addEventListener('click',()=>{if(state[id].reference.trim()&&!confirm('Replace your current reference draft?'))return;state[id].reference=byId[id].drafts[select.value+':aligned'].text;state[id].reviewed=false;state[id].reference_provenance={source:'copied_machine_draft',profile:select.value,binding:D.binding};populate();save()})}for(const field of ['reference','notes'])section.querySelector('.'+field).addEventListener('input',e=>{state[id][field]=e.target.value;state[id].reviewed=false;section.querySelector('.reviewed').checked=false;save()});section.querySelector('.reviewed').addEventListener('change',e=>{if(e.target.checked&&!state[id].reference.trim()){e.target.checked=false;status.textContent='Enter a corrected reference before marking reviewed.';return}state[id].reviewed=e.target.checked;save()})}
try{const previous=localStorage.getItem(key);if(previous)state=validate(JSON.parse(previous))}catch(e){status.textContent='Saved browser draft could not be restored.'}populate();
document.getElementById('export').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(doc(),null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='boundary-pilot-corrections-'+new Date().toISOString().replaceAll(':','-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);status.textContent='Corrections exported'});
document.getElementById('import').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;const next=validate(JSON.parse(await f.text()));state=next;populate();save();status.textContent='Matching corrections imported'}catch(err){status.textContent=err.message}finally{e.target.value=''}});
</script></body></html>'''
    page=page.replace('__ROWS__',''.join(rows)).replace('__CARDS__',''.join(cards)).replace('__DATA__',payload)
    output.write_text(page,encoding='utf8')
    print(json.dumps({'html':str(output),'fresh_minutes':len(fresh),'profiles':len(data['profiles'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path)
    a=p.parse_args();render(a.root.resolve(),a.output or a.root/'fresh-three-minute-review.html')
