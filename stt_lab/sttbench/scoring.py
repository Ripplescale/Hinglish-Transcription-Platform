"""Score exact reference views without silently transliterating hypotheses."""
from __future__ import annotations

import math

from .manifest import digest
from .normalization import Normalizer, entity_counts, word_errors


def corpus_digest(manifest: dict) -> str | None:
    if any("_audio_sha256" not in sample for sample in manifest["samples"]):
        return None
    return digest([{ "id": s["id"], "audio_sha256": s["_audio_sha256"] } for s in manifest["samples"]])


def _finite_nonnegative(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def corpus_coverage(manifest: dict) -> dict:
    """Count unique bytes once, including dev audio outside the scored split."""
    seen = set()
    seconds = {"dev": 0.0, "test": 0.0}
    calls = {"dev": set(), "test": set()}
    verified = True
    for sample in manifest["samples"]:
        audio_hash = sample.get("_audio_sha256")
        duration = sample.get("_duration_seconds")
        if not audio_hash or not _finite_nonnegative(duration):
            verified = False
        elif audio_hash not in seen:
            seen.add(audio_hash)
            seconds[sample["split"]] += duration
        if sample.get("call_id"):
            calls[sample["split"]].add(sample["call_id"])
    return {"verified_audio": verified, "unique_audio_seconds": sum(seconds.values()),
            "unique_audio_seconds_by_split": seconds,
            "call_ids_by_split": {key: sorted(value) for key, value in calls.items()},
            "all_call_ids_supplied": all(bool(sample.get("call_id")) for sample in manifest["samples"])}


def aggregate(rows: list[dict]) -> dict:
    result: dict = {"samples": len(rows), "views": {}, "entities": {}}
    for view in ("original", "roman"):
        scores = [r["views"][view] for r in rows if r["views"][view]["status"] == "scored"]
        totals = {key: sum(s[key] for s in scores) for key in (
            "reference_words", "hypothesis_words", "errors", "substitutions", "deletions", "insertions", "silence_hallucination_words")}
        totals.update(scored_samples=len(scores), missing_samples=len(rows)-len(scores),
                      wer=totals["errors"] / totals["reference_words"] if totals["reference_words"] else None)
        result["views"][view] = totals
    for kind in ("name", "number"):
        matched = sum(r["entities"][kind]["matched"] for r in rows)
        reference = sum(r["entities"][kind]["reference"] for r in rows)
        result["entities"][kind] = {"matched": matched, "reference": reference,
                                    "recall": matched/reference if reference else None}
    matched = sum(v["matched"] for v in result["entities"].values())
    reference = sum(v["reference"] for v in result["entities"].values())
    result["entity_recall"] = matched/reference if reference else None
    result["entities_by_view"] = {}
    for view in ("original", "roman"):
        counts = {}
        for kind in ("name", "number"):
            view_matched = sum(r["entities_by_view"][view][kind]["matched"] for r in rows)
            view_reference = sum(r["entities_by_view"][view][kind]["reference"] for r in rows)
            counts[kind] = {"matched": view_matched, "reference": view_reference,
                            "recall": view_matched/view_reference if view_reference else None}
        denominator = sum(item["reference"] for item in counts.values())
        result["entities_by_view"][view] = {"entities": counts,
            "recall": sum(item["matched"] for item in counts.values())/denominator if denominator else None,
            "missing_samples": sum(not r["entity_view_available"][view] for r in rows)}
    timed = [r for r in rows if r.get("processing_seconds") is not None and r.get("duration_seconds")]
    seconds = sum(r["processing_seconds"] for r in timed)
    duration = sum(r["duration_seconds"] for r in timed)
    result["batch_timing"] = {"timed_samples": len(timed), "audio_seconds": duration,
                               "processing_seconds": seconds, "processing_audio_ratio": seconds/duration if duration else None,
                               "live_latency_p95_seconds": None}
    return result


def score(manifest: dict, hypotheses: list[dict], equivalences: dict | None = None,
          split: str = "test", *, rebind_references: bool = False) -> dict:
    normalizer = Normalizer(equivalences)
    by_id = {}
    all_ids = {s["id"] for s in manifest["samples"]}
    by_sample = {s["id"]: s for s in manifest["samples"]}
    current_corpus = corpus_digest(manifest)
    rebound_ids = set()
    for hypothesis in hypotheses:
        sample_id = hypothesis.get("sample_id")
        if sample_id not in all_ids:
            raise ValueError(f"Hypothesis has unknown sample_id: {sample_id!r}")
        if sample_id in by_id:
            raise ValueError(f"Duplicate hypothesis for {sample_id}; select one attempt explicitly")
        if hypothesis.get("manifest_sha256") not in (None, manifest["manifest_sha256"]):
            sample = by_sample[sample_id]
            if not (rebind_references and hypothesis.get("input_manifest_sha256") == manifest["input_manifest_sha256"]
                    and current_corpus and hypothesis.get("corpus_sha256") == current_corpus
                    and sample.get("_audio_sha256") and hypothesis.get("audio_sha256") == sample["_audio_sha256"]):
                raise ValueError(f"{sample_id}: hypothesis belongs to a different manifest; reference rebinding requires unchanged input structure and exact audio bytes")
            rebound_ids.add(sample_id)
        for key in ("text", "roman_text"):
            if key in hypothesis and not isinstance(hypothesis[key], str):
                raise ValueError(f"{sample_id}: {key} must be a string")
        if hypothesis.get("text_view", "original") not in ("original", "roman"):
            raise ValueError(f"{sample_id}: text_view must be original or roman")
        for key in ("processing_seconds", "duration_seconds"):
            if key in hypothesis and not _finite_nonnegative(hypothesis[key]):
                raise ValueError(f"{sample_id}: invalid {key}")
        by_id[sample_id] = hypothesis
    selected = [s for s in manifest["samples"] if split == "all" or s["split"] == split]
    if not selected:
        raise ValueError(f"No samples in requested split: {split}")
    rows = []
    config_ids = set()
    verified = True
    for sample in selected:
        hypothesis = by_id.get(sample["id"])
        available = hypothesis is not None and hypothesis.get("status", "ok") == "ok" and "text" in hypothesis
        reference = sample.get("reference", {})
        views = {}
        for view, key in (("original", "text"), ("roman", "roman_text")):
            if view not in reference:
                views[view] = {"status": "missing_reference"}
            elif not available or key not in hypothesis or (view == "original" and hypothesis.get("text_view", "original") != "original"):
                views[view] = {"status": "missing_hypothesis"}
            else:
                views[view] = dict(status="scored", **word_errors(normalizer.tokens(reference[view]), normalizer.tokens(hypothesis[key])))
        if available:
            config_id = hypothesis.get("config_sha256")
            if config_id:
                config_ids.add(config_id)
            row_verified = bool(config_id and (hypothesis.get("manifest_sha256") == manifest["manifest_sha256"] or sample["id"] in rebound_ids)
                                and sample.get("_audio_sha256") and hypothesis.get("audio_sha256") == sample["_audio_sha256"])
        else:
            row_verified = False
        verified = verified and row_verified
        model_provenance = (hypothesis or {}).get("provenance", {})
        assets = model_provenance.get("asset_verification", {}) if isinstance(model_provenance, dict) else {}
        assets_verified = bool(isinstance(assets, dict) and assets.get("ok") is True
            and assets.get("integrity") == "manifest_files_verified" and assets.get("manifest_sha256")
            and assets.get("verified_hashes") and assets.get("source_revision_declaration_matches") is True
            and assets.get("source_revision") and assets.get("source_revision") == model_provenance.get("source_revision"))
        if isinstance(model_provenance, dict) and "conversion" in model_provenance:
            conversion = model_provenance["conversion"]
            assets_verified = assets_verified and isinstance(conversion, dict) and conversion.get("source_hashes_independently_verified") is True
        entity_available = {"original": bool(available and hypothesis.get("text_view", "original") == "original"),
                            "roman": bool(available and "roman_text" in hypothesis)}
        entity_views = {view: entity_counts(sample.get("entities", []), hypothesis[key] if entity_available[view] else "", normalizer)
                        for view, key in (("original", "text"), ("roman", "roman_text"))}
        entity_view = "roman" if "roman" in manifest["required_views"] else "original"
        rows.append({"sample_id": sample["id"], "split": sample["split"], "slices": sample.get("slices", {}),
                     "call_id": sample.get("call_id"), "speaker_ids": sample.get("speaker_ids", []),
                     "reference_status": sample.get("reference_status", "pending"), "views": views,
                     "entities": entity_views[entity_view], "entities_by_view": entity_views,
                     "entity_view_available": entity_available,
                     "duration_seconds": sample.get("_duration_seconds", (hypothesis or {}).get("duration_seconds")),
                     "processing_seconds": hypothesis.get("processing_seconds") if available else None,
                     "model_assets_verified": assets_verified,
                     "provenance_verified": row_verified})
    if len(config_ids) > 1:
        raise ValueError("Hypotheses mix multiple model/runtime configurations; score them separately")
    groups: dict[str, list[dict]] = {}
    for row in rows:
        for key, value in {"split": row["split"], **row["slices"]}.items():
            groups.setdefault(f"{key}={value}", []).append(row)
    return {"version": 1, "kind": "sttbench_scores", "manifest_sha256": manifest["manifest_sha256"],
            "input_manifest_sha256": manifest["input_manifest_sha256"],
            "corpus_sha256": current_corpus, "config_sha256": next(iter(config_ids), None),
            "corpus_coverage": corpus_coverage(manifest),
            "reference_rebinding": {"enabled": rebind_references, "samples": sorted(rebound_ids),
                "source_manifest_sha256": sorted({h["manifest_sha256"] for h in hypotheses if h["sample_id"] in rebound_ids})},
            "normalization": {"name": "unicode_nfc_casefold_preserve_marks_numbers_v1", "equivalences": equivalences or {},
                              "sha256": digest(equivalences or {})}, "required_views": manifest["required_views"],
            "split": split, "provenance_verified": verified, "warnings": manifest["warnings"],
            "model_assets_verified": all(row["model_assets_verified"] for row in rows),
            "total": aggregate(rows), "slices": {key: aggregate(value) for key, value in sorted(groups.items())},
            "samples": rows}
