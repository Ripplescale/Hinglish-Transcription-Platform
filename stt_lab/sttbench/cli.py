"""Run with python -m sttbench; score/validate/report have no ML dependencies."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .execution import run, select_model
from .manifest import load_manifest, read_json, read_jsonl, write_json
from .reporting import release_report
from .scoring import corpus_digest, score


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate local WAV files, references, and dev/test identities")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--no-audio-check", action="store_true", help="Schema only; does not qualify audio provenance")
    score_command = commands.add_parser("score", help="Score original and Roman hypotheses separately")
    score_command.add_argument("--manifest", type=Path, required=True)
    score_command.add_argument("--hypotheses", type=Path, required=True)
    score_command.add_argument("--output", type=Path, required=True)
    score_command.add_argument("--split", choices=("dev", "test", "all"), default="test")
    score_command.add_argument("--equivalences", type=Path, help="Explicit phrase -> canonical phrase JSON, applied to both sides")
    score_command.add_argument("--rebind-references", action="store_true",
        help="Use corrected annotations with earlier predictions only when input structure and exact audio hashes still match")
    execute = commands.add_parser("run", help="Transcribe local WAV files through the selected runtime; resume successful samples")
    execute.add_argument("--manifest", type=Path, required=True)
    execute.add_argument("--registry", "--model-registry", dest="registry", type=Path, default=Path(__file__).resolve().parent.parent / "models.json")
    execute.add_argument("--model", required=True)
    execute.add_argument("--runtime-config", type=Path, required=True)
    execute.add_argument("--model-path", type=Path)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    report = commands.add_parser("report", help="Create a fail-closed release decision from scores and optional measured live evidence")
    report.add_argument("--scores", type=Path, required=True)
    report.add_argument("--evidence", type=Path)
    report.add_argument("--output", type=Path, required=True)
    return result


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "validate":
            manifest = load_manifest(arguments.manifest, check_audio=not arguments.no_audio_check)
            value = {"valid": True, "samples": len(manifest["samples"]), "manifest_sha256": manifest["manifest_sha256"],
                     "corpus_sha256": corpus_digest(manifest), "warnings": manifest["warnings"]}
        elif arguments.command == "score":
            manifest = load_manifest(arguments.manifest)
            value = score(manifest, read_jsonl(arguments.hypotheses),
                          read_json(arguments.equivalences) if arguments.equivalences else None, arguments.split,
                          rebind_references=arguments.rebind_references)
            write_json(arguments.output, value)
            value = {"output": str(arguments.output.resolve()), "samples": value["total"]["samples"],
                     "views": value["total"]["views"], "entity_recall": value["total"]["entity_recall"]}
        elif arguments.command == "run":
            manifest = load_manifest(arguments.manifest)
            model = select_model(read_json(arguments.registry), arguments.model)
            config = read_json(arguments.runtime_config)
            if not isinstance(config, dict):
                raise ValueError("Runtime configuration must be a JSON object")
            value = run(manifest, model, config, arguments.output, arguments.split, arguments.model_path)
        else:
            value = release_report(read_json(arguments.scores), read_json(arguments.evidence) if arguments.evidence else None)
            write_json(arguments.output, value)
            value = {"decision": value["decision"], "output": str(arguments.output.resolve()), "criteria": value["criteria"]}
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(f"sttbench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
