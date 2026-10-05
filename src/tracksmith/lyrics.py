from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict, deque

from .model import LyricLine


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return " ".join(re.findall(r"[^\W_]+(?:'[^\W_]+)*", text, flags=re.UNICODE))


def alignment_reference(text: str) -> str:
    """Keep case and punctuation for Whisper, removing non-sung annotations."""
    text = unicodedata.normalize("NFKC", text).replace("’", "'")
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\(?\bx\s*\d+\)?|\(\s*\d+\s*x\s*\)", "", text, flags=re.I)
    return text.strip()


def alignment_text(text: str) -> str:
    return normalize_text(alignment_reference(text))


def split_lyrics(text: str, previous: list[LyricLine] | None = None) -> list[LyricLine]:
    """Keep repeated occurrences distinct. Formatting remains in display_lyrics."""
    saved = defaultdict(deque)
    for line in previous or []:
        saved[line.text].append(line)
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if alignment_text(raw):
            lines.append(saved[raw].popleft() if saved[raw] else LyricLine(raw))
    return lines


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "--:--.---"
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60000)
    secs, ms = divmod(remainder, 1000)
    return f"{minutes:02}:{secs:02}.{ms:03}"


def parse_timestamp(text: str) -> float | None:
    if text.strip() in {"", "--:--.---"}:
        return None
    match = re.fullmatch(r"\s*(\d+):(\d{2})(?:[.,](\d{1,3}))?\s*", text)
    if not match or int(match[2]) >= 60:
        raise ValueError("Use mm:ss.mmm, for example 01:24.140")
    return int(match[1]) * 60 + int(match[2]) + int((match[3] or "0").ljust(3, "0")) / 1000


def timed_lines(lines: list[LyricLine]) -> list[LyricLine]:
    result = [line for line in lines if line.start is not None and not line.excluded]
    if any(not math.isfinite(line.start) or line.start < 0 for line in result):
        raise ValueError("Invalid lyric timestamp")
    return sorted(result, key=lambda line: line.start)


def generate_lrc(lines: list[LyricLine]) -> str:
    result = []
    for line in timed_lines(lines):
        centiseconds = int(line.start * 100 + 0.5)
        minutes, remainder = divmod(centiseconds, 6000)
        seconds, hundredths = divmod(remainder, 100)
        result.append(f"[{minutes:02}:{seconds:02}.{hundredths:02}]{line.text}")
    return "\n".join(result) + ("\n" if result else "")


def parse_lrc(text: str, duration: float) -> tuple[str, list[LyricLine]]:
    """Import basic line LRC, preserving repeated occurrences as unreviewed data."""
    rows, display = [], []
    offset_match = re.search(r"\[offset:([+-]?\d+)\]", text, re.I)
    offset = int(offset_match[1]) / 1000 if offset_match else 0
    for raw in text.splitlines():
        if re.fullmatch(r"\s*\[(?:ar|ti|al|by|re|ve|length|offset):.*\]\s*", raw, re.I):
            continue
        tags = re.match(r"((?:\[\d+:\d{2}(?:[.,]\d{1,3})?\])+)(.*)", raw.strip())
        if tags:
            lyric = tags[2].strip()
            if not alignment_text(lyric):
                continue
            for time in re.findall(r"\[([^]]+)\]", tags[1]):
                seconds = parse_timestamp(time) + offset
                if not 0 <= seconds <= duration:
                    raise ValueError("Imported LRC timestamp is outside this audio")
                display.append(lyric)
                rows.append(
                    LyricLine(
                        lyric,
                        seconds,
                        source="imported",
                        note="Imported LRC; listen against this local recording",
                    )
                )
        else:
            display.append(raw)
            if alignment_text(raw):
                rows.append(LyricLine(raw.strip()))
    if not any(line.start is not None for line in rows):
        raise ValueError("No supported line timestamps found in this LRC file")
    return "\n".join(display), rows
