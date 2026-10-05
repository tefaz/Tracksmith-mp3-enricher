# Tracksmith

A Python/PySide6 desktop app for editing local MP3 metadata, finding artwork and lyrics, and synchronizing lyrics to your recording—including edited songs with cuts or repeated sections.

## Features

- **MP3 details:** edit title, artist, album, album artist, date, track number and genre; inspect audio and tag information.
- **Album artwork:** search MusicBrainz/Cover Art Archive or load a local image; preview and choose replacements.
- **Lyrics:** paste, edit or find lyrics through LRCLIB; compare search results against your current text.
- **Manual line timing:** play the song and stamp line starts with Enter; seek, nudge timestamps, undo/redo and mark unsung lines.
- **Precise word timing:** adjust word starts and ends in a draggable chart or numeric fields, with section playback and looping.
- **Optional local AI:** generate word and line timings using Whisper; optional CTC alignment, word-start refinement and Demucs vocal separation.
- **Listening review:** track line and word review separately, with timing provenance and explicit labels for estimated boundaries.
- **Multi-song workspace:** load files, folders or drag-and-drop MP3s; search songs and filter by saved lyric/timing completeness.
- **Batch tools:** find and save lyrics, generate missing line timings, and find missing covers for acceptance in a visual review grid. Existing timings and covers are protected.
- **Playback tools:** seek, change speed and volume, replay sections and display an optional local waveform.
- **Portable work:** import/export line-timed LRC and complete JSON timing projects; retain corrected word timings in application storage.
- **Recovery and appearance:** recover unsaved drafts, keep per-song edit history, and choose light/dark/system themes and adjustable text size.
- **Verified saves:** review changes before individual saves; write ID3 tags atomically and verify that compressed audio remains unchanged.

## Install and run

Requires **Python 3.11+** (3.12 recommended), **FFmpeg**, and a working desktop audio service. Linux is the primary development platform. Chromaprint (`fpcalc`) is optional for AcoustID identification; AI packages are optional.

```bash
git clone git@github.com:tefaz/Tracksmith-mp3-enricher.git
cd Tracksmith-mp3-enricher
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tracksmith
```

On Arch/CachyOS, install the system dependencies with:

```bash
sudo pacman -S ffmpeg libsndfile libxcb libxkbcommon-x11 xcb-util-cursor
# Optional fingerprint identification:
sudo pacman -S chromaprint
```

You can also run `./start.sh` (uses this folder's `.venv`) or open a file directly:

```bash
python -m song_metadata_enricher /path/to/song.mp3
```

The included `.desktop` launcher uses absolute paths; update its `Exec`, `TryExec`, `Path` and `Icon` entries for your checkout before installing it in your application menu.

### Optional automatic timing

For CPU analysis, install CPU PyTorch before the AI extra:

```bash
source .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[ai]'
# Optional vocal separation:
python -m pip install -e '.[separation]'
```

Select the model, language and device in **Tools → Settings → Audio analysis**, then use **Tools → Automatic timing → Analyze timing**. The default is Whisper `small` on CPU. Models download on first use; allow several GB for packages and checkpoints. Analysis runs locally in a cancellable process.

Automatic singing alignment is experimental. Review generated timings by listening; support scores are heuristics. Downloaded lyric timestamps are discarded so they do not silently become timings for a different recording. Full karaoke presentation and word-level player export are not implemented.

## Typical workflow

1. Load one or more MP3s or a folder.
2. Edit song details, choose artwork, and paste or find lyrics.
3. Apply lyric changes, then stamp line starts manually or run optional analysis.
4. Correct word timings and mark listening review decisions.
5. Use **Review & save to MP3** to inspect and save the selected song.

Opening a song is read-only. Manual work requires no account or network connection.

## Storage and online services

- **Inside the MP3:** metadata, artwork, plain lyrics (USLT) and line starts (SYLT), saved as ID3v2.4 without re-encoding audio. Saves create no permanent companion files or backups.
- **Application data:** corrected word timings, review state and recovery drafts live separately under `~/.local/share/song-metadata-enricher` by default. JSON projects carry this richer state without duplicating audio.
- **Settings:** `~/.config/song-metadata-enricher/settings.json`.
- **Disposable caches and logs:** `~/.cache/song-metadata-enricher`; use **Tools → Manage disposable caches** to clean cached results while keeping recovery and corrected timings.

Paths follow `XDG_DATA_HOME`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`; some locations are configurable in Settings.

MusicBrainz, Cover Art Archive and LRCLIB searches need internet access but no account. Optional AcoustID identification needs your own application key and `fpcalc`; it sends a fingerprint and duration, not the audio file. Batch cover suggestions always require image review before saving. Use lyrics and artwork according to provider terms and applicable rights.

## Development

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
```

Tests use generated tone MP3s and mocked online/model services. FFmpeg is required; Qt playback tests also need access to a desktop audio service. Keep private music and evaluation files in the ignored `local-fixtures/` folder.

- [Detailed guide](docs/USER_GUIDE.md): shortcuts, timing/recovery workflows, optional backends and save verification.
- [Implementation status](IMPLEMENTATION_STATUS.md): implementation and validation notes.
- [Improvement backlog](suggested-improvement.md): design rationale and UX requirements.

Report bugs or propose changes through [GitHub issues](https://github.com/tefaz/Tracksmith-mp3-enricher/issues); include reproduction steps, environment details and relevant errors without private keys or music files.

## License

No license has been selected for this repository yet.
