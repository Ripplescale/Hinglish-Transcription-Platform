'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

const STORAGE_KEY = 'meetily.meetingDetails.transcriptPaneRatio';
const DEFAULT_RATIO = 0.59;
export type MeetingDetailsTab = 'transcript' | 'summary';

/** Desktop split; neither editor can be resized below a usable laptop width. */
export function MeetingDetailsSplitView({ transcript, summary }: {
  transcript: ReactNode;
  summary: ReactNode;
  activeTab: MeetingDetailsTab;
  onTabChange: (tab: MeetingDetailsTab) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [ratio, setRatio] = useState(DEFAULT_RATIO);
  const [width, setWidth] = useState(1000);
  const dragging = useRef(false);
  const minimum = Math.min(0.5, 340 / width);
  const maximum = Math.max(0.5, 1 - 300 / width);
  const clamp = useCallback((value: number) => Math.min(maximum, Math.max(minimum, value)), [minimum, maximum]);
  const actualRatio = clamp(ratio);

  useEffect(() => {
    try {
      const stored = Number(localStorage.getItem(STORAGE_KEY));
      if (stored >= 0.3 && stored <= 0.75) setRatio(stored);
    } catch { /* Layout persistence is optional. */ }
    if (!containerRef.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(1, entries[0].contentRect.width)));
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const persist = (value: number) => {
    try { localStorage.setItem(STORAGE_KEY, String(value)); } catch { /* Optional. */ }
  };
  const finishDrag = () => {
    if (!dragging.current) return;
    dragging.current = false;
    persist(actualRatio);
  };

  return <div ref={containerRef} className="xx-paper flex flex-1 min-h-0 min-w-0 overflow-hidden rounded-2xl"
    onPointerMove={event => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      setRatio(clamp((event.clientX - rect.left) / rect.width));
    }} onPointerUp={finishDrag} onPointerCancel={finishDrag}>
    <div role="region" aria-label="Transcript" className="flex min-h-0 min-w-0 flex-col overflow-hidden" style={{ width: `calc(${actualRatio * 100}% - 4px)` }}>{transcript}</div>
    <div role="separator" aria-orientation="vertical" aria-label="Resize transcript and notes" aria-valuenow={Math.round(actualRatio * 100)} aria-valuemin={Math.round(minimum * 100)} aria-valuemax={Math.round(maximum * 100)} aria-valuetext={`Transcript panel ${Math.round(actualRatio * 100)} percent`} tabIndex={0}
      className="group relative z-10 flex w-2 shrink-0 cursor-col-resize items-center justify-center bg-[var(--xx-paper)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--xx-accent)]"
      onPointerDown={event => { event.preventDefault(); dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
      onKeyDown={event => {
        const next = event.key === 'ArrowLeft' ? clamp(actualRatio - 0.05) : event.key === 'ArrowRight' ? clamp(actualRatio + 0.05) : event.key === 'Home' ? minimum : event.key === 'End' ? maximum : null;
        if (next === null) return;
        event.preventDefault(); setRatio(next); persist(next);
      }}>
      <span className="absolute inset-y-0 w-px bg-[var(--xx-border)]" />
      <span className="relative h-10 w-1 rounded-full bg-[var(--xx-border)] transition-colors group-hover:bg-[var(--xx-accent)] group-focus-visible:bg-[var(--xx-accent)]" />
    </div>
    <div role="region" aria-label="Notes" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{summary}</div>
  </div>;
}
