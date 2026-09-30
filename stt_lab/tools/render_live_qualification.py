"""Render measured live-worker replay evidence without claiming device/UI validation."""
import argparse
from html import escape
import json
from pathlib import Path


def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def number(value):return '—' if value is None else f'{value:.2f}'


def render(root):
    manifest=load(root/'manifest.json');runs=[]
    for directory in (root/'apex-20-paced',root/'trelis-20'):
        if (directory/'summary.json').exists():
            runs.append((directory,load(directory/'summary.json')))
    table=[];details=[]
    for directory,r in runs:
        state=('Completed' if r['state']=='complete' else 'Stopped: '+str(r.get('stopped_reason') or r['state']))
        table.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in [r['profile'],state,
          number(r['window_end_to_ready_p50_seconds']),number(r['window_end_to_ready_p95_seconds']),
          number(r['adapter_realtime_factor']),number(r['max_observed_backlog_seconds']),
          number(r['post_stop_drain_seconds']),r['tail_processed']])+'</tr>')
        observations=load(directory/'observations.json')
        status=load(Path(r['raw_status']));obs={s['id']:s for s in observations['segments']}
        segments=[]
        for s in status.get('segments',[]):
            o=obs.get(s['id'],{})
            lag=o.get('ready_elapsed_seconds',s['end_seconds'])-s['end_seconds']
            segments.append(f'<details><summary>{s["start_seconds"]:.0f}–{s["end_seconds"]:.0f}s · {escape(s["source_track"])} · ready after {lag:.2f}s · {escape(", ".join(s["quality_flags"]) or "no heuristic flags")}</summary><pre>{escape(s["text"] or "[Verified digital silence]")}</pre></details>')
        details.append(f'<section><h2>{escape(r["profile"])}</h2><p>First nonzero window: {number(r["first_nonzero_window_end_to_ready_seconds"])}s after its end. Later p95: {number(r["subsequent_window_end_to_ready_p95_seconds"])}s. Median interval between nonzero updates: {number(r["nonzero_update_cadence_p50_seconds"])}s.</p><p>Input: {r["last_committed_seconds"]}s. Largest input commit delay: {number(r["max_commit_lateness_seconds"])}s. Future audio exposed: {r["future_audio_exposed"]}.</p><p><a href="{directory.name}/summary.json">Metrics JSON</a> · <a href="{directory.name}/observations.json">Observed timings</a> · <a href="{directory.name}/job/status.json">Raw worker results</a></p>{"".join(segments)}</section>')
    doc='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Live transcription qualification</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{max-width:1180px;margin:40px auto;padding:0 22px;color:#17332c;background:#f5f8f6;font:16px/1.55 system-ui}h1,h2{line-height:1.2}table{width:100%;border-collapse:collapse;background:white}td,th{padding:12px;text-align:left;border-bottom:1px solid #ccd9d1}th{background:#e5eee8}section{background:white;border:1px solid #ccd9d1;border-radius:12px;padding:24px;margin-top:24px}details{border-top:1px solid #dae3dc;padding:10px 0}summary{cursor:pointer}pre{white-space:pre-wrap;font:inherit;color:#183b3d}a{color:#096f64}.scroll{overflow:auto}.note{border-left:4px solid #c78935;padding-left:18px}</style><h1>Live transcription: measured worker replay</h1>'''
    doc+='<p class="note">This feeds saved speech into the actual application worker at wall-clock speed. It does not test real microphones, loopback devices, a calling application, speaker identity or native UI rendering.</p><p>Latency starts at the <strong>end of a 20-second audio window</strong> and ends when the durable transcript checkpoint is observed. A word near the start of the window waits roughly another 20 seconds before that processing delay. These results do not establish a 5–15-second word-to-display promise.</p>'
    doc+='<div class="scroll"><table><thead><tr>'+''.join(f'<th>{x}</th>' for x in ['Profile','Outcome','Median end-to-ready (s)','p95 end-to-ready (s)','Adapter RTF','Max observed backlog (s)','Final drain (s)','5s tail processed'])+'</tr></thead><tbody>'+''.join(table)+'</tbody></table></div>'
    doc+='<p>RTF below 1 means adapter compute is faster than its input audio; it excludes other-track work, retry work and waiting for audio. Backlog includes the partially accumulated next window. All-zero opposite tracks exercise the journal workflow with no model inference. The final 40 seconds deliberately feed the same speech to both tracks as a simultaneous load test.</p><section><h2>Input and reproducibility</h2><p>'+escape(manifest['routing'])+'</p><p>Five existing 60-second clips include English and Hinglish; five additional seconds are known digital silence. Chunk WAVs are synchronized to disk and journaled only after their audio clock has elapsed. All raw outputs and decoding metadata are retained.</p><p><a href="manifest.json">Input/source hashes</a> · <a href="runtime.json">Runtime configuration</a></p></section>'
    doc+=''.join(details)+'</html>'
    target=root/'comparison.html';target.write_text(doc,encoding='utf-8');return str(target)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    print(render(p.parse_args().root))
