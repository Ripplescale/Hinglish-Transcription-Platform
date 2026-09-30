"""Render a private, offline review of sampled calls and literal machine drafts.

This renderer never opens audio contents, invokes inference, or contacts a
network. Result files must belong to the exact screening.json bytes supplied.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
from urllib.parse import quote


CSS = """
:root{color-scheme:light;--ink:#172c31;--muted:#637578;--line:#dce5e2;--paper:#fff;
--canvas:#f4f7f5;--green:#215d4c;--pale:#e9f3ed;--amber:#79551d;--amber-bg:#fff6df}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--canvas);
color:var(--ink);font:15px/1.6 "Segoe UI",Arial,sans-serif}a{color:var(--green);text-underline-offset:3px}
a:focus-visible,audio:focus-visible,summary:focus-visible{outline:3px solid #568dce;outline-offset:4px}
.shell{max-width:1530px;margin:auto;display:grid;grid-template-columns:250px minmax(0,1fr);gap:36px;padding:36px}
aside{position:sticky;top:30px;height:calc(100vh - 60px);overflow:auto;padding-right:12px}
.brand{font-size:20px;font-weight:700;letter-spacing:-.5px}.eyebrow{text-transform:uppercase;
letter-spacing:1.8px;font-size:11px;font-weight:700;color:var(--muted)}.brand .dot{color:#477761}
.nav-label{margin-top:38px;margin-bottom:12px}.call-link{display:block;text-decoration:none;padding:12px;
margin:5px 0;border:1px solid transparent;border-radius:10px;color:var(--ink);overflow-wrap:anywhere}
.call-link:hover{background:#fff;border-color:var(--line)}.call-link small{display:block;color:var(--muted);margin-top:3px}
.side-note{border-top:1px solid var(--line);padding-top:20px;margin-top:28px;font-size:12px;color:var(--muted)}
main{min-width:0}h1{font-size:clamp(29px,3vw,42px);line-height:1.16;letter-spacing:-1.25px;margin:12px 0}
h2{font-size:23px;line-height:1.3;margin:3px 0 8px;overflow-wrap:anywhere}h3{font-size:16px;margin:0}
p{margin:8px 0}.intro{max-width:780px;color:var(--muted)}.created{font-size:12px;color:var(--muted);margin:18px 0}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:24px 0}.metric{background:#fff;min-width:0;
border:1px solid var(--line);border-radius:12px;padding:17px 20px}.metric strong{font-size:25px;line-height:1.3;display:block}
.metric span{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.notice{border-left:3px solid #a58545;background:var(--amber-bg);
border-radius:0 9px 9px 0;padding:14px 18px;font-size:13px;margin:20px 0 28px}.notice strong{color:var(--amber)}
.call{scroll-margin-top:24px;margin:30px 0 40px}.call-header{display:flex;align-items:flex-start;
justify-content:space-between;gap:20px;margin-bottom:16px}.meta{font-size:13px;color:var(--muted)}
.call-header>div{min-width:0}.call-header .pill{max-width:100%;white-space:normal;overflow-wrap:anywhere}
.pill{display:inline-block;border-radius:100px;padding:4px 10px;font-size:11px;font-weight:650;
background:var(--pale);color:var(--green);white-space:nowrap}.pill.wait{background:#edf0f0;color:#627172}
.pill.failed{background:#fbece6;color:#88432f}.assessment{padding:15px 18px;border:1px solid var(--line);
border-radius:10px;background:#edf4ef;margin:14px 0;font-size:14px}.assessment p{white-space:pre-wrap}
.clip{border:1px solid var(--line);border-radius:14px;background:var(--paper);overflow:hidden;margin:17px 0;
box-shadow:0 4px 16px #213d2b03}.clip-top{padding:18px 22px;border-bottom:1px solid var(--line);
display:flex;gap:15px;align-items:center;justify-content:space-between;flex-wrap:wrap}.clip-heading{display:flex;gap:10px;
align-items:center;flex-wrap:wrap}.clip-heading .number{flex:none;width:28px;height:28px;border-radius:8px;background:#eff4f1;text-align:center;
font-size:12px;line-height:28px;font-weight:700}.time{font-variant-numeric:tabular-nums;font-size:13px;color:var(--muted)}
audio{width:320px;max-width:100%;height:36px}.drafts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
.draft{padding:21px 22px;min-width:0}.draft+.draft{border-left:1px solid var(--line)}.draft-head{display:flex;
align-items:center;justify-content:space-between;gap:12px}.draft-name{font-size:12px;text-transform:uppercase;
letter-spacing:1px;font-weight:750;min-width:0;overflow-wrap:anywhere}.transcript{white-space:pre-wrap;overflow-wrap:anywhere;font-size:16px;
line-height:1.8;margin:17px 0;min-height:54px}.empty{color:var(--muted);font-size:14px}.timings{font-size:11px;
color:var(--muted);display:flex;gap:12px;flex-wrap:wrap;border-top:1px solid #edf1ef;padding-top:10px;
font-variant-numeric:tabular-nums}.error{color:#88432f;background:#fff5ef;padding:10px;border-radius:6px;font-size:12px;
white-space:pre-wrap;overflow-wrap:anywhere}.warnings{font-size:11px;color:var(--muted);margin-top:10px}
details{font-size:12px;color:var(--muted);margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;
overflow-wrap:anywhere;background:#edf1ee;border-radius:7px;padding:12px;font:11px/1.7 Consolas,monospace}
.empty-call{padding:22px;background:#fff;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}
footer{font-size:12px;color:var(--muted);padding:20px 0;border-top:1px solid var(--line)}
footer code{white-space:normal;overflow-wrap:anywhere;word-break:break-all}
@media(max-width:1050px){.shell{grid-template-columns:200px minmax(0,1fr);gap:20px;padding:24px}.draft{padding:17px}}
@media(max-width:760px){.shell{display:block;padding:20px}aside{position:static;height:auto;padding:0;margin-bottom:28px}
.nav-label,.side-note{display:none}nav{display:flex;gap:6px;overflow:auto;margin-top:12px}.call-link{min-width:170px;
max-width:220px;background:#fff}.call-link small{font-size:11px}.drafts{grid-template-columns:1fr}
.draft+.draft{border-left:0;border-top:1px solid var(--line)}.metrics{gap:8px}.metric{padding:12px}.metric strong{font-size:22px}
.call-header{display:block}.call-header .pill{margin-top:10px}.clip-top{padding:15px}.clip-top audio{width:100%}
h1{letter-spacing:-.8px}.transcript{min-height:0}}
@media print{body{background:#fff}.shell{display:block;padding:0}aside,audio{display:none}.clip{break-inside:avoid}
.notice,.assessment{background:#f5f5f5}.metric{padding:10px}.transcript{font-size:12px}}
"""


def _text(value) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _display_title(value) -> str:
    """Format the first recording timestamp without changing its timezone."""
    raw = str(value)
    match = re.search(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})[Tt _-]+(\d{2})[:._-](\d{2})(?!\d)", raw)
    if match is None:
        match = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})[Tt _-]+(\d{2})(\d{2})(?:\d{2})?(?!\d)", raw)
    if match is None:
        return raw
    try:
        timestamp = datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return raw
    month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[timestamp.month - 1]
    return f"{timestamp.day:02d} {month} {timestamp.year} · {timestamp.hour:02d}:{timestamp.minute:02d}"


def _read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"),
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid number: {value}")))
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return result


def _local_path(value: str | Path) -> Path:
    text = str(value)
    if "://" in text or text.startswith(("\\\\", "//")):
        raise ValueError("Only local filesystem paths are supported")
    return Path(value).expanduser().resolve()


def _inside(root: Path, value: str | Path, relative=False) -> Path:
    text = str(value)
    if not text.strip() or "://" in text or text.startswith(("\\\\", "//")):
        raise ValueError("Media and report paths must be local paths inside the screening directory")
    if relative and (Path(text).is_absolute() or PureWindowsPath(text).drive):
        raise ValueError("Screening media paths must be relative to the screening directory")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Path escapes the screening directory")
    return path


def _number(value, field: str, positive=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{field} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _link(path: Path, output: Path) -> str:
    return _text(quote(os.path.relpath(path, output.parent).replace(os.sep, "/"), safe="/"))


def _timing(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return f"{value:.2f}s"
    return "unavailable"


def _notes(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def _draft(model_id: str, result: dict | None) -> str:
    if result is None:
        return f'<section class="draft"><div class="draft-head"><span class="draft-name">{_text(model_id)}</span><span class="pill wait">Pending</span></div><p class="transcript empty">No local result has been saved for this excerpt yet.</p></section>'
    status = result.get("status", "unknown")
    good = status == "ok"
    text = result.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Runtime transcript text must be a string")
    # Keep the literal returned text, including line breaks. Never rewrite or
    # select one model's phrasing to present it as a corrected reference.
    transcript = _text(text) if text else '<span class="empty">No transcript text returned.</span>'
    timing = result.get("timing") or {}
    error = result.get("error")
    error_text = error.get("message", error.get("code", "")) if isinstance(error, dict) else error
    warning_text = _notes(result.get("warnings", []))
    return (
        f'<section class="draft"><div class="draft-head"><span class="draft-name">{_text(model_id)}</span>'
        f'<span class="pill{"" if good else " failed"}">{"Draft ready" if good else _text(status)}</span></div>'
        f'<p class="transcript">{transcript}</p>'
        + (f'<p class="error">{_text(error_text)}</p>' if error_text else "")
        + '<div class="timings">'
        + f'<span>Inference {_timing(timing.get("inference_seconds"))}</span>'
        + f'<span>Load {_timing(timing.get("load_seconds"))}</span>'
        + f'<span>Total {_timing(timing.get("total_seconds"))}</span></div>'
        + (f'<details class="warnings"><summary>Runtime notes</summary><p>{_text(warning_text)}</p></details>' if warning_text else "")
        + '</section>'
    )


def render(screening: Path, output: Path) -> Path:
    screening = _local_path(screening)
    root = screening.parent
    output = _inside(root, _local_path(output))
    if output.suffix.lower() != ".html":
        raise ValueError("Output must be an .html file inside the screening directory")
    document = _read_json(screening)
    if document.get("version") != 1:
        raise ValueError("Unsupported screening manifest version")
    calls, clips = document.get("calls"), document.get("clips")
    if not isinstance(calls, list) or not isinstance(clips, list):
        raise ValueError("Screening calls and clips must be lists")
    digest = hashlib.sha256(screening.read_bytes()).hexdigest()
    all_inputs = {screening}
    call_map, clip_map, originals, audio_paths = {}, {}, {}, {}
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in call_map:
            raise ValueError("Call IDs must be unique, nonempty strings")
        _number(call.get("audio_seconds"), "audio_seconds")
        original = _inside(root, call["original"], relative=True)
        originals[call["id"]] = original
        all_inputs.add(original)
        call_map[call["id"]] = call
    for clip in clips:
        if not isinstance(clip, dict) or not isinstance(clip.get("id"), str) or not clip["id"] or clip["id"] in clip_map:
            raise ValueError("Clip IDs must be unique, nonempty strings")
        if clip.get("call_id") not in call_map:
            raise ValueError("Clip references an unknown call")
        start = _number(clip.get("start_seconds"), "start_seconds")
        duration = _number(clip.get("duration_seconds"), "duration_seconds", positive=True)
        if start + duration > call_map[clip["call_id"]]["audio_seconds"] + 0.1:
            raise ValueError("Clip extends beyond the recording duration")
        audio = _inside(root, clip["audio"], relative=True)
        audio_paths[clip["id"]] = audio
        all_inputs.add(audio)
        clip_map[clip["id"]] = clip
    results = {}
    result_dir = _inside(root, "results", relative=True)
    if result_dir.exists():
        for candidate in sorted(result_dir.glob("*.json")):
            path = _inside(root, candidate)
            all_inputs.add(path)
            data = _read_json(path)
            model = data.get("model_id")
            if not isinstance(model, str) or not model.strip() or model in results:
                raise ValueError("Result model IDs must be unique, nonempty strings")
            if data.get("screening_sha256") != digest:
                raise ValueError(f"{path.name}: results belong to a different screening manifest")
            rows = data.get("clips")
            if not isinstance(rows, dict) or any(key not in clip_map or not isinstance(value, dict) for key, value in rows.items()):
                raise ValueError(f"{path.name}: results reference unknown or invalid clips")
            results[model] = data
    assessment_path = _inside(root, "assessments.json", relative=True)
    all_inputs.add(assessment_path)
    assessments = _read_json(assessment_path) if assessment_path.exists() else {}
    call_assessments = assessments.get("calls", {})
    if not isinstance(call_assessments, dict) or any(key not in call_map or not isinstance(value, dict) for key, value in call_assessments.items()):
        raise ValueError("Assessments must reference known calls")
    for call_id, assessment in call_assessments.items():
        recommended = assessment.get("recommended_clips", [])
        if not isinstance(recommended, list) or any(clip_id not in clip_map or clip_map[clip_id]["call_id"] != call_id for clip_id in recommended):
            raise ValueError("Recommended clips must belong to their assessed call")
    if output in all_inputs:
        raise ValueError("Output cannot overwrite any screening input or media file")

    nav, sections = [], []
    models = ["apex", "swift"] + sorted(set(results) - {"apex", "swift"})
    for number, call in enumerate(calls, 1):
        call_id = call["id"]
        anchor = f"call-{number}"
        title = _text(_display_title(call.get("title") or call_id))
        selected = sorted((clip for clip in clips if clip["call_id"] == call_id), key=lambda clip: clip["start_seconds"])
        sampled = sum(clip["duration_seconds"] for clip in selected)
        nav.append(f'<a class="call-link" href="#{anchor}">{number:02d} &nbsp;{title}<small>{_clock(call["audio_seconds"])} recording · {len(selected)} excerpts</small></a>')
        assessment = call_assessments.get(call_id, {})
        label = assessment.get("label") or "Not assessed"
        recommended = assessment.get("recommended_clips", [])
        cards = []
        for clip_number, clip in enumerate(selected, 1):
            audio = audio_paths[clip["id"]]
            player = (f'<audio controls preload="none" aria-label="Listen to excerpt {clip_number} from {title}" src="{_link(audio, output)}">Your browser does not support local audio playback.</audio>'
                      if audio.is_file() else '<span class="pill failed">Audio file unavailable</span>')
            drafts = "".join(_draft(model, results.get(model, {}).get("clips", {}).get(clip["id"])) for model in models)
            clip_details = {key: clip.get(key) for key in ("id", "audio_sha256", "rms", "peak")}
            cards.append(
                f'<article class="clip"><div class="clip-top"><div><div class="clip-heading"><span class="number">{clip_number:02d}</span>'
                f'<h3>Excerpt {clip_number}</h3>'
                + ('<span class="pill">Suggested for review</span>' if clip["id"] in recommended else '')
                + f'</div><div class="time">{_clock(clip["start_seconds"])}–{_clock(clip["start_seconds"] + clip["duration_seconds"])} in recording · {clip["duration_seconds"]:g}s</div></div>{player}</div>'
                + f'<div class="drafts">{drafts}</div><details style="margin:0;padding:0 22px 14px"><summary>Excerpt provenance</summary><pre>{_text(json.dumps(clip_details, ensure_ascii=False, indent=2))}</pre></details></article>'
            )
        original = originals[call_id]
        original_link = f'<a href="{_link(original, output)}">Open copied recording</a>' if original.is_file() else '<span>Copied recording unavailable</span>'
        notes = _notes(assessment.get("notes"))
        provenance = {key: call.get(key) for key in ("id", "title", "source_path", "original", "source_sha256", "stream_info")}
        sections.append(
            f'<section class="call" id="{anchor}"><div class="call-header"><div><div class="eyebrow">Recording {number:02d}</div><h2>{title}</h2>'
            f'<div class="meta">{_clock(call["audio_seconds"])} full recording · {_clock(sampled)} sampled · {original_link}</div></div><span class="pill wait">{_text(label)}</span></div>'
            + (f'<div class="assessment"><strong>Provisional screening note</strong><p>{_text(notes)}</p></div>' if notes else '')
            + ("".join(cards) if cards else '<div class="empty-call">No excerpts have been prepared for this recording.</div>')
            + f'<details><summary>Recording provenance</summary><pre>{_text(json.dumps(provenance, ensure_ascii=False, indent=2))}</pre></details></section>'
        )
    total_sampled = sum(clip["duration_seconds"] for clip in clips)
    ready = sum(1 for data in results.values() for result in data["clips"].values() if result.get("status") == "ok")
    overall = _notes(assessments.get("overall_notes"))
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; media-src 'self' file:; base-uri 'none'; form-action 'none'">
<title>Hinglish lab · Private call screening</title><style>{CSS}</style></head>
<body><div class="shell"><aside><div class="brand">Hinglish<span class="dot">.</span> lab</div><div class="eyebrow">Private listening workspace</div>
<div class="eyebrow nav-label">Your recordings</div><nav aria-label="Recordings">{"".join(nav)}</nav>
<p class="side-note">This page and its audio stay in the private screening folder. It contains no remote assets, scripts, or analytics. Keep the folder together for playback.</p></aside>
<main><header><div class="eyebrow">Local model exploration</div><h1>Listen. Compare. Find the Hinglish.</h1>
<p class="intro">Review short excerpts with the exact drafts returned by Apex and Swift. Listen to the recording before deciding which passages are useful for a benchmark.</p>
<p class="created">Screening created {_text(document.get("created_at", "unknown"))}</p></header>
<div class="metrics"><div class="metric"><strong>{len(calls)}</strong><span>recordings</span></div><div class="metric"><strong>{len(clips)}</strong><span>excerpts · {_clock(total_sampled)} sampled</span></div><div class="metric"><strong>{ready}</strong><span>machine drafts ready</span></div></div>
<div class="notice"><strong>Machine drafts, not verified transcripts.</strong> No accuracy scores or model qualification are implied. Limited samples cannot establish that an entire call is English-only. Apex and Swift produce Roman text; their English decoder setting does not identify the spoken language. Timings describe batch processing, not live transcription delay.</div>
{f'<div class="assessment"><strong>Overall screening notes</strong><p>{_text(overall)}</p></div>' if overall else ''}
{"".join(sections)}
<details><summary>Model and runtime provenance</summary><pre>{_text(json.dumps({model: {key: data.get(key) for key in ('model_spec', 'runtime_config', 'screening_sha256')} for model, data in results.items()}, ensure_ascii=False, indent=2))}</pre></details>
<footer>Local review only · References remain pending until a person listens and corrects them.<br>Screening SHA-256: <code>{digest}</code></footer></main></div></body></html>
'''
    output.parent.mkdir(parents=True, exist_ok=True)
    # Report generation never rewrites screening manifests, drafts, or media.
    output.write_text(content, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps({"report": str(render(args.screening, args.output))}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot render screening: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
