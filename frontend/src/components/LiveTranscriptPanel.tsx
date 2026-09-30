'use client';
import { AudioLines, Check, Mic, NotebookPen } from 'lucide-react';
import { useLocalWorkflow } from '@/contexts/LocalWorkflowContext';
import { useRecordingState } from '@/contexts/RecordingStateContext';
import { recordingTime } from '@/lib/transcript-workspace';
import { usePermissionCheck } from '@/hooks/usePermissionCheck';
import { PermissionWarning } from '@/components/PermissionWarning';

export function LiveTranscriptPanel() {
  const { state } = useLocalWorkflow();
  const { isRecording, isPaused } = useRecordingState();
  const permissions = usePermissionCheck();
  const run = state.runs.find(item => item.capture.session_id === state.currentSession && item.role === 'live-draft')
    ?? state.runs.find(item => item.capture.session_id === state.currentSession);
  const live = run?.preferences.timing === 'during-recording';
  const segments = [...(run?.job?.segments ?? [])].filter(segment => segment.text.trim()).sort((a, b) => (a.start_seconds ?? 0) - (b.start_seconds ?? 0) || a.source_track.localeCompare(b.source_track));
  return <section className="xx-live-panel custom-scrollbar" aria-label="Live transcript">
    {!segments.length ? <div className="xx-live-empty">
      <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#f6e2e9] text-[var(--xx-accent)]">{isRecording ? <AudioLines size={23} /> : <NotebookPen size={23} />}</div>
      <p className="xx-eyebrow mb-3">{isRecording ? isPaused ? 'Take your time' : 'Listening, locally' : 'Less typing. More listening.'}</p>
      <h2 className="xx-heading text-[clamp(30px,3vw,44px)] leading-[1.12]">{isRecording ? isPaused ? 'A little pause.' : 'You talk. I’m all ears.' : <>Be here for the<br />conversation.</>}</h2>
      <p className="mt-5 max-w-[460px] text-[15px] leading-7 text-[var(--xx-muted)]">{isRecording ? isPaused ? 'Resume whenever you’re ready. Everything captured so far is saved.' : live ? 'Audio is being saved. Apex will add a draft as it catches up; Trelis prepares the final transcript after the call.' : 'Audio is being saved. Your transcript will be prepared when you stop recording.' : 'A quiet place for your calls, transcripts and notes. An Apex draft while you talk, then a Trelis transcript to come back to.'}</p>
      {!isRecording && <div className="mt-7 flex flex-wrap gap-x-5 gap-y-2 text-xs text-[var(--xx-muted)]"><span className="flex items-center gap-1.5"><Mic size={13} />Record on your laptop</span><span className="flex items-center gap-1.5"><Check size={13} />Review at your pace</span></div>}
      {!isRecording && !permissions.isChecking && <div className="mt-5"><PermissionWarning hasMicrophone={permissions.hasMicrophone} hasSystemAudio={permissions.hasSystemAudio} onRecheck={permissions.checkPermissions} isRechecking={permissions.isChecking} /></div>}
    </div> : <div className="mx-auto max-w-3xl">
      <header className="mb-5"><p className="xx-eyebrow">{isPaused ? 'Paused' : isRecording ? 'Recording in progress' : 'Transcript preview'}</p><h2 className="xx-heading text-3xl mt-2">The conversation so far</h2><p className="mt-2 text-xs text-[var(--xx-muted)]">Apex live draft · buffered updates · Trelis final after the call</p></header>
      {segments.map(segment => <article className="xx-live-row" key={segment.id}><div className="space-y-2 text-[11px] text-[var(--xx-muted)]"><span className="font-mono">{recordingTime(segment.start_seconds)}</span><span className="block">{segment.source_track}</span></div><div><p className="whitespace-pre-wrap break-words text-base leading-8">{segment.text}</p>{!!segment.quality_flags?.length && <span className="mt-2 inline-block text-xs text-amber-800">Review suggested</span>}</div></article>)}
      <p className="mt-5 text-xs leading-5 text-[var(--xx-muted)]">Times mark audio windows. Microphone and system labels identify tracks; individual speaker suggestions are prepared after the call.</p>
    </div>}
  </section>;
}
