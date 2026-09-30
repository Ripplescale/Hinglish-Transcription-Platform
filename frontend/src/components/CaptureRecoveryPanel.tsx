'use client';
import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { useRouter } from 'next/navigation';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { Button } from '@/components/ui/button';
import { useLocalWorkflow } from '@/contexts/LocalWorkflowContext';

interface Capture { session_id: string; session_dir: string; meeting_name: string; created_at: string }
interface Recovered { capture: { session_id: string; session_dir: string }; verification: { verified: boolean }; meeting_name: string }
export function CaptureRecoveryPanel() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recovered, setRecovered] = useState<Recovered | null>(null);
  const router = useRouter();
  const { refetchMeetings } = useSidebar();
  const { workflow } = useLocalWorkflow();
  useEffect(() => {
    let cancelled = false;
    invoke<Capture[]>('list_recoverable_captures').then(result => { if (!cancelled) setCaptures(result); }).catch(reason => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, []);
  const recover = async (capture: Capture) => {
    setBusy(capture.session_id); setError(null);
    try {
      const result = recovered?.capture.session_dir === capture.session_dir ? recovered : await invoke<Recovered>('recover_local_capture', { sessionDir: capture.session_dir });
      if (!result.verification.verified) throw new Error('Recovered audio did not pass verification. Source chunks remain saved.');
      setRecovered(result);
      const meeting = await invoke<{ meeting_id: string }>('ensure_capture_meeting', { sessionDir: result.capture.session_dir });
      await workflow.captureStopped(result.capture);
      const run = workflow.state.runs.find(item => item.capture.session_id === result.capture.session_id && item.role !== 'live-draft');
      if (run?.job && ['stopped', 'failed'].includes(run.job.state)) await workflow.retry(run);
      await refetchMeetings();
      router.push(`/meeting-details?id=${encodeURIComponent(meeting.meeting_id)}`);
    } catch (reason) { setError(`Recovery did not finish. ${String(reason)}`); }
    finally { setBusy(null); }
  };
  if (!captures.length && !error) return null;
  return <details className="rounded-lg border bg-white p-3"><summary className="text-sm font-medium cursor-pointer">Saved audio recovery ({captures.length})</summary><div className="mt-3 space-y-3"><p className="text-xs text-gray-600">Rebuild audio from committed source chunks after an interrupted recording. Transcription can run afterward.</p>{error && <p role="alert" className="text-xs text-red-700">{error}</p>}{captures.map(capture => <div key={capture.session_id} className="flex flex-wrap items-center gap-2"><span className="text-sm flex-1">{capture.meeting_name}</span><Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void recover(capture)}>{busy === capture.session_id ? 'Recovering…' : 'Recover saved audio'}</Button></div>)}</div></details>;
}
