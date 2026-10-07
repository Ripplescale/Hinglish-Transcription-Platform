import { recordingTime, type TranscriptSegmentMetadata } from '@/lib/transcript-workspace';

/** Recognition provenance stays separate from the selected text used for reading and copy. */
export function TranscriptProcessingChecks({ metadata, startSeconds, endSeconds }: { metadata?: TranscriptSegmentMetadata; startSeconds?: number; endSeconds?: number }) {
  if (!metadata || (!metadata.quality_flags?.length && !metadata.recovery)) return null;
  const recovery = metadata.recovery;
  const isContext = recovery?.state === 'complete' && recovery.method === 'context_window_retry';
  const isBoundary = isContext && recovery.original_segment_id != null;
  const isShorter = metadata.alternative?.promoted || recovery?.method === 'shorter_window_retry';
  const originalLabel = isBoundary ? 'Recognition before boundary replay' : isContext ? 'Recognition before context recovery' : 'Recognition before shorter retry';
  return <details className="text-xs text-amber-800">
    <summary className="cursor-pointer">Processing checks</summary>
    {!!metadata.quality_flags?.length && <p className="mt-2">{metadata.quality_flags.map(flag => flag.replaceAll('_', ' ')).join(' · ')}</p>}
    {recovery && <div className="mt-2 space-y-2">
      {recovery.state === 'waiting_for_context' ? <p>Waiting for surrounding audio before retrying this window. The current recognition is provisional.</p>
        : isBoundary ? <p>A boundary replay was selected to preserve neighboring speech. Review it against the recording.</p>
          : isContext ? <p>A retry with surrounding audio was selected for this transcript. Review it against the recording.</p>
          : recovery.method === 'original' ? <p>The original recognition remains selected after recovery attempts. Review it against the recording.</p> : null}
      {isContext && <>
        <p>Selected audio window: {recordingTime(startSeconds)}–{recordingTime(endSeconds)}.</p>
        <p>{isBoundary ? 'Parent retry' : 'Surrounding audio retry'}: {recordingTime(recovery.context_start_seconds)}–{recordingTime(recovery.context_end_seconds)}. Repeated window: {recordingTime(recovery.core_start_seconds)}–{recordingTime(recovery.core_end_seconds)}.</p>
        {isBoundary && <p>Earlier source window: {recordingTime(recovery.original_start_seconds)}–{recordingTime(recovery.original_end_seconds)}.</p>}
      </>}
      {!!recovery.attempts?.length && <details>
        <summary className="cursor-pointer">Recovery attempts</summary>
        <ul className="mt-2 space-y-2">
          {recovery.attempts.map((attempt, index) => <li key={index}>
            {attempt.kind === 'overlap_fringe' ? 'Boundary replay' : attempt.kind === 'shorter_window_retry' ? 'Shorter window retry' : 'Surrounding audio retry'} · {recordingTime(attempt.start_seconds)}–{recordingTime(attempt.end_seconds)}
            {!!attempt.quality_flags?.length && <span> · {attempt.quality_flags.map(flag => flag.replaceAll('_', ' ')).join(' · ')}</span>}
          </li>)}
        </ul>
      </details>}
    </div>}
    {isContext || isShorter ? <div className="mt-2 space-y-2">
      {!isContext && <p>A shorter-window retry was selected for this transcript. Review it against the recording.</p>}
      {metadata.original_recognition_text != null ? <details><summary className="cursor-pointer">{originalLabel}</summary><p className="mt-2 whitespace-pre-wrap leading-6">{metadata.original_recognition_text}</p></details>
        : <p>The recognition before retry is not available in this workspace.</p>}
    </div> : !recovery && metadata.alternative?.text ? <div className="mt-2 space-y-2">
      <p>A shorter-window retry is available for comparison. It has not replaced the original recognition.</p>
      <p className="whitespace-pre-wrap leading-6">{metadata.alternative.text}</p>
    </div> : null}
  </details>;
}
