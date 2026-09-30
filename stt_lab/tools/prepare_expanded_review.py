"""Create new private review excerpts without implying checked references or holdout.

Copies already-preserved source recordings, validates hashes, decodes locally,
then cuts exact PCM sample boundaries. Existing intake and source files are never
modified. No speech model or reference text participates in window selection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import wave

RATE = 16000
SELECTED = ("call-03", "call-05", "call-06")
FRACTIONS = (.08, .30, .65, .93)
CLIP_SECONDS = 28
CONTEXT_SECONDS = 5
EXCLUSION_PADDING = 20


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def inside(root, value):
    value = str(value)
    if "://" in value or PureWindowsPath(value).drive or Path(value).is_absolute():
        raise ValueError("Manifest media paths must be relative local paths")
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Media path leaves the private screening folder")
    return path


def select_windows(duration, previous, count=4):
    if not math.isfinite(duration) or duration < 100:
        raise ValueError("Expanded selection needs a recording longer than 100 seconds")
    forbidden = [(max(0, float(clip["start_seconds"]) - EXCLUSION_PADDING),
                  float(clip["start_seconds"]) + float(clip["duration_seconds"]) + EXCLUSION_PADDING)
                 for clip in previous]
    chosen = []
    # The four predeclared positions are tried first. A deterministic grid is
    # only a fallback when an old sample blocks one, never ASR-based cherry-picking.
    for fraction in (*FRACTIONS, *(value / 100 for value in range(5, 96, 5))):
        start = round((duration * fraction - CLIP_SECONDS / 2) * RATE) / RATE
        end = start + CLIP_SECONDS
        left, right = start - CONTEXT_SECONDS, end + CONTEXT_SECONDS
        if left < 0 or right > duration:
            continue
        if any(left < stop and right > begin for begin, stop in forbidden):
            continue
        if any(left < stop + CONTEXT_SECONDS and right > begin - CONTEXT_SECONDS for begin, stop in chosen):
            continue
        chosen.append((start, end))
        if len(chosen) == count:
            return sorted(chosen)
    raise ValueError("Not enough distinct windows outside prior sample exclusions")


def pcm_slice(source, destination, start, end):
    with wave.open(str(source), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Expected mono PCM16 at 16 kHz")
        begin, stop = round(start * RATE), round(end * RATE)
        if not 0 <= begin < stop <= audio.getnframes():
            raise ValueError("Requested clip is outside the decoded audio")
        audio.setpos(begin)
        raw = audio.readframes(stop - begin)
        if len(raw) != (stop - begin) * 2:
            raise ValueError("Decoded audio is truncated")
    with wave.open(str(destination), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(RATE)
        clip.writeframes(raw)
    return (stop - begin) / RATE


def prepare(screening, output, ffmpeg=None):
    screening = Path(screening).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new output folder; previous reviews are never overwritten")
    if any(part.lower().startswith("onedrive") for part in output.parts):
        raise ValueError("Private audio and review artifacts must stay outside OneDrive")
    source = json.loads(screening.read_text(encoding="utf-8"))
    if source.get("purpose") != "exploratory_language_screening_not_accuracy_evaluation":
        raise ValueError("Expected the original exploratory screening manifest")
    calls = {call["id"]: call for call in source["calls"]}
    selected = []
    for call_id in SELECTED:
        call = calls[call_id]
        original = inside(screening.parent, call["original"])
        if digest(original) != call["source_sha256"]:
            raise ValueError(f"Source copy hash changed for {call_id}")
        previous = [clip for clip in source["clips"] if clip["call_id"] == call_id]
        selected.append((call, original, select_windows(call["audio_seconds"], previous)))
    if ffmpeg is None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    executable = str(Path(ffmpeg).resolve(strict=True))
    version = subprocess.run([executable, "-version"], check=True, capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.splitlines()[0]
    output.mkdir(parents=True)
    document = {"version": 1, "purpose": "exploratory_language_screening_not_accuracy_evaluation",
        "review_phase": "expanded_development_review_batch_1", "created_at": datetime.now(timezone.utc).isoformat(),
        "decoder": version, "human_reviewed": False, "reference_status": "pending",
        "previous_screening": {"path": str(screening), "sha256": digest(screening)},
        "selection": {"method": "predeclared evenly distributed fractions; no model/reference selection",
            "fractions": list(FRACTIONS), "old_window_padding_seconds": EXCLUSION_PADDING,
            "scored_seconds": CLIP_SECONDS, "context_padding_seconds": CONTEXT_SECONDS,
            "language_labels": "Expected from earlier sparse screening; each new excerpt needs human confirmation",
            "holdout_status": "All six supplied calls have prior screening exposure; none is an untouched holdout"},
        "calls": [], "clips": []}
    for call, original, windows in selected:
        folder = output / call["id"]
        folder.mkdir()
        copied = folder / original.name
        shutil.copyfile(original, copied)
        if digest(copied) != call["source_sha256"]:
            raise ValueError("Private copy verification failed")
        analysis = folder / "analysis.wav"
        subprocess.run([executable, "-nostdin", "-v", "error", "-i", str(copied), "-map", "0:a:0",
                        "-vn", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(analysis)],
                       check=True, capture_output=True, text=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        new_call = {**call, "original": copied.relative_to(output).as_posix(),
                    "analysis": analysis.relative_to(output).as_posix(), "analysis_sha256": digest(analysis),
                    "prior_screening_exposure": True, "split": "development"}
        document["calls"].append(new_call)
        for index, (start, end) in enumerate(windows, 1):
            clip_id = f"{call['id']}-expanded-{index:02d}"
            audio, context = folder / f"review-{index:02d}.wav", folder / f"context-{index:02d}.wav"
            duration = pcm_slice(analysis, audio, start, end)
            context_duration = pcm_slice(analysis, context, start - CONTEXT_SECONDS, end + CONTEXT_SECONDS)
            document["clips"].append({"id": clip_id, "call_id": call["id"], "split": "development",
                "audio": audio.relative_to(output).as_posix(), "start_seconds": start,
                "end_seconds": end, "duration_seconds": duration, "audio_sha256": digest(audio),
                "context_audio": context.relative_to(output).as_posix(), "context_sha256": digest(context),
                "context_start_seconds": start - CONTEXT_SECONDS, "context_duration_seconds": context_duration,
                "scored_interval_within_context_seconds": [CONTEXT_SECONDS, CONTEXT_SECONDS + CLIP_SECONDS],
                "expected_language": "English" if call["id"] == "call-06" else "Hinglish",
                "language_human_confirmed": False, "human_reviewed": False, "reference_status": "pending"})
        print(f"Prepared {call['id']}: {len(windows)} new scored clips and context audio", flush=True)
    manifest = output / "screening.json"
    write_json(manifest, document)
    references = {"version": 1, "kind": "expanded_human_reference_review", "screening_sha256": digest(manifest),
        "human_reviewed": False, "samples": [{"sample_id": clip["id"], "audio_sha256": clip["audio_sha256"],
            "reference_roman": "", "names": [], "numbers": [], "notes": "", "language": "unconfirmed",
            "listened": False, "review_completed": False, "no_speech": False,
            "human_reviewed": False, "reference_status": "pending"} for clip in document["clips"]]}
    write_json(output / "user-references-pending.json", references)
    plan = {"version": 1, "current_batch": {"clips": len(document["clips"]), "scored_seconds": sum(c["duration_seconds"] for c in document["clips"]),
        "status": "references_pending", "call_groups": list(SELECTED)},
        "prior_exposure": [{"call_id": call["id"], "previous_screened_clip_ids": [c["id"] for c in source["clips"] if c["call_id"] == call["id"]]} for call in source["calls"]],
        "reserved_groups": {"call-02": "No new batch-1 inference; earlier sparse screening exposure remains",
                            "call-04": "No new batch-1 inference; earlier sparse screening exposure remains"},
        "coverage_target_minutes": [30, 60], "target_status": "not_achieved",
        "stages": ["Review batch 1 (5.6 minutes) and annotate language, names, quantities, noise, overlap and boundary uncertainty",
                   "Expand development review in small batches to 20-40 minutes, retaining silence and difficult speech",
                   "Freeze model/decoding/normalization before evaluating 10-20 minutes from reserved call groups; report their historical exposure",
                   "For a genuinely untouched final holdout, use fresh recordings or a predeclared recording not previously screened; check speaker independence"],
        "release_targets": {"normalized_word_error_max": .20, "names_numbers_accuracy_min": .95,
                            "live_updates_p95_seconds_max": 15, "no_growing_backlog": True},
        "unproven": ["reference accuracy until checked", "representative language coverage", "speaker independence",
                     "streaming latency", "concurrent recording/diarization/calling-app performance", "final release readiness"]}
    write_json(output / "coverage-plan.json", plan)
    (output / "REVIEW.md").write_text("""# Expanded transcript review — batch 1

Twelve new 28-second clips total 5 minutes 36 seconds. References are blank and
pending. Model drafts, when added, are unchecked suggestions, never ground truth.

Listen to the scored clip and write exactly the speech inside its displayed
boundaries. The separate 38-second context clip adds five seconds on each side;
do not include those context words in the reference. Note an uncertain boundary
instead of extending the reference. Annotate names and complete quantities.

All six supplied calls were previously sparsely screened. This batch uses calls
03, 05 and 06 for development; calls 02 and 04 are reserved from further inference
for now, but cannot be described as untouched held-out recordings. Language labels
are expectations from earlier screening and need confirmation for every excerpt.

The editable HTML saves only when you explicitly download review JSON. Import that
JSON to restore work. Nothing uploads, no browser storage is required, and closing
without exporting loses unsaved edits. A review is checked only after both listening
and correction checkboxes are explicitly selected (or silence is explicitly marked).

See coverage-plan.json for the remaining 30–60-minute evaluation plan. This batch
does not establish accuracy, live latency, speaker quality or release readiness.
""", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()
    print(prepare(args.screening, args.output, args.ffmpeg))


if __name__ == "__main__":
    main()
