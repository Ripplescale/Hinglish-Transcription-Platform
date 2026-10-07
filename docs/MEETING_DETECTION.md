# Zoom and Teams call prompts (Windows)

When oats is running, an active microphone session belonging to the Zoom or
Microsoft Teams desktop app can trigger a small side window. Choose **Record
call** to enter the normal recording flow with the selected microphone and system
audio device. **Dismiss**, Escape, or closing the prompt suppresses it for the
current activity. Recording never starts from detection alone.

Toggle this under **Settings → Recording → Zoom & Teams call prompts**. The
preference is saved locally. Closing the main window leaves oats in the tray;
quitting oats stops detection. There is no added Windows login startup task.

The detector inspects Windows audio-session state and process names. It does not
read audio samples, email, calendar entries, window titles, or meeting content.
No Outlook, Sage, Microsoft Graph, or Zoom account connection is needed.

## Detection limits

- Zoom (`Zoom.exe`), current Teams (`ms-teams.exe`), and classic Teams
  (`Teams.exe`) are recognized. A Teams WebView child is attributed by following
  its process ancestry. Browser meetings are not classified.
- Activity must persist for at least six seconds. A microphone test or another
  desktop-app microphone use can also produce a prompt, so it asks whether you
  are in a call rather than asserting that a meeting has started.
- Muted calls work only while the application keeps its microphone session
  active. Calls that never activate a microphone need a manual recording start.
- Prompts expire after 45 seconds and are suppressed while recording. A call
  rearms after 30 seconds without detected activity, to avoid reconnect spam.
  Back-to-back calls less than 30 seconds apart may need a manual start.
- Detection does not automatically stop recordings, change devices, assign a
  meeting title, fetch attendees, or restrict system capture to just the call app.
- Audio permissions and existing recording checks still apply. An unavailable
  detector does not prevent manual recording.

## Verification

`meeting-detection-tests` compiles the production Windows probe, detection state,
Tauri command handlers, and popup integration without loading speech models. Only
the existing recording-status and main-window-focus boundaries are stubbed.

```text
cargo test --manifest-path frontend/src-tauri/meeting-detection-tests/Cargo.toml --lib
cargo run --manifest-path frontend/src-tauri/meeting-detection-tests/Cargo.toml --bin probe
node frontend/tests/ui/meeting-prompt.test.cjs
node frontend/tests/lib/meeting-detection-bridge.test.cjs
```

The probe inspects session metadata only. The browser test uses fictional Teams
metadata and checks Record, duplicate clicks, Dismiss, Escape, failed actions,
expired prompts, and layout within the popup dimensions. It does not record.

A real-device acceptance pass must still cover joining/leaving both desktop apps,
muted calls, Teams WebView attribution, headset switching, reconnects, dismissing,
starting while oats is hidden, and disabling prompts. Verify that selected-device
capture contains both sides of the call. Synthetic checks cannot establish those
results.

Verified on 2026-10-03: frontend production export and type checks passed;
the production native integration and command registration compiled in the
focused harness; four detector tests, two bridge tests, five popup scenarios,
16 existing note-autosave tests, and the existing synthetic notes/recording
browser regression passed. The Windows probe completed successfully with no
active Zoom/Teams microphone sessions at that time. This is not a successful
live-call detection claim or a full application installer validation.

## Integration boundaries

The feature owns `meeting_detection/`, the popup assets, its bridge and settings
components, and focused tests. Existing app hooks are limited to native command
registration/startup, mounting the bridge, mounting the settings toggle, and the
Tauri global API used by the separate static popup window. It reuses the existing
sidebar recording navigation rather than introducing a second recorder. It does
not modify notes, saved recordings, models, or maximize/restore behavior.
