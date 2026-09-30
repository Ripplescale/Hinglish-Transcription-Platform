"""Score human-reviewed screening excerpts without overwriting drafts or references."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sttbench.manifest import file_digest, read_json, write_json
from sttbench.normalization import Normalizer, entity_counts, word_errors

MODEL_REPOSITORIES = {
    "apex": "Oriserve/Whisper-Hindi2Hinglish-Apex",
    "swift": "Oriserve/Whisper-Hindi2Hinglish-Swift",
    "srota": "moorlee/qwen3-asr-0.6b-hinglish",
    "trelis": "Trelis/whisper-hinglish-preview",
}
MIXED_SCRIPT_MODELS = {"srota", "trelis"}
TIMING_KEYS = ("verification_seconds", "load_seconds", "inference_seconds", "process_seconds", "total_seconds")


def contained(root: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Review inputs and outputs must remain inside the private screening folder")
    return path


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def roman_view(document: dict, model: str, spec: dict, results_hash: str,
               sample_id: str, raw_text: str) -> tuple[str, dict]:
    """Require an explicitly derived, reproducible view for mixed-script ASR.

    Artifact v1: screening_sha256; models[model] contains model_id, repo_id,
    source_revision, source_results_sha256, renderer{name, source_revision,
    configuration}, and clips[id]{source_text_sha256, roman_text}.
    """
    derived = document.get("models", {}).get(model)
    if not isinstance(derived, dict):
        raise ValueError(f"{model}: an explicit derived Roman view is required")
    if any(derived.get(key) != spec[key] for key in ("model_id", "repo_id", "source_revision")):
        raise ValueError(f"{model}: Roman-view model provenance differs from the ASR run")
    if derived.get("source_results_sha256") != results_hash:
        raise ValueError(f"{model}: Roman views belong to a different source result file")
    renderer = derived.get("renderer")
    if (not isinstance(renderer, dict)
            or any(not isinstance(renderer.get(key), str) or not renderer[key].strip()
                   for key in ("name", "source_revision"))
            or not isinstance(renderer.get("configuration"), dict)):
        raise ValueError(f"{model}: Roman renderer identity, revision and configuration are required")
    view = derived.get("clips", {}).get(sample_id)
    if not isinstance(view, dict) or not isinstance(view.get("roman_text"), str):
        raise ValueError(f"{model}/{sample_id}: missing explicit Roman output")
    if view.get("status", "ok") != "ok" or view.get("source_text_sha256") != text_digest(raw_text):
        raise ValueError(f"{model}/{sample_id}: Roman source-text hash mismatch or failed rendering")
    roman = view["roman_text"]
    if any("\u0900" <= char <= "\u097f" and unicodedata.category(char)[0] in ("L", "M") for char in roman):
        raise ValueError(f"{model}/{sample_id}: derived Roman output still contains Devanagari text")
    return roman, {"renderer": renderer, "source_results_sha256": results_hash,
                   "source_text_sha256": view["source_text_sha256"], "roman_text_sha256": text_digest(roman)}


def batch_timing(rows: list[dict]) -> dict:
    coverage = {key: sum(row["timing"].get(key) is not None for row in rows) for key in TIMING_KEYS}
    totals = {key: sum(row["timing"][key] for row in rows if row["timing"].get(key) is not None)
              if coverage[key] else None for key in TIMING_KEYS}
    complete = coverage["total_seconds"] == len(rows)
    audio_seconds = sum(row["duration_seconds"] for row in rows)
    return {"samples": len(rows), "audio_seconds": audio_seconds, "coverage": coverage,
            "totals": totals, "total_timing_complete": complete,
            "processing_audio_ratio": totals["total_seconds"] / audio_seconds if complete and audio_seconds else None,
            "live_latency_verified": False}


def _cell(value) -> str:
    return escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _literal(text: str) -> str:
    fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


def _timing_cell(timing: dict, key: str) -> str:
    value = timing["totals"][key]
    if value is None:
        return "Unavailable"
    partial = f" ({timing['coverage'][key]}/{timing['samples']} clips)" if timing["coverage"][key] != timing["samples"] else ""
    return f"{value:.2f}s{partial}"


def evaluate(root: Path, references: Path, policy_path: Path, output: Path,
             models: list[str] | tuple[str, ...] | None = None, roman_views: Path | None = None) -> dict:
    models = ["apex", "swift"] if models is None else list(models)
    if not models or len(set(models)) != len(models) or any(model not in MODEL_REPOSITORIES for model in models):
        raise ValueError("Select unique registered models: apex, swift, srota, trelis")
    root = root.resolve(strict=True)
    references, policy_path, output = [contained(root, path) for path in (references, policy_path, output)]
    if output.exists():
        raise ValueError("Use a new output directory to retain previous scoring revisions")
    screening_path = contained(root, root / "screening.json")
    screening = read_json(screening_path)
    reviewed, policy = read_json(references), read_json(policy_path)
    screen_hash = file_digest(screening_path)
    roman_document = {}
    if roman_views is not None:
        roman_views = contained(root, roman_views)
        roman_document = read_json(roman_views)
        if roman_document.get("version") != 1 or roman_document.get("screening_sha256") != screen_hash:
            raise ValueError("Roman views belong to different screening inputs or an unsupported format")
    if set(models) & MIXED_SCRIPT_MODELS and not roman_document:
        raise ValueError("Mixed-script models require --roman-views; raw mixed text is not scored against Roman references")
    if reviewed.get("screening_sha256") != screen_hash:
        raise ValueError("References belong to different screening audio")
    if policy.get("references_sha256") != file_digest(references):
        raise ValueError("Evaluation policy belongs to different references")
    clips = {clip["id"]: clip for clip in screening["clips"]}
    exclusions = policy.get("excluded_samples", {})
    entities = policy.get("entity_annotations", {})
    normalizer = Normalizer()
    samples = reviewed["samples"]
    sample_ids = [sample["sample_id"] for sample in samples]
    if len(sample_ids) != len(set(sample_ids)) or any(sample_id not in clips for sample_id in sample_ids):
        raise ValueError("Unknown or duplicated reference sample")
    if not set(exclusions).issubset(sample_ids) or not set(entities).issubset(sample_ids):
        raise ValueError("Policy refers to unknown reviewed samples")
    for sample in samples:
        if sample.get("review_completed") is not True:
            raise ValueError("Only explicitly human-reviewed references can be scored")
        for field in ("full_user_reference_roman", "reference_scope_note", "audio_reference_alignment"):
            if field in sample and not isinstance(sample[field], str):
                raise ValueError(f"Reference metadata {field} must be a string")
        clip = clips[sample["sample_id"]]
        audio = contained(root, root / clip["audio"])
        if file_digest(audio) != clip["audio_sha256"]:
            raise ValueError("Sample audio changed after screening")
        reference_tokens = normalizer.tokens(sample["reference_roman"])
        seen_forms = set()
        for entity in entities.get(sample["sample_id"], []):
            if entity.get("kind") not in ("name", "number"):
                raise ValueError("Entity kind must be name or number")
            forms = [entity["text"], *entity.get("aliases", [])]
            token_forms = {tuple(normalizer.tokens(form)) for form in forms}
            if () in token_forms:
                raise ValueError("Entity forms cannot be empty")
            if entity["kind"] == "number" and len(token_forms) > 1:
                raise ValueError("Quantity aliases cannot shorten or change the complete phrase")
            keys = {(entity["kind"], form) for form in token_forms}
            if keys & seen_forms:
                raise ValueError("Duplicate entity annotation forms")
            seen_forms.update(keys)
            if not any(any(tuple(reference_tokens[i:i+len(form)]) == form
                    for i in range(len(reference_tokens)-len(form)+1)) for form in token_forms):
                raise ValueError("Entity annotation does not appear in the reviewed reference")
    report = {
        "version": 3, "kind": "reviewed_screening_pilot", "date": reviewed["received_date"],
        "scorer_sha256": file_digest(Path(__file__)),
        "inputs": {"references": references.relative_to(root).as_posix(), "policy": policy_path.relative_to(root).as_posix()},
        "screening_sha256": screen_hash, "references_sha256": file_digest(references),
        "policy_sha256": file_digest(policy_path), "normalization": "unicode_nfc_casefold_preserve_marks_numbers_v1",
        "entity_method": "Contiguous phrase presence, once per annotation per excerpt; not a semantic or per-occurrence score. Quantity matches require the entire annotated phrase. Name spelling aliases are explicitly recorded in entity_details.",
        "word_equivalences": {}, "models": {}, "excluded_samples": exclusions,
        "selected_models": models,
        "human_reviewed_excerpts": len(samples), "comparable_excerpts": len(samples)-len(exclusions),
        "comparable_audio_seconds": sum(clips[s["sample_id"]]["duration_seconds"] for s in samples if s["sample_id"] not in exclusions),
        "readability_feedback": {s["sample_id"]: s["user_feedback"] for s in samples},
        "release_qualified": False,
        "interpretation": "Small reviewed development pilot; no held-out or live-performance qualification. " + (
            "Boundary-uncertain references remain preserved but excluded from headline scores." if exclusions
            else "All reviewed excerpts are included using their stated reference boundaries; any boundary assumptions remain explicit."),
    }
    if roman_views is not None:
        report["inputs"]["roman_views"] = roman_views.relative_to(root).as_posix()
        report["roman_views_sha256"] = file_digest(roman_views)
    for model in models:
        results_path = contained(root, root / "results" / f"{model}.json")
        results = read_json(results_path)
        results_hash = file_digest(results_path)
        model_spec = results.get("model_spec", {})
        if results.get("model_id") != model or any(model_spec.get(key) != model for key in ("id", "model_id")) or model_spec.get("repo_id") != MODEL_REPOSITORIES[model]:
            raise ValueError("Model identity does not match the expected result file")
        if not re.fullmatch(r"[0-9a-f]{40}", str(model_spec.get("source_revision", ""))):
            raise ValueError("Model source revision must be a pinned commit")
        if results.get("screening_sha256") != screen_hash:
            raise ValueError("Model predictions belong to different screening inputs")
        rows = []
        for sample in samples:
            sample_id = sample["sample_id"]
            result = results["clips"].get(sample_id)
            if not result or result.get("status") != "ok":
                raise ValueError("A required prediction is missing or failed")
            if any(result.get("provenance", {}).get(key) != model_spec[key] for key in ("model_id", "repo_id", "source_revision")):
                raise ValueError("Prediction model identity or revision differs from run metadata")
            reference, raw_text = sample["reference_roman"], result["text"]
            if not isinstance(reference, str) or not isinstance(raw_text, str):
                raise ValueError("References and ASR outputs must be strings")
            hypothesis, derivation = (roman_view(roman_document, model, model_spec, results_hash, sample_id, raw_text)
                                      if model in MIXED_SCRIPT_MODELS else (raw_text, None))
            timing = {key: result.get("timing", {}).get(key) for key in TIMING_KEYS}
            if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                         or not math.isfinite(value) or value < 0) for value in timing.values()):
                raise ValueError("Batch timing values must be finite nonnegative seconds")
            counts = word_errors(normalizer.tokens(reference), normalizer.tokens(hypothesis))
            annotation = entities.get(sample_id, [])
            row = {
                "excerpt": sample["excerpt"], "sample_id": sample_id,
                "included_in_headline": sample_id not in exclusions,
                "exclusion_reason": exclusions.get(sample_id),
                "reference_status": "human_reviewed", "reference": reference, "hypothesis": hypothesis,
                "raw_hypothesis": raw_text, "scoring_view": "derived_roman" if derivation else "native_roman",
                "roman_derivation": derivation, "timing": timing,
                "duration_seconds": clips[sample_id]["duration_seconds"],
                "strict_word_errors": counts,
                "entity_counts": entity_counts(annotation, hypothesis, normalizer),
                "entity_details": [{**entity, "matched": entity_counts([entity], hypothesis, normalizer)[entity["kind"]]["matched"] == 1} for entity in annotation],
                "user_preference": sample["preference"], "user_feedback": sample["user_feedback"],
                "audio_sha256": clips[sample_id]["audio_sha256"],
            }
            row.update({field: sample[field] for field in (
                "full_user_reference_roman", "reference_scope_note", "audio_reference_alignment") if field in sample})
            rows.append(row)
        included = [row for row in rows if row["included_in_headline"]]
        if not included:
            raise ValueError("No comparable reviewed references remain")
        totals = {key: sum(row["strict_word_errors"][key] for row in included)
                  for key in ("reference_words", "hypothesis_words", "errors", "substitutions", "deletions", "insertions")}
        if not totals["reference_words"]:
            raise ValueError("Comparable references must contain words for a WER headline")
        totals["wer"] = totals["errors"] / totals["reference_words"]
        totals["entities"] = {kind: {key: sum(row["entity_counts"][kind][key] for row in included)
                                         for key in ("matched", "reference")} for kind in ("name", "number")}
        report["models"][model] = {"source_results_sha256": results_hash,
            "model_spec": results["model_spec"], "runtime_config": results["runtime_config"],
            "scoring_view": "derived_roman" if model in MIXED_SCRIPT_MODELS else "native_roman",
            "batch_timing": {"all_reviewed_excerpts": batch_timing(rows), "headline_excerpts": batch_timing(included)},
            "headline": totals, "samples": rows}
    scope_summary = ("Excluded excerpts remain human-reviewed; their audio/reference alignment is uncertain. Reasons appear below each reference."
                     if exclusions else "All reviewed excerpts are included. Any user-specified reference boundaries are noted below.")
    lines = ["# Reviewed transcript comparison", "", "Your corrections are the reference text. The original model drafts and recordings remain unchanged.", "",
        f"**Headline comparison:** {report['comparable_excerpts']} excerpts, {report['comparable_audio_seconds']:.1f} seconds. {scope_summary}", "",
        "Word error rate measures word substitutions, missing words and added words; lower is better. Sentence breaks and case do not determine this score. No spelling equivalences are applied to WER.", "",
        "All scores use Roman reading text. Apex and Swift provide native Roman output. Srota and Trelis require a separately recorded Roman rendering; their scores measure the ASR-plus-rendering pipeline, not raw mixed-script recognition alone. Spelling, spacing and words-versus-digits differences can raise strict word error even when meaning is preserved.", "",
        "| Excerpt | " + " | ".join(f"{model.title()} word error rate" for model in models) + " | Your original preference |",
        "|---|" + "---:|" * len(models) + "---|"]
    for index, sample in enumerate(samples):
        values = []
        for model in models:
            row = report["models"][model]["samples"][index]
            score = row["strict_word_errors"]["wer"]
            values.append("Excluded from headline" if not row["included_in_headline"] else (f"{score:.1%}" if score is not None else "N/A: empty reference"))
        lines.append("| " + " | ".join([_cell(sample["excerpt"]), *values, _cell(sample["preference"]).capitalize()]) + " |")
    lines += ["| **Combined comparable excerpts** | " + " | ".join(f"**{report['models'][model]['headline']['wer']:.1%}**" for model in models) + " | |", ""]
    for model in models:
        headline = report["models"][model]["headline"]
        lines.append(f"{model.title()}: {headline['errors']} word errors across {headline['reference_words']} reference words.")
    lines += ["", "## Important names and quantities", "", "| Model | Annotated names preserved | Complete quantities preserved |", "|---|---:|---:|"]
    for model in models:
        counts = report["models"][model]["headline"]["entities"]
        lines.append(f"| {model.title()} | {counts['name']['matched']}/{counts['name']['reference']} | {counts['number']['matched']}/{counts['number']['reference']} |")
    minimum_wer = min(report["models"][model]["headline"]["wer"] for model in models)
    leaders = [model.title() for model in models if report["models"][model]["headline"]["wer"] == minimum_wer]
    if len(models) == 1:
        verdict = f"Only {models[0].title()} was evaluated; no comparative winner is implied."
    elif len(leaders) == len(models):
        verdict = "The models tie in this small pilot."
    elif len(leaders) > 1:
        verdict = f"{', '.join(leaders)} tie for the lowest word error rate in this small pilot."
    else:
        verdict = f"{leaders[0]} leads this small pilot."
    meeting_target = [model.title() for model in models if report["models"][model]["headline"]["wer"] <= 0.20]
    target_text = f"{', '.join(meeting_target)} {'meets' if len(meeting_target) == 1 else 'meet'} the 20% word-error target in this sample." if meeting_target else "None of the selected models meets the 20% word-error target in this sample."
    lines += ["", report["entity_method"], "", "Entity forms used in this comparison:", ""]
    for sample in samples:
        if sample["sample_id"] in exclusions:
            continue
        for entity in entities.get(sample["sample_id"], []):
            aliases = f"; accepted spellings: {', '.join(entity['aliases'])}" if entity.get("aliases") else ""
            lines.append(f"- Excerpt {sample['excerpt']}, {entity['kind']}: {entity['text']}{aliases}")
    timing_scope = "all reviewed excerpts, including clips excluded from accuracy scores" if exclusions else "all reviewed excerpts"
    lines += ["", "## Measured ASR batch timings", "",
        f"These are saved measurements from the original ASR runs, including model loading and other work in the recorded total. Startup and cache conditions differ across runs, so these are not a controlled speed comparison or live-update latency. This table covers {timing_scope}; missing measurements are shown as unavailable or partial. The JSON also provides timings for headline excerpts separately. Roman rendering time is not included in these ASR measurements.", "",
        "| Model | Measured total | Model loading | Inference | Batch processing / audio |", "|---|---:|---:|---:|---:|"]
    for model in models:
        timing = report["models"][model]["batch_timing"]["all_reviewed_excerpts"]
        ratio = timing["processing_audio_ratio"]
        lines.append(f"| {model.title()} | {_timing_cell(timing, 'total_seconds')} | {_timing_cell(timing, 'load_seconds')} | {_timing_cell(timing, 'inference_seconds')} | {f'{ratio:.2f}x' if ratio is not None else 'Unavailable'} |")
    if set(models) & MIXED_SCRIPT_MODELS:
        lines += ["", "## Roman rendering provenance", "",
            f"Derived-view artifact SHA-256: `{report['roman_views_sha256']}`. Every scored view is bound to the source result-file hash, exact raw-text hash, model identity, revision and renderer configuration.", ""]
        for model in models:
            if model in MIXED_SCRIPT_MODELS:
                renderer = report["models"][model]["samples"][0]["roman_derivation"]["renderer"]
                lines.append(f"- {model.title()}: {_cell(renderer['name'])}, revision {_cell(renderer['source_revision'])}.")
    lines += ["", "## How to use the result", "", verdict + " " + target_text + " This is not a representative release benchmark. No live latency or speaker result is implied, and no model should be removed on this pilot alone.", "",
        "Your original readability feedback and preferences are retained separately; adding models does not extend those preferences to models you have not reviewed. Word error rate does not measure sentence boundaries.", ""]
    for sample in samples:
        clip = clips[sample["sample_id"]]
        audio_link = quote(Path(os.path.relpath(root / clip["audio"], output)).as_posix(), safe="/")
        lines += [f"## Excerpt {sample['excerpt']}", "", f"Audio: [Play the sampled clip]({audio_link})", "",
            "**Your checked reference for this excerpt**", "", _literal(sample["reference_roman"]), ""]
        if sample.get("reference_scope_note"):
            lines += ["**Reference scope / boundary assumption**", "", _literal(sample["reference_scope_note"]), ""]
        if sample.get("full_user_reference_roman", sample["reference_roman"]) != sample["reference_roman"]:
            lines += ["**Full user correction (preserved separately from the scored excerpt text)**", "",
                      _literal(sample["full_user_reference_roman"]), ""]
        lines += ["**Your feedback**", "", _literal(sample["user_feedback"]), ""]
        if sample["sample_id"] in exclusions:
            lines += ["**Boundary note:** " + exclusions[sample["sample_id"]], ""]
        for model in models:
            row = next(row for row in report["models"][model]["samples"] if row["sample_id"] == sample["sample_id"])
            lines += [f"**{model.title()} original draft**", "", _literal(row["raw_hypothesis"]), ""]
            if row["scoring_view"] == "derived_roman":
                lines += [f"**{model.title()} derived Roman reading view (scored)**", "", _literal(row["hypothesis"]), ""]
    text = "\n".join(lines)
    output.mkdir(parents=True)
    write_json(output / "scores.json", report)
    (output / "comparison.md").write_text(text + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--models", nargs="+", choices=tuple(MODEL_REPOSITORIES), default=["apex", "swift"])
    parser.add_argument("--roman-views", type=Path, help="Explicit derived Roman views for mixed-script models; never modifies raw drafts")
    args = parser.parse_args()
    result = evaluate(args.root, args.references, args.policy, args.output, args.models, args.roman_views)
    print(json.dumps({"output": str(args.output), "headline": {key: value["headline"] for key, value in result["models"].items()}}, indent=2))
