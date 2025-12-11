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

import json
import os
import sys
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple, cast

try:
    from PyQt5 import QtCore, QtGui, QtWidgets, QtPrintSupport, QtNetwork  # type: ignore
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

TAB_SORT_NONE = "none"
TAB_SORT_NAME_ASC = "name_asc"
TAB_SORT_NAME_DESC = "name_desc"
TAB_SORT_RECENT = "recent"
TAB_SORT_MODES = {
    TAB_SORT_NONE,
    TAB_SORT_NAME_ASC,
    TAB_SORT_NAME_DESC,
    TAB_SORT_RECENT,
}
DEFAULT_TAB_SORT_MODE = TAB_SORT_RECENT

TAB_SORT_NONE = "none"
TAB_SORT_NAME_ASC = "name_asc"
TAB_SORT_NAME_DESC = "name_desc"
TAB_SORT_RECENT = "recent"
TAB_SORT_MODES = {
    TAB_SORT_NONE,
    TAB_SORT_NAME_ASC,
    TAB_SORT_NAME_DESC,
    TAB_SORT_RECENT,
}
DEFAULT_TAB_SORT_MODE = TAB_SORT_RECENT


def exec_qapplication(app: QtWidgets.QApplication) -> int:
    """Compatibility wrapper for QApplication.exec()."""
    return app.exec_()


# ---- Utility functions --------------------------------------------------------

def normalize_path(path: Path) -> Path:
    """Return an absolute path even if the file does not currently exist."""
    candidate = Path(path).expanduser()

    # UNC paths often trigger authentication when resolved; leave them as-is.
    candidate_text = str(candidate)
    if candidate_text.startswith(("\\\\", "//")):
        decoded_text = _decode_unc_hostname(candidate_text)
        if decoded_text != candidate_text:
            return Path(decoded_text)
        return candidate

    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError:
        pass
    except OSError:
        pass

    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        pass

    if candidate.is_absolute():
        return candidate

    try:
        return Path.cwd() / candidate
    except OSError:
        return candidate


def _decode_unc_hostname(text: str) -> str:
    """Decode IDNA (punycode) hostnames in UNC paths for readability."""
    if not text.startswith(("\\\\", "//")):
        return text

    prefix = text[:2]
    remainder = text[2:]
    separator = "\\" if prefix == "\\\\" else "/"
    if not remainder:
        return text

    parts = remainder.split(separator, 1)
    host = parts[0]
    if not host or not host.startswith(("xn--", "XN--")):
        return text

    try:
        decoded = host.encode("ascii").decode("idna")
    except (UnicodeEncodeError, UnicodeError):
        return text

    if decoded == host:
        return text

    tail = parts[1] if len(parts) > 1 else ""
    if tail:
        return f"{prefix}{decoded}{separator}{tail}"
    return f"{prefix}{decoded}"


def _prepare_filesystem_path(path: Path) -> str:
    """Return a string path safe for Windows long/UNC paths."""
    text = os.fspath(path)
    if os.name != "nt":
        return text

    if text.startswith("\\\\?\\") or text.startswith("//?/"):
        return text

    is_unc = text.startswith("\\\\") or text.startswith("//")
    needs_long_path = len(text) >= 260
    if is_unc:
        remainder = text.lstrip("\\/")  # strip leading slashes for UNC body
        if not remainder:
            return text
        return f"\\\\?\\UNC\\{remainder}"
    if needs_long_path:
        return f"\\\\?\\{text}"
    return text


def _display_path_text(path: Path | str) -> str:
    """Return a user-facing path string with decoded UNC hostnames."""
    text = os.fspath(path)
    if os.name != "nt":
        return text

    if text.startswith("\\\\?\\UNC\\"):
        text = f"\\\\{text[8:]}"
    elif text.startswith("\\\\?\\"):
        text = text[4:]

    return _decode_unc_hostname(text)



def read_bool_setting(store: QtCore.QSettings, key: str, default: bool) -> bool:
    value = store.value(key, default)
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)


def resolve_resource_path(name: str) -> Path:
    """Return an absolute path to a bundled auxiliary resource."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / name
        return Path(sys.executable).resolve().parent / name
    return Path(__file__).resolve().parent / name


_APP_ICON: Optional[QtGui.QIcon] = None


def get_application_icon() -> QtGui.QIcon:
    """Return the shared application icon, falling back to an empty icon."""
    global _APP_ICON
    if _APP_ICON is None:
        try:
            icon_path = resolve_resource_path("icon.ico")
            _APP_ICON = QtGui.QIcon(str(icon_path))
        except Exception:
            _APP_ICON = QtGui.QIcon()
    return _APP_ICON


user_token = (os.environ.get('USERNAME') or os.environ.get('USER') or 'default')
user_token = user_token.replace('\\', '_').replace('/', '_').replace(' ', '_')
SINGLE_INSTANCE_SERVER = f'PdfVertViewSingleton_{user_token}'


class SingleInstanceHost(QtCore.QObject):
    """Accept open requests from secondary processes."""

    open_requested = QtCore.pyqtSignal(list)

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._server = QtNetwork.QLocalServer(self)
        QtNetwork.QLocalServer.removeServer(SINGLE_INSTANCE_SERVER)
        if not self._server.listen(SINGLE_INSTANCE_SERVER):
            self._server = None
            return
        self._server.newConnection.connect(self._process_new_connection)

    def _process_new_connection(self) -> None:
        if not self._server:
            return
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                continue
            self._read_socket(connection)
            connection.disconnectFromServer()
            connection.close()
            self._read_socket(connection)
            connection.disconnectFromServer()
            connection.close()

    def _read_socket(self, socket: QtNetwork.QLocalSocket) -> None:
        if not socket.bytesAvailable():
            if not socket.waitForReadyRead(200):
                return
        if not socket.bytesAvailable():
            if not socket.waitForReadyRead(200):
                return
        data = bytes(socket.readAll())
        if not data:
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            payload = []
        paths: list[str] = []
        if isinstance(payload, list):
            paths = [str(item) for item in payload if isinstance(item, str)]
        if paths:
            self.open_requested.emit(paths)
        socket.flush()
        socket.flush()


def forward_paths_to_primary(paths: Iterable[Path]) -> bool:
    serialized = [str(Path(p)) for p in paths]
    if not serialized:
        return False
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_SERVER, QtCore.QIODevice.WriteOnly)
    if not socket.waitForConnected(500):
        return False
    try:
        payload = json.dumps(serialized).encode("utf-8")
        if socket.write(payload) == -1:
            return False
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        socket.waitForDisconnected(500)
        socket.close()
    except Exception:
        return False
    return True


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
        doc = fitz.open(_prepare_filesystem_path(file_path))  # Raises if the file cannot be opened.
        metadata = doc.metadata or {}
        title_text = (metadata.get("title") or "").strip()
        display = file_path.name
        icon_label = title_text or file_path.stem or display
        thumb = cls._build_thumbnail_icon(doc, icon_label)
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

    def get_page_text(self, index: int) -> str:
        """Extract all text from a page."""
        if index < 0 or index >= self.page_count:
            return ""
        page = self.document.load_page(index)
        return page.get_text()

    def search_text(self, query: str, page_index: int = -1) -> list:
        """
        Search for text in the document.
        
        Args:
            query: Text to search for
            page_index: Page to search in (-1 means all pages)
            
        Returns:
            List of (page_index, rect_list) tuples with matches
        """
        if not query.strip():
            return []
        
        results = []
        start_page = page_index if page_index >= 0 else 0
        end_page = page_index + 1 if page_index >= 0 else self.page_count
        
        for idx in range(start_page, end_page):
            try:
                page = self.document.load_page(idx)
                # search_for returns a list of rectangles for each match
                rects = page.search_for(query)
                if rects:
                    results.append((idx, rects))
            except Exception:
                pass
        
        return results

    def get_text_blocks(self, index: int) -> list:
        """Get text blocks with their positions on a page."""
        if index < 0 or index >= self.page_count:
            return []
        page = self.document.load_page(index)
        # Get text with layout info - returns list of blocks
        try:
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])
            return blocks
        except Exception:
            return []

    def get_text_in_rect(self, page_index: int, rect: tuple) -> str:
        """Extract text within a specified rectangle on a page."""
        if page_index < 0 or page_index >= self.page_count:
            return ""
        try:
            page = self.document.load_page(page_index)
            # rect should be (x0, y0, x1, y1)
            fitz_rect = fitz.Rect(rect)
            text = page.get_text("text", clip=fitz_rect)
            return text.strip()
        except Exception:
            return ""

    def close(self) -> None:
        self.document.close()

    def save_as(self, destination: Path) -> None:
        """Save the current document to a new path."""
        dest = Path(destination).expanduser()
        if not dest.is_absolute():
            dest = Path.cwd() / dest

        dest = normalize_path(dest)

        parent = dest.parent
        if parent and not str(dest).startswith("\\\\"):
            parent.mkdir(parents=True, exist_ok=True)

        self.document.save(_prepare_filesystem_path(dest))

    def rotate_document(self, degrees: int) -> None:
        """Rotate every page in the document by the supplied degrees."""
        step = degrees % 360
        if step == 0:
            return
        if step % 90 != 0:
            raise ValueError("Rotation must be a multiple of 90 degrees.")

        for index in range(self.page_count):
            page = self.document.load_page(index)
            self._set_page_rotation(page, (page.rotation + step) % 360)

    def rotate_page(self, index: int, degrees: int) -> None:
        """Rotate a single page by the supplied degrees."""
        if index < 0 or index >= self.page_count:
            raise IndexError("Page index out of range.")
        step = degrees % 360
        if step == 0:
            return
        if step % 90 != 0:
            raise ValueError("Rotation must be a multiple of 90 degrees.")

        page = self.document.load_page(index)
        self._set_page_rotation(page, (page.rotation + step) % 360)

    @staticmethod
    def _set_page_rotation(page: fitz.Page, rotation: int) -> None:
        rotation = rotation % 360
        try:
            page.set_rotation(rotation)
        except AttributeError:  # pragma: no cover - compatibility with older PyMuPDF.
            page.setRotation(rotation)


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
        self._default_fit_mode: str = "page"
        self._fit_mode: Optional[str] = None  # "width", "page", or None
        self._pending_fit_update: bool = False
        self._panning_active: bool = False
        self._last_pan_pos = QtCore.QPoint()
        self._page_indicator_text: str = ""
        
        # Text search and selection support
        self._search_results: list = []
        self._current_search_index: int = 0
        self._search_query: str = ""
        self._text_selection_enabled: bool = True
        self._selection_start = QtCore.QPoint()
        self._selection_end = QtCore.QPoint()
        self._rubber_band: Optional[QtWidgets.QRubberBand] = None
        self._selected_text_cache: str = ""
        self._selection_boxes: list[QtCore.QRectF] = []  # highlight rectangles in image coords

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
        self._scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll_area.viewport().setAutoFillBackground(True)
        self._scroll_area.viewport().setStyleSheet("background-color: #f3f3f3;")
        self._scroll_area.viewport().installEventFilter(self)

        scrollbar_style = """
            QScrollBar:vertical {
                width: 12px;
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

        self._page_scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Vertical)
        self._page_scrollbar.setRange(0, 0)
        self._page_scrollbar.setPageStep(1)
        self._page_scrollbar.setSingleStep(1)
        self._page_scrollbar.setVisible(False)
        self._page_scrollbar.setEnabled(False)
        self._page_scrollbar.setStyleSheet(scrollbar_style)
        self._page_scrollbar.setFixedWidth(14)
        self._page_scrollbar.valueChanged.connect(self._on_page_scrollbar_changed)

        self._page_indicator = QtWidgets.QLabel("")
        self._page_indicator.setAlignment(AlignCenter)
        self._page_indicator.setStyleSheet("color: #555; padding: 4px;")
        self._page_indicator.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self._page_indicator.setMinimumWidth(0)

        # Create search toolbar
        self._search_toolbar = QtWidgets.QWidget()
        search_layout = QtWidgets.QHBoxLayout(self._search_toolbar)
        search_layout.setContentsMargins(8, 4, 8, 4)
        search_layout.setSpacing(6)
        
        search_label = QtWidgets.QLabel("검색:")
        search_layout.addWidget(search_label)
        
        self._search_input = QtWidgets.QLineEdit()
        self._search_input.setPlaceholderText("검색어를 입력하세요...")
        self._search_input.setMaximumWidth(300)
        self._search_input.textChanged.connect(self._on_search_input_text_changed)
        self._search_input.returnPressed.connect(self._on_search_return_pressed)
        search_layout.addWidget(self._search_input)
        
        self._search_prev_btn = QtWidgets.QPushButton("◀ 이전")
        self._search_prev_btn.setMaximumWidth(80)
        self._search_prev_btn.clicked.connect(self._on_search_prev_clicked)
        search_layout.addWidget(self._search_prev_btn)
        
        self._search_next_btn = QtWidgets.QPushButton("다음 ▶")
        self._search_next_btn.setMaximumWidth(80)
        self._search_next_btn.clicked.connect(self._on_search_next_clicked)
        search_layout.addWidget(self._search_next_btn)
        
        self._search_status = QtWidgets.QLabel("")
        self._search_status.setStyleSheet("color: #666; font-size: 11px;")
        search_layout.addWidget(self._search_status, 1)
        
        self._search_toolbar.setStyleSheet(
            "QWidget { background-color: #f9f9f9; border-top: 1px solid #ddd; }"
        )

        self._search_toolbar.hide()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        viewer_row = QtWidgets.QHBoxLayout()
        viewer_row.setContentsMargins(0, 0, 0, 0)
        viewer_row.setSpacing(0)
        viewer_row.addWidget(self._scroll_area, 1)
        viewer_row.addWidget(self._page_scrollbar, 0)

        layout.addLayout(viewer_row, 1)
        layout.addWidget(self._search_toolbar, 0)
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

        self.find_action = QAction("검색 (Ctrl+F)", self)
        self.find_action.setShortcut(QtGui.QKeySequence.StandardKey.Find)
        self.find_action.triggered.connect(self.open_search_dialog)

        self.find_next_action = QAction("다음 검색 결과 (F3)", self)
        self.find_next_action.setShortcut(QtCore.Qt.Key_F3)
        self.find_next_action.triggered.connect(self.find_next)

        self.find_prev_action = QAction("이전 검색 결과 (Shift+F3)", self)
        self.find_prev_action.setShortcut(QtGui.QKeySequence("Shift+F3"))
        self.find_prev_action.triggered.connect(self.find_prev)

        self._search_prev_btn.setEnabled(False)
        self._search_next_btn.setEnabled(False)

        self._search_escape_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        self._search_escape_shortcut.activated.connect(self._hide_search_toolbar)

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
        self._apply_default_fit_mode()
        self._sync_page_scrollbar()

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
        # Clear search/selection state
        self._search_results = []
        self._current_search_index = 0
        self._search_query = ""
        self._selected_text_cache = ""
        self._selection_boxes = []
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._search_status.setText("")
        self._search_prev_btn.setEnabled(False)
        self._search_next_btn.setEnabled(False)
        if self._rubber_band:
            self._rubber_band.hide()
        self._update_action_states()
        self._sync_page_scrollbar()

    # -- navigation ----------------------------------------------------------

    def go_to_previous_page(self) -> None:
        if self._document and self._page_index > 0:
            self._page_index -= 1
            self._apply_fit_or_render()

    def go_to_next_page(self) -> None:
        if self._document and self._page_index < self._document.page_count - 1:
            self._page_index += 1
            self._apply_fit_or_render()

    def go_to_page(self, index: int) -> None:
        if not self._document:
            return
        clamped = max(0, min(index, self._document.page_count - 1))
        if clamped == self._page_index:
            return
        self._page_index = clamped
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

    def set_default_fit_mode(self, mode: str) -> None:
        normalized = "width" if mode == "width" else "page"
        if self._default_fit_mode == normalized and self._fit_mode == normalized:
            return
        self._default_fit_mode = normalized
        if self._document:
            self._fit_mode = normalized
            self._apply_fit()

    def _apply_default_fit_mode(self) -> None:
        if not self._document:
            self._fit_mode = None
            return
        self._fit_mode = self._default_fit_mode
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

        # Rebuild search highlights for current page if we have active search
        current_page_search_boxes: list[QtCore.QRectF] = []
        if self._search_results and self._search_query:
            for page_index, rects in self._search_results:
                if page_index == self._page_index:
                    current_page_search_boxes.extend([
                        QtCore.QRectF(r.x0 * self._zoom, r.y0 * self._zoom, (r.x1 - r.x0) * self._zoom, (r.y1 - r.y0) * self._zoom)
                        for r in rects
                    ])

        # Draw search result overlays if present
        if current_page_search_boxes:
            pm_copy = QtGui.QPixmap(pixmap)
            painter = QtGui.QPainter(pm_copy)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            brush = QtGui.QBrush(QtGui.QColor(255, 200, 0, 150))  # Yellow for search
            pen = QtGui.QPen(QtCore.Qt.NoPen)
            painter.setBrush(brush)
            painter.setPen(pen)
            for rect in current_page_search_boxes:
                painter.drawRect(rect)
            painter.end()
            pixmap = pm_copy

        # Draw selection overlays if present (on top of search)
        if self._selection_boxes:
            pm_copy = QtGui.QPixmap(pixmap)
            painter = QtGui.QPainter(pm_copy)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            brush = QtGui.QBrush(QtGui.QColor(0, 120, 215, 80))  # Blue for selection
            pen = QtGui.QPen(QtCore.Qt.NoPen)
            painter.setBrush(brush)
            painter.setPen(pen)
            for rect in self._selection_boxes:
                painter.drawRect(rect)
            painter.end()
            pixmap = pm_copy

        self._image_label.setPixmap(pixmap)
        self._image_label.setMinimumSize(pixmap.size())
        self._page_indicator_text = (
            f"{self._document.display_name} — Page {self._page_index + 1} / {self._document.page_count}  "
            f"(zoom: {self._zoom * 100:.0f}%)"
        )
        self._update_page_indicator_label()
        self._update_action_states()
        self._sync_page_scrollbar()

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
                elif (
                    mouse_event.button() == QtCore.Qt.LeftButton
                    and self._text_selection_enabled
                    and self._document
                ):
                    # Start text selection
                    self._selection_start = mouse_event.pos()
                    self._selection_end = mouse_event.pos()
                    if self._rubber_band is None:
                        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self._scroll_area.viewport())
                    self._rubber_band.setGeometry(QtCore.QRect(self._selection_start, QtCore.QSize()))
                    self._rubber_band.show()
                    mouse_event.accept()
                    return True
            elif event.type() == QtCore.QEvent.MouseMove:
                mouse_event = cast(QtGui.QMouseEvent, event)
                if self._panning_active:
                    delta = mouse_event.pos() - self._last_pan_pos
                    self._last_pan_pos = mouse_event.pos()
                    hbar = self._scroll_area.horizontalScrollBar()
                    vbar = self._scroll_area.verticalScrollBar()
                    hbar.setValue(hbar.value() - delta.x())
                    vbar.setValue(vbar.value() - delta.y())
                    mouse_event.accept()
                    return True
                elif (
                    mouse_event.buttons() & QtCore.Qt.LeftButton
                    and self._text_selection_enabled
                    and self._document
                ):
                    # Update text selection
                    self._selection_end = mouse_event.pos()
                    if self._rubber_band:
                        rect = QtCore.QRect(self._selection_start, self._selection_end).normalized()
                        self._rubber_band.setGeometry(rect)
                    mouse_event.accept()
                    return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                mouse_event = cast(QtGui.QMouseEvent, event)
                if mouse_event.button() == QtCore.Qt.MiddleButton and self._panning_active:
                    self._panning_active = False
                    self._scroll_area.viewport().unsetCursor()
                    mouse_event.accept()
                    return True
                elif (
                    mouse_event.button() == QtCore.Qt.LeftButton
                    and self._text_selection_enabled
                    and self._document
                ):
                    # End text selection and extract text from selected area
                    self._selection_end = mouse_event.pos()
                    if self._rubber_band:
                        self._rubber_band.hide()
                    selected_text, boxes = self._extract_selected_text()
                    self._selection_boxes = boxes
                    self._selected_text_cache = selected_text
                    if selected_text.strip():
                        # Copy selected text to clipboard
                        clipboard = QtWidgets.QApplication.clipboard()
                        clipboard.setText(selected_text)
                    self._render_current_page()
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

    def _on_page_scrollbar_changed(self, value: int) -> None:
        if not self._document:
            return
        if value == self._page_index:
            return
        self.go_to_page(value)

    def _sync_page_scrollbar(self) -> None:
        bar = self._page_scrollbar
        if not self._document:
            bar.blockSignals(True)
            bar.setRange(0, 0)
            bar.setValue(0)
            bar.setEnabled(False)
            bar.setVisible(False)
            bar.setToolTip("")
            bar.blockSignals(False)
            return

        page_count = max(self._document.page_count, 0)
        bar.blockSignals(True)
        bar.setRange(0, max(page_count - 1, 0))
        bar.setPageStep(1)
        bar.setSingleStep(1)
        bar.setEnabled(page_count > 1)
        bar.setVisible(page_count > 0)
        bar.setValue(min(self._page_index, max(page_count - 1, 0)))
        current_display = self._page_index + 1 if page_count > 0 else 0
        total_display = page_count if page_count > 0 else 0
        bar.setToolTip(f"페이지 탐색: {current_display} / {total_display}")
        bar.blockSignals(False)

    def _update_page_indicator_label(self) -> None:
        if not self._page_indicator_text:
            self._page_indicator.setText("")
            return

        metrics = self._page_indicator.fontMetrics()
        available = max(self._page_indicator.width() - 16, 80)
        elided = metrics.elidedText(self._page_indicator_text, QtCore.Qt.ElideMiddle, available)
        self._page_indicator.setText(elided)

    def current_document(self) -> Optional[PdfDocument]:
        return self._document

    def current_page_index(self) -> int:
        return self._page_index

    def refresh_current_page(self) -> None:
        if self._document:
            self._render_current_page()

    # -- Text search and selection ------------------------------------------

    def _on_search_input_text_changed(self, text: str) -> None:
        """Handle search input text changes - only clear when text emptied."""
        if not self._document:
            return
        
        if not text.strip():
            self._search_results = []
            self._current_search_index = 0
            self._search_query = ""
            self._search_status.setText("")
            self._search_prev_btn.setEnabled(False)
            self._search_next_btn.setEnabled(False)
            self._render_current_page()
    
    def _perform_search(self, text: str) -> None:
        """Perform search for the given text."""
        if not self._document or not text.strip():
            return
        
        self._search_query = text.strip()
        self._current_search_index = 0
        self._search_results = self._document.search_text(self._search_query)
        
        if self._search_results:
            self._search_prev_btn.setEnabled(True)
            self._search_next_btn.setEnabled(True)
            self._go_to_search_result(0)
        else:
            self._search_prev_btn.setEnabled(False)
            self._search_next_btn.setEnabled(False)
            self._update_search_status()
            self._render_current_page()
    
    def _on_search_return_pressed(self) -> None:
        """Handle Enter key in search box - trigger the search."""
        text = self._search_input.text()
        if text.strip():
            self._perform_search(text)
    
    def _on_search_prev_clicked(self) -> None:
        """Handle previous search result button."""
        if not self._search_results:
            return
        self._current_search_index = (self._current_search_index - 1) % len(self._search_results)
        self._go_to_search_result(self._current_search_index)
        self._update_search_status()
    
    def _on_search_next_clicked(self) -> None:
        """Handle next search result button."""
        if not self._search_results:
            return
        self._current_search_index = (self._current_search_index + 1) % len(self._search_results)
        self._go_to_search_result(self._current_search_index)
        self._update_search_status()
    
    def _update_search_status(self) -> None:
        """Update search status display."""
        if self._search_results and self._search_query:
            current_num = self._current_search_index + 1
            total_num = len(self._search_results)
            self._search_status.setText(f"결과: {current_num}/{total_num}")
        elif self._search_query:
            self._search_status.setText(f"'{self._search_query}'을 찾을 수 없음")
        else:
            self._search_status.setText("")

    def _clear_search_state(self) -> None:
        """Reset search state so highlights and buttons disappear."""
        self._search_results = []
        self._current_search_index = 0
        self._search_query = ""
        self._search_status.setText("")
        self._search_prev_btn.setEnabled(False)
        self._search_next_btn.setEnabled(False)
        self._render_current_page()

    def _show_search_toolbar(self) -> None:
        """Reveal the existing search toolbar for user input."""
        self._search_toolbar.setVisible(True)
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _hide_search_toolbar(self) -> None:
        """Hide the search toolbar."""
        if self._search_toolbar.isVisible():
            self._search_toolbar.hide()
            self._clear_search_state()

    def open_search_dialog(self) -> None:
        """Open a dialog to search for text in the PDF."""
        if not self._document:
            QtWidgets.QMessageBox.warning(self, "검색", "먼저 PDF를 열어주세요.")
            return
        
        self._show_search_toolbar()

    def find_next(self) -> None:
        """Go to the next search result (F3 key handler)."""
        if not self._search_results or not self._search_query:
            QtWidgets.QMessageBox.warning(self, "검색", "먼저 검색을 수행해주세요. (Ctrl+F)")
            return
        
        self._on_search_next_clicked()

    def find_prev(self) -> None:
        """Go to the previous search result (Shift+F3 key handler)."""
        if not self._search_results or not self._search_query:
            QtWidgets.QMessageBox.warning(self, "검색", "먼저 검색을 수행해주세요. (Ctrl+F)")
            return
        
        self._on_search_prev_clicked()

    def _go_to_search_result(self, result_index: int) -> None:
        """Navigate to a search result and highlight it."""
        if not self._search_results or result_index < 0 or result_index >= len(self._search_results):
            return
        
        self._current_search_index = result_index
        page_index, rects = self._search_results[result_index]
        self.go_to_page(page_index)
        
        # Update search status in toolbar
        self._update_search_status()
        self._render_current_page()  # Highlights will be rebuilt per-page

    def _extract_selected_text(self) -> tuple[str, list[QtCore.QRectF]]:
        """Extract text and highlight boxes from the selected region."""
        if not self._document or not self._image_label.pixmap():
            return "", []
        
        # Map viewport coords to image coords, respecting alignment and scroll
        start_pt = self._map_viewport_to_image(self._selection_start)
        end_pt = self._map_viewport_to_image(self._selection_end)
        
        # Normalize the rectangle (ensure start is top-left, end is bottom-right)
        rect_x0 = min(start_pt.x(), end_pt.x())
        rect_y0 = min(start_pt.y(), end_pt.y())
        rect_x1 = max(start_pt.x(), end_pt.x())
        rect_y1 = max(start_pt.y(), end_pt.y())

        # Ensure a minimum box so single-click / tiny drags still capture a char
        min_px = 2.0
        if rect_x1 - rect_x0 < min_px:
            center_x = 0.5 * (rect_x0 + rect_x1)
            rect_x0 = center_x - min_px * 0.5
            rect_x1 = center_x + min_px * 0.5
        if rect_y1 - rect_y0 < min_px:
            center_y = 0.5 * (rect_y0 + rect_y1)
            rect_y0 = center_y - min_px * 0.5
            rect_y1 = center_y + min_px * 0.5
        
        # Convert pixel coordinates to PDF coordinates (considering zoom)
        pdf_x0 = rect_x0 / self._zoom
        pdf_y0 = rect_y0 / self._zoom
        pdf_x1 = rect_x1 / self._zoom
        pdf_y1 = rect_y1 / self._zoom

        # Slightly expand selection to keep edge punctuation (e.g., braces)
        pad = max(0.5, 1.0 / max(self._zoom, 0.001))
        clip_rect = fitz.Rect(pdf_x0 - pad, pdf_y0 - pad, pdf_x1 + pad, pdf_y1 + pad)

        selection_boxes: list[QtCore.QRectF] = []

        # 1) Char-level extraction for fine-grained selection
        try:
            page = self._document.document.load_page(self._page_index)
            raw = page.get_text("rawdict") or {}
            chars = []
            for block in raw.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        for ch in span.get("chars", []):
                            bbox = ch.get("bbox")
                            if not bbox or len(bbox) != 4:
                                continue
                            if fitz.Rect(bbox).intersects(clip_rect):
                                chars.append((bbox[0], bbox[1], bbox[2], bbox[3], ch.get("c", "")))
            if chars:
                # Sort top-to-bottom, left-to-right
                chars.sort(key=lambda t: (round(t[1], 2), t[0]))
                out = []
                current_y = None
                line_gap = 4.0
                try:
                    spans_heights = []
                    for block in raw.get("blocks", []):
                        if block.get("type", 0) != 0:
                            continue
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                h = span.get("size", 0)
                                if h:
                                    spans_heights.append(h)
                    if spans_heights:
                        spans_heights.sort()
                        median_h = spans_heights[len(spans_heights)//2]
                        line_gap = max(2.0, median_h * 0.9)
                except Exception:
                    pass

                for x0, y0, x1, y1, c in chars:
                    if current_y is None:
                        current_y = y0
                    elif abs(y0 - current_y) > line_gap:
                        out.append("\n")
                        current_y = y0
                    out.append(c)
                    rect = QtCore.QRectF(x0 * self._zoom, y0 * self._zoom, (x1 - x0) * self._zoom, (y1 - y0) * self._zoom)
                    selection_boxes.append(rect)
                text_joined = "".join(out).strip()
                if text_joined:
                    return text_joined, selection_boxes
        except Exception:
            pass

        # 2) Try direct clip extraction (preserves punctuation and spacing)
        try:
            page = self._document.document.load_page(self._page_index)
            clip_text = page.get_text("text", clip=clip_rect).strip()
            if clip_text:
                # Use the drawn rect as highlight when we don't have per-char boxes
                selection_boxes = [QtCore.QRectF(rect_x0, rect_y0, rect_x1 - rect_x0, rect_y1 - rect_y0)]
                return clip_text, selection_boxes
        except Exception:
            pass

        # 3) Word-level extraction with rect expansion
        try:
            page = self._document.document.load_page(self._page_index)
            words = page.get_text("words")  # (x0,y0,x1,y1, word, block, line, word_no)
            picked = [w for w in words if fitz.Rect(w[0:4]).intersects(clip_rect)]
            if picked:
                picked.sort(key=lambda w: (round(w[1], 1), w[0]))
                lines = []
                current_y = None
                current_line = []
                for w in picked:
                    y0 = w[1]
                    text = w[4]
                    if current_y is None or abs(y0 - current_y) > 3:
                        if current_line:
                            lines.append(" ".join(current_line))
                        current_line = [text]
                        current_y = y0
                    else:
                        current_line.append(text)
                if current_line:
                    lines.append(" ".join(current_line))
                selection_boxes = [
                    QtCore.QRectF(w[0] * self._zoom, w[1] * self._zoom, (w[2] - w[0]) * self._zoom, (w[3] - w[1]) * self._zoom)
                    for w in picked
                ]
                return "\n".join(lines).strip(), selection_boxes
        except Exception:
            pass

        # 4) Fallback: original clip helper
        selected_text = self._document.get_text_in_rect(
            self._page_index,
            (pdf_x0, pdf_y0, pdf_x1, pdf_y1)
        )
        selection_boxes = [QtCore.QRectF(rect_x0, rect_y0, rect_x1 - rect_x0, rect_y1 - rect_y0)]

        return selected_text, selection_boxes

    def _map_viewport_to_image(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        """Map a viewport position to image coordinates, clamped to pixmap bounds."""
        if not self._image_label.pixmap():
            return QtCore.QPoint()
        mapped = self._image_label.mapFrom(self._scroll_area.viewport(), pos)
        pixmap = self._image_label.pixmap()
        if pixmap:
            x = max(0, min(mapped.x(), pixmap.width()))
            y = max(0, min(mapped.y(), pixmap.height()))
            return QtCore.QPoint(x, y)
        return mapped


# ---- Main window --------------------------------------------------------------


class TabListWidget(QtWidgets.QListWidget):
    """List widget that honours a custom minimum width for splitter resizing."""

    def __init__(self, min_width: int, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._base_min_width = max(1, min_width)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDefaultDropAction(QtCore.Qt.IgnoreAction)
        self.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDefaultDropAction(QtCore.Qt.IgnoreAction)
        self.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)

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

    # Block all drag/drop behaviour to prevent the view from reordering items.
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        event.ignore()

    def startDrag(self, supportedActions: QtCore.Qt.DropActions) -> None:  # type: ignore[override]
        return

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.buttons() & QtCore.Qt.LeftButton:
            event.ignore()
            return
        super().mouseMoveEvent(event)

    # Block all drag/drop behaviour to prevent the view from reordering items.
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # type: ignore[override]
        event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # type: ignore[override]
        event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # type: ignore[override]
        event.ignore()

    def startDrag(self, supportedActions: QtCore.Qt.DropActions) -> None:  # type: ignore[override]
        return

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.buttons() & QtCore.Qt.LeftButton:
            event.ignore()
            return
        super().mouseMoveEvent(event)


class StatusLabel(QtWidgets.QLabel):
    """QLabel that notifies listeners when resized so text can be re-elided."""

    resized = QtCore.pyqtSignal()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.resized.emit()


class SettingsDialog(QtWidgets.QDialog):
    """Dialog window for configuring viewer preferences."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setModal(True)
        self.setMinimumWidth(320)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        self.compact_checkbox = QtWidgets.QCheckBox("세로 탭 컴팩트 모드", self)
        layout.addWidget(self.compact_checkbox)

        self.show_tabs_checkbox = QtWidgets.QCheckBox("세로 탭 표시", self)
        layout.addWidget(self.show_tabs_checkbox)

        self.open_same_window_checkbox = QtWidgets.QCheckBox("추가 파일을 동일 창에서 열기", self)
        layout.addWidget(self.open_same_window_checkbox)

        self.delete_original_checkbox = QtWidgets.QCheckBox("다른 이름으로 저장 시 기존 파일 삭제", self)
        layout.addWidget(self.delete_original_checkbox)

        fit_layout = QtWidgets.QHBoxLayout()
        fit_label = QtWidgets.QLabel("새 문서 기본 보기:", self)
        fit_layout.addWidget(fit_label)

        self.default_fit_combo = QtWidgets.QComboBox(self)
        self.default_fit_combo.addItem("페이지 맞춤", "page")
        self.default_fit_combo.addItem("너비 맞춤", "width")
        fit_layout.addWidget(self.default_fit_combo, 1)
        layout.addLayout(fit_layout)

        sort_layout = QtWidgets.QHBoxLayout()
        sort_label = QtWidgets.QLabel("세로 탭 정렬:", self)
        sort_layout.addWidget(sort_label)

        self.tab_sort_combo = QtWidgets.QComboBox(self)
        self.tab_sort_combo.addItem("정렬 안 함", TAB_SORT_NONE)
        self.tab_sort_combo.addItem("파일명 오름차순", TAB_SORT_NAME_ASC)
        self.tab_sort_combo.addItem("파일명 내림차순", TAB_SORT_NAME_DESC)
        self.tab_sort_combo.addItem("최신순서", TAB_SORT_RECENT)
        sort_layout.addWidget(self.tab_sort_combo, 1)
        layout.addLayout(sort_layout)

        sort_layout = QtWidgets.QHBoxLayout()
        sort_label = QtWidgets.QLabel("세로 탭 정렬:", self)
        sort_layout.addWidget(sort_label)

        self.tab_sort_combo = QtWidgets.QComboBox(self)
        self.tab_sort_combo.addItem("정렬 안 함", TAB_SORT_NONE)
        self.tab_sort_combo.addItem("파일명 오름차순", TAB_SORT_NAME_ASC)
        self.tab_sort_combo.addItem("파일명 내림차순", TAB_SORT_NAME_DESC)
        self.tab_sort_combo.addItem("최신순서", TAB_SORT_RECENT)
        sort_layout.addWidget(self.tab_sort_combo, 1)
        layout.addLayout(sort_layout)

        layout.addStretch(1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def set_compact_mode(self, enabled: bool) -> None:
        self.compact_checkbox.setChecked(enabled)

    def set_tab_panel_visible(self, visible: bool) -> None:
        self.show_tabs_checkbox.setChecked(visible)

    def set_open_same_window(self, enabled: bool) -> None:
        self.open_same_window_checkbox.setChecked(enabled)

    def set_delete_original_on_save_as(self, enabled: bool) -> None:
        self.delete_original_checkbox.setChecked(enabled)

    def set_default_fit_mode(self, mode: str) -> None:
        index = self.default_fit_combo.findData(mode)
        if index < 0:
            index = 0
        self.default_fit_combo.setCurrentIndex(index)

    def set_tab_sort_mode(self, mode: str) -> None:
        index = self.tab_sort_combo.findData(mode)
        if index < 0:
            index = 0
        self.tab_sort_combo.setCurrentIndex(index)

    def set_tab_sort_mode(self, mode: str) -> None:
        index = self.tab_sort_combo.findData(mode)
        if index < 0:
            index = 0
        self.tab_sort_combo.setCurrentIndex(index)

    def compact_mode_enabled(self) -> bool:
        return self.compact_checkbox.isChecked()

    def tab_panel_visible(self) -> bool:
        return self.show_tabs_checkbox.isChecked()

    def open_same_window(self) -> bool:
        return self.open_same_window_checkbox.isChecked()

    def delete_original_on_save_as(self) -> bool:
        return self.delete_original_checkbox.isChecked()

    def default_fit_mode(self) -> str:
        data = self.default_fit_combo.currentData()
        return data if isinstance(data, str) else "page"

    def tab_sort_mode(self) -> str:
        data = self.tab_sort_combo.currentData()
        return data if isinstance(data, str) else DEFAULT_TAB_SORT_MODE

    def tab_sort_mode(self) -> str:
        data = self.tab_sort_combo.currentData()
        return data if isinstance(data, str) else DEFAULT_TAB_SORT_MODE


class MainWindow(QtWidgets.QMainWindow):
    """Primary application window with a vertical tab rail."""

    def __init__(self) -> None:
        super().__init__()
        self._app_title = "PDF Vertical Tabs Viewer v1.0.8"
        self.setWindowTitle(self._app_title)
        self._settings = QtCore.QSettings("PdfVertView", "PdfVerticalTabsViewer")
        geometry = self._settings.value("window/geometry", QtCore.QByteArray(), type=QtCore.QByteArray)
        if isinstance(geometry, QtCore.QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        else:
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
        self._secondary_windows: list[MainWindow] = []
        self._open_additional_in_same_window = True
        self._delete_original_on_save_as = False
        self._default_fit_mode = "page"
        self._compact_mode = False
        self._expanded_width = 220
        self._min_tab_width = 72
        self._max_tab_ratio = 0.8  # allow up to 80% of the window width
        self._tab_panel_visible = True
        self._stored_tab_width = self._expanded_width
        self._tab_sort_mode = DEFAULT_TAB_SORT_MODE
        self._tab_recency: Dict[str, int] = {}
        self._recency_counter = 0
        saved_dir_value = self._settings.value("paths/last_save_dir", "", type=str)
        self._last_save_directory: Optional[Path] = None
        if isinstance(saved_dir_value, str) and saved_dir_value:
            try:
                self._last_save_directory = normalize_path(Path(saved_dir_value))
            except Exception:
                self._last_save_directory = Path(saved_dir_value).expanduser()
        self._tab_sort_mode = DEFAULT_TAB_SORT_MODE
        self._tab_recency: Dict[str, int] = {}
        self._recency_counter = 0
        saved_dir_value = self._settings.value("paths/last_save_dir", "", type=str)
        self._last_save_directory: Optional[Path] = None
        if isinstance(saved_dir_value, str) and saved_dir_value:
            try:
                self._last_save_directory = normalize_path(Path(saved_dir_value))
            except Exception:
                self._last_save_directory = Path(saved_dir_value).expanduser()

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
        self.tab_list.setTextElideMode(QtCore.Qt.ElideNone)
        self.tab_list.setWordWrap(True)
        self.tab_list.setLayoutDirection(LayoutLeftToRight)
        self.tab_list.setMovement(QtWidgets.QListView.Static)
        self.tab_list.setMovement(QtWidgets.QListView.Static)

        self.status_label = StatusLabel("")
        self.status_label.setAlignment(AlignLeft | AlignVCenter)
        self.status_label.setWordWrap(False)
        self.status_label.setMargin(6)
        self.status_label.setStyleSheet("color: #666; border-top: 1px solid #d0d0d0;")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.status_label.resized.connect(self._update_status_label)

        self.tab_panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(self.tab_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self.tab_list, 1)
        panel_layout.addWidget(self.status_label, 0)
        self.tab_panel.setMinimumWidth(self._min_tab_width)
        self.tab_panel.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)

        self.viewer = PdfViewerWidget()
        self.viewer.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.viewer.customContextMenuRequested.connect(self._show_viewer_context_menu)

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
        self._load_preferences()
        self._single_instance_host: Optional[SingleInstanceHost] = None
        try:
            host = SingleInstanceHost(self)
        except Exception:
            host = None
        if host is not None:
            host.open_requested.connect(self._handle_external_open_request)
            self._single_instance_host = host
        self._single_instance_host: Optional[SingleInstanceHost] = None
        try:
            host = SingleInstanceHost(self)
        except Exception:
            host = None
        if host is not None:
            host.open_requested.connect(self._handle_external_open_request)
            self._single_instance_host = host
        self._update_status_label()

    # -- menu setup -----------------------------------------------------------

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        base_menu = menubar.addMenu("기본기능")

        self.open_action = QAction("PDF 열기…", self)
        self.open_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        self.open_action.triggered.connect(self._open_documents_dialog)
        base_menu.addAction(self.open_action)

        self.print_preview_action = QAction("인쇄 미리보기...", self)
        preview_key = getattr(QtGui.QKeySequence.StandardKey, "PrintPreview", None)
        if preview_key is not None:
            self.print_preview_action.setShortcut(QtGui.QKeySequence(preview_key))
        else:
            self.print_preview_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+P"))
        self.print_preview_action.triggered.connect(self._show_print_preview)
        self.print_preview_action.setEnabled(False)
        base_menu.addAction(self.print_preview_action)

        self.close_action = QAction("현재 문서 닫기", self)
        self.close_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Close))
        self.close_action.triggered.connect(self.close_current_document)
        self.close_action.setEnabled(False)
        base_menu.addAction(self.close_action)

        self.compact_action = QAction("세로 탭 컴팩트 모드", self)
        self.compact_action.setCheckable(True)
        self.compact_action.triggered.connect(self.toggle_compact_tabs)

        self.toggle_tabs_action = QAction("세로 탭 표시", self)
        self.toggle_tabs_action.setCheckable(True)
        self.toggle_tabs_action.setChecked(True)
        self.toggle_tabs_action.triggered.connect(self.set_tab_panel_visible)

        base_menu.addSeparator()
        self.settings_action = QAction("설정...", self)
        self.settings_action.triggered.connect(self._show_settings_dialog)
        base_menu.addAction(self.settings_action)

        base_menu.addSeparator()
        base_menu.addAction(self.viewer.prev_action)
        base_menu.addAction(self.viewer.next_action)
        base_menu.addSeparator()
        base_menu.addAction(self.viewer.find_action)
        base_menu.addAction(self.viewer.find_prev_action)
        base_menu.addAction(self.viewer.find_next_action)
        base_menu.addSeparator()
        base_menu.addAction(self.viewer.zoom_out_action)
        base_menu.addAction(self.viewer.zoom_in_action)
        base_menu.addAction(self.viewer.fit_width_action)
        base_menu.addAction(self.viewer.fit_page_action)

        base_menu.addSeparator()
        self.exit_action = QAction("프로그램 종료", self)
        self.exit_action.setShortcut(QtGui.QKeySequence.StandardKey.Quit)
        self.exit_action.triggered.connect(self.close)
        base_menu.addAction(self.exit_action)

        modify_menu = menubar.addMenu("변경기능")

        self.rotate_document_cw_action = QAction("문서 회전 (시계방향 90°)", self)
        self.rotate_document_cw_action.triggered.connect(self._rotate_document_cw)
        modify_menu.addAction(self.rotate_document_cw_action)

        self.rotate_document_ccw_action = QAction("문서 회전 (반시계방향 90°)", self)
        self.rotate_document_ccw_action.triggered.connect(self._rotate_document_ccw)
        modify_menu.addAction(self.rotate_document_ccw_action)

        modify_menu.addSeparator()

        self.save_changes_action = QAction("저장하기", self)
        self.save_changes_action.triggered.connect(self._save_changes)
        modify_menu.addAction(self.save_changes_action)

        self.save_changes_as_action = QAction("다른 이름으로 저장하기", self)
        self.save_changes_as_action.triggered.connect(self._save_changes_as)
        modify_menu.addAction(self.save_changes_as_action)

        modify_menu.addSeparator()

        self.rotate_page_cw_action = QAction("페이지 회전 (시계방향 90°)", self)
        self.rotate_page_cw_action.triggered.connect(self._rotate_page_cw)
        modify_menu.addAction(self.rotate_page_cw_action)

        self.rotate_page_ccw_action = QAction("페이지 회전 (반시계방향 90°)", self)
        self.rotate_page_ccw_action.triggered.connect(self._rotate_page_ccw)
        modify_menu.addAction(self.rotate_page_ccw_action)

        modify_menu.addSeparator()

        self.export_page_image_action = QAction("그림 저장...", self)
        self.export_page_image_action.triggered.connect(self._export_page_image)
        modify_menu.addAction(self.export_page_image_action)

        self._update_modify_actions_state(None)

        info_menu = menubar.addMenu("정보")

        release_action = QAction("릴리스 정보", self)
        release_action.triggered.connect(self._show_release_info)
        info_menu.addAction(release_action)
        self.release_info_action = release_action

        license_action = QAction("라이선스 정보", self)
        license_action.triggered.connect(self._show_license_info)
        info_menu.addAction(license_action)

        author_action = QAction("제작자", self)
        author_action.triggered.connect(self._show_author_info)
        info_menu.addAction(author_action)

    def _load_preferences(self) -> None:
        compact_mode = read_bool_setting(self._settings, "ui/compact_tabs", False)
        self.set_compact_tabs_enabled(compact_mode)

        tab_visible = read_bool_setting(self._settings, "ui/tab_panel_visible", True)
        if tab_visible != self._tab_panel_visible:
            self.set_tab_panel_visible(tab_visible)
        else:
            if self.toggle_tabs_action.isChecked() != tab_visible:
                self.toggle_tabs_action.blockSignals(True)
                self.toggle_tabs_action.setChecked(tab_visible)
                self.toggle_tabs_action.blockSignals(False)

        self._open_additional_in_same_window = read_bool_setting(
            self._settings, "behavior/open_in_same_window", True
        )
        self._delete_original_on_save_as = read_bool_setting(
            self._settings, "behavior/delete_original_on_save_as", False
        )
        mode_pref = self._read_string_setting("ui/default_fit_mode", "page")
        self._apply_default_fit_mode(mode_pref, persist=False)
        sort_pref = self._read_string_setting("ui/tab_sort_mode", DEFAULT_TAB_SORT_MODE)
        self._apply_tab_sort_mode(sort_pref, persist=False)
        sort_pref = self._read_string_setting("ui/tab_sort_mode", DEFAULT_TAB_SORT_MODE)
        self._apply_tab_sort_mode(sort_pref, persist=False)

    def _show_settings_dialog(self) -> None:
        dialog = SettingsDialog(self)
        dialog.set_compact_mode(self._compact_mode)
        dialog.set_tab_panel_visible(self._tab_panel_visible)
        dialog.set_open_same_window(self._open_additional_in_same_window)
        dialog.set_delete_original_on_save_as(self._delete_original_on_save_as)
        dialog.set_default_fit_mode(self._default_fit_mode)
        dialog.set_tab_sort_mode(self._tab_sort_mode)
        dialog.set_tab_sort_mode(self._tab_sort_mode)

        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        self.set_compact_tabs_enabled(dialog.compact_mode_enabled())
        self.set_tab_panel_visible(dialog.tab_panel_visible())
        self.set_open_in_same_window(dialog.open_same_window())
        self.set_delete_original_on_save_as(dialog.delete_original_on_save_as())
        self.set_default_fit_mode(dialog.default_fit_mode())
        self._apply_tab_sort_mode(dialog.tab_sort_mode(), persist=True)
        self._apply_tab_sort_mode(dialog.tab_sort_mode(), persist=True)

    # -- document management -------------------------------------------------

    def _open_documents_dialog(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Open PDF files",
            str(Path.home()),
            "PDF documents (*.pdf);;All files (*)",
        )
        selected_paths = [Path(path_str) for path_str in paths]
        self._handle_new_paths(selected_paths)

    def _handle_new_paths(self, paths: Iterable[Path]) -> None:
        materialized = [Path(path).expanduser() for path in paths]
        if not materialized:
            return

        if not self._documents or self._open_additional_in_same_window:
            for path in materialized:
                self.open_document_from_path(path)
            return

        self._open_paths_in_new_window(materialized)

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

        self._handle_new_paths(paths)
        event.acceptProposedAction()

    def _open_paths_in_new_window(self, paths: list[Path]) -> None:
        new_window = MainWindow()
        self._track_secondary_window(new_window)
        new_window.show()
        for path in paths:
            new_window.open_document_from_path(path)
        new_window.raise_()
        new_window.activateWindow()

    def _handle_external_open_request(self, path_strings: list[str]) -> None:
        paths = [Path(p).expanduser() for p in path_strings]
        if not paths:
            self._focus_window()
            return
        if self._open_additional_in_same_window:
            self._handle_new_paths(paths)
            self._focus_window()
        else:
            self._open_paths_in_new_window(paths)

    def _track_secondary_window(self, window: "MainWindow") -> None:
        self._secondary_windows.append(window)

        window_ref: weakref.ReferenceType[MainWindow] = weakref.ref(window)

        def _cleanup(_: Optional[QtCore.QObject] = None) -> None:
            self._cleanup_secondary_window(window_ref)

        window.destroyed.connect(_cleanup)

    def _cleanup_secondary_window(self, window_ref: weakref.ReferenceType["MainWindow"]) -> None:
        window = window_ref()
        if window in self._secondary_windows:
            self._secondary_windows.remove(window)

    def _focus_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

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
            doc = fitz.open(_prepare_filesystem_path(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open failed", f"{path.name}\n\n{exc}")
            return None

        try:
            needs_pass = bool(getattr(doc, "needs_pass", False))
            if needs_pass:
                unlocked = self._authenticate_document(doc, "")
                if not unlocked:
                    auth_result = self._prompt_for_pdf_password(doc, path)
                    if auth_result is None:
                        doc.close()
                        return None
                    unlocked = auth_result
                if not unlocked:
                    doc.close()
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Open failed",
                        f"{path.name}\n\n암호 확인에 실패했습니다.",
                    )
                    return None

            metadata = doc.metadata or {}
            title_text = (metadata.get("title") or "").strip()
            display = path.name
            icon_label = title_text or path.stem or display
            thumbnail = PdfDocument._build_thumbnail_icon(doc, icon_label)
            return PdfDocument(path=path, document=doc, display_name=display, thumbnail=thumbnail)
        except Exception as exc:
            doc.close()
            QtWidgets.QMessageBox.critical(self, "Open failed", f"{path.name}\n\n{exc}")
            return None

    @staticmethod
    def _authenticate_document(doc: fitz.Document, password: str) -> bool:
        try:
            result = doc.authenticate(password)
        except Exception:
            result = False
        if isinstance(result, (bool, int)):
            if bool(result):
                return True
        elif result not in (None, False):
            return True
        return not getattr(doc, "needs_pass", False)


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
                if self._authenticate_document(doc, password):
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

        self._add_document_to_ui(
            document, normalized, insert_row=insert_row, make_current=make_current
        )


    def _add_document_to_ui(
        self,
        document: PdfDocument,
        normalized: Path,
        insert_row: Optional[int] = None,
        make_current: bool = True,
    ) -> None:
        if normalized in self._documents:
            if make_current:
                self._select_tab(normalized)
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

        if (
            self._last_save_directory is None
            or self._last_save_directory == Path.cwd()
            or (self._last_save_directory and not self._last_save_directory.exists())
        ):
            parent_dir = normalized.parent
            if parent_dir.exists():
                self._last_save_directory = parent_dir

        if (
            self._last_save_directory is None
            or self._last_save_directory == Path.cwd()
            or (self._last_save_directory and not self._last_save_directory.exists())
        ):
            parent_dir = normalized.parent
            if parent_dir.exists():
                self._last_save_directory = parent_dir

        if make_current:
            self._select_tab(normalized)

        self._mark_document_recent(normalized)
        self._sort_tabs()
        self._mark_document_recent(normalized)
        self._sort_tabs()
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
            self._mark_document_recent(document.path)
            if self._tab_sort_mode == TAB_SORT_RECENT:
                self._sort_tabs()
            self._mark_document_recent(document.path)
            if self._tab_sort_mode == TAB_SORT_RECENT:
                self._sort_tabs()
        self.setWindowTitle(self._app_title)
        self._update_status_label()

    def toggle_compact_tabs(self) -> None:
        self.set_compact_tabs_enabled(self.compact_action.isChecked())

    def set_compact_tabs_enabled(self, enabled: bool) -> None:
        if self.compact_action.isChecked() != enabled:
            self.compact_action.blockSignals(True)
            self.compact_action.setChecked(enabled)
            self.compact_action.blockSignals(False)

        if self._compact_mode == enabled:
            return

        self._compact_mode = enabled
        if enabled:
            width = 72
            self.tab_list.setMinimumWidth(width)
            self.tab_list.setMaximumWidth(width)
            self.tab_list.setViewMode(QtWidgets.QListView.IconMode)
            self.tab_list.setResizeMode(QtWidgets.QListView.Adjust)
            self.tab_list.setLayoutDirection(LayoutRightToLeft)
            self.tab_list.set_base_min_width(width)
            self.tab_panel.setMinimumWidth(width)
            self.tab_panel.setMaximumWidth(width)
            self._apply_tab_width(width)
        else:
            self.tab_list.setMinimumWidth(self._min_tab_width)
            self.tab_list.setViewMode(QtWidgets.QListView.ListMode)
            self.tab_list.setResizeMode(QtWidgets.QListView.Fixed)
            self.tab_list.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)
            self.tab_list.setLayoutDirection(LayoutLeftToRight)
            self.tab_list.set_base_min_width(self._min_tab_width)
            self.tab_panel.setMinimumWidth(self._min_tab_width)
            self.tab_panel.setMaximumWidth(QtWidgets.QWIDGETSIZE_MAX)
            self._apply_tab_width(self._expanded_width)
        self.tab_list.setMovement(QtWidgets.QListView.Static)
        self.tab_list.setMovement(QtWidgets.QListView.Static)

        self._settings.setValue("ui/compact_tabs", enabled)
        self._refresh_tab_labels()
        self._enforce_tab_limit()
        self._update_status_label()

    def set_tab_panel_visible(self, visible: bool) -> None:
        if self.toggle_tabs_action.isChecked() != visible:
            self.toggle_tabs_action.blockSignals(True)
            self.toggle_tabs_action.setChecked(visible)
            self.toggle_tabs_action.blockSignals(False)

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

        self._settings.setValue("ui/tab_panel_visible", self._tab_panel_visible)

    def set_open_in_same_window(self, enabled: bool) -> None:
        if self._open_additional_in_same_window == enabled:
            return
        self._open_additional_in_same_window = enabled
        self._settings.setValue("behavior/open_in_same_window", enabled)
        try:
            self._settings.sync()
        except Exception:
            pass

    def set_delete_original_on_save_as(self, enabled: bool) -> None:
        if self._delete_original_on_save_as == enabled:
            return
        self._delete_original_on_save_as = enabled
        self._settings.setValue("behavior/delete_original_on_save_as", enabled)
        try:
            self._settings.sync()
        except Exception:
            pass

    def _read_string_setting(self, key: str, default: str) -> str:
        value = self._settings.value(key, default)
        if isinstance(value, str):
            return value
        return str(value) if value is not None else default

    def set_default_fit_mode(self, mode: str) -> None:
        self._apply_default_fit_mode(mode, persist=True)

    def _apply_default_fit_mode(self, mode: str, *, persist: bool) -> None:
        normalized = "width" if mode == "width" else "page"
        if self._default_fit_mode == normalized and not persist:
            # Already applied during startup load; nothing to do.
            return
        self._default_fit_mode = normalized
        self.viewer.set_default_fit_mode(normalized)
        if persist:
            self._settings.setValue("ui/default_fit_mode", normalized)

    def _apply_tab_sort_mode(self, mode: str, *, persist: bool) -> None:
        normalized = mode if mode in TAB_SORT_MODES else DEFAULT_TAB_SORT_MODE
        previous = self._tab_sort_mode
        if normalized == previous and not persist:
            return
        self._tab_sort_mode = normalized
        if persist:
            self._settings.setValue("ui/tab_sort_mode", normalized)
        if normalized != previous:
            self._sort_tabs()

    def _sort_tabs(self) -> None:
        count = self.tab_list.count()
        if count <= 1:
            return

        items = [self.tab_list.item(index) for index in range(count)]
        if any(item is None for item in items):
            return

        if self._tab_sort_mode == TAB_SORT_RECENT:
            ordered = sorted(
                items,
                key=lambda item: self._tab_recency.get(self._item_path_key(item), 0),
                reverse=True,
            )
            if ordered == items:
                return
            self._reorder_tab_items(ordered)
            return

        if self._tab_sort_mode not in (TAB_SORT_NAME_ASC, TAB_SORT_NAME_DESC):
            return

        reverse = self._tab_sort_mode == TAB_SORT_NAME_DESC

        def name_key(item: QtWidgets.QListWidgetItem) -> tuple[str, str]:
            display = (item.data(TITLE_ROLE) or "").casefold()
            fallback = self._item_path_key(item)
            return (display, fallback.casefold())

        ordered = sorted(items, key=name_key, reverse=reverse)
        if ordered == items:
            return

        self._reorder_tab_items(ordered)

    def _reorder_tab_items(self, ordered: list[QtWidgets.QListWidgetItem]) -> None:
        current_item = self.tab_list.currentItem()
        self.tab_list.blockSignals(True)
        for target_index, item in enumerate(ordered):
            current_index = self.tab_list.row(item)
            if current_index == target_index or current_index < 0:
                continue
            taken = self.tab_list.takeItem(current_index)
            self.tab_list.insertItem(target_index, taken)
        self.tab_list.blockSignals(False)
        if current_item:
            self.tab_list.blockSignals(True)
            self.tab_list.setCurrentItem(current_item)
            self.tab_list.blockSignals(False)
        self._refresh_tab_labels()
        self._update_status_label()

    def _move_tab_to_index(self, path: Path, target_index: int) -> None:
        item = self._tab_items.get(path)
        if not item:
            return
        items = [self.tab_list.item(index) for index in range(self.tab_list.count())]
        if item not in items:
            return
        items.remove(item)
        target_index = max(0, min(target_index, len(items)))
        items.insert(target_index, item)
        self._reorder_tab_items(items)

    def _item_path_key(self, item: QtWidgets.QListWidgetItem) -> str:
        path_str = item.data(PATH_ROLE)
        return str(path_str) if path_str else ""

    def _mark_document_recent(self, path: Path) -> None:
        try:
            normalized_path = normalize_path(path)
        except Exception:
            normalized_path = path
        key = str(normalized_path)
        self._recency_counter += 1
        self._tab_recency[key] = self._recency_counter
        if self._tab_sort_mode == TAB_SORT_RECENT:
            self._move_tab_to_index(normalized_path, 0)

    def _apply_tab_sort_mode(self, mode: str, *, persist: bool) -> None:
        normalized = mode if mode in TAB_SORT_MODES else DEFAULT_TAB_SORT_MODE
        previous = self._tab_sort_mode
        if normalized == previous and not persist:
            return
        self._tab_sort_mode = normalized
        if persist:
            self._settings.setValue("ui/tab_sort_mode", normalized)
        if normalized != previous:
            self._sort_tabs()

    def _sort_tabs(self) -> None:
        count = self.tab_list.count()
        if count <= 1:
            return

        items = [self.tab_list.item(index) for index in range(count)]
        if any(item is None for item in items):
            return

        if self._tab_sort_mode == TAB_SORT_RECENT:
            ordered = sorted(
                items,
                key=lambda item: self._tab_recency.get(self._item_path_key(item), 0),
                reverse=True,
            )
            if ordered == items:
                return
            self._reorder_tab_items(ordered)
            return

        if self._tab_sort_mode not in (TAB_SORT_NAME_ASC, TAB_SORT_NAME_DESC):
            return

        reverse = self._tab_sort_mode == TAB_SORT_NAME_DESC

        def name_key(item: QtWidgets.QListWidgetItem) -> tuple[str, str]:
            display = (item.data(TITLE_ROLE) or "").casefold()
            fallback = self._item_path_key(item)
            return (display, fallback.casefold())

        ordered = sorted(items, key=name_key, reverse=reverse)
        if ordered == items:
            return

        self._reorder_tab_items(ordered)

    def _reorder_tab_items(self, ordered: list[QtWidgets.QListWidgetItem]) -> None:
        current_item = self.tab_list.currentItem()
        self.tab_list.blockSignals(True)
        for target_index, item in enumerate(ordered):
            current_index = self.tab_list.row(item)
            if current_index == target_index or current_index < 0:
                continue
            taken = self.tab_list.takeItem(current_index)
            self.tab_list.insertItem(target_index, taken)
        self.tab_list.blockSignals(False)
        if current_item:
            self.tab_list.blockSignals(True)
            self.tab_list.setCurrentItem(current_item)
            self.tab_list.blockSignals(False)
        self._refresh_tab_labels()
        self._update_status_label()

    def _move_tab_to_index(self, path: Path, target_index: int) -> None:
        item = self._tab_items.get(path)
        if not item:
            return
        items = [self.tab_list.item(index) for index in range(self.tab_list.count())]
        if item not in items:
            return
        items.remove(item)
        target_index = max(0, min(target_index, len(items)))
        items.insert(target_index, item)
        self._reorder_tab_items(items)

    def _item_path_key(self, item: QtWidgets.QListWidgetItem) -> str:
        path_str = item.data(PATH_ROLE)
        return str(path_str) if path_str else ""

    def _mark_document_recent(self, path: Path) -> None:
        try:
            normalized_path = normalize_path(path)
        except Exception:
            normalized_path = path
        key = str(normalized_path)
        self._recency_counter += 1
        self._tab_recency[key] = self._recency_counter
        if self._tab_sort_mode == TAB_SORT_RECENT:
            self._move_tab_to_index(normalized_path, 0)

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
        base_text = f"열린 문서: {count}개"
        current_path = ""
        current_name = ""
        current_document: Optional[PdfDocument] = None
        current_item = self.tab_list.currentItem()
        if current_item:
            document = current_item.data(UserRole)
            if isinstance(document, PdfDocument):
                current_document = document
                current_name = document.display_name
                current_path = str(document.path)

        if current_path:
            metrics = self.status_label.fontMetrics()
            prefix = f"{base_text} | 현재: {current_name} | 위치: "
            available = max(self.status_label.width() - metrics.horizontalAdvance(prefix), 0)
            elided_path = metrics.elidedText(current_path, QtCore.Qt.ElideMiddle, available) if available else current_path
            display_text = f"{prefix}{elided_path}"
            tooltip = current_path
        else:
            display_text = base_text
            tooltip = base_text if self._compact_mode else ""

        self.status_label.setText(display_text)
        self.status_label.setToolTip(tooltip)
        self._update_modify_actions_state(current_document)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._settings.setValue("window/geometry", self.saveGeometry())
        try:
            self._settings.sync()
        except Exception:
            pass
        super().closeEvent(event)
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
        self._tab_recency.pop(str(normalized), None)
        self._tab_recency.pop(str(normalized), None)
        self._remove_watch(normalized)

    def _show_tab_context_menu(self, point: QtCore.QPoint) -> None:
        item = self.tab_list.itemAt(point)
        if not item:
            return

        document = item.data(UserRole)
        if not isinstance(document, PdfDocument):
            return

        menu = QtWidgets.QMenu(self)
        copy_name_action = menu.addAction("파일명 복사")
        copy_dir_action = menu.addAction("전체경로 복사")
        copy_dir_action = menu.addAction("전체경로 복사")
        save_as_action = menu.addAction("다른 이름으로 저장…")
        folder_action = menu.addAction("파일 위치 열기")
        menu.addSeparator()
        close_action = menu.addAction("닫기")

        selected = menu.exec(self.tab_list.mapToGlobal(point))
        if selected is None:
            return

        if selected == copy_name_action:
            self.tab_list.setCurrentItem(item)
            QtWidgets.QApplication.clipboard().setText(document.path.name)
        elif selected == copy_dir_action:
            self.tab_list.setCurrentItem(item)
            QtWidgets.QApplication.clipboard().setText(_display_path_text(document.path.parent))
        elif selected == copy_dir_action:
            self.tab_list.setCurrentItem(item)
            QtWidgets.QApplication.clipboard().setText(_display_path_text(document.path.parent))
        elif selected == save_as_action:
            self.tab_list.setCurrentItem(item)
            self._save_document_as(document)
        elif selected == folder_action:
            self._open_document_directory(document)
        elif selected == close_action:
            self.tab_list.setCurrentItem(item)
            self.close_current_document()

    def _show_viewer_context_menu(self, point: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        menu.addAction(self.viewer.find_action)
        menu.addAction(self.viewer.find_prev_action)
        menu.addAction(self.viewer.find_next_action)
        menu.addSeparator()
        menu.addAction(self.save_changes_action)
        menu.addAction(self.save_changes_as_action)
        menu.addSeparator()
        menu.addAction(self.print_preview_action)
        menu.addSeparator()
        menu.addAction(self.viewer.fit_page_action)
        menu.addAction(self.viewer.fit_width_action)
        menu.addSeparator()
        menu.addAction(self.rotate_document_cw_action)
        menu.addAction(self.rotate_document_ccw_action)
        menu.addSeparator()
        menu.addAction(self.rotate_page_cw_action)
        menu.addAction(self.rotate_page_ccw_action)
        menu.addSeparator()
        menu.addAction(self.export_page_image_action)
        menu.exec(self.viewer.mapToGlobal(point))

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

    def _show_release_info(self) -> None:
        notes_path = resolve_resource_path("RELEASE_NOTES.md")
        try:
            content = notes_path.read_text(encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "릴리스 정보",
                f"릴리스 정보를 불러올 수 없습니다.\n\n{exc}",
            )
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("릴리스 정보")
        dialog.setModal(True)
        layout = QtWidgets.QVBoxLayout(dialog)

        text_edit = QtWidgets.QPlainTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        layout.addWidget(text_edit)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.resize(540, 420)
        dialog.exec_()

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


    def _rotate_document_cw(self) -> None:
        self._perform_document_rotation(90)

    def _rotate_document_ccw(self) -> None:
        self._perform_document_rotation(-90)

    def _rotate_page_cw(self) -> None:
        self._perform_page_rotation(90)

    def _rotate_page_ccw(self) -> None:
        self._perform_page_rotation(-90)

    def _perform_document_rotation(self, degrees: int) -> None:
        document = self._get_current_document()
        if not document:
            QtWidgets.QMessageBox.information(self, "문서 회전", "회전할 문서를 먼저 선택하세요.")
            return
        try:
            document.rotate_document(degrees)
        except Exception as exc:  # pragma: no cover - defensive UI path.
            QtWidgets.QMessageBox.critical(self, "문서 회전 실패", str(exc))
            return
        if self.viewer.current_document() is document:
            self.viewer.refresh_current_page()

    def _perform_page_rotation(self, degrees: int) -> None:
        document = self.viewer.current_document()
        if document is None:
            QtWidgets.QMessageBox.information(self, "페이지 회전", "현재 표시 중인 문서가 없습니다.")
            return
        page_index = self.viewer.current_page_index()
        try:
            document.rotate_page(page_index, degrees)
        except Exception as exc:  # pragma: no cover - defensive UI path.
            QtWidgets.QMessageBox.critical(self, "페이지 회전 실패", str(exc))
            return
        self.viewer.refresh_current_page()

    def _save_changes(self) -> None:
        document = self._get_current_document()
        if not document:
            QtWidgets.QMessageBox.information(self, "변경사항 저장", "저장할 문서를 먼저 선택하세요.")
            return

        path = normalize_path(document.path)
        if not path.exists():
            QtWidgets.QMessageBox.information(
                self,
                "변경사항 저장",
                "원본 파일 경로를 찾을 수 없습니다.\n'변경사항 다른 이름으로 저장...'을 사용하세요.",
            )
            return

        doc_obj = document.document
        try:
            if hasattr(doc_obj, "can_save_incrementally") and doc_obj.can_save_incrementally():
                doc_obj.saveIncr()
            else:
                doc_obj.save(str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "변경사항 저장 실패", str(exc))
            return

        self._update_document_identity(path)
        QtWidgets.QMessageBox.information(
            self,
            "변경사항 저장",
            f"{path}\n\n변경사항을 저장했습니다.",
        )

    def _save_changes_as(self) -> None:
        document = self._get_current_document()
        if not document:
            QtWidgets.QMessageBox.information(self, "변경사항 다른 이름으로 저장", "저장할 문서를 먼저 선택하세요.")
            return
        self._save_document_as(document)

    def _show_print_preview(self) -> None:
        document = self.viewer.current_document()
        if document is None:
            QtWidgets.QMessageBox.information(
                self,
                "인쇄 미리보기",
                "현재 표시 중인 문서가 없습니다.",
            )
            return
        if document.page_count <= 0:
            QtWidgets.QMessageBox.information(
                self,
                "인쇄 미리보기",
                "미리보기할 페이지가 없습니다.",
            )
            return

        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        printer.setDocName(document.display_name)
        preview_dialog = QtPrintSupport.QPrintPreviewDialog(printer, self)
        preview_dialog.setWindowTitle("인쇄 미리보기")

        current_page = self.viewer.current_page_index() + 1

        def handle_preview(pr: QtPrintSupport.QPrinter) -> None:
            first_page, last_page = self._determine_print_range(
                pr,
                document,
                current_page,
                force_all=True,
            )
            self._render_pages_to_printer(
                document,
                pr,
                first_page,
                last_page,
                interactive=False,
            )

        preview_dialog.paintRequested.connect(handle_preview)

        if hasattr(QtPrintSupport, "QPrintPreviewWidget"):
            preview_widget = preview_dialog.findChild(QtPrintSupport.QPrintPreviewWidget)
            if preview_widget:
                preview_widget.setCurrentPage(current_page)

        preview_dialog.exec_()

    def _determine_print_range(
        self,
        printer: QtPrintSupport.QPrinter,
        document: PdfDocument,
        current_page: int,
        *,
        force_all: bool,
    ) -> Tuple[int, int]:
        if force_all:
            return 1, document.page_count

        range_mode = printer.printRange()
        if range_mode == QtPrintSupport.QPrinter.PageRange:
            first_page = printer.fromPage() or 0
            last_page = printer.toPage() or 0
            first = max(first_page, 1)
            last = min(last_page if last_page else document.page_count, document.page_count)
            if first <= last:
                return first, last
            QtWidgets.QMessageBox.warning(self, "인쇄", "유효한 페이지 범위를 선택하세요. 전체 페이지를 사용합니다.")
            return 1, document.page_count

        if range_mode in (QtPrintSupport.QPrinter.CurrentPage, QtPrintSupport.QPrinter.Selection):
            return current_page, current_page

        return 1, document.page_count

    def _render_pages_to_printer(
        self,
        document: PdfDocument,
        printer: QtPrintSupport.QPrinter,
        first_page: int,
        last_page: int,
        *,
        interactive: bool,
    ) -> bool:
        painter = QtGui.QPainter()
        if not painter.begin(printer):
            if interactive:
                QtWidgets.QMessageBox.critical(self, "인쇄 실패", "프린터를 초기화할 수 없습니다.")
            return False

        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            dpi = max(printer.resolution(), 150)
            zoom = dpi / 72.0

            for page_number in range(first_page, last_page + 1):
                if page_number != first_page:
                    if not printer.newPage():
                        if interactive:
                            QtWidgets.QMessageBox.critical(self, "인쇄 실패", "새 페이지를 생성할 수 없습니다.")
                        return False

                try:
                    image = document.render_page(page_number - 1, zoom)
                except Exception as exc:  # pragma: no cover - defensive UI path.
                    if interactive:
                        QtWidgets.QMessageBox.critical(self, "인쇄 실패", str(exc))
                    return False

                if image.isNull():
                    continue

                target_rect = printer.pageRect(QtPrintSupport.QPrinter.DevicePixel)
                if isinstance(target_rect, QtCore.QRectF):
                    target_rect = target_rect.toRect()
                if target_rect.isEmpty():
                    target_rect = printer.pageRect()

                available_width = max(float(target_rect.width()), 1.0)
                available_height = max(float(target_rect.height()), 1.0)
                scale = min(available_width / max(image.width(), 1), available_height / max(image.height(), 1))

                draw_width = image.width() * scale
                draw_height = image.height() * scale
                draw_x = float(target_rect.x()) + (available_width - draw_width) / 2.0
                draw_y = float(target_rect.y()) + (available_height - draw_height) / 2.0

                draw_rect = QtCore.QRectF(draw_x, draw_y, draw_width, draw_height)
                painter.drawImage(draw_rect, image)
            return True
        finally:
            painter.end()

    def _get_current_document(self) -> Optional[PdfDocument]:
        item = self.tab_list.currentItem()
        if not item:
            return None
        document = item.data(UserRole)
        return document if isinstance(document, PdfDocument) else None

    def _prompt_document_choice(self) -> Optional[PdfDocument]:
        if not self._documents:
            return None

        documents = list(self._documents.values())
        if len(documents) == 1:
            return documents[0]

        current_doc = self._get_current_document()
        options = [f"{doc.display_name} ({doc.path})" for doc in documents]
        default_index = documents.index(current_doc) if current_doc in documents else 0
        selection, ok = QtWidgets.QInputDialog.getItem(
            self,
            "문서 선택",
            "이미지로 저장할 문서를 선택하세요:",
            options,
            default_index,
            False,
        )
        if not ok:
            return None
        try:
            selected_index = options.index(selection)
        except ValueError:
            return None
        return documents[selected_index]

    def _prompt_page_selection(self, document: PdfDocument) -> Optional[int]:
        if document.page_count <= 0:
            QtWidgets.QMessageBox.information(self, "페이지 선택", "선택 가능한 페이지가 없습니다.")
            return None
        default_page = 1
        if self.viewer.current_document() is document:
            default_page = self.viewer.current_page_index() + 1

        page_number, ok = QtWidgets.QInputDialog.getInt(
            self,
            "페이지 선택",
            f"{document.display_name}에서 저장할 페이지 번호(1~{document.page_count})를 입력하세요:",
            default_page,
            1,
            document.page_count,
        )
        if not ok:
            return None
        return page_number - 1

    def _export_page_image(self) -> None:
        if not self._documents:
            QtWidgets.QMessageBox.information(self, "그림 저장", "열려 있는 문서가 없습니다.")
            return

        document = self._prompt_document_choice()
        if document is None:
            return

        page_index = self._prompt_page_selection(document)
        if page_index is None:
            return

        suggested_name = f"{document.display_name}_p{page_index + 1:03d}.png"
        default_path = document.path.parent / suggested_name if document.path else Path.home() / suggested_name
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "그림 저장",
            str(default_path),
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp);;All files (*)",
        )
        if not file_path:
            return

        target = Path(file_path).expanduser()
        if not target.suffix:
            target = target.with_suffix(".png")

        suffix_map = {
            ".png": "PNG",
            ".jpg": "JPG",
            ".jpeg": "JPG",
            ".bmp": "BMP",
        }
        image_format = suffix_map.get(target.suffix.lower(), "PNG")
        if target.suffix.lower() not in suffix_map:
            target = target.with_suffix(".png")

        try:
            image = document.render_page(page_index, 2.0)
        except Exception as exc:  # pragma: no cover - defensive UI path.
            QtWidgets.QMessageBox.critical(self, "그림 저장 실패", str(exc))
            return

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        if not image.save(_prepare_filesystem_path(target), image_format):
            QtWidgets.QMessageBox.critical(
                self,
                "그림 저장 실패",
                f"{_display_path_text(target)}\n\n이미지를 저장할 수 없습니다.",
            )
            return

        QtWidgets.QMessageBox.information(
            self,
            "그림 저장",
            f"{_display_path_text(target)}\n\n이미지를 저장했습니다.",
        )



    def _update_modify_actions_state(self, current_document: Optional[PdfDocument]) -> None:
        has_any_document = bool(self._documents)
        has_current = current_document is not None
        has_viewer_doc = self.viewer.current_document() is not None

        for action in (
            self.rotate_document_cw_action,
            self.rotate_document_ccw_action,
            self.save_changes_action,
            self.save_changes_as_action,
        ):
            action.setEnabled(has_current)
        for action in (self.rotate_page_cw_action, self.rotate_page_ccw_action):
            action.setEnabled(has_viewer_doc)
        self.export_page_image_action.setEnabled(has_any_document)
        self.print_preview_action.setEnabled(has_viewer_doc)

    def _remember_last_save_directory(self, directory: Path) -> None:
        normalized = normalize_path(directory)
        self._last_save_directory = normalized
        self._settings.setValue("paths/last_save_dir", str(normalized))

    def _remember_last_save_directory(self, directory: Path) -> None:
        normalized = normalize_path(directory)
        self._last_save_directory = normalized
        self._settings.setValue("paths/last_save_dir", str(normalized))

    def _save_document_as(self, document: PdfDocument) -> None:
        suggested_path = document.path if document.path.suffix else document.path.with_suffix(".pdf")
        suggested_path = normalize_path(suggested_path)

        if (
            self._last_save_directory
            and not suggested_path.parent.exists()
        ):
            suggested_path = normalize_path(self._last_save_directory / suggested_path.name)
        suggested_path = normalize_path(suggested_path)

        if (
            self._last_save_directory
            and not suggested_path.parent.exists()
        ):
            suggested_path = normalize_path(self._last_save_directory / suggested_path.name)
        new_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "다른 이름으로 저장",
            "다른 이름으로 저장",
            str(suggested_path),
            "PDF documents (*.pdf);;All files (*)",
        )
        if not new_path:
            return

        target_path = Path(new_path)
        target_path = Path(new_path)
        try:
            document.save_as(target_path)
            document.save_as(target_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "저장 실패", str(exc))
            return

        old_path = normalize_path(document.path)
        new_path_normalized = normalize_path(target_path)
        self._remember_last_save_directory(new_path_normalized.parent)
        new_path_normalized = normalize_path(target_path)
        self._remember_last_save_directory(new_path_normalized.parent)
        original_removed = False
        removal_error: Optional[str] = None
        display_path = _display_path_text(new_path_normalized)

        if new_path_normalized != old_path:
            self._handle_document_renamed(old_path, new_path_normalized)
            if self._delete_original_on_save_as:
                try:
                    if old_path.exists():
                        old_path.unlink()
                        original_removed = True
                except Exception as exc:
                    removal_error = str(exc)
        else:
            self._update_document_identity(old_path)

        message = f"{display_path}\n\n저장이 완료되었습니다."
        if original_removed:
            message += "\n\n원본 파일을 삭제했습니다."
        QtWidgets.QMessageBox.information(self, "저장 완료", message)
        if removal_error:
            QtWidgets.QMessageBox.warning(
                self,
                "삭제 실패",
                f"새 파일은 저장되었지만 원본 파일을 삭제하지 못했습니다.\n\n{removal_error}",
            )

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
    app_icon = get_application_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    settings = QtCore.QSettings("PdfVertView", "PdfVerticalTabsViewer")
    open_same_window = read_bool_setting(settings, "behavior/open_in_same_window", True)
    cli_paths = [Path(argument).expanduser() for argument in sys.argv[1:] if argument]
    existing_paths = [path for path in cli_paths if path.exists()]

    if open_same_window and existing_paths and forward_paths_to_primary(existing_paths):
        return 0

    window = MainWindow()
    window.show()

    for path in existing_paths:
        window.open_document_from_path(path)

    return exec_qapplication(app)


if __name__ == "__main__":
    raise SystemExit(main())

