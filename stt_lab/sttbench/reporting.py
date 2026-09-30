"""Fail-closed qualification using measured end-to-end live evidence."""
from __future__ import annotations

import math


def _number(value, minimum=0):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum


def _criterion(status: str, value=None, reason: str = "") -> dict:
    return {"status": status, "value": value, "reason": reason}


def release_report(scores: dict, evidence: dict | None = None) -> dict:
    if scores.get("version") != 1 or scores.get("kind") != "sttbench_scores":
        raise ValueError("Expected a version=1 sttbench score document")
    criteria = {}
    rows = scores["samples"]
    test_only = bool(rows) and all(row["split"] == "test" for row in rows)
    reviewed = test_only and all(row["reference_status"] == "reviewed" for row in rows)
    criteria["reviewed_test_references"] = _criterion("pass" if reviewed else "pending", reviewed,
        "Qualification requires only held-out test samples with human-reviewed references")
    provenance = bool(scores.get("provenance_verified") and scores.get("config_sha256") and scores.get("corpus_sha256"))
    criteria["reproducible_provenance"] = _criterion("pass" if provenance else "pending", provenance,
        "Every hypothesis must match the model configuration, manifest, and exact audio bytes")
    assets_verified = scores.get("model_assets_verified") is True
    criteria["verified_model_assets"] = _criterion("pass" if assets_verified else "pending", assets_verified,
        "Loaded weights need a matching source revision and verified artifact-manifest hashes; file presence or conversion declarations alone are insufficient")
    coverage = scores.get("corpus_coverage", {})
    seconds = coverage.get("unique_audio_seconds")
    split_seconds = coverage.get("unique_audio_seconds_by_split", {})
    calls = coverage.get("call_ids_by_split", {})
    dev_calls, test_calls = calls.get("dev", []), calls.get("test", [])
    sufficient_corpus = bool(coverage.get("verified_audio") and coverage.get("all_call_ids_supplied")
        and _number(seconds, 1800) and dev_calls and test_calls and not set(dev_calls).intersection(test_calls)
        and _number(split_seconds.get("dev"), .000001) and _number(split_seconds.get("test"), .000001))
    criteria["representative_corpus"] = _criterion("pass" if sufficient_corpus else "pending",
        {"unique_audio_minutes": seconds/60 if _number(seconds) else None,
         "heldout_audio_minutes": split_seconds.get("test", 0)/60,
         "development_calls": len(dev_calls), "heldout_calls": len(test_calls)},
        "Requires at least 30 minutes of unique audio across development and held-out calls with disjoint call IDs; short smoke clips cannot qualify")
    for view in scores.get("required_views", ["roman"]):
        metric = scores["total"]["views"][view]
        value = metric.get("wer")
        status = "pending" if metric.get("missing_samples") or value is None else ("pass" if value <= .20 else "fail")
        criteria[f"{view}_wer"] = _criterion(status, value, "Corpus WER must be <= 0.20; script views are scored separately")
    entity_views = scores["total"].get("entities_by_view", {})
    entity_values = {}
    for view in scores.get("required_views", ["roman"]):
        metrics = entity_views.get(view, {})
        recall = metrics.get("recall")
        both_kinds = all(metrics.get("entities", {}).get(kind, {}).get("reference", 0) > 0 for kind in ("name", "number"))
        state = "pending" if not _number(recall) or not both_kinds or metrics.get("missing_samples", 1) else ("pass" if recall >= .95 else "fail")
        entity_values[view] = {"status": state, "recall": recall}
    entity_states = [value["status"] for value in entity_values.values()]
    entity_status = "fail" if "fail" in entity_states else ("pass" if entity_states and all(s == "pass" for s in entity_states) else "pending")
    criteria["entity_preservation"] = _criterion(entity_status, entity_values,
        "Each required display view needs reference entity recall >=0.95 with both names and numbers; this is not precision")
    for key in ("live_latency", "stable_backlog", "speaker_agreement", "capture_integrity"):
        criteria[key] = _criterion("pending", None, "Matching measured live evidence is missing")
    evidence_errors = []
    if evidence is not None:
        if not isinstance(evidence, dict):
            raise ValueError("Live evidence must be a JSON object")
        matching = evidence.get("version") == 1 and evidence.get("phase") == "live" and evidence.get("clock") == "monotonic"
        for key in ("manifest_sha256", "corpus_sha256", "config_sha256"):
            matching = matching and bool(scores.get(key)) and evidence.get(key) == scores.get(key)
        expected_ids = {row["sample_id"] for row in rows}
        measured = evidence.get("samples", [])
        observed_ids = [item.get("sample_id") for item in measured if isinstance(item, dict)] if isinstance(measured, list) else []
        matching = matching and isinstance(measured, list) and len(observed_ids) == len(measured) and len(observed_ids) == len(set(observed_ids)) and set(observed_ids) == expected_ids
        if not matching:
            evidence_errors.append("Evidence is not a complete live run for the exact scored configuration, manifest, corpus, and samples")
        else:
            latency, backlog, speaker, integrity, live_durations = [], [], [], [], []
            for item in measured:
                sample_id = item["sample_id"]
                events = item.get("latency_events", [])
                valid_events = bool(events) and isinstance(events, list)
                per_sample_latency = []
                if valid_events:
                    for event in events:
                        if not isinstance(event, dict):
                            valid_events = False
                            break
                        end, displayed, identified = [event.get(key) for key in ("speech_end_ms", "roman_displayed_ms", "speaker_displayed_ms")]
                        if not all(_number(value) for value in (end, displayed, identified)) or displayed < end or identified < end:
                            valid_events = False
                            break
                        per_sample_latency.append((max(displayed, identified)-end)/1000)
                if valid_events:
                    latency.extend(per_sample_latency)
                else:
                    evidence_errors.append(f"{sample_id}: missing or invalid timestamped Roman-text-plus-speaker display events")
                    latency.append(None)
                observations = evidence.get("backlog_observations", item.get("backlog_observations", []))
                valid_backlog = isinstance(observations, list) and len(observations) >= 3
                if valid_backlog:
                    valid_backlog = all(isinstance(x, dict) and _number(x.get("elapsed_seconds")) and _number(x.get("queued_audio_seconds")) for x in observations)
                if valid_backlog:
                    times = [x["elapsed_seconds"] for x in observations]
                    queues = [x["queued_audio_seconds"] for x in observations]
                    valid_backlog = all(a < b for a, b in zip(times, times[1:]))
                live_durations.append(times[-1]-times[0] if valid_backlog else None)
                backlog.append((times[-1]-times[0] >= 3600 and max(queues) <= 15 and queues[-1] <= queues[0]+1) if valid_backlog else None)
                agreement = item.get("speaker_agreement", {})
                agreement = agreement if isinstance(agreement, dict) else {}
                correct, reference = agreement.get("correct_speaker_seconds"), agreement.get("reference_speaker_seconds")
                speaker.append((correct, reference) if agreement.get("scope") == "remote" and
                    agreement.get("method") == "optimal_mapping_reference_speaker_time" and
                    _number(correct) and _number(reference, .000001) and correct <= reference else None)
                dropouts = evidence.get("capture_dropouts", item.get("capture_dropouts"))
                integrity.append(dropouts == 0 if isinstance(dropouts, int) and not isinstance(dropouts, bool) and dropouts >= 0 else None)
            if latency and all(value is not None for value in latency):
                latency.sort()
                p95 = latency[max(0, math.ceil(len(latency)*.95)-1)]
                criteria["live_latency"] = _criterion("pass" if p95 <= 15 else "fail", p95,
                    f"Nearest-rank p95 across {len(latency)} speech-end to Roman-text-and-speaker display events; limit 15 seconds")
            if backlog and all(value is not None for value in backlog):
                criteria["stable_backlog"] = _criterion("pass" if all(backlog) else "fail", all(backlog),
                    f"Measured span {min(live_durations):g}s; needs >=3600s sustained live audio and >=3 increasing-time observations; queue max <=15s, ending <=starting+1s")
            if speaker and all(value is not None for value in speaker):
                accuracy = sum(x[0] for x in speaker) / sum(x[1] for x in speaker)
                criteria["speaker_agreement"] = _criterion("pass" if accuracy >= .90 else "fail", accuracy,
                    "Remote reference speaker-time agreement after optimal identity mapping must be >=0.90; missed speech counts against it")
            if integrity and all(value is not None for value in integrity):
                criteria["capture_integrity"] = _criterion("pass" if all(integrity) else "fail", all(integrity), "No recorded capture dropouts")
    states = [criterion["status"] for criterion in criteria.values()]
    decision = "fail" if "fail" in states else ("pass" if all(s == "pass" for s in states) else "pending")
    return {"version": 1, "kind": "sttbench_release_report", "decision": decision,
            "manifest_sha256": scores.get("manifest_sha256"), "corpus_sha256": scores.get("corpus_sha256"),
            "config_sha256": scores.get("config_sha256"), "criteria": criteria, "evidence_errors": evidence_errors,
            "batch_timing": scores["total"]["batch_timing"],
            "warnings": ["Small numbers of calls limit generalizability; duration and threshold checks do not establish broad Hinglish coverage."] if len(dev_calls) < 3 or len(test_calls) < 3 else [],
            "interpretation": "Batch transcription speed never establishes live latency, speaker quality, or full-app readiness."}
