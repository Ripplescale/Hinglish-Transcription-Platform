'use client';

import { useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { toast } from 'sonner';

/** Reuse the existing record/navigation path, including its device and race guards. */
export function MeetingDetectionBridge({ ready }: { ready: boolean }) {
  const { handleRecordingToggle } = useSidebar();
  const start = useRef(handleRecordingToggle);
  start.current = handleRecordingToggle;
  useEffect(() => {
    if (!ready) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    const consume = async () => {
      try {
        if (disposed) return;
        if (await invoke<boolean>('meeting_detection_take_request')) {
          if (!disposed) start.current();
        }
      } catch (error) { if (!disposed) toast.error('Could not start from the call prompt', { description: String(error) }); }
    };
    void (async () => {
      try {
        unlisten = await listen('meeting-record-requested', consume);
        if (disposed) { unlisten(); return; }
        await invoke('meeting_detection_ready', { ready: true });
        if (disposed) { await invoke('meeting_detection_ready', { ready: false }); return; }
        await consume();
      } catch (error) { console.error('Meeting detection unavailable:', error); }
    })();
    return () => {
      disposed = true;
      unlisten?.();
      void invoke('meeting_detection_ready', { ready: false }).catch(() => {});
    };
  }, [ready]);
  return null;
}
