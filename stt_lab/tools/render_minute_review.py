"""Self-contained offline Apex/Trelis review; raw model text is never romanized."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_expanded_review import CSS, JS, clock, call_label, safe_json
from prepare_expanded_review import inside, digest


def render(root, output):
    root = root.resolve(strict=True)
    output = output.resolve()
    if output.parent != root or output.exists() or any(p.lower().startswith('onedrive') for p in root.parts):
        raise ValueError('Choose a new HTML file in the private review folder')
    manifest = root / 'review-manifest.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('kind') != 'sttbench_minute_review' or len(data['sections']) != 12:
        raise ValueError('Expected the twelve minute review')
    manifest_hash = digest(manifest)
    reports = {}
    for model in ('apex', 'trelis'):
        report = json.loads((root / 'results' / f'{model}.json').read_text(encoding='utf-8'))
        if report['model_id'] != model or report['manifest_sha256'] != manifest_hash or report['network_attempts']:
            raise ValueError('Draft provenance does not match')
        reports[model] = report
    seed = {'version': 1, 'kind': 'apex_trelis_minute_review_original_script',
            'screening_sha256': manifest_hash, 'human_reviewed': False, 'samples': []}
    cards = []
    for section in data['sections']:
        sid = section['id']
        audio = inside(root, section['audio'])
        if digest(audio) != section['audio_sha256']:
            raise ValueError('Audio changed')
        with wave.open(str(audio), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (1, 2, 16000, 960000):
                raise ValueError('Expected exactly sixty seconds of PCM16 mono audio')
        seed['samples'].append({'sample_id': sid, 'audio_sha256': section['audio_sha256'],
            'reference_text': '', 'notes': '', 'names': [], 'numbers': [], 'language': 'unconfirmed',
            'listened': False, 'review_completed': False, 'no_speech': False,
            'human_reviewed': False, 'reference_status': 'pending'})
        drafts = []
        for model in reports:
            paragraphs = []
            for chunk in section['chunks']:
                pred = reports[model]['chunks'][chunk['id']]
                if pred['status'] != 'ok' or pred['audio_sha256'] != chunk['audio_sha256'] or digest(inside(root, chunk['audio'])) != chunk['audio_sha256']:
                    raise ValueError('Incomplete or mismatched draft')
                paragraphs.append(f'<p class="draft-text" data-model="{model}" data-chunk="{chunk["id"]}">{escape(pred["text"])}</p>')
            title = 'Apex · Roman Hinglish' if model == 'apex' else 'Trelis · Original script'
            drafts.append(f'<section class="draft"><h3>{title}</h3><span class="unchecked">Unreviewed machine draft</span>{"".join(paragraphs)}</section>')
        def field(key, label, cls='notes'):
            return f'<label class="field" for="{sid}-{key}">{label}</label><textarea id="{sid}-{key}" data-field="{key}" class="{cls}"></textarea>'
        options = ''.join(f'<option value="{v}">{v}</option>' for v in ('unconfirmed','Hinglish','English','Hindi','Other','Silence'))
        audio_b64 = base64.b64encode(audio.read_bytes()).decode('ascii')
        cards.append(f'''<article class="excerpt" id="{sid}"><div class="heading"><div><span class="badge">Minute {section['number']:02d} / 12</span>
<h2>{escape(call_label(section['call_title']))}</h2><p class="meta">Recording position {clock(section['start_seconds'])}–{clock(section['end_seconds'])} · 60 seconds</p></div>
<div class="score-audio"><audio controls preload="none" src="data:audio/wav;base64,{audio_b64}"></audio><div class="player-tools"><button type="button" data-seek="-5" aria-label="Back five seconds">−5s</button><button type="button" data-seek="5" aria-label="Forward five seconds">+5s</button><label>Speed <select data-speed aria-label="Playback speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option></select></label></div></div></div>
<div class="body"><div class="drafts">{''.join(drafts)}</div>
{field('reference_text','What was actually said? Use Devanagari, Roman Hinglish or English as you prefer.','transcript')}
<div class="annotation-grid"><div>{field('names','Names — one per line')}</div><div>{field('numbers','Numbers and units — one phrase per line')}</div></div>
<label class="field" for="{sid}-language">Language heard</label><select id="{sid}-language" data-field="language">{options}</select>
{field('notes','Which draft is closer? Note unclear words, missing speech or overlapping speakers.')}
<div class="checks"><label><input type="checkbox" data-field="listened">I listened to the full minute.</label><label><input type="checkbox" data-field="review_completed">I checked my transcript, names and numbers.</label><label><input type="checkbox" data-field="no_speech">No intelligible speech (leave transcript blank).</label></div><p class="state">Pending your review</p></div></article>''')
    # The older JSON schema's Roman-only field is intentionally not reused.
    js = JS.replace('reference_roman', 'reference_text').replace('hinglish-review-batch1-', 'apex-trelis-review-')
    js = js.replace("querySelectorAll('textarea,input[type=checkbox],select')", "querySelectorAll('[data-field]')")
    js += '''
document.querySelectorAll('.excerpt').forEach(card => {
  const audio = card.querySelector('audio');
  card.querySelectorAll('[data-seek]').forEach(button => button.addEventListener('click', () => {
    audio.currentTime = Math.max(0, Math.min(Number.isFinite(audio.duration) ? audio.duration : 60, audio.currentTime + Number(button.dataset.seek)));
  }));
  card.querySelector('[data-speed]').addEventListener('change', event => {audio.playbackRate = Number(event.target.value);});
  audio.addEventListener('play', () => document.querySelectorAll('audio').forEach(other => {if(other !== audio) other.pause();}));
});
'''
    js_hash = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    csp = f"default-src 'none'; script-src 'sha256-{js_hash}'; style-src 'unsafe-inline'; media-src data:; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    links = ''.join(f'<a href="#{s["id"]}">{s["number"]:02d}</a>' for s in data['sections'])
    provenance = {'manifest_sha256': manifest_hash, 'model_results_sha256': {m: digest(root/'results'/f'{m}.json') for m in reports},
                  'model_revisions': {m:r['model_spec']['source_revision'] for m,r in reports.items()},
                  'trelis_romanization': False, 'clock_basis': data['clock_basis'], 'reference_status': 'pending'}
    css = CSS + '\n.draft-text{font-family:"Nirmala UI","Mangal","Segoe UI",sans-serif;font-size:17px;line-height:1.9}.transcript{min-height:210px}.player-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.player-tools button{padding:5px 10px}.player-tools label{display:flex;gap:8px;align-items:center;font-size:13px}.player-tools select{width:auto}.heading h2{font-size:20px}.drafts{align-items:start}footer{overflow-wrap:anywhere}'
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{escape(csp,quote=True)}"><title>Apex + Trelis · One-minute review</title><style>{css}</style></head><body><main>
<div class="eyebrow">Private listening workspace</div><h1>Apex + Trelis</h1><div class="intro"><p><strong>12 one-minute excerpts. Listen, compare, correct.</strong> These are the same new passages from your Word review. All audio is included in this HTML file.</p><p>Trelis stays in its original Devanagari / English script. Apex stays in Roman Hinglish. Neither draft has been rewritten or transliterated.</p></div>
<div class="notice">Correct the <strong>whole minute</strong>. Each draft combines two 30-second recognition windows; paragraph breaks are not speaker changes. Use whichever script is easiest for your corrections. Source offsets follow the decoded recording clock; word-level alignment is not implied.</div>
<div class="toolbar"><button id="save-review" type="button">Save corrections</button><label class="file-label">Load corrections<input id="import-review" class="file-input" type="file" accept=".json,application/json"></label><span id="progress">0 / 12 checked</span><span id="status" role="status" aria-live="polite">Save your corrections before closing.</span></div>
<nav class="jump" aria-label="Jump to minute">{links}</nav>{''.join(cards)}
<footer><p>Save corrections downloads a JSON file. Load that file here to resume; closing without saving loses edits. Nothing is uploaded and this page makes no network requests.</p><p>Profiles: Apex Q5 Vulkan; Trelis original Transformers CPU checkpoint. Trelis uses the mixed-code Hindi prefix on sections 1–8 and the English prefix on 9–12, based on the existing expected-language labels. These labels still need your confirmation. No automatic language routing or speed comparison is claimed.</p><p>These previously screened calls are development examples. Your review is needed before accuracy can be assessed. Raw outputs remain intact, including any repetitions or errors.</p></footer>
<details class="provenance"><summary>Source and model details</summary><pre>{escape(json.dumps(provenance,indent=2))}</pre></details>
<script id="review-data" type="application/json">{safe_json(seed)}</script><script>{js}</script></main></body></html>'''
    output.write_text(html, encoding='utf-8')
    (root/'html-provenance.json').write_text(json.dumps({**provenance,'html_sha256':digest(output),'sections':12,'audio_seconds':720},indent=2),encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(render(args.root, args.output))
