'use client';

import { useEffect, useRef, useState, type RefObject } from 'react';
import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { toast } from 'sonner';
import type { Transcript } from '@/types';
import type { useTranscriptWorkspace } from '@/hooks/meeting-details/useTranscriptWorkspace';
import { effectiveText, fetchCompleteTranscript, makeTranscriptExport, recordingTime } from '@/lib/transcript-workspace';
import { Button } from '@/components/ui/button';
import { ArrowUpRight, ChevronDown, Download, FileText, Headphones, Pencil, Info } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { useLocalWorkflow } from '@/contexts/LocalWorkflowContext';
import { isTerminalJob, isTrelisProfile, profileLabel, type WorkflowRole } from '@/lib/local-transcription-workflow';
import { useRouter } from 'next/navigation';

type Review = ReturnType<typeof useTranscriptWorkspace>;
interface TranscriptLayer { meeting_id: string; title: string; profile?: string | null; workflow_role?: WorkflowRole | null; job_id?: string | null; state: string; primary: boolean }

export function TranscriptWorkspacePanel({ meeting, review, hasMore, isLoadingMore, totalCount, onLoadMore, onDraftChange, compact = false, playback }: {
  meeting: { id: string; title: string; created_at?: string; transcripts: Transcript[] };
  review: Review;
  hasMore?: boolean;
  isLoadingMore?: boolean;
  totalCount?: number;
  onLoadMore?: () => void;
  onDraftChange?: (dirty: boolean) => void;
  compact?: boolean;
  playback?: { audio: RefObject<HTMLAudioElement>; path: string | null; onError: (message: string) => void };
}) {
  const [audioPath, setAudioPath] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [exporting, setExporting] = useState(false);
  const [displayTitle, setDisplayTitle] = useState(meeting.title);
  const [layers, setLayers] = useState<TranscriptLayer[]>([]);
  const [layerError, setLayerError] = useState<string | null>(null);
  const sourceMeetingId = useRef(meeting.id);
  const router = useRouter();
  const { state } = useLocalWorkflow();
  const run = state.runs.find(item => item.transcriptMeetingId === meeting.id) ?? state.runs.find(item => item.primary && item.meetingId === meeting.id);
  const activeLayer = layers.find(layer => layer.meeting_id === meeting.id);
  const activeRole = activeLayer?.workflow_role ?? (run && 'role' in run ? run.role : null) ?? (activeLayer?.primary && layers.some(layer => layer.workflow_role === 'live-draft') ? 'final' : null);
  const needsAttention = !!(run?.error || run?.job?.error) || ['failed', 'stopped'].includes(activeLayer?.state ?? '');
  const pendingFinal = activeRole === 'final' && !meeting.transcripts.length && (run?.job?.state ?? activeLayer?.state) !== 'complete' && !needsAttention;
  const processing = run?.job && !isTerminalJob(run.job.state);
  const liveState = run?.job?.state ?? activeLayer?.state;
  const liveTitle = liveState === 'complete' ? 'Final transcript' : !run?.recording && !isTerminalJob(liveState ?? 'queued') && !!run?.captureEnded ? 'Finishing transcript' : 'Live transcript';
  const internalAudio = useRef<HTMLAudioElement>(null);
  const audio = playback?.audio ?? internalAudio;
  const playablePath = playback ? playback.path : audioPath;
  const profileId = review.workspace.profile || activeLayer?.profile;
  const transcriptProfile = profileLabel(profileId);
  const layerRefresh = state.runs.map(item => `${item.transcriptMeetingId}:${item.job?.job_id}:${item.job?.state}`).join('|');
  useEffect(() => {
    let cancelled = false;
    sourceMeetingId.current = meeting.id;
    const refresh = () => invoke<{ source_meeting_id: string; layers: TranscriptLayer[] }>('get_local_transcript_layers', { meetingId: meeting.id })
      .then(result => { if (!cancelled) { sourceMeetingId.current = result.source_meeting_id; setLayers(result.layers); setLayerError(null); } })
      .catch(reason => { if (!cancelled) setLayerError(String(reason)); });
    void refresh();
    const updated = () => { void refresh(); };
    window.addEventListener('local-transcript-updated', updated);
    return () => { cancelled = true; window.removeEventListener('local-transcript-updated', updated); };
  }, [meeting.id, layerRefresh]);
  const openLayer = (id: string) => {
    const href = `/meeting-details?id=${encodeURIComponent(id)}`;
    if (window.dispatchEvent(new CustomEvent('xx-before-navigate', { cancelable: true, detail: { href } }))) router.push(href);
  };
  const layerName = (layer: TranscriptLayer) => {
    if (layer.workflow_role === 'fallback') return 'Apex fallback';
    if (layer.workflow_role === 'live-final') {
      const layerRun = state.runs.find(item => item.job?.job_id === layer.job_id);
      return layer.state === 'complete' ? 'Final transcript' : layerRun?.captureEnded && !isTerminalJob(layer.state) ? 'Finishing transcript' : 'Live transcript';
    }
    if (layer.workflow_role === 'live-draft') return 'Live draft';
    if (layer.workflow_role === 'final' || (layer.primary && layers.some(item => item.workflow_role === 'live-draft'))) return 'Final transcript';
    return layer.profile === 'apex-20' ? 'Apex version' : isTrelisProfile(layer.profile) ? `${profileLabel(layer.profile)} version` : 'Original transcript';
  };
  useEffect(() => { onDraftChange?.(editing !== null); }, [editing, onDraftChange]);
  useEffect(() => { setEditing(null); }, [meeting.id]);
  useEffect(() => {
    setDisplayTitle(meeting.title);
    const renamed = (event: Event) => {
      const detail = (event as CustomEvent<{ id: string; title: string }>).detail;
      if (detail.id === meeting.id || detail.id === sourceMeetingId.current) setDisplayTitle(detail.title);
    };
    window.addEventListener('xx-meeting-renamed', renamed);
    return () => window.removeEventListener('xx-meeting-renamed', renamed);
  }, [meeting.id, meeting.title]);

  useEffect(() => {
    if (playback) return;
    let cancelled = false;
    setAudioPath(null);
    setAudioError(null);
    invoke<{ path: string | null }>('local_get_meeting_audio', { meetingId: meeting.id })
      .then(result => { if (!cancelled) setAudioPath(result.path); })
      .catch(reason => { if (!cancelled) setAudioError(`Recording unavailable: ${String(reason)}`); });
    return () => { cancelled = true; };
  }, [meeting.id, run?.recording, !!playback]);

  const exportTranscript = async (format: 'txt' | 'md' | 'json' | 'claude', handoff?: 'chat' | 'cowork') => {
    setExporting(true);
    try {
      const all = await fetchCompleteTranscript(meeting.id, (meetingId, limit, offset) => invoke('api_get_meeting_transcripts', { meetingId, limit, offset }));
      const output = makeTranscriptExport({ ...meeting, title: displayTitle }, all, review.workspace, format, review.dirty);
      if (format === 'claude') {
        if (handoff === 'cowork') {
          await invoke('open_claude_desktop', { mode: 'cowork', transcript: output, title: displayTitle });
          toast.success('Opened Claude Cowork with a local transcript file. Review it before sending.');
        } else {
          await navigator.clipboard.writeText(output);
          if (handoff === 'chat') {
            try {
              await invoke('open_claude_desktop', { mode: 'chat', transcript: output, title: displayTitle });
              toast.success('Transcript copied. Paste it in Claude Desktop and send when ready.');
            } catch (reason) { toast.warning('Transcript copied, but Claude could not be opened. Open it manually and paste.', { description: String(reason) }); }
          } else toast.success('Copied for Claude. Paste it yourself when ready.');
        }
      } else {
        const { writeTextFile } = await import('@tauri-apps/plugin-fs');
        const safeTitle = displayTitle.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 100) || 'transcript';
        const path = await invoke<string | null>('plugin:dialog|save', { options: { defaultPath: `${safeTitle}.${format}`, filters: [{ name: format.toUpperCase(), extensions: [format] }] } });
        if (!path) return;
        await writeTextFile(path, output);
        toast.success('Complete transcript exported');
      }
    } catch (reason) { toast.error(format === 'claude' ? 'Claude handoff did not complete. You can export TXT and attach it manually.' : 'Could not export transcript', { description: String(reason) }); }
    finally { setExporting(false); }
  };

  const seek = (seconds: number) => {
    if (!audio.current || !Number.isFinite(seconds) || seconds < 0) return;
    audio.current.currentTime = seconds;
    void audio.current.play().catch(reason => playback ? playback.onError(String(reason)) : setAudioError(String(reason)));
  };

  return <section className="flex h-full min-h-0 min-w-0 flex-col bg-[var(--xx-paper)] text-[var(--xx-ink)]" aria-label="Transcript review">
    <header className={`shrink-0 border-b border-[var(--xx-border)] px-5 ${compact ? 'pt-4 pb-3 space-y-2 max-h-[45%] overflow-y-auto' : 'pt-5 pb-4 space-y-3'}`}>
      <div className="xx-eyebrow flex items-center gap-2"><FileText size={13} aria-hidden="true" />{activeRole === 'fallback' ? 'Apex fallback' : activeRole === 'live-final' ? liveTitle : activeRole === 'live-draft' ? 'Live draft' : activeRole === 'final' ? 'Final transcript' : 'Transcript'}</div>
      {!compact && <h1 className="xx-heading text-[24px] leading-tight break-words">{displayTitle}</h1>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-[var(--xx-muted)]">{transcriptProfile ? <span className="font-medium text-[var(--xx-accent)]">Transcript: {transcriptProfile}</span> : 'Original script'}<span className="mx-2">·</span>{totalCount ?? meeting.transcripts.length} segments</p>
        <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="xx-button-secondary !min-h-8 !px-3 text-xs" disabled={exporting || !review.loaded || editing !== null}><Download size={13} aria-hidden="true" />{exporting ? 'Preparing…' : 'Export'}<ChevronDown size={12} aria-hidden="true" /></button></DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64 rounded-xl border-[var(--xx-border)] bg-[var(--xx-paper)] text-[var(--xx-ink)] p-2">
            <DropdownMenuLabel className="text-[11px] font-normal text-[var(--xx-muted)]">Complete transcript + current corrections</DropdownMenuLabel>
            {(['txt', 'md', 'json'] as const).map(format => <DropdownMenuItem key={format} onSelect={() => void exportTranscript(format)}>Export {format.toUpperCase()}</DropdownMenuItem>)}
            <DropdownMenuSeparator className="bg-[var(--xx-border)]" />
            <DropdownMenuItem onSelect={() => void exportTranscript('claude', 'chat')}><ArrowUpRight size={14} />Copy &amp; open Claude</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportTranscript('claude')}>Copy only</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportTranscript('claude', 'cowork')}>Open in Claude Cowork</DropdownMenuItem>
            <p className="px-2 pt-2 pb-1 text-[11px] leading-4 text-[var(--xx-muted)]">Claude opens a draft or local attachment. You choose when to send.</p>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {layers.length > 1 && <nav aria-label="Transcript versions" className="flex max-w-full gap-1 overflow-x-auto rounded-lg border border-[var(--xx-border)] p-1">
        {layers.map(layer => <button type="button" key={layer.meeting_id} aria-current={layer.meeting_id === meeting.id ? 'page' : undefined} className={`shrink-0 rounded-md px-3 py-1.5 text-xs transition-colors ${layer.meeting_id === meeting.id ? 'bg-[var(--xx-canvas)] font-medium text-[var(--xx-accent)]' : 'text-[var(--xx-muted)] hover:bg-[var(--xx-canvas)]'}`} onClick={() => openLayer(layer.meeting_id)}>{layerName(layer)}{['queued', 'not_started'].includes(layer.state) ? ' · pending' : !isTerminalJob(layer.state) ? ' · processing' : layer.state === 'failed' || layer.state === 'stopped' ? ' · needs attention' : ''}</button>)}
      </nav>}
      {activeRole === 'live-draft' && <p className="text-[11px] leading-4 text-[var(--xx-muted)]">Apex live draft, kept separately from the Trelis final transcript and its corrections.</p>}
      {activeRole === 'fallback' && <p className="text-[11px] leading-4 text-[var(--xx-muted)]">Apex fallback. This version keeps its own notes and corrections; the Trelis transcript is still available.</p>}
      {activeRole === 'live-final' && <p className="text-[11px] leading-4 text-[var(--xx-muted)]">Trelis keeps the original Hindi + English script. Raw recognition and your corrections are saved separately.</p>}
      {layerError && <p className="text-xs text-amber-800" title={layerError}>Other transcript versions could not be loaded. Your current transcript is still available.</p>}
      {processing && <p role="status" className="text-[11px] text-[var(--xx-accent)]">{activeRole === 'live-final' && !run.recording ? 'Finishing transcript' : 'Transcribing'} · {Math.round(run.job?.processed_audio_seconds ?? 0)}s processed · {Math.round(run.job?.backlog_seconds ?? 0)}s waiting{run.recording ? ' · recording continues' : ''}</p>}
      {run && !run.job && !run.error && <p role="status" className="text-[11px] text-[var(--xx-accent)]">{run.pausedByUser ? 'Transcription paused. Your saved audio is retained.' : run.recording && run.preferences.timing === 'after-recording' ? 'Listening. Transcription starts when you stop.' : 'Audio saved. Waiting for the local worker.'}</p>}
      {(run?.error || run?.job?.error) && <p role="alert" className="text-xs text-amber-800">Audio is saved. Transcription needs attention — open Tools → Transcription &amp; versions.</p>}
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-2 [scrollbar-gutter:stable]">
      {!meeting.transcripts.length && <div className="py-12 text-center"><Headphones className="mx-auto mb-3 text-[var(--xx-accent)]" size={26} /><p className="text-sm text-[var(--xx-muted)]">{pendingFinal ? 'The final transcript is on its way.' : 'Your words will appear here.'}</p><p className="mt-2 text-xs leading-5 text-[var(--xx-muted)]">{pendingFinal ? 'Trelis processes the saved audio after the call. You can read the Apex live draft while you wait.' : activeRole === 'fallback' ? 'Apex replays the saved recording into this separate version.' : 'Audio is transcribed in the selected chunks plus processing time. Your original recording is retained.'}</p></div>}
      {meeting.transcripts.map(segment => {
        const text = effectiveText(segment, review.workspace.corrections);
        const corrected = text !== segment.text;
        const track = segment.source_track ?? review.workspace.segment_metadata?.[segment.id]?.source_track;
        const metadata = review.workspace.segment_metadata?.[segment.id];
        const speakers = review.workspace.speaker_metadata?.segment_assignments?.[segment.id];
        const stale = review.workspace.corrections.some(item => item.segment_id === segment.id && item.original_text !== segment.text);
        return <article key={segment.id} className="group border-b border-[var(--xx-border)] last:border-0 py-4 space-y-2">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--xx-muted)]">
            <button type="button" className="rounded px-1 -ml-1 font-mono text-[var(--xx-accent)] hover:bg-[var(--xx-canvas)] focus-visible:outline focus-visible:outline-2 disabled:opacity-40" disabled={!playablePath || segment.audio_start_time == null} onClick={() => seek(segment.audio_start_time!)} title="Play from this segment boundary">{recordingTime(segment.audio_start_time)}</button>
            {track && <span>{track === 'microphone' ? 'Microphone' : track === 'system' ? 'Computer audio' : `${track} track`}</span>}
            {segment.speaker_id && <span>{segment.speaker_id}</span>}
            {corrected && <span className="text-emerald-700">Corrected</span>}
            {!!metadata?.quality_flags?.length && <span className="text-amber-700">Review suggested</span>}
            <button type="button" className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-[var(--xx-accent)] hover:bg-[var(--xx-canvas)] focus-visible:outline focus-visible:outline-2 disabled:opacity-40" disabled={!review.loaded || review.saving || editing !== null} onClick={() => { setEditing(segment.id); setDraft(text); }}><Pencil size={11} aria-hidden="true" />Edit</button>
          </div>
          {editing === segment.id ? <div data-separate-save="Apply the correction first, then save your changes." className="space-y-2">
            <textarea aria-label="Correct transcript text" className="w-full min-h-32 border border-[var(--xx-border)] bg-white/50 rounded-xl p-3 text-[15px] leading-7 focus:outline-[var(--xx-accent)]" value={draft} onChange={event => setDraft(event.target.value)} />
            <div className="flex gap-2"><button className="xx-button-primary text-xs" disabled={review.saving} onClick={() => { review.updateCorrection(segment, draft); setEditing(null); }}>Apply correction</button><button className="xx-button-secondary text-xs" onClick={() => setEditing(null)}>Cancel</button></div>
          </div> : <p className="whitespace-pre-wrap text-[15px] leading-[1.85] break-words">{text}</p>}
          {corrected && <details className="text-xs text-gray-500"><summary className="cursor-pointer">Original recognition</summary><p className="whitespace-pre-wrap leading-6 mt-2">{segment.text}</p></details>}
          {stale && <p className="text-xs text-amber-700">A saved correction belongs to an earlier recognition result and was not applied.</p>}
          {!!speakers?.speaker_candidates.length && <p className="text-[11px] text-[var(--xx-muted)]" title="Estimated speakers heard in this audio window. Individual words are not assigned to a person.">Heard in this window: {speakers.speaker_candidates.map(id => review.workspace.speaker_names?.[id] || id).join(', ')}{speakers.has_overlap ? ' · overlapping speech' : ''}</p>}
          {!!metadata?.quality_flags?.length && <details className="text-xs text-amber-800"><summary className="cursor-pointer">Processing checks</summary><p className="mt-2">{metadata.quality_flags.map(flag => flag.replaceAll('_', ' ')).join(' · ')}</p>{metadata.alternative?.text && <div className="mt-2 space-y-2">{metadata.alternative.promoted ? <><p>A shorter-window retry was selected for this transcript. Review it against the recording.</p>{metadata.original_recognition_text != null ? <details><summary className="cursor-pointer">Recognition before shorter retry</summary><p className="mt-2 whitespace-pre-wrap leading-6">{metadata.original_recognition_text}</p></details> : <p>The recognition before retry is not available in this workspace.</p>}</> : <><p>A shorter-window retry is available for comparison. It has not replaced the original recognition.</p><p className="whitespace-pre-wrap leading-6">{metadata.alternative.text}</p></>}</div>}</details>}
        </article>;
      })}
      {hasMore && <Button variant="outline" className="w-full" disabled={isLoadingMore} onClick={onLoadMore}>{isLoadingMore ? 'Loading…' : 'Load more transcript'}</Button>}
    </div>
    {!playback && <footer className="shrink-0 border-t border-[var(--xx-border)] px-4 py-3 space-y-2 bg-[var(--xx-paper)]">
      <div className="flex items-center justify-between gap-3 text-[11px] text-[var(--xx-muted)]"><span className="flex items-center gap-2"><Headphones size={13} aria-hidden="true" /> Recording</span><span className="inline-flex items-center gap-1" title="Times identify audio windows, not individual words. Track labels do not identify people. Original recognition script is preserved."><Info size={12} aria-hidden="true" /> Window timestamps</span></div>
      {audioPath ? <audio ref={audio} key={audioPath} controls preload="metadata" src={convertFileSrc(audioPath)} className="w-full h-10" onError={() => setAudioError('The saved audio could not be played. Open its recording folder to inspect it.')} /> : <p className="text-xs text-gray-500">No playable recording is linked to this meeting.</p>}
      {audioError && <p role="alert" className="text-xs text-amber-700">{audioError}</p>}
    </footer>}
  </section>;
}
