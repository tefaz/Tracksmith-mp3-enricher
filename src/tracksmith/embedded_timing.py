"""Standard SYLT playback cues and lossless timing data embedded in the same MP3."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from .lyrics import normalize_text, timed_lines
from .model import LyricLine, Word
from .timing_data import validate_lines

TIMING_OWNER = "org.tracksmith.timing.v1"
MAX_TIMING_BYTES = 16 * 1024 * 1024


def word_fragments(line):
    """Preserve the original spelling, punctuation and spacing between word cues."""
    # Ignore non-sung annotations while keeping their character positions.
    text = re.sub(r"\[[^\]]*\]", lambda match: " " * len(match[0]), line.text)
    text = re.sub(
        r"\(?\bx\s*\d+\)?|\(\s*\d+\s*x\s*\)",
        lambda match: " " * len(match[0]),
        text,
        flags=re.I,
    )
    tokens = list(re.finditer(r"[^\W_]+(?:['’][^\W_]+)*", text, re.UNICODE))
    index = 0
    ends = []
    for word in line.words:
        expected = normalize_text(word.text)
        actual = ""
        while index < len(tokens) and actual != expected:
            actual = " ".join(filter(None, (actual, normalize_text(tokens[index][0]))))
            index += 1
            if not expected.startswith(actual):
                break
        if not expected or actual != expected:
            raise ValueError(
                f"Word text does not match the lyrics in {line.text!r}; review its words"
            )
        end = tokens[index - 1].end()
        next_start = tokens[index].start() if index < len(tokens) else len(line.text)
        while end < next_start and not line.text[end].isspace():
            end += 1
        ends.append(end)
    if index != len(tokens):
        raise ValueError(f"Some lyrics have no word timing in {line.text!r}; review its words")
    boundaries = [0, *ends[:-1], len(line.text)]
    return [line.text[start:end] for start, end in zip(boundaries, boundaries[1:])]


def sylt_cues(lines):
    """Line-only rows remain unchanged; word-timed rows start with the same newline."""
    cues = []
    for line in timed_lines(lines):
        if not line.words:
            cues.append(("\n" + line.text, round(line.start * 1000)))
            continue
        fragments = word_fragments(line)
        for index, (fragment, word) in enumerate(zip(fragments, line.words)):
            cues.append((("\n" if index == 0 else "") + fragment, round(word.start * 1000)))
    if any(before[1] > after[1] for before, after in zip(cues, cues[1:])):
        raise ValueError("Word timings cross the following line; review the line boundaries")
    return cues


def sylt_lines(cues, duration):
    """Read both legacy whole-line entries and standard newline-delimited fragments."""
    has_boundaries = any(text.startswith(("\n", "\r")) for text, _ in cues)
    rows = []
    for text, milliseconds in cues:
        if not rows or not has_boundaries or text.startswith(("\n", "\r")):
            rows.append([])
        rows[-1].append((text.lstrip("\r\n"), milliseconds / 1000))
    lines = []
    for index, parts in enumerate(rows):
        line = LyricLine(
            "".join(text for text, _ in parts),
            parts[0][1],
            source="embedded",
            note="Existing timestamps; verify against local audio",
        )
        # SYLT contains starts only. Without our rich frame, ends are estimates.
        if len(parts) > 1:
            end = rows[index + 1][0][1] if index + 1 < len(rows) else duration
            starts = [start for text, start in parts if text.strip()]
            texts = [text.strip() for text, _ in parts if text.strip()]
            ends = [*starts[1:], end]
            if starts and all(0 <= start < stop <= duration for start, stop in zip(starts, ends)):
                line.words = [
                    Word(text, start, stop, source="estimated")
                    for text, start, stop in zip(texts, starts, ends)
                ]
                line.start = line.words[0].start
                line.end = end
                line.note = "Embedded word starts; word ends estimated from following cues"
        lines.append(line)
    return lines


def timing_payload(track):
    """SYLT players need no custom reader; this frame preserves exact editing data."""
    if not track.aligned_words:
        return None
    lines = validate_lines([asdict(line) for line in track.aligned_lines], track.audio.duration)
    data = json.dumps(
        {
            "version": 1,
            "duration": track.audio.duration,
            "lyrics": track.display_lyrics,
            "lines": [asdict(line) for line in lines],
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > MAX_TIMING_BYTES:
        raise ValueError("Embedded timing data exceeds 16 MB")
    return data


def read_timing_payload(data, track, cues):
    """Ignore damaged/stale private data and keep the standard embedded lyrics."""
    try:
        if len(data) > MAX_TIMING_BYTES:
            return None
        value = json.loads(data)
        if (
            value["version"] != 1
            or value["duration"] != track.audio.duration
            or value["lyrics"] != track.display_lyrics
        ):
            return None
        lines = validate_lines(value["lines"], track.audio.duration)
        if sylt_cues(lines) != cues:
            return None
        return lines
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None
