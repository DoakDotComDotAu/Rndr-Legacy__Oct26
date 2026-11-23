#!/usr/bin/env python3
import sys
import os
import json
import math
import time
import threading

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--enable-transparent-visuals "
    "--disable-gpu-compositing "
    "--autoplay-policy=no-user-gesture-required "
    "--disable-features=AutoplayIgnoreWebAudio "
    "--enable-webgl "
    "--ignore-gpu-blocklist"
)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QStatusBar, QMessageBox, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QRect, QRectF, QTimer, QUrl, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QPixmap, QImage, QPen, QBrush, QColor, QFont,
    QPalette, QAction, QLinearGradient, QKeySequence
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineSettings, QWebEnginePage, QWebEngineProfile
)

try:
    import sounddevice as sd
    HAVE_SD = True
except ImportError:
    HAVE_SD = False

try:
    import pyaudio
    HAVE_PYAUDIO = True
except ImportError:
    HAVE_PYAUDIO = False

CANVAS_W, CANVAS_H = 1920, 1080
RENDER_FPS         = 60
AUDIO_SAMPLE_RATE  = 48000
AUDIO_CHANNELS     = 2
SCENE_VERSION      = "1.0"

DARK_BG   = "#0d0d1a"
PANEL_BG  = "#16213e"
ACCENT    = "#0f3460"
HIGHLIGHT = "#e94560"
TEXT_PRI  = "#e0e0e0"
TEXT_DIM  = "#888899"
BORDER    = "#2a2a4a"


class CompressorFilter:

    def __init__(self):
        self.enabled      = False
        self.ratio        = 10.0
        self.threshold_db = -18.0
        self.attack_ms    = 6.0
        self.release_ms   = 60.0
        self.output_gain  = 0.0
        self._envelope    = 0.0

    def update(self, d: dict):
        self.enabled      = bool(d.get("enabled", False))
        self.ratio        = float(d.get("ratio", 10.0))
        self.threshold_db = float(d.get("threshold", -18.0))
        self.attack_ms    = float(d.get("attack", 6.0))
        self.release_ms   = float(d.get("release", 60.0))
        self.output_gain  = float(d.get("output_gain", 0.0))

    @staticmethod
    def _db(x):  return 10.0 ** (x / 20.0)
    @staticmethod
    def _ldb(x): return -120.0 if x <= 0 else 20.0 * math.log10(x)

    def process(self, frames: list) -> list:
        if not self.enabled:
            return frames
        sr = AUDIO_SAMPLE_RATE
        atk  = math.exp(-1.0 / (sr * self.attack_ms  / 1000.0))
        rel  = math.exp(-1.0 / (sr * self.release_ms / 1000.0))
        thr  = self._db(self.threshold_db)
        mkup = self._db(self.output_gain)
        env  = self._envelope
        out  = []
        for frame in frames:
            peak = max(abs(s) for s in frame)
            if peak > env:
                env = atk * env + (1 - atk) * peak
            else:
                env = rel * env + (1 - rel) * peak
            if env > thr > 0:
                gain_db = (self._ldb(thr) +
                           (self._ldb(env) - self._ldb(thr)) / self.ratio
                           - self._ldb(env))
                gain = self._db(gain_db)
            else:
                gain = 1.0
            out.append([s * gain * mkup for s in frame])
        self._envelope = env
        return out


class AudioMixer:

    def __init__(self):
        self._sources     = {}       # id -> cfg dict
        self._compressors = {}       # id -> CompressorFilter
        self._lock        = threading.Lock()

    def configure(self, sid: str, cfg: dict):
        with self._lock:
            self._sources[sid] = cfg
            if sid not in self._compressors:
                self._compressors[sid] = CompressorFilter()
            self._compressors[sid].update(cfg.get("compressor", {}))

    def remove(self, sid: str):
        with self._lock:
            self._sources.pop(sid, None)
            self._compressors.pop(sid, None)

    def clear(self):
        with self._lock:
            self._sources.clear()
            self._compressors.clear()

    def process(self, sid: str, frames: list) -> list:
        with self._lock:
            cfg  = self._sources.get(sid, {})
            comp = self._compressors.get(sid)
        if cfg.get("muted", False):
            return [[0.0] * len(f) for f in frames]
        vol = cfg.get("volume", 100) / 100.0
        frames = [[s * vol for s in f] for f in frames]
        if comp:
            frames = comp.process(frames)
        return frames

    def start(self):
        print(f"[Audio] Mixer ready  (sounddevice={HAVE_SD}  pyaudio={HAVE_PYAUDIO})")

    def stop(self):
        print("[Audio] Mixer stopped")

_POST_LOAD_JS = """
(function(){
    // Transparent background
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';

    // Force autoplay on all existing media
    document.querySelectorAll('video,audio').forEach(function(m){
        m.muted = false;
        m.play().catch(function(){});
    });

    // Watch for dynamically added media
    var obs = new MutationObserver(function(muts){
        muts.forEach(function(mut){
            mut.addedNodes.forEach(function(n){
                if(n.tagName==='VIDEO'||n.tagName==='AUDIO'){
                    n.muted=false; n.play().catch(function(){});
                }
            });
        });
    });
    obs.observe(document.body,{childList:true,subtree:true});
})();
"""

class BrowserSourceView(QWebEngineView):

    def __init__(self, src: dict, parent=None):
        super().__init__(parent)
        self._src          = src
        self._last_frame   = None   # last successfully grabbed pixmap
        self._grab_pending = False  # dont stack grab requests

        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,               True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,        True)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled,                    True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages,                  True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled,             True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,     False)

        self.page().setBackgroundColor(QColor(0, 0, 0, 0))

        w = src.get("browser_width",  CANVAS_W)
        h = src.get("browser_height", CANVAS_H)
        self.resize(w, h)
        self.setMinimumSize(1, 1)

        self.loadFinished.connect(self._on_load_finished)
        
        self._grab_timer = QTimer(self)
        self._grab_timer.timeout.connect(self._request_grab)
        self._grab_timer.start(33)

        url = src.get("url", "")
        if url and url != "about:blank":
            self.load(QUrl(url))
        else:
            self.setHtml(
                "<html><head><style>"
                "html,body{margin:0;background:transparent;color:#aaa;"
                "font-family:sans-serif;display:flex;align-items:center;"
                "justify-content:center;height:100vh;}"
                "</style></head><body>"
                "<div style='text-align:center'>No URL set</div>"
                "</body></html>"
            )

    def _on_load_finished(self, ok: bool):
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.page().runJavaScript(_POST_LOAD_JS)
        css = self._src.get("custom_css", "")
        if css:
            self._inject_css(css)

    def _inject_css(self, css: str):
        escaped = css.replace("\\", "\\\\").replace("`", "\\`")
        self.page().runJavaScript(f"""
            (function(){{
                var el = document.getElementById('__wxhds_css');
                if(!el){{el=document.createElement('style');
                         el.id='__wxhds_css';
                         document.head.appendChild(el);}}
                el.textContent=`{escaped}`;
            }})();
        """)

    def _request_grab(self):
        if self._grab_pending:
            return
        self._grab_pending = True
        pm = QPixmap(self.grab())
        if not pm.isNull():
            self._last_frame = pm
        self._grab_pending = False

    def _on_grab_ready(self, image: QImage):
        self._grab_pending = False
        if not image.isNull():
            self._last_frame = QPixmap.fromImage(image)

    def get_frame(self) -> QPixmap:
        return self._last_frame

    def update_url(self, url: str):
        if url != self._src.get("url", ""):
            self._src["url"] = url
            self.page().setBackgroundColor(QColor(0, 0, 0, 0))
            self.load(QUrl(url))


class Compositor:

    def __init__(self, w=CANVAS_W, h=CANVAS_H):
        self.w = w
        self.h = h
        self._cache: dict[str, QPixmap] = {}   # img cache

    def resize(self, w, h):
        self.w, self.h = w, h

    def invalidate(self, sid=None):
        if sid is None:
            self._cache.clear()
        else:
            self._cache.pop(sid, None)

    def compose(self, scene: dict, browser_views: dict) -> QImage:
        """Return one composited ARGB32 frame."""
        frame = QImage(self.w, self.h, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(QColor(0, 0, 0, 0))

        p = QPainter(frame)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        for src in reversed(scene.get("sources", [])):
            if not src.get("visible", True):
                continue
            try:
                self._draw(p, src, browser_views)
            except Exception as ex:
                print(f"[Compositor] {src.get('name','?')}: {ex}")

        p.end()
        return frame

    def _draw(self, p: QPainter, src: dict, bviews: dict):
        sid  = src["id"]
        kind = src["kind"]
        x, y = int(src["x"]), int(src["y"])
        w, h = int(src["w"]), int(src["h"])
        rot  = float(src.get("rotation", 0.0))
        op   = src.get("opacity", 100) / 100.0
        bm   = src.get("blend_mode", "normal")

        pm = None
        if kind == "image":
            pm = self._image_pixmap(src)
        elif kind == "browser":
            bv = bviews.get(sid)
            if bv:
                pm = bv.get_frame()
            if pm is None:
                pm = self._browser_placeholder(w, h)
        elif kind == "text":
            pm = self._render_text(src)

        if pm is None or pm.isNull():
            return

        ct = int(src.get("crop_top",    0))
        cb = int(src.get("crop_bottom", 0))
        cl = int(src.get("crop_left",   0))
        cr = int(src.get("crop_right",  0))
        if ct or cb or cl or cr:
            pm = self._crop(pm, ct, cb, cl, cr)
            if pm is None or pm.isNull():
                return

        pm = self._scale(pm, w, h, src.get("scale_mode", "stretch"))
        if pm.isNull():
            return

        p.save()
        p.setCompositionMode(self._blend(bm))
        p.setOpacity(op)

        cx = x + w / 2.0
        cy = y + h / 2.0
        if rot:
            p.translate(cx, cy)
            p.rotate(rot)
            p.translate(-cx, -cy)

        p.drawPixmap(QRect(x, y, w, h), pm)
        p.restore()

    def _image_pixmap(self, src: dict):
        sid  = src["id"]
        path = src.get("file", "")
        if not path or not os.path.isfile(path):
            return None
        if sid not in self._cache:
            pm = QPixmap(path)
            if not pm.isNull():
                self._cache[sid] = pm
        return self._cache.get(sid)

    def _browser_placeholder(self, w: int, h: int) -> QPixmap:
        pm = QPixmap(max(1, w), max(1, h))
        pm.fill(QColor(20, 40, 20, 180))
        p = QPainter(pm)
        p.setPen(QColor("#50c050"))
        p.setFont(QFont("Arial", max(10, h // 30)))
        p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                   "🌐 Loading…")
        p.end()
        return pm

    def _render_text(self, src: dict) -> QPixmap:
        w = max(1, int(src["w"]))
        h = max(1, int(src["h"]))
        pm = QPixmap(w, h)
        pm.fill(QColor(0, 0, 0, 0))

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        text   = src.get("text", "")
        color  = QColor(src.get("color", "#ffffff"))
        family = src.get("font_family", "Arial")
        size   = max(4, int(src.get("font_size", 72)))

        font = QFont(family, size)
        font.setBold(src.get("font_bold", False))
        font.setItalic(src.get("font_italic", False))
        font.setUnderline(src.get("font_underline", False))

        ah = {"left": Qt.AlignmentFlag.AlignLeft,
              "center": Qt.AlignmentFlag.AlignHCenter,
              "right":  Qt.AlignmentFlag.AlignRight
             }.get(src.get("align","left"), Qt.AlignmentFlag.AlignLeft)
        av = {"top":    Qt.AlignmentFlag.AlignTop,
              "center": Qt.AlignmentFlag.AlignVCenter,
              "bottom": Qt.AlignmentFlag.AlignBottom
             }.get(src.get("valign","top"), Qt.AlignmentFlag.AlignTop)

        flags = int(ah | av)
        if src.get("word_wrap", True):
            flags |= int(Qt.TextFlag.TextWordWrap)

        rect = QRect(0, 0, w, h)

        if src.get("shadow", False):
            sc = QColor(src.get("shadow_color", "#000000"))
            sc.setAlphaF(0.7)
            p.setFont(font)
            p.setPen(QPen(sc))
            p.drawText(rect.translated(3, 3), flags, text)

        if src.get("outline", False):
            oc  = QColor(src.get("outline_color", "#000000"))
            osz = int(src.get("outline_size", 3))
            p.setFont(font)
            p.setPen(QPen(oc, osz))
            for dx, dy in [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]:
                p.drawText(rect.translated(dx, dy), flags, text)

        p.setFont(font)
        if src.get("gradient", False):
            gc  = QColor(src.get("gradient_color", "#ff0000"))
            rad = math.radians(src.get("gradient_direction", 90))
            gx  = QLinearGradient(0, 0, w * math.cos(rad), h * math.sin(rad))
            gx.setColorAt(0.0, color)
            gx.setColorAt(1.0, gc)
            p.setPen(QPen(QBrush(gx), 1))
        else:
            p.setPen(QPen(color))

        p.drawText(rect, flags, text)
        p.end()
        return pm

    def _crop(self, pm: QPixmap, ct, cb, cl, cr):
        sw, sh = pm.width(), pm.height()
        x = max(0, cl)
        y = max(0, ct)
        w = sw - cl - cr
        h = sh - ct - cb
        if w <= 0 or h <= 0:
            return None
        return pm.copy(x, y, w, h)

    def _scale(self, pm: QPixmap, tw: int, th: int, mode: str) -> QPixmap:
        ar_ignore   = Qt.AspectRatioMode.IgnoreAspectRatio
        ar_keep     = Qt.AspectRatioMode.KeepAspectRatio
        ar_expand   = Qt.AspectRatioMode.KeepAspectRatioByExpanding
        smooth      = Qt.TransformationMode.SmoothTransformation
        if mode == "fit":
            return pm.scaled(tw, th, ar_keep,   smooth)
        if mode == "fill":
            return pm.scaled(tw, th, ar_expand, smooth)
        if mode == "none":
            return pm
        return pm.scaled(tw, th, ar_ignore, smooth) 

    @staticmethod
    def _blend(mode: str):
        M = QPainter.CompositionMode
        return {
            "normal":      M.CompositionMode_SourceOver,
            "additive":    M.CompositionMode_Plus,
            "multiply":    M.CompositionMode_Multiply,
            "screen":      M.CompositionMode_Screen,
            "overlay":     M.CompositionMode_Overlay,
            "darken":      M.CompositionMode_Darken,
            "lighten":     M.CompositionMode_Lighten,
            "color_dodge": M.CompositionMode_ColorDodge,
            "color_burn":  M.CompositionMode_ColorBurn,
            "hard_light":  M.CompositionMode_HardLight,
            "soft_light":  M.CompositionMode_SoftLight,
            "difference":  M.CompositionMode_Difference,
            "exclusion":   M.CompositionMode_Exclusion,
        }.get(mode, M.CompositionMode_SourceOver)


class RenderOutputWidget(QWidget):

    def __init__(self, scene: dict, parent=None):
        super().__init__(parent)
        self.scene        = scene
        self._cw          = scene.get("canvas_w", CANVAS_W)
        self._ch          = scene.get("canvas_h", CANVAS_H)
        self._fps         = scene.get("output", {}).get("fps", RENDER_FPS)
        self._frame       = None
        self._compositor  = Compositor(self._cw, self._ch)
        self._bviews: dict[str, BrowserSourceView] = {}
        self._mixer       = AudioMixer()
        self._scene_file  = None
        self._scene_mtime = 0.0

        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setStyleSheet("background: transparent;")

        self._bcontainer = QWidget(self)
        self._bcontainer.setGeometry(-9999, -9999, 1, 1)
        self._bcontainer.show()

        self._init_sources()
        self._mixer.start()

        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._tick)
        self._render_timer.start(1000 // max(1, self._fps))

        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._check_reload)
        self._watch_timer.start(1000)

    def set_scene_file(self, path: str):
        self._scene_file = path
        if path and os.path.isfile(path):
            self._scene_mtime = os.path.getmtime(path)

    def _init_sources(self):
        for src in self.scene.get("sources", []):
            self._add_source(src)

    def _add_source(self, src: dict):
        sid  = src["id"]
        kind = src.get("kind")
        if kind == "browser":
            bv = BrowserSourceView(src, self._bcontainer)
            bv.resize(src.get("browser_width",  self._cw),
                      src.get("browser_height", self._ch))
            bv.show()
            self._bviews[sid] = bv
        if src.get("audio_enabled", False):
            self._mixer.configure(sid, src)

    def reload_scene(self, new_scene: dict):
        new_ids = {s["id"] for s in new_scene.get("sources", [])}
        for sid in list(self._bviews):
            if sid not in new_ids:
                bv = self._bviews.pop(sid)
                bv.close(); bv.deleteLater()
        self._mixer.clear()
        self.scene = new_scene
        self._cw   = new_scene.get("canvas_w", CANVAS_W)
        self._ch   = new_scene.get("canvas_h", CANVAS_H)
        self._compositor.resize(self._cw, self._ch)
        self._compositor.invalidate()
        for src in new_scene.get("sources", []):
            sid  = src["id"]
            kind = src.get("kind")
            if kind == "browser":
                bv = self._bviews.get(sid)
                if bv:
                    bv.update_url(src.get("url", ""))
                else:
                    bv = BrowserSourceView(src, self._bcontainer)
                    bv.resize(src.get("browser_width",  self._cw),
                              src.get("browser_height", self._ch))
                    bv.show()
                    self._bviews[sid] = bv
            if src.get("audio_enabled", False):
                self._mixer.configure(sid, src)
        self._render_timer.setInterval(
            1000 // max(1, new_scene.get("output", {}).get("fps", RENDER_FPS)))
        self.update()

    def _check_reload(self):
        if not self._scene_file or not os.path.isfile(self._scene_file):
            return
        try:
            mt = os.path.getmtime(self._scene_file)
            if mt > self._scene_mtime:
                self._scene_mtime = mt
                with open(self._scene_file, "r", encoding="utf-8") as fp:
                    ns = json.load(fp)
                print(f"[Renderer] Hot-reloaded: {self._scene_file}")
                self.reload_scene(ns)
        except Exception as ex:
            print(f"[Renderer] Reload error: {ex}")

    def _tick(self):
        self._frame = self._compositor.compose(self.scene, self._bviews)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
    
        p.fillRect(self.rect(), QColor(0, 0, 0, 255))
        if self._frame and not self._frame.isNull():
            fw, fh = self._frame.width(), self._frame.height()
            ww, wh = self.width(), self.height()
            scale  = min(ww / fw, wh / fh)
            dw     = int(fw * scale)
            dh     = int(fh * scale)
            ox     = (ww - dw) // 2
            oy     = (wh - dh) // 2
            p.drawPixmap(ox, oy, dw, dh, QPixmap.fromImage(self._frame))
        p.end()

    def get_frame(self) -> QImage:
        return self._frame

    def shutdown(self):
        self._render_timer.stop()
        self._watch_timer.stop()
        self._mixer.stop()
        for bv in self._bviews.values():
            bv.close()
        self._bviews.clear()

class RendererWindow(QMainWindow):

    def __init__(self, scene: dict, scene_file: str = None):
        super().__init__()
        self._scene      = scene
        self._scene_file = scene_file

        self.setWindowTitle(f"{scene.get('name','')}")
        cw = scene.get("canvas_w", CANVAS_W)
        ch = scene.get("canvas_h", CANVAS_H)
        self.setMinimumSize(640, 360)
        self.resize(cw, ch)

        self.setStyleSheet(f"""
            QMainWindow,QWidget{{background:{DARK_BG};color:{TEXT_PRI};}}
            QMenuBar{{background:{PANEL_BG};color:{TEXT_PRI};border-bottom:1px solid {BORDER};}}
            QMenuBar::item:selected{{background:{ACCENT};}}
            QMenu{{background:{PANEL_BG};color:{TEXT_PRI};border:1px solid {BORDER};}}
            QMenu::item:selected{{background:{ACCENT};}}
            QStatusBar{{background:{PANEL_BG};color:{TEXT_DIM};border-top:1px solid {BORDER};}}
        """)

        self.render_widget = RenderOutputWidget(scene, self)
        self.render_widget.set_scene_file(scene_file)
        self.setCentralWidget(self.render_widget)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage(
            f"Rendering  |  {cw}×{ch}  |  {scene.get('output',{}).get('fps',RENDER_FPS)} fps target"
            f"  |  {scene_file or 'in-memory'}"
        )

        self._build_menus()

        for seq, fn in [("F11", self._toggle_fullscreen),
                        ("Ctrl+R", self._manual_reload),
                        ("Ctrl+Q", self.close)]:
            a = QAction(seq, self)
            a.setShortcut(QKeySequence(seq))
            a.triggered.connect(fn)
            self.addAction(a)

    def _build_menus(self):
        mb = self.menuBar()

        vm = mb.addMenu("&View")
        a  = QAction("Toggle Fullscreen  F11", self)
        a.triggered.connect(self._toggle_fullscreen)
        vm.addAction(a)

        sm = mb.addMenu("&Scene")
        r  = QAction("Reload Scene  Ctrl+R", self)
        r.triggered.connect(self._manual_reload)
        sm.addAction(r)
        sm.addSeparator()
        i  = QAction("Scene Info", self)
        i.triggered.connect(self._show_info)
        sm.addAction(i)

        am = mb.addMenu("&Audio")
        ai = QAction("Audio Info", self)
        ai.triggered.connect(self._show_audio_info)
        am.addAction(ai)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _manual_reload(self):
        sf = self._scene_file
        if sf and os.path.isfile(sf):
            try:
                with open(sf, "r", encoding="utf-8") as fp:
                    ns = json.load(fp)
                self.render_widget.reload_scene(ns)
                self._status.showMessage(f"Reloaded: {sf}")
            except Exception as ex:
                QMessageBox.critical(self, "Reload Error", str(ex))
        else:
            QMessageBox.information(self, "No File", "No scene file to reload.")

    def _show_info(self):
        s   = self._scene
        src = s.get("sources", [])
        QMessageBox.information(self, "Scene Info",
            f"Scene: {s.get('name','')}\n"
            f"Canvas: {s.get('canvas_w',CANVAS_W)} × {s.get('canvas_h',CANVAS_H)}\n"
            f"Sources: {len(src)}  "
            f"(img={sum(1 for x in src if x['kind']=='image')}  "
            f"browser={sum(1 for x in src if x['kind']=='browser')}  "
            f"text={sum(1 for x in src if x['kind']=='text')})\n"
            f"FPS target: {s.get('output',{}).get('fps', RENDER_FPS)}\n"
            f"File: {self._scene_file or 'in-memory'}"
        )

    def _show_audio_info(self):
        mx  = self.render_widget._mixer
        bks = [b for b, ok in [("sounddevice", HAVE_SD), ("pyaudio", HAVE_PYAUDIO)] if ok]
        txt = (f"Backends: {', '.join(bks) or 'none installed'}\n"
               f"Sample rate: {AUDIO_SAMPLE_RATE} Hz  Channels: {AUDIO_CHANNELS}\n\n"
               f"Configured sources: {len(mx._sources)}\n")
        for sid, cfg in mx._sources.items():
            c = mx._compressors.get(sid)
            txt += (f"\n  {cfg.get('name','?')}\n"
                    f"    vol={cfg.get('volume',100)}%  muted={cfg.get('muted',False)}\n"
                    f"    compressor={'ON' if c and c.enabled else 'off'}\n")
            if c and c.enabled:
                txt += (f"      ratio={c.ratio}:1  thr={c.threshold_db}dB\n"
                        f"      atk={c.attack_ms}ms  rel={c.release_ms}ms  "
                        f"gain={c.output_gain}dB\n")
        QMessageBox.information(self, "Audio Info", txt)

    def closeEvent(self, e):
        self.render_widget.shutdown()
        e.accept()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("wxHDS-Rndr")
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(DARK_BG))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.ColorRole.Base,            QColor("#0d0d1a"))
    pal.setColor(QPalette.ColorRole.Text,            QColor(TEXT_PRI))
    pal.setColor(QPalette.ColorRole.Button,          QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT_PRI))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)

    scene      = None
    scene_file = None

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        scene_file = sys.argv[1]
        try:
            with open(scene_file, "r", encoding="utf-8") as fp:
                scene = json.load(fp)
            print(f"[Renderer] Loaded: {scene_file}")
        except Exception as ex:
            print(f"[Renderer] Failed to load scene: {ex}")

    if scene is None:
        scene = {
            "version": SCENE_VERSION,
            "name": "Demo Scene",
            "canvas_w": CANVAS_W, "canvas_h": CANVAS_H,
            "sources": [
                {
                    "id": "demo_browser", "kind": "browser",
                    "name": "Demo Browser", "visible": True, "locked": False,
                    "x": 0, "y": 0, "w": CANVAS_W, "h": CANVAS_H,
                    "rotation": 0.0,
                    "crop_top": 0, "crop_bottom": 0, "crop_left": 0, "crop_right": 0,
                    "opacity": 100, "blend_mode": "normal", "scale_mode": "stretch",
                    "audio_enabled": True, "volume": 100, "muted": False,
                    "audio_monitor": "none",
                    "compressor": {"enabled": False, "ratio": 10.0, "threshold": -18.0,
                                   "attack": 6.0, "release": 60.0, "output_gain": 0.0,
                                   "sidechain_source": ""},
                    "filters": [],
                    "url": "https://elliotd.au", "fps": 30, "custom_css": "",
                    "shutdown_when_not_visible": False, "refresh_on_activate": True,
                    "reroute_audio": False,
                    "browser_width": CANVAS_W, "browser_height": CANVAS_H,
                },
                {
                    "id": "demo_text", "kind": "text",
                    "name": "Watermark", "visible": True, "locked": False,
                    "x": 40, "y": 40, "w": 600, "h": 120,
                    "rotation": 0.0,
                    "crop_top": 0, "crop_bottom": 0, "crop_left": 0, "crop_right": 0,
                    "opacity": 90, "blend_mode": "normal", "scale_mode": "stretch",
                    "audio_enabled": False, "volume": 100, "muted": False,
                    "audio_monitor": "none",
                    "compressor": {"enabled": False, "ratio": 10.0, "threshold": -18.0,
                                   "attack": 6.0, "release": 60.0, "output_gain": 0.0,
                                   "sidechain_source": ""},
                    "filters": [],
                    "text": "Rndr demo scene not set!!!", "font_family": "Arial", "font_size": 72,
                    "font_bold": True, "font_italic": False, "font_underline": False,
                    "color": "#ffffff", "outline": True, "outline_color": "#000000",
                    "outline_size": 4, "shadow": True, "shadow_color": "#000000",
                    "align": "left", "valign": "top", "word_wrap": False,
                    "gradient": False, "gradient_color": "#ff4400",
                    "gradient_direction": 90,
                }
            ],
            "audio_sources": [],
            "output": {"width": CANVAS_W, "height": CANVAS_H, "fps": 60,
                       "format": "mkv", "encoder": "x264", "bitrate": 6000}
        }
        print("[Renderer] No scene file so im using da demo scene.")

    win = RendererWindow(scene, scene_file)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()