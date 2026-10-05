# UI/UX implementation status

The user approved the full [suggested-improvement.md](suggested-improvement.md) backlog. Changes were made in `src/song_metadata_enricher`; generated `build/lib` files were not edited. The app still opens music read-only and saves only through its existing explicit, verified, atomic MP3 write path.

| Scope | Implemented behavior |
| --- | --- |
| UX-01: smaller screens | Scrollable welcome/workspace/forms/help, wrapping toolbars, collapsed details, pinned confirmation actions, responsive cover preview, restored geometry/splitters. Laptop and larger-font render checks described below. |
| UX-02: keyboard focus | Table-scoped timing shortcuts; text, sliders, lists and focused buttons retain their own actions. Dialog slider arrows seek playback. Native typing Undo stays local. |
| UX-03: guided stamping | Shared neighbouring-line bounds, visible valid interval, availability feedback, held-Enter guard and unchanged target on rejection. |
| UX-04: review semantics | Stable occurrence IDs, separate line/word listening flags, independent support/provenance, persistent Not sung exclusions, temporary Skip, shared summaries and legacy migration. |
| UX-05: recovery | Debounced atomic drafts with original save baselines and pending text, startup restore/relocate, strict identity validation, retained failed/skipped restores, explicit discard and visible recovery status. Declined/canceled/corrupt recovery cannot be overwritten by a new workspace. |
| UX-06: undo | Bounded per-song Undo/Redo for committed metadata/artwork, timing, review, lyric, import and analysis changes. Guided edits join main history; word drafts keep local history until Apply. Snapshot guards protect later edits. |
| UX-07: pending lyrics | Visible stale-table banner and disabled timing edits; Apply lyric changes preserves unchanged occurrences, previews removed timings and is reversible. Formatting hints are visible beside lyric entry. |
| UX-08: workspace focus | Clickable section navigation and Details/Lyrics/Timing focus controls; timing focus hides both metadata and source lyrics. Save remains consistently in the footer and supports tags-only editing. |
| UX-09: word correction | Selected-word numeric inspector with duration/provenance, exact table, keyboard nudges, continuous validation, precise offending-word selection/conflict highlighting, joined-boundary feedback, Apply + previous/next, draft-discard protection and visible estimate labels during playback. |
| UX-10: proposals | Current/proposed field review, opt-in replacements of nonempty values, fill-empty behavior, explicit extra-release fill choice, independently selected cover replacement and saved-value cues/revert actions. |
| UX-11: search choosers | Filters, lyric line counts and differences against current text, independent lookup terms, retryable failed covers, separate missing/transient cover states and details-only choice while covers load. |
| UX-12: save review | Scrollable changes, target identity, saved/proposed artwork, shared timing/review/exclusion counts, storage explanation, fixed Back/Save/Cancel actions and retained verified-success feedback. |
| UX-13: loaded songs/close | Search, artist/status, elided long titles with full identity available, recent files, partial-open retry, close one/close saved songs, per-song exit saves, recovery/discard/cancel and safe cancellation before closing. |
| UX-14: operations | Operation-specific titles/outcomes, inline retry/search feedback, handler-error failure outcomes, persistent playback-lock explanation, compact completed progress and disabled retry during cancellation. Existing real progress/activity reporting remains. |
| UX-15: settings | Friendly labels, preserved custom languages, optional advanced fields, backend applicability, cache picker, validation/persistence before acceptance, local package/checkpoint readiness without imports/downloads and explicit disposable-cache management. |
| UX-16: presentation/accessibility | Field buddies and accessible names/descriptions, table/list/chart focus, numeric/chart keyboard equivalents, status text alongside colors, neutral Cancel, destructive Discard, Light/Dark/System, scalable fonts and reduced motion. |
| UX-17: playback | Volume/mute, exact seek, short seeks, replay selected section, speed choices, bounded section looping and optional cached local waveform with zoom, playhead, section/line markers and keyboard seeking. |
| UX-18: portable work | Complete schema-2 JSON projects with artwork/metadata/lyrics/IDs/word/review/exclusion state, schema-1 migration, validated worker import with strict audio identity and reversible application; basic LRC import remains explicitly unreviewed. Corrected timings live outside disposable caches. |

## Storage and migration

- Settings: `$XDG_CONFIG_HOME/song-metadata-enricher/settings.json`.
- Recovery, presentation and corrected timing snapshots: `workspace_directory`, defaulting to `$XDG_DATA_HOME/song-metadata-enricher` (`~/.local/share/song-metadata-enricher`).
- Existing valid corrected-timing snapshots are read from the legacy cache and copied to protected storage when opened. Automatic cleanup excludes legacy snapshots and model weights too.
- Portable projects reference the MP3's exact content hash/duration; they include artwork but do not duplicate audio. Load a relocated byte-identical MP3 before importing. Different edits and externally modified files are rejected, leaving the current draft unchanged.
- Review flags do not modify model support or unchanged word provenance. Intentional exclusions retain plain lyrics but leave synchronized outputs and review queues.
- Undo history is per-run and bounded to 80 commands per song. Recovery/project formats preserve draft content, not historical commands. Closing Discard removes current recovery drafts, while unresolved drafts from a prior workspace remain retained until explicitly discarded.

## Verification

Final verification on 2026-10-04: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q` — **174 passed**; `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` — passed. Qt playback tests ran with access to the desktop audio service; all music fixtures were generated test tones.

Regression coverage includes focus routing, Enter repeat/bounds, pending-text protection, exclusions and separate review, per-song Undo/Redo, read-only recovery/import, original save baselines, corrupt/declined/failed recovery, identity/boundary validation, selective proposals, custom-language settings, partial opens, job cancellation/close, protected cache cleanup, local waveform generation/cancellation, saved exclusion restoration, large text, word-draft cancellation and section looping. The full suite retains the existing ID3/audio-byte verification and mocked alignment/provider coverage.

Synthetic rendering checks cover welcome, loaded/review/completed-job workspaces, lookup choosers, artwork, Settings, guided timing, exact word controls and structured save. The main workspace fits 1280×720 at default, 14-point and 20-point text; exact word dialogs fit 1024×650 with pinned actions and scrolling content. Long titles and dark-mode switch/toolbar contrast were inspected. These are offscreen Qt checks against generated tone audio, not music evaluation.

No real song was modified for these checks. Live provider availability, real-song listening quality, screen-reader compatibility, hardware GPU inference and audible quality of all playback speeds were not independently validated in this UI work. Readiness reports local package/checkpoint presence; actual device/model loading remains checked at analysis time. The waveform represents amplitude and must not be treated as lyric evidence. Full karaoke presentation, calibrated model confidence and folder/batch scheduling remain outside the original backlog.
