# oats · Peach Buddy design QA

Reviewed 2026-10-07. **No actionable P0, P1 or P2 findings remain in the inspected final captures.**

## Comparison target and evidence

- Visual truth: [selected Peach Buddy reference](docs/assets/oats-peach-buddy-reference.png), 1142 × 1377 pixels.
- Final implementation: [notes library](docs/assets/xx-library.png), 680 × 820 pixels; CSS viewport 680 × 820, device scale factor 1, confirmed capture flag `--force-device-scale-factor=1`.
- [Combined source/implementation comparison](docs/assets/oats-peach-buddy-comparison.png), 1392 × 860 pixels, was opened and inspected as one comparison input. The source is normalized to 680 × 820; its aspect-ratio difference from that viewport is below 0.01%. Both columns show the same compact-sidebar library state, three fictional notes and dates October 7/6/5. There is no browser or device frame in either column.
- Additional final captures: `stt_lab/private/oats-design/implementation-600x560.png`, `stt_lab/private/oats-design/settings-expanded-600x560.png`, [saved notes at 680 × 820](docs/assets/xx-workspace.png), `stt_lab/private/peach-buddy-final-qa-20261007/clean-notes/live-collapsed-600x560.png`, and `stt_lab/private/peach-buddy-final-qa-20261007/clean-notes/live-transcript-680x820.png`.

The combined comparison is readable at its original resolution. Separate focused passes over the rail, heading/art, search/action row, tabs, grouped notes and footer used those same two columns; additional crops were unnecessary.

## Required fidelity surfaces

| Surface | Final inspection |
| --- | --- |
| Fonts and typography | Local DynaPuff supplies the rounded wordmark/headings (42px wordmark, 40px library title at the main viewport); Segoe UI supplies readable controls, 15px notes and 14px previews. The brand/title hierarchy, line heights and truncation remain clear. The real display font has slightly tighter shapes than the generated reference, an acceptable font adaptation. Body copy and mixed Hindi/English transcript remain legible; note-taking text uses the body font. |
| Spacing and layout rhythm | Compact rail, rounded paper surface, upper-right buddy, greeting/title, search beside one primary New note action, tabs, grouped rows and bottom footer preserve the selected composition. Small differences in row spacing and heading position do not change content hierarchy. At 600 × 560 the notes list scrolls within its surface, with search, New note and footer visible. Expanded Settings tabs wrap and remain reachable. Live pause/finish/transcript controls and saved-note playback remain visible. |
| Colors and tokens | Cream paper, peach rail/buttons, cocoa text and restrained dividers match the reference. Final calculated contrast: muted text on canvas 4.76:1, on paper 5.45:1, and in search 4.86:1; peach CTA text 7.68:1; checked switch against paper 6.38:1. Dark switch tracks are an intentional accessibility adaptation. No gradients or unrelated semantic colors were introduced into the library. |
| Image quality and fidelity | Real generated buddy and listening-scene PNGs match the selected oat/headphone identity, peach cheeks, book, cup and sage plant. The art keeps its aspect ratio and integrates with the cream surface; no objectionable halo, clipping or distortion appears at actual display size. No CSS, emoji or handcrafted SVG substitutes replace the mascot. Existing Lucide controls provide consistent functional icons; native audio-player appearance is an accepted platform adaptation. |
| Copy and content | Exact lowercase oats branding and coherent standalone labels match the chosen direction. The three library titles/previews and fictional dates match the source. Saved notes retain their writing content; live transcripts retain mixed script. Footer copy explains that New note starts recording. No prompt language or invented features appear in the product. |

## Comparison history and resolved findings

1. **P2: checked switch contrast.** The new peach primary token originally produced about 1.7:1 track contrast. Checked switches now use `--xx-accent`; the dark track is visible in the final expanded Settings capture.
2. **P2: muted text on peach canvas.** The original `#82685d` token measured 4.43:1. It is now `#7d6358`; final sidebar and Settings copy are visible in the post-fix captures and measure 4.76:1 against the canvas.
3. **P2: placeholder contrast.** Compose placeholder `#a28b7c` measured 3.17:1. Compose, sidebar/library search and saved-note placeholders now use the muted token; saved-note placeholder opacity is 1. Search text is readable in the final comparison and narrow capture. The final live/saved captures contain note text rather than empty placeholders, so their empty-state appearance is supported by the inspected final CSS, not a claimed placeholder screenshot.
4. **P2 responsive risk: Settings tab overflow.** The fixed one-line tab list could exceed the available width with the expanded sidebar. The final list uses `max-w-full flex-wrap`; the 600 × 560 expanded Settings capture shows all four tabs, including Claude handoff on a second line.

These fixes preceded the final exported build and screenshots. The final combined comparison and additional responsive captures were inspected after the fixes; no further visual repair was required.

## Functional evidence and limits

Inspected reports:

- `stt_lab/private/peach-buddy-final-qa-20261007/clean-notes/report.json`: passed; no page errors or external requests. Checks include branding/About, project creation/search, one capture/job per rapid New note interaction, note/project autosave, transcript show/hide, and finish preserving pending edits.
- `stt_lab/private/peach-buddy-final-qa-20261007/saved-notes/report.json`: passed; no page errors. Checks include notes/transcript layouts at 680 × 820, 600 × 560, 720 × 560 and 920 × 740, autosave races, explicit correction application, persistent playback, transcript versions/exports and save-failure recovery. Reported document dimensions match the tested viewports without page overflow.
- `stt_lab/private/peach-buddy-final-qa-20261007/prompt/report.txt`: record, dismiss, Escape, error and expired prompt cases passed.

These are fictional fixtures with synthetic native IPC; they do not qualify real microphone/system recording, model execution, or an installed app. The actual native meeting-prompt window and assistive-technology/zoom behavior were not visually exercised in this review. Root reports the final Next export and native Check passed; those checks supplement, rather than replace, the visual evidence.

## Implementation checklist

- [x] Compare the selected source and final rendered library together at matching viewport/density.
- [x] Inspect all five fidelity surfaces and readable individual regions.
- [x] Verify final narrow library, expanded Settings, live writing/transcript and saved-note captures.
- [x] Record prior findings, fixes and post-fix evidence.
- [x] Inspect existing functional reports and state their fixture limitations.

final result: passed
