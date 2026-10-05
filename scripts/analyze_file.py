"""Run the same alignment backend without Qt, for repeatable private-file evaluation."""

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path

from tracksmith.alignment_process import run_alignment
from tracksmith.config import Settings
from tracksmith.jobs import JobContext
from tracksmith.lyrics import generate_lrc, split_lyrics
from tracksmith.tags import read_track


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mp3", type=Path)
    parser.add_argument("lyrics", type=Path, help="UTF-8 display lyrics")
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSON output path; will not overwrite"
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--device", choices=["cpu", "auto", "rocm"], default="cpu")
    parser.add_argument("--separate-vocals", action="store_true")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Recompute recognition and timing instead of reusing cached results",
    )
    parser.add_argument(
        "--backend", choices=["whisper-attention", "whisper-refined", "whisper-ctc"], default="whisper-attention"
    )
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".lrc").exists():
        parser.error("Output files already exist; choose a new path")
    logging.basicConfig(level=logging.INFO)
    settings = Settings(
        cache_directory=str(args.cache.absolute()),
        whisper_model=args.model,
        language=args.language,
        device=args.device,
        separate_vocals=args.separate_vocals,
        ai_backend=args.backend,
    )
    context = JobContext(lambda text, percent: print(f"{percent}% {text}", flush=True))
    track = read_track(args.mp3, context)
    track.display_lyrics = args.lyrics.read_text(encoding="utf-8")
    track.aligned_lines = split_lyrics(track.display_lyrics)
    lines = run_alignment(track, settings, context, fresh=args.fresh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(
            {"file_hash": track.content_hash, "lines": [asdict(line) for line in lines]},
            handle,
            indent=2,
        )
    with args.output.with_suffix(".lrc").open("x", encoding="utf-8") as handle:
        handle.write(generate_lrc(lines))
    for line in lines:
        print(
            f"{line.start if line.start is not None else 'unmatched'} · {line.confidence:.2f} · {line.text} · {line.note}"
        )


if __name__ == "__main__":
    main()
