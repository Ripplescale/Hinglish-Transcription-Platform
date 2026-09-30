"""Reconcile anonymous acoustic clusters, never assign words from coarse ASR windows."""
from __future__ import annotations
import copy
import math


def validated_turns(turns):
    result = []
    for turn in turns:
        start, end = float(turn['start_seconds']), float(turn['end_seconds'])
        label = str(turn['model_speaker_label'])
        if not label or not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
            raise ValueError('Invalid speaker turn')
        result.append({'start_seconds': start, 'end_seconds': end, 'model_speaker_label': label})
    return sorted(result, key=lambda turn: (turn['start_seconds'], turn['end_seconds'], turn['model_speaker_label']))


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def duration(intervals):
    return sum(end-start for start, end in union(intervals))


def intersect(first, second):
    return duration([(max(a, c), min(b, d)) for a, b in union(first) for c, d in union(second) if max(a, c) < min(b, d)])


def reconcile_speakers(session_id, turns, segments, previous=None):
    turns = validated_turns(turns)
    previous = previous or {}
    if previous and previous.get('session_id') != session_id:
        raise ValueError('Speaker identities belong to another recording')
    old_speakers = {speaker['id']: copy.deepcopy(speaker) for speaker in previous.get('speakers', [])}
    old_intervals = {}
    for turn in previous.get('turns', []):
        if turn['speaker_id'] not in old_speakers:
            raise ValueError('Previous turn refers to an unknown speaker')
        old_intervals.setdefault(turn['speaker_id'], []).append((float(turn['start_seconds']), float(turn['end_seconds'])))
    intervals = {}
    for turn in turns:
        intervals.setdefault(turn['model_speaker_label'], []).append((turn['start_seconds'], turn['end_seconds']))
    scores = {}
    for label, current in intervals.items():
        for speaker, old in old_intervals.items():
            shared = intersect(current, old)
            scores[label, speaker] = shared / max(duration(current), duration(old)) if shared else 0.0
    # Require strong mutual and unambiguous overlap. A split or merge receives a
    # new ID rather than silently inheriting a manual person's name.
    mapping, reused, unresolved = {}, [], []
    for label in intervals:
        ranked = sorted(((scores[label, old], old) for old in old_intervals), reverse=True)
        if ranked:
            best, speaker = ranked[0]
            second = ranked[1][0] if len(ranked) > 1 else 0.0
            competitors = sorted((scores[other, speaker] for other in intervals if other != label), reverse=True)
            competitor = competitors[0] if competitors else 0.0
            if best >= 0.65 and best-second >= 0.2 and best-competitor >= 0.2:
                mapping[label] = speaker
                reused.append({'model_speaker_label': label, 'speaker_id': speaker, 'overlap_score': best})
                continue
        unresolved.append(label)
    speakers = old_speakers
    for speaker in speakers.values():
        speaker['active'] = False
    for label in intervals:
        if label not in mapping:
            sequence = 1
            while f'speaker-{sequence:04d}' in speakers:
                sequence += 1
            mapping[label] = f'speaker-{sequence:04d}'
            speakers[mapping[label]] = {'id': mapping[label], 'display_name': None, 'active': True}
        speakers[mapping[label]]['active'] = True
    assigned_turns = [{**turn, 'id': f'turn-{index:06d}', 'speaker_id': mapping[turn['model_speaker_label']]} for index, turn in enumerate(turns)]
    assignments = {}
    for segment in segments:
        identifier = segment['id']
        if identifier in assignments:
            raise ValueError('Duplicate transcript segment identity')
        start, end = float(segment['start_seconds']), float(segment['end_seconds'])
        if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
            raise ValueError('Invalid transcript window')
        relevant = [turn for turn in assigned_turns if max(start, turn['start_seconds']) < min(end, turn['end_seconds'])]
        overlap = any(first['speaker_id'] != second['speaker_id'] and
                      max(start, first['start_seconds'], second['start_seconds']) < min(end, first['end_seconds'], second['end_seconds'])
                      for index, first in enumerate(relevant) for second in relevant[index+1:])
        assignments[identifier] = {
            'speaker_id': None,
            'speaker_candidates': sorted({turn['speaker_id'] for turn in relevant}),
            'turn_ids': [turn['id'] for turn in relevant],
            'has_overlap': overlap,
            'assignment': 'window_candidates_only',
            'timestamp_kind': 'audio_window',
        }
    return {
        'version': 1, 'session_id': session_id,
        'speakers': list(speakers.values()), 'turns': assigned_turns,
        'segment_assignments': assignments,
        'reconciliation': {'reused': reused, 'new_model_labels': unresolved,
                           'requires_review': bool(previous and unresolved),
                           'method': 'mutual_temporal_overlap', 'word_alignment_available': False},
    }
