"""Render measured conversion/replay evidence as a private offline HTML report."""
from __future__ import annotations
import argparse
from datetime import date
from html import escape
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def e(value): return escape(str(value), quote=True)
def seconds(value): return 'Unavailable' if value is None else f'{value:.2f}s'
def percent(value): return 'Unavailable' if value is None else f'{value:.1%}'
def link(path, output): return quote(Path(os.path.relpath(path, output.parent)).as_posix(), safe='/')
def profile(doc):
    cfg=doc['runtime_config']
    name=Path(cfg.get('artifact_path', 'official-float32')).stem
    spec=doc.get('model_spec',{})
    model=doc.get('model_id') or spec.get('model_id') or spec.get('id') or 'Unknown model'
    precision={'ggml-model':'F16', 'ggml-model-q5_0':'Q5', 'official-float32':'original float32'}.get(name,name)
    device={'cpu':'CPU', 'vulkan':'GPU (Vulkan)'}.get(cfg.get('device','cpu'),cfg.get('device','cpu'))
    return f"{model.title()} / {device} / {precision}"


CSS='''*{box-sizing:border-box}body{margin:0;background:#f2f5f1;color:#183a31;font:16px/1.6 "Segoe UI",sans-serif}
main{max-width:1100px;margin:auto;padding:38px 26px}h1{font-size:38px;line-height:1.15;letter-spacing:-1px}h2{font-size:24px;margin:0 0 12px}
h3{font-size:18px;overflow-wrap:anywhere}a{color:#17674b;text-underline-offset:3px}.muted{color:#62786e;font-size:14px}
.card{background:white;border:1px solid #d7e2d9;border-radius:14px;padding:24px;margin:22px 0}.notice{background:#fff4d8;border-color:#ead6a4}
.tag{font-size:12px;border-radius:16px;background:#e5eee7;padding:5px 10px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:18px 0}
.stat strong{font-size:29px;display:block}.stat span{font-size:13px;color:#536d60}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:11px 12px;text-align:left;border-bottom:1px solid #e0e8e1;vertical-align:top}th{background:#eff4ee}
.plot{width:100%;height:220px}.axis{display:flex;justify-content:space-between;font-size:12px;color:#62786e}.legend{font-size:13px;margin:6px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f1;padding:14px;font:12px/1.5 Consolas,monospace}summary{cursor:pointer;font-size:14px}
@media(max-width:650px){main{padding:22px 15px}h1{font-size:31px}.card{padding:18px}.grid{grid-template-columns:1fr 1fr;gap:15px}.stat strong{font-size:25px}}
'''


def chart(doc):
    rows=doc['rows']; duration=doc['summary']['planned_audio_seconds']
    maximum=max(20, max((r.get('oldest_audio_delay_seconds',0) for r in rows),default=0)*1.1)
    def points(field):
        return ' '.join(f"{r['source_end_seconds']/duration*1000:.2f},{210-r[field]/maximum*200:.2f}" for r in rows)
    y=210-15/maximum*200
    return (f'<div class="legend">Blue: oldest audio to ASR result · Grey: waiting queue · Dashed line: 15 seconds<br>Vertical scale: 0–{maximum:.0f} seconds</div>'
            f'<svg class="plot" viewBox="0 0 1000 220" preserveAspectRatio="none" role="img" aria-label="Measured delay over the planned source interval">'
            f'<line x1="0" y1="{y}" x2="1000" y2="{y}" stroke="#aa8137" stroke-dasharray="8 6"/>'
            f'<polyline points="{points("oldest_audio_delay_seconds")}" fill="none" stroke="#20647a" stroke-width="2" vector-effect="non-scaling-stroke"/>'
            f'<polyline points="{points("queue_delay_seconds")}" fill="none" stroke="#8d9f92" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'
            f'<div class="axis"><span>Source start</span><span>{duration/60:g} minutes planned</span></div>')


def render(run_paths, probe_paths, baseline_path, review_path, output):
    if output.exists(): raise ValueError('Choose a new report path')
    for path in [*run_paths,*probe_paths,baseline_path,review_path]:
        if not path.is_file(): raise ValueError(f'Missing report artifact: {path}')
    runs=[(p,read(p)) for p in run_paths]; probes=[(p,read(p)) for p in probe_paths]
    baseline=read(baseline_path)
    baseline_hash=hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    for p,d in probes:
        if d.get('baseline_sha256') != baseline_hash:
            raise ValueError(f'Conversion probe belongs to another reviewed baseline: {p}')
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'">',
        '<title>Local STT speed and conversion results</title>',f'<style>{CSS}</style><main>',
        f'<p class="muted">LOCAL BENCHMARK · GENERATED {date.today().isoformat()}</p><h1>Speed and conversion results</h1>',
        f'<p><a href="{link(review_path,output)}">Open the new 12-excerpt review pack</a> · 5 minutes 36 seconds of new audio; references await your review.</p>',
        '<p class="muted">The new review pack contains original Transformers Apex and Swift drafts. The converted F16 and Q5 scores below use only the existing four checked excerpts. Your new corrections will support a later converted-profile evaluation; the larger review pack is not verified Q5 accuracy evidence.</p>',
        '<section class="card notice"><h2>What these checks establish</h2><p>These runs test local speech recognition on saved audio. They do not include recording, speaker recognition, the calling application or the final user interface. All supplied calls have previous screening exposure. No model is qualified for release.</p>',
        '<p>The replay releases one 10-second chunk at each fixed source deadline. The reported oldest-audio delay includes collection, waiting and processing; the source-end delay excludes collection. Neither is a measured per-word or on-screen latency.</p></section>']
    parts+=['<section class="card"><h2>Conversion and quantization</h2><p class="muted">F16 is the larger converted copy; Q5 stores weights at lower precision to reduce size. Four previously checked excerpts, 216 reference words. Strict Roman text scoring; lower is better. Converted CLI decoding differs from Transformers, including timestamp generation. This measures the complete runtime profile, not isolated quantization loss.</p>',
            '<div class="table-wrap"><table><tr><th>Profile</th><th>Word error</th><th>Names</th><th>Quantities</th><th>Normalized text matches original</th><th>Timestamp checks</th></tr>']
    for model in ('apex','swift'):
        h=baseline['models'][model]['headline']; en=h['entities']
        parts.append(f'<tr><td>{model.title()} / original Transformers</td><td>{percent(h["wer"])}</td><td>{en["name"]["matched"]}/{en["name"]["reference"]}</td><td>{en["number"]["matched"]}/{en["number"]["reference"]}</td><td>Reference runtime</td><td>Not measured</td></tr>')
    for p,d in probes:
        if d['runtime_config'].get('timestamps') is not False:
            continue
        s=d['summary']; en=s['entities']; ts='Not requested' if s.get('timestamps_requested') is False else ('Basic checks pass' if s['all_timestamp_sanity_checks_pass'] else 'Not passed')
        parts.append(f'<tr><td><a href="{link(p,output)}">{e(profile(d))}</a><br>{s["successful_samples"]}/{s["expected_samples"]} accepted results</td><td>{percent(s["reference_wer"])}</td><td>{en["name"]["matched"]}/{en["name"]["reference"]}</td><td>{en["number"]["matched"]}/{en["number"]["reference"]}</td><td>{s["identical_normalized_samples"]}/{s["expected_samples"]} excerpts</td><td>{ts}</td></tr>')
    parts+=['</table></div><p class="muted">Names and quantities use frozen complete-phrase checks, not semantic accuracy. Failed profiles show counts only for accepted results and no complete word-error score. Text-only profiles make no timestamp claim; the saved commands record whether timestamp generation was disabled.</p></section>']
    timestamp_probes=[(p,d) for p,d in probes if d['runtime_config'].get('timestamps') is not False]
    if timestamp_probes:
        parts+=['<section class="card notice"><h2>Timestamp support needs further work</h2><p>Separate timestamp-generation probes are retained below. Out-of-bounds offsets were rejected rather than clamped. Text-only recognition can still be measured, but playback alignment is not qualified.</p><details><summary>Inspect timestamp probe results</summary><div class="table-wrap"><table><tr><th>Profile</th><th>Accepted clips</th><th>Result</th></tr>']
        for p,d in timestamp_probes:
            s=d['summary']; errors=sorted({r['result'].get('error',{}).get('code','') for r in d['samples'] if r['result']['status']!='ok'})
            parts.append(f'<tr><td><a href="{link(p,output)}">{e(profile(d))}</a></td><td>{s["successful_samples"]}/{s["expected_samples"]}</td><td>{e(", ".join(errors) or "Basic bounds check passed; spoken alignment unverified")}</td></tr>')
        parts+=['</table></div></details></section>']
    parts.append('<h2>Sustained processing</h2>')
    for p,d in runs:
        s=d['summary']; paced=d['mode']=='paced'
        parts.append(f'<section class="card"><span class="tag">{e(d["state"])} · {e(d["mode"])}</span><h3>{e(profile(d))}</h3>')
        stats=[(f'{s["successful_chunks"]}/{s["expected_chunks"]}','Successful chunks'),(f'{s["processed_audio_seconds"]/60:.1f} min','Audio attempted'),(seconds(s['service_p95_seconds']),'95th-percentile processing time')]
        if paced: stats += [(seconds(s['oldest_audio_delay_p95_seconds']),'95th-percentile oldest-audio delay'),(seconds(s['source_end_delay_p95_seconds']),'95th-percentile source-end delay'),(seconds(s['maximum_queue_seconds']),'Largest waiting queue')]
        parts.append('<div class="grid">'+''.join(f'<div class="stat"><strong>{e(v)}</strong><span>{e(label)}</span></div>' for v,label in stats)+'</div>')
        if paced:
            parts.append(chart(d))
            parts.append(f'<p>{s["oldest_audio_deadline_misses"]} of {s["attempted_chunks"]} attempted chunks missed the 15-second oldest-audio deadline or failed. End waiting queue: {seconds(s["end_queue_seconds"])}. After-first-chunk oldest-audio p95: {seconds(s["after_first_oldest_audio_delay_p95_seconds"])}.</p>')
            if d.get('deadline_miss_budget') is not None:
                parts.append(f'<p class="muted">The conservative replay target permits at most {d["deadline_miss_budget"]} deadline misses across {s["expected_chunks"]} planned chunks. The run stops once that budget is exceeded, because later chunks cannot restore a 95% pass rate.</p>')
        else: parts.append('<p>Unpaced capacity probe. These processing times do not establish real-time latency or queue stability.</p>')
        parts.append(f'<p class="muted">Failed chunks: {s["failed_chunks"]}. Unattempted chunks: {s["unattempted_chunks"]}.</p>')
        if d['state'] != 'complete' or not s['complete']:
            stop=d.get('stop_reason') or ('Still running' if d['state']=='running' else 'No stop reason recorded')
            parts.append(f'<p><strong>Incomplete run.</strong> Stop reason: {e(stop)}. Its partial statistics cannot qualify the planned call.</p>')
        parts.append(f'<p class="muted">First-chunk processing: {seconds(s["cold_first_service_seconds"])}. Model cache reused on {s["model_cache_reused_chunks"]} chunks. Operating-system file cache was not controlled.</p>')
        parts.append(f'<details><summary>Settings and reproducibility</summary><pre>{e(json.dumps({"runtime":d["runtime_config"],"conditions":d["conditions"],"input_sha256":d["input_sha256"],"network_attempts":d["network_attempts"]},indent=2))}</pre><a href="{link(p,output)}">Full measured results</a></details></section>')
    parts+=['<section class="card notice"><h2>Remaining before an app release</h2><p>Review the new reference excerpts; expand checked coverage toward 30–60 minutes; obtain genuinely unseen calls for the final test; validate capture, speaker labels and screen updates together. English summaries remain on the agreed local Qwen model.</p></section>', '</main></html>']
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(''.join(parts),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',nargs='+',required=True,type=Path);p.add_argument('--probes',nargs='*',default=[],type=Path)
    p.add_argument('--baseline-scores',required=True,type=Path);p.add_argument('--review',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();render(a.runs,a.probes,a.baseline_scores,a.review,a.output)
    print(str(a.output.resolve()))
