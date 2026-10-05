from __future__ import annotations

import base64
import json
import shutil
import time
from copy import deepcopy
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, QThreadPool, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QColor, QKeySequence, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .alignment_process import run_alignment
from .artwork_dialog import ArtworkDialog
from .batch import CATEGORIES, folder_mp3s, run_batch, timing_category
from .cache import Cache, atomic_write, clear_disposable, disposable_inventory
from .config import Settings
from .cover_batch import AcceptedCoverProvider, BatchCoverProvider, find_cover_proposals
from .cover_review import CoverReviewDialog
from .dialogs import MetadataProposalDialog, SaveReviewDialog, SearchHintsDialog
from .jobs import Cancelled, Job, JobContext
from .layouts import WrappingLayout
from .lyric_table import LyricTable
from .lyrics import (
    format_timestamp,
    generate_lrc,
    parse_timestamp,
    split_lyrics,
    timed_lines,
)
from .model import Metadata, Track, active_line, needs_review, review_summary
from .providers import (
    AcoustIDService,
    CoverArtProvider,
    HttpClient,
    LRCLibProvider,
    MusicBrainzProvider,
    merge_release_candidates,
    parse_filename,
    prepare_artwork,
)
from .session import SongSession
from .song_list import SongCategoryDelegate, SongLoadButton, category_icon
from .tags import changes, read_track, save_track
from .theme import (
    REVIEW,
    ElidedLabel,
    ToggleSwitch,
    apply_theme,
    timing_selection_color,
    tone,
)
from .timing_data import (
    generate_timing_json,
    pending_line_at,
    restore_timing_state,
    store_timing_state,
    word_timing_window,
)
from .timing_editor import WordTimingDialog
from .waveform import Waveform, decode_overview
from .workspace import WorkspaceStore, apply_state, encode_artwork, validated_state


class CandidateDialog(QDialog):
    def __init__(self, title: str, labels: list[str], details: list[str], parent, current_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 440)
        layout = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter results…")
        layout.addWidget(self.filter_edit)
        self.list = QListWidget()
        self.list.setObjectName("candidateList")
        self.list.setSpacing(3)
        self.list.setWordWrap(False)
        self.list.addItems(labels)
        row_height = self.list.fontMetrics().height() + 16
        for row in range(self.list.count()):
            self.list.item(row).setSizeHint(QSize(0, row_height))
        self.filter_edit.textChanged.connect(self.filter_results)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.details = details
        self.current_text = current_text
        self.comparison = QPlainTextEdit()
        self.comparison.setReadOnly(True)
        self.candidate_summary = QLabel()
        self.list.currentRowChanged.connect(self.preview_candidate)
        layout.addWidget(self.list)
        previews = QTabWidget()
        previews.addTab(self.preview, "Selected result")
        if current_text:
            previews.addTab(self.comparison, "Changes to current draft")
        layout.addWidget(previews, 1)
        layout.addWidget(self.candidate_summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Use these lyrics" if "lyrics" in title.lower() else "Use selected proposal"
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.list.currentRowChanged.connect(lambda row: self.ok_button.setEnabled(row >= 0))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if labels:
            self.list.setCurrentRow(0)

    def preview_candidate(self, index):
        import difflib

        text = self.details[index] if index >= 0 else ""
        self.preview.setPlainText(text)
        if self.current_text:
            diff = difflib.unified_diff(
                self.current_text.splitlines(),
                text.splitlines(),
                fromfile="Current draft",
                tofile="Selected result",
                lineterm="",
            )
            self.comparison.setPlainText("\n".join(diff) or "No lyric text changes")
        self.candidate_summary.setText(
            f"{len(text.splitlines())} display lines" if index >= 0 else "No matching result"
        )

    def filter_results(self, text):
        first = -1
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(text.casefold() not in item.text().casefold())
            if not item.isHidden() and first < 0:
                first = row
        current = self.list.currentRow()
        if current < 0 or self.list.item(current).isHidden():
            self.list.setCurrentRow(first)
        if first < 0:
            self.preview.setPlainText("No results match. Clear or change the filter.")


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(700, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(16)
        self.tabs = QTabWidget()
        forms = {}
        groups = {
            "General": ("cache_directory", "workspace_directory", "confidence_threshold"),
            "Audio analysis": (
                "ai_backend",
                "whisper_model",
                "device",
                "language",
                "ctc_model",
                "separate_vocals",
            ),
            "Online sources": ("lyrics_provider", "metadata_provider", "acoustid_key"),
        }
        names = {
            "cache_directory": "Cache folder",
            "workspace_directory": "Drafts and corrected timings",
            "confidence_threshold": "Review threshold",
            "separate_vocals": "Separate vocals first",
            "ai_backend": "Alignment method",
            "whisper_model": "Recognition model",
            "device": "Processing device",
            "language": "Song language",
            "ctc_model": "Optional CTC model",
            "lyrics_provider": "Lyrics source",
            "metadata_provider": "Song details source",
            "acoustid_key": "AcoustID application key",
        }
        for title, keys in groups.items():
            page = QWidget()
            page_layout = QVBoxLayout(page)
            form = QFormLayout()
            form.setContentsMargins(16, 16, 16, 12)
            form.setSpacing(14)
            page_layout.addLayout(form)
            if title == "Audio analysis":
                advanced_check = ToggleSwitch("Show advanced alignment options")
                page_layout.addWidget(advanced_check)
                self.advanced_analysis = QWidget()
                advanced_form = QFormLayout(self.advanced_analysis)
                advanced_form.setSpacing(14)
                page_layout.addWidget(self.advanced_analysis)
                self.advanced_analysis.hide()
                advanced_check.toggled.connect(self.advanced_analysis.setVisible)
            page_layout.addStretch()
            for key in keys:
                forms[key] = advanced_form if key in ("ctc_model", "separate_vocals") else form
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
        self.values = {}
        choices = {
            "device": ["cpu", "auto", "rocm"],
            "whisper_model": ["tiny", "base", "small", "medium", "turbo", "large"],
            "language": ["auto", "en", "de", "fr", "es", "da"],
            "lyrics_provider": ["lrclib", "manual"],
            "metadata_provider": ["musicbrainz", "manual"],
            "ai_backend": ["whisper-attention", "whisper-refined", "whisper-ctc"],
        }
        for field in fields(Settings):
            value = getattr(settings, field.name)
            if isinstance(value, bool):
                widget = ToggleSwitch()
                widget.setChecked(value)
            elif field.name == "confidence_threshold":
                widget = QDoubleSpinBox()
                widget.setRange(0, 1)
                widget.setSingleStep(0.05)
                widget.setValue(value)
            elif field.name in choices:
                widget = QComboBox()
                friendly = {
                    "auto": "Automatic",
                    "cpu": "CPU",
                    "rocm": "AMD GPU (ROCm)",
                    "en": "English",
                    "de": "German",
                    "fr": "French",
                    "es": "Spanish",
                    "da": "Danish",
                    "whisper-attention": "Whisper attention (recommended)",
                    "whisper-refined": "Whisper + word-start refinement (slower, experimental)",
                    "whisper-ctc": "Whisper + CTC (advanced)",
                    "manual": "Manual entry",
                    "lrclib": "LRCLIB",
                    "musicbrainz": "MusicBrainz",
                }
                for choice in dict.fromkeys([*choices[field.name], value]):
                    widget.addItem(friendly.get(choice, choice), choice)
                widget.setCurrentIndex(widget.findData(value))
            else:
                widget = QLineEdit(str(value))
                if field.name == "acoustid_key":
                    widget.setEchoMode(QLineEdit.EchoMode.Password)
            self.values[field.name] = widget
            if field.name == "workspace_directory":
                widget.setReadOnly(True)
            forms[field.name].addRow(names[field.name], widget)
            widget.setAccessibleName(names[field.name])
            if field.name == "cache_directory":
                browse = QPushButton("Choose folder")
                browse.clicked.connect(
                    lambda checked=False, target=widget: self.choose_folder(target)
                )
                forms[field.name].addRow("", browse)
        layout.addWidget(self.tabs, 1)
        note = QLabel(
            "CPU works without a GPU. ROCm requires HIP PyTorch.\nAI models download on first use. Model support is heuristic; the threshold highlights low support, while listening review is recorded separately.\nEmpty CTC model selects a supported language model automatically."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.validation_message = QLabel()
        self.validation_message.setWordWrap(True)
        layout.addWidget(self.validation_message)
        self.save_callback = None
        self.values["ai_backend"].currentIndexChanged.connect(self.update_backend)
        self.update_backend()
        readiness = QPushButton("Check local analysis readiness (no downloads)")
        readiness.clicked.connect(self.check_readiness)
        layout.addWidget(readiness)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def settings(self) -> Settings:
        result = {}
        for key, widget in self.values.items():
            if isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, QDoubleSpinBox):
                result[key] = widget.value()
            else:
                result[key] = widget.text().strip()
        if not result["cache_directory"]:
            raise ValueError("Cache directory cannot be empty")
        for key in ("cache_directory", "workspace_directory"):
            if not result[key]:
                raise ValueError(f"{key.replace('_', ' ').title()} cannot be empty")
            result[key] = str(Path(result[key]).expanduser().absolute())
        return Settings(**result)

    def choose_folder(self, target):
        directory = QFileDialog.getExistingDirectory(self, "Choose folder", target.text())
        if directory:
            target.setText(directory)

    def update_backend(self):
        self.values["ctc_model"].setEnabled(
            self.values["ai_backend"].currentData() == "whisper-ctc"
        )

    def accept(self):
        try:
            settings = self.settings()
            for key in ("cache_directory", "workspace_directory"):
                path = Path(getattr(settings, key)).expanduser()
                if not str(path) or (path.exists() and not path.is_dir()):
                    self.values[key].setFocus()
                    raise ValueError(f"{key.replace('_', ' ').title()} must be a folder")
            if self.save_callback:
                self.save_callback(settings)
        except (ValueError, OSError) as exc:
            self.validation_message.setText(str(exc))
            return
        super().accept()

    def check_readiness(self):
        import importlib.util
        import shutil

        packages = ["torch", "whisper"]
        if self.values["ai_backend"].currentData() == "whisper-ctc":
            packages.append("transformers")
        if self.values["separate_vocals"].isChecked():
            packages.append("demucs")
        available = [name for name in packages if importlib.util.find_spec(name)]
        missing = [name for name in packages if name not in available]
        checkpoint = (
            Path(self.values["cache_directory"].text()).expanduser()
            / "models/whisper"
            / (self.values["whisper_model"].currentData() + ".pt")
        )
        try:
            model_status = f"Selected Whisper checkpoint file: {checkpoint.stat().st_size / 1024**2:.1f} MB on disk; loading will verify it."
        except OSError:
            model_status = "Selected Whisper checkpoint is not present in this cache; analysis may download it."
        self.validation_message.setText(
            f"FFmpeg: {'available' if shutil.which('ffmpeg') else 'missing'}. "
            f"Installed packages: {', '.join(available) or 'none'}. Missing: {', '.join(missing) or 'none'}. "
            + model_status
            + " Device/model loading is checked when analysis starts. Manual timing always remains available."
        )


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        apply_theme(QApplication.instance())
        self.settings = settings or Settings.load()
        self.workspace_store = WorkspaceStore(self.settings.workspace_directory)
        self.preferences = self.workspace_store.preferences()
        apply_theme(
            QApplication.instance(),
            self.preferences.get("theme", "Dark").lower(),
            self.preferences.get("font_size", 10),
            self.preferences.get("reduced_motion", False),
        )
        self._edit_baseline = None
        self._restoring_history = False
        self._pending_close = False
        self._exit_save_queue = []
        self._recovery_suspended = False
        self._unrestored_drafts = []
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setSingleShot(True)
        self.recovery_timer.setInterval(700)
        self.recovery_timer.timeout.connect(self.save_recovery)
        self.track: Track | None = None
        self.sessions: list[SongSession] = []
        self.job: Job | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.wave_pool = QThreadPool(QApplication.instance())
        self.wave_pool.setMaxThreadCount(1)
        self.wave_jobs = []
        self._result_handler = None
        self._rendering = False
        self._lyrics_pending = False
        self._active = None
        self._stamp_history = []
        self.release_id = ""
        self.release_candidates = []
        self.job_timer = QTimer(self)
        self.job_timer.setInterval(500)
        self.job_timer.timeout.connect(self.update_job_elapsed)
        self.job_stage = ""
        self.job_stage_started = 0
        self.job_started = 0
        self.job_last_progress = 0
        self.job_percent = 0
        self.job_is_analysis = False
        self.job_cpu_active = None
        self.job_used_cache = False
        self._save_position = 0
        self._pending_playback_position = None
        self.setWindowTitle("Tracksmith")
        self.resize(1440, 960)
        self.setAcceptDrops(True)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.mediaStatusChanged.connect(self.restore_song_position)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        self.player.errorOccurred.connect(
            lambda error, text: self.statusBar().showMessage(f"Playback: {text}")
        )
        self._build_ui()
        self._build_menus()
        QApplication.instance().installEventFilter(self)
        self._enabled()
        self.restore_presentation()

    def current_session(self):
        return next((session for session in self.sessions if session.track is self.track), None)

    def edit_snapshot(self):
        if not self.track:
            return None
        return {"state": deepcopy(self.track.editable_state()), "pending": self._lyrics_pending}

    def record_change(self, label, before=None):
        if self._restoring_history or not self.track:
            return
        before = before if before is not None else self._edit_baseline
        after = self.edit_snapshot()
        if before and self._lyrics_pending:
            before = deepcopy(before)
            before["state"]["lyrics"] = after["state"]["lyrics"]
            before["pending"] = True
        session = self.current_session()
        if before and before != after and session:
            command = {
                "label": label,
                "before": deepcopy(before),
                "after": after,
                "at": time.monotonic(),
            }
            if (
                label == "edit song details"
                and session.history
                and session.history[-1]["label"] == label
                and command["at"] - session.history[-1]["at"] < 1
            ):
                command["before"] = session.history.pop()["before"]
            session.history.append(command)
            del session.history[:-80]
            session.redo.clear()
        self._edit_baseline = after
        self.schedule_recovery()

    def restore_edit(self, snapshot):
        self._restoring_history = True
        try:
            apply_state(self.track, snapshot["state"])
            self._lyrics_pending = snapshot["pending"]
            self._rendering = True
            for key, edit in self.metadata_fields.items():
                edit.setText(getattr(self.track.proposed_metadata, key))
            self.lyrics_editor.setPlainText(self.track.display_lyrics)
            self._rendering = False
            self.show_artwork()
            self.render_table()
            self._title()
            self._edit_baseline = self.edit_snapshot()
        finally:
            self._restoring_history = False
        self.schedule_recovery()

    def redo_edit(self):
        session = self.current_session()
        if self.job or not session or not session.redo:
            return
        if self.edit_snapshot() != session.redo[-1]["before"]:
            self.statusBar().showMessage(
                "The draft changed after Undo. Apply or undo pending edits before Redo."
            )
            return
        command = session.redo.pop()
        session.history.append(command)
        self.restore_edit(command["after"])
        self.statusBar().showMessage(f"Redid {command['label']}")

    def schedule_recovery(self):
        if self.sessions:
            self.recovery_timer.start()

    def save_recovery(self):
        self.remember_current_song()
        if self._recovery_suspended:
            self.update_paused_recovery_status()
            return False
        try:
            self.workspace_store.save(self.sessions, self._unrestored_drafts)
            self.recovery_state.setText(
                "An automatic recovery copy of your workspace has been saved. "
                "Use Review & save to MP3 to write your edits to the song file."
            )
            return True
        except (OSError, ValueError) as exc:
            self.recovery_state.setText(
                f"The automatic recovery copy could not be saved: {exc}\n"
                "Use Review & save to MP3 to save your current song."
            )
            return False

    def update_paused_recovery_status(self):
        self.recovery_state.setText(
            "Automatic recovery is paused because drafts from a previous session were kept. "
            "Your current edits are not being copied for recovery. Use File → Restore previous "
            "workspace or Discard retained recovery to resume automatic recovery. "
            "You can still use Review & save to MP3 to save your current song."
        )

    def restore_presentation(self):
        geometry = self.preferences.get("geometry")
        if geometry:
            try:
                self.restoreGeometry(base64.b64decode(geometry))
            except (ValueError, TypeError):
                pass
        screen = self.screen().availableGeometry()
        self.resize(
            min(self.width(), screen.width() - 20), min(self.height(), screen.height() - 50)
        )
        if not screen.contains(self.frameGeometry()):
            self.move(screen.topLeft())
        for key, splitter in (("sidebar", self.workspace_splitter), ("panels", self.splitter)):
            sizes = self.preferences.get(key)
            if isinstance(sizes, list) and all(type(size) is int and size >= 0 for size in sizes):
                splitter.setSizes(sizes)
        self.audio_output.setVolume(self.preferences.get("volume", 0.8))
        self.volume.setValue(round(self.audio_output.volume() * 100))

    def save_presentation(self):
        self.preferences.update(
            {
                "geometry": base64.b64encode(bytes(self.saveGeometry())).decode(),
                "sidebar": self.workspace_splitter.sizes(),
                "panels": self.splitter.sizes(),
                "volume": self.audio_output.volume(),
            }
        )
        try:
            self.workspace_store.save_preferences(self.preferences)
        except OSError:
            pass

    def offer_recovery(self):
        if self.job:
            self.statusBar().showMessage(
                "Wait for the current operation before restoring recovery."
            )
            return
        self.remember_current_song()
        if self.sessions and any(
            session.track.dirty or session.lyrics_pending for session in self.sessions
        ):
            self.error(
                "Save or close your current unsaved songs before restoring another workspace."
            )
            return
        try:
            data = self.workspace_store.recovery()
        except (OSError, ValueError) as exc:
            self._recovery_suspended = True
            self.discard_recovery_action.setEnabled(True)
            self.update_paused_recovery_status()
            self.error(f"Recovery workspace could not be loaded: {exc}")
            return
        if not data or not data["songs"]:
            return
        answer = QMessageBox.question(
            self,
            "Restore previous workspace",
            f"Restore {len(data['songs'])} songs from {data.get('saved_at', 'the last session')}?\nMP3 files will remain unchanged.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self.workspace_store.discard()
            self._recovery_suspended = False
            self._unrestored_drafts = []
            self.discard_recovery_action.setEnabled(False)
            self.recovery_state.setText(
                "Previous recovery drafts were discarded. "
                "Automatic recovery copies will be saved after edits."
            )
            return
        if answer != QMessageBox.StandardButton.Yes:
            self._recovery_suspended = True
            self.discard_recovery_action.setEnabled(True)
            self.update_paused_recovery_status()
            return
        self._recovery_suspended = True
        self.discard_recovery_action.setEnabled(True)
        entries = []
        skipped_entries = []
        for entry in data["songs"]:
            path = Path(entry["path"])
            if not path.exists():
                replacement, _ = QFileDialog.getOpenFileName(
                    self,
                    f"Relocate {path.name} (Cancel skips this song)",
                    str(path.parent),
                    "MP3 (*.mp3)",
                )
                if not replacement:
                    skipped_entries.append(entry)
                    continue
                path = Path(replacement)
            entries.append((path, entry))

        def restore(context):
            results, errors = [], []
            retained = list(skipped_entries)
            for path, entry in entries:
                context.check()
                try:
                    track = read_track(path, context)
                    if (
                        track.content_hash != entry["hash"]
                        or track.audio.duration != entry["duration"]
                    ):
                        raise ValueError("file changed since the draft; reopen it separately")
                    state = validated_state(entry["draft"], track.audio.duration)
                    baseline = validated_state(entry["baseline"], track.audio.duration)
                    apply_state(track, state)
                    track._saved_state = baseline
                    results.append((track, entry))
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{path.name}: {exc}")
                    retained.append(entry)
            return results, errors, retained

        def restored(result):
            results, errors, retained = result
            self._recovery_suspended = False
            self._unrestored_drafts = retained
            self.discard_recovery_action.setEnabled(bool(retained))
            for track, entry in results:
                self.load_track(track, restoring=True)
                session = self.current_session()
                session.lyrics_pending = bool(entry.get("pending"))
                session.playback_position = max(
                    0, min(round(track.audio.duration * 1000), int(entry.get("position", 0)))
                )
                session.selected_line = max(0, int(entry.get("selected", 0)))
                self.display_song(self.sessions.index(session))
            self.finish_job_display(
                "Workspace restored",
                "Unsaved changes are drafts; save each MP3 explicitly."
                + ("\n" + "\n".join(errors) if errors else ""),
            )
            self.schedule_recovery()

        self.start_job(restore, restored, name="Restoring previous workspace")

    def discard_retained_recovery(self):
        answer = QMessageBox.question(
            self,
            "Discard retained recovery",
            "Discard the previous workspace's recovery drafts? Current loaded songs and MP3s are unchanged.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return
        try:
            self.workspace_store.discard()
        except OSError as exc:
            self.error(f"Recovery could not be discarded: {exc}")
            return
        self._unrestored_drafts = []
        self._recovery_suspended = False
        self.discard_recovery_action.setEnabled(False)
        self.save_recovery()

    def _button(self, text, handler, layout):
        button = QPushButton(text)
        button.clicked.connect(handler)
        layout.addWidget(button)
        return button

    def _build_ui(self):
        def label(text, role="muted"):
            widget = QLabel(text)
            widget.setProperty("role", role)
            widget.setWordWrap(True)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            return widget

        def card():
            widget = QWidget()
            widget.setProperty("card", True)
            box = QVBoxLayout(widget)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(10)
            return widget, box

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(16, 10, 16, 12)
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        root.addWidget(self.workspace_splitter)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 22, 16, 16)
        side_layout.setSpacing(14)
        song_heading = QHBoxLayout()
        song_heading.setSpacing(6)
        songs_title = ElidedLabel("Your songs")
        songs_title.setProperty("role", "heading")
        song_heading.addWidget(songs_title, 1)
        self.load_button = SongLoadButton("song")
        self.load_button.clicked.connect(self.open_dialog)
        self.load_button.setToolTip("Load song — add MP3 files (Ctrl+O)")
        song_heading.addWidget(self.load_button)
        self.folder_button = SongLoadButton("folder")
        self.folder_button.clicked.connect(self.open_folder_dialog)
        self.folder_button.setToolTip("Load folder — add MP3 files from a folder")
        song_heading.addWidget(self.folder_button)
        side_layout.addLayout(song_heading)
        self.category_filter = QComboBox()
        self.category_filter.setAccessibleName("Lyric timing category")
        self.category_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.category_filter.setMinimumContentsLength(16)
        self.category_filter.addItem("All categories", "")
        for category in CATEGORIES:
            self.category_filter.addItem(category_icon(category), category, category)
            self.category_filter.setItemData(
                self.category_filter.count() - 1, category, Qt.ItemDataRole.ToolTipRole
            )
        self.category_filter.currentIndexChanged.connect(
            lambda: self.filter_songs(self.song_search.text())
        )
        side_layout.addWidget(self.category_filter)
        self.unload_category_button = self._button(
            "Unload category", self.unload_category, side_layout
        )
        self.unload_category_button.setToolTip(
            "Unload every song in the selected category, including songs hidden by search. "
            "Music files stay on disk."
        )
        self.song_list = QListWidget()
        self.song_list.setObjectName("songList")
        self.song_list.setItemDelegate(SongCategoryDelegate(self.song_list))
        self.song_list.setMinimumWidth(180)
        self.song_list.setWordWrap(False)
        self.song_list.setSpacing(1)
        self.song_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.song_list.customContextMenuRequested.connect(self.song_context_menu)
        self.song_search = QLineEdit()
        self.song_search.setPlaceholderText("Search loaded songs…")
        self.song_search.setAccessibleName("Search loaded songs")
        self.song_search.textChanged.connect(self.filter_songs)
        side_layout.addWidget(self.song_search)
        self.song_list.currentRowChanged.connect(self.select_song)
        side_layout.addWidget(self.song_list, 1)
        self.song_count = label("No songs loaded")
        side_layout.addWidget(self.song_count)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("sidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        sidebar_scroll.setWidget(sidebar)
        self.workspace_splitter.addWidget(sidebar_scroll)
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(10)
        self.workspace_splitter.addWidget(editor)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([235, 1180])
        heading = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(2)
        self.song_heading = ElidedLabel("Your audio. Your timing.")
        self.song_heading.setProperty("role", "heading")
        title.addWidget(self.song_heading)
        self.file_label = label("Metadata, artwork and lyrics for your own version of a song.")
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.addWidget(self.file_label)
        heading.addLayout(title, 1)
        self.dark_mode_toggle = ToggleSwitch("Dark mode")
        self.dark_mode_toggle.setAccessibleName("Dark mode")
        self.dark_mode_toggle.setToolTip("Switch between light and dark mode")
        self.dark_mode_toggle.setChecked(
            QApplication.instance().palette().base().color().lightness() < 128
        )
        self.dark_mode_toggle.toggled.connect(self.toggle_dark_mode)
        heading.addWidget(self.dark_mode_toggle, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)

        self.workspace_stack = QStackedWidget()
        layout.addWidget(self.workspace_stack, 1)
        welcome = QWidget()
        welcome_layout = QVBoxLayout(welcome)
        welcome_layout.addStretch()
        welcome_card, welcome_box = card()
        welcome_card.setMaximumWidth(640)
        welcome_box.setContentsMargins(40, 34, 40, 34)
        welcome_box.setSpacing(20)
        welcome_box.addWidget(label("START WITH ONE MP3", "eyebrow"))
        welcome_box.addWidget(label("Give your song the right lyrics and timing.", "heading"))
        welcome_box.addWidget(
            label(
                "Open a file, paste or find lyrics, then time them against your local audio. You can correct every line and word before saving."
            )
        )
        welcome_box.addWidget(label("Lyrics & timing: edit, listen and review", "section"))
        welcome_box.addWidget(label("Song details: tags, artwork and file information", "section"))
        self.welcome_load_button = tone(
            self._button("＋  Load your first song", self.open_dialog, welcome_box), "positive"
        )
        welcome_box.addWidget(
            label(
                "You can also drag an MP3 into this window. Opening a song never changes the file."
            )
        )
        welcome_layout.addWidget(welcome_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        welcome_layout.addStretch()
        welcome_scroll = QScrollArea()
        welcome_scroll.setWidgetResizable(True)
        welcome_scroll.setWidget(welcome)
        welcome_scroll.setMinimumSize(0, 0)
        self.workspace_stack.addWidget(welcome_scroll)
        workspace = QWidget()
        work_layout = QVBoxLayout(workspace)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(10)
        self.work_scroll = QScrollArea()
        self.work_scroll.setWidgetResizable(True)
        self.work_scroll.setWidget(workspace)
        self.work_scroll.setMinimumSize(0, 0)
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setAccessibleName("Song workspace")
        self.workspace_tabs.addTab(self.work_scroll, "Lyrics && timing")
        details_page = QWidget()
        details_page_layout = QVBoxLayout(details_page)
        details_page_layout.setContentsMargins(12, 12, 12, 12)
        details_page_layout.setSpacing(12)
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setWidget(details_page)
        self.details_scroll.setMinimumSize(0, 0)
        self.workspace_tabs.addTab(self.details_scroll, "Song details")
        self.workspace_stack.addWidget(self.workspace_tabs)

        self.details_card, details_box = card()
        details_row = QHBoxLayout()
        art_layout = QVBoxLayout()
        art_layout.setSpacing(5)
        self.artwork_label = QLabel("No cover\nChoose an album or image")
        self.artwork_label.setObjectName("artwork")
        self.artwork_label.setFixedSize(220, 220)
        self.artwork_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_layout.addWidget(self.artwork_label)
        self.cover_details = label("No cover image")
        art_layout.addWidget(self.cover_details)
        cover_actions = WrappingLayout()
        cover_actions.setSpacing(4)
        self.cover_button = tone(
            self._button("Find cover", self.find_artwork, cover_actions), "quiet"
        )
        self.cover_button.setToolTip("Search other album editions and change only the cover")
        self.art_button = tone(self._button("Local", self.load_image, cover_actions), "quiet")
        self.art_button.setToolTip("Choose a cover image from your computer")
        self.remove_art_button = self._button("Remove", self.remove_artwork, cover_actions)
        tone(self.remove_art_button, "danger")
        art_layout.addLayout(cover_actions)
        art_layout.addStretch()
        details_row.addLayout(art_layout)
        details_row.addSpacing(12)
        details_layout = QVBoxLayout()
        details_heading = QHBoxLayout()
        details_heading.addWidget(label("Song details", "section"), 1)
        self.metadata_button = self._button(
            "Find details && cover", self.find_metadata, details_heading
        )
        details_layout.addLayout(details_heading)
        self.metadata_widget = QWidget()
        form = QGridLayout(self.metadata_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.metadata_fields = {}
        names = {
            "title": "Title",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Album artist",
            "date": "Release date",
            "track_number": "Track",
            "genre": "Genre",
        }
        for index, key in enumerate(names):
            edit = QLineEdit()
            edit.setPlaceholderText(names[key])
            edit.setAccessibleName(names[key])
            edit.textEdited.connect(self.metadata_edited)
            self.metadata_fields[key] = edit
            row, column = index // 2, (index % 2) * 2
            field_label = label(names[key])
            field_label.setBuddy(edit)
            form.addWidget(field_label, row, column)
            form.addWidget(edit, row, column + 1)
            revert = QAction("Revert to saved value", edit)
            revert.triggered.connect(lambda checked=False, name=key: self.revert_metadata(name))
            edit.addAction(revert)
            edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            edit.customContextMenuRequested.connect(
                lambda pos, field=edit, action=revert: self.metadata_context_menu(
                    field, action, pos
                )
            )
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        details_layout.addWidget(self.metadata_widget)
        details_layout.addStretch()
        details_row.addLayout(details_layout, 1)
        details_box.addLayout(details_row)
        details_page_layout.addWidget(self.details_card)
        file_card, file_box = card()
        file_box.addWidget(label("File information", "section"))
        file_grid = QGridLayout()
        self.file_details = {}
        for key, title, row, column, span in (
            ("filename", "File name", 0, 0, 3),
            ("location", "Location", 1, 0, 3),
            ("duration", "Duration", 2, 0, 1),
            ("size", "File size", 2, 2, 1),
            ("bitrate", "Bitrate", 3, 0, 1),
            ("sample_rate", "Sample rate", 3, 2, 1),
            ("channels", "Channels", 4, 0, 1),
            ("id3", "Tag version", 4, 2, 1),
            ("lyrics_language", "Lyrics language", 5, 0, 3),
            ("saved_category", "Saved timing status", 6, 0, 3),
        ):
            value = label("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(title)
            value.setMinimumWidth(0)
            file_grid.addWidget(label(title), row, column, Qt.AlignmentFlag.AlignTop)
            file_grid.addWidget(value, row, column + 1, 1, span)
            self.file_details[key] = value
        file_grid.setColumnStretch(1, 1)
        file_grid.setColumnStretch(3, 1)
        file_grid.setHorizontalSpacing(24)
        file_grid.setVerticalSpacing(8)
        file_box.addLayout(file_grid)
        details_page_layout.addWidget(file_card)
        details_page_layout.addStretch()

        player_card, player_box = card()
        player_layout = QHBoxLayout()
        self.play_button = tone(
            self._button("▶  Play (space)", self.toggle_play, player_layout), "positive"
        )
        self.play_button.setMinimumWidth(98)
        self.play_button.setToolTip("Play or pause · Space")
        self.stop_button = self._button("■  Stop (backspace)", self.stop_playback, player_layout)
        self.stop_button.setToolTip("Stop and return to the beginning · Backspace")
        self.position_label = label("00:00.000 / 00:00.000")
        self.position_label.setMinimumWidth(178)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.setAccessibleName("Song playback position")
        self.timeline.valueChanged.connect(self.player.setPosition)
        self.timeline.sliderMoved.connect(self.player.setPosition)
        self.timeline.sliderReleased.connect(lambda: self.player.setPosition(self.timeline.value()))
        self.timeline.mousePressEvent = self.timeline_mouse_press
        player_layout.addWidget(self.timeline, 1)
        player_layout.addWidget(self.position_label)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(round(self.audio_output.volume() * 100))
        self.volume.setMaximumWidth(100)
        self.volume.setAccessibleName("Playback volume")
        self.volume.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        player_layout.addWidget(QLabel("Volume"))
        player_layout.addWidget(self.volume)
        player_box.addLayout(player_layout)
        self.waveform = Waveform()
        self.waveform.seek_requested.connect(
            lambda seconds: self.player.setPosition(round(seconds * 1000))
        )
        player_box.addWidget(self.waveform)
        self.waveform.hide()
        work_layout.addWidget(player_card)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        lyrics_panel, lyrics_layout = card()
        self.lyrics_panel = lyrics_panel
        lyrics_heading = QHBoxLayout()
        lyrics_heading.addWidget(label("Lyrics", "section"), 1)
        self.lyrics_button = self._button("Find lyrics", self.find_lyrics, lyrics_heading)
        lyrics_layout.addLayout(lyrics_heading)
        lyrics_layout.addWidget(
            label(
                "Keep [Verse] headings; repeat choruses as separate lines. Mark cut lyrics Not sung during review."
            )
        )
        self.lyrics_editor = QPlainTextEdit()
        self.lyrics_editor.setAccessibleName("Display lyrics, one sung line per row")
        self.lyrics_editor.setPlaceholderText(
            "Paste lyrics here, or choose Find lyrics\n\nOne sung line per row. You can keep section headings."
        )
        self.lyrics_editor.textChanged.connect(self.lyrics_edited)
        lyrics_layout.addWidget(self.lyrics_editor, 1)
        lyrics_actions = WrappingLayout()
        self.apply_button = self._button("Apply lyric changes", self.apply_lyrics, lyrics_actions)
        self.apply_button.setToolTip("Refresh the timing table after editing lyrics")
        lyrics_layout.addLayout(lyrics_actions)
        self.splitter.addWidget(lyrics_panel)

        timing_panel, timing_layout = card()
        self.timing_panel = timing_panel
        self.timing_layout = timing_layout
        timing_heading = QHBoxLayout()
        timing_heading.addWidget(label("Timing & review", "section"), 1)
        self.review_filter = ToggleSwitch("Hide reviewed lines")
        self.review_filter.setToolTip(
            "Hide lines whose line timing and any existing word timings have been reviewed. "
            "Lines with missing or unreviewed timings stay visible; not-sung lines are hidden."
        )
        self.review_filter.toggled.connect(self.filter_review_rows)
        timing_heading.addWidget(self.review_filter)
        timing_layout.addLayout(timing_heading)
        self.timing_summary = label("Load a song to review line and word timings.")
        timing_layout.addWidget(self.timing_summary)
        self.pending_banner = label("Lyrics changed — apply lyric changes before timing edits.")
        tone(self.pending_banner, "warning")
        self.pending_banner.hide()
        timing_layout.addWidget(self.pending_banner)
        timing_actions = WrappingLayout()
        self.words_button = self._button("Word timings", self.edit_word_timings, timing_actions)
        self.words_button.setToolTip("Drag word rectangles on a Gantt chart for the selected line")
        self.clear_auto_words_button = self._button(
            "Clear automatic word timings", self.clear_automatic_word_timings, timing_actions
        )
        self.clear_auto_words_button.setToolTip(
            "Clear words across this song on lines with only AI or estimated word timings. "
            "Keep line timestamps and lines with manual word corrections. Undo is available."
        )
        timing_layout.addLayout(timing_actions)
        self.review_button = QPushButton("Review decision")
        review_menu = QMenu(self.review_button)
        for text, value in (
            ("Mark line reviewed", "line"),
            ("Mark all words reviewed", "words"),
            ("Not sung in this version", "excluded"),
            ("Reopen review", "reopen"),
        ):
            review_menu.addAction(
                text, lambda checked=False, decision=value: self.review_decision(decision)
            )
        self.review_button.setMenu(review_menu)
        timing_actions.addWidget(self.review_button)
        self.table = LyricTable(0, 4)
        self.table.setAccessibleName("Lyric line timing and listening review")
        self.table.setMinimumHeight(200)
        self.table.selection_blocked.connect(self.explain_selection_lock)
        self.table.playback_line_clicked.connect(self.timing_cell_clicked)
        self.table.setHorizontalHeaderLabels(["Line start", "Lyrics", "Review", "Words"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 116)
        self.table.setColumnWidth(2, 155)
        self.table.setColumnWidth(3, 100)
        self.table.itemChanged.connect(self.table_edited)
        self.table.cellClicked.connect(self.timing_cell_clicked)
        self.table.itemSelectionChanged.connect(self.update_timing_selection)
        self.review_empty = label(
            'No lines need review. Turn off "Hide reviewed lines" to see all timings.'
        )
        self.review_empty.hide()
        timing_layout.addWidget(self.review_empty)
        timing_layout.addWidget(self.table, 1)
        editing = WrappingLayout()
        self.stamp_button = tone(
            self._button("Stamp (Enter)", self.stamp_line, editing), "positive"
        )
        self.stamp_button.setToolTip(
            "Stamp the untimed line shown above. Existing timestamps are protected."
        )
        self.unmatch_button = tone(
            self._button("Clear time (Del)", self.unmatch_line, editing), "quiet"
        )
        self.unmatch_button.setToolTip("Clear the selected line’s timing · Delete")
        self.clear_all_times_button = tone(
            self._button("Clear all", self.clear_all_timings, editing), "quiet"
        )
        self.clear_all_times_button.setToolTip(
            "Clear all line and word timings for this song. Undo is available."
        )
        editing.addStretch()
        timing_layout.addLayout(editing)
        self.splitter.addWidget(timing_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([340, 810])
        work_layout.addWidget(self.splitter, 1)

        self.job_panel, job_layout = card()
        self.job_panel.setObjectName("jobPanel")
        self.job_heading = label("", "section")
        job_title = QHBoxLayout()
        job_title.addWidget(self.job_heading, 1)
        self.retry_job_button = self._button("Retry", lambda: self._retry_operation(), job_title)
        self.retry_job_button.hide()
        self.job_search_button = self._button(
            "Edit search terms", self.edit_search_hints, job_title
        )
        self.job_search_button.hide()
        self.dismiss_job_button = tone(
            self._button("Close", lambda: self.activity_dialog.hide(), job_title), "quiet"
        )
        job_layout.addLayout(job_title)
        self.job_detail = label("")
        self.job_activity_label = label("")
        self.job_steps = label("")
        for widget in (self.job_steps, self.job_detail, self.job_activity_label):
            job_layout.addWidget(widget)
        # Detailed activity is available on demand; compact progress lives in the footer.
        job_content = self.job_panel
        self.job_panel = QScrollArea()
        self.job_panel.setObjectName("jobPanel")
        self.job_panel.setWidgetResizable(True)
        self.job_panel.setWidget(job_content)
        self.job_panel.setMinimumSize(0, 0)
        self.activity_dialog = QDialog(self)
        self.activity_dialog.setWindowTitle("Activity")
        self.activity_dialog.resize(620, 300)
        activity_layout = QVBoxLayout(self.activity_dialog)
        activity_layout.addWidget(self.job_panel)
        activity_layout.addWidget(label("Draft recovery", "section"))
        self.recovery_state = label(
            "Automatic recovery copies are saved after edits. "
            "Use Review & save to MP3 to write your edits to the song file."
        )
        activity_layout.addWidget(self.recovery_state)

        footer, footer_box = card()
        actions = QHBoxLayout()
        self.next_step = label("Load an MP3 to get started.")
        actions.addWidget(self.next_step, 1)
        self.save_state = label("No song selected", "badge")
        actions.addWidget(self.save_state)
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(0)
        self.progress.setToolTip("Estimated analysis progress. Detailed activity: View → Activity.")
        progress_layout.addWidget(self.progress, 1)
        self.cancel_button = tone(
            self._button("Cancel analysis", self.cancel_job, progress_layout), "danger"
        )
        footer_box.addWidget(self.progress_row)
        self.save_button = tone(
            self._button("Review && save to MP3", self.save, actions), "positive"
        )
        self.save_button.setToolTip(
            "Review changes before embedding details, cover and lyrics in the MP3"
        )
        footer_box.addLayout(actions)
        layout.addWidget(footer)
        self.footer = footer
        self.workspace_tabs.currentChanged.connect(self.update_workflow)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        self.open_action = QAction("Load songs", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(self.open_action)
        self.folder_action = file_menu.addAction("Load folder", self.open_folder_dialog)
        self.recent_menu = file_menu.addMenu("Recent songs")
        self.update_recent_menu()
        self.unload_song_action = file_menu.addAction("Unload song", self.close_song)
        file_menu.addAction("Close saved songs", self.close_saved_songs)
        file_menu.addAction("Restore previous workspace", self.offer_recovery)
        file_menu.addAction("Recovery status", self.show_recovery_status)
        self.discard_recovery_action = file_menu.addAction(
            "Discard retained recovery", self.discard_retained_recovery
        )
        self.discard_recovery_action.setEnabled(False)
        self.save_action = QAction("Save to MP3", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save)
        file_menu.addAction(self.save_action)
        self.export_action = file_menu.addAction(
            "Export timed lyrics to a separate file (.lrc)", self.export_lrc
        )
        self.export_action.setStatusTip(
            "Optional: create a timed-lyrics text file for other players. Save to MP3 embeds lyrics directly."
        )
        self.export_action.setToolTip(self.export_action.statusTip())
        self.export_words_action = file_menu.addAction(
            "Save timing project (.json)", self.export_word_timings
        )
        self.export_words_action.setStatusTip(
            "Preserve word starts, ends, support scores, and untimed lines for future karaoke work"
        )
        self.import_project_action = file_menu.addAction(
            "Open timing project (.json)", self.import_project
        )
        self.import_lrc_action = file_menu.addAction("Import line lyrics (.lrc)", self.import_lrc)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)
        tools = self.menuBar().addMenu("Tools")
        self.batch_lyrics_action = tools.addAction(
            "Batch: find and save lyrics", lambda: self.start_batch("lyrics")
        )
        self.batch_covers_action = tools.addAction(
            "Selected category: add missing album covers", lambda: self.start_batch("covers")
        )
        self.batch_covers_action.setToolTip(
            "Find album cover suggestions and review them in a grid before saving."
        )
        self.category_filter.addAction(self.batch_covers_action)
        self.category_filter.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.cancel_job_action = tools.addAction("Cancel current operation", self.cancel_job)
        self.cancel_job_action.setEnabled(False)
        self.filename_action = tools.addAction(
            "Propose tags from filename", self.filename_suggestion
        )
        self.fingerprint_action = tools.addAction("Identify with AcoustID", self.fingerprint)
        automatic_timing = tools.addMenu("Automatic timing")
        self.analyze_action = automatic_timing.addAction("Analyze timing", self.analyze)
        self.analyze_action.setToolTip(
            "Find timestamps against this local audio. Results need review."
        )
        self.fresh_analysis_action = automatic_timing.addAction(
            "Analyze timing again from audio (ignore previous results)",
            lambda: self.analyze(fresh=True),
        )
        self.fresh_analysis_action.setToolTip(
            "Recompute recognition and known-lyric alignment. Model files remain installed."
        )
        automatic_timing.addSeparator()
        self.batch_timing_action = automatic_timing.addAction(
            "Batch: generate line timings", lambda: self.start_batch("timing")
        )
        self.settings_action = tools.addAction("Settings", self.edit_settings)
        self.cache_action = tools.addAction("Manage disposable caches", self.inspect_cache)
        self.undo_action = tools.addAction("Undo", self.undo_stamp)
        self.redo_action = tools.addAction("Redo", self.redo_edit)
        self.redo_action.setStatusTip("Ctrl+Shift+Z while the timing table has focus")
        tools.addAction("Edit lookup terms", self.edit_search_hints)
        view = self.menuBar().addMenu("View")
        view.addAction("Activity", self.show_activity)
        view.addAction("Song details", lambda: self.focus_section("details"))
        view.addAction("Lyrics and timing", lambda: self.focus_section("lyrics"))
        view.addAction("Focus timing review", lambda: self.focus_section("timing"))
        self.wave_action = view.addAction("Show waveform")
        self.wave_action.setCheckable(True)
        self.wave_action.toggled.connect(self.toggle_waveform)
        view.addAction("Zoom waveform in", lambda: self.waveform.zoom(2))
        view.addAction("Zoom waveform out", lambda: self.waveform.zoom(0.5))
        view.addAction("Appearance", self.edit_appearance)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("Workflow and shortcuts", self.show_help)
        help_menu.addAction("What is a lyric file (.lrc)?", self.explain_lrc)

    def _enabled(self):
        idle = self.job is None
        loaded = self.track is not None
        for widget in [
            self.metadata_widget,
            self.lyrics_editor,
            self.table,
            self.apply_button,
            self.art_button,
            self.stamp_button,
            self.unmatch_button,
            self.metadata_button,
            self.lyrics_button,
            self.save_button,
            self.words_button,
            self.remove_art_button,
        ]:
            widget.setEnabled(loaded and idle)
        self.cover_button.setEnabled(loaded and idle)
        self.play_button.setEnabled(loaded)
        self.stop_button.setEnabled(loaded)
        self.timeline.setEnabled(loaded)
        self.open_action.setEnabled(idle)
        self.load_button.setEnabled(idle)
        self.folder_button.setEnabled(idle)
        self.folder_action.setEnabled(idle)
        self.category_filter.setEnabled(idle)
        self.update_unload_category_button()
        self.unload_song_action.setEnabled(loaded and idle)
        self.batch_lyrics_action.setEnabled(idle and bool(self.sessions))
        self.batch_timing_action.setEnabled(idle and bool(self.sessions))
        self.welcome_load_button.setEnabled(idle)
        self.review_filter.setEnabled(loaded and idle and not self.is_playing())
        self.workspace_stack.setCurrentIndex(1 if loaded else 0)
        self.settings_action.setEnabled(idle)
        self.cache_action.setEnabled(idle)
        for action in [
            self.save_action,
            self.export_action,
            self.export_words_action,
            self.filename_action,
            self.fingerprint_action,
            self.analyze_action,
            self.fresh_analysis_action,
            self.import_project_action,
            self.import_lrc_action,
        ]:
            action.setEnabled(loaded and idle)
        self.song_list.setEnabled(idle)
        self.song_list.setToolTip(
            "Wait for the current operation or cancel it before switching songs."
            if not idle
            else "Select a song to edit its details and lyrics."
        )
        self.cancel_button.setVisible(not idle)
        self.cancel_job_action.setEnabled(not idle)
        self.dismiss_job_button.setVisible(idle)
        self.retry_job_button.setEnabled(idle)
        self.progress.setVisible(not idle)
        self.progress_row.setVisible(not idle)
        self.update_workflow()

    def show_activity(self):
        self.activity_dialog.show()
        self.activity_dialog.raise_()
        self.activity_dialog.activateWindow()

    def show_recovery_status(self):
        QMessageBox.information(self, "Draft recovery", self.recovery_state.text())

    def _title(self):
        self.refresh_song_list()
        if self.track:
            self.song_heading.setText(self.track.proposed_metadata.title or self.track.path.stem)
        name = self.track.path.name if self.track else ""
        dirty = self.track and (self.track.dirty or self._lyrics_pending)
        self.setWindowTitle(
            f"{'* ' if dirty else ''}{name + ' — ' if name else ''}Tracksmith"
        )
        self.update_workflow()

    def needs_review(self, line):
        return needs_review(line)

    def filter_review_rows(self):
        if not self.track:
            return
        selected = self.table.currentRow()
        for row, line in enumerate(self.track.aligned_lines):
            self.table.setRowHidden(
                row, self.review_filter.isChecked() and not self.needs_review(line)
            )
        if selected >= 0 and self.table.isRowHidden(selected):
            visible = next(
                (row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)), -1
            )
            if visible >= 0:
                self.table.selectRow(visible)
            else:
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
        self.review_empty.setVisible(
            self.review_filter.isChecked()
            and not any(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        )
        self.update_timing_selection()

    def update_timing_selection(self):
        row = self.table.currentRow()
        selected = (
            self.track is not None
            and self.job is None
            and row >= 0
            and not self.table.isRowHidden(row)
        )
        self.words_button.setEnabled(selected)
        self.clear_auto_words_button.setEnabled(
            self.track is not None
            and self.job is None
            and not self._lyrics_pending
            and any(self.has_automatic_words(line) for line in self.track.aligned_lines)
        )
        self.review_button.setEnabled(
            selected and not self.is_playing() and not self._lyrics_pending
        )
        self.unmatch_button.setEnabled(selected and not self.is_playing())
        self.clear_all_times_button.setEnabled(
            self.track is not None
            and self.job is None
            and not self._lyrics_pending
            and not self.is_playing()
            and any(
                line.start is not None or line.end is not None or line.words
                for line in self.track.aligned_lines
            )
        )
        session = self.current_session()
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.job is None and bool(session and session.history))
            self.redo_action.setEnabled(self.job is None and bool(session and session.redo))
            self.undo_action.setText(
                "Undo " + (session.history[-1]["label"] if session and session.history else "")
            )
            self.redo_action.setText(
                "Redo " + (session.redo[-1]["label"] if session and session.redo else "")
            )
        self.table.setEnabled(
            self.track is not None and self.job is None and not self._lyrics_pending
        )
        self.pending_banner.setVisible(self.track is not None and self._lyrics_pending)
        if self._lyrics_pending:
            self.unmatch_button.setEnabled(False)
            self.words_button.setEnabled(False)
        self.update_stamp_target()
        self.paint_selected_line()

    def stamp_target(self, position=None):
        if not self.track:
            return None
        row = self.table.currentRow()
        if (
            0 <= row < len(self.track.aligned_lines)
            and not self.table.isRowHidden(row)
            and self.track.aligned_lines[row].start is None
            and not self.track.aligned_lines[row].excluded
        ):
            return row
        return None

    def update_stamp_target(self, position=None):
        row = self.stamp_target(position)
        valid = False
        if row is not None:
            try:
                window = word_timing_window(
                    self.track.aligned_lines, row, self.track.audio.duration
                )
                seconds = self.player.position() / 1000 if position is None else position / 1000
                valid = (
                    window.start <= seconds < window.end
                    or seconds == window.end == self.track.audio.duration
                )
            except ValueError:
                pass
        self.stamp_button.setEnabled(valid and self.job is None and not self._lyrics_pending)

    def update_workflow(self):
        loaded = self.track is not None
        dirty = loaded and (self.track.dirty or self._lyrics_pending)
        if loaded:
            for key, edit in self.metadata_fields.items():
                changed = getattr(self.track.proposed_metadata, key) != getattr(
                    self.track.existing_metadata, key
                )
                tone(edit, "warning" if changed else "neutral")
                edit.setToolTip(
                    f"Saved value: {getattr(self.track.existing_metadata, key) or '(empty)'}"
                    if changed
                    else ""
                )
        self.save_button.setEnabled(bool(dirty) and self.job is None)
        self.footer.setVisible(loaded or self.job is not None)
        self.update_timing_selection()
        self.save_state.setText(
            "Edits not saved" if dirty else "No unsaved changes" if loaded else "No song selected"
        )
        tone(self.save_state, "warning" if dirty else "neutral")
        if not loaded:
            self.next_step.setText("Load an MP3 to get started.")
            return
        lines = self.track.aligned_lines
        summary = review_summary(lines)
        timed, review = summary["timed"], summary["review"]
        self.file_details["saved_category"].setText(timing_category(self.track))
        if self.job:
            self.next_step.setText(self.job_stage or "Working on this song…")
        elif self.is_playing():
            self.next_step.setText(
                "Playback selects the line. Click a timed line to pause at its start; Enter stamps the selected line if untimed."
            )
        elif self._lyrics_pending:
            self.next_step.setText(
                "Lyrics changed. Update the lines before reviewing their timing."
            )
        elif self.workspace_tabs.currentIndex() == 1:
            self.next_step.setText(
                "Edit tags or choose a cover, then review your changes before saving to MP3."
            )
        elif not lines:
            self.next_step.setText("Next: paste lyrics or find them, then check the text.")
        elif not timed:
            self.next_step.setText("Lyrics are ready. Analyze your audio, or stamp times manually.")
        elif review:
            self.next_step.setText(
                f"{review} lines need a listen. Correct missing timings; leave cut lyrics untimed."
            )
        else:
            self.next_step.setText(
                "Timing is ready. Listen once more, then review your changes and save."
            )

    def play_from_start(self):
        self.player.setPosition(0)
        self.player.play()

    def error(self, text):
        QMessageBox.critical(self, "Operation failed", text)

    def confirm_discard(self) -> bool:
        self.remember_current_song()
        count = sum(session.track.dirty or session.lyrics_pending for session in self.sessions)
        if count:
            dialog = QDialog(self)
            dialog.setWindowTitle("Unsaved songs")
            root = QVBoxLayout(dialog)
            root.addWidget(QLabel("Choose what to do with your unsaved songs."))
            checks = []
            content = QWidget()
            body = QVBoxLayout(content)
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(content)
            root.addWidget(area)
            for session in self.sessions:
                if session.track.dirty or session.lyrics_pending:
                    check = QCheckBox(session.track.path.name)
                    check.setChecked(True)
                    body.addWidget(check)
                    checks.append((check, session))
            choice = {"value": "cancel"}
            actions = WrappingLayout()
            for text, value in (
                ("Save selected", "save"),
                ("Keep recovery drafts", "keep"),
                ("Discard edits", "discard"),
                ("Cancel", "cancel"),
            ):
                button = QPushButton(text)
                tone(
                    button,
                    "danger" if value == "discard" else "positive" if value == "save" else "quiet",
                )
                button.clicked.connect(
                    lambda checked=False, decision=value: (
                        choice.update(value=decision),
                        dialog.accept(),
                    )
                )
                actions.addWidget(button)
            root.addLayout(actions)
            dialog.resize(720, 380)
            dialog.exec()
            if choice["value"] == "keep":
                return self.save_recovery()
            if choice["value"] == "discard":
                try:
                    if not self._recovery_suspended:
                        self.workspace_store.save([], self._unrestored_drafts)
                except OSError as exc:
                    self.error(str(exc))
                    return False
                self.recovery_timer.stop()
                return True
            if choice["value"] == "save":
                self._exit_save_queue = [
                    session.track.path for check, session in checks if check.isChecked()
                ]
                self._saving_on_exit = True
                QTimer.singleShot(0, self.save_next_on_exit)
            return False
        return True

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load MP3 songs", self.preferences.get("open_directory", ""), "MP3 audio (*.mp3)"
        )
        if paths:
            self.open_paths([Path(path) for path in paths])

    def open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Load MP3 folder", self.preferences.get("open_directory", "")
        )
        if folder:
            self.open_folder(Path(folder))

    def open_folder(self, folder: Path):
        if self.job:
            return
        # Directory traversal happens in the worker along with MP3 reads.
        self.open_paths([], folder=folder)

    def start_batch(self, mode):
        if self.job:
            return
        self.remember_current_song()
        category = self.category_filter.currentData()
        if mode == "covers":
            if not category:
                self.statusBar().showMessage("Select a song category first to add missing covers.")
                return
        sessions = [
            session
            for session in self.sessions
            if (not category or timing_category(session.track) == category)
            and (
                mode in {"lyrics", "covers"}
                or (
                    session.track.display_lyrics.strip()
                    and session.track.aligned_lines
                    and not any(line.start is not None for line in session.track.aligned_lines)
                )
            )
        ]
        eligible = [
            session
            for session in sessions
            if not session.track.dirty and not session.lyrics_pending
            and (mode != "covers" or session.track.artwork is None)
        ]
        if mode == "lyrics" and self.settings.lyrics_provider == "manual":
            self.error("Choose LRCLIB in Settings → Online sources to run batch lyric lookup.")
            return
        if not eligible:
            self.finish_job_display(
                "No eligible songs",
                "Covers require saved songs without embedded artwork in the selected category. "
                "Save unsaved edits first."
                if mode == "covers"
                else "Save any unsaved edits first. Line timing requires saved sung lyrics and zero existing line timestamps.",
            )
            return
        dialog = QDialog(self)
        names = {"lyrics": "Batch lyric lookup", "timing": "Batch line timings", "covers": "Batch album covers"}
        dialog.setWindowTitle(names[mode])
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            f"Category: {category or 'All categories'} · {len(eligible)} saved songs\n"
            + (
                "Add missing covers only for this category, including songs hidden by search.\n"
                "Search using artist/title tags or an Artist - Title filename. No account or key needed.\n"
                "Review suggested covers in a grid and accept the ones you want.\n"
                "Search allows close name matches and prefers original albums when available.\n"
                "Existing covers are skipped. Searching does not change any MP3 files.\n"
                if mode == "covers"
                else "Find the closest artist/title match and embed plain lyrics in each MP3.\n"
                if mode == "lyrics"
                else "Analyze saved lyrics locally and embed generated line timings in each MP3.\nGenerated timings need listening review.\n"
            )
            + (
                "Only covers you accept on the review screen can be saved."
                if mode == "covers"
                else "Files are saved one at a time with tag and audio verification.\n"
                "Cancel stops the remaining queue; completed saves are kept."
            )
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        listing = QListWidget()
        for session in eligible:
            listing.addItem(str(session.track.path))
        layout.addWidget(listing, 1)
        replace = QCheckBox("Also replace existing lyrics (preserve timed/excluded lines)")
        replace.setVisible(mode == "lyrics")
        layout.addWidget(replace)
        fingerprint = QCheckBox("Also verify the audio identity (optional, requires AcoustID setup)")
        fingerprint.setVisible(mode == "covers")
        fingerprint.setToolTip("Requires an AcoustID application key in Settings and fpcalc installed.")
        layout.addWidget(fingerprint)
        if len(sessions) != len(eligible):
            layout.addWidget(
                QLabel(f"{len(sessions) - len(eligible)} songs with unsaved edits or existing artwork will be skipped.")
            )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Find covers to review" if mode == "covers" else "Start and save to MP3s"
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        verify_audio = mode == "covers" and fingerprint.isChecked()
        if verify_audio and (not self.settings.acoustid_key or not shutil.which("fpcalc")):
            self.error(
                "Optional audio verification requires an AcoustID application key in Settings "
                "and fpcalc installed. Leave the audio verification option unchecked to use name lookup."
            )
            return
        tracks = [deepcopy(session.track) for session in eligible]
        settings = deepcopy(self.settings)
        provider = (
            BatchCoverProvider(self.http(), settings.acoustid_key, verify_audio)
            if mode == "covers" else LRCLibProvider(self.http())
        )
        replace_existing = replace.isChecked()
        self.player.stop()
        self.player.setSource(QUrl())
        if mode == "covers":
            self.start_job(
                lambda context: find_cover_proposals(tracks, provider, context),
                self.review_batch_covers,
                name="Finding album cover suggestions",
                retry=lambda: self.start_batch("covers"),
            )
            return
        self.start_job(
            lambda context: run_batch(
                tracks,
                mode,
                settings,
                provider,
                context,
                replace=replace_existing,
                on_saved=lambda track: store_timing_state(
                    track, Cache(self.workspace_store.directory / "timings")
                ),
            ),
            self.batch_completed,
            analysis=mode == "timing",
            name=names[mode],
            retry=lambda: self.start_batch(mode),
        )

    def review_batch_covers(self, result):
        if not result.proposals:
            self.finish_job_display(
                "Cover search cancelled" if result.cancelled else "No cover suggestions found",
                "No MP3 files were changed.\n"
                + "\n".join(f"{path.name}: {message}" for path, message in result.rows),
            )
            return
        dialog = CoverReviewDialog(result, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.accepted_proposals:
            self.finish_job_display("Cover review closed", "No MP3 files were changed.")
            return
        proposals = dialog.accepted_proposals
        accepted_paths = {proposal.track.path for proposal in proposals}
        rows = list(result.rows)
        for proposal in result.proposals:
            if proposal.track.path not in accepted_paths:
                rows.append((proposal.track.path, "Skipped: not accepted"))
        valid = []
        for proposal in proposals:
            session = next((s for s in self.sessions if s.track.path == proposal.track.path), None)
            if (
                session is None or session.track.dirty or session.lyrics_pending
                or session.track.content_hash != proposal.track.content_hash
            ):
                rows.append((proposal.track.path, "Skipped: song changed since cover search"))
            else:
                valid.append(proposal)
        if not valid:
            self.finish_job_display("No accepted covers eligible to save", "\n".join(f"{p.name}: {m}" for p, m in rows))
            return

        def completed(saved):
            saved.rows = rows + saved.rows
            self.batch_completed(saved)

        self.start_job(
            lambda context: run_batch(
                [proposal.track for proposal in valid], "covers", deepcopy(self.settings),
                AcceptedCoverProvider(valid), context,
                on_saved=lambda track: store_timing_state(
                    track, Cache(self.workspace_store.directory / "timings")
                ),
            ),
            completed,
            name="Saving accepted album covers",
            # Retrying must obtain fresh suggestions and user decisions.
            retry=lambda: self.start_batch("covers"),
        )

    def batch_completed(self, result):
        for track in result.saved:
            session = next(session for session in self.sessions if session.track.path == track.path)
            session.track = track
            session.history.clear()
            session.redo.clear()
            session.stamp_history.clear()
        if self.track:
            index = next(
                i
                for i, session in enumerate(self.sessions)
                if session.track.path == self.track.path
            )
            self.display_song(index)
        self.refresh_song_list()
        self.save_recovery()
        self.finish_job_display(
            f"Batch {'cancelled' if result.cancelled else 'completed'} · {len(result.saved)} MP3s saved",
            "\n".join(f"{path.name}: {message}" for path, message in result.rows)
            + ("\nRemaining songs were not processed." if result.cancelled else ""),
        )

    def open_path(self, path: Path):
        self.open_paths([path])

    def open_paths(self, paths: list[Path], folder: Path | None = None):
        if self.job or (not paths and folder is None):
            return
        paths = list(dict.fromkeys(path.expanduser().absolute() for path in paths))
        if any(path.suffix.lower() != ".mp3" for path in paths):
            self.error("Select MP3 files")
            return
        loaded = {session.track.path: index for index, session in enumerate(self.sessions)}
        new_paths = [path for path in paths if path not in loaded]
        if not new_paths and folder is None:
            self.song_list.setCurrentRow(loaded[paths[0]])
            return
        self.player.pause()

        def read(context):
            if folder is not None:
                context.progress("Scanning folder for MP3 files", -1)
                paths.extend(folder_mp3s(folder))
                new_paths.extend(path for path in paths if path not in loaded)
            tracks, failures, timing_warnings = [], [], []
            for number, path in enumerate(new_paths):
                prefix = f"Opening {number + 1}/{len(new_paths)} · {path.name}"
                child = JobContext(
                    lambda text, percent, number=number, prefix=prefix: context.progress(
                        f"{prefix} · {text}",
                        int((number + max(0, min(100, percent)) / 100) * 95 / len(new_paths)),
                    )
                )
                child.cancelled = context.cancelled
                child.progress("Read song", 0)
                context.check()
                try:
                    track = read_track(path, child)
                    child.progress("Restore saved word timings", 90)
                    warning = self.restore_loaded_timing(track)
                    if warning:
                        timing_warnings.append(f"{path.name}: {warning}")
                    tracks.append(track)
                except Cancelled:
                    raise
                except (OSError, ValueError, RuntimeError) as exc:
                    failures.append((path, str(exc)))
                except Exception as exc:
                    failures.append((path, str(exc)))
                context.progress(
                    f"Read {number + 1}/{len(new_paths)} songs",
                    int((number + 1) * 95 / len(new_paths)),
                )
            context.check()
            context.progress(f"Adding {len(tracks)} songs to the workspace", 95)
            return tracks, failures, timing_warnings

        def loaded_tracks(result):
            tracks, failures, timing_warnings = result
            self.remember_current_song()
            self.sessions.extend(SongSession(track) for track in tracks)
            blocker = QSignalBlocker(self.song_list)
            self.song_list.addItems([""] * len(tracks))
            del blocker
            first = next(
                (
                    index
                    for index, session in enumerate(self.sessions)
                    if paths and session.track.path == paths[0]
                ),
                None,
            )
            if first is not None:
                blocker = QSignalBlocker(self.song_list)
                self.song_list.setCurrentRow(first)
                del blocker
                self.display_song(first)
            elif tracks:
                first = len(self.sessions) - len(tracks)
                blocker = QSignalBlocker(self.song_list)
                self.song_list.setCurrentRow(first)
                del blocker
                self.display_song(first)
            self.preferences["open_directory"] = str(folder or paths[0].parent)
            if not paths:
                self.finish_job_display(
                    "No MP3 files found",
                    "Choose a folder containing MP3 files. Subfolders are not included.",
                )
            recent = list(
                dict.fromkeys(
                    [*(str(track.path) for track in tracks), *self.preferences.get("recent", [])]
                )
            )[:12]
            self.preferences["recent"] = recent
            self.update_recent_menu()
            if failures:
                self._failed_open_paths = [path for path, _ in failures]
                self.finish_job_display(
                    f"Loaded {len(tracks)} songs; {len(failures)} failed",
                    "\n".join(f"{path.name}: {error}" for path, error in failures),
                )
                self._retry_operation = lambda: self.open_paths(self._failed_open_paths)
                self.retry_job_button.show()
            elif paths:
                self.finish_job_display(
                    f"Loaded {len(tracks)} new songs · {len(self.sessions)} songs in workspace",
                    "\n".join(timing_warnings)
                    or "Select a song to edit lyrics and stamp line timings.",
                )
            self.schedule_recovery()

        self.start_job(
            read,
            loaded_tracks,
            name="Opening folder" if folder else f"Opening {len(new_paths)} song(s)",
        )

    def remember_current_song(self):
        if not self.track:
            return
        for session in self.sessions:
            if session.track is self.track:
                session.lyrics_pending = self._lyrics_pending
                session.playback_position = self.player.position()
                session.selected_line = max(0, self.table.currentRow())
                session.stamp_history = self._stamp_history
                session.release_id = self.release_id
                session.release_candidates = self.release_candidates
                break

    def refresh_song_list(self):
        for index, session in enumerate(self.sessions):
            track = session.track
            metadata = track.proposed_metadata
            title = metadata.title or track.path.stem
            artist = metadata.artist or "Unknown artist"
            self.song_list.item(index).setData(Qt.ItemDataRole.UserRole, timing_category(track))
            pending = self._lyrics_pending if track is self.track else session.lyrics_pending
            suffix = " · Unsaved" if track.dirty or pending else ""
            metrics = self.song_list.fontMetrics()
            width = max(80, self.song_list.viewport().width() - 30)
            self.song_list.item(index).setText(
                f"{metrics.elidedText(('• ' if suffix else '') + title, Qt.TextElideMode.ElideRight, width)}\n"
                f"{metrics.elidedText(artist, Qt.TextElideMode.ElideRight, width)}"
            )
            self.song_list.item(index).setSizeHint(QSize(0, metrics.height() * 2 + 8))
            self.song_list.item(index).setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{title}, {artist}, {timing_category(track)}"
                + (", Unsaved changes" if suffix else ""),
            )
            self.song_list.item(index).setToolTip(
                f"{track.path}\n{timing_category(track)}\n{track.status}{suffix}"
            )
        self.song_count.setText(
            f"{len(self.sessions)} song(s) loaded" if self.sessions else "No songs loaded"
        )
        for index, category in enumerate(CATEGORIES, 1):
            count = sum(timing_category(session.track) == category for session in self.sessions)
            self.category_filter.setItemText(index, f"{category} ({count})")
        self.filter_songs(self.song_search.text())

    def restore_loaded_timing(self, track: Track):
        """Restore timing files without touching widgets; safe during background loading."""
        # A save result already carries the current rich timing state. Never
        # replace it with an older cache if updating that cache failed.
        if track.status != "Saved" and not track.aligned_words:
            if not restore_timing_state(track, Cache(self.workspace_store.directory / "timings")):
                if restore_timing_state(track, Cache(Path(self.settings.cache_directory))):
                    try:
                        store_timing_state(track, Cache(self.workspace_store.directory / "timings"))
                    except OSError:
                        return "Corrected timings loaded, but could not be copied into protected storage. Export a timing project."
        return ""

    def load_track(self, track: Track, restoring=False):
        if not restoring:
            warning = self.restore_loaded_timing(track)
            if warning:
                self.statusBar().showMessage(warning)
        self.remember_current_song()
        index = next(
            (
                index
                for index, session in enumerate(self.sessions)
                if session.track.path == track.path
            ),
            -1,
        )
        if index < 0:
            self.sessions.append(SongSession(track))
            self.song_list.addItem("")
            index = len(self.sessions) - 1
        else:
            self.sessions[index].track = track
            self.sessions[index].lyrics_pending = False
            self.sessions[index].stamp_history = []
        blocker = QSignalBlocker(self.song_list)
        self.song_list.setCurrentRow(index)
        del blocker
        self.display_song(index)
        self.statusBar().showMessage("Song loaded; the MP3 has not been modified")

    @Slot(int)
    def select_song(self, index):
        if index < 0 or index >= len(self.sessions):
            return
        if self.job:
            blocker = QSignalBlocker(self.song_list)
            self.song_list.setCurrentRow(
                next(i for i, session in enumerate(self.sessions) if session.track is self.track)
            )
            del blocker
            return
        self.remember_current_song()
        self.display_song(index)

    def display_song(self, index):
        session = self.sessions[index]
        track = session.track
        self.player.pause()
        self.track = track
        for job in self.wave_jobs:
            job.context.cancelled.set()
        self.waveform.set_audio(track.audio.duration)
        if hasattr(self, "wave_action") and self.wave_action.isChecked():
            self.load_waveform()
        self.release_id = session.release_id
        self.release_candidates = session.release_candidates
        self._lyrics_pending = session.lyrics_pending
        self._active = None
        self._stamp_history = session.stamp_history
        self._rendering = True
        for key, edit in self.metadata_fields.items():
            edit.setText(getattr(track.proposed_metadata, key))
        self.lyrics_editor.setPlainText(track.display_lyrics)
        self._rendering = False
        self.song_heading.setText(track.proposed_metadata.title or track.path.stem)
        self.file_label.setText(
            f"{track.path.name} · {format_timestamp(track.audio.duration)} · {track.audio.bitrate // 1000} kbps"
        )
        self.file_label.setToolTip(
            f"{track.path}\n{track.audio.sample_rate} Hz · {track.audio.channels} channels · ID3 {track.audio.id3_version}"
        )
        self.update_file_details()
        self.show_artwork()
        self.render_table()
        self._pending_playback_position = session.playback_position
        self.player.setSource(QUrl.fromLocalFile(str(track.path)))
        self.player.setPosition(session.playback_position)
        self.duration_changed(round(track.audio.duration * 1000))
        if not self.job:
            self.activity_dialog.hide()
        if self.table.rowCount():
            self.table.selectRow(min(session.selected_line, self.table.rowCount() - 1))
        self._title()
        self._enabled()
        self._edit_baseline = self.edit_snapshot()
        if self._lyrics_pending:
            self.statusBar().showMessage(
                "Lyrics were edited. Apply lyric changes to refresh the timing table."
            )
        else:
            self.statusBar().showMessage(
                "Showing selected song; your edits in other songs are kept"
            )

    @Slot(QMediaPlayer.MediaStatus)
    def restore_song_position(self, status):
        if (
            status == QMediaPlayer.MediaStatus.LoadedMedia
            and self._pending_playback_position is not None
        ):
            position = self._pending_playback_position
            self._pending_playback_position = None
            self.player.setPosition(position)

    def update_file_details(self):
        track = self.track
        try:
            size = f"{track.path.stat().st_size / (1024 * 1024):.2f} MB"
        except OSError:
            size = "File unavailable"
        values = {
            "filename": track.path.name,
            "location": str(track.path.parent),
            "duration": format_timestamp(track.audio.duration),
            "size": size,
            "bitrate": f"{track.audio.bitrate / 1000:.0f} kbps",
            "sample_rate": f"{track.audio.sample_rate:,} Hz",
            "channels": {1: "Mono", 2: "Stereo"}.get(
                track.audio.channels, f"{track.audio.channels} channels"
            ),
            "id3": "None"
            if track.audio.id3_version == "none"
            else f"ID3 {track.audio.id3_version}",
            "lyrics_language": (
                track.lyrics_language if track.lyrics_frame_key else "No embedded plain lyrics"
            ),
            "saved_category": timing_category(track),
        }
        for key, value in values.items():
            self.file_details[key].setText(value)

    def show_artwork(self):
        self.artwork_label.clear()
        self.cover_details.setText("No cover image")
        if self.track and self.track.artwork:
            pixmap = QPixmap()
            if not pixmap.loadFromData(self.track.artwork.data) or pixmap.isNull():
                self.artwork_label.setText("Artwork could not be displayed. Load another image.")
                self.cover_details.setText("Unreadable cover image")
                self.artwork_label.setWordWrap(True)
                return
            self.cover_details.setText(
                f"{pixmap.width()} × {pixmap.height()} pixels\n"
                f"{self.track.artwork.mime} · {len(self.track.artwork.data) / 1024:.0f} KB"
            )
            self.artwork_label.setPixmap(
                pixmap.scaled(
                    self.artwork_label.width() - 4,
                    self.artwork_label.height() - 4,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.artwork_label.setText("No artwork")

    def metadata_edited(self):
        if self.track and not self._rendering:
            self.track.proposed_metadata = Metadata(
                **{key: edit.text() for key, edit in self.metadata_fields.items()}
            )
            self.release_id = ""
            self.release_candidates = []
            self.record_change("edit song details")
            self._enabled()
            self._title()

    def lyrics_edited(self):
        if self.track and not self._rendering:
            self.track.display_lyrics = self.lyrics_editor.toPlainText()
            self._lyrics_pending = True
            self._title()
            self.update_timing_selection()
            self.schedule_recovery()
            self.statusBar().showMessage(
                "Apply the edited lyrics before timing, exporting, or saving"
            )

    def apply_lyrics(self):
        if not self.track:
            return False
        before = self.edit_snapshot()
        previous = self.track.aligned_lines
        new_text = self.lyrics_editor.toPlainText()
        proposed = split_lyrics(new_text, previous)
        retained = {line.line_id for line in proposed}
        affected = sum(line.start is not None and line.line_id not in retained for line in previous)
        if (
            affected
            and QMessageBox.question(
                self,
                "Apply lyric changes",
                f"{affected} timed occurrence(s) will be removed or lose their timing.\nUnchanged repeated lines keep their individual timings. This change can be undone.",
                QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Apply
        ):
            return False
        self.track.display_lyrics = self.lyrics_editor.toPlainText()
        self.track.aligned_lines = proposed
        self._lyrics_pending = False
        self.record_change("apply lyric changes", before)
        self.render_table()
        self._title()
        return True

    def render_table(self):
        self._rendering = True
        selected = self.table.currentRow()
        self.table.setRowCount(len(self.track.aligned_lines) if self.track else 0)
        metrics = self.table.fontMetrics()
        self.table.setColumnWidth(0, max(116, metrics.horizontalAdvance("00:00.000") + 24))
        self.table.setColumnWidth(
            2, max(155, min(280, metrics.horizontalAdvance("Words need review") + 24))
        )
        self.table.setColumnWidth(3, max(100, metrics.horizontalAdvance("10 timed") + 24))
        if self.track:
            for row, line in enumerate(self.track.aligned_lines):
                estimated = sum(word.source == "estimated" for word in line.words)
                status = (
                    "Not sung"
                    if line.excluded
                    else "Not timed"
                    if line.start is None
                    else "Low support · listen"
                    if line.source == "ai" and line.confidence < self.settings.confidence_threshold
                    else "Line needs listening"
                    if not line.line_reviewed
                    else "Words need review"
                    if line.words and not line.words_reviewed
                    else "Reviewed"
                )
                word_status = (
                    f"{len(line.words)} · {estimated} est."
                    if estimated
                    else f"{len(line.words)} timed"
                    if line.words
                    else "Line only"
                    if line.start is not None
                    else "Untimed"
                )
                if estimated and not line.words_reviewed:
                    status = "Estimated · review"
                for column, text in enumerate(
                    [format_timestamp(line.start), line.text, status, word_status]
                ):
                    item = QTableWidgetItem(text)
                    item.setToolTip(
                        f"{line.note}\nModel support: {line.confidence:.0%} (heuristic). Source: {line.source}\nOccurrence: {row + 1}"
                    )
                    if column == 3:
                        item.setToolTip("Click to open word timings for this line.")
                    if column in (2, 3):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, column, item)
            if self.table.rowCount():
                self.table.selectRow(max(0, min(selected, self.table.rowCount() - 1)))
        self._rendering = False
        self.table.resizeRowsToContents()
        lines = self.track.aligned_lines if self.track else []
        s = review_summary(lines)
        self.timing_summary.setText(
            f"{s['timed']} / {s['total']} lines timed · {s['words']} words timed · {s['missing']} missing · {s['excluded']} not sung · {s['review']} to review"
        )
        self.filter_review_rows()
        self.update_workflow()
        self.position_changed(self.player.position())
        self._edit_baseline = self.edit_snapshot()

    def table_edited(self, item):
        if self._rendering or not self.track or self._lyrics_pending:
            return
        before_state = self.edit_snapshot()
        line = self.track.aligned_lines[item.row()]
        try:
            if item.column() == 0:
                line.move_timestamp(parse_timestamp(item.text()), self.track.audio.duration)
            elif item.column() == 1:
                before = line.text
                occurrence = sum(
                    previous.text == before for previous in self.track.aligned_lines[: item.row()]
                )
                rows = self.track.display_lyrics.splitlines()
                matching = [index for index, raw in enumerate(rows) if raw.strip() == before]
                if occurrence < len(matching):
                    rows[matching[occurrence]] = item.text()
                    self.track.display_lyrics = "\n".join(rows)
                else:
                    self.track.display_lyrics = "\n".join(
                        item.text() if index == item.row() else value.text
                        for index, value in enumerate(self.track.aligned_lines)
                    )
                line.text = item.text()
                line.line_reviewed = False
                line.words_reviewed = False
                line.excluded = False
                if line.source != "manual":
                    line.set_timestamp(None, self.track.audio.duration)
                else:
                    line.words = []
                self._rendering = True
                self.lyrics_editor.setPlainText(self.track.display_lyrics)
                self._rendering = False
            self.record_change("edit lyric timing/text", before_state)
            self.render_table()
            self._title()
        except ValueError as exc:
            self.error(str(exc))
            self.render_table()

    def timeline_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.timeline.isEnabled():
            value = int(
                max(0, min(1, event.position().x() / max(1, self.timeline.width())))
                * self.timeline.maximum()
            )
            self.timeline.setValue(value)
            self.player.setPosition(value)
        QSlider.mousePressEvent(self.timeline, event)

    def toggle_play(self):
        if not self.track:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_playback(self):
        if self.track:
            self.player.stop()

    @Slot(int)
    def duration_changed(self, duration):
        self.timeline.setMaximum(duration)

    @Slot(int)
    def position_changed(self, position):
        if not self.track:
            return
        self.waveform.position = position / 1000
        self.waveform.boundaries = [
            line.start
            for line in self.track.aligned_lines
            if line.start is not None and not line.excluded
        ]
        row = self.table.currentRow()
        if row >= 0:
            try:
                window = word_timing_window(
                    self.track.aligned_lines, row, self.track.audio.duration
                )
                self.waveform.selection = (window.start, window.end)
            except ValueError:
                self.waveform.selection = None
        self.waveform.update()
        # Styling also emits itemChanged; it must never become a timestamp edit.
        _signal_blocker = QSignalBlocker(self.table)
        if not self.timeline.isSliderDown():
            timeline_blocker = QSignalBlocker(self.timeline)
            self.timeline.setValue(position)
            del timeline_blocker
        self.position_label.setText(
            f"{format_timestamp(position / 1000)} / {format_timestamp(self.track.audio.duration)}"
        )
        index = active_line(self.track.aligned_lines, position / 1000)
        previous_active = self._active
        self._active = index
        if (
            self.is_playing()
            and not QApplication.activeModalWidget()
            and self.table.state() != QAbstractItemView.State.EditingState
        ):
            follow = self.playback_line(position, index)
            if follow is not None and not self.table.isRowHidden(follow):
                if self.table.currentRow() != follow:
                    self.table.selectRow(follow)
                    self.table.scrollToItem(self.table.item(follow, 1))
                elif index != previous_active:
                    self.table.scrollToItem(self.table.item(follow, 1))
        self.update_timing_selection()

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def explain_selection_lock(self):
        self.statusBar().showMessage("Pause playback to select or edit a different lyric line.")

    def playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Ⅱ  Pause (space)" if playing else "▶  Play (space)")
        self.table.set_playback_locked(playing)
        self.review_filter.setEnabled(self.track is not None and self.job is None and not playing)
        self.position_changed(self.player.position())
        self.update_workflow()

    def playback_line(self, position, active):
        seconds = position / 1000
        row = self.table.currentRow()
        if (
            0 <= row < len(self.track.aligned_lines)
            and self.track.aligned_lines[row].start is None
            and not self.track.aligned_lines[row].excluded
        ):
            try:
                window = word_timing_window(
                    self.track.aligned_lines, row, self.track.audio.duration
                )
                if window.start <= seconds < window.end:
                    return row
            except ValueError:
                pass
        # Retain a known sung line until its word span ends, then offer the gap's
        # missing lyric. A line-only timestamp cannot supply an invented end.
        if active is not None and self.track.aligned_lines[active].end is not None:
            return active
        pending = pending_line_at(self.track.aligned_lines, seconds, self.track.audio.duration)
        return pending if pending is not None else active

    def paint_selected_line(self):
        if not self.track:
            return
        blocker = QSignalBlocker(self.table)
        selected = self.table.currentRow()
        for row, line in enumerate(self.track.aligned_lines):
            color = QColor(
                timing_selection_color(self.table).name()
                if row == selected
                else self.table.palette().alternateBase().color().name()
                if line.start is None
                else (
                    "#303030" if self.table.palette().base().color().lightness() < 128 else REVIEW
                )
                if self.needs_review(line)
                else self.table.palette().base().color().name()
            )
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setBackground(color)
                    item.setForeground(
                        QColor("#202020")
                        if self.table.palette().base().color().lightness() >= 128
                        and (
                            row == selected or (line.start is not None and self.needs_review(line))
                        )
                        else self.table.palette().text().color()
                    )
        del blocker

    def timing_cell_clicked(self, row, column):
        if (
            not self.track
            or self.job
            or self._lyrics_pending
            or not 0 <= row < len(self.track.aligned_lines)
        ):
            return
        if column == 3:
            self.player.pause()
            self.table.selectRow(row)
            self.edit_word_timings()
        elif self.is_playing():
            self.pause_at_line(row)
        else:
            self.seek_line(row, column)

    def pause_at_line(self, row):
        if not self.track or not 0 <= row < len(self.track.aligned_lines):
            return
        if self.track.aligned_lines[row].start is None:
            self.statusBar().showMessage("This line has no start time yet. Pause to select it.")
            return
        self.player.pause()
        self.table.selectRow(row)
        self.seek_line(row)

    def seek_line(self, row, column=0):
        if self.is_playing():
            self.explain_selection_lock()
            return
        if self.track and 0 <= row < len(self.track.aligned_lines):
            line = self.track.aligned_lines[row]
            if line.start is not None:
                self.player.setPosition(round(line.start * 1000))
            self.update_timing_selection()

    def _set_selected(self, position):
        if self._lyrics_pending or self.job:
            return
        row = self.table.currentRow()
        if self.track and row >= 0:
            try:
                self.track.aligned_lines[row].move_timestamp(position, self.track.audio.duration)
                self.record_change("clear timestamp" if position is None else "adjust timestamp")
                self.render_table()
                self._title()
            except ValueError as exc:
                self.error(str(exc))

    def stamp_line(self):
        if self.job or not self.track:
            return
        if self._lyrics_pending and not self.apply_lyrics():
            return
        row = self.stamp_target()
        if row is None:
            self.statusBar().showMessage(
                "The selected line is already timed or no line is selected. Existing timestamps were kept."
            )
            return
        try:
            window = word_timing_window(self.track.aligned_lines, row, self.track.audio.duration)
            position = self.player.position() / 1000
            if not (
                window.start <= position < window.end
                or position == window.end == self.track.audio.duration
            ):
                raise ValueError(
                    f"This line belongs between {format_timestamp(window.start)} and {format_timestamp(window.end)}. Seek there before stamping."
                )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        if not self.record_stamp(row, position):
            return
        self.update_timing_selection()

    def record_stamp(self, row, position=None):
        line = self.track.aligned_lines[row]
        before = deepcopy(line)
        try:
            line.move_timestamp(
                self.player.position() / 1000 if position is None else position,
                self.track.audio.duration,
            )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return False
        self._stamp_history.append((row, before, deepcopy(line)))
        self.record_change("stamp line")
        self.table.selectRow(row)
        self.render_table()
        self._title()
        self.statusBar().showMessage(f"Timed line {row + 1} at {format_timestamp(line.start)}")
        return True

    def undo_stamp(self):
        session = self.current_session()
        if self.job or not session or not session.history:
            return
        command = session.history[-1]
        if self.edit_snapshot() != command["after"]:
            self.statusBar().showMessage(
                "The draft changed after this edit. Apply or undo pending edits before Undo."
            )
            return
        session.history.pop()
        session.redo.append(command)
        self.restore_edit(command["before"])
        self.statusBar().showMessage(f"Undid {command['label']}")

    def unmatch_line(self):
        if self.is_playing():
            self.explain_selection_lock()
            return
        self._set_selected(None)

    def clear_all_timings(self):
        if not self.track or self.job or self._lyrics_pending:
            return
        if self.is_playing():
            self.explain_selection_lock()
            return
        before = self.edit_snapshot()
        count = 0
        for line in self.track.aligned_lines:
            if line.start is not None or line.end is not None or line.words:
                count += 1
                excluded = line.excluded
                line.set_timestamp(None, self.track.audio.duration)
                line.excluded = excluded
        if not count:
            return
        self.record_change("clear all timings", before)
        self.render_table()
        self._title()
        self.statusBar().showMessage(
            "Cleared all line and word timings for this song. Undo is available.", 8000
        )

    @staticmethod
    def has_automatic_words(line):
        return bool(line.words) and all(word.source in {"ai", "estimated"} for word in line.words)

    def clear_automatic_word_timings(self):
        if not self.track or self.job or self._lyrics_pending:
            return
        before = self.edit_snapshot()
        count = 0
        for line in self.track.aligned_lines:
            if self.has_automatic_words(line):
                count += len(line.words)
                line.words = []
                line.words_reviewed = False
        if not count:
            return
        self.record_change("clear automatic word timings", before)
        self.render_table()
        self._title()
        self.statusBar().showMessage(
            f"Cleared {count} automatic word timings. Line times and manual corrections kept. Undo is available.",
            8000,
        )

    def edit_word_timings(self):
        if self.job or not self.track:
            return
        self.player.pause()
        if self._lyrics_pending and not self.apply_lyrics():
            return
        row = self.table.currentRow()
        if row < 0:
            self.statusBar().showMessage("Select a lyric line first.")
            return
        if self.track.aligned_lines[row].excluded:
            self.statusBar().showMessage("This line is marked not sung; reopen review to time it.")
            return
        try:
            window = word_timing_window(self.track.aligned_lines, row, self.track.audio.duration)
        except ValueError as exc:
            self.error(str(exc))
            return
        dialog = WordTimingDialog(
            self.track.aligned_lines[row],
            self.player,
            self.track.audio.duration,
            self,
            next_start=window.end if window.end < self.track.audio.duration else None,
            window=window,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_line is not None
        if accepted:
            self.track.aligned_lines[row] = dialog.result_line
            self.record_change("apply word timings")
            self.render_table()
            if not self.table.isRowHidden(row):
                self.table.selectRow(row)
            self._title()
        dialog.deleteLater()

    def eventFilter(self, watched, event):
        if (
            event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
            or not self.isActiveWindow()
            or not self.track
        ):
            return super().eventFilter(watched, event)
        focus = QApplication.focusWidget()
        if (focus and focus.window() is not self) or QApplication.activePopupWidget():
            return False
        # Enter belongs to stamping throughout this window, while text fields,
        # inline editors and open dialogs retain their usual Enter behavior.
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self.workspace_tabs.currentIndex() == 0
            and not event.modifiers()
            and not isinstance(
                focus, (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox)
            )
        ):
            if (
                event.type() == QEvent.Type.KeyPress
                and not event.isAutoRepeat()
                and self.stamp_button.isEnabled()
            ):
                self.stamp_line()
            return True
        if (
            event.key() == Qt.Key.Key_Delete
            and self.workspace_tabs.currentIndex() == 0
            and not event.modifiers()
            and not isinstance(
                focus, (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox)
            )
        ):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self.unmatch_button.click()
            return True
        # Playback shortcuts work across the main window, including focused
        # buttons and switches. Text editors retain their typing behavior.
        if (
            event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Backspace)
            and not event.modifiers()
            and not isinstance(
                focus, (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox)
            )
        ):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                if event.key() == Qt.Key.Key_Backspace:
                    self.stop_playback()
                else:
                    self.toggle_play()
            return True
        if event.type() != QEvent.Type.KeyPress:
            return False
        if isinstance(
            focus, (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox, ToggleSwitch)
        ) or isinstance(focus, QDialog):
            return False
        if isinstance(focus, QPushButton) and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                focus.click()
            return True
        if self.workspace_tabs.currentIndex() != 0 or (
            focus is not self.table and focus is not self
        ):
            return False
        key, modifiers = event.key(), event.modifiers()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and event.isAutoRepeat():
            return True
        if modifiers & Qt.KeyboardModifier.ControlModifier and not self.job:
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self.redo_edit()
                else:
                    self.undo_stamp()
                return True
        if modifiers & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        if self.job:
            return False
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.stamp_line()
            return True
        row = self.table.currentRow()
        if self.is_playing() and key in {
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
        }:
            self.explain_selection_lock()
            return True
        if key in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
            visible = [
                index for index in range(self.table.rowCount()) if not self.table.isRowHidden(index)
            ]
            if not visible:
                return True
            candidates = (
                [index for index in visible if index < row]
                if key == Qt.Key.Key_Up
                else [index for index in visible if index > row]
            )
            next_row = (
                (candidates[-1] if key == Qt.Key.Key_Up else candidates[0])
                if candidates
                else (visible[0] if key == Qt.Key.Key_Up else visible[-1])
            )
            self.table.selectRow(next_row)
            self.seek_line(next_row)
            return True
        if key in {Qt.Key.Key_Left, Qt.Key.Key_Right} and row >= 0:
            start = self.track.aligned_lines[row].start
            if start is not None:
                step = 1 if modifiers & Qt.KeyboardModifier.ShiftModifier else 0.1
                self._set_selected(
                    max(
                        0,
                        min(
                            self.track.audio.duration,
                            start + (step if key == Qt.Key.Key_Right else -step),
                        ),
                    )
                )
            return True
        return False

    def start_job(self, operation, handler, analysis=False, name=None, retry=None):
        if self.job:
            return
        self._result_handler = handler
        self.job = Job(operation)
        self.job_name = name or ("Analyzing lyric timing" if analysis else "Working on this song")
        self._retry_operation = retry or (
            lambda: self.start_job(operation, handler, analysis=analysis, name=name, retry=retry)
        )
        self.retry_job_button.hide()
        self.job_search_button.hide()
        self.job.signals.progress.connect(self.job_progress)
        self.job.signals.activity.connect(self.job_activity)
        self.job.signals.result.connect(self.job_result)
        self.job.signals.failed.connect(self.job_failed)
        self.job.signals.cancelled.connect(self.job_cancelled)
        self.job.signals.finished.connect(self.job_finished)
        self.progress.setValue(0)
        self.progress.setRange(0, 100 if analysis else 0)
        self.job_stage = "Starting job"
        self.job_stage_started = time.monotonic()
        self.job_started = self.job_last_progress = self.job_stage_started
        self.job_percent = 0
        self.job_is_analysis = analysis
        self.job_cpu_active = None
        self.job_used_cache = False
        self.cancel_button.setText("Cancel analysis" if analysis else "Cancel operation")
        self.job_steps.setVisible(analysis)
        self.progress.setFormat("Estimated overall progress: %p%" if analysis else "%p%")
        self.job_timer.start()
        self.update_job_elapsed()
        self._enabled()
        self.pool.start(self.job)

    @Slot(str, int)
    def job_progress(self, text, percent):
        if "cached alignment" in text.lower():
            self.job_used_cache = True
        if text != self.job_stage or percent != self.job_percent:
            self.job_last_progress = time.monotonic()
        if text != self.job_stage:
            self.job_stage_started = time.monotonic()
        self.job_stage, self.job_percent = text, percent
        self.progress.setRange(0, 0 if percent < 0 else 100)
        if percent >= 0:
            self.progress.setValue(percent)
        self.update_job_elapsed()

    @Slot(object)
    def job_activity(self, activity):
        self.job_cpu_active = activity.get("cpu_active")
        self.update_job_elapsed()

    @Slot()
    def update_job_elapsed(self):
        if not self.job:
            return
        now = time.monotonic()
        total = int(now - self.job_started)
        quiet = int(now - self.job_last_progress)
        stages = [
            (0, "Prepare analysis"),
            (20, "Read audio"),
            (30, "Recognize vocals"),
            (60, "Find lyric lines"),
            (65, "Load timing model"),
            (70, "Set timestamps"),
        ]
        if self.job_is_analysis:
            current = max(i for i, (percent, _) in enumerate(stages) if self.job_percent >= percent)
            self.job_heading.setText(f"Step {current + 1} of {len(stages)} — {stages[current][1]}")
            self.job_steps.setText(
                "  →  ".join(
                    ("✓ " if index < current else "● " if index == current else "") + name
                    for index, (_, name) in enumerate(stages)
                )
            )
        else:
            self.job_heading.setText(self.job_name)
        self.job_detail.setText(self.job_stage)
        clock = f"{total // 60:02d}:{total % 60:02d}"
        if self.job.context.cancelled.is_set():
            activity = "Cancelling — your edits will be kept"
        elif self.job_cpu_active:
            activity = "Analysis is using the CPU"
        elif quiet >= 30 and self.job_is_analysis:
            activity = "No new progress reported — analysis may be waiting or stalled. You can cancel and retry."
        else:
            activity = (
                "Waiting for the next progress update"
                if self.job_is_analysis
                else "Operation in progress"
            )
        self.job_activity_label.setText(
            f"Elapsed {clock} · Last progress update {quiet}s ago\n{activity}"
        )
        self.next_step.setText(f"{self.job_stage} · {clock}")
        self.next_step.setToolTip(activity + "\nDetails: View → Activity.")
        self.progress.setToolTip(
            f"{activity}\nLast progress update {quiet}s ago. Details: View → Activity."
        )
        self.statusBar().showMessage(f"{self.job_stage} · elapsed {clock}")

    def finish_job_display(self, heading, detail):
        self.job_heading.setText(heading)
        self.job_detail.setText(detail)
        self.job_steps.setVisible(False)
        elapsed = max(0, int(time.monotonic() - self.job_started))
        self.job_activity_label.setText(f"Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}")
        self.statusBar().showMessage(heading, 8000)

    @Slot(object)
    def job_result(self, result):
        handler = self._result_handler
        self.job_timer.stop()
        self.job = None
        self.finish_job_display(
            f"{self.job_name} — completed",
            "Your changes remain in memory until you save to the MP3.",
        )
        self._enabled()
        applied = True
        if handler:
            try:
                handler(result)
            except Exception as exc:
                applied = False
                self.finish_job_display(f"{self.job_name} — could not apply result", str(exc))
                self.error(str(exc))
        if self.job is None:
            # Include the final workspace update in the elapsed time and mark
            # completion only after results have actually been applied.
            elapsed = max(0, int(time.monotonic() - self.job_started))
            self.job_activity_label.setText(f"Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}")
            if applied:
                self.progress.setRange(0, 100)
                self.progress.setValue(100)

    @Slot(str)
    def job_failed(self, text):
        self._exit_save_queue.clear()
        self._saving_on_exit = False
        self._pending_close = False
        self._close_after_save = None
        self.job_timer.stop()
        self.job = None
        self.finish_job_display(f"{self.job_name} failed — edits kept", text)
        self.retry_job_button.show()
        self._enabled()
        self.restore_player_after_save()

    @Slot()
    def job_cancelled(self):
        self._exit_save_queue.clear()
        self._saving_on_exit = False
        self._close_after_save = None
        self.job_timer.stop()
        self.finish_job_display(
            f"{self.job_name} cancelled — edits kept",
            "You can retry or continue editing this song.",
        )
        self.statusBar().showMessage("Job cancelled; edits retained")
        self.restore_player_after_save()
        self.retry_job_button.show()

    @Slot()
    def job_finished(self):
        # Result handlers may start another job. Clear only the completed sender.
        if self.job and self.sender() is self.job.signals:
            self.job = None
        if self.job is None:
            self.job_timer.stop()
        self._enabled()
        if self._pending_close and self.job is None and not getattr(self, "_saving_on_exit", False):
            QTimer.singleShot(0, self.close)
        if self.job is None and getattr(self, "_pending_startup_path", None):
            path = self._pending_startup_path
            self._pending_startup_path = None
            QTimer.singleShot(0, lambda: self.open_path(path))

    def cancel_job(self):
        if self.job:
            self.job.context.cancelled.set()
            self.job_stage = "Cancelling job"
            self.job_stage_started = time.monotonic()
            self.statusBar().showMessage(self.job_stage)

    def http(self):
        return HttpClient(Cache(Path(self.settings.cache_directory)))

    def _choose_metadata(self, candidates):
        if not candidates:
            self.finish_job_display(
                "No details found",
                "Edit the lookup terms and retry, or edit song details manually.",
            )
            self.job_search_button.show()
            self.retry_job_button.show()
            return
        self.track.identification_candidates = candidates
        if any(candidate.release_id for candidate in candidates):
            self.choose_release(candidates)
            return
        labels = [
            f"{c.confidence:.0%} · {c.metadata.artist} — {c.metadata.title} · {c.metadata.album or 'no release'} · {c.metadata.date} [{c.source}]"
            for c in candidates
        ]
        details = [
            "\n".join(
                f"{field.name}: {getattr(c.metadata, field.name)}" for field in fields(Metadata)
            )
            + "\n\n"
            + c.note
            for c in candidates
        ]
        dialog = CandidateDialog(
            "Review identification / release candidates", labels, details, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            candidate = candidates[dialog.list.currentRow()]
            if candidate.recording_id:
                self.start_job(
                    lambda context: MusicBrainzProvider(self.http()).releases(candidate, context),
                    self.choose_release,
                )
            else:
                self.apply_candidate(candidate)

    def choose_release(self, candidates):
        if not candidates:
            self.finish_job_display(
                "No releases found", "Edit search terms or choose a local cover image."
            )
            self.job_search_button.show()
            return
        candidates = merge_release_candidates(candidates)
        self.release_candidates = candidates
        dialog = ArtworkDialog(
            candidates, CoverArtProvider(self.http()), self, metadata_selection=True
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            candidate = candidates[dialog.list.currentRow()]
            # Apply the reviewed choice immediately, even if the optional details
            # request fails. A release without a cover keeps the current artwork.
            proposal = MetadataProposalDialog(
                self.track.proposed_metadata,
                candidate.metadata,
                self,
                dialog.artwork,
                allow_extra=True,
            )
            if proposal.exec() != QDialog.DialogCode.Accepted:
                return
            allowed = proposal.selected_fields()
            self.apply_candidate(candidate, allowed)
            if dialog.artwork and proposal.cover_check.isChecked():
                self.apply_artwork(dialog.artwork)

            if candidate.release_id:
                self.start_job(
                    lambda context: MusicBrainzProvider(self.http()).release(candidate, context),
                    lambda extra: self.apply_candidate(extra, allowed),
                    name="Loading selected release details",
                )

    def apply_candidate(self, candidate, selected_fields=None):
        if selected_fields is None:
            proposal = MetadataProposalDialog(
                self.track.proposed_metadata, candidate.metadata, self
            )
            if proposal.exec() != QDialog.DialogCode.Accepted:
                return
            selected_fields = proposal.selected_fields()
        self.release_id = candidate.release_id
        for field in fields(Metadata):
            value = getattr(candidate.metadata, field.name)
            if value and field.name in selected_fields:
                self.metadata_fields[field.name].setText(value)
                setattr(self.track.proposed_metadata, field.name, value)
        self.record_change("apply metadata proposal")
        self._title()
        self._enabled()
        self.statusBar().showMessage("Proposal applied to form; MP3 will change only on Save")

    def find_metadata(self):
        if self.settings.metadata_provider == "manual":
            self.statusBar().showMessage("Manual metadata provider selected; edit the fields above")
            return
        metadata = deepcopy(self.track.proposed_metadata)
        filename_artist, filename_title = parse_filename(self.track.path.name)
        metadata.title = metadata.title or filename_title
        metadata.artist = metadata.artist or filename_artist
        metadata.artist, metadata.title = self.lookup_terms(metadata.artist, metadata.title)
        self.start_job(
            lambda context: MusicBrainzProvider(self.http()).search_covers(
                metadata.artist, metadata.title, context
            ),
            self._choose_metadata,
            name="Searching MusicBrainz details and cover",
            retry=self.find_metadata,
        )

    def filename_suggestion(self):
        artist, title = parse_filename(self.track.path.name)
        from .model import Candidate

        self._choose_metadata(
            [
                Candidate(
                    Metadata(artist=artist, title=title),
                    0.0,
                    "Filename",
                    note="Unverified filename parsing",
                )
            ]
        )

    def fingerprint(self):
        track = deepcopy(self.track)
        self.start_job(
            lambda context: AcoustIDService(self.http(), self.settings.acoustid_key).identify(
                track, context
            ),
            self._choose_metadata,
        )

    def find_lyrics(self):
        if self.settings.lyrics_provider == "manual":
            self.lyrics_editor.setFocus()
            self.statusBar().showMessage(
                "Manual lyrics provider selected; paste lyrics into the editor"
            )
            return
        metadata = deepcopy(self.track.proposed_metadata)
        metadata.artist, metadata.title = self.lookup_terms(metadata.artist, metadata.title)
        self.start_job(
            lambda context: LRCLibProvider(self.http()).search(
                metadata.artist, metadata.title, context
            ),
            self.choose_lyrics,
            name="Searching LRCLIB lyrics",
            retry=self.find_lyrics,
        )

    def choose_lyrics(self, candidates):
        if not candidates:
            self.finish_job_display(
                "No plain lyrics found",
                "Edit the lookup terms and retry, or paste known lyrics into the editor.",
            )
            self.job_search_button.show()
            self.retry_job_button.show()
            self.lyrics_editor.setFocus()
            return
        dialog = CandidateDialog(
            "Choose plain lyrics",
            [f"{c.artist} — {c.title} · {c.album}" for c in candidates],
            [c.lyrics for c in candidates],
            self,
            current_text=self.track.display_lyrics,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            before = self.edit_snapshot()
            if (
                self.track.display_lyrics
                and QMessageBox.question(
                    self,
                    "Replace lyrics",
                    "Replace your current lyric draft with this result?\nChanged timed occurrences will be reviewed before rebuilding, and replacement can be undone.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            self.lyrics_editor.setPlainText(candidates[dialog.list.currentRow()].lyrics)
            if self.apply_lyrics():
                self.record_change("replace lyrics", before)
            else:
                self.restore_edit(before)

    def find_artwork(self):
        metadata = deepcopy(self.track.proposed_metadata)
        filename_artist, filename_title = parse_filename(self.track.path.name)
        remembered = deepcopy(self.release_candidates)
        artist, title = self.lookup_terms(
            metadata.artist or filename_artist, metadata.title or filename_title
        )

        def search(context):
            try:
                found = MusicBrainzProvider(self.http()).search_covers(artist, title, context)
            except (RuntimeError, ValueError):
                context.check()
                if not remembered:
                    raise
                context.progress("Search unavailable; showing previously found editions", 100)
                return remembered
            # Fresh, ranked matches take precedence over old suggestions and tags.
            # An incorrect existing album must not override the original-album lookup.
            return found

        self.start_job(
            search, self.choose_artwork, name="Searching album covers", retry=self.find_artwork
        )

    def choose_artwork(self, candidates):
        if not candidates:
            self.finish_job_display(
                "No cover editions found", "Edit lookup terms or choose a local image."
            )
            self.job_search_button.show()
            return
        candidates = merge_release_candidates(candidates)
        self.release_candidates = candidates
        dialog = ArtworkDialog(candidates, CoverArtProvider(self.http()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.artwork:
            self.apply_artwork(dialog.artwork)
            self.statusBar().showMessage("Selected artwork shown; it will be embedded only on Save")

    def apply_artwork(self, art):
        self.track.artwork = art
        self.record_change("replace artwork")
        self.show_artwork()
        self._title()

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load cover artwork", "", "Images (*.jpg *.jpeg *.png *.webp)"
        )
        if path:

            def prepare(context):
                if Path(path).stat().st_size > 15 * 1024 * 1024:
                    raise ValueError("Artwork exceeds 15 MB")
                return prepare_artwork(Path(path).read_bytes())

            self.start_job(prepare, self.apply_artwork)

    def analyze(self, fresh=False):
        if not self.track or self.job:
            return
        if self._lyrics_pending and not self.apply_lyrics():
            return
        if not self.track.aligned_lines:
            self.lyrics_editor.setFocus()
            self.statusBar().showMessage("Add sung lyric lines before analysis.")
            return
        if any(line.start is not None for line in self.track.aligned_lines):
            if (
                QMessageBox.question(
                    self,
                    "Replace timing",
                    "Replace the current timing with a new local analysis?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        track, settings = deepcopy(self.track), deepcopy(self.settings)
        self.workspace_tabs.setCurrentIndex(0)
        self.start_job(
            lambda context: run_alignment(track, settings, context, fresh=fresh),
            self.apply_alignment,
            analysis=True,
        )

    def apply_alignment(self, lines):
        from collections import defaultdict, deque

        previous = defaultdict(deque)
        for line in self.track.aligned_lines:
            previous[line.text].append(line)
        for line in lines:
            if previous[line.text]:
                old = previous[line.text].popleft()
                line.line_id = old.line_id
                if old.excluded:
                    line.set_timestamp(None, self.track.audio.duration)
                    line.excluded = True
        self.track.aligned_lines = lines
        self.record_change("replace analysis")
        review = review_summary(lines)["review"]
        self.track.status = "Needs alignment review" if review else "Aligned"
        self.render_table()
        self._title()
        timed = sum(line.start is not None for line in lines)
        self.finish_job_display(
            (
                "Previous analysis loaded — unchanged audio and lyrics"
                if self.job_used_cache
                else "Analysis finished — review the results"
                if timed
                else "No reliable timings found"
            ),
            f"{timed} of {len(lines)} lines timed · {len(self.track.aligned_words)} words timed · {review} need review. "
            "Play the song and press Enter to stamp untimed lines, "
            "or use Word timings to review individual words."
            + (
                " Use Tools → Automatic timing → Analyze timing again from audio to run a fresh analysis."
                if self.job_used_cache or not timed
                else ""
            ),
        )
        self.statusBar().showMessage(
            f"Local alignment: {review} need listening review. Model support is heuristic, not a correctness probability."
        )

    def export_lrc(self):
        if self._lyrics_pending and not self.apply_lyrics():
            return
        if not timed_lines(self.track.aligned_lines):
            self.error("Set at least one timestamp before exporting")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export a separate timed-lyrics file (.lrc)",
            str(
                Path(self.preferences.get("export_directory", str(self.track.path.parent)))
                / self.track.path.with_suffix(".lrc").name
            ),
            "LRC lyrics (*.lrc)",
        )
        if path:
            try:
                if Path(path).suffix.lower() != ".lrc":
                    raise ValueError("Choose an .lrc filename for lyric export")
                atomic_write(Path(path), generate_lrc(self.track.aligned_lines).encode("utf-8"))
                self.preferences["export_directory"] = str(Path(path).parent)
                omitted = sum(line.start is None for line in self.track.aligned_lines)
                self.statusBar().showMessage(f"Exported LRC; {omitted} unmatched lines omitted")
            except Exception as exc:
                self.error(str(exc))

    def export_word_timings(self):
        if not self.track or self.job:
            return
        if self._lyrics_pending and not self.apply_lyrics():
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save timing project",
            str(
                Path(self.preferences.get("export_directory", str(self.track.path.parent)))
                / self.track.path.with_suffix(".timings.json").name
            ),
            "Timing project (*.json)",
        )
        if path:
            try:
                if Path(path).suffix.lower() != ".json":
                    raise ValueError("Choose a .json filename for word timing data")
                project = json.loads(generate_timing_json(self.track))
                project["artwork"] = encode_artwork(self.track.artwork)
                atomic_write(
                    Path(path),
                    (
                        json.dumps(project, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
                    ).encode("utf-8"),
                )
                self.preferences["export_directory"] = str(Path(path).parent)
                self.statusBar().showMessage("Exported line and word timing data; MP3 unchanged")
            except Exception as exc:
                self.error(str(exc))

    def save(self):
        if self.job or not self.track:
            return
        if self._lyrics_pending and not self.apply_lyrics():
            return
        proposed = changes(self.track)
        if not proposed:
            self.statusBar().showMessage("No changes to save")
            return
        dialog = SaveReviewDialog(self.track, proposed, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._exit_save_queue.clear()
            self._pending_close = False
            return
        self._save_position = self.player.position()
        self.player.stop()
        self.player.setSource(QUrl())
        track = deepcopy(self.track)
        self.start_job(
            lambda context: save_track(track, context=context),
            self.saved,
            name=f"Saving {track.path.name}",
        )

    def saved(self, track):
        cache_saved = True
        try:
            store_timing_state(track, Cache(self.workspace_store.directory / "timings"))
        except OSError:
            cache_saved = False
        self.load_track(track)
        self._pending_playback_position = self._save_position
        self.player.setPosition(self._save_position)
        self.finish_job_display(
            f"Saved {track.path.name}",
            "Tags and compressed audio verified. Word timings are in application storage; export a timing project for a portable copy."
            if cache_saved
            else "MP3 saved, but word timing storage failed. Export a timing project now to preserve corrected words.",
        )
        self.statusBar().showMessage(
            "Saved; tags and compressed audio verified"
            if cache_saved
            else "MP3 saved; rich timing cache could not be written. Export timing JSON to preserve words."
        )
        self.save_recovery()
        if self._exit_save_queue:
            QTimer.singleShot(0, self.save_next_on_exit)
        elif getattr(self, "_saving_on_exit", False):
            QTimer.singleShot(0, self.save_next_on_exit)
        if getattr(self, "_close_after_save", None) == track.path:
            self._close_after_save = None
            QTimer.singleShot(0, self.close_song)

    def restore_player_after_save(self):
        if self.track and self.player.source().isEmpty():
            self.player.setSource(QUrl.fromLocalFile(str(self.track.path)))
            self.player.setPosition(self._save_position)

    def inspect_cache(self):
        if self.job:
            return
        directory = self.settings.cache_directory

        def review(inventory):
            if not inventory:
                self.finish_job_display(
                    "No disposable caches found",
                    "Recovery drafts and corrected timing snapshots are kept in application storage.",
                )
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Choose disposable caches to remove")
            root = QVBoxLayout(dialog)
            explanation = QLabel(
                "Removed search, artwork, waveform and original AI results can be recreated. Corrected timings, recovery drafts, portable projects and model weights are preserved."
            )
            explanation.setWordWrap(True)
            root.addWidget(explanation)
            checks = {}
            for name, files in inventory.items():
                check = QCheckBox(
                    f"{name}: {len(files)} files · {sum(size for _, size in files) / (1024 * 1024):.1f} MB"
                )
                checks[name] = check
                root.addWidget(check)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Remove selected caches")
            tone(buttons.button(QDialogButtonBox.StandardButton.Ok), "danger")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            root.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen = [name for name, check in checks.items() if check.isChecked()]
                if chosen:
                    self.start_job(
                        lambda context: clear_disposable(directory, inventory, chosen, context),
                        lambda count: self.finish_job_display(
                            "Cache cleanup completed",
                            f"Removed {count} disposable files. Corrected timings and drafts kept.",
                        ),
                        name="Removing selected disposable caches",
                    )

        self.start_job(
            lambda context: disposable_inventory(directory, context),
            review,
            name="Inspecting disposable caches",
        )

    def edit_settings(self):
        dialog = SettingsDialog(self.settings, self)
        dialog.save_callback = lambda settings: settings.save()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                settings = dialog.settings()
                self.settings = settings
                self.workspace_store = WorkspaceStore(settings.workspace_directory)
                self.save_recovery()
                if self.track:
                    self.render_table()
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self.error(str(exc))

    def toggle_waveform(self, visible):
        self.waveform.setVisible(visible)
        if visible:
            self.load_waveform()

    def load_waveform(self):
        if not self.track:
            return
        track = self.track
        self.waveform.message = "Reading local audio…"
        job = Job(
            lambda context: decode_overview(
                track.path,
                track.audio.duration,
                context,
                Cache(Path(self.settings.cache_directory)),
                track.content_hash,
            )
        )
        self.wave_jobs.append(job)
        job.signals.result.connect(
            lambda peaks: (
                self.waveform.set_audio(track.audio.duration, peaks)
                if self.track is track
                else None
            )
        )
        job.signals.failed.connect(lambda text: self.waveform_failed(track, text))
        job.signals.finished.connect(
            lambda: self.wave_jobs.remove(job) if job in self.wave_jobs else None
        )
        self.wave_pool.start(job)

    def waveform_failed(self, track, text):
        if self.track is track:
            self.waveform.message = f"Waveform unavailable: {text}"
            self.waveform.update()

    def toggle_dark_mode(self, enabled):
        self.preferences["theme"] = "Dark" if enabled else "Light"
        apply_theme(
            QApplication.instance(),
            self.preferences["theme"].lower(),
            self.preferences.get("font_size", 10),
            self.preferences.get("reduced_motion", False),
        )
        self.save_presentation()
        if self.track:
            self.render_table()

    def edit_appearance(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Appearance")
        root = QVBoxLayout(dialog)
        form = QFormLayout()
        scheme = QComboBox()
        scheme.addItems(["Light", "Dark", "System"])
        scheme.setCurrentText(self.preferences.get("theme", "Dark"))
        size = QDoubleSpinBox()
        size.setRange(9, 20)
        size.setDecimals(0)
        size.setValue(self.preferences.get("font_size", 10))
        motion = ToggleSwitch("Reduce motion")
        motion.setChecked(self.preferences.get("reduced_motion", False))
        form.addRow("Color scheme", scheme)
        form.addRow("Font size", size)
        form.addRow(motion)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.preferences.update(
                theme=scheme.currentText(),
                font_size=int(size.value()),
                reduced_motion=motion.isChecked(),
            )
            apply_theme(
                QApplication.instance(),
                self.preferences["theme"].lower(),
                self.preferences["font_size"],
                self.preferences["reduced_motion"],
            )
            self.save_presentation()
            blocker = QSignalBlocker(self.dark_mode_toggle)
            self.dark_mode_toggle.setChecked(
                QApplication.instance().palette().base().color().lightness() < 128
            )
            del blocker
            if self.track:
                self.render_table()
                self._title()

    def focus_section(self, section):
        self.workspace_tabs.setCurrentIndex(1 if section == "details" else 0)
        if section == "details":
            self.details_scroll.ensureWidgetVisible(self.metadata_fields["title"])
            self.metadata_fields["title"].setFocus()
        elif section == "lyrics":
            self.lyrics_editor.setFocus()
            self.work_scroll.ensureWidgetVisible(self.lyrics_editor)
        else:
            self.table.setFocus()
            self.work_scroll.ensureWidgetVisible(self.table)

    def review_decision(self, decision):
        if not self.track or self.job or self._lyrics_pending or self.is_playing():
            return
        row = self.table.currentRow()
        if row < 0:
            return
        line = self.track.aligned_lines[row]
        if decision == "excluded":
            line.set_timestamp(None, self.track.audio.duration)
            line.excluded = True
        elif decision == "reopen":
            line.excluded = False
            line.line_reviewed = line.words_reviewed = False
        elif decision == "line" and line.start is not None:
            line.line_reviewed = True
        elif decision == "words" and line.words:
            line.words_reviewed = True
        else:
            self.statusBar().showMessage("Set the relevant timings before marking them reviewed.")
            return
        self.record_change("change listening review")
        self.render_table()
        self._title()

    def filter_songs(self, text):
        self.category_filter.setToolTip(self.category_filter.currentData() or "All categories")
        for row, session in enumerate(self.sessions):
            value = f"{session.track.path} {session.track.proposed_metadata.artist} {session.track.proposed_metadata.title}"
            category = self.category_filter.currentData()
            self.song_list.item(row).setHidden(
                text.casefold() not in value.casefold()
                or bool(category and timing_category(session.track) != category)
            )
        self.update_unload_category_button()

    def update_unload_category_button(self):
        category = self.category_filter.currentData()
        available = (
            self.job is None
            and bool(category)
            and any(timing_category(session.track) == category for session in self.sessions)
        )
        self.unload_category_button.setEnabled(available)
        if hasattr(self, "batch_covers_action"):
            self.batch_covers_action.setEnabled(available)

    def unload_category(self):
        category = self.category_filter.currentData()
        if self.job or not category:
            return
        self.remember_current_song()
        paths = {
            session.track.path
            for session in self.sessions
            if timing_category(session.track) == category
        }
        if not paths:
            return
        unsaved = sum(
            session.track.dirty or session.lyrics_pending
            for session in self.sessions
            if session.track.path in paths
        )
        if (
            unsaved
            and QMessageBox.question(
                self,
                "Unload category",
                f"Unload all {len(paths)} song(s) in ‘{category}’?\n\n"
                f"{unsaved} song(s) have unsaved edits that will be discarded. "
                "Cancel to keep them or save them first. Music files stay on disk.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Discard
        ):
            return
        self.remove_loaded_songs(paths)
        self.category_filter.setCurrentIndex(0)
        self.statusBar().showMessage(f"Unloaded {len(paths)} song(s) from {category}")

    def remove_loaded_songs(self, paths):
        """Remove sessions in one pass, preserving an unaffected active song."""
        self.remember_current_song()
        current_removed = self.track is not None and self.track.path in paths
        current_index = self.song_list.currentRow()
        if current_removed:
            self.player.stop()
            self.player.setSource(QUrl())
            self.track = None
        blocker = QSignalBlocker(self.song_list)
        for index in range(len(self.sessions) - 1, -1, -1):
            if self.sessions[index].track.path in paths:
                self.sessions.pop(index)
                self.song_list.takeItem(index)
        if self.sessions:
            target = (
                min(max(0, current_index), len(self.sessions) - 1)
                if current_removed
                else self.sessions.index(self.current_session())
            )
            self.song_list.setCurrentRow(target)
        del blocker
        if current_removed:
            if self.sessions:
                self.display_song(target)
            else:
                for job in self.wave_jobs:
                    job.context.cancelled.set()
                self.waveform.set_audio(0)
                self._lyrics_pending = False
                self._edit_baseline = None
                self._stamp_history = []
                self.release_id = ""
                self.release_candidates = []
                self._active = None
                self._pending_playback_position = None
                self._rendering = True
                for edit in self.metadata_fields.values():
                    edit.clear()
                self.lyrics_editor.clear()
                self.table.setRowCount(0)
                self.artwork_label.clear()
                self._rendering = False
        self.refresh_song_list()
        self._enabled()
        self.save_recovery()

    def update_recent_menu(self):
        self.recent_menu.clear()
        for path in self.preferences.get("recent", []):
            action = self.recent_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(
                lambda checked=False, source=path: self.open_path(Path(source))
            )
        self.recent_menu.setEnabled(bool(self.preferences.get("recent")))

    def song_context_menu(self, position):
        item = self.song_list.itemAt(position)
        if item is None:
            return
        menu = QMenu(self.song_list)
        unload = menu.addAction("Unload song")
        unload.setEnabled(self.job is None)
        if menu.exec(self.song_list.viewport().mapToGlobal(position)) == unload:
            self.song_list.setCurrentItem(item)
            self.close_song()

    def close_song(self):
        if not self.track or self.job:
            return
        self.remember_current_song()
        if self.track.dirty or self._lyrics_pending:
            answer = QMessageBox.question(
                self,
                "Unload song",
                f"Save changes to {self.track.path.name} before unloading it?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save:
                self._close_after_save = self.track.path
                self.save()
                if not self.job:
                    self._close_after_save = None
                return
        self.remove_loaded_songs({self.track.path})

    def close_saved_songs(self):
        if self.job:
            return
        self.remember_current_song()
        paths = [
            session.track.path
            for session in self.sessions
            if not session.track.dirty and not session.lyrics_pending
        ]
        for path in paths:
            index = next(i for i, session in enumerate(self.sessions) if session.track.path == path)
            self.song_list.setCurrentRow(index)
            self.close_song()

    def save_next_on_exit(self):
        if self.job:
            return
        if not self._exit_save_queue:
            self._saving_on_exit = False
            self._pending_close = False
            self.close()
            return
        path = self._exit_save_queue.pop(0)
        index = next(
            (i for i, session in enumerate(self.sessions) if session.track.path == path), None
        )
        if index is None:
            QTimer.singleShot(0, self.save_next_on_exit)
            return
        self.song_list.setCurrentRow(index)
        self.save()
        if not self.job:
            self._saving_on_exit = False
            self._exit_save_queue.clear()

    def lookup_terms(self, artist, title):
        values = getattr(self, "_search_hints", {}).get(str(self.track.path))
        return values if values else (artist, title)

    def edit_search_hints(self):
        if not self.track or self.job:
            return
        md = self.track.proposed_metadata
        artist, title = self.lookup_terms(md.artist, md.title)
        dialog = SearchHintsDialog(artist, title, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not hasattr(self, "_search_hints"):
                self._search_hints = {}
            self._search_hints[str(self.track.path)] = (
                dialog.artist.text().strip(),
                dialog.title.text().strip(),
            )
            self.statusBar().showMessage("Lookup terms updated; song details kept.")

    def remove_artwork(self):
        if self.track and not self.job:
            self.track.artwork = None
            self.record_change("remove artwork")
            self.show_artwork()
            self._title()

    def metadata_context_menu(self, edit, revert, position):
        menu = edit.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(revert)
        menu.exec(edit.mapToGlobal(position))

    def revert_metadata(self, key):
        if self.track and not self.job:
            setattr(self.track.proposed_metadata, key, getattr(self.track.existing_metadata, key))
            self.metadata_fields[key].setText(getattr(self.track.existing_metadata, key))
            self.record_change("revert song detail")
            self._title()

    def import_lrc(self):
        if not self.track or self.job:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import unreviewed line lyrics",
            self.preferences.get("export_directory", str(self.track.path.parent)),
            "Line lyrics (*.lrc)",
        )
        if not path:
            return
        self.player.pause()
        before = self.edit_snapshot()

        def apply_import(result):
            display, lines = result
            if (
                QMessageBox.question(
                    self,
                    "Import line lyrics",
                    f"Replace this lyric draft with {len(lines)} imported lines? All imported timing requires listening review. Word timings will be replaced. This can be undone.",
                    QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                != QMessageBox.StandardButton.Apply
            ):
                return
            self.track.display_lyrics = display
            self.track.aligned_lines = lines
            self._lyrics_pending = False
            self.record_change("import line lyrics", before)
            self.restore_edit(self.edit_snapshot())
            self.finish_job_display(
                "Line lyrics imported",
                "Imported timestamps require listening review. MP3 unchanged.",
            )

        from .projects import read_lrc

        track = deepcopy(self.track)
        self.start_job(
            lambda context: read_lrc(path, track, context),
            apply_import,
            name="Reading imported line lyrics",
        )

    def import_project(self):
        if not self.track or self.job:
            self.statusBar().showMessage("Load the source MP3 before opening its timing project.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open timing project for this MP3",
            self.preferences.get("export_directory", str(self.track.path.parent)),
            "Timing projects (*.json)",
        )
        if not path:
            return
        self.player.pause()
        before = self.edit_snapshot()

        def apply_project(project):
            changes = [
                f"{field.name.replace('_', ' ').title()}: {getattr(self.track.proposed_metadata, field.name) or '(empty)'} → {getattr(project['metadata'], field.name) or '(empty)'}"
                for field in fields(Metadata)
                if getattr(self.track.proposed_metadata, field.name)
                != getattr(project["metadata"], field.name)
            ]
            s = review_summary(project["lines"])
            message = (
                f"Apply {Path(path).name} to the working draft for {self.track.path.name}?\n{s['total']} lines · {s['words']} words · {s['excluded']} not sung · {s['review']} need review\n"
                + "\n".join(changes)
                + "\nThe lyric draft and project artwork will be applied. This can be undone; the MP3 remains unchanged until Save."
            )
            if (
                QMessageBox.question(
                    self,
                    "Apply timing project",
                    message,
                    QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                != QMessageBox.StandardButton.Apply
            ):
                return
            self.track.proposed_metadata = project["metadata"]
            self.track.display_lyrics = project["lyrics"]
            self.track.aligned_lines = project["lines"]
            self.track.artwork = project["artwork"]
            self._lyrics_pending = False
            self.record_change("import timing project", before)
            self.restore_edit(self.edit_snapshot())
            self.finish_job_display(
                "Timing project opened",
                "Review decisions and word boundaries restored. MP3 changes are still unsaved.",
            )

        from .projects import read_project

        track = deepcopy(self.track)
        self.start_job(
            lambda context: read_project(path, track, context),
            apply_project,
            name="Reading timing project",
        )

    def show_help(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Workflow and shortcuts")
        dialog.resize(760, 520)
        root = QVBoxLayout(dialog)
        tabs = QTabWidget()
        chapters = {
            "Workflow": "Load MP3s → check details and lyrics → apply lyric changes → analyze or time manually → listen and review → save. Opening and importing never write an MP3. Save always reviews the selected song.\n\nUse the Lyrics & timing tab for lyric editing and listening review. The Song details tab contains editable tags, artwork and file information. The View menu also switches tabs and focuses the lyric editor or timing table. You can edit tags and save without timing any lyrics.\n\nPaste one sung line per row. Keep [Verse] headings if useful; explicitly repeat chorus occurrences. Pending lyric edits lock the old timing table. Applying changes previews removed timings and can be undone.\n\nFind details/lyrics uses online sources only on request. Edit search terms independently from your tags. Proposals let you choose fields and keep your cover.",
            "Timing keys": "Enter stamps from anywhere in the main window except text fields and dialogs. Other timing shortcuts apply when the lyric table has focus.\n\nSpace: play/pause. Backspace: stop and return to the beginning (except in text fields). Paused Up/Down: select and seek. Left/Right: nudge ±100 ms; Shift: ±1 second. Enter: stamp only the indicated untimed line. Delete: clear the selected line timing while paused (also works after using playback controls). Ctrl+Z: undo; Ctrl+Shift+Z: redo. Ctrl+O opens songs; Ctrl+S reviews saving.\n\nPlayback owns one highlighted line and the Enter target. Pause to choose another. Held Enter cannot stamp multiple lines. To replace a timestamp, pause, select the line and use Clear time, then play and press Enter at the new start. Review decision → Not sung in this version excludes a line persistently.",
            "Word review": "Drag words or boundaries, or use the selected-word Start/End inspector. Chart Up/Down selects words. Left/Right moves by 10 ms; Ctrl changes the start and Shift changes the end. Exact table/tap controls are optional.\n\nPlayback is bounded to the review window; Loop this section repeats it. Apply + previous/next line commits the valid draft and navigates. Validation identifies the offending word. Cancel asks before discarding your manual changes.\n\nEqual spacing is an estimate. Model support is a heuristic, never a correctness probability. Mark line and word listening review separately; moving a line does not certify its words. Mark words reviewed preserves their support/provenance. Not sung leaves plain lyrics intact and omits synchronized output.",
            "Saving and recovery": "Edits stay in a working draft until Review & save to MP3. Recoverable drafts are saved after changes in application data storage, separately from caches. On startup, restore the previous workspace; changed files are rejected and missing ones can be relocated. Canceling recovery retains the old draft and pauses new recovery until you restore or discard it. Failed/skipped drafts remain preserved.\n\nClosing offers Save selected, Keep recovery drafts, Discard or Cancel. Each save follows its normal review. Undo is per song and does not automatically roll back files.\n\nMP3 and LRC retain line starts. Timing projects (.json) retain metadata, artwork, display lyrics, word boundaries, review decisions and excluded lines. Export one for a portable copy; reopen it against its original MP3. Imported LRC remains unreviewed.\n\nView → Show waveform provides local amplitude navigation and zoom; it is not lyric evidence. The playback bar shows position and duration beside the volume control. Appearance offers light/dark/system, font size and reduced motion. Settings validates fields before closing and checks local dependency presence without downloads.",
        }
        for name, text in chapters.items():
            page = QPlainTextEdit(text)
            page.setReadOnly(True)
            tabs.addTab(page, name)
        root.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        dialog.exec()

    def explain_lrc(self):
        QMessageBox.information(
            self,
            "Optional lyric file (.lrc)",
            "An .lrc file is a separate text file containing lyrics and timestamps. Some players read it alongside an MP3.\n\nYou do not need it to save your work here: Review & save to MP3 embeds the lyrics and timing directly inside the song.\n\nIf you want a separate copy for another player, use File → Export timed lyrics to a separate file (.lrc).",
        )

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if (
            not self.job
            and urls
            and all(
                url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".mp3"
                for url in urls
            )
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.open_paths([Path(url.toLocalFile()) for url in event.mimeData().urls()])
        event.acceptProposedAction()

    def closeEvent(self, event):
        for job in self.wave_jobs:
            job.context.cancelled.set()
        if self.wave_jobs:
            self._pending_close = True
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        if self.job:
            self._pending_close = True
            self.cancel_job()
            self.statusBar().showMessage("Cancelling before closing; your edits are kept.")
            event.ignore()
        elif self.confirm_discard():
            self.recovery_timer.stop()
            self.save_presentation()
            if not any(session.track.dirty or session.lyrics_pending for session in self.sessions):
                self.save_recovery()
            QApplication.instance().removeEventFilter(self)
            self.player.stop()
            event.accept()
        else:
            self._pending_close = False
            event.ignore()
