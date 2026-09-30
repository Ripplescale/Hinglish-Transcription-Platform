'use client';

import { useState, useEffect } from 'react';
import { RecordingControls } from '@/components/RecordingControls';
import { useSidebar } from '@/components/Sidebar/SidebarProvider';
import { usePermissionCheck } from '@/hooks/usePermissionCheck';
import { useRecordingState, RecordingStatus } from '@/contexts/RecordingStateContext';
import { useTranscripts } from '@/contexts/TranscriptContext';
import { useConfig } from '@/contexts/ConfigContext';
import { StatusOverlays } from '@/app/_components/StatusOverlays';
import Analytics from '@/lib/analytics';
import { SettingsModals } from './_components/SettingsModal';
import { TranscriptPanel } from './_components/TranscriptPanel';
import { useModalState } from '@/hooks/useModalState';
import { useRecordingStateSync } from '@/hooks/useRecordingStateSync';
import { useRecordingStart } from '@/hooks/useRecordingStart';
import { useRecordingStop } from '@/hooks/useRecordingStop';
import { useTranscriptRecovery } from '@/hooks/useTranscriptRecovery';
import { TranscriptRecovery } from '@/components/TranscriptRecovery';
import { indexedDBService } from '@/services/indexedDBService';
import { toast } from 'sonner';
import { useRouter } from 'next/navigation';
import { LocalTranscriptionPanel } from '@/components/LocalTranscriptionPanel';
import { CaptureRecoveryPanel } from '@/components/CaptureRecoveryPanel';
import { LiveTranscriptPanel } from '@/components/LiveTranscriptPanel';
import { FileText, SlidersHorizontal, ArrowUpRight, Headphones } from 'lucide-react';

export default function Home() {
  // Local page state (not moved to contexts)
  const [isRecording, setIsRecordingState] = useState(false);
  const barHeights = ['10px', '18px', '12px'];
  const [showRecoveryDialog, setShowRecoveryDialog] = useState(false);

  // Use contexts for state management
  const { meetingTitle } = useTranscripts();
  const { transcriptModelConfig, selectedDevices } = useConfig();
  const recordingState = useRecordingState();

  // Extract status from global state
  const { status, isStopping, isProcessing, isSaving } = recordingState;

  // Hooks
  const { hasMicrophone } = usePermissionCheck();
  const { setIsMeetingActive, isCollapsed: sidebarCollapsed, refetchMeetings, meetings, setCurrentMeeting } = useSidebar();
  const { modals, messages, showModal, hideModal } = useModalState(transcriptModelConfig);
  const { isRecordingDisabled, setIsRecordingDisabled } = useRecordingStateSync(isRecording, setIsRecordingState, setIsMeetingActive);
  const { handleRecordingStart } = useRecordingStart(isRecording, setIsRecordingState, showModal);

  // Get handleRecordingStop function and setIsStopping (state comes from global context)
  const { handleRecordingStop, setIsStopping } = useRecordingStop(
    setIsRecordingState,
    setIsRecordingDisabled
  );

  // Recovery hook
  const {
    recoverableMeetings,
    isLoading: isLoadingRecovery,
    isRecovering,
    checkForRecoverableTranscripts,
    recoverMeeting,
    loadMeetingTranscripts,
    deleteRecoverableMeeting
  } = useTranscriptRecovery();

  const router = useRouter();

  useEffect(() => {
    // Track page view
    Analytics.trackPageView('home');
  }, []);

  // Startup recovery check
  useEffect(() => {
    const performStartupChecks = async () => {
      try {
        // Skip recovery check if currently recording or processing stop
        // This prevents the recovery dialog from showing when:
        if (recordingState.isRecording ||
          status === RecordingStatus.STOPPING ||
          status === RecordingStatus.PROCESSING_TRANSCRIPTS ||
          status === RecordingStatus.SAVING) {
          console.log('Skipping recovery check - recording in progress or processing');
          return;
        }

        // 1. Clean up old meetings (7+ days)
        try {
          await indexedDBService.deleteOldMeetings(7);
        } catch (error) {
          console.warn('⚠️ Failed to clean up old meetings:', error);
        }

        // 2. Clean up saved meetings (24+ hours after save)
        try {
          await indexedDBService.deleteSavedMeetings(24);
        } catch (error) {
          console.warn('⚠️ Failed to clean up saved meetings:', error);
        }

        // 3. Always check for recoverable meetings on startup
        // Don't skip based on sessionStorage - we need to check every time
        await checkForRecoverableTranscripts();
      } catch (error) {
        console.error('Failed to perform startup checks:', error);
      }
    };

    performStartupChecks();
  }, [checkForRecoverableTranscripts, recordingState.isRecording, status]);

  // Watch for recoverable meetings changes and show dialog once per session
  useEffect(() => {
    // Only show dialog if we have meetings and haven't shown it yet this session
    if (recoverableMeetings.length > 0) {
      const shownThisSession = sessionStorage.getItem('recovery_dialog_shown');
      if (!shownThisSession) {
        setShowRecoveryDialog(true);
        sessionStorage.setItem('recovery_dialog_shown', 'true');
      }
    }
  }, [recoverableMeetings]);

  // Handle recovery with toast notifications and navigation
  const handleRecovery = async (meetingId: string) => {
    try {
      const result = await recoverMeeting(meetingId);

      if (result.success) {
        toast.success('Meeting recovered successfully!', {
          description: result.audioRecoveryStatus?.status === 'success'
            ? 'Transcripts and audio recovered'
            : 'Transcripts recovered (no audio available)',
          action: result.meetingId ? {
            label: 'View Meeting',
            onClick: () => {
              router.push(`/meeting-details?id=${result.meetingId}`);
            }
          } : undefined,
          duration: 10000,
        });

        // Refresh sidebar to show the newly recovered meeting
        await refetchMeetings();

        // If no more recoverable meetings, clear session flag so dialog can show again
        if (recoverableMeetings.length === 0) {
          sessionStorage.removeItem('recovery_dialog_shown');
        }

        // Auto-navigate after a short delay
        if (result.meetingId) {
          setTimeout(() => {
            router.push(`/meeting-details?id=${result.meetingId}`);
          }, 2000);
        }
      }
    } catch (error) {
      toast.error('Failed to recover meeting', {
        description: error instanceof Error ? error.message : 'Unknown error occurred',
      });
      throw error;
    }
  };

  // Handle dialog close - clear session flag if no meetings left
  const handleDialogClose = () => {
    setShowRecoveryDialog(false);
    // If user closes dialog and there are no more meetings, clear the flag
    // This allows the dialog to show again next session if new meetings appear
    if (recoverableMeetings.length === 0) {
      sessionStorage.removeItem('recovery_dialog_shown');
    }
  };

  // Computed values using global status
  const isProcessingStop = status === RecordingStatus.PROCESSING_TRANSCRIPTS || isProcessing;

  return (
    <div className="xx-home">
      <header className="flex shrink-0 items-center justify-between gap-4">
        <div><p className="xx-eyebrow">A little room for every conversation</p><h1 className="xx-heading text-[28px] mt-1">Your listening space</h1></div>
        <span className="flex items-center gap-2 text-xs text-[var(--xx-muted)]"><Headphones size={14} />Headphones recommended</span>
      </header>
      <CaptureRecoveryPanel />
      <SettingsModals modals={modals} messages={messages} onClose={hideModal} />
      <TranscriptRecovery isOpen={showRecoveryDialog} onClose={handleDialogClose} recoverableMeetings={recoverableMeetings} onRecover={handleRecovery} onDelete={deleteRecoverableMeeting} onLoadPreview={loadMeetingTranscripts} />
      <div className="xx-home-sheet xx-paper">
        <details className="xx-recording-options shrink-0 border-b border-[var(--xx-border)]">
          <summary className="flex cursor-pointer items-center gap-2 px-6 py-3 text-xs text-[var(--xx-muted)]"><SlidersHorizontal size={14} />Recording options<span className="ml-auto">Automatic · Apex draft → Trelis final</span></summary>
          <div className="max-h-[38vh] overflow-y-auto px-5 pb-4"><LocalTranscriptionPanel /></div>
        </details>
        <LiveTranscriptPanel />
        {(hasMicrophone || isRecording) && status !== RecordingStatus.PROCESSING_TRANSCRIPTS && status !== RecordingStatus.SAVING && <div className="xx-recording-dock">
          <RecordingControls isRecording={recordingState.isRecording} onRecordingStop={(callApi = true) => handleRecordingStop(callApi)} onRecordingStart={handleRecordingStart} onTranscriptReceived={() => {}} onStopInitiated={() => setIsStopping(true)} barHeights={barHeights} onTranscriptionError={message => showModal('errorAlert', message)} isRecordingDisabled={isRecordingDisabled} isParentProcessing={isProcessingStop} selectedDevices={selectedDevices} meetingName={meetingTitle} />
          <p className="max-w-[220px] text-xs leading-5 text-[var(--xx-muted)]">{isRecording ? 'Your audio is being saved on this laptop.' : 'Microphone + computer audio. You decide when to start.'}</p>
        </div>}
        <StatusOverlays isProcessing={status === RecordingStatus.PROCESSING_TRANSCRIPTS && !recordingState.isRecording} isSaving={status === RecordingStatus.SAVING} sidebarCollapsed={sidebarCollapsed} />
      </div>
      {!isRecording && meetings.length > 0 && <section className="shrink-0" aria-label="Recent conversations">
        <div className="xx-eyebrow mb-2">Pick up where you left off</div>
        <div className="grid grid-cols-3 gap-3">{meetings.slice(0, 3).map(meeting => <button key={meeting.id} className="xx-recent-meeting" onClick={() => { setCurrentMeeting(meeting); router.push(`/meeting-details?id=${encodeURIComponent(meeting.id)}`); }}><FileText size={15} className="shrink-0 text-[var(--xx-muted)]" /><span className="truncate">{meeting.title}</span><ArrowUpRight size={13} className="ml-auto shrink-0 text-[var(--xx-muted)]" /></button>)}</div>
      </section>}
    </div>
  );
}
