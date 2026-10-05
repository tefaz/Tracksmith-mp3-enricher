"""Bounded local project/LRC reads, validated in workers before application."""

import json
from pathlib import Path

from .cache import file_hash
from .lyrics import parse_lrc
from .timing_data import parse_timing_project
from .workspace import decode_artwork


def read_project(path, track, context):
    context.check()
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Timing project exceeds 32 MB")
    if file_hash(track.path) != track.content_hash:
        raise ValueError("The MP3 changed outside the application; reopen it before importing")
    text = path.read_text()
    project = parse_timing_project(text, track)
    raw = json.loads(text)
    project["artwork"] = decode_artwork(raw["artwork"]) if "artwork" in raw else track.artwork
    context.check()
    return project


def read_lrc(path, track, context):
    context.check()
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("LRC file exceeds 2 MB")
    if file_hash(track.path) != track.content_hash:
        raise ValueError("The MP3 changed outside the application; reopen it before importing")
    result = parse_lrc(path.read_text(encoding="utf-8-sig"), track.audio.duration)
    context.check()
    return result
