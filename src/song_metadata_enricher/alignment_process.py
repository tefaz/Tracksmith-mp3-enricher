"""Run model code in a cancellable child so native ML work cannot block Qt's GIL."""

from __future__ import annotations

import json
import logging
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .cache import file_hash
from .config import CPU_THREAD_LIMIT, Settings
from .jobs import JobContext
from .model import LyricLine, Track, Word


def process_cpu_seconds(pid: int) -> float | None:
    """Linux CPU activity, including model threads; unavailable platforms return None."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def run_alignment(
    track: Track, settings: Settings, context: JobContext, fresh: bool = False
) -> list[LyricLine]:
    context.check()
    with tempfile.TemporaryDirectory(prefix="song-analysis-") as temporary:
        request = Path(temporary) / "request.json"
        model_settings = asdict(settings)
        model_settings["acoustid_key"] = ""
        request.write_text(
            json.dumps(
                {
                    "path": str(track.path),
                    "hash": track.content_hash,
                    "lyrics": [line.text for line in track.aligned_lines],
                    "settings": model_settings,
                    "fresh": fresh,
                }
            )
        )
        request.chmod(0o600)
        environment = dict(os.environ)
        environment.setdefault("OMP_NUM_THREADS", str(CPU_THREAD_LIMIT))
        environment.setdefault("MKL_NUM_THREADS", str(CPU_THREAD_LIMIT))
        environment.update(
            {
                "PYTHONUNBUFFERED": "1",
                "HF_HUB_DISABLE_XET": "1",
                "HF_HUB_ETAG_TIMEOUT": "10",
                "HF_HUB_DOWNLOAD_TIMEOUT": "20",
            }
        )
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                [sys.executable, "-m", __name__, str(request)],
                stdout=subprocess.PIPE,
                stderr=stderr,
                env=environment,
                start_new_session=True,
            )
            result = None
            buffer = b""
            last_progress = time.monotonic()
            last_activity = last_progress
            previous_cpu = process_cpu_seconds(process.pid)
            stage = "Start local audio analysis"
            percent = 0
            context.progress(stage, percent)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        context.check()
                        for key, _ in selector.select(timeout=0.1):
                            data = os.read(key.fileobj.fileno(), 65536)
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            buffer += data
                            while b"\n" in buffer:
                                raw, buffer = buffer.split(b"\n", 1)
                                try:
                                    event = json.loads(raw)
                                except ValueError:
                                    continue
                                if event.get("event") == "progress":
                                    stage, percent = event["message"], event["percent"]
                                    last_progress = time.monotonic()
                                    context.progress(stage, percent)
                                elif event.get("event") == "result":
                                    result = event["lines"]
                                elif event.get("event") == "log":
                                    logging.getLogger(event["logger"]).log(
                                        event["level"],
                                        event["message"],
                                        extra=event.get("details", {}),
                                    )
                        # Bound model initialization and network stalls. The inference stage
                        # can take longer, but remains cancellable by killing this child.
                        if percent == 65 and time.monotonic() - last_progress > 180:
                            raise RuntimeError(
                                "Alignment model loading made no progress for 3 minutes. "
                                "Check the network connection and retry; your edits are retained."
                            )
                        if time.monotonic() - last_activity >= 1:
                            cpu = process_cpu_seconds(process.pid)
                            active = (
                                None
                                if cpu is None or previous_cpu is None
                                else cpu - previous_cpu > 0.02
                            )
                            context.activity({"cpu_active": active})
                            previous_cpu = cpu
                            last_activity = time.monotonic()
                process.wait(timeout=5)
                context.check()
                if process.returncode or result is None:
                    stderr.seek(0)
                    detail = stderr.read()[-2500:].decode(errors="replace")
                    raise RuntimeError(
                        f"Local alignment failed. Your edits are retained.\n{detail}"
                    )
                if file_hash(track.path) != track.content_hash:
                    raise ValueError("MP3 changed during analysis; results discarded")
                return [
                    LyricLine(**{**line, "words": [Word(**word) for word in line["words"]]})
                    for line in result
                ]
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                process.stdout.close()


def _emit(event: dict):
    print(json.dumps(event), flush=True)


def _worker(request: Path):
    from .alignment import LocalAlignment
    from .tags import read_track

    class Handler(logging.Handler):
        def emit(self, record):
            details = {
                key: getattr(record, key)
                for key in ("stage", "seconds", "backend", "device", "model", "versions")
                if hasattr(record, key)
            }
            _emit(
                {
                    "event": "log",
                    "logger": record.name,
                    "level": record.levelno,
                    "message": record.getMessage(),
                    "details": details,
                }
            )

    logging.basicConfig(level=logging.INFO, handlers=[Handler()])
    data = json.loads(request.read_text())
    context = JobContext(
        lambda message, percent: _emit(
            {"event": "progress", "message": message, "percent": percent}
        )
    )
    track = read_track(Path(data["path"]), context)
    if track.content_hash != data["hash"]:
        raise ValueError("MP3 changed; reopen before analysis")
    track.aligned_lines = [LyricLine(text) for text in data["lyrics"]]
    lines = LocalAlignment(Settings(**data["settings"])).align(
        track, context, fresh=data.get("fresh", False)
    )
    _emit({"event": "result", "lines": [asdict(line) for line in lines]})


if __name__ == "__main__":
    _worker(Path(sys.argv[1]))
