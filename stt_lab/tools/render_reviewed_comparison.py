"""Render scored local excerpts as an offline HTML comparison; never overwrite.

Uses scores.json plus its exact screening.json and, when declared, the verified
review-reference document. All media/input/output paths stay inside the private
screening root. No inference, transliteration, network, JavaScript or CDN assets.
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
:root{color-scheme:light;--ink:#19342e;--muted:#64746d;--line:#dbe3dd;--paper:#fff;
--canvas:#f3f5f0;--accent:#245c48;--soft:#eaf2eb;--warm:#fff3d8}*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font:16px/1.6 'Segoe UI',Arial,sans-serif}
main{max-width:1120px;margin:auto;padding:42px 30px 60px}a{color:var(--accent);text-underline-offset:3px}
a:focus-visible,summary:focus-visible,audio:focus-visible{outline:3px solid #386fc5;outline-offset:4px}
h1{font-size:clamp(30px,4vw,46px);line-height:1.15;letter-spacing:-1.3px;margin:10px 0 14px}
h2{font-size:25px;line-height:1.3;margin:0 0 10px}h3{font-size:18px;margin:0}p{margin:8px 0}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.7px;font-weight:750;color:var(--accent)}
.muted,.meta{color:var(--muted)}.intro{max-width:830px}.meta{font-size:13px}.tag{display:inline-block;
font-size:11px;font-weight:650;padding:4px 9px;border-radius:30px;background:var(--soft);color:var(--accent)}
.tag.excluded{background:var(--warm);color:#7d5521}.notice{padding:17px 21px;background:var(--warm);
border:1px solid #e9d9b4;border-radius:13px;margin:24px 0;font-size:14px}.summary{display:flex;gap:30px;
flex-wrap:wrap;margin:24px 0}.summary strong{display:block;font-size:26px;line-height:1.3}
.summary span{font-size:12px;color:var(--muted)}.rankings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
gap:15px;margin:18px 0 28px}.rank-card{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:22px}
.rank-top,.draft-head,.excerpt-top{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap}
.rank-number{font-size:12px;color:var(--muted);margin-right:9px}.wer{font-size:38px;font-weight:750;letter-spacing:-1px;
line-height:1.2;margin-top:15px}.wer small{font-size:12px;font-weight:500;letter-spacing:0;color:var(--muted)}
.facts{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.facts strong{display:block}.jump{display:flex;flex-wrap:wrap;gap:9px;margin:14px 0 25px}.jump a{padding:7px 12px;
background:#fff;border:1px solid var(--line);border-radius:8px;text-decoration:none;font-size:13px}
.excerpt{margin:28px 0 38px;scroll-margin-top:20px;border:1px solid var(--line);background:var(--paper);border-radius:17px;overflow:hidden}
.excerpt-top{padding:23px 25px;background:#fafbf8;border-bottom:1px solid var(--line)}.excerpt-title{min-width:0;flex:1}
.excerpt-title p{margin:5px 0}.audio{width:330px;max-width:100%}audio{width:100%;height:40px}.audio a{font-size:12px}
.reference{margin:23px 25px;padding:19px 22px;background:var(--soft);border:1px solid #d5e6d8;border-radius:11px}
.reference .label{font-size:12px;font-weight:750;color:var(--accent)}.text{white-space:pre-wrap;overflow-wrap:anywhere;
font-size:16px;line-height:1.85;margin:12px 0 0}.scope{margin:0 25px 20px;font-size:13px;color:var(--muted)}
.scope .boundary{background:var(--warm);padding:12px 15px;border-radius:9px;color:#775420;margin:10px 0}
.drafts{padding:0 25px 25px}.draft{border-top:1px solid var(--line);padding:23px 0 7px}.draft-head{align-items:center}
.draft-name{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.sample-score{font-size:14px;font-weight:650}
.sample-score span{font-size:11px;font-weight:400;color:var(--muted)}.timing{font-size:12px;color:var(--muted);margin:12px 0 8px}
details{margin:13px 0;color:var(--muted);font-size:13px}summary{cursor:pointer;font-weight:600;padding:3px 0}
details .text{background:#f5f7f2;color:var(--ink);padding:15px;border-radius:9px;font-size:15px}pre{white-space:pre-wrap;
overflow-wrap:anywhere;font:12px/1.65 Consolas,monospace;background:#f3f5f0;padding:14px;border-radius:8px}
.empty{font-style:italic;color:var(--muted)}footer{padding-top:18px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
footer code{overflow-wrap:anywhere}.local-files{display:flex;gap:15px;flex-wrap:wrap}
@media(max-width:650px){main{padding:26px 16px 40px}.rankings{grid-template-columns:1fr;gap:11px}
.rank-card{padding:18px}.summary{gap:22px}.excerpt-top{padding:18px}.audio{width:100%}.reference{margin:18px;padding:16px}
.scope{margin-left:18px;margin-right:18px}.drafts{padding:0 18px 18px}.text{font-size:15px}.notice{padding:15px}
.wer{font-size:33px}h1{letter-spacing:-.7px}.draft-head{gap:7px}.sample-score{width:100%}}
@media print{body{background:#fff}main{max-width:none;padding:0}audio,.jump,.local-files{display:none}
.rank-card,.reference,.draft{break-inside:avoid}.excerpt{overflow:visible}.text{font-size:12px}.rankings{grid-template-columns:1fr 1fr}}
"""


def text(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def local_path(value) -> Path:
    raw = str(value)
    if not raw.strip() or "://" in raw or raw.startswith(("\\\\", "//")):
        raise ValueError("Only local filesystem paths are supported")
    return Path(value).expanduser().resolve()


def inside(root: Path, value, *, relative=False) -> Path:
    raw = str(value)
    if relative and (Path(raw).is_absolute() or PureWindowsPath(raw).drive):
        raise ValueError("Manifest paths must be relative to the private screening root")
    path = local_path(root / raw) if "://" not in raw and not raw.startswith(("\\\\", "//")) else local_path(raw)
    if path == root or not path.is_relative_to(root):
        raise ValueError("Comparison paths must remain inside the private screening root")
    return path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {value}")))
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return data


def number(value, label="number") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def duration(value) -> str:
    return "Unavailable" if value is None else f"{number(value, 'timing'):.2f}s"


def clock(value) -> str:
    seconds = number(value, "source offset")
    whole, hundredths = divmod(round(seconds * 100), 100)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    prefix = f"{hours}:{minutes:02d}" if hours else str(minutes)
    return f"{prefix}:{secs:02d}.{hundredths:02d}"


def recording_title(call: dict) -> str:
    raw = str(call.get("title") or call.get("id") or "Recording")
    match = re.search(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})[Tt _-]+(\d{2})[:._-](\d{2})(?!\d)", raw)
    if match:
        try:
            stamp = datetime(*(int(part) for part in match.groups()))
            return stamp.strftime("%d %b %Y · %H:%M")
        except ValueError:
            pass
    return raw


def href(path: Path, output: Path) -> str:
    return text(quote(os.path.relpath(path, output.parent).replace(os.sep, "/"), safe="/"))


def model_name(model: str, record: dict) -> str:
    return str(record.get("model_spec", {}).get("display_name") or model.title())


def entity_stat(headline: dict, kind: str) -> str:
    counts = headline.get("entities", {}).get(kind, {})
    return f"{text(counts['matched'])}/{text(counts['reference'])}" if "matched" in counts and "reference" in counts else "Unavailable"


def scope_details(reference_sample: dict) -> str:
    # Preserve arbitrary review annotations without guessing which field holds
    # the full user correction. The scorer's reference remains the scoring text.
    ordinary = {"sample_id", "excerpt", "reference_roman", "review_completed", "preference", "user_feedback"}
    extras = {key: value for key, value in reference_sample.items() if key not in ordinary}
    if not extras:
        return ""
    content = []
    labels = {
        "full_user_reference_roman": "Your full original correction · preserved",
        "raw_transcript": "Your original verbatim correction",
        "reference_scope_note": "Reference scope note",
        "audio_reference_alignment": "How the reference was aligned with the audio",
    }
    for key, value in extras.items():
        label = labels.get(key, str(key).replace("_", " ").capitalize())
        if key == "audio_reference_alignment" and value == "user_assumed_ending_outside_clip":
            value = "The ending was excluded from the scored reference at your direction, assuming it lies outside the excerpt. This boundary has not been independently verified."
        if isinstance(value, str):
            content.append(f'<p><strong>{text(label)}</strong></p><p class="text">{text(value)}</p>')
        else:
            content.append(f'<p><strong>{text(label)}</strong></p><pre>{text(json.dumps(value, ensure_ascii=False, indent=2))}</pre>')
    return '<details><summary>Scope notes and preserved correction</summary>' + "".join(content) + '</details>'


def render(screening_path: Path, scores_path: Path, output: Path | None = None) -> Path:
    screening_path = local_path(screening_path)
    root = screening_path.parent
    scores_path = inside(root, scores_path)
    output = inside(root, output or scores_path.with_name("comparison.html"))
    if output.exists():
        raise ValueError("Comparison already exists; choose a new filename to preserve it")
    if not output.parent.is_dir():
        raise ValueError("The output directory must already exist inside the private screening root")
    screening, scores = read_json(screening_path), read_json(scores_path)
    screen_hash = digest(screening_path)
    if scores.get("screening_sha256") != screen_hash or scores.get("kind") != "reviewed_screening_pilot":
        raise ValueError("Scores must belong to the exact reviewed screening manifest")
    model_data = scores.get("models")
    if not isinstance(model_data, dict) or not model_data:
        raise ValueError("Scores contain no models")
    names = list(scores.get("selected_models", model_data))
    if len(names) != len(set(names)) or set(names) != set(model_data):
        raise ValueError("Selected models do not match the scored model data")
    order = sorted(names, key=lambda name: (number(model_data[name]["headline"]["wer"], "word error rate"), name))
    clips = {clip["id"]: clip for clip in screening["clips"]}
    calls = {call["id"]: call for call in screening["calls"]}
    if len(clips) != len(screening["clips"]) or len(calls) != len(screening["calls"]):
        raise ValueError("Screening contains duplicate call or clip IDs")
    reference_path = None
    reference_samples = {}
    declared_reference = scores.get("inputs", {}).get("references")
    if declared_reference:
        reference_path = inside(root, declared_reference, relative=True)
        if digest(reference_path) != scores.get("references_sha256"):
            raise ValueError("Review-reference document differs from the scored input")
        reviewed = read_json(reference_path)
        reference_samples = {sample["sample_id"]: sample for sample in reviewed.get("samples", [])}
    rows_by_model = {}
    sample_order = [row["sample_id"] for row in model_data[names[0]]["samples"]]
    for name in names:
        rows = model_data[name]["samples"]
        indexed = {row["sample_id"]: row for row in rows}
        if len(indexed) != len(rows) or set(indexed) != set(sample_order):
            raise ValueError("Models must contain the same unique reviewed excerpts")
        rows_by_model[name] = indexed
    ranking_cards = []
    previous_score, rank = None, 0
    for position, name in enumerate(order, 1):
        record, headline = model_data[name], model_data[name]["headline"]
        wer = number(headline["wer"])
        if wer != previous_score:
            rank = position
        previous_score = wer
        derived = record.get("scoring_view") == "derived_roman"
        timing = record.get("batch_timing", {}).get("headline_excerpts", {})
        total = timing.get("totals", {}).get("total_seconds")
        complete = timing.get("total_timing_complete") is True
        timing_text = duration(total) + (" · partial timing" if total is not None and not complete else "")
        ranking_cards.append(
            f'<article class="rank-card"><div class="rank-top"><h3><span class="rank-number">#{rank}</span>{text(model_name(name, record))}</h3>'
            f'<span class="tag">{"ASR + Roman rendering" if derived else "Native Roman output"}</span></div>'
            f'<div class="wer">{wer:.1%} <small>word error rate</small></div>'
            f'<p class="meta">{text(headline.get("errors"))} word errors / {text(headline.get("reference_words"))} reference words</p>'
            f'<div class="facts"><div><strong>{entity_stat(headline, "name")}</strong>Annotated names</div>'
            f'<div><strong>{entity_stat(headline, "number")}</strong>Complete quantities</div>'
            f'<div><strong>{text(timing_text)}</strong>Batch ASR total · scored excerpts</div></div></article>')
    sections, nav = [], []
    for index, sample_id in enumerate(sample_order, 1):
        row = rows_by_model[names[0]][sample_id]
        clip = clips.get(sample_id)
        if clip is None or clip.get("call_id") not in calls:
            raise ValueError("A scored excerpt is missing its screening clip or source call")
        call = calls[clip["call_id"]]
        audio = inside(root, clip["audio"], relative=True)
        if digest(audio) != clip.get("audio_sha256"):
            raise ValueError("Excerpt audio changed after screening")
        for name in names:
            other = rows_by_model[name][sample_id]
            if any(other.get(key) != row.get(key) for key in ("reference", "included_in_headline", "excerpt")):
                raise ValueError("Models have inconsistent reference or scoring scope")
            if other.get("audio_sha256") != clip.get("audio_sha256"):
                raise ValueError("Scored audio identity differs from screening")
        start = number(clip["start_seconds"])
        length = number(clip["duration_seconds"])
        label = f'Excerpt {row.get("excerpt", index)}'
        anchor = f"excerpt-{index}"
        nav.append(f'<a href="#{anchor}">{text(label)}</a>')
        included = row.get("included_in_headline") is True
        audio_url = href(audio, output)
        source_link = ""
        if call.get("original"):
            original = inside(root, call["original"], relative=True)
            if not original.is_file():
                raise ValueError("Preserved original recording is missing")
            source_link = f' · <a href="{href(original, output)}#t={start:.6f},{start + length:.6f}">Open source at excerpt</a>'
        reference = row["reference"]
        if not isinstance(reference, str):
            raise ValueError("Reference text must be a string")
        boundary = row.get("exclusion_reason") or scores.get("excluded_samples", {}).get(sample_id)
        feedback = row.get("user_feedback")
        scope = (f'<div class="boundary"><strong>Excluded from headline.</strong> {text(boundary or "Audio/reference scope needs review.")}</div>' if not included else "")
        scope_keys = ("full_user_reference_roman", "raw_transcript", "reference_scope_note", "audio_reference_alignment")
        reference_sample = {**{key: row[key] for key in scope_keys if key in row}, **reference_samples.get(sample_id, {})}
        if reference_sample.get("reference_scope_note"):
            scope += f'<div class="boundary"><strong>Scoring scope.</strong> {text(reference_sample["reference_scope_note"])}</div>'
        if reference_sample.get("audio_reference_alignment") == "user_assumed_ending_outside_clip":
            scope += '<p>The excerpt ending is excluded by your stated boundary assumption; the full correction remains preserved below.</p>'
        scope += scope_details(reference_sample)
        if feedback:
            scope += f'<details><summary>Your review feedback</summary><p class="text">{text(feedback)}</p></details>'
        drafts = []
        for name in order:
            prediction = rows_by_model[name][sample_id]
            hypothesis, raw = prediction.get("hypothesis"), prediction.get("raw_hypothesis")
            if not isinstance(hypothesis, str) or (raw is not None and not isinstance(raw, str)):
                raise ValueError("Model drafts must be strings")
            wer = prediction.get("strict_word_errors", {}).get("wer")
            metric = f'{number(wer):.1%} <span>word error rate</span>' if included and wer is not None else '<span>Excluded from headline</span>' if not included else '<span>WER unavailable</span>'
            timing = prediction.get("timing", {})
            derived = prediction.get("scoring_view") == "derived_roman"
            native = (f'<details><summary>Original model output · mixed script</summary><p class="text" dir="auto">{text(raw)}</p></details>'
                      if raw is not None and raw != hypothesis else "")
            renderer = prediction.get("roman_derivation")
            provenance = f'<details><summary>Roman rendering provenance</summary><pre>{text(json.dumps(renderer, ensure_ascii=False, indent=2))}</pre></details>' if renderer else ""
            drafts.append(f'<section class="draft"><div class="draft-head"><div class="draft-name"><h3>{text(model_name(name, model_data[name]))}</h3>'
                          f'<span class="tag">{"Rendered Roman draft" if derived else "Native Roman draft"}</span></div><div class="sample-score">{metric}</div></div>'
                          f'<p class="text">{text(hypothesis) if hypothesis else "<span class=empty>No text returned.</span>"}</p>'
                          f'<p class="timing">Batch inference: {duration(timing.get("inference_seconds"))} · Model loading: {duration(timing.get("load_seconds"))} · Total: {duration(timing.get("total_seconds"))}</p>'
                          f'{native}{provenance}</section>')
        sections.append(f'<article class="excerpt" id="{anchor}"><header class="excerpt-top"><div class="excerpt-title"><h2>{text(label)}</h2>'
                        f'<p>{text(recording_title(call))}</p><p class="meta">Source {clock(start)}–{clock(start + length)} · {length:.2f}s · {text(sample_id)}</p>'
                        f'<span class="tag{" excluded" if not included else ""}">{"Included in headline" if included else "Preserved · excluded from headline"}</span></div>'
                        f'<div class="audio"><audio controls preload="none" aria-label="Audio for {text(label)}" src="{audio_url}"></audio>'
                        f'<a href="{audio_url}">Open excerpt audio</a>{source_link}</div></header>'
                        f'<div class="reference"><div class="label">Your reviewed reference · text used for scoring</div><p class="text">{text(reference)}</p></div>'
                        f'<div class="scope">{scope}</div><div class="drafts">{"".join(drafts)}</div></article>')
    provenance = {"score_date": scores.get("date"), "normalization": scores.get("normalization"),
                  "screening_sha256": screen_hash, "scores_sha256": digest(scores_path),
                  "references_sha256": scores.get("references_sha256"), "policy_sha256": scores.get("policy_sha256"),
                  "models": {name: {"model_spec": model_data[name].get("model_spec"), "runtime_config": model_data[name].get("runtime_config")} for name in names}}
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; media-src \'self\' file:; script-src \'none\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        '<title>Reviewed transcript comparison</title><style>' + CSS + '</style></head><body><main>'
        '<header><div class="eyebrow">Private transcription lab · reviewed excerpts</div><h1>Hinglish transcript comparison</h1>'
        '<p class="intro muted">Your checked transcript is the reference. Review each model’s Roman draft beside the exact local audio, with original mixed-script output available below it.</p>'
        f'<p class="meta">Reviewed {text(scores.get("date", ""))} · Recording dates shown as supplied, without timezone conversion.</p></header>'
        f'<div class="summary"><div><strong>{text(scores.get("comparable_excerpts"))}</strong><span>Scored excerpts</span></div>'
        f'<div><strong>{number(scores.get("comparable_audio_seconds", 0)):.1f}s</strong><span>Scored audio</span></div>'
        f'<div><strong>{len(names)}</strong><span>Models compared</span></div></div>'
        '<section aria-labelledby="ranking"><h2 id="ranking">Lower word error rate ranks higher</h2>'
        '<p class="meta">Word error rate counts substitutions, missing words and added words against the reviewed reference. It is not an accuracy percentage; values can exceed 100%.</p>'
        '<div class="rankings">' + ''.join(ranking_cards) + '</div></section>'
        '<div class="notice"><strong>What this comparison measures</strong><p>All scores use Roman reading text. Native Roman models are compared with mixed-script models after their separately recorded Roman rendering. Those derived scores measure transcription plus rendering; spelling choices in that step can change WER.</p>'
        '<p>Strict Roman scoring can penalize spelling, word spacing and words-versus-digits even when a model preserves a name, numeric value or meaning. Inspect the original model output and annotated-name and quantity counts alongside the WER ranking.</p>'
        '<p>Batch timings come from different runs with different startup and cache conditions. Some clips used an already-loaded model; first clips in other runs include startup. These timings are not a controlled speed comparison. They report ASR runtime, excluding separate Roman rendering, and do not measure live latency.</p>'
        '<p>This is a small reviewed development sample. Annotated names and quantities are phrase-presence checks, not a complete semantic assessment. No release qualification is implied.</p></div>'
        '<nav class="jump" aria-label="Reviewed excerpts">' + ''.join(nav) + '</nav>' + ''.join(sections)
        + '<footer><p>Local files only. This page contains no remote assets, scripts, analytics or network requests. Audio stays in the private screening folder.</p>'
        + f'<div class="local-files"><a href="{href(scores_path, output)}">Scored results JSON</a><a href="{href(screening_path, output)}">Screening manifest</a>'
        + (f'<a href="{href(reference_path, output)}">Preserved review references</a>' if reference_path else '') + '</div>'
        + '<details><summary>Input hashes and model settings</summary><pre>' + text(json.dumps(provenance, ensure_ascii=False, indent=2)) + '</pre></details></footer></main></body></html>')
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(document + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Default: comparison.html beside scores.json; never overwritten")
    args = parser.parse_args()
    try:
        print(render(args.screening, args.scores, args.output))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Comparison rendering failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
