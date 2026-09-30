"""Render an offline, editable transcript review with explicit JSON save/import.

Transcripts and imported strings are always data. No uploads, localStorage,
external assets or automatic reference approval. Exact manifest/audio bindings
are required on import and before any draft is displayed.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_expanded_review import digest, inside

CSS = """
:root{--ink:#183b35;--muted:#64776e;--line:#d7e2d9;--green:#276b54;--paper:#fff;--wash:#f2f5ef}*{box-sizing:border-box}
body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.6 'Segoe UI',Arial,sans-serif}main{max-width:1120px;margin:auto;padding:38px 28px 80px}
h1{font-size:clamp(30px,4vw,44px);line-height:1.15;letter-spacing:-1px;margin:8px 0 18px}h2{font-size:23px;line-height:1.3;margin:0}h3{font-size:16px;margin:0 0 8px}p{margin:8px 0}
.eyebrow{text-transform:uppercase;font-size:11px;letter-spacing:1.5px;font-weight:750;color:var(--green)}.muted,.meta{color:var(--muted)}.meta{font-size:13px}.intro{max-width:880px}
.notice{background:#fff3d8;border:1px solid #e8d8ac;border-radius:12px;padding:16px 20px;margin:22px 0;font-size:14px}.toolbar{position:sticky;top:0;z-index:2;background:#f2f5eff5;backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:14px 0;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
button,.file-label{font:inherit;font-size:14px;font-weight:650;border:1px solid var(--green);border-radius:8px;padding:10px 16px;background:var(--green);color:white;cursor:pointer}.file-label{background:white;color:var(--green)}.file-input{position:absolute;width:1px;height:1px;opacity:0}button:focus-visible,.file-label:focus-within,a:focus-visible,summary:focus-visible,textarea:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #3e78c8;outline-offset:3px}
#status{font-size:13px;margin-left:auto}#progress{font-weight:650;color:var(--green)}.jump{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}.jump a{padding:5px 11px;border:1px solid var(--line);border-radius:7px;background:white;font-size:13px;text-decoration:none;color:var(--green)}
.excerpt{scroll-margin-top:95px;margin:24px 0 32px;background:white;border:1px solid var(--line);border-radius:16px;overflow:hidden}.heading{padding:22px 24px;border-bottom:1px solid var(--line);background:#fafcf8;display:flex;gap:20px;justify-content:space-between;flex-wrap:wrap}.badge{display:inline-block;background:#e8f0e8;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:650}.score-audio{width:340px;max-width:100%}audio{width:100%;height:40px}.body{padding:22px 24px}.context{margin:12px 0 22px;border:1px solid var(--line);border-radius:9px;padding:10px 14px;font-size:13px}.context audio{max-width:500px}.context summary{cursor:pointer;font-weight:650}
.drafts{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:18px 0 24px}.draft{padding:17px 19px;border:1px solid var(--line);border-radius:10px;background:#f8faf5}.draft-text{white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.75}.unchecked{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#766133}
label.field{display:block;font-size:13px;font-weight:650;margin:15px 0 6px}textarea,input[type=text],select{width:100%;font:inherit;font-size:15px;border:1px solid #a9bcae;border-radius:8px;background:#fff;color:var(--ink);padding:10px 12px}textarea{min-height:140px;resize:vertical;line-height:1.7}.notes{min-height:78px}.annotation-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.checks{display:flex;flex-direction:column;gap:9px;margin-top:20px;font-size:14px}.checks label{display:flex;align-items:flex-start;gap:9px}.checks input{margin-top:6px;accent-color:var(--green)}.state{font-size:13px;font-weight:650;color:#79622b;margin-top:12px}.state.checked{color:var(--green)}details.provenance{font-size:12px;color:var(--muted);overflow-wrap:anywhere;margin-top:28px}footer{border-top:1px solid var(--line);padding-top:22px;color:var(--muted);font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:650px){main{padding:26px 15px 50px}.heading,.body{padding:18px}.drafts,.annotation-grid{grid-template-columns:1fr}.score-audio{width:100%}.toolbar{position:static}#status{margin-left:0;width:100%}.notice{padding:14px}h1{letter-spacing:-.5px}.excerpt{scroll-margin-top:12px}}
@media print{.toolbar,.jump,audio,.checks{display:none}body{background:white}main{padding:0}.excerpt{break-inside:avoid}.drafts{grid-template-columns:1fr 1fr}}
"""

JS = r"""'use strict';
(() => {
  const seed = JSON.parse(document.getElementById('review-data').textContent);
  const status = document.getElementById('status');
  let dirty = false;
  const languages = ['unconfirmed', 'Hinglish', 'English', 'Hindi', 'Other', 'Silence'];
  const cards = new Map(seed.samples.map(sample => [sample.sample_id, document.getElementById(sample.sample_id)]));
  const control = (card, key) => card.querySelector('[data-field="' + key + '"]');
  function read(card, sample) {
    const result = {sample_id: sample.sample_id, audio_sha256: sample.audio_sha256};
    for (const key of ['reference_roman', 'notes', 'language']) result[key] = control(card, key).value;
    for (const key of ['names', 'numbers']) result[key] = control(card, key).value.split('\n').map(s => s.trim()).filter(Boolean);
    for (const key of ['listened', 'review_completed', 'no_speech']) result[key] = control(card, key).checked;
    result.human_reviewed = result.listened && result.review_completed &&
      (result.no_speech ? !result.reference_roman.trim() && !result.names.length && !result.numbers.length : Boolean(result.reference_roman.trim()));
    result.reference_status = result.human_reviewed ? 'reviewed' : 'pending';
    return result;
  }
  function collect() {
    const samples = seed.samples.map(sample => read(cards.get(sample.sample_id), sample));
    return {version: 1, kind: seed.kind, screening_sha256: seed.screening_sha256,
      exported_at: new Date().toISOString(), human_reviewed: samples.every(s => s.human_reviewed), samples};
  }
  function update() {
    const review = collect();
    document.getElementById('progress').textContent = review.samples.filter(s => s.human_reviewed).length + ' / ' + review.samples.length + ' checked';
    for (const sample of review.samples) {
      const state = cards.get(sample.sample_id).querySelector('.state');
      state.textContent = sample.human_reviewed ? 'Checked by you — ready for reference validation' : 'Pending your review';
      state.classList.toggle('checked', sample.human_reviewed);
      if (sample.no_speech && (sample.reference_roman.trim() || sample.names.length || sample.numbers.length)) state.textContent = 'Pending: clear transcript and annotations or uncheck “No speech”';
    }
  }
  function validateImport(value) {
    if (!value || Array.isArray(value) || value.version !== 1 || value.kind !== seed.kind || value.screening_sha256 !== seed.screening_sha256)
      throw Error('This file belongs to a different review or has an unsupported format.');
    if (!Array.isArray(value.samples) || value.samples.length !== seed.samples.length) throw Error('The imported file must contain all review excerpts.');
    const seen = new Set();
    const samples = value.samples.map(sample => {
      if (!sample || typeof sample !== 'object' || seen.has(sample.sample_id)) throw Error('Invalid or duplicate excerpt.');
      const expected = seed.samples.find(s => s.sample_id === sample.sample_id);
      if (!expected || sample.audio_sha256 !== expected.audio_sha256) throw Error('Excerpt audio identity does not match.');
      seen.add(sample.sample_id);
      for (const key of ['reference_roman', 'notes']) if (typeof sample[key] !== 'string' || sample[key].length > 30000) throw Error('Invalid correction text.');
      if (!languages.includes(sample.language)) throw Error('Invalid language label.');
      for (const key of ['names', 'numbers']) if (!Array.isArray(sample[key]) || sample[key].length > 100 || sample[key].some(s => typeof s !== 'string' || s.length > 1000)) throw Error('Invalid entity annotations.');
      for (const key of ['listened', 'review_completed', 'no_speech']) if (typeof sample[key] !== 'boolean') throw Error('Explicit review flags are missing.');
      const checked = sample.listened && sample.review_completed && (sample.no_speech ? !sample.reference_roman.trim() && !sample.names.length && !sample.numbers.length : Boolean(sample.reference_roman.trim()));
      if (sample.human_reviewed !== checked || sample.reference_status !== (checked ? 'reviewed' : 'pending')) throw Error('Review status conflicts with the explicit listening/correction flags.');
      return sample;
    });
    if (value.human_reviewed !== samples.every(s => s.human_reviewed)) throw Error('Overall review status is inconsistent.');
    return samples;
  }
  document.querySelectorAll('textarea,input[type=checkbox],select').forEach(element => element.addEventListener('input', () => {
    dirty = true; status.textContent = 'Unsaved edits — download JSON to keep them.'; update();
  }));
  document.getElementById('save-review').addEventListener('click', () => {
    const data = collect();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url;
    link.download = 'hinglish-review-batch1-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    dirty = false; status.textContent = 'Download requested. Keep the JSON file and import it to resume.';
  });
  document.getElementById('import-review').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (file.size > 2000000) throw Error('Review file is too large.');
      const samples = validateImport(JSON.parse(await file.text()));
      if (dirty && !window.confirm('Replace unsaved edits with the imported review? Download first if you want to keep both versions.')) return;
      for (const sample of samples) {
        const card = cards.get(sample.sample_id);
        for (const key of ['reference_roman', 'notes', 'language']) control(card, key).value = sample[key];
        for (const key of ['names', 'numbers']) control(card, key).value = sample[key].join('\n');
        for (const key of ['listened', 'review_completed', 'no_speech']) control(card, key).checked = sample[key];
      }
      dirty = false; status.textContent = 'Imported local review JSON.'; update();
    } catch (error) { status.textContent = 'Import refused: ' + error.message; }
    finally { event.target.value = ''; }
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  update();
})();
"""


def clock(seconds):
    ms = round(seconds * 1000)
    minutes, remainder = divmod(ms, 60000)
    secs, millis = divmod(remainder, 1000)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def call_label(title):
    match = re.match(r"Meeting (\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", title)
    if not match:
        return title
    date = datetime.strptime(match[1], "%Y-%m-%d").strftime("%d %B %Y")
    return f"{date} · recording {match[2]}:{match[3]}:{match[4]}"


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render(screening, output=None, models=("apex", "swift")):
    screening = Path(screening).resolve(strict=True)
    root = screening.parent
    document = json.loads(screening.read_text(encoding="utf-8"))
    if document.get("review_phase") != "expanded_development_review_batch_1":
        raise ValueError("Expected the expanded development review manifest")
    if len({clip["id"] for clip in document["clips"]}) != len(document["clips"]):
        raise ValueError("Duplicate excerpt identity")
    manifest_sha = digest(screening)
    output = Path(output).resolve() if output else root / "review.html"
    if output.parent != root or output.exists():
        raise ValueError("Choose a new HTML file directly inside the private review folder")
    seed = json.loads((root / "user-references-pending.json").read_text(encoding="utf-8"))
    if seed["screening_sha256"] != manifest_sha or seed.get("human_reviewed") is not False:
        raise ValueError("Expected untouched pending reference template bound to this manifest")
    expected = {clip["id"]: clip["audio_sha256"] for clip in document["clips"]}
    if {sample["sample_id"]: sample["audio_sha256"] for sample in seed["samples"]} != expected:
        raise ValueError("Reference template audio bindings changed")
    if any(sample.get("reference_roman") or sample.get("human_reviewed") or sample.get("review_completed") or sample.get("listened") for sample in seed["samples"]):
        raise ValueError("Reference template must stay blank and pending; import corrections in the review page")
    reports = {}
    for model in models:
        if model not in ("apex", "swift", "trelis", "srota"):
            raise ValueError("Unsupported review model")
        path = root / "results" / f"{model}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("screening_sha256") != manifest_sha or result.get("model_id") != model:
            raise ValueError("Draft result belongs to different inputs")
        if result.get("network_attempts"):
            raise ValueError("Draft run recorded a blocked network attempt; investigate before review")
        reports[model] = (result, digest(path))
    cards = []
    calls = {call["id"]: call for call in document["calls"]}
    for index, clip in enumerate(document["clips"], 1):
        for key, hash_key in (("audio", "audio_sha256"), ("context_audio", "context_sha256")):
            if digest(inside(root, clip[key])) != clip[hash_key]:
                raise ValueError("Review audio changed")
        cid = escape(clip["id"], quote=True)
        call = calls[clip["call_id"]]
        drafts = []
        for model in models:
            result = reports.get(model, ({}, ""))[0].get("clips", {}).get(clip["id"])
            if result and result.get("status") == "ok":
                if result.get("audio_sha256") != clip["audio_sha256"]:
                    raise ValueError("Draft audio identity changed")
                draft = escape(result.get("text", ""))
            else:
                draft = "Draft not available yet. Your reference remains blank."
            drafts.append(f'<section class="draft"><h3>{escape(model.title())}</h3><div class="unchecked">Unchecked model draft</div><p class="draft-text">{draft}</p></section>')
        options = "".join(f'<option value="{name}">{name if name != "unconfirmed" else "Confirm after listening"}</option>' for name in ("unconfirmed", "Hinglish", "English", "Hindi", "Other", "Silence"))
        def field(key, label, cls="", placeholder=""):
            return f'<label class="field" for="{cid}-{key}">{label}</label><textarea id="{cid}-{key}" data-field="{key}" class="{cls}" placeholder="{placeholder}"></textarea>'
        cards.append(f'''<article class="excerpt" id="{cid}">
<div class="heading"><div><span class="badge">Excerpt {index} · {escape(clip['expected_language'])} expected</span><h2>{escape(clip['call_id'])} · {clock(clip['start_seconds'])}–{clock(clip['end_seconds'])}</h2><p class="meta">{escape(call_label(call['title']))}</p><p class="meta">Scored clip: exactly {clip['duration_seconds']:g} seconds · reference pending</p></div>
<div class="score-audio"><p class="meta"><strong>Listen to the scored clip</strong></p><audio controls preload="none" src="{quote(clip['audio'], safe='/')}"></audio></div></div>
<div class="body"><details class="context"><summary>Need context at a boundary? Listen with 5 seconds on each side</summary><p>Source {clock(clip['context_start_seconds'])}–{clock(clip['context_start_seconds'] + clip['context_duration_seconds'])}. Only <strong>00:05–00:33</strong> in this context player belongs in the scored reference. The scored player above is the authority for clip contents.</p><audio controls preload="none" src="{quote(clip['context_audio'], safe='/')}"></audio></details>
<div class="drafts">{''.join(drafts)}</div>
{field('reference_roman', 'What was actually said inside the scored clip?', placeholder='Write your checked Roman Hinglish / English reference here. Model drafts are not copied automatically.')}
<div class="annotation-grid"><div>{field('names', 'Names — one per line', 'notes', 'Exact names heard inside this clip')}</div><div>{field('numbers', 'Numbers and units — one phrase per line', 'notes', 'For example: 165 to 190 ml')}</div></div>
<label class="field" for="{cid}-language">Language heard in this excerpt</label><select id="{cid}-language" data-field="language">{options}</select>
{field('notes', 'Uncertain words, boundaries, missing speech, noise or overlapping speakers', 'notes', 'Flag uncertainty rather than guessing; context words outside the clip do not count.')}
<div class="checks"><label><input type="checkbox" data-field="listened">I listened to the scored clip.</label><label><input type="checkbox" data-field="review_completed">I checked my correction, names and numbers against that clip.</label><label><input type="checkbox" data-field="no_speech">No intelligible speech in this scored clip (leave transcript blank).</label></div><p class="state">Pending your review</p></div></article>''')
    script_hash = base64.b64encode(hashlib.sha256(JS.encode("utf-8")).digest()).decode("ascii")
    policy = f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; media-src 'self' file:; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    title = "Hinglish transcript review — batch 1"
    links = "".join(f'<a href="#{escape(clip["id"], quote=True)}">{i}</a>' for i, clip in enumerate(document["clips"], 1))
    source_info = {"screening_sha256": manifest_sha, "model_results_sha256": {model: data[1] for model, data in reports.items()},
                   "model_revisions": {model: data[0].get("model_spec", {}).get("source_revision") for model, data in reports.items()}}
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{escape(policy, quote=True)}"><title>{title}</title><style>{CSS}</style></head><body><main>
<div class="eyebrow">Private local review · references pending</div><h1>{title}</h1>
<div class="intro"><p><strong>{len(document['clips'])} new excerpts · {sum(c['duration_seconds'] for c in document['clips']) / 60:g} minutes of scored audio.</strong> Compare the drafts, listen, and write what was actually said. Check a few excerpts at a time; download your review JSON to save progress.</p><p class="muted">These excerpts come from previously screened calls. They are development examples, not an untouched final test. Expected language labels also need your confirmation.</p></div>
<div class="notice"><strong>Use only speech inside the scored clip.</strong> Each context player adds five seconds before and after to clarify clipped words. Do not add those extra words to the reference. Nothing is scored until you explicitly check your corrections.</div>
<div class="toolbar"><button id="save-review" type="button">Download review JSON</button><label class="file-label">Import saved review<input id="import-review" class="file-input" type="file" accept=".json,application/json"></label><span id="progress">0 / {len(document['clips'])} checked</span><span id="status" role="status" aria-live="polite">Edits stay in this page until you download them.</span></div>
<nav class="jump" aria-label="Jump to excerpt">{links}</nav>{''.join(cards)}
<footer><p>Closing this page without downloading loses edits. Import the downloaded JSON to resume. This page makes no remote requests and does not use browser storage. Model drafts are unreviewed; no accuracy or release decision is implied.</p><p>Further evaluation: review 30–60 minutes, freeze scoring rules, assess names and quantities, and validate live speed alongside recording and speakers. Calls 02 and 04 remain reserved from this batch, with their earlier screening exposure recorded in coverage-plan.json.</p></footer>
<details class="provenance"><summary>Input hashes and model revisions</summary><pre>{escape(json.dumps(source_info, indent=2))}</pre></details>
<script id="review-data" type="application/json">{safe_json(seed)}</script><script>{JS}</script></main></body></html>'''
    output.write_text(html, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--models", nargs="+", default=["apex", "swift"])
    args = parser.parse_args()
    print(render(args.screening, args.output, tuple(args.models)))


if __name__ == "__main__":
    main()
