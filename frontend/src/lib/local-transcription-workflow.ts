/** Coordinates durable native jobs; capture and model inference stay outside this class. */
export type WorkflowRole = 'live-draft' | 'final';
export interface LocalProfile { id: string; model: string; chunk_seconds: number; available: boolean; reason?: string; live_qualified: boolean }
export interface LocalSegment { id: string; text: string; source_track: string; start_seconds?: number; end_seconds?: number; quality_flags?: string[] }
export interface LocalJob { job_id: string; session_dir: string; profile: string; state: string; workflow_role?: WorkflowRole; capture_session_id?: string; language_mode?: 'hinglish' | 'english'; processed_audio_seconds?: number; available_audio_seconds?: number; backlog_seconds?: number; segments?: LocalSegment[]; error?: string }
export interface LocalPreferences { profile: 'trelis-20' | 'apex-20'; languageMode: 'hinglish' | 'english'; timing: 'after-recording' | 'during-recording' }
export interface SpeakerJob { job_id: string; state: string; error?: string; result?: unknown }
export interface SpeakerSetup { available: boolean; enabled?: boolean; reason?: string }
export interface CaptureSession { session_id: string; session_dir: string }
export interface WorkflowRun {
  capture: CaptureSession; meetingId?: string; transcriptMeetingId?: string; preferences: LocalPreferences;
  recording: boolean; job?: LocalJob; error?: string; importedCount?: number; importedState?: string;
  primary: boolean; role?: WorkflowRole; speakerJob?: SpeakerJob; speakerError?: string; needsRecovery?: boolean;
  draftSkipped?: boolean; stopRequested?: boolean; stopAttempted?: boolean; stopError?: string;
  pausedByUser?: boolean; resumeRequested?: boolean; captureEnded?: boolean; stopNotified?: boolean;
}
export interface WorkflowState { preferences: LocalPreferences; runs: WorkflowRun[]; currentSession?: string; speakerSetup?: SpeakerSetup }
export const DEFAULT_LOCAL_PREFERENCES: LocalPreferences = { profile: 'apex-20', languageMode: 'hinglish', timing: 'during-recording' };
export const isTerminalJob = (state: string) => ['complete', 'failed', 'stopped'].includes(state);
export const pathKey = (value: string) => value.replace(/^\\\\\?\\/, '').replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();
/** Global settings describe new recordings. Existing run settings are never migrated. */
export function readPreferences(value: unknown): LocalPreferences {
  const source = value && typeof value === 'object' ? value as Partial<LocalPreferences> : {};
  return { ...DEFAULT_LOCAL_PREFERENCES, languageMode: source.languageMode === 'english' ? 'english' : 'hinglish' };
}
function jobPreferences(job: LocalJob, languageMode: LocalPreferences['languageMode']): LocalPreferences {
  return { profile: job.profile === 'trelis-20' ? 'trelis-20' : 'apex-20', languageMode: job.language_mode ?? languageMode,
    timing: job.workflow_role === 'live-draft' ? 'during-recording' : 'after-recording' };
}
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
interface Dependencies { invoke: Invoke; changed: (state: WorkflowState) => void; imported: (meetingId: string) => void; stopped: (meetingId?: string, error?: string) => void }

export class LocalWorkflow {
  state: WorkflowState;
  private locks = new Map<string, Promise<void>>();
  private ticking = false;
  private startInFlight = false;
  private startingRun?: WorkflowRun;
  private notifiedStopped = new Set<string>();
  constructor(private dependencies: Dependencies, previous?: Partial<WorkflowState>) {
    this.state = { preferences: readPreferences(previous?.preferences), runs: Array.isArray(previous?.runs) ? previous.runs : [], currentSession: previous?.currentSession };
  }
  private changed() { this.state = { ...this.state, runs: [...this.state.runs] }; this.dependencies.changed(this.state); }
  setPreferences(preferences: LocalPreferences) { this.state.preferences = readPreferences(preferences); this.changed(); }
  private async serialize(key: string, operation: () => Promise<void>) {
    const prior = this.locks.get(key) ?? Promise.resolve();
    const pending = prior.catch(() => {}).then(operation);
    this.locks.set(key, pending);
    try { await pending; } finally { if (this.locks.get(key) === pending) this.locks.delete(key); }
  }
  private sameCapture(run: WorkflowRun, capture: CaptureSession) {
    return run.capture.session_id === capture.session_id || (!!run.role && pathKey(run.capture.session_dir) === pathKey(capture.session_dir));
  }
  private legacy(capture: CaptureSession) {
    if (this.state.runs.some(run => run.role && this.sameCapture(run, capture))) return undefined;
    return this.state.runs.find(run => !run.role && run.capture.session_id === capture.session_id);
  }
  private pair(capture: CaptureSession, recording: boolean): [WorkflowRun, WorkflowRun] {
    const existing = this.state.runs.filter(run => run.role && this.sameCapture(run, capture));
    const languageMode = existing[0]?.preferences.languageMode ?? this.state.preferences.languageMode;
    const ended = existing.some(run => run.captureEnded) || !recording;
    let draft = existing.find(run => run.role === 'live-draft');
    let final = existing.find(run => run.role === 'final');
    if (!draft) {
      draft = { capture, role: 'live-draft', primary: false, recording: !ended,
        preferences: { profile: 'apex-20', timing: 'during-recording', languageMode }, captureEnded: ended };
      this.state.runs.push(draft);
    }
    if (!final) {
      final = { capture, role: 'final', primary: true, recording: !ended,
        preferences: { profile: 'trelis-20', timing: 'after-recording', languageMode }, captureEnded: ended };
      this.state.runs.push(final);
    }
    const meetingId = final.meetingId ?? draft.meetingId;
    if (meetingId) { draft.meetingId = meetingId; final.meetingId = meetingId; }
    return [draft, final];
  }
  private async ensurePairMeeting(draft: WorkflowRun, final: WorkflowRun) {
    await this.ensureMeeting(draft);
    if (draft.meetingId) {
      final.meetingId = draft.meetingId;
      if (final.error?.startsWith('Audio is saved, but its meeting could not be linked.')) final.error = final.stopError;
      this.changed();
    }
  }
  async captureStarted(capture: CaptureSession) {
    await this.serialize(capture.session_id, async () => {
      const legacy = this.legacy(capture);
      if (legacy) {
        legacy.recording = true; this.state.currentSession = capture.session_id; this.changed();
        await this.ensureMeeting(legacy);
        return;
      }
      const [draft, final] = this.pair(capture, true);
      this.state.currentSession = capture.session_id;
      // A delayed duplicate start event cannot reopen an already ended capture.
      if (!draft.captureEnded && !final.captureEnded) { draft.recording = true; final.recording = true; }
      this.changed();
      await this.ensurePairMeeting(draft, final);
    });
    await this.tick();
  }
  async captureStopped(capture: CaptureSession) {
    await this.serialize(capture.session_id, async () => {
      const legacy = this.legacy(capture);
      if (legacy) {
        if (legacy.needsRecovery) legacy.error = legacy.stopError;
        legacy.recording = false; legacy.needsRecovery = false; this.state.currentSession = capture.session_id;
        await this.ensureMeeting(legacy);
        this.notifyStopped(legacy, legacy.transcriptMeetingId ?? legacy.meetingId);
        this.changed();
        return;
      }
      const [draft, final] = this.pair(capture, false);
      for (const run of [draft, final]) {
        if (run.needsRecovery) run.error = run.stopError;
        run.recording = false; run.captureEnded = true; run.needsRecovery = false;
      }
      draft.resumeRequested = false;
      if (!draft.job && this.startingRun !== draft) draft.draftSkipped = true;
      this.state.currentSession = capture.session_id;
      this.changed();
      await this.ensurePairMeeting(draft, final);
      this.notifyStopped(final, final.meetingId);
      if (draft.job && !isTerminalJob(draft.job.state)) await this.requestStop(draft);
    });
    await this.tick();
  }
  private notifyStopped(run: WorkflowRun, meetingId?: string) {
    const id = run.capture.session_id;
    if (!run.stopNotified && !this.notifiedStopped.has(id)) {
      run.stopNotified = true; this.notifiedStopped.add(id); this.changed();
      this.dependencies.stopped(meetingId, run.error);
    }
  }
  private async ensureMeeting(run: WorkflowRun) {
    if (run.meetingId) return;
    try {
      const result = await this.dependencies.invoke<{ meeting_id: string }>('ensure_capture_meeting', { sessionDir: run.capture.session_dir });
      run.meetingId = result.meeting_id; run.error = run.stopError; this.changed();
    } catch (error) { run.error = `Audio is saved, but its meeting could not be linked. ${String(error)}`; this.changed(); }
  }
  private activeWorker() {
    return this.state.runs.some(run => (run.job && !isTerminalJob(run.job.state)) || (run.speakerJob && !isTerminalJob(run.speakerJob.state)));
  }
  private draftSettled(run: WorkflowRun) {
    const draft = this.state.runs.find(item => item.role === 'live-draft' && this.sameCapture(item, run.capture));
    if (!draft) return true; // A durably adopted Final does not invent a missing Draft.
    if (this.startingRun === draft) return false;
    if (!draft.job) return !!draft.draftSkipped;
    return isTerminalJob(draft.job.state) && Array.isArray(draft.job.segments)
      && draft.importedCount === draft.job.segments.length && draft.importedState === draft.job.state;
  }
  private eligible(run: WorkflowRun) {
    if (run.pausedByUser || run.needsRecovery || run.error || !run.meetingId) return false;
    if (run.role === 'live-draft' && (!run.recording || run.captureEnded || run.draftSkipped)) return false;
    if (run.role === 'final' && (run.recording || !run.captureEnded || !this.draftSettled(run))) return false;
    if (!run.role && run.recording && run.preferences.timing !== 'during-recording') return false;
    return !run.job || (!!run.resumeRequested && ['failed', 'stopped'].includes(run.job.state));
  }
  private async start(run: WorkflowRun) {
    if (run.job || this.startInFlight || this.activeWorker() || !this.eligible(run)) return;
    this.startInFlight = true; this.startingRun = run;
    try {
      const args: Record<string, unknown> = { sessionDir: run.capture.session_dir, profile: run.preferences.profile, languageMode: run.preferences.languageMode, projectId: null };
      if (run.role) args.workflowRole = run.role;
      const job = await this.dependencies.invoke<LocalJob>('start_local_transcription', args);
      run.job = { ...job, session_dir: run.capture.session_dir, profile: run.preferences.profile, ...(run.role ? { workflow_role: run.role } : {}) };
      run.error = undefined;
    } catch (error) {
      run.error = `Transcription did not start. Audio remains saved. ${String(error)}`;
      if (run.role === 'live-draft' && !run.recording) run.draftSkipped = true;
    } finally { this.startInFlight = false; this.startingRun = undefined; this.changed(); }
  }
  private async resume(run: WorkflowRun) {
    if (!run.job || this.startInFlight || this.activeWorker() || !this.eligible(run)) return;
    this.startInFlight = true; run.resumeRequested = false;
    try {
      const next = await this.dependencies.invoke<Partial<LocalJob>>('resume_local_transcription', { jobId: run.job.job_id });
      run.job = { ...run.job, ...next }; run.stopRequested = false; run.stopAttempted = false; run.stopError = undefined;
    } catch (error) { run.error = String(error); }
    finally { this.startInFlight = false; this.changed(); }
  }
  private async requestStop(run: WorkflowRun) {
    if (!run.job || isTerminalJob(run.job.state) || run.stopAttempted) return;
    run.stopAttempted = true;
    try {
      await this.dependencies.invoke('stop_local_transcription', { jobId: run.job.job_id });
      run.stopRequested = true; run.stopError = undefined;
    } catch (error) { run.stopError = `Could not request the worker to stop. Audio remains saved; retry the handoff. ${String(error)}`; run.error = run.stopError; }
    this.changed();
  }
  async retry(run: WorkflowRun) {
    run.pausedByUser = false; run.error = undefined; run.stopError = undefined; run.stopAttempted = false;
    await this.ensureMeeting(run);
    if (run.role === 'live-draft' && !run.recording) {
      // Post-call retry repairs import/handoff, never restarts an obsolete preview.
      run.resumeRequested = false;
      if (!run.job) run.draftSkipped = true;
      else if (!isTerminalJob(run.job.state)) await this.requestStop(run);
      run.importedState = undefined;
    } else if (run.job && ['failed', 'stopped'].includes(run.job.state)) {
      run.resumeRequested = true;
    } else if (run.job && run.stopRequested && !isTerminalJob(run.job.state)) {
      run.resumeRequested = true; // An explicit retry may wait for an acknowledged pause to finish.
    } else if (run.job) run.importedState = undefined;
    this.changed();
    await this.tick();
  }
  async stopWorker(run: WorkflowRun) {
    run.pausedByUser = true; run.resumeRequested = false;
    await this.requestStop(run);
    this.changed();
    await this.tick();
  }
  async adopt(job: LocalJob, meetingId: string, primary = false) {
    let run = this.state.runs.find(item => item.job?.job_id === job.job_id);
    if (!run && job.workflow_role) run = this.state.runs.find(item => item.role === job.workflow_role && pathKey(item.capture.session_dir) === pathKey(job.session_dir) && !item.job);
    if (!run) {
      run = { capture: { session_id: job.capture_session_id ?? `job-${job.job_id}`, session_dir: job.session_dir }, meetingId,
        recording: false, captureEnded: !!job.workflow_role, primary: job.workflow_role === 'final' ? true : primary,
        role: job.workflow_role, preferences: jobPreferences(job, this.state.preferences.languageMode), job };
      this.state.runs.push(run);
    } else if (!run.job) run.job = job;
    this.changed();
    await this.tick();
  }
  private async restoreNativeJobs(capture: CaptureSession) {
    // Missing browser pointers are not permission to replace a durable legacy
    // job or resume a paused/failed process. Discover identities first.
    const jobs = await this.dependencies.invoke<LocalJob[]>('list_local_transcription_jobs', { sessionDir: capture.session_dir });
    const matching = jobs.filter(job => pathKey(job.session_dir) === pathKey(capture.session_dir));
    if (!matching.length) return;
    const base = await this.dependencies.invoke<{ meeting_id: string }>('ensure_capture_meeting', { sessionDir: capture.session_dir });
    const hasRoles = matching.some(job => job.workflow_role === 'live-draft' || job.workflow_role === 'final');
    let legacyPrimary = false;
    for (const job of matching) {
      if (this.state.runs.some(run => run.job?.job_id === job.job_id)) continue;
      const role = job.workflow_role === 'live-draft' || job.workflow_role === 'final' ? job.workflow_role : undefined;
      const primary = role === 'final' || (!hasRoles && !legacyPrimary);
      if (!role && primary) legacyPrimary = true;
      this.state.runs.push({ capture, meetingId: base.meeting_id, role, primary,
        recording: !!role || primary, captureEnded: role ? false : undefined,
        preferences: jobPreferences(job, this.state.preferences.languageMode), job });
    }
    this.changed();
  }
  async refreshAfterReload(active: CaptureSession | null) {
    if (active && !this.state.runs.some(run => this.sameCapture(run, active))) await this.restoreNativeJobs(active);
    for (const run of this.state.runs) {
      if (run.recording && (!active || !this.sameCapture(run, active))) {
        run.needsRecovery = true;
        run.error = 'The recording was interrupted. Recover its saved audio before resuming transcription.';
      }
      run.recording = !!active && this.sameCapture(run, active) && !run.captureEnded;
    }
    if (active) await this.captureStarted(active);
    this.changed();
    await this.tick();
  }
  async checkSpeakerSetup() {
    try { const result = await this.dependencies.invoke<SpeakerSetup>('get_speaker_setup_status'); this.state.speakerSetup = result && typeof result.available === 'boolean' ? result : { available: false, reason: 'Speaker setup is not configured.' }; }
    catch (error) { this.state.speakerSetup = { available: false, reason: String(error) }; }
    this.changed();
  }
  async retrySpeakers(run: WorkflowRun) {
    if (run.role === 'live-draft' || run.pausedByUser || this.startInFlight || this.activeWorker()) return;
    this.startInFlight = true; run.speakerError = undefined;
    try {
      if (run.speakerJob) run.speakerJob = await this.dependencies.invoke<SpeakerJob>('retry_speaker_identification', { jobId: run.speakerJob.job_id });
      else if (run.job && run.transcriptMeetingId) run.speakerJob = await this.dependencies.invoke<SpeakerJob>('start_speaker_identification', { transcriptionJobId: run.job.job_id, meetingId: run.transcriptMeetingId });
    } catch (error) { run.speakerError = String(error); }
    finally { this.startInFlight = false; this.changed(); }
  }
  async tick() {
    if (this.ticking) return;
    this.ticking = true;
    try {
      for (const run of this.state.runs) {
        if (!run.meetingId) {
          const paired = run.role && this.state.runs.find(item => item !== run && item.role && this.sameCapture(item, run.capture) && item.meetingId);
          if (paired) run.meetingId = paired.meetingId;
          else await this.ensureMeeting(run);
        }
        if (run.job) {
          try {
            if (!run.job.segments || !isTerminalJob(run.job.state)) {
              const next = await this.dependencies.invoke<LocalJob>('get_local_transcription_status', { jobId: run.job.job_id });
              run.job = { ...run.job, ...next };
            }
            if (isTerminalJob(run.job.state)) run.stopError = undefined;
            const count = run.job.segments?.length ?? 0;
            if (run.meetingId && run.job.segments && (count !== (run.importedCount ?? -1) || run.job.state !== run.importedState)) {
              const result = await this.dependencies.invoke<{ meeting_id: string }>('import_local_transcription', { jobId: run.job.job_id, meetingId: run.meetingId, primary: run.primary });
              run.transcriptMeetingId = result.meeting_id; run.importedCount = count; run.importedState = run.job.state;
              run.error = run.needsRecovery ? 'The recording was interrupted. Recover its saved audio before resuming transcription.' : run.stopError;
              this.dependencies.imported(result.meeting_id);
            }
          } catch (error) { run.error = `Saved audio and completed chunks are retained. ${String(error)}`; }
        }
        if (run.role === 'live-draft' && !run.recording && run.captureEnded) {
          run.resumeRequested = false;
          if (!run.job && this.startingRun !== run) run.draftSkipped = true;
          else if (run.job && !isTerminalJob(run.job.state)) await this.requestStop(run);
        }
        if (run.speakerJob && !isTerminalJob(run.speakerJob.state)) {
          try {
            run.speakerJob = await this.dependencies.invoke<SpeakerJob>('get_speaker_job_status', { jobId: run.speakerJob.job_id });
            if (run.speakerJob.state === 'complete' && run.transcriptMeetingId) this.dependencies.imported(run.transcriptMeetingId);
          } catch (error) { run.speakerError = String(error); }
        }
      }
      const priority = (run: WorkflowRun) => run.role === 'final' ? 0 : run.role === 'live-draft' ? 2 : 1;
      const queued = this.state.runs.filter(run => this.eligible(run)).sort((a, b) => priority(a) - priority(b))[0];
      if (queued) { if (queued.job) await this.resume(queued); else await this.start(queued); }
      if (!queued && !this.activeWorker()) {
        if (!this.state.speakerSetup) await this.checkSpeakerSetup();
        if (this.state.speakerSetup?.available && this.state.speakerSetup.enabled !== false) {
          const next = this.state.runs.find(run => run.role !== 'live-draft' && !run.pausedByUser && run.job?.state === 'complete'
            && run.importedState === 'complete' && run.transcriptMeetingId && !run.speakerJob && !run.speakerError && !run.error);
          if (next) await this.retrySpeakers(next);
        }
      }
      this.changed();
    } finally { this.ticking = false; }
  }
}
