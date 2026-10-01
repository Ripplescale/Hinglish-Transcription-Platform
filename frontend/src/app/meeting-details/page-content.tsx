'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, NotebookPen, Settings2 } from 'lucide-react';
import { Popover, PopoverTrigger } from '@/components/ui/popover';
import { Portal as ToolsPortal, Content as ToolsContent } from '@radix-ui/react-popover';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import type { MeetingSummary, SummaryProcessResponse } from '@/types';
import { MeetingDetailsSplitView, type MeetingDetailsTab } from '@/components/MeetingDetails/MeetingDetailsSplitView';
import { TranscriptWorkspacePanel } from '@/components/MeetingDetails/TranscriptWorkspacePanel';
import { BlockNoteSummaryView } from '@/components/AISummary/BlockNoteSummaryView';
import { useMeetingData } from '@/hooks/meeting-details/useMeetingData';
import { useTranscriptWorkspace } from '@/hooks/meeting-details/useTranscriptWorkspace';
import { hasVisibleSummaryContent } from '@/lib/summary-content';
import { Button } from '@/components/ui/button';
import { ProjectVaultPanel } from '@/components/ProjectVaultPanel';
import { LocalTranscriptionPanel } from '@/components/LocalTranscriptionPanel';
import { SpeakerReviewPanel } from '@/components/MeetingDetails/SpeakerReviewPanel';

export default function PageContent({ meeting, summaryData, onMeetingUpdated, hasMore, isLoadingMore, totalCount, onLoadMore }: {
  meeting: any;
  summaryData: MeetingSummary | null;
  initialSummary?: SummaryProcessResponse | null;
  shouldAutoGenerate?: boolean;
  onAutoGenerateComplete?: () => void;
  onMeetingUpdated?: () => Promise<void>;
  onRefetchTranscripts?: () => Promise<void>;
  segments?: any[];
  hasMore?: boolean;
  isLoadingMore?: boolean;
  totalCount?: number;
  loadedCount?: number;
  onLoadMore?: () => void;
}) {
  const [activeTab, setActiveTab] = useState<MeetingDetailsTab>('transcript');
  const [correctionDraft, setCorrectionDraft] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<{ href: string; historyDelta?: number } | null>(null);
  const router = useRouter();
  const review = useTranscriptWorkspace(meeting.id);
  const draftNotes = 'workflow_role' in review.workspace && review.workspace.workflow_role === 'live-draft';
  const fallbackNotes = review.workspace.workflow_role === 'fallback';
  const legacy = useMeetingData({ meeting, summaryData, onMeetingUpdated });
  const save = async () => {
    try { await review.save(); toast.success('Notes and corrections saved'); }
    catch { /* The persistent error retains unsaved edits in the workspace. */ }
  };
  const saveCurrent = useRef(save);
  saveCurrent.current = save;
  const saveEnabled = useRef(false);
  saveEnabled.current = review.loaded && review.dirty && !review.saving;
  const needsSave = useRef(false);
  needsSave.current = review.dirty || review.saving || correctionDraft;
  const allowHistory = useRef(false);
  const pageIndex = useRef<number>();
  useEffect(() => {
    // Next can mount a restored page before committing its URL to history.
    if (window.location.pathname === '/meeting-details' && new URLSearchParams(window.location.search).get('id') === meeting.id) {
      pageIndex.current = (window as unknown as { navigation?: { currentEntry?: { index?: number } } }).navigation?.currentEntry?.index;
    }
  });
  useEffect(() => {
    const currentHref = new URL(`/meeting-details?id=${encodeURIComponent(meeting.id)}`, window.location.origin).href;
    const navIndex = () => (window as unknown as { navigation?: { currentEntry?: { index?: number } } }).navigation?.currentEntry?.index;
    const navigation = (window as unknown as { navigation?: EventTarget }).navigation;
    let restoring = false;
    const beforeTraverse = (event: Event) => {
      const transition = event as Event & { navigationType: string; canIntercept: boolean; destination: { url: string; index: number; sameDocument: boolean } };
      if (transition.navigationType !== 'traverse') return;
      if (allowHistory.current) { allowHistory.current = false; return; }
      if (!needsSave.current || !transition.cancelable || !transition.canIntercept || !transition.destination.sameDocument || new URL(transition.destination.url).origin !== window.location.origin || transition.destination.url === currentHref) return;
      const delta = transition.destination.index - (navIndex() ?? pageIndex.current ?? transition.destination.index + 1);
      // Cancel before the router receives popstate, so the editor never unmounts.
      transition.preventDefault();
      setPendingNavigation({ href: transition.destination.url, historyDelta: delta || -1 });
    };
    const beforeNavigation = (event: Event) => {
      const href = (event as CustomEvent<{ href: string }>).detail?.href;
      if (!needsSave.current || !href || new URL(href, currentHref).href === currentHref) return;
      event.preventDefault(); setPendingNavigation({ href });
    };
    const beforeLink = (event: MouseEvent) => {
      if (!needsSave.current || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = (event.target as HTMLElement)?.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target === '_blank' || link.download || link.origin !== window.location.origin || link.href === currentHref) return;
      event.preventDefault(); event.stopPropagation(); setPendingNavigation({ href: link.href });
    };
    const beforeHistory = (event: PopStateEvent) => {
      if (restoring) { event.stopImmediatePropagation(); restoring = false; return; }
      if (allowHistory.current) { allowHistory.current = false; return; }
      if (!needsSave.current || window.location.href === currentHref) return;
      const href = window.location.href;
      const destinationIndex = navIndex();
      const calculated = pageIndex.current != null && destinationIndex != null ? destinationIndex - pageIndex.current : -1;
      const delta = calculated || -1;
      // Restore this entry before displaying a dialog; keep Next's route and edits intact.
      event.stopImmediatePropagation(); restoring = true;
      window.history.go(-delta);
      setPendingNavigation({ href, historyDelta: delta });
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!needsSave.current) return;
      event.preventDefault(); event.returnValue = '';
    };
    window.addEventListener('xx-before-navigate', beforeNavigation);
    document.addEventListener('click', beforeLink, true);
    if (navigation) navigation.addEventListener('navigate', beforeTraverse);
    else window.addEventListener('popstate', beforeHistory, true);
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      window.removeEventListener('xx-before-navigate', beforeNavigation);
      document.removeEventListener('click', beforeLink, true);
      if (navigation) navigation.removeEventListener('navigate', beforeTraverse);
      else window.removeEventListener('popstate', beforeHistory, true);
      window.removeEventListener('beforeunload', beforeUnload);
    };
  }, [meeting.id]);
  useEffect(() => {
    const keyboardSave = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
      // A reference has a separate save action and must not appear saved by Ctrl+S.
      event.preventDefault();
      const separate = (event.target as HTMLElement)?.closest('[data-separate-save]');
      if (separate) { toast.info(separate.getAttribute('data-separate-save') || 'Use Save reference to save this reference.'); return; }
      if (saveEnabled.current) void saveCurrent.current();
    };
    window.addEventListener('keydown', keyboardSave);
    return () => window.removeEventListener('keydown', keyboardSave);
  }, [meeting.id]);
  return <div className="flex h-full min-h-0 min-w-0 flex-col p-4 pl-1">
    <MeetingDetailsSplitView activeTab={activeTab} onTabChange={setActiveTab}
      transcript={<TranscriptWorkspacePanel meeting={meeting} review={review} hasMore={hasMore} isLoadingMore={isLoadingMore} totalCount={totalCount} onLoadMore={onLoadMore} onDraftChange={setCorrectionDraft} />}
      summary={<section className="flex h-full min-h-0 min-w-0 flex-col bg-[var(--xx-paper)] text-[var(--xx-ink)]" aria-label="Meeting notes">
        <header className="shrink-0 px-5 pt-5 pb-4 border-b border-[var(--xx-border)] space-y-4">
          <div className="flex items-center justify-between gap-2"><h2 className="xx-eyebrow flex items-center gap-2"><NotebookPen size={13} aria-hidden="true" />{fallbackNotes ? 'Notes for this fallback' : draftNotes ? 'Notes for this draft' : 'My notes'}</h2>
            <Popover><PopoverTrigger asChild><button type="button" className="xx-button-secondary !min-h-8 !px-2 text-xs"><Settings2 size={13} aria-hidden="true" />Tools<ChevronDown size={12} aria-hidden="true" /></button></PopoverTrigger>
              <ToolsPortal forceMount><ToolsContent forceMount align="end" sideOffset={10} aria-label="Meeting tools" className="data-[state=closed]:hidden z-50 border shadow-lg outline-none w-[380px] max-h-[min(680px,calc(100vh-120px))] overflow-y-auto rounded-2xl border-[var(--xx-border)] bg-[var(--xx-paper)] p-4 text-[var(--xx-ink)] space-y-3">
                <div><h3 className="font-medium text-sm">Meeting tools</h3><p className="mt-1 text-xs text-[var(--xx-muted)]">Fine-tune this meeting when you need to.</p></div>
                <details className="rounded-xl border border-[var(--xx-border)] p-3"><summary className="cursor-pointer text-sm font-medium">Transcription &amp; versions</summary><div className="mt-3"><LocalTranscriptionPanel sessionDir={meeting.folder_path} meetingId={meeting.id} projectId={review.workspace.project_id} /></div></details>
                <SpeakerReviewPanel meetingId={meeting.id} review={review} />
                <ProjectVaultPanel selectedProject={review.workspace.project_id} onSelectProject={review.updateProject} disabled={!review.loaded || review.saving} />
                {hasVisibleSummaryContent(legacy.aiSummary) && <details className="rounded-xl border border-[var(--xx-border)] p-3"><summary className="cursor-pointer text-sm font-medium">Previously saved summary</summary><p className="text-xs text-[var(--xx-muted)] my-3">Preserved from the earlier workflow.</p><BlockNoteSummaryView ref={legacy.blockNoteSummaryRef} summaryData={legacy.aiSummary} onSave={legacy.handleSaveSummary} onSummaryChange={legacy.handleSummaryChange} onDirtyChange={legacy.setIsSummaryDirty} status="idle" meeting={{ id: meeting.id, title: meeting.title, created_at: meeting.created_at }} /><Button size="sm" variant="outline" className="mt-3" disabled={!legacy.isSummaryDirty || legacy.isSaving} onClick={() => void legacy.saveAllChanges()}>{legacy.isSaving ? 'Saving…' : 'Save earlier summary changes'}</Button></details>}
              </ToolsContent></ToolsPortal>
            </Popover>
          </div>
          <div className="flex items-center justify-between gap-2"><span role="status" className="text-[11px] text-[var(--xx-muted)] flex items-center gap-1.5">{!review.loaded ? 'Loading notes…' : review.saving ? 'Saving…' : review.dirty ? <><span className="h-1.5 w-1.5 rounded-full bg-[var(--xx-accent)]" />Unsaved changes</> : <><Check size={12} aria-hidden="true" />Saved on this device</>}</span><button type="button" className="xx-button-primary !min-h-8 !px-3 text-xs" title="Save notes and corrections (Ctrl+S)" disabled={!review.loaded || review.saving || !review.dirty} onClick={() => void save()}>{review.saving ? 'Saving…' : 'Save changes'}</button></div>
        </header>
        {review.error && <p role="alert" className="mx-5 mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">{review.error}</p>}
        <textarea aria-label="Meeting notes" placeholder={'A little space for your thoughts.\n\nJot down ideas, decisions or next steps — or paste your refined summary here.'} className="flex-1 min-h-0 w-full resize-none border-0 bg-transparent p-5 text-[15px] leading-[1.9] placeholder:text-[var(--xx-muted)] focus:outline-none focus-visible:shadow-[inset_0_0_0_2px_var(--xx-border)]" disabled={!review.loaded} value={review.workspace.notes} onChange={event => review.updateNotes(event.target.value)} />
        <footer className="shrink-0 px-5 py-3 border-t border-[var(--xx-border)] text-[11px] text-[var(--xx-muted)] flex items-center justify-between gap-2"><span>{draftNotes || fallbackNotes ? 'Notes and corrections stay with this version.' : 'Your notes, in your words.'}</span><kbd className="shrink-0 rounded border border-[var(--xx-border)] px-1.5 py-0.5 text-[10px]">Ctrl S to save</kbd></footer>
      </section>} />
    <Dialog open={pendingNavigation !== null} onOpenChange={open => { if (!open) setPendingNavigation(null); }}><DialogContent className="rounded-2xl border-[var(--xx-border)] bg-[var(--xx-paper)] text-[var(--xx-ink)] max-w-sm"><DialogHeader><DialogTitle>Keep your changes?</DialogTitle><DialogDescription className="text-[var(--xx-muted)]">This meeting has unsaved notes or corrections. Stay here to save them before leaving.</DialogDescription></DialogHeader><DialogFooter className="gap-2"><button className="xx-button-secondary text-sm" onClick={() => {
      const next = pendingNavigation; setPendingNavigation(null);
      if (!next) return;
      needsSave.current = false;
      if (next.historyDelta != null) { allowHistory.current = true; window.history.go(next.historyDelta); }
      else router.push(next.href);
    }}>Leave without saving</button><button className="xx-button-primary text-sm" onClick={() => setPendingNavigation(null)}>Stay here</button></DialogFooter></DialogContent></Dialog>
  </div>;
}
