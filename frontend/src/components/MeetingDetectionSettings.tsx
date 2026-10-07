'use client';
import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';

interface Status { enabled: boolean; supported: boolean; error: string | null }
export function MeetingDetectionSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { void invoke<Status>('meeting_detection_status').then(setStatus).catch(() => {}); }, []);
  if (!status?.supported) return null;
  async function change(enabled: boolean) {
    setSaving(true);
    try { await invoke('set_meeting_detection', { enabled }); setStatus(previous => previous && { ...previous, enabled }); }
    catch (error) { toast.error('Could not save call detection preference', { description: String(error) }); }
    finally { setSaving(false); }
  }
  return <div className="rounded-lg border bg-white p-4">
    <div className="flex items-center justify-between gap-4">
      <div><label htmlFor="meeting-detection" className="font-medium">Zoom &amp; Teams call prompts</label>
        <p className="text-sm text-gray-600">Show a small prompt when a desktop call uses your microphone. You choose when to record.</p></div>
      <Switch id="meeting-detection" checked={status.enabled} disabled={saving} onCheckedChange={change} />
    </div>
    <p className="mt-2 text-xs text-gray-500">Keep oats running in the tray. Browser calls and calls that never activate the microphone may need a manual start.</p>
    {status.error && <p role="status" className="mt-2 text-xs text-red-700">Detection is unavailable. You can still start recording manually.</p>}
  </div>;
}
