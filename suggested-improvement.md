# Suggested UI and UX improvements

Reviewed: 2026-10-04. The original audit and implementation brief are preserved below. The user subsequently approved the full scope; implementations are recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md), with current workflows in [README.md](README.md). Statements about the original defects below describe the audited version, not the updated application.

## Review scope and evidence

The review covers the current PySide6 desktop app in `src/tracksmith`, its README, and the existing UI, UX, artwork, playback-selection, stamping, and timing-editor tests. The generated files under `build/lib` are not the implementation target.

I rendered the welcome screen, loaded workspace, review filter, completed-analysis panel, lyric chooser, artwork chooser, settings, missing-line dialog, and word editor using synthetic song data and a generated 40-second tone MP3. I inspected screenshots and measured Qt layout sizes. Small interaction probes also checked keyboard focus, review classification, and guided stamping. Artwork failures were deliberately simulated; they are not evidence of a provider outage. No real music was changed, no online lookup or AI analysis was performed, and no settings were saved. This is not a full regression-test run, an accessibility audit with assistive technology, or a listening evaluation of alignment quality.

The recommendations below distinguish **confirmed behavior** (rendered or reproduced), **source findings** (visible in the implementation), and **proposed enhancements**. Effort estimates are relative: S = localized UI change; M = several widgets or shared behavior; L = new persistent state or substantial interaction work. They are not delivery estimates.

## What should remain intact

The existing light theme, grouped cards, explicit save action, per-song in-memory edits, asynchronous jobs, cancellation, cover previews, review filtering, guided line timing, and word Gantt chart form a useful foundation. Improve them incrementally.

- Opening a song must remain read-only. Saving must continue preserving compressed audio and using the existing verified, atomic write path.
- Save applies to the selected song. Adding session recovery must not automatically write to MP3s.
- Lyrics and metadata proposals remain reviewable. Remote synchronized lyrics must not become locally verified timing results.
- Keep one selected lyric row, with the highlighted row and Enter stamp target agreeing. Preserve the playback-selection lock and existing protections against replacing a time with ordinary Enter.
- Preserve repeated lyric occurrences, section headings, cut lyrics, precise timestamps, word provenance, and the distinction between estimates and audio alignment.
- Retain manual editing without network access or AI dependencies. Keep online requests explicit and long work off the UI thread.

## Prioritized backlog

P1 means address first because the issue affects ordinary use, trust, or preservation of work. P2 improves common workflows. P3 is an optional expansion after the core experience is stable. Each ID is a separate implementation scope; implementing this whole document in one change is unnecessary.

| ID | Priority | Suggested change | Evidence | Effort |
| --- | --- | --- | --- | --- |
| UX-01 | P1 | Make the workspace and dialogs fit smaller windows | Confirmed rendering | M |
| UX-02 | P1 | Make shortcuts respect the focused control | Confirmed interaction | M |
| UX-03 | P1 | Apply main-window stamping safeguards in the guided dialog | Confirmed interaction + source | S–M |
| UX-04 | P1 | Separate timing support, listening review, and intentional exclusions | Confirmed interaction + source | L |
| UX-05 | P1 | Recover unsaved work across restarts | Source finding; documented limitation | L |
| UX-06 | P1 | Provide consistent undo for meaningful edits | Source finding | L |
| UX-07 | P1 | Protect lyric edits while the timing table is stale | Source finding | M |
| UX-08 | P2 | Give timing review more space and clearer action hierarchy | Confirmed rendering + enhancement | M |
| UX-09 | P2 | Make word correction and validation easier | Confirmed rendering + source | M |
| UX-10 | P2 | Preview and selectively apply metadata proposals | Source finding | M |
| UX-11 | P2 | Make search choosers easier to compare and retry | Confirmed rendering + source | M |
| UX-12 | P2 | Replace the dense save message with a structured review | Source finding | M |
| UX-13 | P2 | Improve management of loaded songs and closing | Confirmed rendering + source | M |
| UX-14 | P2 | Make operation feedback specific and actionable | Source finding | M |
| UX-15 | P2 | Make settings understandable and validate before closing | Confirmed rendering + source | M |
| UX-16 | P2 | Improve focus, labeling, and visual status semantics | Confirmed rendering + source | M |
| UX-17 | P3 | Add focused playback tools, then a waveform | Proposed enhancement | M–L |
| UX-18 | P3 | Make exported timing data usable for returning to work | Source finding + enhancement | L |

## UX-01 — Fit the workspace and dialogs to smaller screens

**Problem and evidence.** `MainWindow` starts at 1440×960 and stacks a header, workflow badges, metadata, player, editor, job panel, and footer vertically. In the synthetic review, the loaded workspace had a minimum size hint of **1100×845**. A request for 1280×720 actually produced **1280×845**; the table showed its header and barely one row. At 1440×960 with metadata visible, only about three full timing rows were visible. Showing the completed-analysis panel with metadata visible raised the minimum height to **969**. The word editor grew to **842 pixels high** when exact timestamp controls were enabled, despite requesting 1024×768. The artwork chooser grew to 668 pixels high and Settings to 522. These are offscreen Qt measurements at the review environment's font settings, not universal dimensions.

**Suggested behavior.**

- Select initial size from the available screen geometry and keep title bar, actions, and footer reachable. Treat 1280×720 and 1366×768 as practical target display sizes, allowing for desktop decorations.
- Let details and help text scroll or collapse instead of forcing the entire window taller. Prefer giving the table and word chart the flexible space.
- Reflow toolbars into two rows or use an overflow menu when narrow. At narrow widths, offer Lyrics/Timing tabs or a vertical layout instead of squeezing both horizontal panels.
- Make the welcome card width responsive; it currently has a fixed 640-pixel width. Check the minimum size contribution of both pages of `QStackedWidget`, including the inactive welcome page.
- Keep dialog confirmation buttons visible while content scrolls. Adapt the 320×320 artwork preview to available space.
- Persist geometry and splitter positions; restore them inside the currently available screen after monitor changes. After a panel is hidden, allow the window to shrink immediately.

**Implementation entry points.** `ui.py`: `_build_ui`, initial `resize`, both splitters, workspace stack, job panel. `timing_editor.py`: `PlaybackDialog`, `WordTimingDialog`. `artwork_dialog.py`: preview sizing. Add presentation preferences separately from analysis settings in `config.py`.

**Acceptance.** At the target display sizes, metadata can be accessed, several lyric rows remain usable, and Save/Cancel are always reachable. Check empty and loaded states, active/completed jobs, exact word controls, long titles, and larger font/display scaling. Hiding metadata must reduce the required space without restarting the app.

## UX-02 — Respect keyboard focus before applying song shortcuts

**Problem and evidence.** The application-wide `MainWindow.eventFilter` excludes typing fields and switches, but not sliders, the song list, or ordinary buttons. A reproduced case: with a timed line starting at 8.000 seconds and the playback slider focused, pressing Right changed the line start to **8.100 seconds**. A user manipulating playback can therefore edit timing unintentionally. The same global handling can take Up/Down away from song-list navigation and Enter away from focused button activation. `PlaybackDialog.eventFilter` similarly intercepts keys for ordinary controls.

**Suggested behavior.**

- Define a focus policy: typing widgets edit text; sliders handle their own navigation; the song list navigates songs; buttons and switches activate normally; the timing table and an explicit timing-workspace focus handle stamping and timestamp nudges.
- Scope Ctrl+Z to the relevant editor/history and avoid song-level undo while typing. Apply the same policy in the line and word dialogs.
- Preserve convenient Space playback where it does not conflict with the focused control. Display the active shortcut context near the timing tools.
- Do not rely on globally swallowing all navigation keys. Keep the playback-selection lock intact specifically for the lyric table.

**Implementation entry points.** `ui.py`: `eventFilter`. `timing_editor.py`: `PlaybackDialog.eventFilter`. `lyric_table.py`: table lock. Consider scoped `QAction` shortcuts or an explicit control-aware dispatcher.

**Acceptance.** Right on the playback slider changes playback only; Up/Down in the sidebar changes songs only; Enter on Find lyrics activates that button; typing fields preserve normal behavior. Enter in the timing context stamps the indicated row only. Verify paused/playing states, dialogs, held keys, and Ctrl+Z routing.

## UX-03 — Make guided stamping as safe as main-window stamping

**Problem and evidence.** The main window checks `word_timing_window` before stamping and suppresses auto-repeated Enter. The guided `MissingTimingDialog.stamp` only checks the audio duration through `set_timestamp`. With neighbouring timed rows at **8 and 17 seconds**, the dialog accepted a stamp at **23 seconds**. The dialog's shared event filter also lacks the main window's auto-repeat protection. Its lead-in follows the previous line, while playback remains a full-song range.

**Suggested behavior.**

- Reuse the same validated neighbouring-line bounds for both stamp paths. Show the allowable interval beside the current line and disable stamping outside it.
- Ignore auto-repeated Enter/Shift+Enter. Require a separate physical keypress for each stamp.
- When anchors conflict, show the relevant rows and provide a way to return to them; do not invent a valid interval.
- Keep the lead-in useful for listening, but explain when stamping becomes available. If playback is bounded, account for lead-in separately from the valid stamp interval.
- Show a clear accepted-stamp acknowledgment before advancing, and make rejected stamps retain the current target.

**Implementation entry points.** `timing_editor.py`: `MissingTimingDialog.stamp`, `play_lead_in`, `refresh`, `PlaybackDialog.eventFilter`. Share logic with `timing_data.py` and main-window stamping rather than duplicating it.

**Acceptance.** A stamp outside 8–17 seconds is rejected in the guided dialog just as in the main window. Holding Enter does not fill several missing lines. A rejected action leaves timestamps and selection unchanged. Opening/closing a dialog still disconnects playback handlers correctly.

## UX-04 — Separate timing support from listening review and exclusions

**Problem and evidence.** `needs_review` mixes missing times, word estimates, manual provenance, and the confidence threshold. AI lines above the threshold receive a percentage and check mark even without listening review. Conversely, there is no direct main-table action to accept a correct low-support line after listening. `MissingTimingDialog.skipped` exists only for that dialog: skipping a cut verse leaves it in the main review queue. In a reproduced case, moving a low-support AI line made its source `manual` and removed it from review while its retained words still had **0.4 support**. Manual line adjustment therefore does not reliably communicate word-review status.

**Suggested behavior.**

- Represent separate concepts: line-time provenance, word provenance/support, explicit listening-review state, and intentional exclusion from this recording. Avoid encoding all of them in `source` or `confidence`.
- Add **Mark line reviewed**, **Mark words reviewed**, **Not sung in this version**, and **Reopen review** actions. A line-level decision must not automatically certify its word boundaries.
- Keep cut lyrics in plain lyrics while excluding them from synchronized output and the outstanding review queue. Offer **Skip for now** separately; temporary deferral must not mean intentionally absent.
- Present categories such as `Untimed`, `AI timing · needs listening`, `Line reviewed`, `Words need review`, and `Not sung`. Put support scores in secondary detail labeled **Model support**, not as a correctness probability or a reviewed check mark.
- Derive the table, filters, footer, workflow indicators, job result, and save counts from one shared review summary. Separate genuinely missing lines from intentional exclusions.
- Use stable line/occurrence IDs for review decisions. Changing a lyric or its relevant timing should invalidate the corresponding review; changing a threshold must not fabricate a listening decision.

**Implementation entry points.** `model.py`: `LyricLine`, `Word`, `move_timestamp`; `ui.py`: `needs_review`, `render_table`, `update_workflow`, `apply_alignment`, `save`; `timing_editor.py`: skip and word review; `timing_data.py`: serialization. This requires a schema migration, including compatibility for existing cache/export data.

**Acceptance.** A correctly excluded cut verse no longer cycles through Next to review, but remains in plain lyrics. Retiming an AI line does not mark its words reviewed. A low-support line can be accepted explicitly without changing its measured support. All displayed review totals agree and decisions survive saving/reopening under the persistence rules.

## UX-05 — Recover unsaved edits after restart

**Problem and evidence.** `SongSession` is in memory. Closing offers Discard/Cancel when there are unsaved songs; there is no session recovery. Rich timing snapshots are written after an explicit MP3 save, so they do not protect a long unsaved review pass. The README clearly documents this limitation.

**Suggested behavior.**

- Maintain an atomic, versioned recovery draft in application storage after meaningful changes, using a debounce to avoid writing on every keystroke or playback tick. Include metadata, chosen artwork, full display lyrics, unapplied lyric edits, line/word timings, review/exclusion state, selected occurrence, file identity, and loaded-song order.
- On startup, offer **Restore previous workspace** with the affected songs and draft date. Check each source file before attaching a draft; offer Relocate for missing files and a conflict explanation for changed files.
- Preserve the original MP3 as the save baseline. A recovery snapshot must not make restored unsaved edits appear saved.
- Distinguish **MP3 changes not saved** from **Recovery draft saved**. Discarding a draft should remove its recovery state so it does not return unexpectedly.
- Define storage ownership and retention explicitly. Recovery drafts and manually corrected timing snapshots are valuable user work, unlike disposable download/search caches.

**Implementation entry points.** `session.py`, `model.py`, `cache.py` atomic helpers, `timing_data.py` validation, `app.py` startup, `ui.py` mutation and close paths. Use a separate namespace/location for recoverable user drafts.

**Acceptance.** Restore an unsaved workspace containing two songs, pending lyric text, artwork, and word corrections after process termination. MP3 hashes remain unchanged. Missing/modified files are not silently overwritten. Discarded drafts do not reappear.

## UX-06 — Make undo consistent across meaningful edits

**Problem and evidence.** Main-window Undo restores stamps/re-times only. Direct timestamp edits, Left/Right nudges, Clear time, metadata/artwork proposals, and lyric rebuilding do not use that history. The guided missing-line dialog has a separate local stamp history. The word editor already has draft Undo/Redo, but accepted changes are not a main-window undo command. Reapplying lyrics or analysis clears stamp history. A button labeled simply Undo therefore has a narrower scope than many users expect.

**Suggested behavior.**

- First make the current scope explicit with **Undo timing stamp**. Then introduce a per-song command history for committed editing actions: timestamp edits/nudges, clearing time, accepted word changes, applied proposals, lyric rebuilds, and analysis replacement.
- Make a rebuild or analysis replacement one operation containing the before/after line and word state. Keep text-editor undo local while typing; committed application actions belong to the song history.
- Route guided stamps into the same song history so they can be undone after closing the dialog. Keep word-draft history separate until Apply; Apply becomes one song command.
- Show the next undo/redo action in menu labels, such as **Undo clear timestamp**, and add Redo. Bound history memory, especially for artwork and full analyses.
- Keep saving distinct from editing: undo after a save changes the working draft and marks it dirty; it must not automatically roll the file back.

**Implementation entry points.** `session.py`, `ui.py`: `_stamp_history`, `_set_selected`, `table_edited`, `apply_lyrics`, `apply_alignment`, proposal handlers; `timing_editor.py`. A shared command model or `QUndoStack` is a possible implementation, not a requirement.

**Acceptance.** Clear a line with word data, then undo to restore every boundary/support/source. Undo an accepted word edit and a guided stamp after dialog close. Switching songs uses their respective histories. Text typing and later edits cannot be overwritten by an unrelated old stamp undo.

## UX-07 — Protect pending lyrics and make rebuild consequences visible

**Problem and evidence.** Editing the lyrics sets `_lyrics_pending`, but the timing table still displays its old text and remains generally editable. Save, export, and analysis call `apply_lyrics` automatically. Rebuilding clears the stamp history and can invalidate AI timing for changed text. `table_edited` may reconstruct display lyrics from the old table when it cannot match the edited row in the pending text, which creates a path for losing newly edited text or headings. These are source findings; that fallback was not exercised in the visual probe.

**Suggested behavior.**

- Show an inline **Lyrics changed — timing table needs updating** banner at the table. Disable edits to stale rows, or synchronize them through stable occurrence IDs; do not allow two conflicting lyric representations to be edited independently.
- Rename Update lines to something that states its effect, such as **Apply lyric changes**. Show counts of unchanged, new, removed, and timing-invalidated occurrences when a rebuild will affect existing timings.
- Preserve unchanged repeats and headings. Make large transformations one reversible operation under UX-06.
- Keep automatic application convenient for first-time/plain text input. When it would discard existing timing work, show the impact before committing and let the user cancel without changing the draft.
- The footer should offer both **Find lyrics** and **Paste lyrics** where useful. Add a short formatting hint about headings, repeated choruses, and omitted verses beside the editor; the README should not be required to learn this.

**Implementation entry points.** `ui.py`: `lyrics_edited`, `table_edited`, `apply_lyrics`, `save`, export and analysis entry points; `lyrics.py`: `split_lyrics`; `session.py`: pending state.

**Acceptance.** After changing a lyric in the editor, editing a stale table row cannot overwrite pending text. Canceling a rebuild/replacement keeps the prior timing and the pending draft. Repeated identical lines retain their individual times where appropriate, and section headings remain intact.

## UX-08 — Give the current task more room and reduce competing actions

**Problem and evidence.** The loaded screen shows Load, Play, Analyze, and Save with the same strong green emphasis, alongside several repeated next-step actions. Metadata uses a large top card; the lyrics text duplicates the timing table during review. The workflow badges are status labels rather than navigation, and the footer drives timing even when a user only wants to edit tags. Starting analysis or selecting Next to review automatically hides metadata.

**Suggested behavior.**

- Add explicit **Details**, **Lyrics**, and **Timing review** workspace views or focus controls using the existing widgets. For review, collapse the lyric source pane as well as details so the table gets most of the space. Keep playback and the selected song identity visible.
- Let the workflow indicators navigate to their sections and expose whether each is ready or needs attention. Offer a simple route to edit details and save without timing lyrics.
- Have one strongest action per context: Find/Add lyrics, Analyze timing, or Review next. Keep Save consistently placed and make its importance follow unsaved changes. Treat Add song and secondary tools more quietly after the first song loads.
- Explain automatic view changes and preserve/restore user layout preferences. Use explicit buttons such as **Show details** or **Focus timing** rather than surprising disappearance.
- Keep concise task help near Stamp/Re-time. Longer instructional text can move into a collapsible help panel.

**Implementation entry points.** `ui.py`: `_build_ui`, `update_workflow`, `next_review_line`, `analyze`. Coordinate with UX-01; avoid building a separate second editor with divergent state.

**Acceptance.** A user can perform details-only editing without being pushed toward analysis. Timing focus provides substantially more visible rows. Switching views retains lyric drafts, timing selection, review filter, and player position.

## UX-09 — Improve word correction, validation, and keyboard access

**Problem and evidence.** The Gantt chart is useful, but exact controls make the dialog too tall (UX-01), numeric errors appear in one general message label, and `apply_words` identifies categories of invalid data without selecting the offending word. `WordTimeline` has a strong focus policy and an accessible name but no keyboard editing handlers or per-word accessible representation. Numeric editing already provides an alternative, though it is hidden by default. Cancel discards the draft, including automatic spacing estimates, without a draft-change close check.

**Suggested behavior.**

- Keep the chart central and expose a compact selected-word inspector with Word, Start, End, Duration, and provenance. Keep the full exact table available in a collapsible/scrollable area.
- On validation failure, identify the first invalid word and boundary, scroll it into view, and explain the correction: for example, **Word 4 starts before word 3 ends at 00:24.500**. Highlight both involved spans for overlap.
- Continuously show incomplete/invalid draft state without modal errors. Keep Apply reachable; if disabled, give a persistent reason.
- Add explicit keyboard selection and boundary nudging, with documented coarse/fine increments. Every chart edit must have a keyboard/numeric equivalent. Report joins that move a neighbouring boundary.
- Add **Previous line** and **Next line** within word review, with Apply/Discard handling for the current draft; preserve the bounded playback window for each line.
- On user edits followed by Escape/window close/Cancel, offer to keep editing or discard. Avoid a confirmation for merely opening the editor and viewing automatic estimates without user changes.
- Keep estimates visibly identified even during active playback, when their rectangles currently turn green.

**Implementation entry points.** `timing_editor.py`: `WordTimingDialog` rendering, validation, lifecycle; `word_timeline.py`: interaction/accessibility. Coordinate draft history with UX-06 and review semantics with UX-04.

**Acceptance.** Repair an overlap and an incomplete word using only the keyboard. The invalid word is identified precisely. Apply/Cancel remain visible on smaller screens. Moving to another line never silently loses a manually edited draft or plays outside the new review window.

## UX-10 — Preview metadata changes and let users choose fields

**Problem and evidence.** `apply_candidate` replaces every nonempty proposed field, so choosing a release can overwrite manual title/artist/album choices before the save review. In an app aimed at edited versions, the release's canonical title can erase a useful edit suffix. The combined chooser already offers details-only when a cover is unavailable, which should be retained.

**Suggested behavior.**

- Show **Current → Proposed** for each changed field before applying a candidate. Provide per-field selection, plus **Fill empty fields** and **Use selected fields**.
- Preserve nonempty current values by default unless the user explicitly selects their replacements. Remember whether the artist/title search terms are lookup hints or values to write.
- Offer artwork independently from text, even when a cover is available. A user should be able to take details while retaining an existing cover.
- Retain selection through the optional extra release-details request. Later-arriving fields must obey the same selected-field policy; they must not overwrite an edit made after the first proposal.
- Mark changed fields subtly and offer **Revert field** to the last saved value. Add lightweight date/track examples without forbidding legitimate free-form ID3 values.
- Add a clear **Remove cover** action; the save layer supports artwork removal but the main UI exposes search and replacement only.

**Implementation entry points.** `ui.py`: `choose_release`, `apply_candidate`, metadata form, artwork controls; `artwork_dialog.py`; `tags.py`: `changes` for comparison. Reuse the existing proposal/save boundaries.

**Acceptance.** Selecting a release can fill album/date/track while preserving a manually edited title and cover. Extra details cannot override unselected fields. Reverting or removing artwork updates dirty state and the eventual save preview accurately.

## UX-11 — Improve candidate comparison and lookup recovery

**Problem and evidence.** The lyric chooser uses the generic `CandidateDialog`, with an unfiltered list, a preview, and **OK**. There is no current-vs-selected lyric comparison or explicit replacement explanation. Metadata and lyric searches use the editing fields as search hints. The artwork chooser already has filtering and clear unavailable-cover states, but stores every failure as unavailable and offers no retry for a transient failure. Its metadata Apply action remains disabled while a cover is loading.

**Suggested behavior.**

- Use task-specific labels: **Use these lyrics**, **Apply selected details**, **Use this cover**. For lyrics, show line count and a comparison against the current draft when one exists. Replacement must be undoable and its timing impact visible.
- Let users adjust artist/title lookup terms in the chooser or a small search panel without rewriting their MP3 metadata. Show query and provider so a failed search is understandable.
- Add candidate filtering, a clear-filter action, and a useful empty state. Preserve the existing lyric preview and the artwork chooser's visible-row preview loading.
- Distinguish a confirmed missing cover from network/timeout/invalid-image failure. Provide **Retry selected preview** for retryable failures and keep cached successful previews.
- In details mode, allow **Use details only** while the cover is still loading. Make the choice stable; a late preview must not silently change what is applied.
- Present edition facts prominently where the provider supplies them. Describe a displayed percentage as text match/support rather than verified recording identity.

**Implementation entry points.** `ui.py`: `CandidateDialog`, `find_metadata`, `find_lyrics`, `choose_lyrics`; `artwork_dialog.py`: selection/failure/retry state; `providers.py`: available candidate details and error classification.

**Acceptance.** An unsuccessful lookup can be corrected and retried without altering edited metadata. A temporary preview failure can recover without closing/reopening the chooser. Choosing replacement lyrics clearly shows affected work and can be canceled/undone. Slow covers do not block details-only selection.

## UX-12 — Use a structured save review and explicit success state

**Problem and evidence.** `save` currently puts changed fields, unmatched/uncertain counts, SYLT information, JSON guidance, ID3 version, and audio preservation into a `QMessageBox`. Timed lyric details are in its expandable text. This is useful information but difficult to compare or scan on a small screen. The uncertainty count has different rules from `needs_review`, so estimated/review status can disagree. Success is principally reported in the status bar; `load_track` hides the operation panel.

**Suggested behavior.**

- Build a resizable review dialog with the target filename/path at the top, changed-field rows, artwork before/after thumbnails, and a concise lyric summary. Use the shared review summary from UX-04.
- Clearly distinguish what is embedded in the MP3 (metadata, cover, plain lyrics, line starts), what is retained only in application storage (rich word data), and what a portable export preserves. Keep low-level ID3 details in an expandable technical section.
- Show intentionally excluded lyrics separately from unresolved omissions. Allow **Back to review** without losing changes. Do not forbid a deliberate partial save.
- Keep one explicit **Save changes to this MP3** action with Cancel. Make long comparisons scroll while actions remain visible.
- After completion, show a compact persistent confirmation naming the saved song and offering timing export if useful. If the MP3 succeeded but rich timing storage failed, show a distinct recoverable warning and direct export action.

**Implementation entry points.** `ui.py`: `save`, `saved`, job result handling; `tags.py`: `changes`. Continue delegating the actual file write/verification to `save_track`.

**Acceptance.** The review names the selected target, accurately reflects all changes and review counts, and fits a small screen. Cancel preserves edits. Success is visible without reading a transient status bar, and cache failure does not masquerade as MP3 failure or discard word data.

## UX-13 — Make loaded-song management and closing clearer

**Problem and evidence.** Song-list items have a fixed 76-pixel size hint while their text wraps. The rendered long-title example elided content before the artist could be seen. There is no search, remove/close-song action, or recent-file menu. Multi-file opening reads all files in one list comprehension; one failed read prevents delivery of the successful tracks. The unsaved-close prompt only offers Discard/Cancel. Closing during a job cancels it and asks the user to close again later.

**Suggested behavior.**

- Render a clear title and artist plus a separate small unsaved/status indicator, with deliberate elision and the full path available in a tooltip/detail view. Include filename/path to distinguish duplicate titles where needed.
- Add **Search loaded songs**, **Close this song**, and **Close saved songs**. Closing a dirty song must offer save/recovery/discard choices and retain other songs.
- Add recent MP3 locations and remember the last used open/export directory. Keep MP3 support explicit; do not imply that folders or other formats already work.
- For multiple selected files, return successes plus per-file failures. Show a summary with Retry failed files rather than throwing away the successfully read files.
- On exit, show the unsaved songs and offer **Save selected**, **Keep recovery drafts** once UX-05 exists, **Discard**, and Cancel. Save each song through its ordinary review and verified write path; handle partial failure visibly.
- For exit during a job, record a pending-close request, cancel safely, and then finish the normal unsaved-work flow. Do not require a second close gesture, and do not destroy live workers.

**Implementation entry points.** `ui.py`: `refresh_song_list`, `open_paths`, `select_song`, `confirm_discard`, `closeEvent`, `job_finished`; `session.py`. Changes here must respect the existing rule that ordinary Save affects only the selected song.

**Acceptance.** Long titles still expose artist/status; duplicate titles can be distinguished. Two valid files load even if a third is unreadable. Removing one song retains the other drafts. Canceling an exit/save sequence keeps unsaved work. A close requested during analysis waits for safe cancellation and then completes the normal close decision.

## UX-14 — Give operations specific names, outcomes, and recovery actions

**Problem and evidence.** Non-analysis jobs use **Working on the selected song** and a generic **Operation completed** message even for opening files or searching. Failures combine a retained job panel with a modal **Operation failed** message. No-result and selection-lock explanations live in the status bar. Errors from result handlers can follow a generic completed display. The app already measures elapsed time, progress updates, stalls, and cancellation; retain that honesty.

**Suggested behavior.**

- Give `start_job` operation context: **Opening 3 songs**, **Searching lyrics**, **Loading artwork**, **Saving Example Song**. Capture the target identity and appropriate retry action.
- State the actual outcome after the handler finishes: **No lyrics found**, **Details loaded**, **Save completed**, **Analysis canceled**. Distinguish provider absence, transient failure, invalid input, and partial success.
- Use inline feedback for recoverable search/playback errors and provide actions such as **Retry**, **Edit search**, **Paste lyrics**, or **Open settings**. Keep detailed diagnostics expandable/copyable and exclude credentials/full lyric content where inappropriate.
- Put **Playback follows the selected line — pause to choose another** beside the table while playing, with a Pause action. A disabled filter or blocked row click should have a persistent explanation, not only a status-bar message.
- Make completed progress compact so it does not consume the review area. Keep current analysis, real activity, elapsed time, cancel, and stall information visible; do not invent time-to-completion estimates.

**Implementation entry points.** `ui.py`: job lifecycle, `error`, playback lock explanation; `jobs.py`: operation metadata if needed. Keep per-song operation identity even if later navigation/concurrency is introduced.

**Acceptance.** A canceled lyric search and a canceled analysis display distinct outcomes and useful next actions. A failed result handler cannot leave a misleading success banner. Playback locks are understandable before an attempted edit. Retry is disabled while a cancellation is still finishing.

## UX-15 — Make settings clearer and validate within the dialog

**Problem and evidence.** Settings expose raw values such as `whisper-attention`, `cpu`, `rocm`, and `en`; CTC settings remain visible for the attention backend. The cache path lacks a folder picker. The fixed language choices are `auto/en/de/fr/es/da`; a configured supported language outside that list cannot be represented faithfully by the noneditable combo. Validation and persistence happen after the dialog has already accepted, so a failure closes the form and shows a general error. Optional installation is explained mostly through README/help text.

**Suggested behavior.**

- Use friendly labels with stable internal values: **Automatic language**, **English**, **CPU**, **AMD GPU (ROCm)**, and a short explanation of each alignment method. Preserve unfamiliar valid configured values instead of silently selecting a different item.
- Show basic model/language/device controls first; reveal CTC model IDs and vocal separation in Advanced. Disable or hide controls that do not apply to the selected backend.
- Add a folder picker and inline cache-path validation. Move review threshold next to the review explanation; explain that it changes suggested review flags, not measured model support.
- Validate before accepting and keep the form open with focus on the invalid field. Saving settings must either succeed or leave the attempted values available for correction.
- Add a local readiness view for optional dependencies/device/model availability. Offer the offline manual path when AI is unavailable. Run expensive capability checks in a worker; this UI must not trigger a model download or installation merely by opening settings.
- Show model download/storage information where reliable local/configured information exists. Provide a separate, explicitly invoked setup flow if installation is later added.
- Distinguish disposable caches from corrected timing and recovery drafts. A future cache-cleanup UI should preview categories/sizes and preserve user work by default.

**Implementation entry points.** `ui.py`: `SettingsDialog`, `edit_settings`; `config.py`; optional readiness checks around `alignment_process.py`. Coordinate storage presentation with UX-05/18.

**Acceptance.** Opening/accepting Settings preserves a configured language outside the initial short list. Invalid paths keep the dialog open. CTC controls only appear when relevant. Opening settings performs no downloads and does not stall the UI. AI unavailability still leaves manual workflows usable.

## UX-16 — Improve accessibility and visual status semantics

**Problem and evidence.** The custom switch retains checkbox keyboard semantics and draws focus, which is good. Metadata labels are created as separate `QLabel`s without buddies. The main playback slider and lyric editor have no explicit accessible names. The stylesheet removes table/list outlines, and several status distinctions depend heavily on green/amber backgrounds. `ThemeEvents` colors RejectRole and NoRole buttons red, making ordinary Cancel visually destructive. `apply_theme` forces a light palette/font and uses many hard-coded colors, including row painting.

**Suggested behavior.**

- Give fields, playback sliders, lyric editors, tables, and unnamed switches clear accessible names/descriptions. Associate labels with fields and establish a predictable tab order.
- Keep text/icon status alongside color, including on selected/active rows where the highlight currently masks the review tint. A selected estimated word still needs a visible estimate label.
- Use red for genuinely destructive choices such as Discard edits or Remove cover; ordinary Cancel should be neutral. Distinguish a ready step from an explicitly reviewed result.
- Draw an unmistakable keyboard focus indicator for lists, tables, and the Gantt chart. Check it against selected-row styling.
- Support increased font size and reduced motion; rely on UX-01 for reflow. Offer System/Light/Dark as a later presentation preference if desired, backed by shared color tokens rather than scattered replacements.

**Implementation entry points.** `theme.py`: `ThemeEvents`, palette/styles, `ToggleSwitch`; `ui.py` labels/control construction and row painting; `timing_editor.py` and `word_timeline.py`. The chart requires keyboard/numeric equivalents from UX-09 as well as labeling.

**Acceptance.** Complete loading, lyric entry, timing selection/correction, and saving using a keyboard with visible focus. At increased font size, controls remain usable. Verify names and statuses with an actual screen reader before claiming full accessibility. Cancel is visually distinct from Discard, and review status remains readable when selected.

## UX-17 — Add precise playback tools before a waveform

**Problem and evidence.** The main player exposes Play/Pause, a full-song slider, and elapsed/duration text. Volume is set to 0.8 with no UI control. Dialogs already provide ±2-second seeking and bounded playback, but there is no repeat-section loop or playback-rate control. A waveform is deferred in the README. This is an enhancement, not a claim that audio analysis is incorrect.

**Suggested behavior.**

- Start with shared volume/mute, short backward/forward seeks, an exact seek field, and **Replay selected line/section**. Make the distinction between seeking audio and nudging a timestamp explicit (UX-02).
- Add optional **Loop this section** for word review and playback-rate choices for listening if supported correctly by the current backend. Pausing at the section end remains the default.
- Later add a cached, zoomable amplitude waveform with the playhead, selected review window, and line/word boundaries. Decode/build overview data asynchronously; never block UI rendering or upload the audio.
- Use the waveform as a listening/navigation aid. Do not present peaks as proof of lyric or word boundaries. Keep the existing slider/numeric controls available and preserve neighbouring-line bounds.

**Implementation entry points.** `ui.py` player card, `timing_editor.py`: `PlaybackDialog`; optional new waveform widget/data layer. Share player preferences rather than duplicating inconsistent controls across dialogs.

**Acceptance.** A user can repeatedly listen to a difficult word section, control loudness, and seek precisely without altering timing accidentally. Looping never escapes the review window. Waveform generation is cancellable and MP3 bytes remain unchanged.

## UX-18 — Make rich timing preservation portable and restorable

**Problem and evidence.** JSON export preserves current word data and untimed lines, but there is no import action. MP3 SYLT and ordinary LRC contain line starts only. Rich saved timing restoration depends on an app-cache snapshot matching file identity, lyrics, and embedded line times. Copying a song to another computer or clearing that storage can therefore lose access to corrected word ends. The README explains this, but an exported JSON currently cannot be reopened in the app.

**Suggested behavior.**

- Add **Open timing project** or **Import timing JSON**, validating a versioned schema and source identity before previewing what will be applied. Reuse validation rather than accepting arbitrary timestamps.
- Define whether a project is just timing data or a complete portable draft. If complete, include artwork/reference strategy, display lyrics, proposed metadata, review/exclusion state, and stable occurrence IDs. An audio path/reference is not a reason to embed or duplicate the MP3 automatically.
- Let a moved source file be relocated. A mismatched hash/duration needs an explicit conflict/compatibility flow; never silently attach old word timing to a different edit. Keep the existing strict restore path unless a separately validated compatibility mechanism is implemented.
- Make export modes understandable: **Save edits into MP3**, **Export line lyrics (.lrc)**, **Save timing project**. Explain what each preserves and whether it can be reopened.
- Keep recovery (UX-05), corrected timing storage, and portable projects separate from disposable model/network caches. Share their serializers and migration rules where possible.
- Consider LRC import only as a later convenience. External timestamps must retain an imported/unreviewed provenance and require listening against this audio.

**Implementation entry points.** `timing_data.py`: export/restore schema and validation; `ui.py` file menu/import workflow; `session.py` and `model.py`. This builds on UX-04/05 rather than creating a competing persistence format.

**Acceptance.** Export a corrected timing project and reopen it with all words, untimed/excluded lines, display text, and provenance preserved. Wrong-version, malformed, overlapping, out-of-bounds, or mismatched-source data produce a clear recoverable response and leave the working song unchanged. Importing a project does not write its MP3 until explicit Save.

## Suggested implementation order

1. **Fix concrete interaction and layout defects:** UX-01, UX-02, UX-03, UX-07. These have observable, bounded reproduction cases and make the present workflow safer immediately.
2. **Define the shared state model:** UX-04 and UX-06. Decide stable occurrence IDs, review/exclusion fields, command boundaries, and schema versioning before persisting them.
3. **Protect ongoing work:** UX-05, followed by portable restoration in UX-18 when chosen. Keep recovery and MP3 save semantics clearly separate.
4. **Improve everyday review and lookup:** UX-08 through UX-16, with responsive/accessibility checks throughout. Reuse shared summaries, operation context, and review policies rather than adding local variants.
5. **Expand audio navigation:** UX-17 after the existing keyboard, timing, and persistence behavior is stable.

For a small first implementation, take UX-02 and UX-03 as separate fixes, then UX-01. Do not wait for a complete redesign or project-file system to correct these confirmed problems. Waveform, dark mode, folder/batch processing, and full karaoke presentation should not be prerequisites.

## Guidance for a future implementing LLM

- Read the current source and local instructions before changing anything; this review describes the inspected version and function names can move. Preserve the existing PySide6 architecture and service boundaries unless a chosen change requires otherwise.
- Select an explicit subset of backlog IDs and state its scope. Distinguish layout/copy changes from model/schema changes; migrate persisted data when fields change.
- Use [ui.py](src/tracksmith/ui.py), [timing_editor.py](src/tracksmith/timing_editor.py), [artwork_dialog.py](src/tracksmith/artwork_dialog.py), and [theme.py](src/tracksmith/theme.py) for presentation. Use [session.py](src/tracksmith/session.py), [model.py](src/tracksmith/model.py), and [timing_data.py](src/tracksmith/timing_data.py) for shared working state. Keep file-write safeguards in [tags.py](src/tracksmith/tags.py).
- Extend meaningful coverage in `tests/test_ux.py`, `tests/test_ui.py`, `tests/test_playback_selection.py`, `tests/test_stamping.py`, `tests/test_timing_editor.py`, `tests/test_word_timeline.py`, `tests/test_word_window.py`, and `tests/test_artwork_dialog.py` as relevant. Prefer regression cases for the observed failures and state transitions over tests that mirror widget construction.
- Render affected states with synthetic fixtures at large and laptop sizes. Include long titles, repeated lyrics, untimed/cut lines, estimates, pending text, two unsaved songs, errors, active/completed/canceled jobs, and exact-word controls. Check keyboard focus and visible action reachability, not just widget existence.
- Preserve audio-byte verification and external-file identity checks for any change touching save/import/recovery. Do not overwrite real user songs in tests.
- Update README/help when behavior changes, especially shortcuts, review labels, pending lyric application, persistence, and export capabilities. Avoid documenting a feature before it exists.

The artifact is intentionally self-contained: the observations and reproduction cases are described here, and implementation should not depend on temporary screenshots or audit scripts surviving on this machine.
