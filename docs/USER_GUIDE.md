# Tracksmith detailed guide

Detailed workflow notes and development observations. For current setup and a feature overview, see the [README](../README.md). Machine-specific validation notes describe the original development environment.

A Python/PySide6 desktop prototype for local MP3s, editing individual songs and processing folders of MP3s, especially edited music whose downloaded lyric timestamps are wrong. Opening a file is read-only. Metadata changes never re-encode its audio.

## Run

On this computer, double-click **Tracksmith** on your desktop, or find it in the application menu. It opens without a song loaded. Click **Load song** in the left sidebar to choose one or more MP3s. Select a loaded song in the list to show its details and lyrics.

The project also includes `start.sh`: run `./start.sh` from this folder, or launch it through your file manager. It uses the project's virtual environment automatically, so no environment activation or song argument is required. The desktop shortcut points to this folder; update it if you move the project.

### First-time installation on another computer

Python **3.12 is recommended**, including on Arch/CachyOS. The GUI requires Python 3.11+; optional ML wheels may lag Arch's newest system Python. A Python 3.12 virtual environment has been created in this workspace.

```bash
sudo pacman -S ffmpeg chromaprint libsndfile libxcb libxkbcommon-x11
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
tracksmith
# or
python -m song_metadata_enricher /path/to/song.mp3
```

FFmpeg supplies decoding and compressed-audio verification. `ffprobe` is available for diagnostics; Mutagen reads MP3 technical information directly. Chromaprint's `fpcalc` is optional for fingerprint lookup. Qt Multimedia uses the bundled FFmpeg backend; a working desktop PipeWire/PulseAudio service is needed for audible playback. On a minimal installation, Qt's xcb plugin can also require `xcb-util-cursor`. Wayland uses the PySide6 wheel's Qt Wayland plugin. Do not run the GUI inside an environment that blocks the desktop audio socket.

The base installation is roughly **500–800 MB**, depending on Python wheels. AI is optional and adds several GB.

## Workspace and review controls

The compact left **Your songs** list keeps each song's working draft, shows title and artist on two lines and marks unsaved changes with a leading dot, and offers search, recent files, **Close this song**, and **Close saved songs**. Loading several files keeps successful reads even if one fails; the result panel offers Retry for failures. The welcome page and workspace scroll on smaller screens. Confirmation actions remain outside dialog scrolling areas.

The default **Lyrics & timing** tab keeps the lyric editor, playback and timing review together. **Song details** contains editable tags, a larger cover preview and file information including duration, bitrate, sample rate, channels, tag version and saved timing status. The **View** menu switches tabs and focuses the lyric editor or timing table. **Review & save to MP3** stays in the footer and works for details-only edits too. Geometry, splitters, directories and volume are remembered. **View → Appearance** offers Light, Dark, System, 9–20 point text and reduced switch animation. Toolbars wrap as space decreases; longer forms and help pages scroll. Cancel is neutral; destructive Discard/Remove actions use red. Accessible field names, label buddies, focus outlines and numeric alternatives support keyboard use; actual screen-reader compatibility still requires evaluation.

**Hide reviewed lines** hides lines only when their line timing and any existing word timings have explicit listening review. **Review decision…** offers **Mark line reviewed**, **Mark all words reviewed**, **Not sung in this version**, and **Reopen review**. Marking a line does not certify its words. Retiming a line preserves word support/provenance and reopens word review. Intentional exclusions remain in plain lyrics, leave the outstanding review queue, and are omitted from synchronized output. Low model support and spacing estimates have separate text labels. The table, footer, result summary and save dialog share review counts.

Completed operations have compact, dismissible feedback. Running jobs retain progress, real activity and cancellation controls. Search failures and no results offer relevant Retry/Edit search actions. Playback explains its selection lock beside the table. **Tools → Settings** groups General, Audio analysis and Online sources; advanced alignment options are optional, and invalid input stays in the form.

## Folder and batch workflow

Click **Load folder** in the sidebar or File menu to load every MP3 directly inside a selected folder (including `.MP3`; subfolders are not included). Loading shows an animated indicator while scanning, then progress across the files being read. Saved word timings are restored in the background, and the workspace displays the selected song once rather than rendering every imported song. Use the category selector to view **No embedded lyrics**, **Embedded lyrics but missing line timings**, **Embedded lyrics and line timings but some missing word timings**, or **fully timed**, with live counts and matching category colors: rose for no lyrics, amber for missing line timings, blue for missing word timings, and green for fully timed. Song rows retain their category background when selected, with an outline identifying the selection. Categories reflect saved lyrics and timing data; unsaved drafts do not change a song's category. Any missing non-excluded line start belongs to the second category. The third category requires all line starts but lacks complete valid word spans. “fully timed” requires every non-excluded sung line and word to be timed, independently of listening review. Word timings come from saved application timing data; standard line timings and lyrics are embedded in the MP3. Search narrows the visible list; batch actions use the selected category, independently of search.

**Batch: find and save lyrics** looks up each saved song using tagged artist/title, falling back to its filename. Results are ranked by normalized artist/title similarity, then album match. Weak matches are skipped. Existing lyrics are kept by default; the batch dialog offers replacement, preserving unchanged timed occurrences and skipping replacements that would remove timed or excluded lines. Automatic selection is a best guess; inspect downloaded lyrics afterward.

**Tools → Automatic timing → Batch: generate line timings** processes only songs in the category with saved sung lyrics and zero existing timestamps. It uses the configured local alignment backend and embeds successful line timings in each MP3. Partial and full timing are protected from replacement. Generated timings remain unreviewed, and partial line results stay in the missing-line-timings category.

**Tools → Selected category: add missing album covers**, also available by right-clicking the category selector, processes only the selected category (choose a category other than All categories). Search does not limit this queue. It skips existing embedded pictures and unsaved edits. Choose **Find covers to review** to search without changing any MP3s. By default it needs no account, application key, or extra installation: MusicBrainz lookup uses artist/title tags or an `Artist - Title` filename. Close name matches, recording variants, and short clips can receive suggestions. Search prefers original studio albums, then other release types, and tries another edition if a cover is missing.

The **Review album covers** screen shows a scrolling grid with square cover previews, local song/artist names, filenames, and the suggested album and recording names. Text wraps below the image, and cards grow to fit long titles. Every cover starts unreviewed. Click **✓ Accept** on the covers you want: the button turns gray and the accepted status turns green. Click the same button again to undo acceptance. **Accept all** selects every suggestion without saving or closing the review; individual selections can still be undone afterward. **Save accepted covers to MP3s** saves only the accepted images; every other suggestion is skipped automatically. Closing/cancelling the review saves nothing. Cancelling a search retains already-found previews for review. Names cannot verify audio identity, so inspect suggestions for random clips or incorrectly named files.

The batch dialog's optional **Also verify the audio identity** checkbox enables the stricter AcoustID fingerprint check. This requires an application key in Settings → Online sources plus `fpcalc`, a fingerprint score of at least 0.95, and no competing recording within 0.10 of the best score. These thresholds are conservative heuristics, not identity probabilities; some valid edited songs will be skipped. AcoustID is free for non-commercial use, but registering an application is necessary to obtain its lookup key.

With optional audio verification enabled, cover lookup also requires a unique earliest official studio album by the same artist, known release chronology, agreement with an existing album tag, an original-year edition, and a single approved front image from Cover Art Archive. This stricter mode skips short clips, recording variants, and uncertain original albums. It still presents the images for review before saving. Neither mode guarantees a particular regional pressing's artwork. Use the individual cover chooser for other editions.

Batch actions list their target files before starting; covers have the additional image review stage. Songs with unsaved or unapplied edits are skipped. Accepted cover saves recheck the file and preserve existing pictures added since the search; externally changed files are skipped. Only artwork changes: tags, lyrics, timing and audio are preserved. Saves use the same atomic tag/audio verification as individual saves; rich timing data is also stored in application storage. Failures do not stop subsequent files. Cancel stops the remaining save queue while retaining completed saves. The result panel lists saved, discarded, skipped and failed files. Batch saves reset edit history for the files they update.

## Manual workflow

1. Click **Load song**, use **File → Load songs**, or drop one or more MP3s onto the window. Selecting an already loaded path uses its existing draft. Opening a file is read-only.
2. Check tags and artwork. Existing plain lyrics and millisecond SYLT line starts load automatically. Downloaded and imported timings require listening review. Metadata proposals show current/proposed values and let you choose individual replacements; nonempty values and the current cover are kept by default.
3. Paste/edit lyrics, then **Apply lyric changes**. The old timing table is disabled while text is pending. Rebuilding preserves unchanged repeated occurrences and previews any existing timed occurrences that would be removed. Cancel preserves the prior timings and pending draft; Apply can be undone. Blank lines and `[Verse]` headings stay in display text but do not become sung timing rows. Repeat each chorus occurrence explicitly.
4. Play the file and press **Enter** or **Stamp** when the indicated untimed line begins. The green **Enter → line…** label identifies the one selected/highlighted row. During playback, selection follows the audio; pause to choose another row. Enter stamps even after using playback or other main-window controls; text fields and dialogs keep their normal Enter behavior. Ordinary Enter never replaces an existing time, and held Enter never stamps several lines. Surrounding anchors bound valid stamps. **Auto-advance** skips existing timings; later sections wait until reached. To replace a timestamp, pause and select the line, use **Clear time**, then play and press **Enter** at the new start. **Review decision → Not sung in this version** records an intentional exclusion.
5. While paused, select a timed row to seek; double-click time/text to edit, or use **Clear time**. **Word timings** opens precise word correction. Listen and use the separate line/word review decisions. **Review & save to MP3** previews changes and writes only the selected song using the verified atomic save path.

| Key | Action when the timing table has focus |
| --- | --- |
| Space | Play/pause |
| Up / Down | While paused: previous/next line and seek if timed |
| Left / Right | While paused: selected timestamp −/+ 100 ms |
| Shift + Left / Right | While paused: selected timestamp −/+ 1 second |
| Enter | Stamp the indicated untimed line; existing times are protected |
| Delete | Clear the selected line’s timing while paused |
| Ctrl+Z / Ctrl+Shift+Z | Undo / Redo the song edit |
| Ctrl+O / Ctrl+S | Open / review save (window actions) |

Text editors retain their native typing/Undo behavior, sliders navigate playback, the song list navigates songs, and Enter stamps and Delete clears the selected line timing from main-window controls too; text fields and dialogs retain their normal Enter behavior. The word chart has its own selection and timing keys. Per-song Undo/Redo covers direct timing edits, nudges, clearing time, accepted word drafts, metadata/artwork changes, proposals, lyric rebuilds, imports and analysis replacement. Large operations are one history step; undo after saving changes the working draft without rolling back the MP3 automatically. Later untracked or pending changes are protected from an unrelated older Undo/Redo. History is bounded and lasts for the current run.

Timestamps retain millisecond precision. LRC rounds to centiseconds and sorts chronologically. Untimed and intentionally excluded lines stay in plain lyrics but are omitted from LRC/SYLT. Editing lyric text clears its words and invalidates nonmanual timing; unchanged repeated occurrences preserve their distinct data. AI support is heuristic, **not a correctness probability**.

Playback has a single row with play/pause, a seekable progress bar, current time / song duration, and volume. **View → Show waveform** decodes a cached local amplitude overview in a cancellable worker, with playhead, section and line markers, seek and zoom. It never uploads or modifies audio and is a navigation aid, not evidence of lyric boundaries.

### Recovery and closing

Meaningful edits save a debounced, atomic recovery draft to application data storage, separately from disposable caches. Drafts include loaded-song order, original save baseline, metadata/artwork, pending display text, lines/words, review/exclusions, selection and playback position. The footer distinguishes unsaved MP3 edits from a saved recovery draft.

At startup, **Restore previous workspace** offers the previous draft date and song count. Missing MP3s can be relocated; changed file hashes/durations are rejected. Restored edits remain unsaved. Cancel preserves the old recovery and suspends new recovery writes until **File → Restore previous workspace** or **Discard retained recovery** resolves it. Failed/skipped restores remain retained. A malformed recovery is kept for manual inspection or explicit discard.

Closing offers **Save selected**, **Keep recovery drafts**, **Discard edits**, or **Cancel**. Each selected save still gets its ordinary review; canceled/failed saves leave the window and remaining edits open. Close during a job waits for cancellation. Closing one song keeps other drafts. Recovery never writes changes to MP3s.

### Word timings and karaoke preparation

Automatic analysis produces **word starts and ends first**, then derives line starts from the accepted words. The timing summary reports both counts. The **Words** column distinguishes `N timed`, `Line only`, and `Untimed`; a manually stamped line does not acquire invented word timestamps.

Select a line and click **Word timings** for a **Gantt chart** with one duration rectangle per word. Drag a rectangle or its edges, or use the selected-word Start/End inspector, which also shows duration and provenance. **Keep neighbouring words joined** redistributes shared boundaries and reports when neighbours move. Click the ruler/word to seek. Up/Down selects words; Left/Right moves 10 ms, Ctrl adjusts the start, Shift adjusts the end, and Alt increases the step to 100 ms. Exact timestamp/tap controls remain optional. Zoom and draft Undo/Redo support correction; a whole drag or keyboard nudge is one undo step.

The chart and playback use a fixed **review window**. For a timed line, this runs from its start to the next timed line in audio order. For an **untimed line**, it runs from the previous timed lyric row's start to the next timed row's start: if those are 19 and 23 seconds, the editor stays between 19 and 23 seconds. Missing neighbours use the beginning/end of the audio. Seeking, the playback slider, and **Play this section** stay inside this window; playback pauses at its end, and playing again restarts inside it.

When a line has no word data, the editor **spaces words equally** within these bounds. For an untimed line, it starts the guesses after the previous line's known word/line end when available. Start/end fields let you shorten this estimate range to avoid silence; they cannot widen the review window. You can drag words throughout the review window. Existing AI/manual word spans are kept until you explicitly choose **Space words evenly**. If no lines have timestamps yet, the editor offers the full audio range and leaves words untimed until you choose spacing or use the optional tap controls. Conflicting neighbouring timestamps must be corrected before opening the editor.

Equal spacing is an **editable estimate**, not audio alignment. Estimates remain amber and labelled during playback, retaining their source and support in storage/projects. Checking **I have reviewed all word timings for this line by listening** records a separate review decision without replacing original support/provenance. Editing only a few words does not certify the rest. The draft continuously identifies incomplete, overlapping or out-of-section boundaries; Apply selects the first offending word and highlights conflicting spans. **Apply + previous/next line** commits a valid draft before navigating to a fresh bounded section. User edits followed by Cancel/Escape/close require an explicit discard decision; simply viewing automatic estimates does not. Accepted word changes become one song-level Undo step. **Loop this section** repeats the bounded review window; pausing at its end is the default.

Current **MP3 SYLT and ordinary LRC export contain line starts only**. Explicit MP3 Save also stores full corrected line/word data, IDs, support/provenance and review/exclusions under the protected application-data `timings` folder, keyed to the resulting file bytes. Reopening restores it only when identity, plain lyrics and embedded line times agree. Existing valid snapshots in the old cache location are read and copied into protected storage.

**File → Save timing project (.json)…** exports a complete portable draft: proposed metadata, artwork bytes, display lyrics, every timed/untimed/excluded occurrence, words, provenance/support and review state. **Open timing project (.json)…** validates the version, exact source hash/duration and all boundaries before previewing/applying it. Load the matching MP3 first; a moved copy is valid if its bytes match. Different or externally changed files are rejected. Schema 1 timing exports migrate to schema 2 defaults; imports are undoable and never write an MP3 until explicit Save. JSON projects do not include the audio and are not a standard karaoke-player format.

**Import line lyrics (.lrc)…** supports basic line timestamps, multiple timestamps for repeated lyrics, offsets, untimed text and common metadata headers. Imported timestamps retain unreviewed provenance and must be checked against this local recording. Full karaoke presentation/export remains future work.

**Optional lyric-file export:** File → **Export timed lyrics to a separate file (.lrc)** creates a small text file containing timestamps and lyric lines. Some other players read it beside the MP3. It is not needed when saving lyrics directly into your MP3. Help → **What is a lyric file (.lrc)?** explains this in the app.

## Experimental automatic timing

For a slower word-boundary pass, choose **Tools → Settings → Audio analysis → Alignment method → Whisper + word-start refinement (slower, experimental)**, then run analysis again. This uses the existing Whisper model to mask small portions of each word and check whether its acoustic support falls. It conservatively moves supported word starts later, leaves the first word/line start and every word end fixed, and does not move neighbouring words. No additional packages or model downloads are required. Use Undo to restore the previous analysis; MP3 files change only on Save.

This is an optional improvement for early word starts, not a fix for every singing-alignment error. It can worsen individual words and takes substantially longer on CPU. In a local comparison with 39 manually corrected words across five lines, average start error decreased from 197 ms to 173 ms (18 improved, 8 worsened, 13 unchanged); average end error stayed at 238 ms. Those measurements are a small development sample, not a general accuracy guarantee. Ends of sustained sung words deliberately remain unchanged.

Install CPU PyTorch **before** the AI extra to avoid pulling a GPU-specific default build:

```bash
source .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[ai]'
```

Paste or find known lyrics, check the text, then **Tools → Automatic timing → Analyze timing**. Tools → Settings selects the model, language, device and low-support threshold; advanced options include CTC and vocal separation. Explicit listening review remains independent of this threshold. Default inference is CPU, multilingual Whisper `small`, and the `whisper-attention` known-lyrics alignment backend. `whisper-ctc` remains an optional backend in Settings. Analysis subprocesses default to four CPU threads to avoid oversubscription; `OMP_NUM_THREADS` and `MKL_NUM_THREADS` can override this. `tiny` is useful for installation smoke tests; stronger models generally provide better passage evidence but still struggle with singing.

The pipeline is deliberately conservative:

1. Optionally isolate vocals into a temporary directory.
2. Decode **this local file** to mono 16 kHz PCM using FFmpeg.
3. Transcribe with [Whisper's PyTorch implementation](https://github.com/openai/whisper), including word timestamps. No supplied-lyrics prompt is used: independent recognition is evidence against inventing missing verses. Singing is not discarded solely because a speech-detection score is high; the later text/acoustic checks decide whether it is supported. This fixes a bug that discarded most correctly recognized words in the private music test.
4. Find plausible known-lyric passages in the local transcript. A chronological beam search can skip reference lines and consume repeated occurrences separately. Strong unused matches can recover reordered passages, always capped below the default review threshold.
5. The default uses [Whisper’s attention and dynamic time warping](https://github.com/openai/whisper/blob/main/whisper/timing.py) to force-align **the supplied lyric text** against the local audio. Nearby evidenced lines share a window of at most 30 seconds, in actual audio order, so each short line is not treated as a new utterance. Case and punctuation are retained for the model; section/repetition annotations are removed only from alignment input. Words are partitioned back into their original line occurrences. The optional `whisper-ctc` backend instead uses language-specific [wav2vec2 CTC](https://huggingface.co/facebook/wav2vec2-base-960h) and Viterbi alignment with repeated-letter blank transitions.
6. Derive line times from words. Combine text/recognition support with acoustic support. Missing matches, unsupported characters, impossible paths, or very weak evidence remain untimed. AI analysis has no equally spaced timestamp fallback; estimated equal spacing is available only in the manual word editor.

Whisper attention supports the selected Whisper model’s languages without a separate CTC download. For the optional CTC backend, bundled model choices support English, German, French, and Spanish. German uses [jonatasgrosman's German XLSR model](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-german). Other languages need a compatible character-level `AutoModelForCTC`/`AutoProcessor` model ID in Settings. Unsupported scripts/tokenizers fail visibly. English vocabularies are uppercased only for the model; display text is untouched.

**Limits:** these are speech-trained models, not a proven singing aligner. Screams, stretched vowels, harmonies, and instrumentation can prevent passage discovery. Recognition mistakes can leave sung lines unmatched. Short common phrases need stronger evidence. Identical choruses cannot always be associated with the right textual occurrence after cuts; reorder recovery and ambiguous repeats require listening. Each supplied line can be assigned at most once: add extra chorus occurrences to the lyrics if necessary. CTC candidate windows use recognition estimates with 250 ms margins. Whisper windows add 500 ms of context around a group; a line is accepted only inside a 2.5-second tolerance around its independent recognition anchors. A boundary shift over 500 ms caps confidence below the review threshold. Very weak minimum word support (below 0.35), zero-length/invalid words, or incomplete coverage withholds the entire line’s timing. Severe transcription timing errors can still prevent recovery of stretched words. Confidence needs calibration on real edited music before unattended use.

Model downloads happen on first analysis. A dedicated analysis panel shows the current step out of six, completed steps, estimated overall percentage, download megabytes or completed lyric-line count when available, total elapsed time, and time since the last progress update. Recognition reports when its inference finishes; there is no fabricated per-second recognition percentage. CPU activity is measured from the analysis process on Linux. After 30 seconds without progress or CPU activity, the panel says analysis may be waiting or stalled and offers cancellation. Completed runs keep their timed/untimed line summary visible. Selecting another song clears that panel so results cannot be confused with the newly selected file. The overall percentage is a stage estimate, not a predicted completion time. If model initialization or downloading makes no progress for three minutes, the run stops with an error and keeps your edits. Cancel terminates the isolated analysis process even during model loading or native inference; retry uses completed models and resumable download files. Approximate checkpoint sizes: Whisper tiny ~75 MB, small ~460 MB, medium ~1.5 GB, large ~3 GB; optional English CTC ~360 MB, optional German XLSR ~1.2 GB. The default attention backend reuses the Whisper checkpoint and needs no additional timing model. Allow **3–8 GB** for CPU dependencies and a few models; more for multiple checkpoints, GPU wheels, and stems. Temporary PCM/stems are deleted when the run ends. Transcription and word/line results persist in the configured cache. Reused successful alignment is clearly labelled **Previous analysis loaded — unchanged audio and lyrics**. A result with zero timed lines is not automatically reused as a successful analysis. **Tools → Automatic timing → Analyze timing again from audio (ignore previous results)** recomputes both recognition and alignment; model downloads remain installed. The CLI equivalent is `--fresh`. Pipeline version 3 invalidates the previous buggy transcription/alignment results.

The optional Demucs adapter is disabled by default:

```bash
python -m pip install -e '.[separation]'
```

[Demucs's original repository is archived](https://github.com/facebookresearch/demucs); its [author's fork receives limited fixes](https://github.com/adefossez/demucs). This is isolated behind a separation interface, not a required dependency. Test dependency compatibility in a separate environment before adding it to a ROCm setup. It may need additional torchaudio dependencies matched to your torch build. It never replaces the MP3. Separated stem caching is deferred.

### AMD RX 9070 XT / Linux

The alignment code uses PyTorch, not faster-whisper/CTranslate2. [WhisperX](https://github.com/m-bain/whisperX) is a useful alternative but its default transcription stack adds AMD installation friction. [Stable-ts](https://github.com/jianfch/stable-ts) offers convenient alignment, but its repository is archived; it is not the prototype's required backend.

[AMD's Linux compatibility matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/native_linux/native_linux_compatibility.html) lists the RX 9070 XT. Its validated OS list covers Ubuntu/RHEL, **not Arch/CachyOS**. Install a current HIP/ROCm PyTorch build compatible with `gfx1201`, your Python version, and the installed ROCm runtime using [AMD's current installation documentation](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/frameworks/pytorch/install.html); do not mix CUDA, CPU, and HIP wheel stacks. This project does not install system drivers.

```bash
python -c "import torch; print(torch.__version__, torch.version.hip); print(torch.cuda.is_available())"
```

`torch.cuda` is also PyTorch's **ROCm** device API. Settings → **AMD GPU (ROCm)** accepts only a HIP build with an available GPU; failure asks you to choose CPU. `auto` chooses available HIP or CPU. Default `cpu` remains usable without a GPU. FP32 is used for compatibility; models are loaded sequentially to limit VRAM use. ROCm speed and 16 GB memory consumption have not been benchmarked in this prototype. GPU runtime/OOM errors are visible; switch to CPU and retry rather than accepting fabricated results.

## Identification, metadata, artwork, and lyrics

- **Tools → Propose tags from filename** parses artist/title and edit suffixes. This is an unverified proposal.
- **Find details & cover** searches the official [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API) using editable artist/title hints, falling back to the filename for blank fields. It uses the same original-album lookup as **Find cover**, trying normal terms, fuzzy terms, then bounded prefixes with local fuzzy ranking when spelling searches return nothing. Title and artist must each meet their own similarity threshold. One chooser shows song details, album editions, and cover previews together; there is no separate text-only selection first. Covers load automatically for visible rows, with thumbnails and explicit unavailable labels. Filter by album, artist, song, or edition, and optionally hide confirmed missing covers. Transient preview failures stay retryable. Choose **Use details and cover**, or **Use details only (keep current cover)** even while a preview is loading. A second proposal review chooses the exact fields and independently whether to replace the cover. Nonempty fields are unchecked by default; fill-empty and optional extra-release choices are explicit. The score is text similarity, not a verified song identity. Release details fill album artist/date/track number where available; a failed extra-details request keeps your reviewed choice and cover. Manual genre editing is supported; unsupported genres are not guessed.
- **Tools → Identify with AcoustID** computes a local Chromaprint fingerprint and calls the [official lookup API](https://acoustid.org/webservice). Set your application API key in Settings. No built-in borrowed key; edited audio may not identify reliably. Fingerprints and duration are sent to AcoustID only on this action; the audio file is not uploaded. The key is stored in the permissions-restricted settings file and excluded from logs/cache URLs.
- **Find cover** searches strong artist/title matches, inspects up to three closely matching recordings, and pages through their releases. Early official studio albums rank ahead of singles, compilations, live albums, and bootlegs, with available front covers preferred among editions of the same album. The search examines up to 100 recordings and deprioritizes live/remix/demo versions unless requested. Fresh results replace old suggestions, and an existing album tag cannot override the ranking; selecting a cover changes only the artwork. Choosing an edition without an image no longer confines future searches to that recording. Previously found editions remain available when a new search cannot reach the provider. Previews come from [Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API), with at most two downloads at once and only visible rows prefetched. Duplicate references to the same release share a download. Closing the chooser stops further previews; missing-cover responses are cached for six hours, successful images for 30 days. Missing covers and invalid images show explicit errors; try another edition or **Local**. **Search Google Images** opens browser image results for the selected album and album artist; download a preferred image and use **Local** to load it. Artwork changes stay in memory until Save. New artwork is JPEG, capped at 1000 px, quality 88; remote requests use a 500 px front thumbnail.
- **Find Lyrics** uses [LRCLIB's documented API](https://lrclib.net/docs). Filter results, compare line counts and inspect a differences tab against current lyrics before choosing. Edit lookup terms independently from your tags. Select and review plain lyrics before alignment. **Remote synchronized timestamps are discarded**. No site scraping or lyric publishing. Manual paste works without network access or credentials. Respect provider terms and rights when using/exporting lyrics.

Network actions are explicit. MusicBrainz requests are paced at more than one second; metadata/lyrics responses are cached for seven days, artwork for thirty. Timeouts and response-size limits keep failures bounded. Missing API keys or search failures do not block manual work.

## Safe ID3 persistence

Before writing, a structured scrollable review shows the target MP3, changed fields, saved/proposed cover thumbnails, timed/missing/excluded counts, listening review and the output ID3 version. It explains what lives in the MP3, protected application storage and portable projects. Back to review and Cancel preserve the draft; Save writes only this MP3. Success feedback remains visible after tag/audio verification.

1. Reject an externally changed MP3 using its SHA-256 content identity. Symbolic/hard-linked files are rejected for atomic save semantics.
2. Copy to a temporary file beside the original.
3. Use Mutagen to write changed TIT2/TPE1/TALB/TPE2/TDRC/TRCK/TCON fields, selected front APIC, USLT, and standard **SYLT (`format=2`, milliseconds; `type=1`, lyrics)**. SYLT strings include newline boundaries as specified by [ID3v2.4](https://id3.org/id3v2.4.0-frames).
4. Reopen and compare written frame inventory/data and unrecognized frames. Verify duration and SHA-256 of compressed audio packets through FFmpeg **stream copy**, never encoding.
5. Metadata, cover art, and lyrics are embedded directly in the MP3. Saving creates no backups or permanent companion files.
6. Check the source again, fsync the verified file, then atomically replace and fsync the directory. Pre-commit failure/cancellation leaves the original unchanged.

Output uses ID3v2.4; older tags are upgraded using Mutagen. Unrelated frames and non-front artwork remain. Only the selected active USLT/SYLT frame is replaced when edited; other language/description variants remain. Reads prefer this application's frame, then generic plain lyrics. Clearing all timing removes the selected active SYLT. Metadata-only saves leave lyrics/art unchanged. Unknown frame preservation is verified, though malformed or nonstandard files can fail rather than save. ID3v1/trailing data are left to Mutagen's preservation behavior.

As with any filesystem update there is a small race with an external writer after the final identity check; avoid editing the same MP3 simultaneously in another application. The prototype does not coordinate locks with other music tools. Temporary copies require free disk space comparable to the MP3 and are removed after saving or failure. Every save uses verification and atomic replacement.

## Jobs, cache, logs, and design

File and network work runs in Qt workers. All AI imports, model loading, downloads, and inference run in a separate process, supervised by a Qt worker, so native code holding Python’s GIL cannot freeze the desktop window. Cancel terminates the analysis process group, including temporary FFmpeg/Demucs children. Workers emit signals; widgets update in the main thread. Editors are disabled during jobs, while playback remains available during analysis. Artwork previews use a small separate thread pool and cancel when their chooser closes; an active HTTP read remains bounded by its timeout. Close waits for cancellation instead of destroying a live worker.

Settings are in `$XDG_CONFIG_HOME/song-metadata-enricher/settings.json` (default `~/.config`). Cache and rotating JSON logs default to `$XDG_CACHE_HOME/song-metadata-enricher` (`~/.cache`). Recovery, presentation preferences and corrected timings default to `$XDG_DATA_HOME/song-metadata-enricher` (`~/.local/share`), with `workspace_directory` recorded in settings. The settings dialog displays that protected location read-only to avoid relocating drafts accidentally. Cache selection has a folder picker; custom configured languages are preserved. Local readiness checks selected-backend package presence and the selected cached Whisper file size without importing models or downloading anything. Runtime/device loading is verified when analysis starts.

**Tools → Manage disposable caches…** inventories file counts/sizes in a worker and asks which categories to remove. Nothing is preselected. HTTP, artwork, fingerprints, recognition, alignment and waveform results are disposable; model weights, recovery, corrected timing snapshots and legacy `saved_timings` are excluded. There is no automatic eviction. Content hashes, supplied lyrics, model/language/device/separation settings, dependency versions and pipeline version identify analysis results. Renaming alone cannot cause stale audio results; changing tags conservatively invalidates analysis too. Normal logs contain stage durations/backend information and errors, not full lyrics/model outputs.

`model.py` owns Qt-independent dataclasses; `session.py` keeps per-song working state; `lyrics.py` handles pure conversions; `tags.py` handles persistence; `alignment.py` and `providers.py` expose replaceable service boundaries; `jobs.py` owns background execution; `alignment_process.py` isolates and supervises AI jobs; `artwork_dialog.py` handles asynchronous cover selection; `timing_editor.py` handles playback-assisted word editing; `word_timeline.py` paints the draggable word Gantt chart; `theme.py` owns the shared palette, styles, and accessible pill switches; `timing_data.py` validates saved timing snapshots and portable projects; `workspace.py` owns durable recovery/presentation state; `projects.py` validates local imports in workers; `dialogs.py` provides selective proposals and structured save review; `layouts.py` wraps toolbars; `waveform.py` handles local amplitude navigation; `lyric_table.py` locks manual row selection during playback; `ui.py` owns widgets; `config.py`/`cache.py` own shared settings/state. `batch.py` owns folder discovery, timing categories, lyric match ranking and sequential verified batch saves. [implementation status](../IMPLEMENTATION_STATUS.md) records scope and milestones.

## Validation

```bash
source .venv/bin/activate
QT_QPA_PLATFORM=offscreen pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
```

Tests generate tone MP3s with FFmpeg (no copyrighted commercial fixtures). They cover filename parsing, normalization, timestamps/LRC, repeated/omitted/reordered passages, CTC repeated letters, mocked model pipelines decoding a real local MP3, state/cache/settings, ID3 preservation, exact compressed audio checks, save/cancel failures, and Qt manual workflow, multi-song switching with unsaved edits, saving only the selected song, progress/stall explanations and actual CPU activity reporting, asynchronous cover previews/selection, child-process failures, byte download progress, and UI responsiveness/cancellation while native model code stalls. Qt playback tests require access to a desktop audio service even with an offscreen window.

Place private manual evaluation files outside version control, e.g. `local-fixtures/`. For each edited song, record language, model/device/separation settings, lines correctly found, deleted lines falsely timed, repeated/reordered lines, and timing errors by listening. Try cuts at the intro, deleted verses, repeated choruses, and instrumentals. Passing automated logic tests does not establish reliability on singing. Calibrated confidence and full karaoke presentation/export remain deferred. Waveform navigation, LRC/project import and durable recovery are available.

Run the exact same backend without the UI for repeatable private-file evaluation; outputs retain word details and never modify the MP3:

```bash
python scripts/analyze_file.py local-fixtures/edited.mp3 local-fixtures/lyrics.txt \
  --output /tmp/timing-review.json --cache /tmp/song-model-cache --language de --device cpu
```

An earlier CTC CPU smoke check was completed on generated eSpeak speech with Whisper `tiny` and English CTC: two occurrences of an original phrase were aligned at **0.060 s and 3.331 s**, each with five word spans; the deliberately omitted middle verse remained unmatched. The synthesized repeat starts at 3.268 s. Scores **0.71/0.72** correctly remain below the default review threshold. This checks model integration, not singing accuracy. GUI tests passed with desktop audio access; a restrictive sandbox blocks Qt's audio socket even when the window is offscreen.

To reproduce that installation check (optional fixture-generation dependency only):

```bash
python -m pip install espeakng-loader
python scripts/generate_alignment_fixture.py /tmp/song-smoke
python scripts/analyze_file.py /tmp/song-smoke/synthetic.mp3 /tmp/song-smoke/lyrics.txt \
  --output /tmp/song-smoke/result.json --cache /tmp/song-smoke/cache --model tiny --language en --backend whisper-ctc
```

CPU AI dependencies are installed in this workspace's `.venv`. Small/other model weights download when selected. The validation's tiny/English weights and outputs are under `/tmp/song-alignment-smoke`, separate from the application's default cache. Vocal separation and ROCm inference remain untested here. The validated environment uses Python 3.12.14, PySide6 6.11.2, Mutagen 1.48.1, CPU PyTorch 2.14.1, Transformers 4.57.6, and openai-whisper 20250625.

Private singing evaluation: the initial CTC pipeline completed model initialization but produced zero timed lines. Investigation found that a raw speech-detection filter discarded correct singing: only 42 recognized words survived. Removing that filter recovered 234 words and evidence for 38 of 40 supplied lines. The new default Whisper attention backend then produced **19 timed lines and 98 retained word timings** in about **45 seconds** on CPU (fresh recognition included); **three lines** exceeded the default 0.8 review threshold. Six accepted lines were explicitly capped for recognition/alignment boundary disagreement. Sixteen lines had weak forced-model support, three had invalid word spans, and two had no reliable text match. This is useful partial recovery, not proof that every timestamp is correct or that other songs will perform similarly. The original MP3 hash remained unchanged. A real alternate release cover was also checked; some MusicBrainz editions have no Cover Art Archive image.

The current default attention backend was also tested against that generated repeat/deleted-verse fixture: one chorus occurrence was accepted at 3.52 s (confidence 0.85), the deleted verse stayed unmatched, and the first occurrence was withheld for weak evidence with the tiny model. This confirms conservative behavior; use `small` for normal singing work.
