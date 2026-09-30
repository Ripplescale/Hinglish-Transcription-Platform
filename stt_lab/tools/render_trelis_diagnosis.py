"""Render the controlled omission experiment without altering the user's review."""
import argparse
import base64
from html import escape
import json
from pathlib import Path
from render_expanded_review import CSS, clock, call_label


def render(root):
    data=json.loads((root/'diagnosis.json').read_text(encoding='utf-8'))
    cases=data['cases']
    required=['identical_english_30s_first','identical_english_30s_repeat','publisher_english_30s',
              'hindi_mixedcode_30s','english_mixedcode_30s','english_15s_part_1','english_15s_part_2']
    if any(k not in cases or cases[k]['status']!='ok' for k in required):
        raise ValueError('Wait for the seven controlled reruns to finish')
    baseline=data['original_result']['text']
    repeated=all(cases[k]['text']==baseline for k in required[:3])
    ended=all(cases[k]['raw_generation_traces'][-1]['last_token_id']==cases[k]['raw_generation_traces'][-1]['eos_token_id'] for k in required[:3])
    if not (repeated and ended):
        raise ValueError('Expected reproduction and early-end evidence was not established')
    a=(root/'first-half-part-1.wav').read_bytes()
    b=(root/'first-half-part-2.wav').read_bytes()
    # The exact diagnostic source is identified by its verified minute manifest.
    review=root.parent/'minute-review-20260922'
    original_audio=review/data['section']['chunks'][0]['audio']
    def player(raw):
        return '<audio controls preload="metadata" src="data:audio/wav;base64,'+base64.b64encode(raw).decode()+'"></audio>'
    def text(name):
        return '<p class="draft-text" data-case="'+name+'">'+escape(cases[name]['text'])+'</p>'
    cards=[]
    for name,title in [('hindi_mixedcode_30s','30 seconds · Hinglish with Hindi prefix'),('english_mixedcode_30s','30 seconds · Hinglish with English prefix'),('publisher_english_30s','30 seconds · Publisher’s explicit English prefix')]:
        cards.append(f'<section class="draft"><h3>{title}</h3>{text(name)}</section>')
    section=data['section'];start=section['start_seconds']
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; media-src data:; connect-src 'none'; object-src 'none'"><title>Trelis omission diagnosis · Section 10</title><style>{CSS}.draft-text{{font:17px/1.85 'Nirmala UI','Segoe UI',sans-serif}}.draft{{margin:12px 0}}audio{{max-width:520px}}.drafts{{align-items:start}}.body{{padding:22px 0}}.subclip{{padding-top:15px;border-top:1px solid #d7e2d9;margin-top:16px}}</style></head><body><main>
<div class="eyebrow">Controlled local reruns · Section 10</div><h1>Why was this passage missing?</h1>
<p>{escape(call_label(section['call_title']))}</p><p>Exact affected audio: <strong>{clock(start)}–{clock(start+30)}</strong>, the first 30 seconds of review minute 10.</p>{player(original_audio.read_bytes())}
<div class="notice"><strong>Confirmed:</strong> both identical reruns and the publisher’s English-prefix recipe reproduced the original short sentence. Raw token traces end with the model’s end-of-transcript token. The HTML did not discard a longer transcript, and the 440-token limit was not reached.</div>
<p>The shorter-window outputs below cover more of this passage. They remain unchecked machine drafts; more text is not by itself proof of accuracy. Names and boundary words still need your review.</p>
<div class="drafts"><section class="draft"><h2>Original 30-second result</h2><p class="draft-text" data-original>{escape(baseline)}</p><p class="meta">The identical reruns returned exactly the same text.</p></section><section class="draft"><h2>Two 15-second windows</h2><div class="subclip"><h3>{clock(start)}–{clock(start+15)}</h3>{player(a)}{text('english_15s_part_1')}</div><div class="subclip"><h3>{clock(start+15)}–{clock(start+30)}</h3>{player(b)}{text('english_15s_part_2')}</div></section></div>
<h2>Other tested settings</h2>{''.join(cards)}
<footer><p>All outputs use the same pinned Trelis checkpoint locally, with original scripts preserved. Language prefixes and window sizes vary as labelled. Your original comparison and corrections have not been overwritten. No corrected reference or accuracy score was manufactured.</p><p>Publisher recipe was reproduced with this laptop’s CPU float32 runtime; the publisher’s example uses CUDA bfloat16. This isolates the prompt recipe, not cross-hardware equivalence.</p></footer>
<details><summary>Raw trace for the repeated result</summary><pre>{escape(json.dumps(cases['identical_english_30s_first']['raw_generation_traces'],ensure_ascii=False,indent=2))}</pre></details></main></body></html>'''
    output=root/'section-10-diagnosis.html'
    if output.exists(): raise ValueError('Diagnosis HTML already exists')
    output.write_text(page,encoding='utf-8')
    print(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    render(p.parse_args().root.resolve())
