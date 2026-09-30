"""Build an interim, audio-bound reference check from an explicit Trelis snapshot.

Never reads the live Trelis result. Reference exports remain usable when later
chunk-size inference completes; displayed model drafts are provenance only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import wave

from render_chunk_sweep import CSS, local_file
from sttbench.manifest import file_digest, read_json


JS = r"""
'use strict';
const study=JSON.parse(document.getElementById('study-data').textContent);
const el=id=>document.getElementById(id),samples=study.samples;
let index=0,dirty=false;
let reviews=Object.fromEntries(samples.map(s=>[s.id,{reference:'',notes:'',reviewed:false,draft_context:study.displayed_drafts[s.id]}]));
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
function tc(value){const ms=Math.round(value*1000);return String(Math.floor(ms/60000)).padStart(2,'0')+':'+String(Math.floor(ms%60000/1000)).padStart(2,'0')+'.'+String(ms%1000).padStart(3,'0');}
function progress(){el('progress').textContent=samples.filter(s=>reviews[s.id].reviewed).length+' of '+samples.length+' references checked';}
function changed(){dirty=true;el('save-status').textContent='Unsaved changes — save corrections before closing.';el('save-status').classList.remove('error');progress();}
function show(){const sample=samples[index],review=reviews[sample.id];el('sample-select').value=String(index);el('sample-title').textContent=sample.title;el('sample-source').textContent=sample.call_title+' · '+tc(sample.source_start_seconds)+'–'+tc(sample.source_start_seconds+sample.duration_seconds)+' in decoded source audio';el('audio').pause();el('audio').src=sample.audio_uri;el('audio').playbackRate=Number(el('speed').value);el('duration').textContent=sample.duration_seconds.toFixed(0)+' seconds';for(const model of ['apex','trelis']){el(model+'-text').textContent=sample.drafts[model].text;el(model+'-boundaries').textContent='Audio pieces: '+sample.drafts[model].partitions.map(p=>tc(p.start_seconds)+'–'+tc(p.end_seconds)).join(' · ');}el('correction').value=review.reference;el('notes').value=review.notes;el('reviewed').checked=review.reviewed;el('review-status').textContent=review.reviewed?'You marked this reference checked.':'Reference pending';el('previous').disabled=index===0;el('next').disabled=index===samples.length-1;progress();}
samples.forEach((sample,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=(i+1)+'. '+sample.title;el('sample-select').append(option);});
el('sample-select').addEventListener('change',()=>{index=Number(el('sample-select').value);show();});el('previous').addEventListener('click',()=>{index--;show();});el('next').addEventListener('click',()=>{index++;show();});
el('speed').addEventListener('change',()=>el('audio').playbackRate=Number(el('speed').value));el('back').addEventListener('click',()=>el('audio').currentTime=Math.max(0,el('audio').currentTime-5));el('forward').addEventListener('click',()=>el('audio').currentTime=Math.min(samples[index].duration_seconds,el('audio').currentTime+5));
el('correction').addEventListener('input',()=>{const review=reviews[samples[index].id];review.reference=el('correction').value;review.reviewed=false;review.draft_context=study.displayed_drafts[samples[index].id];el('reviewed').checked=false;el('review-status').textContent='Text edited — confirm the reference when finished.';changed();});el('notes').addEventListener('input',()=>{reviews[samples[index].id].notes=el('notes').value;changed();});el('reviewed').addEventListener('change',()=>{const review=reviews[samples[index].id];if(el('reviewed').checked&&!review.reference.trim()){el('reviewed').checked=false;el('review-status').textContent='Enter the spoken words before marking the reference checked.';return;}review.reviewed=el('reviewed').checked;el('review-status').textContent=review.reviewed?'You marked this reference checked.':'Reference pending';changed();});
el('save').addEventListener('click',()=>{const payload={kind:'chunk_size_reference_check',version:1,binding:study.binding,exported_at:new Date().toISOString(),scope:'Human references only; no model or chunk-size approval.',reviews};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='two-minute-reference-check-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);dirty=false;el('save-status').classList.remove('error');el('save-status').textContent='Export requested. Keep the downloaded JSON file to restore or share these references.';});
function cleanContext(context){if(!context||typeof context!=='object'||Array.isArray(context))throw new Error('Missing displayed-draft provenance.');const result={};for(const model of ['apex','trelis']){const value=context[model];if(!value||!Number.isInteger(value.window_seconds)||value.window_seconds<=0||value.window_seconds>30||!['text_sha256','result_snapshot_sha256'].every(k=>typeof value[k]==='string'&&/^[0-9a-f]{64}$/.test(value[k])))throw new Error('Invalid displayed-draft provenance.');result[model]={window_seconds:value.window_seconds,text_sha256:value.text_sha256,result_snapshot_sha256:value.result_snapshot_sha256};}return result;}
function validate(payload){if(!payload||payload.kind!=='chunk_size_reference_check'||payload.version!==1||stable(payload.binding)!==stable(study.binding))throw new Error('This reference file belongs to different study inputs or audio.');if(!payload.reviews||typeof payload.reviews!=='object'||Array.isArray(payload.reviews)||stable(Object.keys(payload.reviews).sort())!==stable(samples.map(s=>s.id).sort()))throw new Error('The passage set does not match.');const clean={};for(const sample of samples){const r=payload.reviews[sample.id];if(!r||typeof r.reference!=='string'||typeof r.notes!=='string'||typeof r.reviewed!=='boolean'||r.reference.length>200000||r.notes.length>200000||(r.reviewed&&!r.reference.trim()))throw new Error('Invalid reference entry.');clean[sample.id]={reference:r.reference,notes:r.notes,reviewed:r.reviewed,draft_context:cleanContext(r.draft_context)};}return clean;}
el('load').addEventListener('click',()=>el('import').click());el('import').addEventListener('change',async()=>{const input=el('import'),file=input.files[0];if(!file)return;try{if(file.size>10000000)throw new Error('Reference file is too large.');const clean=validate(JSON.parse(await file.text()));if(dirty&&!window.confirm('Loading replaces the unsaved references currently in this page. Continue?'))return;reviews=clean;dirty=false;show();el('save-status').classList.remove('error');el('save-status').textContent='Matching references loaded. Saved draft provenance is retained; no model approval is implied.';}catch(error){el('save-status').textContent='Could not load: '+error.message;el('save-status').classList.add('error');}finally{input.value='';}});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});show();
"""


def build(root: Path, snapshot_path: Path, output: Path) -> dict:
    root = root.resolve(strict=True)
    snapshot_path = snapshot_path.resolve(strict=True)
    output = output.resolve()
    if snapshot_path == (root / "results/trelis.json").resolve():
        raise ValueError("Use an explicit safe snapshot, never the live Trelis result")
    if not output.is_relative_to(root) or any(p.casefold().startswith("onedrive") for p in output.parts):
        raise ValueError("Keep this private HTML inside the private study directory outside OneDrive")
    if output.exists():
        raise ValueError("Choose a new HTML name to preserve the previous review")
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest_hash = file_digest(manifest_path)
    result_paths = {"apex": root / "results/apex.json", "trelis": snapshot_path}
    results = {model: read_json(path) for model, path in result_paths.items()}
    result_hashes = {model: file_digest(path) for model, path in result_paths.items()}
    for model, result in results.items():
        if result.get("model_id") != model or result.get("manifest_sha256") != manifest_hash:
            raise ValueError("Model identity or manifest binding differs from this study")
        if result.get("network_attempts"):
            raise ValueError("Inference reported a network attempt")
        if result.get("state") not in (("complete",) if model == "apex" else ("running", "complete")):
            raise ValueError("Apex must be complete and Trelis must have a valid running/complete snapshot")
    samples, audio_hashes, displayed_drafts = [], {}, {}
    selected = [("section-10", "The passage with missing speech"), ("section-02", "Hinglish continuity check")]
    for identifier, title in selected:
        sample = next(s for s in manifest["samples"] if s["id"] == identifier)
        audio = local_file(root, sample["audio"])
        if file_digest(audio) != sample["audio_sha256"]:
            raise ValueError("Reference audio hash changed")
        with wave.open(str(audio), "rb") as reader:
            duration = reader.getnframes() / reader.getframerate()
        if abs(duration - sample["duration_seconds"]) > 0.001:
            raise ValueError("Reference audio duration changed")
        drafts, provenance = {}, {}
        for model, size in (("apex", 30), ("trelis", 15)):
            parts = sample["partitions"][str(size)]
            text_parts = []
            for part in parts:
                result = results[model]["chunks"].get(part["id"])
                if not result or result.get("status") != "ok" or result.get("audio_sha256") != part["audio_sha256"]:
                    raise ValueError(f"Missing, failed or mismatched completed draft: {model}/{part['id']}")
                if not isinstance(result.get("text"), str):
                    raise ValueError("Model draft must be text")
                text_parts.append(result["text"])
            text = "\n\n".join(text_parts)
            drafts[model] = {"text": text, "window_seconds": size,
                             "partitions": [{k: part[k] for k in ("start_seconds", "end_seconds")} for part in parts]}
            provenance[model] = {"window_seconds": size, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                 "result_snapshot_sha256": result_hashes[model]}
        audio_hashes[identifier] = sample["audio_sha256"]
        displayed_drafts[identifier] = provenance
        samples.append({"id": identifier, "title": title, "call_title": sample["call_title"],
                        "source_start_seconds": sample["source_start_seconds"], "duration_seconds": duration,
                        "audio_uri": "data:audio/wav;base64," + base64.b64encode(audio.read_bytes()).decode("ascii"), "drafts": drafts})
    binding = {"manifest_sha256": manifest_hash, "audio_sha256": audio_hashes}
    payload = {"samples": samples, "binding": binding, "displayed_drafts": displayed_drafts}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    hash_csp = lambda text: base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
    csp = f"default-src 'none'; media-src data:; style-src 'sha256-{hash_csp(CSS)}'; script-src 'sha256-{hash_csp(JS)}' 'sha256-{hash_csp(data)}'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{escape(csp,quote=True)}"><title>Two-minute reference check · Apex + Trelis</title><style>{CSS}</style></head>
<body><main><header><span class="eyebrow">Interim focused reference check</span><h1>Two minutes to check against the audio</h1><p class="intro">Help establish exactly what was said in the omission example and one Hinglish passage. The chunk-size study is still being evaluated. These drafts provide listening context; this page makes no model ranking or chunk-size recommendation.</p><span class="pill">Human references · two passages · offline</span></header>
<p class="notice section small">Listen to each minute and enter the spoken words in either script. Preserve repetitions and names; use [unclear] where needed. Your checked reference will remain usable as further model results arrive. Marking it checked does not approve either model.</p>
<div class="toolbar"><label class="stack grow" for="sample-select">Passage<select id="sample-select"></select></label><span id="progress" class="small muted" aria-live="polite"></span></div>
<section class="card"><h2 id="sample-title"></h2><p id="sample-source" class="source small muted"></p><div class="audio-panel"><audio id="audio" controls preload="metadata" aria-label="Current passage audio"></audio><div class="row"><button id="back" type="button">−5 seconds</button><button id="forward" type="button">+5 seconds</button><label for="speed" class="small">Playback</label><select id="speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option></select><span id="duration" class="small muted"></span></div></div>
<div class="grid section"><article class="card draft-panel"><h3>Apex · Roman Hinglish</h3><p class="small muted">Unedited draft · 30-second audio windows</p><div id="apex-text" class="draft" dir="auto"></div><p id="apex-boundaries" class="boundary"></p></article><article class="card draft-panel"><h3>Trelis · Original script</h3><p class="small muted">Unedited draft · 15-second audio windows · no transliteration</p><div id="trelis-text" class="draft" dir="auto"></div><p id="trelis-boundaries" class="boundary"></p></article></div>
<div class="review-fields"><label for="correction" class="field">What was actually said <span class="field-note small">— either script is fine</span></label><textarea id="correction" dir="auto" placeholder="Enter the full spoken passage. Use [unclear] where needed."></textarea><label for="notes" class="field">Notes <span class="field-note small">— names, quantities or hard-to-hear words</span></label><textarea id="notes" class="notes" dir="auto"></textarea><label class="checkline"><input type="checkbox" id="reviewed"><span>I listened to the whole minute and checked my reference transcript above.</span></label><p id="review-status" class="review-state" aria-live="polite"></p></div><div class="row controls-end"><button id="previous" type="button">← Previous passage</button><button id="next" type="button">Next passage →</button></div></section>
<div class="savebar"><button id="save" class="primary" type="button">Save corrections</button><button id="load" type="button">Load corrections</button><input id="import" type="file" accept=".json,application/json" hidden><strong>Save before closing.</strong></div><p id="save-status" class="small save-status" role="status">Edits stay in this page until you export them. Nothing is uploaded.</p><p class="privacy">This self-contained HTML contains private audio and drafts. Keep it private. Playback and editing work offline. Import checks the exact study and audio; displayed draft hashes are retained only as provenance.</p>
<footer class="footer"><p>Source offsets use the decoded recording’s sample clock. Draft chunks are joined literally; paragraph breaks are not speaker labels. New references do not rewrite model output or approve a model.</p><details><summary>Study identity</summary><p>{manifest_hash}</p></details></footer></main><script id="study-data" type="application/json">{data}</script><script>{JS}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return {"output": str(output), "sha256": file_digest(output), "sample_ids": [s["id"] for s in samples],
            "audio_seconds": sum(s["duration_seconds"] for s in samples), "binding": binding,
            "displayed_drafts": displayed_drafts, "interim": True, "trelis_romanization": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--trelis-snapshot", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.trelis_snapshot, args.output or args.root / "interim-reference-check.html"), indent=2))
