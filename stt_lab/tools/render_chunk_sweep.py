"""Render a portable, script-preserving HTML review for a completed chunk sweep.

The page contains local audio and literal drafts. Human review is exported as a
separate JSON document, bound to the exact study, audio and model results.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, read_json


def local_file(root: Path, relative: str) -> Path:
    target = (root / relative).resolve(strict=True)
    if not target.is_relative_to(root):
        raise ValueError("Study input leaves its directory")
    return target


def clock(seconds: float) -> str:
    millis = round(seconds * 1000)
    minutes, remainder = divmod(millis, 60000)
    sec, ms = divmod(remainder, 1000)
    return f"{minutes:02}:{sec:02}.{ms:03}"


def percent(value: float | None) -> str:
    return "Not scored" if value is None else f"{value:.1%}"


def score_table(rows: list[dict], *, certain: bool = False) -> str:
    key = "boundary_certain_aggregate" if certain else "aggregate"
    body = []
    for row in rows:
        metric = row[key]
        eligible = [sample for sample in row["samples"] if sample["metrics"]]
        if certain:
            eligible = [sample for sample in eligible if not sample.get("has_scope_note")]
        denominator = metric["reference_words"]
        columns = [f"{row['size']}s", str(len(eligible)), percent(metric["wer"]),
                   str(metric["deletions"]) if denominator else "—",
                   str(metric["substitutions"]) if denominator else "—",
                   str(metric["insertions"]) if denominator else "—"]
        body.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in columns) + "</tr>")
    return ('<div class="table-wrap"><table><thead><tr><th>Window</th><th>Clips</th>'
            '<th>WER ↓</th><th>Deleted</th><th>Changed</th><th>Added</th></tr></thead>'
            '<tbody>' + "".join(body) + '</tbody></table></div>')


def best_tested(rows: list[dict], *, certain: bool = False) -> str:
    key = "boundary_certain_aggregate" if certain else "aggregate"
    sets = []
    for row in rows:
        sets.append(tuple(sample["sample_id"] for sample in row["samples"]
                          if sample["metrics"] and (not certain or not sample.get("has_scope_note"))))
    if not sets or not sets[0] or any(ids != sets[0] for ids in sets):
        return "No comparable size ranking: the same script-compatible references are not available for every size."
    best = min(row[key]["wer"] for row in rows)
    sizes = [str(row["size"]) + "s" for row in rows if row[key]["wer"] == best]
    scope = "this checked clip" if len(sets[0]) == 1 else f"these {len(sets[0])} checked clips"
    return f"Lowest observed WER: {', '.join(sizes)} ({best:.1%}) on {scope}. This is a development result, not a proven optimum."


CSS = r"""
:root{color-scheme:light;--ink:#173038;--muted:#556970;--line:#d4dfdc;--paper:#fbfcfa;--green:#176551;--soft:#eaf2ee;--gold:#936329}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 'Segoe UI',Arial,sans-serif}
main{max-width:1240px;margin:auto;padding:36px 28px 56px}h1{font-size:34px;line-height:1.15;margin:12px 0}h2{font-size:22px;line-height:1.25;margin:0 0 12px}h3{font-size:18px;margin:0 0 8px}p{margin:9px 0}button,select,textarea{font:inherit}
button,select{min-height:42px;border:1px solid #aebfba;border-radius:7px;padding:7px 12px;background:white;color:var(--ink)}button{cursor:pointer}button:hover{background:var(--soft)}button:disabled{opacity:.5;cursor:default}.primary{background:var(--green);color:white;border-color:var(--green)}.primary:hover{background:#10513f}button:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #3c83c0;outline-offset:3px}
.eyebrow{color:var(--green);font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700}.intro{max-width:900px}.muted{color:var(--muted)}.small{font-size:14px}.pill{display:inline-block;border-radius:100px;padding:4px 10px;background:var(--soft);font-size:12px;font-weight:600}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:22px}.score-grid{margin:25px 0 18px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px;margin:12px 0}th,td{padding:9px 7px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{font-size:12px;color:var(--muted);font-weight:600}.note{background:#fff6e7;border:1px solid #ead5ad;padding:14px 18px;border-radius:9px}.notice{background:var(--soft);padding:14px 18px;border-radius:9px}.section{margin-top:30px}details{margin-top:14px}summary{cursor:pointer;font-weight:600}details[open]>summary{margin-bottom:12px}.toolbar,.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.toolbar{justify-content:space-between;margin:18px 0}.stack{display:flex;flex-direction:column;gap:6px}.grow{flex:1;min-width:240px}.sample-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:15px}.source{overflow-wrap:anywhere}.audio-panel{padding:18px;background:var(--soft);border-radius:9px;margin:18px 0}audio{width:100%;height:48px}.audio-panel .row{margin-top:10px}.draft{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;margin:18px 0;min-height:130px}.draft-panel{align-self:start}.draft-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.draft-title .stack{min-width:105px}.boundary{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.reference{white-space:pre-wrap;border-left:3px solid #c5d6ce;padding-left:14px}.review-fields{margin-top:20px}textarea{display:block;width:100%;resize:vertical;border:1px solid #abbeb7;border-radius:7px;padding:12px;color:var(--ink);background:white;line-height:1.6;min-height:160px}.notes{min-height:80px}.checkline{display:flex;align-items:flex-start;gap:10px;margin:15px 0}.checkline input{width:19px;height:19px;margin-top:4px}.field{display:block;font-weight:600;margin:15px 0 7px}.field-note{font-weight:400}.review-state{color:var(--gold);font-size:13px}.footer{border-top:1px solid var(--line);margin-top:36px;padding-top:18px;color:var(--muted);font-size:13px}.error{color:#9c2c2c}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}[hidden]{display:none!important}.assessment{width:100%}.metric-line{font-size:13px;color:var(--muted)}.savebar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:16px 0 0}.savebar strong{font-size:14px}.focus-list{padding-left:20px}.focus-list li{margin:4px 0}.privacy{font-size:13px;color:var(--muted);margin-top:14px}.controls-end{margin-top:18px;justify-content:space-between}.save-status{min-height:24px}
@media(max-width:760px){main{padding:23px 15px 35px}.grid{grid-template-columns:1fr;gap:15px}h1{font-size:28px}.card{padding:17px}.sample-heading{display:block}.sample-heading .pill{margin-top:8px}.draft-title{flex-wrap:wrap}.toolbar .grow{width:100%}.grow select{width:100%}.score-grid{margin-top:20px}.draft{min-height:0}.controls-end{gap:12px}}
.footer p{overflow-wrap:anywhere}
@media print{main{max-width:none;padding:0}.toolbar,.audio-panel,.savebar,.controls-end,.assessment,.review-fields,.privacy{display:none}.grid{display:block}.card{break-inside:avoid;margin:12px 0}.draft{min-height:0}body{font-size:11pt}}
"""


JS = r"""
'use strict';
const study=JSON.parse(document.getElementById('study-data').textContent);
const el=id=>document.getElementById(id);
const sampleMap=new Map(study.samples.map(s=>[s.id,s]));
const focused=study.focused_samples.filter(id=>sampleMap.has(id));
const sizes=study.sizes;
let queue=[...focused], selected=queue[0], dirty=false;
const selection={apex:15,trelis:15};
const blank=()=>({reference:'',notes:'',reviewed:false,draft_checks:{},reference_provenance:null});
let reviews=Object.fromEntries(study.samples.map(s=>[s.id,blank()]));
const assessments=['not_checked','missing_speech','looks_complete','unclear'];
const statusNames={scored:'Checked-reference diagnostic',pending_native_script_reference:'Hinglish reference still needed in a compatible script',pending_human_review:'No checked reference yet',script_confounded:'Not scored: output and reference scripts differ'};
function tc(value){let ms=Math.round(value*1000),m=Math.floor(ms/60000),s=Math.floor(ms%60000/1000),f=ms%1000;return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+'.'+String(f).padStart(3,'0');}
function stable(value){if(Array.isArray(value))return '['+value.map(stable).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+stable(value[k])).join(',')+'}';return JSON.stringify(value);}
function setDirty(){dirty=true;el('save-status').textContent='Unsaved changes — use Save corrections before closing.';el('save-status').classList.remove('error');updateProgress();}
function updateProgress(){const done=queue.filter(id=>reviews[id].reviewed).length;el('progress').textContent=done+' of '+queue.length+' passages marked reviewed';}
function fillSamples(){const select=el('sample-select');select.replaceChildren();queue.forEach((id,i)=>{let option=document.createElement('option');option.value=id;option.textContent=(i+1)+'. '+sampleMap.get(id).title;select.append(option);});select.value=selected;updateProgress();}
function showModel(model){const sample=sampleMap.get(selected),size=selection[model],draft=sample.drafts[model][String(size)];el(model+'-text').textContent=draft.text;el(model+'-size').value=String(size);el(model+'-boundaries').textContent='Actual audio chunks: '+draft.partitions.map(p=>tc(p.start_seconds)+'–'+tc(p.end_seconds)).join(' · ');el(model+'-status').textContent=statusNames[draft.score_status]||draft.score_status;const metric=draft.metrics;el(model+'-metrics').textContent=metric?'WER '+(metric.wer*100).toFixed(1)+'% · '+metric.deletions+' deleted · '+metric.substitutions+' changed · '+metric.insertions+' added · '+metric.reference_words+' reference words':'Accuracy unscored; text length is not a completeness score.';el(model+'-assessment').value=reviews[selected].draft_checks[model+':'+size]||'not_checked';}
function showSample(){const sample=sampleMap.get(selected);el('sample-select').value=selected;el('sample-title').textContent=sample.title;el('sample-kind').textContent=sample.reference_status==='user_reviewed'?'Previously checked passage':'New review needed';el('sample-source').textContent=sample.call_title+' · '+tc(sample.source_start_seconds)+'–'+tc(sample.source_start_seconds+sample.duration_seconds)+' in decoded source audio';el('sample-duration').textContent=sample.duration_seconds.toFixed(2)+' seconds';const player=el('audio');player.pause();player.src=sample.audio_uri;player.playbackRate=Number(el('speed').value);el('reference-details').hidden=!sample.reference_roman;el('reference-text').textContent=sample.reference_roman||'';el('reference-scope').textContent=sample.reference_scope_note||'';el('reference-scope').hidden=!sample.reference_scope_note;el('correction').value=reviews[selected].reference;el('notes').value=reviews[selected].notes;el('reviewed').checked=reviews[selected].reviewed;el('sample-review-state').textContent=reviews[selected].reviewed?'You marked this passage reviewed.':'Review pending';showModel('apex');showModel('trelis');const index=queue.indexOf(selected);el('previous').disabled=index===0;el('next').disabled=index===queue.length-1;updateProgress();}
for(const model of ['apex','trelis']){const select=el(model+'-size');sizes.forEach(size=>{let option=document.createElement('option');option.value=String(size);option.textContent=size+' seconds';select.append(option);});select.addEventListener('change',()=>{selection[model]=Number(select.value);showModel(model);});el(model+'-assessment').addEventListener('change',()=>{reviews[selected].draft_checks[model+':'+selection[model]]=el(model+'-assessment').value;setDirty();});}
el('sample-select').addEventListener('change',()=>{selected=el('sample-select').value;showSample();});
el('queue-select').addEventListener('change',()=>{queue=el('queue-select').value==='focused'?[...focused]:study.samples.map(s=>s.id);if(!queue.includes(selected))selected=queue[0];fillSamples();showSample();});
el('previous').addEventListener('click',()=>{selected=queue[queue.indexOf(selected)-1];showSample();});el('next').addEventListener('click',()=>{selected=queue[queue.indexOf(selected)+1];showSample();});
el('back').addEventListener('click',()=>el('audio').currentTime=Math.max(0,el('audio').currentTime-5));el('forward').addEventListener('click',()=>{const a=el('audio');a.currentTime=Math.min(Number.isFinite(a.duration)?a.duration:sampleMap.get(selected).duration_seconds,a.currentTime+5);});el('speed').addEventListener('change',()=>el('audio').playbackRate=Number(el('speed').value));
el('correction').addEventListener('input',()=>{reviews[selected].reference=el('correction').value;reviews[selected].reviewed=false;if(reviews[selected].reference_provenance)reviews[selected].reference_provenance.edited_in_full_review=true;el('reviewed').checked=false;el('sample-review-state').textContent='Text edited — confirm review again when finished.';setDirty();});el('notes').addEventListener('input',()=>{reviews[selected].notes=el('notes').value;if(reviews[selected].reference_provenance)reviews[selected].reference_provenance.edited_in_full_review=true;setDirty();});el('reviewed').addEventListener('change',()=>{if(el('reviewed').checked&&!reviews[selected].reference.trim()){el('reviewed').checked=false;el('sample-review-state').textContent='Enter what was said before marking the passage reviewed.';return;}reviews[selected].reviewed=el('reviewed').checked;el('sample-review-state').textContent=reviews[selected].reviewed?'You marked this passage reviewed.':'Review pending';setDirty();});
el('save').addEventListener('click',()=>{const payload={kind:'chunk_size_human_review',version:1,binding:study.binding,exported_at:new Date().toISOString(),reviews};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='chunk-size-corrections-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);dirty=false;el('save-status').classList.remove('error');el('save-status').textContent='Export requested. Keep the downloaded JSON file to restore or share your review.';});
function cleanDraftContext(context){if(!context||typeof context!=='object'||Array.isArray(context))throw new Error('Missing reference draft provenance.');const clean={};for(const model of ['apex','trelis']){const value=context[model];if(!value||!Number.isInteger(value.window_seconds)||value.window_seconds<=0||value.window_seconds>30||!['text_sha256','result_snapshot_sha256'].every(key=>typeof value[key]==='string'&&/^[0-9a-f]{64}$/.test(value[key])))throw new Error('Invalid reference draft provenance.');clean[model]={window_seconds:value.window_seconds,text_sha256:value.text_sha256,result_snapshot_sha256:value.result_snapshot_sha256};}return clean;}
function cleanReferenceProvenance(value,id){if(value==null)return null;if(value.kind!=='chunk_size_reference_check'||value.manifest_sha256!==study.binding.manifest_sha256||value.audio_sha256!==study.binding.audio_sha256[id]||typeof value.edited_in_full_review!=='boolean')throw new Error('Reference provenance differs from this audio.');return {kind:value.kind,manifest_sha256:value.manifest_sha256,audio_sha256:value.audio_sha256,draft_context:cleanDraftContext(value.draft_context),edited_in_full_review:value.edited_in_full_review};}
function validReferenceFields(r){if(!r||typeof r.reference!=='string'||typeof r.notes!=='string'||typeof r.reviewed!=='boolean'||r.reference.length>200000||r.notes.length>200000||(r.reviewed&&!r.reference.trim()))throw new Error('A reference entry has an invalid format.');}
function validReferenceImport(payload){if(payload.version!==1||!payload.binding||payload.binding.manifest_sha256!==study.binding.manifest_sha256)throw new Error('These references belong to a different study manifest.');const audio=payload.binding.audio_sha256;if(!audio||typeof audio!=='object'||Array.isArray(audio)||!payload.reviews||typeof payload.reviews!=='object'||Array.isArray(payload.reviews))throw new Error('Invalid reference-only file.');const ids=Object.keys(payload.reviews);if(!ids.length||stable(ids.sort())!==stable(Object.keys(audio).sort()))throw new Error('Reference audio bindings do not match the imported passages.');const clean={};for(const id of ids){if(!sampleMap.has(id)||audio[id]!==study.binding.audio_sha256[id])throw new Error('An imported reference belongs to unknown or different audio.');const r=payload.reviews[id];validReferenceFields(r);clean[id]={reference:r.reference,notes:r.notes,reviewed:r.reviewed,reference_provenance:{kind:'chunk_size_reference_check',manifest_sha256:payload.binding.manifest_sha256,audio_sha256:audio[id],draft_context:cleanDraftContext(r.draft_context),edited_in_full_review:false}};}return clean;}
function validImport(payload){if(!payload||payload.kind!=='chunk_size_human_review'||payload.version!==1||stable(payload.binding)!==stable(study.binding))throw new Error('This file belongs to different audio, model outputs or study inputs.');if(!payload.reviews||typeof payload.reviews!=='object'||Array.isArray(payload.reviews)||stable(Object.keys(payload.reviews).sort())!==stable([...sampleMap.keys()].sort()))throw new Error('The review sample set does not match.');const clean={};for(const id of sampleMap.keys()){const r=payload.reviews[id];validReferenceFields(r);if(!r.draft_checks||typeof r.draft_checks!=='object'||Array.isArray(r.draft_checks))throw new Error('A review entry has an invalid format.');const checks={};for(const [key,value] of Object.entries(r.draft_checks)){const [model,size,...extra]=key.split(':');if(extra.length||!['apex','trelis'].includes(model)||!sizes.map(String).includes(size)||!assessments.includes(value))throw new Error('A draft assessment has an invalid format.');checks[key]=value;}clean[id]={reference:r.reference,notes:r.notes,reviewed:r.reviewed,draft_checks:checks,reference_provenance:cleanReferenceProvenance(r.reference_provenance,id)};}return clean;}
el('load').addEventListener('click',()=>el('import').click());el('import').addEventListener('change',async()=>{const input=el('import'),file=input.files[0];if(!file)return;try{if(file.size>10000000)throw new Error('Review file is too large.');const payload=JSON.parse(await file.text()),referenceOnly=payload&&payload.kind==='chunk_size_reference_check',clean=referenceOnly?validReferenceImport(payload):validImport(payload);const warning=referenceOnly?'Loading replaces reference text, notes and review status for '+Object.keys(clean).length+' matched passages. Other passages and draft assessments are kept. Continue?':'Loading will replace the unsaved corrections currently in this page. Continue?';if(dirty&&!window.confirm(warning))return;if(referenceOnly){for(const [id,value] of Object.entries(clean))reviews[id]={...reviews[id],...value};dirty=true;}else{reviews=clean;dirty=false;}showSample();el('save-status').classList.remove('error');el('save-status').textContent=referenceOnly?'Imported '+Object.keys(clean).length+' references. Other passages and draft assessments are kept. Save corrections to keep the merged review.':'Loaded matching corrections. Your explicit review flags were restored.';}catch(error){el('save-status').textContent='Could not load: '+error.message;el('save-status').classList.add('error');}finally{input.value='';}});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
fillSamples();showSample();
"""


def panel(model: str, title: str, subtitle: str) -> str:
    return f'''<article class="card draft-panel"><div class="draft-title"><div><h3>{escape(title)}</h3><span class="small muted">{escape(subtitle)}</span></div>
<label class="stack small" for="{model}-size">Audio window<select id="{model}-size"></select></label></div>
<p id="{model}-status" class="small muted"></p><p id="{model}-metrics" class="metric-line"></p>
<div id="{model}-text" class="draft" dir="auto"></div><p id="{model}-boundaries" class="boundary"></p>
<label class="field small" for="{model}-assessment">Your assessment of this draft</label><select class="assessment" id="{model}-assessment">
<option value="not_checked">Not checked</option><option value="missing_speech">Missing spoken content</option><option value="looks_complete">Looks complete after listening</option><option value="unclear">Unclear / needs another listen</option></select></article>'''


def build(root: Path, output: Path) -> dict:
    root = root.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to(root) or any(part.casefold().startswith("onedrive") for part in output.parts):
        raise ValueError("Keep this private HTML inside the private study directory, outside OneDrive")
    if output.exists():
        raise ValueError("Output already exists; choose a new name to preserve prior reviews")
    manifest_path, summary_path = root / "manifest.json", root / "summary.json"
    manifest, summary = read_json(manifest_path), read_json(summary_path)
    manifest_hash = file_digest(manifest_path)
    if summary["manifest_sha256"] != manifest_hash or summary["trelis_romanization"] or summary["release_qualified"]:
        raise ValueError("Expected an unqualified, script-preserving summary bound to this study")
    if summary["sizes"] != manifest["sizes"]:
        raise ValueError("Summary window sizes differ from manifest")
    models = {}
    results_hashes = {}
    sample_ids = {sample["id"] for sample in manifest["samples"]}
    for model in ("apex", "trelis"):
        result_path = root / "results" / f"{model}.json"
        result = read_json(result_path)
        result_hash = file_digest(result_path)
        report = summary["models"][model]
        if result["state"] != "complete" or result["manifest_sha256"] != manifest_hash or report["result_sha256"] != result_hash:
            raise ValueError(f"Incomplete or changed {model} results")
        if result.get("network_attempts"):
            raise ValueError("Inference reported a network attempt")
        if [row["size"] for row in report["sizes"]] != manifest["sizes"]:
            raise ValueError("Model window sizes differ from manifest")
        for row in report["sizes"]:
            if len(row["samples"]) != len(sample_ids) or {s["sample_id"] for s in row["samples"]} != sample_ids:
                raise ValueError("Summary sample set differs from manifest")
        models[model] = result
        results_hashes[model] = result_hash
    samples = []
    audio_hashes = {}
    for sample in manifest["samples"]:
        audio = local_file(root, sample["audio"])
        if file_digest(audio) != sample["audio_sha256"]:
            raise ValueError("Review audio changed")
        with wave.open(str(audio), "rb") as reader:
            actual_duration = reader.getnframes() / reader.getframerate()
        if abs(actual_duration - sample["duration_seconds"]) > 0.001:
            raise ValueError("Review audio duration differs from manifest")
        drafts = {}
        for model in models:
            drafts[model] = {}
            for row in summary["models"][model]["sizes"]:
                record = next(s for s in row["samples"] if s["sample_id"] == sample["id"])
                partitions = sample["partitions"][str(row["size"])]; pieces = []
                for part in partitions:
                    prediction = models[model]["chunks"][part["id"]]
                    if prediction["status"] != "ok" or prediction["audio_sha256"] != part["audio_sha256"]:
                        raise ValueError("Invalid raw prediction")
                    pieces.append(prediction["text"])
                literal = "\n\n".join(pieces)
                if record["text"] != literal:
                    raise ValueError("Summary text differs from literal model outputs")
                if model == "trelis" and record["metrics"] and not sample["direct_trelis_reference_candidate"]:
                    raise ValueError("Unexpected Trelis mixed-script scoring")
                record["has_scope_note"] = bool(sample.get("reference_scope_note"))
                drafts[model][str(row["size"])] = {key: record[key] for key in ("text", "score_status", "metrics")}
                drafts[model][str(row["size"])]["partitions"] = [{key: p[key] for key in ("start_seconds", "end_seconds")} for p in partitions]
        audio_hashes[sample["id"]] = sample["audio_sha256"]
        samples.append({**{key: sample.get(key) for key in ("id", "title", "call_title", "source_start_seconds", "duration_seconds", "reference_status", "reference_roman", "reference_scope_note")},
                        "drafts": drafts, "audio_uri": "data:audio/wav;base64," + base64.b64encode(audio.read_bytes()).decode("ascii")})
    focused = [sid for sid in ("section-10", "section-02", "call-06-01") if sid in sample_ids]
    binding = {"manifest_sha256": manifest_hash, "summary_sha256": file_digest(summary_path),
               "audio_sha256": audio_hashes, "model_results_sha256": results_hashes}
    data = {"sizes": manifest["sizes"], "samples": samples, "focused_samples": focused, "binding": binding}
    raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    csp_hash = lambda text: base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
    csp = f"default-src 'none'; media-src data:; style-src 'sha256-{csp_hash(CSS)}'; script-src 'sha256-{csp_hash(JS)}' 'sha256-{csp_hash(raw_json)}'; connect-src 'none'; img-src data:; object-src 'none'; base-uri 'none'; form-action 'none'"
    apex_rows, trelis_rows = summary["models"]["apex"]["sizes"], summary["models"]["trelis"]["sizes"]
    focus_seconds = sum(s["duration_seconds"] for s in samples if s["id"] in focused)
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}"><title>Apex + Trelis · Chunk-size review</title><style>{CSS}</style></head>
<body><main><header><span class="eyebrow">Local transcription study</span><h1>How much audio at a time?</h1><p class="intro">Compare 5, 10, 15, 20 and 30-second windows on the same audio. Apex stays in Roman Hinglish; Trelis stays exactly as produced, including Devanagari.</p><span class="pill">Development study · no proven optimum yet</span></header>
<section class="grid score-grid" aria-label="Separate model results"><article class="card"><h2>Apex</h2><p class="small muted">Four previously checked passages. These scores compare chunk sizes within Apex.</p>{score_table(apex_rows)}<p class="small">{escape(best_tested(apex_rows))}</p><details><summary>Check sensitivity to excerpt 1’s ending</summary><p class="small">The user placed the disputed ending outside the clip. These scores exclude that entire passage, leaving three checked clips.</p>{score_table(apex_rows, certain=True)}<p class="small">{escape(best_tested(apex_rows, certain=True))}</p></details></article>
<article class="card"><h2>Trelis</h2><p class="small muted">At most one checked English passage can be scored directly. Hinglish completeness remains pending human review; no Roman conversion was applied.</p>{score_table(trelis_rows)}<p class="small">{escape(best_tested(trelis_rows))}</p><p class="small muted">If Trelis uses Devanagari in this English reference, that size is left unscored. Apex and Trelis have different scoring scopes, so these tables do not rank the models against each other.</p></article></section>
<p class="notice small"><strong>What the numbers mean:</strong> WER counts changed, deleted and added words against a checked reference. The deletion count is an alignment diagnostic—not a percentage of spoken meaning captured. More output can include repetition or invented speech.</p>
<details><summary>Audio boundaries and study limits</summary><p class="small">The checked passages are about 25 seconds each. Their 30-second setting is an unsplit ~25-second clip. Only the two new minute-long passages contain full 30-second windows. Short final tails below one second are rebalanced with the previous chunk; actual boundaries are shown beside every draft.</p><p class="small">All audio is covered with disjoint windows and no overlap. Draft chunks are joined literally, without rewriting or removing repetitions. Source timestamps use the decoded recording’s sample clock. Runtime conditions were not a controlled live-speed comparison. Shifted boundaries, overlap, further checked passages and full-call tests are still needed before choosing a production setting.</p></details>
<section class="section" aria-labelledby="review-heading"><h2 id="review-heading">A focused listening check</h2><p>Start with just <strong>{len(focused)} passages ({focus_seconds/60:.1f} minutes)</strong>: the known omission, a Hinglish continuity check and the checked English passage. You do not need to finish the earlier 12-minute review first.</p><p class="small muted">Listen once, then compare the window sizes using the two menus. Enter what was actually said in either script. Mark a passage reviewed only after checking the whole clip; assessments of drafts are saved separately.</p>
<div class="toolbar"><label class="stack small" for="queue-select">Review queue<select id="queue-select"><option value="focused">Focused passages</option><option value="all">All six study passages</option></select></label><label class="stack grow small" for="sample-select">Passage<select id="sample-select"></select></label><span id="progress" class="small muted" aria-live="polite"></span></div>
<div class="card"><div class="sample-heading"><div><h3 id="sample-title"></h3><p id="sample-source" class="source small muted"></p></div><span id="sample-kind" class="pill"></span></div>
<div class="audio-panel"><audio id="audio" controls preload="metadata" aria-label="Current passage audio"></audio><div class="row"><button id="back" type="button">−5 seconds</button><button id="forward" type="button">+5 seconds</button><label for="speed" class="small">Playback</label><select id="speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option></select><span id="sample-duration" class="small muted"></span></div></div>
<details id="reference-details"><summary>Previously checked reference</summary><p id="reference-text" class="reference"></p><p id="reference-scope" class="note small"></p></details>
<div class="grid section">{panel('apex','Apex · Roman Hinglish','Unedited model draft')}{panel('trelis','Trelis · Original script','Unedited model draft · no transliteration')}</div>
<div class="review-fields"><label for="correction" class="field">What was actually said <span class="field-note small">— either script is fine</span></label><textarea id="correction" dir="auto" placeholder="Transcribe the whole passage. Use [unclear] where you cannot make out the words."></textarea><label for="notes" class="field">Notes <span class="field-note small">— missing phrases, names, quantities or difficult boundaries</span></label><textarea id="notes" class="notes" dir="auto"></textarea><label class="checkline"><input type="checkbox" id="reviewed"><span>I listened to the whole passage and checked my transcript above.</span></label><p id="sample-review-state" class="review-state" aria-live="polite"></p></div>
<div class="row controls-end"><button id="previous" type="button">← Previous passage</button><button id="next" type="button">Next passage →</button></div></div>
<div class="savebar"><button id="save" class="primary" type="button">Save corrections</button><button id="load" type="button">Load corrections</button><input type="file" id="import" accept=".json,application/json" hidden><strong>Save before closing.</strong></div><p id="save-status" class="small save-status" role="status">Edits stay in this page until you export them. Nothing is uploaded.</p><p class="small muted">Already used the two-minute reference check? Load its saved JSON here. Matching references are merged, other passages and draft assessments are kept, and no model approval is transferred.</p><p class="privacy">This HTML includes the audio and transcripts. Keep it private. It runs offline with no external scripts, fonts or services. Full reviews must match these exact model outputs; reference-only imports must match the study and their audio.</p></section>
<footer class="footer"><p>Model drafts and checked references are preserved separately. New corrections do not change the results above or automatically approve a model or chunk size.</p><details><summary>Study identifiers</summary><p>Manifest: {manifest_hash}</p><p>Apex results: {results_hashes['apex']}</p><p>Trelis results: {results_hashes['trelis']}</p></details></footer>
</main><script type="application/json" id="study-data">{raw_json}</script><script>{JS}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return {"output": str(output), "sha256": file_digest(output), "samples": len(samples),
            "focused_samples": focused, "embedded_audio_seconds": sum(s["duration_seconds"] for s in samples),
            "raw_draft_texts_verified": len(samples) * len(models) * len(manifest["sizes"]),
            "binding": binding, "trelis_romanization": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="New HTML path inside the private study directory")
    arguments = parser.parse_args()
    destination = arguments.output or arguments.root / "chunk-size-review.html"
    print(json.dumps(build(arguments.root, destination), indent=2))
