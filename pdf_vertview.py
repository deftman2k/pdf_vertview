"""
Vertical-tab PDF viewer inspired by the Microsoft Edge tab strip.

This script opens multiple PDF documents in a single window and lists them
down the right side, allowing you to collapse the list into an icon-only rail
when space is tight. It depends on PyQt5 and PyMuPDF.

Usage:
    python pdf_vertview.py                 # launch empty viewer
    python pdf_vertview.py file1.pdf ...   # preload documents

Required packages:
    pip install PyMuPDF PyQt5
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, cast

try:
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore
except ImportError as exc:  # pragma: no cover - import guard.
    raise SystemExit("PyQt5 is required. Install with 'pip install PyQt5'.") from exc

try:
    import fitz  # type: ignore  # PyMuPDF
except ImportError as exc:  # pragma: no cover - import guard.
    raise SystemExit("PyMuPDF is required. Install with 'pip install PyMuPDF'.") from exc


# ---- Qt helpers ----------------------------------------------------------------

AlignCenter = QtCore.Qt.AlignCenter
AlignLeft = QtCore.Qt.AlignLeft
AlignVCenter = QtCore.Qt.AlignVCenter
ItemIsEnabled = QtCore.Qt.ItemIsEnabled
ItemIsSelectable = QtCore.Qt.ItemIsSelectable
LayoutLeftToRight = QtCore.Qt.LeftToRight
LayoutRightToLeft = QtCore.Qt.RightToLeft
NoPen = QtCore.Qt.NoPen
Transparent = QtCore.Qt.transparent
UserRole = QtCore.Qt.UserRole
QAction = QtWidgets.QAction

TITLE_ROLE = int(UserRole) + 1
PATH_ROLE = int(UserRole) + 2


def exec_qapplication(app: QtWidgets.QApplication) -> int:
    """Compatibility wrapper for QApplication.exec()."""
    return app.exec_()


# ---- Utility functions --------------------------------------------------------

def normalize_path(path: Path) -> Path:
    """Return an absolute path even if the file does not currently exist."""
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve(strict=False)


def qimage_from_pixmap(pix: fitz.Pixmap) -> QtGui.QImage:
    """Convert a PyMuPDF pixmap to a deep-copied QImage."""
    fmt = QtGui.QImage.Format_RGBA8888 if pix.alpha else QtGui.QImage.Format_RGB888
    image = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
    return image.copy()


def create_letter_icon(text: str, size: QtCore.QSize = QtCore.QSize(56, 72)) -> QtGui.QIcon:
    """Generate a simple colored tile icon that uses the first letter of the text."""
    display = text.strip() or "?"
    base = QtGui.QPixmap(size)
    base.fill(Transparent)

    # Derive a stable color from the file name to help distinguish tabs.
    hue = abs(hash(display.lower())) % 360
    color = QtGui.QColor.fromHsv(hue, 180, 220)

    painter = QtGui.QPainter(base)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(color)
    painter.setPen(NoPen)
    painter.drawRoundedRect(base.rect().adjusted(1, 1, -1, -1), 12, 12)

    painter.setPen(QtCore.Qt.white)
    font = painter.font()
    font.setBold(True)
    font.setPointSizeF(size.height() * 0.42)
    painter.setFont(font)
    painter.drawText(base.rect(), AlignCenter, display[0].upper())
    painter.end()

    return QtGui.QIcon(base)


# ---- PDF domain objects -------------------------------------------------------


@dataclass
class PdfDocument:
    """Container for an opened PDF document and related assets."""

    path: Path
    document: fitz.Document
    display_name: str
    thumbnail: QtGui.QIcon

    @classmethod
    def open(cls, file_path: Path) -> "PdfDocument":
        doc = fitz.open(file_path)  # Raises if the file cannot be opened.
        meta_title = (doc.metadata or {}).get("title") or ""
        display = meta_title.strip() or file_path.name
        thumb = cls._build_thumbnail_icon(doc, display)
        return cls(path=file_path, document=doc, display_name=display, thumbnail=thumb)

    @staticmethod
    def _build_thumbnail_icon(doc: fitz.Document, display: str) -> QtGui.QIcon:
        """Render a small preview icon; fall back to an initial letter tile."""
        try:
            page = doc.load_page(0)
            zoom = 64 / max(page.rect.height, 1)
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = qimage_from_pixmap(pix)
            return QtGui.QIcon(QtGui.QPixmap.fromImage(img))
        except Exception:
            return create_letter_icon(display)

    @property
    def page_count(self) -> int:
        return self.document.page_count

    def render_page(self, index: int, zoom: float) -> QtGui.QImage:
        page = self.document.load_page(index)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return qimage_from_pixmap(pix)

    def page_rect(self, index: int) -> fitz.Rect:
        return self.document.load_page(index).rect

    def close(self) -> None:
        self.document.close()

    def save_as(self, destination: Path) -> None:
        """Save the current document to a new path."""
        dest = destination.resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.document.save(str(dest))


@dataclass(frozen=True)
class FileIdentity:
    """Lightweight fingerprint for tracking files across renames."""

    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> Optional["FileIdentity"]:
        try:
            stat_result = path.stat()
        except OSError:
            return None

        inode = getattr(stat_result, "st_ino", 0) or 0
        try:
            mtime_ns = stat_result.st_mtime_ns  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover - older Python fallback.
            mtime_ns = int(stat_result.st_mtime * 1_000_000_000)

        return cls(
            device=stat_result.st_dev,
            inode=inode,
            size=stat_result.st_size,
            mtime_ns=mtime_ns,
        )

    def matches(self, other: "FileIdentity") -> bool:
        inode_match = (
            self.inode
            and other.inode
            and self.inode == other.inode
            and self.device == other.device
        )
        if inode_match:
            return True
        if self.size != other.size:
            return False
        return abs(self.mtime_ns - other.mtime_ns) <= 1_000_000


# ---- Viewer widgets -----------------------------------------------------------


class PdfViewerWidget(QtWidgets.QWidget):
    """Central widget that shows the current PDF page and exposes viewer actions."""

    MIN_ZOOM = 0.2
    MAX_ZOOM = 6.0

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._document: Optional[PdfDocument] = None
        self._page_index: int = 0
        self._zoom: float = 1.0
        self._fit_mode: Optional[str] = None  # "width", "page", or None
        self._pending_fit_update: bool = False
        self._panning_active: bool = False
        self._last_pan_pos = QtCore.QPoint()
        self._page_indicator_text: str = ""

        self._image_label = QtWidgets.QLabel(
            "Open a PDF or drag & drop to begin\n\n기본기능에서 PDF파일을 열기 하거나, 드래그&드롭으로 가져올 수 있습니다"
        )
        self._image_label.setAlignment(AlignCenter)
        self._image_label.setBackgroundRole(QtGui.QPalette.Base)
        self._image_label.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored
        )

        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(AlignCenter)
        self._scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll_area.setWidget(self._image_label)
        self._scroll_area.viewport().installEventFilter(self)
        self._scroll_area.verticalScrollBar().setStyleSheet(
            """
            QScrollBar:vertical {
                width: 10px;
                margin: 0;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(120, 120, 120, 160);
                min-height: 24px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
            """
        )

        self._page_indicator = QtWidgets.QLabel("")
        self._page_indicator.setAlignment(AlignCenter)
        self._page_indicator.setStyleSheet("color: #555; padding: 4px;")
        self._page_indicator.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self._page_indicator.setMinimumWidth(0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._scroll_area, 1)
        layout.addWidget(self._page_indicator, 0)

        self.prev_action = QAction("◀ 이전 PAGE", self)
        self.prev_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.MoveToPreviousPage))
        self.prev_action.triggered.connect(self.go_to_previous_page)

        self.next_action = QAction("▶ 다음 PAGE", self)
        self.next_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.MoveToNextPage))
        self.next_action.triggered.connect(self.go_to_next_page)

        self.zoom_in_action = QAction("Zoom in", self)
        self.zoom_in_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.ZoomIn))
        self.zoom_in_action.triggered.connect(self.zoom_in)

        self.zoom_out_action = QAction("Zoom out", self)
        self.zoom_out_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.ZoomOut))
        self.zoom_out_action.triggered.connect(self.zoom_out)

        self.fit_width_action = QAction("Fit width", self)
        self.fit_width_action.triggered.connect(self.fit_to_width)

        self.fit_page_action = QAction("Fit page", self)
        self.fit_page_action.triggered.connect(self.fit_to_page)

        self._update_action_states()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """Use wheel for page navigation, with Ctrl-modified wheel reserved for zooming."""
        if not self._document:
            super().wheelEvent(event)
            return

        modifiers = event.modifiers()
        angle_delta = event.angleDelta()
        delta = angle_delta.y() or angle_delta.x()
        ctrl_pressed = modifiers & QtCore.Qt.ControlModifier

        if ctrl_pressed:
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return

        if delta == 0:
            super().wheelEvent(event)
            return

        if delta > 0:
            self.go_to_previous_page()
        else:
            self.go_to_next_page()
        event.accept()

    # -- document lifecycle --------------------------------------------------

    def load_document(self, document: PdfDocument) -> None:
        self._document = document
        self._page_index = 0
        self._zoom = 1.0
        self._pending_fit_update = False
        self._panning_active = False
        self._scroll_area.viewport().unsetCursor()
        self._apply_fit_or_render()

    def clear(self) -> None:
        self._document = None
        if self._panning_active:
            self._panning_active = False
            self._scroll_area.viewport().unsetCursor()
        self._image_label.setPixmap(QtGui.QPixmap())
        self._image_label.setText("Open a PDF to begin")
        self._page_indicator.setText("")
        self._page_indicator_text = ""
        self._pending_fit_update = False
        self._update_action_states()

    # -- navigation ----------------------------------------------------------

    def go_to_previous_page(self) -> None:
        if self._document and self._page_index > 0:
            self._page_index -= 1
            self._apply_fit_or_render()

    def go_to_next_page(self) -> None:
        if self._document and self._page_index < self._document.page_count - 1:
            self._page_index += 1
            self._apply_fit_or_render()

    # -- zoom controls -------------------------------------------------------

    def zoom_in(self) -> None:
        self._fit_mode = None
        self._set_zoom(self._zoom * 1.2)

    def zoom_out(self) -> None:
        self._fit_mode = None
        self._set_zoom(self._zoom / 1.2)

    def fit_to_width(self) -> None:
        if not self._document:
            return
        self._fit_mode = "width"
        self._apply_fit()

    def fit_to_page(self) -> None:
        if not self._document:
            return
        self._fit_mode = "page"
        self._apply_fit()

    # -- helpers -------------------------------------------------------------

    def _apply_fit_or_render(self) -> None:
        if self._fit_mode:
            self._apply_fit()
        else:
            self._render_current_page()

    def _set_zoom(self, value: float, *, from_fit: bool = False) -> None:
        if not self._document:
            return
        clamped = max(self.MIN_ZOOM, min(self.MAX_ZOOM, value))
        if abs(clamped - self._zoom) > 0.0001:
            if not from_fit:
                self._fit_mode = None
            self._zoom = clamped
            self._render_current_page()
        elif from_fit:
            # Even if the zoom does not change numerically we refresh to keep the
            # status indicator synchronized with the current fit mode.
            self._render_current_page()

    def _render_current_page(self) -> None:
        if not self._document:
            self.clear()
            return

        try:
            image = self._document.render_page(self._page_index, self._zoom)
        except Exception as exc:  # pragma: no cover - defensive UI path.
            QtWidgets.QMessageBox.critical(self, "Rendering error", str(exc))
            return

        pixmap = QtGui.QPixmap.fromImage(image)
        self._image_label.setPixmap(pixmap)
        self._image_label.setMinimumSize(pixmap.size())
        self._page_indicator_text = (
            f"{self._document.display_name} — Page {self._page_index + 1} / {self._document.page_count}  "
            f"(zoom: {self._zoom * 100:.0f}%)"
        )
        self._update_page_indicator_label()
        self._update_action_states()

    def _update_action_states(self) -> None:
        has_doc = self._document is not None
        can_prev = has_doc and self._page_index > 0
        can_next = has_doc and self._document is not None and self._page_index < self._document.page_count - 1

        for action, enabled in (
            (self.prev_action, can_prev),
            (self.next_action, can_next),
            (self.zoom_in_action, has_doc),
            (self.zoom_out_action, has_doc),
            (self.fit_width_action, has_doc),
            (self.fit_page_action, has_doc),
        ):
            action.setEnabled(enabled)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self._scroll_area.viewport():
            if event.type() == QtCore.QEvent.Resize:
                self._schedule_fit_update()
            elif event.type() == QtCore.QEvent.MouseButtonPress:
                mouse_event = cast(QtGui.QMouseEvent, event)
                if (
                    mouse_event.button() == QtCore.Qt.MiddleButton
                    and self._document
                    and self._image_label.pixmap()
                ):
                    self._panning_active = True
                    self._last_pan_pos = mouse_event.pos()
                    self._scroll_area.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
                    mouse_event.accept()
                    return True
            elif event.type() == QtCore.QEvent.MouseMove and self._panning_active:
                mouse_event = cast(QtGui.QMouseEvent, event)
                delta = mouse_event.pos() - self._last_pan_pos
                self._last_pan_pos = mouse_event.pos()
                hbar = self._scroll_area.horizontalScrollBar()
                vbar = self._scroll_area.verticalScrollBar()
                hbar.setValue(hbar.value() - delta.x())
                vbar.setValue(vbar.value() - delta.y())
                mouse_event.accept()
                return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                mouse_event = cast(QtGui.QMouseEvent, event)
                if mouse_event.button() == QtCore.Qt.MiddleButton and self._panning_active:
                    self._panning_active = False
                    self._scroll_area.viewport().unsetCursor()
                    mouse_event.accept()
                    return True
            elif event.type() == QtCore.QEvent.Leave and self._panning_active:
                self._panning_active = False
                self._scroll_area.viewport().unsetCursor()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._schedule_fit_update()
        self._update_page_indicator_label()

    def _schedule_fit_update(self) -> None:
        if not self._fit_mode:
            return
        if self._pending_fit_update:
            return
        self._pending_fit_update = True

        def apply() -> None:
            self._pending_fit_update = False
            if self._fit_mode:
                self._apply_fit()

        QtCore.QTimer.singleShot(0, apply)

    def _apply_fit(self) -> None:
        if not self._document or not self._fit_mode:
            return

        rect = self._document.page_rect(self._page_index)
        if rect.width == 0 or rect.height == 0:
            return

        viewport = self._scroll_area.viewport().size()
        available_width = max(viewport.width() - 16, 1)
        available_height = max(viewport.height() - 16, 1)

        if self._fit_mode == "width":
            new_zoom = available_width / max(rect.width, 1)
        else:
            width_ratio = available_width / rect.width
            height_ratio = available_height / rect.height
            new_zoom = min(width_ratio, height_ratio)

        self._set_zoom(new_zoom, from_fit=True)

    def _update_page_indicator_label(self) -> None:
        if not self._page_indicator_text:
            self._page_indicator.setText("")
            return

        metrics = self._page_indicator.fontMetrics()
        available = max(self._page_indicator.width() - 16, 80)
        elided = metrics.elidedText(self._page_indicator_text, QtCore.Qt.ElideMiddle, available)
        self._page_indicator.setText(elided)


# ---- Main window --------------------------------------------------------------


class TabListWidget(QtWidgets.QListWidget):
    """List widget that honours a custom minimum width for splitter resizing."""

    def __init__(self, min_width: int, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._base_min_width = max(1, min_width)
        self.setUniformItemSizes(True)

    def set_base_min_width(self, width: int) -> None:
        self._base_min_width = max(1, width)
        self.updateGeometry()

    def minimumSizeHint(self) -> QtCore.QSize:
        hint = super().minimumSizeHint()
        hint.setWidth(self._base_min_width)
        return hint

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        hint.setWidth(max(self._base_min_width, hint.width()))
        return hint


class MainWindow(QtWidgets.QMainWindow):
    """Primary application window with a vertical tab rail."""

    def __init__(self) -> None:
        super().__init__()
        self._app_title = "PDF Vertical Tabs Viewer v1.0"
        self.setWindowTitle(self._app_title)
        self.resize(980, 760)
        self.setAcceptDrops(True)

        self._documents: Dict[Path, PdfDocument] = {}
        self._tab_items: Dict[Path, QtWidgets.QListWidgetItem] = {}
        self._doc_identities: Dict[Path, FileIdentity] = {}
        self._watched_files: Set[Path] = set()
        self._watched_dirs: Dict[Path, int] = {}
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._handle_watched_file_changed)
        self._watcher.directoryChanged.connect(self._handle_watched_directory_changed)
        self._compact_mode = False
        self._expanded_width = 220
        self._min_tab_width = 72
        self._max_tab_ratio = 0.8  # allow up to 80% of the window width
        self._tab_panel_visible = True
        self._stored_tab_width = self._expanded_width

        self.tab_list = TabListWidget(self._min_tab_width)
        self.tab_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tab_list.setSpacing(4)
        self.tab_list.setAlternatingRowColors(True)
        self.tab_list.setIconSize(QtCore.QSize(48, 60))
        self.tab_list.currentItemChanged.connect(self._handle_tab_selection)
        self.tab_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tab_list.customContextMenuRequested.connect(self._show_tab_context_menu)
        self.tab_list.setMinimumWidth(self._min_tab_width)
        self.tab_list.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)
        self.tab_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tab_list.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        self.tab_list.setTextElideMode(QtCore.Qt.ElideRight)
        self.tab_list.setLayoutDirection(LayoutLeftToRight)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setAlignment(AlignLeft | AlignVCenter)
        self.status_label.setWordWrap(False)
        self.status_label.setMargin(6)
        self.status_label.setStyleSheet("color: #666; border-top: 1px solid #d0d0d0;")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)

        self.tab_panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(self.tab_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self.tab_list, 1)
        panel_layout.addWidget(self.status_label, 0)
        self.tab_panel.setMinimumWidth(self._min_tab_width)
        self.tab_panel.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)

        self.viewer = PdfViewerWidget()

        self._splitter = QtWidgets.QSplitter()
        self._splitter.addWidget(self.viewer)
        self._splitter.addWidget(self.tab_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)
        self._splitter.setHandleWidth(6)
        self._splitter.setStyleSheet(
            """
            QSplitter::handle {
                background: #d5d5d5;
            }
            QSplitter::handle:horizontal {
                margin: 0;
            }
            """
        )
        self._splitter.setSizes([720, self._expanded_width])
        self._splitter.splitterMoved.connect(self._enforce_tab_limit)
        self.setCentralWidget(self._splitter)

        self._build_menus()
        self._update_status_label()

    # -- menu setup -----------------------------------------------------------

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        base_menu = menubar.addMenu("기본기능")

        self.open_action = QAction("PDF 열기…", self)
        self.open_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        self.open_action.triggered.connect(self._open_documents_dialog)
        base_menu.addAction(self.open_action)

        self.close_action = QAction("현재 문서 닫기", self)
        self.close_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Close))
        self.close_action.triggered.connect(self.close_current_document)
        self.close_action.setEnabled(False)
        base_menu.addAction(self.close_action)

        base_menu.addSeparator()
        self.compact_action = QAction("세로 탭 컴팩트 모드", self)
        self.compact_action.setCheckable(True)
        self.compact_action.triggered.connect(self.toggle_compact_tabs)
        base_menu.addAction(self.compact_action)

        self.toggle_tabs_action = QAction("세로 탭 표시", self)
        self.toggle_tabs_action.setCheckable(True)
        self.toggle_tabs_action.setChecked(True)
        self.toggle_tabs_action.triggered.connect(self.set_tab_panel_visible)
        base_menu.addAction(self.toggle_tabs_action)

        base_menu.addSeparator()
        base_menu.addAction(self.viewer.prev_action)
        base_menu.addAction(self.viewer.next_action)
        base_menu.addSeparator()
        base_menu.addAction(self.viewer.zoom_out_action)
        base_menu.addAction(self.viewer.zoom_in_action)
        base_menu.addAction(self.viewer.fit_width_action)
        base_menu.addAction(self.viewer.fit_page_action)

        modify_menu = menubar.addMenu("변경기능")
        placeholder = QAction("추가 기능 준비 중", self)
        placeholder.setEnabled(False)
        modify_menu.addAction(placeholder)

        info_menu = menubar.addMenu("정보")

        license_action = QAction("라이선스 정보", self)
        license_action.triggered.connect(self._show_license_info)
        info_menu.addAction(license_action)

        author_action = QAction("제작자", self)
        author_action.triggered.connect(self._show_author_info)
        info_menu.addAction(author_action)

    # -- document management -------------------------------------------------

    def _open_documents_dialog(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Open PDF files",
            str(Path.home()),
            "PDF documents (*.pdf);;All files (*)",
        )
        for path_str in paths:
            self.open_document_from_path(Path(path_str))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if self._extract_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        paths = self._extract_pdf_paths(event.mimeData())
        if not paths:
            event.ignore()
            return

        for path in paths:
            self.open_document_from_path(path)
        event.acceptProposedAction()

    def _extract_pdf_paths(self, mime: QtCore.QMimeData) -> list[Path]:
        if not mime.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".pdf":
                paths.append(path)
        return paths

    def _create_pdf_document(self, path: Path) -> Optional[PdfDocument]:
        try:
            doc = fitz.open(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", f"{path.name}\n\n{exc}")
            return None

        try:
            if getattr(doc, "needs_pass", False):
                unlocked = False
                try:
                    unlocked = bool(doc.authenticate(""))
                except Exception:
                    unlocked = False
                if not unlocked:
                    auth_result = self._prompt_for_pdf_password(doc, path)
                    if auth_result is None:
                        doc.close()
                        return None
                    if not auth_result or getattr(doc, "needs_pass", False):
                        doc.close()
                        QtWidgets.QMessageBox.critical(
                            self,
                            "Open failed",
                            f"{path.name}\n\n암호 확인에 실패했습니다.",
                        )
                        return None
                if getattr(doc, "needs_pass", False):
                    doc.close()
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Open failed",
                        f"{path.name}\n\n암호 확인에 실패했습니다.",
                    )
                    return None

            metadata = doc.metadata or {}
            title = (metadata.get("title") or "").strip()
            display = title or path.name
            thumbnail = PdfDocument._build_thumbnail_icon(doc, display)
            return PdfDocument(path=path, document=doc, display_name=display, thumbnail=thumbnail)
        except Exception as exc:
            doc.close()
            QtWidgets.QMessageBox.critical(self, "Open failed", f"{path.name}\n\n{exc}")
            return None

    def _prompt_for_pdf_password(self, doc: fitz.Document, path: Path) -> Optional[bool]:
        prompt = (
            f"{path.name} 문서는 암호로 보호되어 있습니다.\n"
            "열람을 위해 암호를 입력해 주세요."
        )
        for _attempt in range(3):
            password, ok = QtWidgets.QInputDialog.getText(
                self,
                "암호 필요",
                prompt,
                QtWidgets.QLineEdit.Password,
            )
            if not ok:
                return None
            try:
                if doc.authenticate(password) and not getattr(doc, "needs_pass", False):
                    return True
            except Exception:
                pass
            QtWidgets.QMessageBox.warning(self, "암호 오류", "암호가 올바르지 않습니다.")
        return False

    def open_document_from_path(
        self,
        path: Path,
        insert_row: Optional[int] = None,
        make_current: bool = True,
    ) -> None:
        normalized = normalize_path(path)
        if normalized in self._documents:
            if make_current:
                self._select_tab(normalized)
            return

        document = self._create_pdf_document(normalized)
        if document is None:
            return

        item = QtWidgets.QListWidgetItem(document.display_name)
        item.setData(PATH_ROLE, str(normalized))
        item.setData(TITLE_ROLE, document.display_name)
        item.setData(UserRole, document)
        item.setFlags(ItemIsEnabled | ItemIsSelectable)
        item.setIcon(document.thumbnail)
        item.setToolTip(str(normalized))

        if insert_row is None or insert_row >= self.tab_list.count():
            self.tab_list.addItem(item)
        else:
            self.tab_list.insertItem(insert_row, item)

        self._documents[normalized] = document
        self._tab_items[normalized] = item
        self._add_watch(normalized)
        self._update_document_identity(normalized)

        if make_current:
            self._select_tab(normalized)

        self.close_action.setEnabled(True)
        self._refresh_tab_labels()
        self._update_status_label()

    def close_current_document(self) -> None:
        current_item = self.tab_list.currentItem()
        if not current_item:
            return

        path_str = current_item.data(PATH_ROLE)
        if not path_str:
            return

        normalized = normalize_path(Path(path_str))
        row = self.tab_list.row(current_item)
        self.tab_list.takeItem(row)
        self._cleanup_document_path(normalized)

        if self.tab_list.count() == 0:
            self.viewer.clear()
            self.close_action.setEnabled(False)
            self._update_status_label()
            return

        new_row = min(row, self.tab_list.count() - 1)
        self.tab_list.setCurrentRow(new_row)
        self._refresh_tab_labels()
        self.close_action.setEnabled(self.tab_list.count() > 0)
        self._update_status_label()

    def _select_tab(self, path: Path) -> None:
        item = self._tab_items.get(path)
        if item:
            row = self.tab_list.row(item)
            self.tab_list.setCurrentRow(row)

    def _handle_tab_selection(
        self,
        current: Optional[QtWidgets.QListWidgetItem],
        previous: Optional[QtWidgets.QListWidgetItem],
    ) -> None:
        if not current:
            self.viewer.clear()
            self._update_status_label()
            return

        document = current.data(UserRole)
        if isinstance(document, PdfDocument):
            self.viewer.load_document(document)
        self.setWindowTitle(self._app_title)
        self._update_status_label()

    def toggle_compact_tabs(self) -> None:
        self._compact_mode = self.compact_action.isChecked()
        if self._compact_mode:
            width = 72
            self.tab_list.setMinimumWidth(width)
            self.tab_list.setMaximumWidth(width)
            self.tab_list.setViewMode(QtWidgets.QListView.IconMode)
            self.tab_list.setResizeMode(QtWidgets.QListView.Adjust)
            self.tab_list.setMovement(QtWidgets.QListView.Static)
            self.tab_list.setLayoutDirection(LayoutRightToLeft)
            self.tab_list.set_base_min_width(width)
            self.tab_panel.setMinimumWidth(width)
            self.tab_panel.setMaximumWidth(width)
            self._apply_tab_width(width)
        else:
            self.tab_list.setMinimumWidth(self._min_tab_width)
            self.tab_list.setViewMode(QtWidgets.QListView.ListMode)
            self.tab_list.setMovement(QtWidgets.QListView.Free)
            self.tab_list.setResizeMode(QtWidgets.QListView.Fixed)
            self.tab_list.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)
            self.tab_list.setLayoutDirection(LayoutLeftToRight)
            self.tab_list.set_base_min_width(self._min_tab_width)
            self.tab_panel.setMinimumWidth(self._min_tab_width)
            self.tab_panel.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)
            self._apply_tab_width(self._expanded_width)

        self._refresh_tab_labels()
        self._enforce_tab_limit()
        self._update_status_label()

    def set_tab_panel_visible(self, visible: bool) -> None:
        if self._tab_panel_visible == visible:
            return

        self._tab_panel_visible = visible
        if visible:
            self.tab_panel.show()
            desired = self._stored_tab_width
            if self._compact_mode:
                desired = max(desired, self.tab_list.minimumWidth())
            else:
                desired = max(desired, self._min_tab_width)
            self._apply_tab_width(desired)
            self._enforce_tab_limit()
        else:
            sizes = self._splitter.sizes()
            if len(sizes) >= 2:
                self._stored_tab_width = max(sizes[1], self._min_tab_width)
            self.tab_panel.hide()
            sizes = self._splitter.sizes()
            total = sum(sizes) if len(sizes) >= 2 else self.width()
            self._splitter.blockSignals(True)
            self._splitter.setSizes([total, 0])
            self._splitter.blockSignals(False)

        self.toggle_tabs_action.blockSignals(True)
        self.toggle_tabs_action.setChecked(self._tab_panel_visible)
        self.toggle_tabs_action.blockSignals(False)

    def _refresh_tab_labels(self) -> None:
        for index in range(self.tab_list.count()):
            item = self.tab_list.item(index)
            display = item.data(TITLE_ROLE) or ""
            if self._compact_mode:
                item.setText("")
                item.setToolTip(display)
            else:
                item.setText(display)

    def _update_status_label(self) -> None:
        count = len(self._documents)
        current_name = ""
        current_item = self.tab_list.currentItem()
        if current_item:
            document = current_item.data(UserRole)
            if isinstance(document, PdfDocument):
                current_name = document.display_name

        display_text = f"열린 문서: {count}개"
        self.status_label.setText(display_text)

        if current_name:
            tooltip = f"{display_text} | 현재: {current_name}"
            self.status_label.setToolTip(tooltip)
        else:
            self.status_label.setToolTip(display_text if self._compact_mode else "")
    def _add_watch(self, path: Path) -> None:
        normalized = normalize_path(path)
        if normalized not in self._watched_files:
            try:
                self._watcher.addPath(str(normalized))
            except Exception:
                pass
            self._watched_files.add(normalized)

        directory = normalize_path(normalized.parent)
        count = self._watched_dirs.get(directory, 0)
        if count == 0:
            try:
                self._watcher.addPath(str(directory))
            except Exception:
                pass
        self._watched_dirs[directory] = count + 1

    def _remove_watch(self, path: Path) -> None:
        normalized = normalize_path(path)
        if normalized in self._watched_files:
            try:
                self._watcher.removePath(str(normalized))
            except Exception:
                pass
            self._watched_files.discard(normalized)

        directory = normalize_path(normalized.parent)
        count = self._watched_dirs.get(directory, 0) - 1
        if count <= 0:
            if directory in self._watched_dirs:
                self._watched_dirs.pop(directory, None)
            try:
                self._watcher.removePath(str(directory))
            except Exception:
                pass
        else:
            self._watched_dirs[directory] = count

    def _update_document_identity(self, path: Path) -> None:
        normalized = normalize_path(path)
        identity = FileIdentity.from_path(normalized)
        if identity:
            self._doc_identities[normalized] = identity

    def _handle_watched_file_changed(self, path_str: str) -> None:
        path = normalize_path(Path(path_str))
        if path not in self._documents:
            return
        if path.exists():
            self._update_document_identity(path)
            return

        QtCore.QTimer.singleShot(250, lambda p=path: self._attempt_recover_renamed_file(p))

    def _handle_watched_directory_changed(self, directory_str: str) -> None:
        directory = normalize_path(Path(directory_str))
        missing = [
            doc_path
            for doc_path in list(self._documents.keys())
            if doc_path.parent == directory and not doc_path.exists()
        ]
        if not missing:
            return

        def trigger_recovery(paths: list[Path]) -> None:
            for doc_path in paths:
                self._attempt_recover_renamed_file(doc_path)

        QtCore.QTimer.singleShot(250, lambda paths=missing: trigger_recovery(paths))

    def _attempt_recover_renamed_file(self, old_path: Path) -> None:
        normalized = normalize_path(old_path)
        if normalized not in self._documents:
            return

        identity = self._doc_identities.get(normalized)
        if not identity:
            return

        directory = normalize_path(normalized.parent)
        candidate = self._find_identity_match(directory, identity, normalized)
        if not candidate:
            return

        candidate_normalized = normalize_path(candidate)
        if candidate_normalized == normalized:
            return

        self._handle_document_renamed(normalized, candidate_normalized)

    def _find_identity_match(
        self,
        directory: Path,
        identity: FileIdentity,
        exclude: Path,
    ) -> Optional[Path]:
        if not directory.exists():
            return None

        try:
            entries = list(directory.iterdir())
        except OSError:
            return None

        fallback: Optional[Path] = None
        for entry in entries:
            if entry == exclude or entry.suffix.lower() != ".pdf":
                continue

            candidate_identity = FileIdentity.from_path(entry)
            if not candidate_identity:
                continue

            if identity.matches(candidate_identity):
                return entry

            if (
                fallback is None
                and identity.size == candidate_identity.size
                and abs(identity.mtime_ns - candidate_identity.mtime_ns) <= 1_000_000
            ):
                fallback = entry

        return fallback

    def _handle_document_renamed(self, old_path: Path, new_path: Path) -> None:
        item = self._tab_items.get(old_path)
        document = self._documents.get(old_path)
        if not item or not document:
            return

        row = self.tab_list.row(item)
        normalized_new = normalize_path(new_path)
        if normalized_new in self._documents:
            was_current = self.tab_list.currentItem() is item
            self.tab_list.takeItem(row)
            self._cleanup_document_path(old_path)
            if was_current and self.tab_list.count():
                self.tab_list.setCurrentRow(min(row, self.tab_list.count() - 1))
            self.close_action.setEnabled(self.tab_list.count() > 0)
            self._update_status_label()
            return

        was_current = self.tab_list.currentItem() is item
        self.tab_list.takeItem(row)
        self._cleanup_document_path(old_path)
        self.open_document_from_path(normalized_new, insert_row=row, make_current=was_current)
        if was_current:
            self.tab_list.setCurrentRow(row)
        self.close_action.setEnabled(self.tab_list.count() > 0)
        self._update_status_label()

    def _cleanup_document_path(self, path: Path, close_document: bool = True) -> None:
        normalized = normalize_path(path)
        document = self._documents.pop(normalized, None)
        if close_document and document:
            try:
                document.close()
            except Exception:
                pass
        self._tab_items.pop(normalized, None)
        self._doc_identities.pop(normalized, None)
        self._remove_watch(normalized)

    def _show_tab_context_menu(self, point: QtCore.QPoint) -> None:
        item = self.tab_list.itemAt(point)
        if not item:
            return

        document = item.data(UserRole)
        if not isinstance(document, PdfDocument):
            return

        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction("문서로 이동")
        save_as_action = menu.addAction("다음 이름으로 저장…")
        folder_action = menu.addAction("파일 위치 열기")
        menu.addSeparator()
        close_action = menu.addAction("닫기")

        selected = menu.exec(self.tab_list.mapToGlobal(point))
        if selected is None:
            return

        if selected == open_action:
            self.tab_list.setCurrentItem(item)
        elif selected == save_as_action:
            self.tab_list.setCurrentItem(item)
            self._save_document_as(document)
        elif selected == folder_action:
            self._open_document_directory(document)
        elif selected == close_action:
            self.tab_list.setCurrentItem(item)
            self.close_current_document()

    def _enforce_tab_limit(self, *_args) -> None:
        """Keep the tab rail from occupying more than half of the window."""
        if not self._tab_panel_visible:
            return
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        max_allowed = max(int(total * self._max_tab_ratio), self._min_tab_width)
        tab_width = min(sizes[1], max_allowed)
        viewer_width = max(total - tab_width, 1)
        if tab_width != sizes[1]:
            self._splitter.blockSignals(True)
            self._splitter.setSizes([viewer_width, tab_width])
            self._splitter.blockSignals(False)
            sizes = self._splitter.sizes()
        if len(sizes) >= 2:
            self._stored_tab_width = sizes[1]

    def _apply_tab_width(self, target_width: int) -> None:
        """Force the splitter so the tab rail occupies exactly the desired width."""
        if not self._tab_panel_visible:
            self._stored_tab_width = max(target_width, getattr(self, "_min_tab_width", 1))
            return
        minimum = getattr(self, "_min_tab_width", 1)
        target = max(target_width, minimum)
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        total = sum(sizes)
        max_allowed = max(int(total * getattr(self, "_max_tab_ratio", 1.0)), minimum)
        target = min(target, max_allowed)
        if total <= target:
            total = target + max(self.viewer.width(), 1)
        viewer_width = max(total - target, 1)
        self._splitter.blockSignals(True)
        self._splitter.setSizes([viewer_width, target])
        self._splitter.blockSignals(False)
        sizes = self._splitter.sizes()
        if len(sizes) >= 2:
            self._stored_tab_width = sizes[1]

    def _show_license_info(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "라이선스 정보",
            (
                "이 프로그램은 PyMuPDF와 PyQt5 (GPLv3)에 기반하여 제작되었습니다.\n"
                "PyQt5 GPL 라이선스 조건을 준수해야 합니다."
            ),
        )

    def _show_author_info(self) -> None:
        QtWidgets.QMessageBox.information(self, "제작자", "Made by Chris\n2025.10.28")

    def _save_document_as(self, document: PdfDocument) -> None:
        suggested_path = document.path if document.path.suffix else document.path.with_suffix(".pdf")
        new_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "다음 이름으로 저장",
            str(suggested_path),
            "PDF documents (*.pdf);;All files (*)",
        )
        if not new_path:
            return

        try:
            document.save_as(Path(new_path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "저장 실패", str(exc))
            return

        old_path = normalize_path(document.path)
        new_path_normalized = normalize_path(Path(new_path))
        if new_path_normalized != old_path:
            self._handle_document_renamed(old_path, new_path_normalized)
        else:
            self._update_document_identity(old_path)

        QtWidgets.QMessageBox.information(self, "저장 완료", f"{new_path}\n\n저장이 완료되었습니다.")

    def _open_document_directory(self, document: PdfDocument) -> None:
        directory = document.path.parent
        if sys.platform.startswith("win"):
            os.startfile(directory)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            QtCore.QProcess.startDetached("open", [str(directory)])
        else:
            QtCore.QProcess.startDetached("xdg-open", [str(directory)])


# ---- Entrypoint ---------------------------------------------------------------


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()

    for argument in sys.argv[1:]:
        path = Path(argument).expanduser()
        if path.exists():
            window.open_document_from_path(path)

    return exec_qapplication(app)


if __name__ == "__main__":
    raise SystemExit(main())
