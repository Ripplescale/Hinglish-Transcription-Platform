/* A separate, local-only window: never mounts the main app or its providers. */
(() => {
  const record = document.getElementById('record');
  const dismiss = document.getElementById('dismiss');
  const status = document.getElementById('status');
  const invoke = (...args) => window.__TAURI__.core.invoke(...args);
  let prompt;
  async function respond(shouldRecord) {
    if (!prompt || record.disabled) return;
    record.disabled = dismiss.disabled = true;
    try {
      await invoke('respond_to_meeting', { id: prompt.id, record: shouldRecord });
    } catch (error) {
      status.textContent = String(error);
      // Keep Dismiss available after a recording error.
      record.disabled = dismiss.disabled = false;
    }
  }
  record.addEventListener('click', () => respond(true));
  dismiss.addEventListener('click', () => respond(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') void respond(false); });
  invoke('meeting_prompt').then(value => {
    prompt = value;
    if (!prompt) { status.textContent = 'This call prompt has expired.'; return; }
    document.getElementById('title').textContent = `${prompt.app} audio detected`;
    document.getElementById('description').textContent = 'In a call? Save the audio and take notes in oats.';
    record.disabled = dismiss.disabled = false;
  }).catch(() => { status.textContent = 'Open oats to start recording.'; });
})();
