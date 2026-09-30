"""Create a short interrupted-capture fixture from approved native QA audio."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid
import wave


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(fixture_path, seconds=17.3):
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    root = Path(fixture["data_root"]).resolve()
    if fixture_path.resolve().parent != root or not root.name.startswith("native-qa-"):
        raise ValueError("Only an isolated native QA root may receive this fixture")
    source = Path(fixture["session_dir"]) / "audio.wav"
    sid = str(uuid.uuid4())
    session = root / "recordings" / sid
    track = session / "tracks" / "system"
    track.mkdir(parents=True)
    metadata = json.loads((source.parent / "session.json").read_text(encoding="utf-8"))
    metadata.update(session_id=sid, meeting_name="Native recovery check — interrupted saved audio")
    (session / "session.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with wave.open(str(source), "rb") as audio:
        rate = audio.getframerate()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("Expected mono PCM16 fixture")
        pcm = audio.readframes(round(seconds * rate))
    if len(pcm) != round(seconds * rate) * 2:
        raise ValueError("Source fixture is too short")
    rows = []
    for sequence, offset in enumerate(range(0, len(pcm), rate * 2)):
        chunk = track / f"{sequence:08d}.wav"
        block = pcm[offset:offset + rate * 2]
        count = len(block) // 2
        with wave.open(str(chunk), "wb") as output:
            output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            output.writeframes(block)
        rows.append({"kind": "audio_chunk", "track": "system", "sequence": sequence,
                     "file": f"tracks/system/{chunk.name}", "sample_rate": rate,
                     "sample_count": count, "start_seconds": offset / (rate * 2),
                     "end_seconds": (offset / 2 + count) / rate, "sha256": sha(chunk)})
    # No capture_stopped/finalized record and no mixed audio: emulate a process
    # interruption after the last committed partial chunk was saved.
    journal = session / "timeline.jsonl"
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = {"data_root": str(root), "session_dir": str(session), "session_id": sid,
              "duration_seconds": seconds, "recorded_live": False,
              "source_sha256": sha(source), "journal_sha256": sha(journal),
              "source_files": {str(path.relative_to(session)): sha(path)
                               for path in session.rglob("*") if path.is_file()}}
    output = root / "recovery-fixture.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"fixture": str(output), "session_dir": str(session), "duration_seconds": seconds}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.fixture)))
