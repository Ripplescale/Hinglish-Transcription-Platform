"""Audit GGML vocabulary IDs and dimensions against local HF source assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct


def byte_decoder() -> dict[str, int]:
    values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    encoded = values.copy()
    for value in range(256):
        if value not in values:
            values.append(value)
            encoded.append(256 + len(encoded) - (126 - 33 + 1 + 172 - 161 + 1 + 255 - 174 + 1))
    return {chr(char): value for value, char in zip(values, encoded)}


def audit(model: Path, binary: Path) -> dict:
    config = json.loads((model / "config.json").read_text(encoding="utf-8"))
    vocab = json.loads((model / "vocab.json").read_text(encoding="utf-8"))
    added = json.loads((model / "added_tokens.json").read_text(encoding="utf-8"))
    with binary.open("rb") as stream:
        header = struct.unpack("<12i", stream.read(48))
        expected = (0x67676d6c, config["vocab_size"], config["max_source_positions"],
                    config["d_model"], config["encoder_attention_heads"], config["encoder_layers"],
                    config.get("max_length") or config["max_target_positions"], config["d_model"],
                    config["decoder_attention_heads"], config["decoder_layers"], config["num_mel_bins"])
        if header[:11] != expected:
            raise ValueError("Converted GGML dimensions differ from source config")
        mel_rows, mel_columns = struct.unpack("<2i", stream.read(8))
        if mel_rows != config["num_mel_bins"] or mel_columns != 201:
            raise ValueError("Unexpected mel filter dimensions")
        stream.seek(mel_rows * mel_columns * 4, 1)
        count, = struct.unpack("<i", stream.read(4))
        if count != len(vocab):
            raise ValueError("Serialized base vocabulary size differs from source")
        decoder = byte_decoder()
        for token, token_id in sorted(vocab.items(), key=lambda item: item[1]):
            size, = struct.unpack("<i", stream.read(4))
            raw = stream.read(size)
            if raw != bytes(decoder[char] for char in token):
                raise ValueError(f"Base vocabulary differs at token {token_id}")
    if set(added.values()) != set(range(len(vocab), config["vocab_size"])):
        raise ValueError("Added vocabulary IDs are not contiguous above base vocabulary")
    # Pinned whisper.cpp whisper_vocab has n_languages=n_vocab-51766 for
    # multilingual models; token IDs are adjusted by n_languages-98.
    delta = config["vocab_size"] - 51766 - 98
    required = {"<|endoftext|>": 50257, "<|startoftranscript|>": 50258,
                "<|en|>": 50259, "<|hi|>": 50276, "<|translate|>": 50357 + delta,
                "<|transcribe|>": 50358 + delta, "<|startoflm|>": 50359 + delta,
                "<|startofprev|>": 50360 + delta, "<|nospeech|>": 50361 + delta,
                "<|notimestamps|>": 50362 + delta}
    for token, token_id in required.items():
        if added.get(token) != token_id:
            raise ValueError(f"Required token ID differs from whisper.cpp reconstruction: {token}")
    timestamp_start = 50363 + delta
    for index in range(1501):
        if added.get(f"<|{index * .02:.2f}|>") != timestamp_start + index:
            raise ValueError(f"Timestamp token mismatch at {index}")
    return {"status": "passed", "binary": str(binary.resolve()), "total_vocabulary": header[1],
            "base_vocabulary_entries_compared": count, "added_tokens_count": len(added),
            "required_special_token_ids": required, "timestamp_token_ids_compared": 1501,
            "ggml_ftype": header[-1], "note": "Token IDs and binary layout checked; no recognition or timestamp accuracy claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing audit")
    result = audit(args.model, args.binary)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
