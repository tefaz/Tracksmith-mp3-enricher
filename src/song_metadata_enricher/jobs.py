from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(
        self,
        progress: Callable[[str, int], None] = lambda text, percent: None,
        activity: Callable[[dict], None] = lambda details: None,
    ):
        self.cancelled = threading.Event()
        self.progress = progress
        self.activity = activity

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled("Operation cancelled")

    def run(self, command: list[str]) -> bytes:
        """Drain stdout/stderr into temporary files; never deadlock on full pipes."""
        self.check()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            except FileNotFoundError as exc:
                raise RuntimeError(f"Required executable missing: {command[0]}") from exc
            try:
                while process.poll() is None:
                    self.check()
                    self.cancelled.wait(0.1)
                self.check()
                if process.returncode:
                    stderr.seek(0)
                    raise RuntimeError(
                        f"{command[0]} failed: {stderr.read()[-2000:].decode(errors='replace')}"
                    )
                stdout.seek(0)
                return stdout.read()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    @contextmanager
    def stage(self, name: str, percent: int, check_after: bool = True):
        self.check()
        self.progress(name, percent)
        started = time.monotonic()
        try:
            yield
        finally:
            log.info(
                "stage", extra={"stage": name, "seconds": round(time.monotonic() - started, 3)}
            )
        if check_after:
            self.check()


class JobSignals(QObject):
    progress = Signal(str, int)
    activity = Signal(object)
    result = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()


class Job(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.signals = JobSignals()
        self.context = JobContext(self.signals.progress.emit, self.signals.activity.emit)
        self.operation = operation

    @Slot()
    def run(self):
        try:
            result = self.operation(self.context)
            self.signals.result.emit(result)
        except Cancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            log.exception("job failed")
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()
