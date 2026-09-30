"""Copy local recordings into private storage and create a reviewable benchmark.

This is an explicit intake operation, never an upload. Originals are preserved.
Only PCM WAV is supported without FFmpeg; other formats require --ffmpeg.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import wave


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def prepare(paths: list[Path], output: Path, ffmpeg: Path | None = None, clip_seconds: int = 30) -> Path:
    if not 5 <= clip_seconds <= 30:
        raise ValueError('Clip length must be between 5 and 30 seconds')
    sources = [path.expanduser().resolve(strict=True) for path in paths]
    if not sources or any(not path.is_file() for path in sources):
        raise ValueError('Provide at least one existing recording file')
    output = output.resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; existing intake is never overwritten')
    # Detect duplicate recordings before creating or copying anything. Different
    # filenames must not let identical audio enter both development and test.
    source_hashes = [sha256(path) for path in sources]
    if len(set(source_hashes)) != len(source_hashes):
        raise ValueError('Duplicate recordings would leak across development and test')
    output.mkdir(parents=True)
    # Roman is the common product reading view. A native-Roman recognizer must
    # not be penalized for lacking an invented Devanagari rendering.
    manifest = {'version': 1, 'required_views': ['roman'], 'samples': []}
    provenance = {'version': 1, 'purpose': 'user-provided local evaluation', 'sources': []}
    for number, source in enumerate(sources, 1):
        call_id = f'call-{number:02d}'
        call_dir = output / call_id
        call_dir.mkdir()
        digest = sha256(source)
        preserved = call_dir / ('original' + source.suffix.lower())
        shutil.copyfile(source, preserved)
        if sha256(preserved) != digest:
            raise ValueError('Copy verification failed')
        normalized = call_dir / 'analysis.wav'
        is_compatible = False
        if source.suffix.lower() == '.wav':
            try:
                with wave.open(str(preserved), 'rb') as audio:
                    is_compatible = audio.getframerate() == 16000 and audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getcomptype() == 'NONE'
            except (wave.Error, EOFError):
                pass
        if is_compatible:
            shutil.copyfile(preserved, normalized)
        elif ffmpeg:
            executable = ffmpeg.resolve(strict=True)
            result = subprocess.run([str(executable), '-nostdin', '-v', 'error', '-i', str(preserved), '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(normalized)], capture_output=True, text=True, timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError(f'Audio decoding failed: {result.stderr[-1000:]}')
        else:
            raise ValueError('This format needs FFmpeg. The original copy is retained; provide --ffmpeg and a new output directory.')
        with wave.open(str(normalized), 'rb') as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE':
                raise ValueError('Normalized audio must be mono 16 kHz PCM16; decoder output did not satisfy the contract')
            frame_count = audio.getnframes()
            if frame_count == 0:
                raise ValueError('Recording contains no audio frames')
            rate = audio.getframerate()
            clip_frames = clip_seconds * rate
            clip_index = 0
            consumed_frames = 0
            while data := audio.readframes(clip_frames):
                if len(data) % 2:
                    raise ValueError('Truncated PCM16 audio contains an incomplete sample frame')
                consumed_frames += len(data) // 2
                clip_index += 1
                filename = f'clip-{clip_index:04d}.wav'
                clip_path = call_dir / filename
                with wave.open(str(clip_path), 'wb') as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(rate)
                    clip.writeframes(data)
                sample_id = f'{call_id}-{clip_index:04d}'
                # Hold out entire calls, not adjacent clips from the same call.
                split = 'dev' if number == 1 else 'test'
                manifest['samples'].append({
                    'id': sample_id, 'audio': f'{call_id}/{filename}', 'split': split,
                    'call_id': call_id, 'source_start_seconds': (clip_index - 1) * clip_seconds,
                    'source_end_seconds': min(clip_index * clip_seconds, frame_count / rate),
                    'reference_status': 'pending', 'entities': [],
                    'slices': {'source': 'personal_call'}, 'license': 'user-provided-private-evaluation',
                    'audio_sha256': sha256(clip_path),
                })
            if consumed_frames != frame_count:
                raise ValueError(f'Truncated WAV: header declares {frame_count} frames but only {consumed_frames} were read')
        provenance['sources'].append({'call_id': call_id, 'original_path': str(source), 'preserved_path': str(preserved.relative_to(output)), 'sha256': digest, 'analysis_sha256': sha256(normalized), 'source_track': 'mixed_or_unknown', 'duration_seconds': frame_count / rate})
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (output / 'intake-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    (output / 'REVIEW.md').write_text('''# Reference review required

These are private copies, never uploads. Original recordings are unchanged.
First call is development; subsequent calls are held out. With only one call,
there is no test set and no release decision can pass.

Generate draft transcripts, listen to the clips, then fill each sample's
`reference.roman` and, for mixed-script models, `reference.original`; add important names/numbers to
`entities`, and set `reference_status` to `reviewed` only after human correction.
Machine output must not be used as its own ground truth. Record speaker IDs and
use disjoint speakers between development and test when possible.

Clips are contiguous 30-second windows (or the explicitly requested duration).
They preserve all speech and silence but can cut an utterance at a boundary;
review boundary words against the preserved full recording.

No speaker identification, streaming latency, or model qualification is implied
by this intake. Benchmark reports remain pending until evidence is supplied.
''', encoding='utf-8')
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recordings', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ffmpeg', type=Path)
    parser.add_argument('--clip-seconds', type=int, default=30)
    args = parser.parse_args()
    local = os.environ.get('LOCALAPPDATA')
    if not args.output and not local:
        parser.error('LOCALAPPDATA is unavailable; supply a private --output directory')
    output = args.output or Path(local) / 'STTApp' / 'benchmark' / ('intake-' + uuid.uuid4().hex[:10])
    try:
        path = prepare(args.recordings, output, args.ffmpeg, args.clip_seconds)
        print(json.dumps({'manifest': str(path), 'reference_status': 'pending'}, indent=2))
        return 0
    except (OSError, ValueError, wave.Error, EOFError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
