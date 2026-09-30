"""Render a private, offline readback of a scored chunk-size correction revision.

Usage: python render_chunk_corrections.py --root STUDY --analysis REVISION.json
       --output NEW.html

The original study and prediction files remain immutable. No new inference or
transliteration occurs here; literal drafts are checked against the saved runs.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sttbench.manifest import file_digest, read_json
from render_chunk_sweep import CSS, clock, local_file


EXTRA_CSS = r"""
.assessment-readback{font-size:13px;padding:10px 12px;background:var(--soft);border-radius:7px}
.reference-block{margin:18px 0}.reference-block .reference{line-height:1.75}
.notes-readback{white-space:pre-wrap;overflow-wrap:anywhere}
.table-wrap table{min-width:400px}.source-badge{margin:12px 0}
.score-grid>.card{min-width:0}.draft-title>div{min-width:0}
.scope-label{font-size:13px;color:var(--muted)}.warning{color:#81531e}
.table-wrap:focus-visible{outline:3px solid #3c83c0;outline-offset:2px}
.controls-end button{max-width:100%}.hash{font-family:Consolas,monospace;font-size:12px;overflow-wrap:anywhere}
@media(max-width:760px){.grow{min-width:0;width:100%}.sample-heading h3{overflow-wrap:anywhere}.stack select{max-width:100%}}
"""


def percent(value: float | None) -> str:
    return "Not scored" if value is None else f"{value:.1%}"


def table(rows: list[dict], key: str, caption: str) -> str:
    body = []
    for row in rows:
        metrics = row.get(key) or {}
        count = metrics.get("reference_words", 0)
        values = [f"{row['size']}s", percent(metrics.get("wer")), str(count),
                  str(metrics.get("deletions", 0)) if count else "—",
                  str(metrics.get("substitutions", 0)) if count else "—",
                  str(metrics.get("insertions", 0)) if count else "—"]
        body.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in values) + "</tr>")
    return ('<div class="table-wrap" tabindex="0" role="region" aria-label="' + escape(caption, quote=True) + '">'
            '<table><caption class="sr-only">' + escape(caption) + '</caption><thead><tr>'
            '<th scope="col">Window</th><th scope="col">WER ↓</th><th scope="col">Ref. words</th>'
            '<th scope="col">Deleted</th><th scope="col">Changed</th><th scope="col">Added</th>'
            '</tr></thead><tbody>' + "".join(body) + '</tbody></table></div>')


def finding(rows: list[dict], key: str = "aggregate") -> str:
    valid = [row for row in rows if (row.get(key) or {}).get("wer") is not None]
    if not valid:
        return "No scored comparison is available for this panel."
    best_wer = min(row[key]["wer"] for row in valid)
    fewest = min(row[key]["deletions"] for row in valid)
    best_sizes = ", ".join(f"{row['size']}s" for row in valid if row[key]["wer"] == best_wer)
    deletion_sizes = ", ".join(f"{row['size']}s" for row in valid if row[key]["deletions"] == fewest)
    return (f"Lowest observed WER: {best_sizes} ({best_wer:.1%}). "
            f"Fewest alignment deletions: {deletion_sizes} ({fewest}). "
            "These are sample-specific development results; neither establishes a production optimum.")


def panel(model: str, title: str, subtitle: str) -> str:
    return f'''<article class="card draft-panel"><div class="draft-title"><div><h3>{escape(title)}</h3>
<span class="small muted">{escape(subtitle)}</span></div><label class="stack small" for="{model}-size">Audio window
<select id="{model}-size"></select></label></div><p id="{model}-status" class="scope-label"></p>
<p id="{model}-metrics" class="metric-line"></p><p id="{model}-warning" class="small warning" hidden></p>
<p id="{model}-assessment" class="assessment-readback"></p><div id="{model}-text" class="draft" dir="auto"></div>
<p id="{model}-boundaries" class="boundary"></p></article>'''


JS = r"""
'use strict';
const data=JSON.parse(document.getElementById('study-data').textContent);
const el=id=>document.getElementById(id),samples=new Map(data.samples.map(s=>[s.id,s]));
const order=data.display_order,selection={apex:15,trelis:15};let selected=order[0];
function tc(value){let ms=Math.round(value*1000),m=Math.floor(ms/60000),s=Math.floor(ms%60000/1000),f=ms%1000;return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+'.'+String(f).padStart(3,'0');}
const kinds={english:'English reference',native_mixed:'Native mixed-script reference',roman_hinglish:'Roman Hinglish reference',pending:'Reference pending'};
const sources={uploaded_review:'Checked reference from this correction file',prior_checked_reference:'Earlier checked reference retained',pending:'No checked reference'};
const statuses={scored:'Scored against the checked reference',strict_surface_diagnostic:'Strict word-error diagnostic against the checked reference',scored_surface_diagnostic:'Literal surface score; see script warning',pending_native_script_reference:'No compatible reference for this model',script_incompatible:'No compatible reference for this model',incompatible_reference_script:'No compatible reference for this model',script_confounded:'Scripts differ; do not treat this as a fair accuracy score',pending_human_review:'Reference still needs review',pending:'Not scored'};
const checks={looks_complete:'Looks complete after listening',missing_speech:'Missing spoken content',unclear:'Unclear / needs another listen',not_checked:'Not assessed in this correction file'};
function showModel(model){const sample=samples.get(selected),size=selection[model],draft=sample.drafts[model][String(size)];el(model+'-size').value=String(size);el(model+'-text').textContent=draft.text;
el(model+'-status').textContent=statuses[draft.score_status]||draft.score_status.replaceAll('_',' ');
const metric=draft.metrics;el(model+'-metrics').textContent=metric?'WER '+(metric.wer*100).toFixed(1)+'% · '+metric.deletions+' deleted · '+metric.substitutions+' changed · '+metric.insertions+' added · '+metric.reference_words+' reference words':'Unscored against this reference; output length is not a completeness measure.';
const warning=draft.script_surface_warning;el(model+'-warning').hidden=!warning;el(model+'-warning').textContent=typeof warning==='string'?warning:warning?'Output and reference differ in script. This surface score includes spelling/script penalties.':'';
const assessment=sample.draft_checks?.[model+':'+size];el(model+'-assessment').textContent=assessment&&assessment!=='not_checked'?'Your '+size+'s draft check: '+(checks[assessment]||assessment)+'. Applies only to this draft.':'Not assessed in this correction file.';
const chunks=draft.chunks||[];el(model+'-boundaries').textContent='Actual audio chunks: '+chunks.map(p=>tc(p.start_seconds)+'–'+tc(p.end_seconds)).join(' · ');
}
function showSample(){const sample=samples.get(selected);el('sample-select').value=selected;el('sample-title').textContent=sample.title;el('sample-source').textContent=sample.call_title+' · '+tc(sample.source_start_seconds)+'–'+tc(sample.source_start_seconds+sample.duration_seconds)+' in decoded source audio';el('sample-kind').textContent=sources[sample.reference_source]||sample.reference_source;el('reference-kind').textContent=kinds[sample.reference_kind]||sample.reference_kind;
el('reference-text').textContent=sample.reference||'';el('notes-text').textContent=sample.notes||'';el('notes-section').hidden=!sample.notes;el('reference-scope').textContent=sample.reference_scope_note||'';el('reference-scope').hidden=!sample.reference_scope_note;
el('previous-reference').textContent=sample.previous_reference||'';el('previous-reference-details').hidden=!sample.previous_reference||sample.previous_reference===sample.reference;el('previous-reference-details').open=false;
el('sample-progress').textContent=(order.indexOf(selected)+1)+' of '+order.length+' passages';el('sample-duration').textContent=sample.duration_seconds.toFixed(2)+' seconds';const player=el('audio');player.pause();player.src=sample.audio_uri;player.playbackRate=Number(el('speed').value);
showModel('apex');showModel('trelis');const index=order.indexOf(selected);el('previous').disabled=index===0;el('next').disabled=index===order.length-1;}
for(const id of order){const option=document.createElement('option');option.value=id;option.textContent=(order.indexOf(id)+1)+'. '+samples.get(id).title;el('sample-select').append(option);}
for(const model of ['apex','trelis']){for(const size of data.sizes){const option=document.createElement('option');option.value=String(size);option.textContent=size+' seconds';el(model+'-size').append(option);}el(model+'-size').addEventListener('change',()=>{selection[model]=Number(el(model+'-size').value);showModel(model);});}
el('sample-select').addEventListener('change',()=>{selected=el('sample-select').value;showSample();});
el('previous').addEventListener('click',()=>{selected=order[order.indexOf(selected)-1];showSample();});el('next').addEventListener('click',()=>{selected=order[order.indexOf(selected)+1];showSample();});
el('back').addEventListener('click',()=>el('audio').currentTime=Math.max(0,el('audio').currentTime-5));el('forward').addEventListener('click',()=>{const player=el('audio');player.currentTime=Math.min(Number.isFinite(player.duration)?player.duration:samples.get(selected).duration_seconds,player.currentTime+5);});el('speed').addEventListener('change',()=>el('audio').playbackRate=Number(el('speed').value));showSample();
"""


def render(root: Path, output: Path, analysis: dict) -> None:
    root = root.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to(root) or any(part.casefold().startswith("onedrive") for part in output.parts):
        raise ValueError("Keep this private HTML inside the private study directory, outside OneDrive")
    if output.exists():
        raise ValueError("Output already exists; choose a new revision filename")
    if analysis.get("trelis_romanization") is not False or analysis.get("release_qualified") is not False:
        raise ValueError("Expected an unqualified revision preserving native Trelis text")
    if analysis.get("held_out") is not False:
        raise ValueError("This report is designed for the non-held-out development study")
    manifest = read_json(root / "manifest.json")
    binding = analysis["source"]["binding"]
    if binding["manifest_sha256"] != file_digest(root / "manifest.json"):
        raise ValueError("Analysis belongs to a different study manifest")
    if binding["summary_sha256"] != file_digest(root / "summary.json"):
        raise ValueError("Original summary binding changed")
    if analysis["sizes"] != manifest["sizes"]:
        raise ValueError("Analysis sizes differ from the study")
    source_samples = {sample["id"]: sample for sample in manifest["samples"]}
    if len(analysis["samples"]) != len(source_samples) or {s["id"] for s in analysis["samples"]} != set(source_samples):
        raise ValueError("Analysis sample set differs from the study")
    predictions = {}
    for model in ("apex", "trelis"):
        path = root / "results" / f"{model}.json"
        if file_digest(path) != binding["model_results_sha256"][model]:
            raise ValueError(f"{model} result binding changed")
        predictions[model] = read_json(path)
        if predictions[model]["state"] != "complete" or predictions[model].get("network_attempts"):
            raise ValueError("Expected complete local model results")
    data = copy.deepcopy(analysis)
    for sample in data["samples"]:
        original = source_samples[sample["id"]]
        for key in ("audio", "audio_sha256", "duration_seconds", "source_start_seconds", "call_title"):
            if sample[key] != original[key]:
                raise ValueError(f"Changed source metadata: {sample['id']} / {key}")
        audio = local_file(root, sample["audio"])
        audio_hash = file_digest(audio)
        if audio_hash != sample["audio_sha256"] or audio_hash != binding["audio_sha256"][sample["id"]]:
            raise ValueError("Audio binding changed")
        with wave.open(str(audio), "rb") as reader:
            actual_duration = reader.getnframes() / reader.getframerate()
        if abs(actual_duration - sample["duration_seconds"]) > 0.001:
            raise ValueError("Audio duration differs from the manifest")
        sample["audio_uri"] = "data:audio/wav;base64," + base64.b64encode(audio.read_bytes()).decode("ascii")
        for model in predictions:
            for size in data["sizes"]:
                partitions = original["partitions"][str(size)]
                pieces = []
                for part in partitions:
                    prediction = predictions[model]["chunks"][part["id"]]
                    if prediction["status"] != "ok" or prediction["audio_sha256"] != part["audio_sha256"]:
                        raise ValueError("Invalid saved model prediction")
                    pieces.append(prediction["text"])
                draft = sample["drafts"][model][str(size)]
                if draft["text"] != "\n\n".join(pieces):
                    raise ValueError("Analysis text differs from literal saved model output")
                draft["chunks"] = [{key: part[key] for key in ("start_seconds", "end_seconds")} for part in partitions]
    preferred = [sid for sid in ("section-10", "section-02", "call-06-01") if sid in source_samples]
    data["display_order"] = preferred + [s["id"] for s in data["samples"] if s["id"] not in preferred]
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    apex, trelis = data["models"]["apex"], data["models"]["trelis"]
    apex_rows, trelis_rows = apex["rows"], trelis["rows"]
    updated = sum(s["reference_source"] == "uploaded_review" for s in data["samples"])
    retained = sum(s["reference_source"] == "prior_checked_reference" for s in data["samples"])
    assessments = sum(v == "looks_complete" for s in data["samples"] for v in s.get("draft_checks", {}).values())
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src data:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Chunk-size comparison · checked corrections</title><style>{CSS}{EXTRA_CSS}</style></head><body><main>
<header class="intro"><span class="eyebrow">Hinglish STT · corrected reference revision</span><h1>What your corrections change</h1>
<p>{updated} checked references from this file are incorporated; {retained} earlier checked references are retained. Saved model outputs are unchanged. Trelis remains in its original Devanagari/English script.</p>
<span class="pill">Development evidence · no final optimum yet</span><p class="small muted">{assessments} drafts were marked “looks complete” in the uploaded review. Each flag applies only to its exact model and chunk size; it does not approve other settings or establish word accuracy.</p></header>
<p class="note small"><strong>Names, quantities and readability remain separate checks.</strong> Your notes remain unresolved checks; completeness flags do not clear names, quantities or punctuation. Read the original notes alongside each passage. Punctuation is not scored by WER.</p>
<section class="grid score-grid" aria-label="Separate model results"><article id="apex-results" class="card"><h2>Apex · Roman output</h2>
<p class="small muted">Fixed panel: {len(apex['sample_ids'])} compatible checked passages. The native mixed-script reference is excluded from Apex scoring.</p>
{table(apex_rows, 'aggregate', 'Apex fixed reference panel')}<p class="small">{escape(finding(apex_rows))}</p>
<details id="boundary-sensitivity"><summary>Sensitivity: exclude excerpt 1’s uncertain ending</summary><p class="small">The earlier decision places the disputed ending outside the clip. This check excludes the entire passage, using the same remaining passages at every size.</p>
{table(apex_rows, 'boundary_certain_aggregate', 'Apex boundary-certain sensitivity')}<p class="small">{escape(finding(apex_rows, 'boundary_certain_aggregate'))}</p></details></article>
<article id="trelis-results" class="card"><h2>Trelis · Original script</h2><p class="small muted">Fixed panel: {len(trelis['sample_ids'])} checked passages. This includes the known omission case at every size, including its poor 30s result.</p>
{table(trelis_rows, 'aggregate', 'Trelis fixed reference panel')}<p class="small">{escape(finding(trelis_rows))}</p>
<p class="note small">The 30s omission passage contains a Devanagari name against an English reference. Its literal score includes that script penalty as well as recognition errors. It stays visible so its missing content cannot disappear from the comparison.</p>
<details id="script-sensitivity"><summary>Sensitivity: use only passages with stable scripts</summary><p class="small">Any English-reference passage with Devanagari at any tested size is excluded from every size in this sensitivity check. The native mixed-script Hinglish passage remains eligible. No output is transliterated.</p>
{table(trelis_rows, 'script_stable_aggregate', 'Trelis script-stable sensitivity')}<p class="small">{escape(finding(trelis_rows, 'script_stable_aggregate'))}</p></details></article></section>
<p class="notice small"><strong>Read each model’s table separately.</strong> Their reference panels differ, so these pooled numbers do not rank Apex against Trelis. WER counts changed, deleted and added words. Alignment deletions do not measure the percentage of spoken meaning captured.</p>
<details id="methods"><summary>Scoring method and remaining limitations</summary><p class="small">This revision rescores saved predictions against your corrected text; it does not rerun either model. New and revised references change the denominator, so earlier reported WER values are not directly comparable.</p>
<p class="small">Scoring preserves script and words while normalizing case and ordinary punctuation. Digits versus written-out numbers can count as errors; the “90 versus 95” distinction still matters. Punctuation quality is a separate readability issue and does not affect this WER. Your reference text and notes are reproduced literally below.</p>
<p class="small">This was a review assisted by visible model drafts, not a blind or held-out test. Completeness checks apply only to the specifically marked drafts. Names and quantities still need separate checking. No release acceptance or live-latency target is established by this report.</p>
<p class="small">The four older clips are about 25 seconds long: their 30s setting is a single unsplit ~25s clip. The two minute-long passages contain full 30s windows. All audio is covered by disjoint chunks; short final tails below one second are rebalanced with the preceding chunk. Actual boundaries are shown with each draft. Chunks are joined literally, with no deduplication, rewriting or Roman conversion.</p>
<p class="small">Broader held-out recordings, shifted chunk boundaries, overlap and end-to-end live tests are still needed to select a production setting.</p></details>
<section class="section" aria-labelledby="passages-heading"><h2 id="passages-heading">Listen against the checked text</h2><p class="small muted">The newly reviewed passages appear first. Both menus start at 15s, the setting assessed in this correction file. Select any size to inspect its unchanged model output.</p>
<div class="toolbar"><label class="stack grow small" for="sample-select">Passage<select id="sample-select"></select></label><span id="sample-progress" class="small muted" aria-live="polite"></span></div>
<div class="card"><div class="sample-heading"><div><h3 id="sample-title"></h3><p id="sample-source" class="source small muted"></p></div><span id="sample-kind" class="pill"></span></div>
<div class="audio-panel"><audio id="audio" controls preload="metadata" aria-label="Current passage audio"></audio><div class="row"><button id="back" type="button">−5 seconds</button><button id="forward" type="button">+5 seconds</button><label for="speed" class="small">Playback</label><select id="speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option></select><span id="sample-duration" class="small muted"></span></div></div>
<section class="reference-block" aria-labelledby="reference-heading"><h3 id="reference-heading">Checked reference</h3><span id="reference-kind" class="scope-label"></span><p id="reference-text" class="reference" dir="auto"></p><p id="reference-scope" class="note small" hidden></p></section>
<section id="notes-section" class="note" hidden><h3>Your notes</h3><p id="notes-text" class="notes-readback" dir="auto"></p></section>
<details id="previous-reference-details" hidden><summary>Earlier reference retained for comparison</summary><p class="small muted">This is the previous version, not the effective reference used for this revision’s scores.</p><p id="previous-reference" class="reference" dir="auto"></p></details>
<div class="grid section">{panel('apex', 'Apex · Roman Hinglish', 'Literal saved model output')}{panel('trelis', 'Trelis · Original script', 'Literal saved model output · no transliteration')}</div>
<div class="row controls-end"><button id="previous" type="button">← Previous passage</button><button id="next" type="button">Next passage →</button></div></div></section>
<footer class="footer"><p>This readback is immutable. Original references, model results and the uploaded correction file remain preserved separately. The HTML embeds private recordings and transcripts and runs offline without external scripts, fonts or services.</p><details id="identifiers"><summary>Source identifiers</summary><p>Corrections: {escape(data['source']['corrections_filename'])}</p><p class="hash">Corrections SHA-256: {escape(data['source']['corrections_sha256'])}</p><p class="hash">Manifest SHA-256: {escape(binding['manifest_sha256'])}</p><p class="hash">Apex results SHA-256: {escape(binding['model_results_sha256']['apex'])}</p><p class="hash">Trelis results SHA-256: {escape(binding['model_results_sha256']['trelis'])}</p></details></footer>
</main><script type="application/json" id="study-data">{encoded}</script><script>{JS}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.root, args.output, read_json(args.analysis))
    print(json.dumps({"output": str(args.output.resolve()), "sha256": file_digest(args.output)}, indent=2))
