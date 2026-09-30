'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import type { Transcript } from '@/types';
import { emptyWorkspace, mergeSavedWorkspace, setCorrection, type TranscriptWorkspace } from '@/lib/transcript-workspace';

export function useTranscriptWorkspace(meetingId: string) {
  const [workspace, setWorkspace] = useState(() => emptyWorkspace(meetingId));
  const [loaded, setLoaded] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const meetingRef = useRef(meetingId);
  meetingRef.current = meetingId;
  const editGeneration = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    setDirty(false);
    setSaving(false);
    setError(null);
    invoke<TranscriptWorkspace>('load_transcript_workspace', { meetingId }).then(result => {
      if (!cancelled) { setWorkspace(result); setLoaded(true); }
    }).catch(reason => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, [meetingId]);

  useEffect(() => {
    let cancelled = false;
    const update = (event: Event) => {
      if ((event as CustomEvent<{ meetingId: string }>).detail.meetingId !== meetingId || dirty || saving) return;
      const generation = editGeneration.current;
      void invoke<TranscriptWorkspace>('load_transcript_workspace', { meetingId }).then(result => { if (!cancelled && generation === editGeneration.current) setWorkspace(result); }).catch(reason => { if (!cancelled) setError(String(reason)); });
    };
    window.addEventListener('local-transcript-updated', update);
    return () => { cancelled = true; window.removeEventListener('local-transcript-updated', update); };
  }, [meetingId, dirty, saving]);

  useEffect(() => {
    if (!dirty) return;
    const preventClose = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', preventClose);
    return () => window.removeEventListener('beforeunload', preventClose);
  }, [dirty]);

  const updateNotes = (notes: string) => { editGeneration.current += 1; setWorkspace(value => ({ ...value, notes })); setDirty(true); };
  const updateProject = (project_id: string | null) => { editGeneration.current += 1; setWorkspace(value => ({ ...value, project_id })); setDirty(true); };
  const updateSpeakerName = (speakerId: string, name: string) => { editGeneration.current += 1; setWorkspace(value => ({ ...value, speaker_names: { ...value.speaker_names, [speakerId]: name } })); setDirty(true); };
  const updateCorrection = (segment: Transcript, text: string) => {
    editGeneration.current += 1;
    setWorkspace(value => ({ ...value, corrections: setCorrection(value.corrections, segment, text, new Date().toISOString()) }));
    setDirty(true);
  };
  const save = useCallback(async () => {
    if (!loaded || saving) return;
    setSaving(true);
    setError(null);
    const generationAtSave = editGeneration.current;
    const savedMeetingId = meetingId;
    try {
      const saved = await invoke<TranscriptWorkspace>('save_transcript_workspace', {
        meetingId, expectedRevision: workspace.revision, notes: workspace.notes,
        corrections: workspace.corrections, projectId: workspace.project_id, profile: workspace.profile,
        speakerNames: workspace.speaker_names ?? {},
      });
      if (meetingRef.current !== savedMeetingId) return;
      const hasNewerEdits = generationAtSave !== editGeneration.current;
      setWorkspace(current => mergeSavedWorkspace(current, saved, hasNewerEdits));
      setDirty(hasNewerEdits);
    } catch (reason) {
      if (meetingRef.current === savedMeetingId) setError(`Could not save. Your edits remain here. ${String(reason)}`);
      throw reason;
    } finally { if (meetingRef.current === savedMeetingId) setSaving(false); }
  }, [loaded, saving, meetingId, workspace]);
  return { workspace, loaded, dirty, saving, error, updateNotes, updateProject, updateCorrection, updateSpeakerName, save };
}
