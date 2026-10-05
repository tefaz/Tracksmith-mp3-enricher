from __future__ import annotations

import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import fields
from pathlib import Path

from mutagen.id3 import APIC, ID3, PRIV, SYLT, USLT, Encoding, Frames, PictureType
from mutagen.mp3 import MP3

from .cache import file_hash
from .embedded_timing import (
    TIMING_OWNER,
    read_timing_payload,
    sylt_cues,
    sylt_lines,
    timing_payload,
)
from .jobs import JobContext
from .lyrics import split_lyrics, timed_lines
from .model import Artwork, AudioInfo, Metadata, Track

TAG_MAP = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "date": "TDRC",
    "track_number": "TRCK",
    "genre": "TCON",
}
# Keep the legacy frame identifier so existing embedded lyrics remain compatible.
DESCRIPTION = "Tracksmith"
LEGACY_DESCRIPTION = "Song Metadata Enricher"


def read_track(path: Path, context: JobContext | None = None) -> Track:
    context = context or JobContext()
    path = path.absolute()
    with context.stage("Read MP3", 10):
        audio = MP3(path)
        tags = audio.tags or ID3()
        metadata = Metadata(**{name: str(tags.get(frame, "")) for name, frame in TAG_MAP.items()})
        version = ".".join(map(str, tags.version)) if audio.tags else "none"
        info = AudioInfo(
            audio.info.length,
            audio.info.bitrate,
            audio.info.sample_rate,
            audio.info.channels,
            version,
        )
        track = Track(path, info, metadata, deepcopy(metadata), file_hash(path))
        covers = tags.getall("APIC")
        cover = next(
            (c for c in covers if c.type == PictureType.COVER_FRONT), covers[0] if covers else None
        )
        if cover is not None:
            track.artwork = Artwork(cover.data, cover.mime, cover.desc)
            track.artwork_frame_key = (
                cover.HashKey if cover.type == PictureType.COVER_FRONT else None
            )
        plain = tags.getall("USLT")
        frame = (
            min(
                plain,
                key=lambda f: (
                    f.desc != DESCRIPTION,
                    f.desc != LEGACY_DESCRIPTION,
                    f.desc != "",
                    f.lang != "eng",
                ),
            )
            if plain
            else None
        )
        if frame is not None:
            track.display_lyrics = frame.text
            track.lyrics_frame_key = frame.HashKey
            track.lyrics_language = frame.lang
        synced = tags.getall("SYLT")
        sync = next(
            (
                f
                for description in (DESCRIPTION, LEGACY_DESCRIPTION)
                for f in synced
                if f.desc == description and f.format == 2 and f.type == 1
            ),
            next((f for f in synced if f.format == 2 and f.type == 1), None),
        )
        if sync is not None:
            track.sync_frame_key = sync.HashKey
            track.sync_language = sync.lang
            track.aligned_lines = sylt_lines(sync.text, track.audio.duration)
            if not track.display_lyrics:
                track.display_lyrics = "\n".join(line.text for line in track.aligned_lines)
            else:
                # SYLT omits untimed/deleted lines. Keep the plain lyric rows so
                # reopening still allows the user to fill the missing timings.
                embedded = track.aligned_lines
                combined = split_lyrics(track.display_lyrics, embedded)
                used = {id(line) for line in combined}
                track.aligned_lines = combined + [line for line in embedded if id(line) not in used]
        else:
            track.aligned_lines = split_lyrics(track.display_lyrics)
        if sync is not None:
            for frame in tags.getall("PRIV"):
                if frame.owner == TIMING_OWNER:
                    lines = read_timing_payload(frame.data, track, sync.text)
                    if lines is not None:
                        track.aligned_lines = lines
                        break
        track.mark_saved()
        return track


def changes(track: Track) -> list[str]:
    previous = track._saved_state
    result = []
    for item in fields(Metadata):
        old = getattr(track.existing_metadata, item.name)
        new = getattr(track.proposed_metadata, item.name)
        if old != new:
            result.append(
                f"{item.name.replace('_', ' ').title()}: {old or '(empty)'} → {new or '(empty)'}"
            )
    if previous.get("artwork") != track.artwork:
        result.append("Replace/remove selected artwork")
    if previous.get("lyrics") != track.display_lyrics:
        result.append("Update plain lyrics (USLT)")
    if previous.get("lines") != track.editable_state()["lines"]:
        words = len(track.aligned_words)
        result.append(
            f"Write {len(timed_lines(track.aligned_lines))} timed lines"
            + (f" and {words} timed words" if words else "")
            + " (SYLT, milliseconds)"
        )
    return result


def compressed_audio_hash(path: Path, context: JobContext) -> bytes:
    return context.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    ).strip()


def _write_tags(path: Path, track: Track):
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    state = track.editable_state()
    old = track._saved_state
    for name, frame_id in TAG_MAP.items():
        if getattr(track.proposed_metadata, name) != getattr(track.existing_metadata, name):
            tags.delall(frame_id)
            value = getattr(track.proposed_metadata, name)
            if value:
                tags.add(Frames[frame_id](encoding=Encoding.UTF8, text=[value]))
    if state["artwork"] != old.get("artwork"):
        if track.artwork_frame_key:
            tags.pop(track.artwork_frame_key, None)
        if track.artwork:
            art = track.artwork
            description = art.description
            index = 1
            while f"APIC:{description}" in tags:
                description = f"{art.description} ({index})"
                index += 1
            tags.add(
                APIC(
                    encoding=Encoding.UTF8,
                    mime=art.mime,
                    type=PictureType.COVER_FRONT,
                    desc=description,
                    data=art.data,
                )
            )
    if state["lyrics"] != old.get("lyrics"):
        if track.lyrics_frame_key:
            tags.pop(track.lyrics_frame_key, None)
        if track.display_lyrics:
            tags.add(
                USLT(
                    encoding=Encoding.UTF8,
                    lang=track.lyrics_language,
                    desc=DESCRIPTION,
                    text=track.display_lyrics,
                )
            )
    payload = timing_payload(track)
    private_frames = [frame for frame in tags.getall("PRIV") if frame.owner == TIMING_OWNER]
    if state["lines"] != old.get("lines") or (
        payload is not None and not any(frame.data == payload for frame in private_frames)
    ):
        if track.sync_frame_key:
            tags.pop(track.sync_frame_key, None)
        lines = timed_lines(track.aligned_lines)
        if lines:
            tags.add(
                SYLT(
                    encoding=Encoding.UTF8,
                    lang=track.sync_language,
                    desc=DESCRIPTION,
                    format=2,
                    type=1,
                    text=sylt_cues(lines),
                )
            )
        for frame in private_frames:
            tags.pop(frame.HashKey, None)
        if payload is not None:
            tags.add(PRIV(owner=TIMING_OWNER, data=payload))
    # Mutagen's v2.4 path preserves modern/unknown frames better than conversion to v2.3.
    audio.save(v2_version=4)
    return tags


def save_track(track: Track, context: JobContext | None = None) -> Track:
    context = context or JobContext()
    path = track.path
    if path.is_symlink() or path.stat().st_nlink > 1:
        raise ValueError("For atomic saving, open a regular file with no symbolic/hard links")
    if file_hash(path) != track.content_hash:
        raise ValueError("MP3 changed outside the application; reopen it before saving")
    for line in timed_lines(track.aligned_lines):
        if line.start > track.audio.duration:
            raise ValueError("A timestamp exceeds the audio duration")
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".mp3", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with context.stage("Prepare safe copy", 15):
            original_audio_hash = compressed_audio_hash(path, context)
            shutil.copy2(path, temporary)
        with context.stage("Write and verify ID3", 45):
            expected = _write_tags(temporary, track)
            actual = MP3(temporary)
            for key, frame in expected.items():
                if key not in actual.tags or actual.tags[key] != frame:
                    raise ValueError(f"ID3 verification failed for {key}")
            if set(actual.tags) != set(expected):
                raise ValueError("ID3 frame inventory changed unexpectedly")
            if actual.tags.unknown_frames != expected.unknown_frames:
                raise ValueError("Unrecognized ID3 frames were changed")
            if abs(actual.info.length - track.audio.duration) > 0.01:
                raise ValueError("Audio duration changed unexpectedly")
            if compressed_audio_hash(temporary, context) != original_audio_hash:
                raise ValueError("Compressed audio verification failed; original remains untouched")
            verified = read_track(temporary, context)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
        with context.stage("Atomic replacement", 95, check_after=False):
            if file_hash(path) != track.content_hash:
                raise ValueError("Original changed during save; replacement aborted")
            context.check()
            if file_hash(path) != track.content_hash:
                raise ValueError("Original changed before replacement; replacement aborted")
            os.replace(temporary, path)
            # Commit point: cancellation must not report a successfully saved file as cancelled.
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    # File replacement already succeeded; report success with a log entry.
                    import logging

                    logging.getLogger(__name__).exception(
                        "Directory fsync failed after save commit"
                    )
            finally:
                os.close(directory_fd)
            verified.path = path
            verified.aligned_lines = deepcopy(track.aligned_lines)
            verified.status = "Saved"
            verified.mark_saved()
            return verified
    finally:
        temporary.unlink(missing_ok=True)
