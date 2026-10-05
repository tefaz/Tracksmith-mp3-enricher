"""One release chooser with metadata, proactive cover previews, and local filtering."""

from __future__ import annotations

from urllib.parse import urlencode

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .jobs import Job
from .model import Artwork, Candidate
from .theme import ToggleSwitch


class ArtworkDialog(QDialog):
    def __init__(
        self, candidates: list[Candidate], provider, parent=None, metadata_selection: bool = False
    ):
        super().__init__(parent)
        self.setWindowTitle(
            "Find song details and cover" if metadata_selection else "Choose another cover"
        )
        self.resize(940, 640)
        self.candidates = candidates
        self.provider = provider
        self.metadata_selection = metadata_selection
        self.artworks: dict[int, Artwork] = {}
        self.failures: dict[int, str] = {}
        self.missing_covers = set()
        self.jobs: dict[int, Job] = {}
        self.closed = False
        self.details_only = False
        # App ownership avoids waiting in a dialog destructor for an HTTP read.
        self.pool = QThreadPool(QApplication.instance())
        self.pool.setMaxThreadCount(2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        explanation = QLabel(
            "Covers load automatically for the releases shown below. Choose a release to review its details and image together."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        filters = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter albums, artists, song titles, or editions…")
        self.hide_missing = ToggleSwitch("Hide unavailable covers")
        filters.addWidget(self.filter_edit, 1)
        filters.addWidget(self.hide_missing)
        layout.addLayout(filters)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setIconSize(QSize(72, 72))
        self.list.setSpacing(5)
        self.list.setWordWrap(True)
        for index in range(len(candidates)):
            self.list.addItem("")
            self.update_row(index)
        body.addWidget(self.list, 1)
        preview = QWidget()
        preview.setProperty("card", True)
        preview_layout = QVBoxLayout(preview)
        self.image = QLabel("Choose a release to preview its cover")
        self.image.setObjectName("coverPreview")
        self.image.setMinimumSize(120, 120)
        self.image.setMaximumSize(260, 260)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setWordWrap(True)
        preview_layout.addWidget(self.image, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.details)
        preview_layout.addStretch()
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setWidget(preview)
        body.addWidget(preview_scroll, 1)
        layout.addLayout(body, 1)
        self.preview_count = QLabel()
        layout.addWidget(self.preview_count)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.retry_button = QPushButton("Retry preview")
        self.retry_button.clicked.connect(self.retry_preview)
        self.buttons.addButton(self.retry_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.retry_button.hide()
        self.google_button = QPushButton("Search Google Images")
        self.google_button.setToolTip("Open image results for the selected artist and album")
        self.google_button.clicked.connect(self.search_google_images)
        self.google_button.setEnabled(False)
        self.list.currentRowChanged.connect(
            lambda row: self.google_button.setEnabled(
                row >= 0 and bool(self.candidates[row].metadata.album)
            )
        )
        self.buttons.addButton(self.google_button, QDialogButtonBox.ButtonRole.ActionRole)
        if metadata_selection:
            details_button = QPushButton("Use details only")
            details_button.clicked.connect(self.accept_details)
            self.buttons.addButton(details_button, QDialogButtonBox.ButtonRole.ActionRole)
            self.list.currentRowChanged.connect(lambda row: details_button.setEnabled(row >= 0))
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.list.currentRowChanged.connect(self.selected)
        self.list.verticalScrollBar().valueChanged.connect(self.queue_previews)
        self.filter_edit.textChanged.connect(self.filter_candidates)
        self.hide_missing.toggled.connect(self.filter_candidates)
        self.update_counts()
        if candidates:
            self.list.setCurrentRow(0)

    @property
    def artwork(self) -> Artwork | None:
        return None if self.details_only else self.artworks.get(self.list.currentRow())

    def accept_details(self):
        if self.list.currentRow() >= 0:
            self.details_only = True
            self.accept()

    def search_google_images(self):
        index = self.list.currentRow()
        if index < 0:
            return
        metadata = self.candidates[index].metadata
        artist = metadata.album_artist or metadata.artist
        query = f'"{artist}" "{metadata.album}" album cover'
        QDesktopServices.openUrl(
            QUrl("https://www.google.com/search?" + urlencode({"q": query, "tbm": "isch"}))
        )

    def retry_preview(self):
        index = self.list.currentRow()
        if index < 0 or index in self.jobs:
            return
        for row in self.related_rows(index):
            self.failures.pop(row, None)
            self.missing_covers.discard(row)
            self.update_row(row)
        self.request_preview(index)
        self.selected(index)

    def update_row(self, index):
        candidate = self.candidates[index]
        if index in self.artworks:
            status = "Cover ready"
        elif index in self.failures:
            status = (
                "Cover unavailable" if index in self.missing_covers else "Preview failed — retry"
            )
        elif not candidate.release_id:
            status = "No album edition listed"
        else:
            status = "Loading preview…" if index in self.jobs else "Preview waiting"
        title = candidate.metadata.album or "Unknown release"
        description = f"{candidate.metadata.artist} — {candidate.metadata.title}"
        date = candidate.metadata.date or "Date unknown"
        self.list.item(index).setText(
            f"{title} · {status}\n{description}\n{date} · {candidate.confidence:.0%} match"
        )
        self.list.item(index).setToolTip(
            f"{candidate.note}\nRelease: {candidate.release_id or 'not supplied'}"
        )
        if index not in self.artworks:
            icon = (
                QStyle.StandardPixmap.SP_MessageBoxWarning
                if index in self.failures
                else QStyle.StandardPixmap.SP_FileIcon
            )
            self.list.item(index).setIcon(self.style().standardIcon(icon))

    def update_counts(self):
        count = len(self.candidates)
        visible = sum(not self.list.item(index).isHidden() for index in range(count))
        self.preview_count.setText(
            f"{visible} of {count} releases shown · {len(self.artworks)} covers ready · {len(self.missing_covers)} unavailable · {len(self.failures) - len(self.missing_covers)} failed previews"
        )

    @Slot()
    def filter_candidates(self):
        if self.closed:
            return
        query = self.filter_edit.text().casefold().split()
        first = -1
        for index, candidate in enumerate(self.candidates):
            haystack = " ".join(
                (
                    candidate.metadata.album,
                    candidate.metadata.artist,
                    candidate.metadata.title,
                    candidate.metadata.date,
                    candidate.note,
                )
            ).casefold()
            visible = all(word in haystack for word in query) and not (
                self.hide_missing.isChecked() and index in self.missing_covers
            )
            self.list.item(index).setHidden(not visible)
            if visible and first < 0:
                first = index
        current = self.list.currentRow()
        if current < 0 or self.list.item(current).isHidden():
            self.list.setCurrentRow(first)
        self.update_counts()
        self.queue_previews()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.queue_previews)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.queue_previews)

    @Slot()
    def queue_previews(self):
        if self.closed:
            return
        current = self.list.currentRow()
        order = [current] if current >= 0 and not self.list.item(current).isHidden() else []
        # Prefetch visible rows only; scrolling/filtering schedules the next rows.
        if self.isVisible():
            viewport = self.list.viewport().rect()
            order += [
                index
                for index in range(len(self.candidates))
                if not self.list.item(index).isHidden()
                and self.list.visualItemRect(self.list.item(index)).intersects(viewport)
            ]
        for index in dict.fromkeys(order):
            if len(self.jobs) >= 2:
                break
            self.request_preview(index)
        self.update_counts()

    def request_preview(self, index):
        if index in self.artworks or index in self.failures or index in self.jobs:
            return
        candidate = self.candidates[index]
        if not candidate.release_id:
            self.failed_for(index, "This candidate has no release ID")
            return
        # One transfer supplies all candidates referring to the same physical release.
        if any(self.candidates[row].release_id == candidate.release_id for row in self.jobs):
            return
        job = Job(lambda context: (index, self.provider.fetch(candidate.release_id, context)))
        job.signals.setProperty("candidate_index", index)
        job.signals.result.connect(self.loaded)
        job.signals.failed.connect(self.failed)
        job.signals.finished.connect(self.job_finished)
        self.jobs[index] = job
        self.update_row(index)
        self.pool.start(job)

    @Slot(int)
    def selected(self, index):
        if self.closed:
            return
        self.image.clear()
        self.retry_button.setVisible(index in self.failures)
        self.ok_button.setEnabled(False)
        self.ok_button.setText(
            "Use details and cover" if self.metadata_selection else "Use this cover"
        )
        if index < 0:
            self.image.setText("No releases match this filter")
            self.details.clear()
            self.status.setText("Change the filter to see other releases.")
            return
        candidate = self.candidates[index]
        metadata = candidate.metadata
        self.details.setText(
            f"Song: {metadata.title}\nArtist: {metadata.artist}\nAlbum: {metadata.album}\nDate: {metadata.date or 'Unknown'}\n{candidate.note}"
        )
        if index in self.artworks:
            pixmap = QPixmap()
            if not pixmap.loadFromData(self.artworks[index].data) or pixmap.isNull():
                self.failed_for(index, "The downloaded image could not be displayed")
                return
            self.image.setPixmap(
                pixmap.scaled(
                    260,
                    260,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.status.setText(
                "This release has a cover. Your choice changes the form only; save to the MP3 when ready."
            )
            self.ok_button.setEnabled(True)
        elif index in self.failures:
            self.image.setText(
                "No cover preview available"
                if index in self.missing_covers
                else "Cover preview failed — try Retry preview"
            )
            next_step = (
                "Choose another edition, or use these details and keep your current cover."
                if self.metadata_selection
                else "Choose another edition or load a local image."
            )
            self.status.setText(self.failures[index] + ". " + next_step)
            self.ok_button.setText(
                "Use details only (keep current cover)"
                if self.metadata_selection
                else "Use this cover"
            )
            self.ok_button.setEnabled(self.metadata_selection)
        else:
            self.image.setText("Loading cover preview…")
            self.status.setText(f"Checking cover for {metadata.album}")
            self.queue_previews()

    def related_rows(self, index):
        release_id = self.candidates[index].release_id
        return [
            row
            for row, candidate in enumerate(self.candidates)
            if row == index or (release_id and candidate.release_id == release_id)
        ]

    @Slot(object)
    def loaded(self, result):
        if self.closed:
            return
        index, artwork = result
        pixmap = QPixmap()
        if not pixmap.loadFromData(artwork.data) or pixmap.isNull():
            self.failed_for(index, "The downloaded image could not be displayed")
            return
        for row in self.related_rows(index):
            self.artworks[row] = artwork
            self.update_row(row)
            self.list.item(row).setIcon(QIcon(pixmap))
        if self.list.currentRow() in self.related_rows(index):
            self.selected(self.list.currentRow())
        self.update_counts()

    def failed_for(self, index, message):
        for row in self.related_rows(index):
            if any(
                text in message.lower()
                for text in ("404", "no front cover", "no cover", "no release id")
            ):
                self.missing_covers.add(row)
            self.artworks.pop(row, None)
            self.failures[row] = message
            self.update_row(row)
            self.list.item(row).setToolTip(message)
        if self.list.currentRow() in self.related_rows(index):
            self.selected(self.list.currentRow())
        self.filter_candidates()

    @Slot(str)
    def failed(self, message):
        if not self.closed:
            self.failed_for(self.sender().property("candidate_index"), message)

    @Slot()
    def job_finished(self):
        self.jobs.pop(self.sender().property("candidate_index"), None)
        self.queue_previews()

    def done(self, result):
        self.closed = True
        for job in self.jobs.values():
            job.context.cancelled.set()
        super().done(result)
