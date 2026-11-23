#!/usr/bin/env python3
import sys
import os
import json
import math
import copy
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QSlider,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QGroupBox,
    QFileDialog, QMessageBox, QMenu, QDialog, QDialogButtonBox,
    QComboBox, QCheckBox, QScrollArea, QTabWidget, QColorDialog,
    QTextEdit, QFormLayout, QSizePolicy, QToolBar, QStatusBar,
    QInputDialog, QAbstractItemView
)
from PyQt6.QtCore import (
    Qt, QRect, QPoint, QSize, QRectF, QPointF, QTimer, QMimeData,
    pyqtSignal, QEvent
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QBrush, QColor, QFont, QFontDatabase,
    QKeySequence, QAction, QTransform, QPainterPath, QCursor,
    QDragEnterEvent, QDropEvent, QImage, QPalette, QIcon, QFontMetrics
)

CANVAS_W, CANVAS_H = 1920, 1080
SCENE_VERSION = "1.0"
APP_NAME = "wxHDS-Rndr Editor"
FILE_EXT = ".rndr.json"

DARK_BG      = "#1a1a2e"
PANEL_BG     = "#16213e"
ACCENT       = "#0f3460"
ACCENT2      = "#533483"
HIGHLIGHT    = "#e94560"
SURFACE      = "#0d0d1a"
TEXT_PRIMARY = "#e0e0e0"
TEXT_DIM     = "#888899"
BORDER       = "#2a2a4a"
HANDLE_COLOR = "#e94560"
SELECT_COLOR = "#00d4ff"

def make_source(kind="image"):
    base = {
        "id": os.urandom(4).hex(),
        "kind": kind,         
        "name": f"New {kind.title()} Source",
        "visible": True,
        "locked": False,
        "x": 0, "y": 0,
        "w": 640, "h": 360,
        "rotation": 0.0,
        "crop_top": 0, "crop_bottom": 0,
        "crop_left": 0, "crop_right": 0,
        "opacity": 100,
        "blend_mode": "normal",
        "scale_mode": "stretch",
        "audio_enabled": False,
        "volume": 100,
        "muted": False,
        "audio_monitor": "none",
        "compressor": {
            "enabled": False,
            "ratio": 10.0,
            "threshold": -18.0,
            "attack": 6.0,
            "release": 60.0,
            "output_gain": 0.0,
            "sidechain_source": ""
        },
        "filters": [],
    }

    if kind == "image":
        base["file"] = ""
    elif kind == "browser":
        base["url"] = "https://elliotd.au"
        base["fps"] = 30
        base["custom_css"] = ""
        base["audio_enabled"] = True
        base["shutdown_when_not_visible"] = False
        base["refresh_on_activate"] = True
        base["reroute_audio"] = False
        base["browser_width"] = 1920
        base["browser_height"] = 1080
    elif kind == "text":
        base["text"] = "Sample Text"
        base["font_family"] = "Arial"
        base["font_size"] = 72
        base["font_bold"] = False
        base["font_italic"] = False
        base["font_underline"] = False
        base["color"] = "#ffffff"
        base["outline"] = False
        base["outline_color"] = "#000000"
        base["outline_size"] = 3
        base["shadow"] = False
        base["shadow_color"] = "#000000"
        base["align"] = "left"
        base["valign"] = "top"
        base["word_wrap"] = True
        base["gradient"] = False
        base["gradient_color"] = "#ff0000"
        base["gradient_direction"] = 90

    return base


def make_scene(name="Scene"):
    return {
        "version": SCENE_VERSION,
        "name": name,
        "canvas_w": CANVAS_W,
        "canvas_h": CANVAS_H,
        "sources": [],
        "audio_sources": [],
        "output": {
            "width": 1920,
            "height": 1080,
            "fps": 60,
            "format": "mkv",
            "encoder": "x264",
            "bitrate": 6000
        }
    }


class CanvasWidget(QWidget):

    selectionChanged = pyqtSignal(object)  
    sourceMoved      = pyqtSignal(object)   
    sceneChanged     = pyqtSignal()

    HANDLE_NONE   = 0
    HANDLE_BODY   = 1
    HANDLE_TL     = 2
    HANDLE_TR     = 3
    HANDLE_BL     = 4
    HANDLE_BR     = 5
    HANDLE_T      = 6
    HANDLE_B      = 7
    HANDLE_L      = 8
    HANDLE_R      = 9
    HANDLE_ROTATE = 10

    HANDLE_SIZE = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)

        self.scene_data = make_scene()
        self.selected_id = None
        self._drag_start = None
        self._drag_orig  = None
        self._active_handle = self.HANDLE_NONE
        self._preview_pixmaps = {}  
        self._scale = 1.0
        self._offset = QPointF(0, 0)

        self.setMinimumSize(400, 225)

    def sources(self):
        return self.scene_data["sources"]

    def selected_source(self):
        if self.selected_id is None:
            return None
        for s in self.sources():
            if s["id"] == self.selected_id:
                return s
        return None

    def set_scene(self, scene_data):
        self.scene_data = scene_data
        self.selected_id = None
        self._preview_pixmaps.clear()
        self.update()
        self.selectionChanged.emit(None)

    def _canvas_to_widget(self, x, y):
        return (x * self._scale + self._offset.x(),
                y * self._scale + self._offset.y())

    def _widget_to_canvas(self, wx, wy):
        return ((wx - self._offset.x()) / self._scale,
                (wy - self._offset.y()) / self._scale)

    def _source_rect_widget(self, s):
        x, y = self._canvas_to_widget(s["x"], s["y"])
        w = s["w"] * self._scale
        h = s["h"] * self._scale
        return QRectF(x, y, w, h)

    def _handle_rects(self, s):
        r = self._source_rect_widget(s)
        hs = self.HANDLE_SIZE
        cx, cy = r.center().x(), r.center().y()
        handles = {
            self.HANDLE_TL: QRectF(r.left()-hs/2,    r.top()-hs/2,    hs, hs),
            self.HANDLE_TR: QRectF(r.right()-hs/2,   r.top()-hs/2,    hs, hs),
            self.HANDLE_BL: QRectF(r.left()-hs/2,    r.bottom()-hs/2, hs, hs),
            self.HANDLE_BR: QRectF(r.right()-hs/2,   r.bottom()-hs/2, hs, hs),
            self.HANDLE_T:  QRectF(cx-hs/2,          r.top()-hs/2,    hs, hs),
            self.HANDLE_B:  QRectF(cx-hs/2,          r.bottom()-hs/2, hs, hs),
            self.HANDLE_L:  QRectF(r.left()-hs/2,    cy-hs/2,         hs, hs),
            self.HANDLE_R:  QRectF(r.right()-hs/2,   cy-hs/2,         hs, hs),
            self.HANDLE_ROTATE: QRectF(cx-hs/2, r.top()-30-hs/2, hs, hs),
        }
        return handles

    def _hit_test_source(self, s, wx, wy):
        handles = self._handle_rects(s)
        for role, rect in handles.items():
            if rect.contains(wx, wy):
                return role
        r = self._source_rect_widget(s)
        if r.contains(wx, wy):
            return self.HANDLE_BODY
        return self.HANDLE_NONE

    def resizeEvent(self, e):
        self._recalc_scale()
        self.update()

    def _recalc_scale(self):
        w, h = self.width(), self.height()
        sw = w / CANVAS_W
        sh = h / CANVAS_H
        self._scale = min(sw, sh)
        ox = (w - CANVAS_W * self._scale) / 2
        oy = (h - CANVAS_H * self._scale) / 2
        self._offset = QPointF(ox, oy)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p.fillRect(self.rect(), QColor(SURFACE))

        cw = int(CANVAS_W * self._scale)
        ch = int(CANVAS_H * self._scale)
        ox, oy = int(self._offset.x()), int(self._offset.y())
        p.fillRect(ox, oy, cw, ch, QColor("#000000"))

        self._draw_checker(p, ox, oy, cw, ch)

        p.setClipRect(ox, oy, cw, ch)

        for src in reversed(self.sources()):
            if not src["visible"]:
                continue
            self._draw_source(p, src)

        p.setClipping(False)

        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRect(ox, oy, cw, ch)

        sel = self.selected_source()
        if sel:
            self._draw_handles(p, sel)

        p.end()

    def _draw_checker(self, p, ox, oy, cw, ch):
        size = 16
        c1 = QColor("#1a1a1a")
        c2 = QColor("#2a2a2a")
        for row in range(0, ch // size + 1):
            for col in range(0, cw // size + 1):
                c = c1 if (row + col) % 2 == 0 else c2
                p.fillRect(ox + col*size, oy + row*size, size, size, c)

    def _draw_source(self, p, src):
        r = self._source_rect_widget(src)
        opacity = src.get("opacity", 100) / 100.0

        p.save()
        p.setOpacity(opacity)

        cx, cy = r.center().x(), r.center().y()
        p.translate(cx, cy)
        p.rotate(src.get("rotation", 0.0))
        p.translate(-cx, -cy)

        kind = src["kind"]
        if kind == "image":
            self._draw_image_source(p, src, r)
        elif kind == "browser":
            self._draw_browser_source(p, src, r)
        elif kind == "text":
            self._draw_text_source(p, src, r)

        p.restore()

    def _draw_image_source(self, p, src, r):
        sid = src["id"]
        file_path = src.get("file", "")
        if sid not in self._preview_pixmaps and file_path and os.path.isfile(file_path):
            pm = QPixmap(file_path)
            if not pm.isNull():
                self._preview_pixmaps[sid] = pm
        pm = self._preview_pixmaps.get(sid)
        if pm and not pm.isNull():
            p.drawPixmap(r.toRect(), pm)
        else:
            p.fillRect(r, QColor(40, 40, 80, 180))
            p.setPen(QPen(QColor(100, 100, 180), 1))
            p.drawRect(r)
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Arial", max(8, int(12 * self._scale))))
            name = src.get("file") or "(no file)"
            p.drawText(r.toRect(), Qt.AlignmentFlag.AlignCenter, f"🖼 {os.path.basename(name)}")

    def _draw_browser_source(self, p, src, r):
        p.fillRect(r, QColor(20, 40, 20, 180))
        p.setPen(QPen(QColor(60, 160, 60), 1))
        p.drawRect(r)
        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Arial", max(8, int(11 * self._scale))))
        url = src.get("url", "")
        p.drawText(r.toRect(), Qt.AlignmentFlag.AlignCenter,
                   f"🌐 {url[:48]}{'…' if len(url)>48 else ''}")

    def _draw_text_source(self, p, src, r):
        text = src.get("text", "")
        color = QColor(src.get("color", "#ffffff"))
        family = src.get("font_family", "Arial")
        size_pt = max(4, int(src.get("font_size", 72) * self._scale))
        bold   = src.get("font_bold", False)
        italic = src.get("font_italic", False)
        underline = src.get("font_underline", False)

        font = QFont(family, size_pt)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)

        align_h = Qt.AlignmentFlag.AlignLeft
        ah = src.get("align", "left")
        if ah == "center": align_h = Qt.AlignmentFlag.AlignHCenter
        elif ah == "right": align_h = Qt.AlignmentFlag.AlignRight

        align_v = Qt.AlignmentFlag.AlignTop
        av = src.get("valign", "top")
        if av == "center": align_v = Qt.AlignmentFlag.AlignVCenter
        elif av == "bottom": align_v = Qt.AlignmentFlag.AlignBottom

        flags = align_h | align_v
        if src.get("word_wrap", True):
            flags |= Qt.TextFlag.TextWordWrap

        if src.get("shadow", False):
            sc = QColor(src.get("shadow_color", "#000000"))
            sc.setAlphaF(0.7)
            p.setFont(font)
            p.setPen(QPen(sc))
            sr = r.translated(2, 2)
            p.drawText(sr.toRect(), int(flags), text)

        if src.get("outline", False):
            oc = QColor(src.get("outline_color", "#000000"))
            ot = src.get("outline_size", 3)
            p.setFont(font)
            p.setPen(QPen(oc, ot))
            for dx, dy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
                or_ = r.translated(dx, dy)
                p.drawText(or_.toRect(), int(flags), text)

        p.setFont(font)
        p.setPen(QPen(color))
        p.drawText(r.toRect(), int(flags), text)

    def _draw_handles(self, p, s):
        r = self._source_rect_widget(s)
        hs = self.HANDLE_SIZE

        p.setPen(QPen(QColor(SELECT_COLOR), 1.5, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = r.center().x(), r.center().y()
        p.save()
        p.translate(cx, cy)
        p.rotate(s.get("rotation", 0.0))
        p.translate(-cx, -cy)
        p.drawRect(r)
        p.restore()

        handles = self._handle_rects(s)
        p.setPen(QPen(QColor(HANDLE_COLOR), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        for role, hr in handles.items():
            if role == self.HANDLE_ROTATE:
                p.setBrush(QBrush(QColor(HIGHLIGHT)))
                p.drawEllipse(hr)
                p.setBrush(QBrush(QColor("#ffffff")))
            else:
                p.drawRect(hr)

        rh = handles[self.HANDLE_ROTATE]
        p.setPen(QPen(QColor(SELECT_COLOR), 1))
        p.drawLine(int(cx), int(r.top()), int(rh.center().x()), int(rh.center().y()))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        wx, wy = e.position().x(), e.position().y()
        cx, cy = self._widget_to_canvas(wx, wy)

        sel = self.selected_source()
        if sel:
            role = self._hit_test_source(sel, wx, wy)
            if role != self.HANDLE_NONE:
                self._active_handle = role
                self._drag_start = (wx, wy)
                self._drag_orig = copy.deepcopy(sel)
                return

        hit = None
        for src in self.sources():
            if not src["visible"] or src.get("locked", False):
                continue
            role = self._hit_test_source(src, wx, wy)
            if role != self.HANDLE_NONE:
                hit = src
                break

        if hit:
            self.selected_id = hit["id"]
            self._active_handle = self.HANDLE_BODY
            self._drag_start = (wx, wy)
            self._drag_orig = copy.deepcopy(hit)
            self.selectionChanged.emit(hit)
        else:
            self.selected_id = None
            self._active_handle = self.HANDLE_NONE
            self.selectionChanged.emit(None)

        self.update()

    def mouseMoveEvent(self, e):
        wx, wy = e.position().x(), e.position().y()
        if self._drag_start is None:
            self._update_cursor(wx, wy)
            return

        dx = (wx - self._drag_start[0]) / self._scale
        dy = (wy - self._drag_start[1]) / self._scale

        sel = self.selected_source()
        if sel is None:
            return

        orig = self._drag_orig
        role = self._active_handle

        if role == self.HANDLE_BODY:
            sel["x"] = int(orig["x"] + dx)
            sel["y"] = int(orig["y"] + dy)

        elif role == self.HANDLE_ROTATE:
            cx = orig["x"] + orig["w"] / 2
            cy = orig["y"] + orig["h"] / 2
            ccx, ccy = self._canvas_to_widget(cx, cy)
            angle = math.degrees(math.atan2(wy - ccy, wx - ccx)) + 90
            sel["rotation"] = round(angle, 1)

        elif role in (self.HANDLE_TL, self.HANDLE_TR, self.HANDLE_BL, self.HANDLE_BR,
                      self.HANDLE_T, self.HANDLE_B, self.HANDLE_L, self.HANDLE_R):
            self._resize_source(sel, orig, role, dx, dy)

        self.sourceMoved.emit(sel)
        self.update()

    def _resize_source(self, sel, orig, role, dx, dy):
        min_sz = 10
        x, y, w, h = orig["x"], orig["y"], orig["w"], orig["h"]

        if role in (self.HANDLE_TL, self.HANDLE_T, self.HANDLE_TR):
            new_y = min(y + dy, y + h - min_sz)
            new_h = max(h - dy, min_sz)
            if role != self.HANDLE_TR:
                new_h2 = y + h - new_y
                sel["y"] = int(new_y)
                sel["h"] = max(int(new_h2), min_sz)
            else:
                sel["y"] = int(new_y)
                sel["h"] = max(int(y + h - new_y), min_sz)

        if role in (self.HANDLE_BL, self.HANDLE_B, self.HANDLE_BR):
            sel["h"] = max(int(h + dy), min_sz)

        if role in (self.HANDLE_TL, self.HANDLE_L, self.HANDLE_BL):
            new_x = min(x + dx, x + w - min_sz)
            new_w = x + w - new_x
            sel["x"] = int(new_x)
            sel["w"] = max(int(new_w), min_sz)

        if role in (self.HANDLE_TR, self.HANDLE_R, self.HANDLE_BR):
            sel["w"] = max(int(w + dx), min_sz)

    def mouseReleaseEvent(self, e):
        if self._drag_start is not None:
            self._drag_start = None
            self._drag_orig  = None
            self._active_handle = self.HANDLE_NONE
            self.sceneChanged.emit()

    def _update_cursor(self, wx, wy):
        sel = self.selected_source()
        if sel:
            role = self._hit_test_source(sel, wx, wy)
            cursors = {
                self.HANDLE_BODY:   Qt.CursorShape.SizeAllCursor,
                self.HANDLE_ROTATE: Qt.CursorShape.CrossCursor,
                self.HANDLE_TL:     Qt.CursorShape.SizeFDiagCursor,
                self.HANDLE_BR:     Qt.CursorShape.SizeFDiagCursor,
                self.HANDLE_TR:     Qt.CursorShape.SizeBDiagCursor,
                self.HANDLE_BL:     Qt.CursorShape.SizeBDiagCursor,
                self.HANDLE_T:      Qt.CursorShape.SizeVerCursor,
                self.HANDLE_B:      Qt.CursorShape.SizeVerCursor,
                self.HANDLE_L:      Qt.CursorShape.SizeHorCursor,
                self.HANDLE_R:      Qt.CursorShape.SizeHorCursor,
            }
            if role in cursors:
                self.setCursor(cursors[role])
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def keyPressEvent(self, e):
        sel = self.selected_source()
        if sel is None or sel.get("locked", False):
            return
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        key = e.key()
        if key == Qt.Key.Key_Left:  sel["x"] -= step
        elif key == Qt.Key.Key_Right: sel["x"] += step
        elif key == Qt.Key.Key_Up:    sel["y"] -= step
        elif key == Qt.Key.Key_Down:  sel["y"] += step
        elif key == Qt.Key.Key_Delete:
            self._delete_selected()
            return
        self.sourceMoved.emit(sel)
        self.sceneChanged.emit()
        self.update()

    def _delete_selected(self):
        if self.selected_id is None:
            return
        self.scene_data["sources"] = [s for s in self.sources() if s["id"] != self.selected_id]
        self.selected_id = None
        self.selectionChanged.emit(None)
        self.sceneChanged.emit()
        self.update()

    def refresh_preview(self, source_id):
        if source_id in self._preview_pixmaps:
            del self._preview_pixmaps[source_id]
        self.update()

    def contextMenuEvent(self, e):
        sel = self.selected_source()
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{background:{PANEL_BG};color:{TEXT_PRIMARY};border:1px solid {BORDER};}}"
                           f"QMenu::item:selected{{background:{ACCENT};}}")

        if sel:
            dup = menu.addAction("Duplicate Source")
            dup.triggered.connect(lambda: self._duplicate_source(sel))
            lock_label = "Unlock Source" if sel.get("locked") else "Lock Source"
            lk = menu.addAction(lock_label)
            lk.triggered.connect(lambda: self._toggle_lock(sel))
            menu.addSeparator()
            vis_label = "Show Source" if not sel["visible"] else "Hide Source"
            vis = menu.addAction(vis_label)
            vis.triggered.connect(lambda: self._toggle_visible(sel))
            menu.addSeparator()
            fit = menu.addAction("Fit to Canvas")
            fit.triggered.connect(lambda: self._fit_to_canvas(sel))
            center = menu.addAction("Center on Canvas")
            center.triggered.connect(lambda: self._center_source(sel))
            reset_tr = menu.addAction("Reset Transform")
            reset_tr.triggered.connect(lambda: self._reset_transform(sel))
            menu.addSeparator()
            rm = menu.addAction("Remove Source")
            rm.triggered.connect(self._delete_selected)

        menu.exec(e.globalPos())

    def _duplicate_source(self, src):
        new_src = copy.deepcopy(src)
        new_src["id"] = os.urandom(4).hex()
        new_src["name"] = src["name"] + " (copy)"
        new_src["x"] += 20
        new_src["y"] += 20
        self.scene_data["sources"].insert(0, new_src)
        self.selected_id = new_src["id"]
        self.selectionChanged.emit(new_src)
        self.sceneChanged.emit()
        self.update()

    def _toggle_lock(self, src):
        src["locked"] = not src.get("locked", False)
        self.sceneChanged.emit()
        self.update()

    def _toggle_visible(self, src):
        src["visible"] = not src["visible"]
        self.sceneChanged.emit()
        self.update()

    def _fit_to_canvas(self, src):
        src["x"] = 0; src["y"] = 0
        src["w"] = CANVAS_W; src["h"] = CANVAS_H
        self.sourceMoved.emit(src)
        self.sceneChanged.emit()
        self.update()

    def _center_source(self, src):
        src["x"] = (CANVAS_W - src["w"]) // 2
        src["y"] = (CANVAS_H - src["h"]) // 2
        self.sourceMoved.emit(src)
        self.sceneChanged.emit()
        self.update()

    def _reset_transform(self, src):
        src["rotation"] = 0.0
        src["crop_top"] = src["crop_bottom"] = src["crop_left"] = src["crop_right"] = 0
        self.sourceMoved.emit(src)
        self.sceneChanged.emit()
        self.update()


class SourceListWidget(QWidget):

    selectionChanged = pyqtSignal(str)  
    orderChanged     = pyqtSignal()
    visibilityToggled = pyqtSignal(str, bool)

    def __init__(self, canvas: CanvasWidget, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._build_ui()
        canvas.selectionChanged.connect(self._on_canvas_selection)
        canvas.sceneChanged.connect(self.refresh)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        lbl = QLabel("SOURCES")
        lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;font-weight:bold;letter-spacing:1px;")
        lay.addWidget(lbl)

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setStyleSheet(f"""
            QListWidget {{
                background:{SURFACE}; color:{TEXT_PRIMARY};
                border:1px solid {BORDER}; border-radius:4px;
            }}
            QListWidget::item {{ padding:6px 4px; border-bottom:1px solid {BORDER}; }}
            QListWidget::item:selected {{ background:{ACCENT}; }}
        """)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        lay.addWidget(self.list)

        btn_row = QHBoxLayout()
        for icon, tip, fn in [
            ("+", "Add Source", self._add_menu),
            ("−", "Remove Source", self._remove_source),
            ("↑", "Move Up",   self._move_up),
            ("↓", "Move Down", self._move_down),
        ]:
            b = QPushButton(icon)
            b.setToolTip(tip)
            b.setFixedSize(28, 28)
            b.setStyleSheet(f"QPushButton{{background:{ACCENT};color:{TEXT_PRIMARY};border:none;border-radius:4px;font-size:14px;}}"
                            f"QPushButton:hover{{background:{HIGHLIGHT};}}")
            b.clicked.connect(fn)
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

    def refresh(self):
        current_id = None
        item = self.list.currentItem()
        if item:
            current_id = item.data(Qt.ItemDataRole.UserRole)

        self.list.blockSignals(True)
        self.list.clear()
        for src in self.canvas.sources():
            kind_icon = {"image": "🖼", "browser": "🌐", "text": "T"}.get(src["kind"], "?")
            item = QListWidgetItem(f"{kind_icon}  {src['name']}")
            item.setData(Qt.ItemDataRole.UserRole, src["id"])
            item.setForeground(QColor(TEXT_PRIMARY if src["visible"] else TEXT_DIM))
            if not src["visible"]:
                item.setFont(QFont("Arial", 9, QFont.Weight.Normal, True))
            if src.get("locked"):
                item.setText(item.text() + " 🔒")
            self.list.addItem(item)

        if current_id:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == current_id:
                    self.list.setCurrentRow(i)
                    break
        self.list.blockSignals(False)

    def _on_row_changed(self, row):
        if row < 0:
            return
        sid = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        self.canvas.selected_id = sid
        for src in self.canvas.sources():
            if src["id"] == sid:
                self.canvas.selectionChanged.emit(src)
                break
        self.canvas.update()

    def _on_canvas_selection(self, src):
        self.list.blockSignals(True)
        if src is None:
            self.list.clearSelection()
        else:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == src["id"]:
                    self.list.setCurrentRow(i)
                    break
        self.list.blockSignals(False)

    def _on_rows_moved(self, *args):
        new_order = []
        ids = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        src_map = {s["id"]: s for s in self.canvas.sources()}
        self.canvas.scene_data["sources"] = [src_map[i] for i in ids if i in src_map]
        self.orderChanged.emit()
        self.canvas.sceneChanged.emit()
        self.canvas.update()

    def _add_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu{{background:{PANEL_BG};color:{TEXT_PRIMARY};border:1px solid {BORDER};}}"
                           f"QMenu::item:selected{{background:{ACCENT};}}")
        menu.addAction("🖼  Image Source").triggered.connect(lambda: self._add_source("image"))
        menu.addAction("🌐  Browser Source").triggered.connect(lambda: self._add_source("browser"))
        menu.addAction("T   Text Source").triggered.connect(lambda: self._add_source("text"))
        menu.exec(self.mapToGlobal(self.list.pos()))

    def _add_source(self, kind):
        src = make_source(kind)
        src["name"] = f"{kind.title()} {len(self.canvas.sources())+1}"
        self.canvas.scene_data["sources"].insert(0, src)
        self.canvas.selected_id = src["id"]
        self.refresh()
        self.canvas.selectionChanged.emit(src)
        self.canvas.sceneChanged.emit()
        self.canvas.update()

    def _remove_source(self):
        item = self.list.currentItem()
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        self.canvas.scene_data["sources"] = [s for s in self.canvas.sources() if s["id"] != sid]
        self.canvas.selected_id = None
        self.canvas.selectionChanged.emit(None)
        self.refresh()
        self.canvas.sceneChanged.emit()
        self.canvas.update()

    def _move_up(self):
        row = self.list.currentRow()
        if row > 0:
            srcs = self.canvas.scene_data["sources"]
            srcs[row-1], srcs[row] = srcs[row], srcs[row-1]
            self.refresh()
            self.list.setCurrentRow(row-1)
            self.canvas.sceneChanged.emit()
            self.canvas.update()

    def _move_down(self):
        row = self.list.currentRow()
        srcs = self.canvas.scene_data["sources"]
        if row < len(srcs)-1:
            srcs[row], srcs[row+1] = srcs[row+1], srcs[row]
            self.refresh()
            self.list.setCurrentRow(row+1)
            self.canvas.sceneChanged.emit()
            self.canvas.update()


class PropertiesPanel(QScrollArea):

    def __init__(self, canvas: CanvasWidget, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._source = None
        self._updating = False

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea{{background:{PANEL_BG};border:none;}}")

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background:{PANEL_BG};")
        self._main_layout = QVBoxLayout(self._inner)
        self._main_layout.setContentsMargins(8, 8, 8, 8)
        self._main_layout.setSpacing(6)
        self.setWidget(self._inner)

        self._build_empty()
        canvas.selectionChanged.connect(self.set_source)
        canvas.sourceMoved.connect(self._on_source_moved)

    def _clear(self):
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build_empty(self):
        self._clear()
        lbl = QLabel("Select a source to edit its properties.")
        lbl.setStyleSheet(f"color:{TEXT_DIM};padding:20px;")
        lbl.setWordWrap(True)
        self._main_layout.addWidget(lbl)
        self._main_layout.addStretch()

    def set_source(self, src):
        self._source = src
        if src is None:
            self._build_empty()
            return
        self._build_for_source(src)

    def _on_source_moved(self, src):
        if src is self._source or (src and self._source and src["id"] == self._source["id"]):
            self._refresh_transform_values()

    def _refresh_transform_values(self):
        src = self._source
        if src is None:
            return
        self._updating = True
        for attr, spin in getattr(self, "_transform_spins", {}).items():
            spin.setValue(src.get(attr, 0))
        self._updating = False

    def _group(self, title):
        g = QGroupBox(title)
        g.setStyleSheet(f"""
            QGroupBox {{
                color:{TEXT_DIM}; font-size:10px; font-weight:bold;
                border:1px solid {BORDER}; border-radius:4px;
                margin-top:8px; padding:4px;
            }}
            QGroupBox::title {{
                subcontrol-origin:margin; left:8px;
                padding:0 4px; color:{HIGHLIGHT};
            }}
        """)
        lay = QFormLayout()
        lay.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        lay.setSpacing(4)
        g.setLayout(lay)
        return g, lay

    def _spin(self, val, lo, hi, step=1, double=False):
        if double:
            s = QDoubleSpinBox()
            s.setDecimals(1)
            s.setSingleStep(step)
        else:
            s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setStyleSheet(f"QSpinBox,QDoubleSpinBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;}}")
        return s

    def _build_for_source(self, src):
        self._clear()
        self._transform_spins = {}

        name_edit = QLineEdit(src["name"])
        name_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:4px;font-weight:bold;")
        name_edit.textChanged.connect(lambda t: self._set(src, "name", t) or True)
        self._main_layout.addWidget(name_edit)

        vl_row = QHBoxLayout()
        vis_cb = QCheckBox("Visible")
        vis_cb.setChecked(src["visible"])
        vis_cb.stateChanged.connect(lambda s: self._set(src, "visible", bool(s)))
        lock_cb = QCheckBox("Locked")
        lock_cb.setChecked(src.get("locked", False))
        lock_cb.stateChanged.connect(lambda s: self._set(src, "locked", bool(s)))
        for cb in (vis_cb, lock_cb):
            cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
            vl_row.addWidget(cb)
        vl_row.addStretch()
        self._main_layout.addLayout(vl_row)

        tg, tl = self._group("TRANSFORM")
        for attr, label, lo, hi, step in [
            ("x", "X", -9999, 9999, 1),
            ("y", "Y", -9999, 9999, 1),
            ("w", "Width", 1, 9999, 1),
            ("h", "Height", 1, 9999, 1),
            ("rotation", "Rotation", -360, 360, 0.1),
        ]:
            double = attr == "rotation"
            s = self._spin(src.get(attr, 0), lo, hi, step, double)
            self._transform_spins[attr] = s
            def _make_cb(a, sp):
                def cb(v):
                    if not self._updating:
                        self._set(src, a, v)
                        self.canvas.update()
                return cb
            if double:
                s.valueChanged.connect(_make_cb(attr, s))
            else:
                s.valueChanged.connect(_make_cb(attr, s))
            tl.addRow(label, s)

        fit_row = QHBoxLayout()
        for lbl, fn in [("Fit Canvas", lambda: self.canvas._fit_to_canvas(src)),
                        ("Center", lambda: self.canvas._center_source(src)),
                        ("Reset", lambda: self.canvas._reset_transform(src))]:
            b = QPushButton(lbl)
            b.setStyleSheet(f"QPushButton{{background:{ACCENT};color:{TEXT_PRIMARY};border:none;border-radius:3px;padding:3px 6px;font-size:10px;}}"
                            f"QPushButton:hover{{background:{HIGHLIGHT};}}")
            b.clicked.connect(fn)
            fit_row.addWidget(b)
        tl.addRow("", fit_row)
        self._main_layout.addWidget(tg)

        cg, cl = self._group("CROP")
        for attr, label in [("crop_top","Top"),("crop_bottom","Bottom"),
                             ("crop_left","Left"),("crop_right","Right")]:
            s = self._spin(src.get(attr, 0), 0, 9999)
            s.valueChanged.connect(lambda v, a=attr: self._set(src, a, v))
            cl.addRow(label, s)
        self._main_layout.addWidget(cg)

        bg_, bl_ = self._group("BLEND")
        op = self._spin(src.get("opacity", 100), 0, 100)
        op.valueChanged.connect(lambda v: self._set(src, "opacity", v))
        bl_.addRow("Opacity %", op)

        blend_modes = ["normal","additive","multiply","screen","overlay","darken","lighten","color_dodge","color_burn","hard_light","soft_light","difference","exclusion"]
        bm = QComboBox()
        bm.addItems(blend_modes)
        bm.setCurrentText(src.get("blend_mode", "normal"))
        bm.currentTextChanged.connect(lambda t: self._set(src, "blend_mode", t))
        bm.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px;}}")
        bl_.addRow("Blend Mode", bm)

        sm = QComboBox()
        sm.addItems(["stretch","fit","fill","none"])
        sm.setCurrentText(src.get("scale_mode", "stretch"))
        sm.currentTextChanged.connect(lambda t: self._set(src, "scale_mode", t))
        sm.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px;}}")
        bl_.addRow("Scale Mode", sm)
        self._main_layout.addWidget(bg_)

        if src["kind"] == "image":
            self._build_image_props(src)
        elif src["kind"] == "browser":
            self._build_browser_props(src)
        elif src["kind"] == "text":
            self._build_text_props(src)

        self._build_audio_props(src)

        self._main_layout.addStretch()

    def _build_image_props(self, src):
        ig, il = self._group("IMAGE SOURCE")
        path_edit = QLineEdit(src.get("file", ""))
        path_edit.setReadOnly(True)
        path_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px;")
        browse = QPushButton("Browse…")
        browse.setStyleSheet(f"QPushButton{{background:{ACCENT};color:{TEXT_PRIMARY};border:none;border-radius:3px;padding:3px 8px;}}")

        def browse_file():
            f, _ = QFileDialog.getOpenFileName(self, "Select Image", "",
                "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.svg)")
            if f:
                src["file"] = f
                path_edit.setText(f)
                self.canvas.refresh_preview(src["id"])
                self.canvas.sceneChanged.emit()

        browse.clicked.connect(browse_file)
        row = QHBoxLayout()
        row.addWidget(path_edit)
        row.addWidget(browse)
        il.addRow("File", row)
        self._main_layout.addWidget(ig)

    def _build_browser_props(self, src):
        bg, bl = self._group("BROWSER SOURCE")

        url_edit = QLineEdit(src.get("url", ""))
        url_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px;")
        url_edit.textChanged.connect(lambda t: self._set(src, "url", t))
        bl.addRow("URL", url_edit)

        for attr, label, lo, hi in [("browser_width","Width",1,9999),
                                      ("browser_height","Height",1,9999),
                                      ("fps","FPS",1,120)]:
            s = self._spin(src.get(attr, 1920 if "width" in attr else (1080 if "height" in attr else 30)), lo, hi)
            s.valueChanged.connect(lambda v, a=attr: self._set(src, a, v))
            bl.addRow(label, s)

        for attr, label in [("shutdown_when_not_visible","Shutdown when hidden"),
                             ("refresh_on_activate","Refresh on activate"),
                             ("reroute_audio","Reroute audio")]:
            cb = QCheckBox(label)
            cb.setChecked(src.get(attr, False))
            cb.stateChanged.connect(lambda s, a=attr: self._set(src, a, bool(s)))
            cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
            bl.addRow("", cb)

        css_edit = QTextEdit(src.get("custom_css", ""))
        css_edit.setMaximumHeight(80)
        css_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;font-family:monospace;font-size:10px;")
        css_edit.textChanged.connect(lambda: self._set(src, "custom_css", css_edit.toPlainText()))
        bl.addRow("Custom CSS", css_edit)

        self._main_layout.addWidget(bg)

    def _build_text_props(self, src):
        tg, tl = self._group("TEXT SOURCE")

        text_edit = QTextEdit(src.get("text", ""))
        text_edit.setMaximumHeight(80)
        text_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;")
        text_edit.textChanged.connect(lambda: self._set(src, "text", text_edit.toPlainText()) or self.canvas.update())
        tl.addRow("Text", text_edit)

        font_combo = QComboBox()
        fonts = sorted(QFontDatabase.families())
        font_combo.addItems(fonts)
        font_combo.setCurrentText(src.get("font_family", "Arial"))
        font_combo.currentTextChanged.connect(lambda t: (self._set(src, "font_family", t), self.canvas.update()))
        font_combo.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:2px;max-height:22px;}}")
        tl.addRow("Font", font_combo)

        size_s = self._spin(src.get("font_size", 72), 4, 999)
        size_s.valueChanged.connect(lambda v: (self._set(src, "font_size", v), self.canvas.update()))
        tl.addRow("Size (pt)", size_s)

        style_row = QHBoxLayout()
        for attr, label in [("font_bold","B"),("font_italic","I"),("font_underline","U")]:
            cb = QCheckBox(label)
            cb.setChecked(src.get(attr, False))
            cb.stateChanged.connect(lambda s, a=attr: (self._set(src, a, bool(s)), self.canvas.update()))
            cb.setStyleSheet(f"color:{TEXT_PRIMARY};font-weight:bold;")
            style_row.addWidget(cb)
        style_row.addStretch()
        tl.addRow("Style", style_row)

        def pick_color(attr):
            c = QColorDialog.getColor(QColor(src.get(attr, "#ffffff")), self, "Pick Color",
                                       QColorDialog.ColorDialogOption.ShowAlphaChannel)
            if c.isValid():
                self._set(src, attr, c.name(QColor.NameFormat.HexArgb))
                self.canvas.update()
                self._build_for_source(src)

        color_btn = QPushButton()
        color_btn.setFixedSize(28, 20)
        color_btn.setStyleSheet(f"background:{src.get('color','#ffffff')};border:1px solid {BORDER};border-radius:3px;")
        color_btn.clicked.connect(lambda: pick_color("color"))
        tl.addRow("Color", color_btn)

        ah_combo = QComboBox()
        ah_combo.addItems(["left","center","right"])
        ah_combo.setCurrentText(src.get("align","left"))
        ah_combo.currentTextChanged.connect(lambda t: (self._set(src,"align",t), self.canvas.update()))
        ah_combo.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;}}")
        tl.addRow("H Align", ah_combo)

        av_combo = QComboBox()
        av_combo.addItems(["top","center","bottom"])
        av_combo.setCurrentText(src.get("valign","top"))
        av_combo.currentTextChanged.connect(lambda t: (self._set(src,"valign",t), self.canvas.update()))
        av_combo.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;}}")
        tl.addRow("V Align", av_combo)

        ww_cb = QCheckBox("Word Wrap")
        ww_cb.setChecked(src.get("word_wrap", True))
        ww_cb.stateChanged.connect(lambda s: (self._set(src,"word_wrap",bool(s)), self.canvas.update()))
        ww_cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
        tl.addRow("", ww_cb)

        out_cb = QCheckBox("Outline")
        out_cb.setChecked(src.get("outline", False))
        out_cb.stateChanged.connect(lambda s: (self._set(src,"outline",bool(s)), self.canvas.update()))
        out_cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
        tl.addRow("", out_cb)

        out_size = self._spin(src.get("outline_size",3), 1, 50)
        out_size.valueChanged.connect(lambda v: (self._set(src,"outline_size",v), self.canvas.update()))
        tl.addRow("Outline Size", out_size)

        out_color_btn = QPushButton()
        out_color_btn.setFixedSize(28, 20)
        out_color_btn.setStyleSheet(f"background:{src.get('outline_color','#000000')};border:1px solid {BORDER};border-radius:3px;")
        out_color_btn.clicked.connect(lambda: pick_color("outline_color"))
        tl.addRow("Outline Color", out_color_btn)

        sh_cb = QCheckBox("Drop Shadow")
        sh_cb.setChecked(src.get("shadow", False))
        sh_cb.stateChanged.connect(lambda s: (self._set(src,"shadow",bool(s)), self.canvas.update()))
        sh_cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
        tl.addRow("", sh_cb)

        self._main_layout.addWidget(tg)

    def _build_audio_props(self, src):
        ag, al = self._group("AUDIO")

        audio_cb = QCheckBox("Audio Enabled")
        audio_cb.setChecked(src.get("audio_enabled", False))
        audio_cb.stateChanged.connect(lambda s: self._set(src, "audio_enabled", bool(s)))
        audio_cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
        al.addRow("", audio_cb)

        vol = self._spin(src.get("volume", 100), 0, 100)
        vol.valueChanged.connect(lambda v: self._set(src, "volume", v))
        al.addRow("Volume %", vol)

        mute_cb = QCheckBox("Muted")
        mute_cb.setChecked(src.get("muted", False))
        mute_cb.stateChanged.connect(lambda s: self._set(src, "muted", bool(s)))
        mute_cb.setStyleSheet(f"color:{TEXT_PRIMARY};")
        al.addRow("", mute_cb)

        monitor = QComboBox()
        monitor.addItems(["none","monitor_only","monitor_and_output"])
        monitor.setCurrentText(src.get("audio_monitor","none"))
        monitor.currentTextChanged.connect(lambda t: self._set(src, "audio_monitor", t))
        monitor.setStyleSheet(f"QComboBox{{background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;}}")
        al.addRow("Monitor", monitor)
        self._main_layout.addWidget(ag)

        comp = src.get("compressor", {})
        cg, cl = self._group("COMPRESSOR FILTER")

        comp_en = QCheckBox("Enable Compressor")
        comp_en.setChecked(comp.get("enabled", False))
        comp_en.stateChanged.connect(lambda s: self._set_nested(src,"compressor","enabled",bool(s)))
        comp_en.setStyleSheet(f"color:{TEXT_PRIMARY};")
        cl.addRow("", comp_en)

        def add_comp_spin(attr, label, lo, hi, step, default):
            s = self._spin(comp.get(attr, default), lo, hi, step, double=isinstance(default, float))
            s.valueChanged.connect(lambda v, a=attr: self._set_nested(src,"compressor",a,v))
            cl.addRow(label, s)

        add_comp_spin("ratio",        "Ratio",         1,   100,  0.1, 10.0)
        add_comp_spin("threshold",    "Threshold (dB)",-60, 0,    0.1, -18.0)
        add_comp_spin("attack",       "Attack (ms)",   1,   500,  0.1, 6.0)
        add_comp_spin("release",      "Release (ms)",  1,  2000,  0.1, 60.0)
        add_comp_spin("output_gain",  "Output Gain(dB)",-30, 30,  0.1, 0.0)

        self._main_layout.addWidget(cg)

    def _set(self, src, key, value):
        src[key] = value
        self.canvas.sceneChanged.emit()

    def _set_nested(self, src, group, key, value):
        src[group][key] = value
        self.canvas.sceneChanged.emit()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 860)
        self._current_file = None
        self._modified = False

        self._apply_stylesheet()
        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._connect_signals()
        self._new_scene()

    def _apply_stylesheet(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{DARK_BG}; color:{TEXT_PRIMARY}; }}
            QMenuBar {{ background:{PANEL_BG}; color:{TEXT_PRIMARY}; border-bottom:1px solid {BORDER}; }}
            QMenuBar::item:selected {{ background:{ACCENT}; }}
            QMenu {{ background:{PANEL_BG}; color:{TEXT_PRIMARY}; border:1px solid {BORDER}; }}
            QMenu::item:selected {{ background:{ACCENT}; }}
            QToolBar {{ background:{PANEL_BG}; border-bottom:1px solid {BORDER}; spacing:4px; padding:2px; }}
            QStatusBar {{ background:{PANEL_BG}; color:{TEXT_DIM}; border-top:1px solid {BORDER}; }}
            QSplitter::handle {{ background:{BORDER}; width:2px; height:2px; }}
            QScrollBar:vertical {{ background:{SURFACE}; width:8px; border-radius:4px; }}
            QScrollBar::handle:vertical {{ background:{ACCENT}; border-radius:4px; }}
            QLabel {{ color:{TEXT_PRIMARY}; }}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        self.source_list_panel = QWidget()
        self.source_list_panel.setMinimumWidth(200)
        self.source_list_panel.setMaximumWidth(280)
        slp_lay = QVBoxLayout(self.source_list_panel)
        slp_lay.setContentsMargins(0, 0, 0, 0)

        self.canvas = CanvasWidget()
        self.source_list = SourceListWidget(self.canvas)
        slp_lay.addWidget(self.source_list)

        scene_frame = QFrame()
        scene_frame.setStyleSheet(f"background:{PANEL_BG};border-bottom:1px solid {BORDER};")
        sf_lay = QVBoxLayout(scene_frame)
        sf_lay.setContentsMargins(4, 4, 4, 4)
        slbl = QLabel("SCENES")
        slbl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;font-weight:bold;letter-spacing:1px;")
        self.scene_name_edit = QLineEdit()
        self.scene_name_edit.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;padding:4px;")
        sf_lay.addWidget(slbl)
        sf_lay.addWidget(self.scene_name_edit)
        slp_lay.insertWidget(0, scene_frame)

        canvas_container = QWidget()
        cc_lay = QVBoxLayout(canvas_container)
        cc_lay.setContentsMargins(4, 4, 4, 4)
        cc_lay.addWidget(self.canvas)

        self.canvas_status = QLabel("Canvas: 1920 × 1080  |  No selection")
        self.canvas_status.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;padding:2px;")
        cc_lay.addWidget(self.canvas_status)

        self.props = PropertiesPanel(self.canvas)
        self.props.setMinimumWidth(240)
        self.props.setMaximumWidth(340)

        splitter.addWidget(self.source_list_panel)
        splitter.addWidget(canvas_container)
        splitter.addWidget(self.props)
        splitter.setSizes([240, 900, 280])

        main_lay.addWidget(splitter)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

    def _build_menus(self):
        mb = self.menuBar()

        file_m = mb.addMenu("&File")
        acts = [
            ("New Scene",       "Ctrl+N", self._new_scene),
            ("Open…",           "Ctrl+O", self._open_file),
            ("Save",            "Ctrl+S", self._save_file),
            ("Save As…",        "Ctrl+Shift+S", self._save_as),
            (None, None, None),
            ("Export JSON",     None, self._export_json),
            ("Export XML",      None, self._export_xml),
            (None, None, None),
            ("Launch Renderer", "Ctrl+R", self._launch_renderer),
            (None, None, None),
            ("Quit",            "Ctrl+Q", self.close),
        ]
        for label, shortcut, fn in acts:
            if label is None:
                file_m.addSeparator()
            else:
                a = QAction(label, self)
                if shortcut:
                    a.setShortcut(QKeySequence(shortcut))
                a.triggered.connect(fn)
                file_m.addAction(a)

        edit_m = mb.addMenu("&Edit")
        for label, shortcut, fn in [
            ("Undo",    "Ctrl+Z",       self._undo),
            ("Redo",    "Ctrl+Shift+Z", self._redo),
            (None,None,None),
            ("Select All", "Ctrl+A",   self._select_all),
            ("Delete Source", "Delete", self._delete_source),
            (None,None,None),
            ("Canvas Settings…", None, self._canvas_settings),
        ]:
            if label is None:
                edit_m.addSeparator()
            else:
                a = QAction(label, self)
                if shortcut: a.setShortcut(QKeySequence(shortcut))
                a.triggered.connect(fn)
                edit_m.addAction(a)

        view_m = mb.addMenu("&View")
        for label, fn in [
            ("Zoom In",   lambda: None),
            ("Zoom Out",  lambda: None),
            ("Zoom Fit",  lambda: None),
        ]:
            a = QAction(label, self)
            a.triggered.connect(fn)
            view_m.addAction(a)

        src_m = mb.addMenu("&Sources")
        for label, kind in [("Add Image Source", "image"),
                              ("Add Browser Source", "browser"),
                              ("Add Text Source", "text")]:
            a = QAction(label, self)
            a.triggered.connect(lambda checked, k=kind: self.source_list._add_source(k))
            src_m.addAction(a)

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        for label, tip, fn in [
            ("New",   "New Scene (Ctrl+N)",   self._new_scene),
            ("Open",  "Open Scene (Ctrl+O)",  self._open_file),
            ("Save",  "Save Scene (Ctrl+S)",  self._save_file),
            ("|", "", None),
            ("▶ Run", "Launch Renderer",      self._launch_renderer),
        ]:
            if label == "|":
                tb.addSeparator()
            else:
                b = QPushButton(label)
                b.setToolTip(tip)
                b.setStyleSheet(
                    f"QPushButton{{background:{ACCENT};color:{TEXT_PRIMARY};border:none;"
                    f"border-radius:3px;padding:4px 10px;font-size:11px;}}"
                    f"QPushButton:hover{{background:{HIGHLIGHT};}}")
                if fn: b.clicked.connect(fn)
                tb.addWidget(b)

        tb.addSeparator()
        self._canvas_size_lbl = QLabel(f"  Canvas: {CANVAS_W}×{CANVAS_H}  ")
        self._canvas_size_lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        tb.addWidget(self._canvas_size_lbl)

    def _connect_signals(self):
        self.canvas.sceneChanged.connect(self._on_scene_changed)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.sourceMoved.connect(self._on_source_moved)
        self.scene_name_edit.textChanged.connect(self._on_scene_name_changed)

    def _on_scene_changed(self):
        self._modified = True
        self._update_title()
        self.source_list.refresh()

    def _on_scene_name_changed(self, name):
        self.canvas.scene_data["name"] = name
        self._on_scene_changed()

    def _on_selection_changed(self, src):
        if src:
            self.canvas_status.setText(
                f"Canvas: {CANVAS_W}×{CANVAS_H}  |  "
                f"Selected: {src['name']}  "
                f"({src['x']},{src['y']})  {src['w']}×{src['h']}  "
                f"rot:{src['rotation']}°"
            )
        else:
            self.canvas_status.setText(f"Canvas: {CANVAS_W}×{CANVAS_H}  |  No selection")

    def _on_source_moved(self, src):
        self._on_selection_changed(src)

    def _update_title(self):
        name = self._current_file or "Untitled"
        mod = " •" if self._modified else ""
        self.setWindowTitle(f"{APP_NAME} — {os.path.basename(name)}{mod}")

    def _new_scene(self):
        if self._modified and not self._confirm_discard():
            return
        self.canvas.set_scene(make_scene("New Scene"))
        self.scene_name_edit.setText("New Scene")
        self._current_file = None
        self._modified = False
        self._update_title()
        self.source_list.refresh()

    def _open_file(self):
        if self._modified and not self._confirm_discard():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Open Scene", "",
                                            f"wxHDS-Rndr Scene (*{FILE_EXT});;JSON (*.json)")
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            self.canvas.set_scene(data)
            self.scene_name_edit.setText(data.get("name",""))
            self._current_file = f
            self._modified = False
            self._update_title()
            self.source_list.refresh()
            self.status.showMessage(f"Opened: {f}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file:\n{e}")

    def _save_file(self):
        if self._current_file:
            self._write_json(self._current_file)
        else:
            self._save_as()

    def _save_as(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save Scene As", "",
                                            f"wxHDS-Rndr Scene (*{FILE_EXT});;JSON (*.json)")
        if f:
            if not f.endswith(FILE_EXT) and not f.endswith(".json"):
                f += FILE_EXT
            self._write_json(f)
            self._current_file = f
            self._update_title()

    def _write_json(self, path):
        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(self.canvas.scene_data, fp, indent=2, ensure_ascii=False)
            self._modified = False
            self._update_title()
            self.status.showMessage(f"Saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def _export_json(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export JSON", "",
                                            "JSON files (*.json)")
        if f:
            self._write_json(f)

    def _export_xml(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export XML", "",
                                            "XML files (*.xml)")
        if not f:
            return
        try:
            from lxml import etree
            root = etree.Element("wxhds_scene", version=SCENE_VERSION)
            root.set("name", self.canvas.scene_data.get("name",""))
            root.set("canvas_w", str(self.canvas.scene_data.get("canvas_w", CANVAS_W)))
            root.set("canvas_h", str(self.canvas.scene_data.get("canvas_h", CANVAS_H)))
            sources_el = etree.SubElement(root, "sources")
            for src in self.canvas.sources():
                se = etree.SubElement(sources_el, "source")
                for k, v in src.items():
                    if isinstance(v, dict):
                        sub = etree.SubElement(se, k)
                        for sk, sv in v.items():
                            sub.set(str(sk), str(sv))
                    elif isinstance(v, list):
                        pass
                    else:
                        se.set(str(k), str(v))
            out_el = etree.SubElement(root, "output")
            for k, v in self.canvas.scene_data.get("output",{}).items():
                out_el.set(str(k), str(v))
            tree = etree.ElementTree(root)
            etree.indent(tree, space="  ")
            with open(f, "wb") as fp:
                tree.write(fp, xml_declaration=True, encoding="UTF-8", pretty_print=True)
            self.status.showMessage(f"Exported XML: {f}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"XML export failed:\n{e}")

    def _launch_renderer(self):
        scene_file = self._current_file
        if not scene_file or self._modified:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=FILE_EXT, delete=False, mode="w", encoding="utf-8")
            json.dump(self.canvas.scene_data, tmp, indent=2)
            tmp.close()
            scene_file = tmp.name

        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rndr.py")
        if not os.path.isfile(script):
            QMessageBox.warning(self, "Renderer Not Found",
                                f"rndr.py not found at:\n{script}\n\nPlease ensure rndr.py is in the same directory.")
            return
        try:
            subprocess.Popen([sys.executable, script, scene_file])
            self.status.showMessage(f"Renderer launched: {scene_file}")
        except Exception as e:
            QMessageBox.critical(self, "Launch Error", str(e))

    def _confirm_discard(self):
        r = QMessageBox.question(self, "Unsaved Changes",
                                  "You have unsaved changes. Discard them?",
                                  QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    def _undo(self): pass  # TODO: undo stack
    def _redo(self): pass

    def _select_all(self): pass

    def _delete_source(self):
        self.canvas._delete_selected()

    def _canvas_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Canvas Settings")
        dlg.setStyleSheet(f"background:{PANEL_BG};color:{TEXT_PRIMARY};")
        lay = QFormLayout(dlg)

        cw_s = QSpinBox()
        cw_s.setRange(320, 7680)
        cw_s.setValue(self.canvas.scene_data.get("canvas_w", CANVAS_W))
        ch_s = QSpinBox()
        ch_s.setRange(240, 4320)
        ch_s.setValue(self.canvas.scene_data.get("canvas_h", CANVAS_H))

        for s in (cw_s, ch_s):
            s.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;")

        fps_s = QSpinBox()
        fps_s.setRange(1, 240)
        fps_s.setValue(self.canvas.scene_data.get("output",{}).get("fps", 60))
        fps_s.setStyleSheet(f"background:{SURFACE};color:{TEXT_PRIMARY};border:1px solid {BORDER};border-radius:3px;")

        lay.addRow("Canvas Width:", cw_s)
        lay.addRow("Canvas Height:", ch_s)
        lay.addRow("Output FPS:", fps_s)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        bb.setStyleSheet(f"QPushButton{{background:{ACCENT};color:{TEXT_PRIMARY};border:none;border-radius:3px;padding:4px 12px;}}")
        lay.addRow(bb)

        if dlg.exec():
            self.canvas.scene_data["canvas_w"] = cw_s.value()
            self.canvas.scene_data["canvas_h"] = ch_s.value()
            self.canvas.scene_data.setdefault("output",{})["fps"] = fps_s.value()
            self._canvas_size_lbl.setText(f"  Canvas: {cw_s.value()}×{ch_s.value()}  ")
            self.canvas._recalc_scale()
            self.canvas.update()
            self._on_scene_changed()

    def closeEvent(self, e):
        if self._modified and not self._confirm_discard():
            e.ignore()
        else:
            e.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("wxHDS-Rndr Editor")
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(DARK_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_BG))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Button, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT2))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)

    win = MainWindow()
    win.show()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        try:
            with open(sys.argv[1], "r", encoding="utf-8") as fp:
                data = json.load(fp)
            win.canvas.set_scene(data)
            win.scene_name_edit.setText(data.get("name",""))
            win._current_file = sys.argv[1]
            win._modified = False
            win._update_title()
            win.source_list.refresh()
        except Exception as e:
            print(f"Warning: could not load {sys.argv[1]}: {e}")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()