"""PDF 压缩器 —— 桌面界面（tkinter）。

界面分三块：左边文件列表（支持拖拽），右边压缩参数（预设 + 自定义），
底部进度与操作。压缩在内核 engine 里跑，界面只负责调度和展示。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

try:  # 可选：拖拽支持
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # pragma: no cover
    HAS_DND = False

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine import (  # noqa: E402
    PRESET_HINTS, PRESET_LABELS, PRESET_ORDER, __version__, analyze,
    compress_file, default_output_path, get_preset, human_size, preset_for,
    quick_info,
)
from engine.settings import Settings  # noqa: E402
from engine.utils import CancelToken, unique_output_path  # noqa: E402

APP_TITLE = "PDF 压缩器"
SETTINGS_VERSION = 1

BG = "#F4F6FB"
WHITE = "#FFFFFF"
TEXT = "#1E293B"
MUTED = "#6B7A90"
FAINT = "#94A3B8"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#EFF6FF"
ACCENT_LINE = "#BFDBFE"
BORDER = "#E3E8F0"
GREEN = "#15803D"
GREEN_SOFT = "#F0FDF4"
RED = "#DC2626"
RED_SOFT = "#FEF2F2"
AMBER = "#B45309"

FONT_FAMILY = "Microsoft YaHei UI"

PRESET_ESTIMATE = {
    "smallest": "通常再省 70–95%",
    "standard": "通常再省 40–85%",
    "high": "通常再省 10–40%",
    "print": "通常再省 5–25%",
}

# ---------------------------------------------------------------------------
# 免责声明
# 首次启动必须手动复述一句承诺才能使用；版本号变了会重新询问。
# 三处声明：README 文首的「特别说明」、独立 DISCLAIMER.md、程序内首次启动确认。
# ---------------------------------------------------------------------------
DISCLAIMER_VERSION = "2026-11-25"
DISCLAIMER_SUMMARY = """PDF 压缩器是一款本地批量压缩工具，仅供学习、研究与技术交流使用，不是商业产品。

· 只压缩你有合法权利处理的文件，并遵守所在地法律法规；
· 压缩全程在本机完成：不联网、不上传文件、不收集任何数据；
· 不提供破解加密、绕过 DRM 或规避授权限制的功能，加密文档会被直接拒绝；
· 压缩属于有损处理：图像分辨率与画质会变化，重要文件请先备份原件并人工复核；
· 因使用或无法使用本软件产生的后果由你自行承担，开发者与贡献者不承担责任。

完整条款见随附的 DISCLAIMER.md（共 11 节）。"""
CONSENT_PHRASE = "我只压缩我有权处理的文件，并遵守所在地法律法规，自行承担使用后果。"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", str(HERE))
    return os.path.join(base, rel)


def config_dir() -> Path:
    """设置文件目录：Windows 用 %APPDATA%，macOS 用 Application Support。"""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "PDFCompressor"
    elif IS_MACOS:
        path = Path.home() / "Library" / "Application Support" / "PDFCompressor"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "PDFCompressor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_path(path: Path) -> None:
    """用系统默认程序打开文件/文件夹。"""
    path = Path(path)
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # noqa: S606
        elif IS_MACOS:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def reveal_path(path: Path) -> None:
    """在文件管理器里定位到某个文件。"""
    path = Path(path)
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass


def open_in_explorer(path: Path, select: bool = False) -> None:
    """兼容旧调用：select=True 时定位文件，否则打开所在文件夹。"""
    if select:
        reveal_path(path)
    else:
        open_path(Path(path).parent if Path(path).is_file() else path)


def _norm_consent(text: str) -> str:
    """只比「有效字符」：忽略空格、换行与中英文标点差异。"""
    return "".join(ch for ch in text if ch.isalnum())


def _consent_matches(typed: str) -> bool:
    return _norm_consent(typed) == _norm_consent(CONSENT_PHRASE)


def _read_settings_dict() -> dict:
    try:
        return json.loads((config_dir() / "settings.json").read_text("utf-8"))
    except Exception:
        return {}


def disclaimer_accepted() -> bool:
    """本机是否已经确认过当前版本的免责声明。"""
    return _read_settings_dict().get("disclaimer") == DISCLAIMER_VERSION


def record_disclaimer_acceptance() -> None:
    """只更新 disclaimer 字段，不覆盖其他设置。"""
    try:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        data = _read_settings_dict()
        data["disclaimer"] = DISCLAIMER_VERSION
        (folder / "settings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def disclaimer_doc_path() -> Path | None:
    """随程序分发的 DISCLAIMER.md（打包后在 _internal 里）。"""
    for candidate in (Path(resource_path("DISCLAIMER.md")), HERE / "DISCLAIMER.md"):
        if candidate.is_file():
            return candidate
    return None


def percent_text(ratio: float | None) -> str:
    """0.9988 显示成 -99.9%，不要四舍五入成 -100%（看着像文件没了）。"""
    if ratio is None:
        return "—"
    value = max(0.0, min(1.0, ratio)) * 100
    if value >= 99.5:
        return f"-{min(value, 99.9):.1f}%"
    return f"-{value:.0f}%"


# ---------------------------------------------------------------------------
# 预设卡片
# ---------------------------------------------------------------------------

class PresetCard(tk.Frame):
    def __init__(self, parent, key: str, fonts, on_select):
        super().__init__(parent, bg=WHITE, highlightthickness=1,
                         highlightbackground=BORDER, cursor="hand2")
        self.key = key
        self.selected = False
        self.on_select = on_select

        self.pad = tk.Frame(self, bg=WHITE)
        self.pad.pack(fill="both", expand=True, padx=12, pady=7)

        top = tk.Frame(self.pad, bg=WHITE)
        top.pack(fill="x")
        self.top = top
        self.dot = tk.Label(top, text="○", bg=WHITE, fg=FAINT,
                            font=(FONT_FAMILY, 12), cursor="hand2")
        self.dot.pack(side="left")
        self.name = tk.Label(top, text=PRESET_LABELS[key], bg=WHITE, fg=TEXT,
                             font=fonts["card"], cursor="hand2")
        self.name.pack(side="left", padx=(6, 0))
        self.badge = tk.Label(top, text=PRESET_ESTIMATE[key], bg="#F1F5F9", fg=MUTED,
                              font=fonts["tiny"], padx=6, pady=1, cursor="hand2")
        self.badge.pack(side="right")

        self.desc = tk.Label(self.pad, text=PRESET_HINTS[key], bg=WHITE, fg=MUTED,
                             font=fonts["hint"], anchor="w", justify="left",
                             wraplength=280, cursor="hand2")
        self.desc.pack(fill="x", padx=(21, 0), pady=(2, 0))
        self.desc.bind("<Configure>", self._fit_text)

        for widget in (self, self.pad, top, self.dot, self.name, self.badge, self.desc):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.on_select(self.key)

    def _fit_text(self, event=None):
        """跟随卡片宽度调整换行宽度，避免文字压到边框外。"""
        width = self.desc.winfo_width()
        if width > 40:
            wrap = max(120, width - 4)
            if abs(int(self.desc.cget("wraplength")) - wrap) > 6:
                self.desc.configure(wraplength=wrap)

    def _enter(self, _event=None):
        if not self.selected:
            self._paint("#F8FAFC", BORDER)

    def _leave(self, _event=None):
        self._paint(ACCENT_SOFT if self.selected else WHITE,
                    ACCENT if self.selected else BORDER)

    def _paint(self, bg: str, border: str):
        self.configure(bg=bg, highlightbackground=border,
                       highlightthickness=2 if self.selected else 1)
        for widget in (self.pad, self.top, self.dot, self.name, self.desc):
            widget.configure(bg=bg)
        self.desc.configure(fg=MUTED)

    def set_selected(self, value: bool):
        self.selected = value
        self.dot.configure(text="●" if value else "○", fg=ACCENT if value else FAINT)
        self._paint(ACCENT_SOFT if value else WHITE, ACCENT if value else BORDER)


# ---------------------------------------------------------------------------
# 自绘滑杆
#
# Tk Canvas 画圆没抗锯齿，小半径时看着像多边形。这里改成先用 PIL 按 4 倍
# 尺寸绘制、再 LANCZOS 缩小成一张图贴上去，圆头和轨道都是平滑的。
# ---------------------------------------------------------------------------

def _render_slider(width: int, height: int, ratio: float,
                   hover: bool = False, dragging: bool = False) -> Image.Image:
    scale = 4
    width = max(1, int(width))
    canvas_w, canvas_h = width * scale, height * scale
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    pad = 12 * scale
    left, right = pad, canvas_w - pad
    mid = canvas_h // 2
    track = 4 * scale

    # 未填充的轨道
    draw.rounded_rectangle((left, mid - track / 2, right, mid + track / 2),
                           radius=track / 2, fill=(226, 232, 240, 255))

    knob_r = (8.4 if (hover or dragging) else 7.4) * scale
    ratio = max(0.0, min(1.0, ratio))
    knob_x = left + ratio * (right - left)

    # 已填充部分
    if knob_x - left > track / 2:
        draw.rounded_rectangle((left, mid - track / 2, knob_x, mid + track / 2),
                               radius=track / 2, fill=(37, 99, 235, 255))

    # 光晕：悬停/拖动时稍微大一圈
    glow = knob_r + (5.0 if (hover or dragging) else 3.0) * scale
    draw.ellipse((knob_x - glow, mid - glow, knob_x + glow, mid + glow),
                 fill=(37, 99, 235, 26))

    # 白色圆芯 + 蓝色描边
    draw.ellipse((knob_x - knob_r, mid - knob_r, knob_x + knob_r, mid + knob_r),
                 fill=(255, 255, 255, 255), outline=(37, 99, 235, 255),
                 width=max(1, round(1.8 * scale)))

    # 平铺到白底（避免依赖 Tk 的 alpha 合成），再缩回原始尺寸
    flat = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
    flat.alpha_composite(layer)
    return flat.convert("RGB").resize((width, height), Image.LANCZOS)


def _render_document_icon(size: int = 76) -> Image.Image:
    """空白页里的文档图标：同样超采样后缩小，边角不会有锯齿。"""
    scale = 4
    s = size * scale
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    left, top = round(s * 0.17), round(s * 0.08)
    right, bottom = round(s * 0.83), round(s * 0.92)
    radius = round(s * 0.06)
    stroke = max(1, round(1.8 * scale))
    border = (191, 219, 254, 255)

    draw.rounded_rectangle((left, top, right, bottom), radius=radius,
                           fill=(255, 255, 255, 255))
    fold = round(s * 0.20)
    inset = stroke
    draw.polygon([(right - fold, top + inset), (right - inset, top + fold),
                  (right - fold, top + fold)], fill=(219, 234, 254, 255))
    draw.line([(right - fold, top + inset), (right - inset, top + fold)],
              fill=border, width=max(1, round(1.2 * scale)))
    draw.rounded_rectangle((left, top, right, bottom), radius=radius,
                           outline=border, width=stroke)
    for index in range(3):
        y = top + round(s * 0.33) + index * round(s * 0.14)
        draw.rounded_rectangle(
            (left + round(s * 0.12), y, right - round(s * 0.22),
             y + max(1, round(2.2 * scale))),
            radius=max(1, round(1.1 * scale)), fill=(203, 213, 225, 255))

    flat = Image.new("RGBA", (s, s), (251, 252, 254, 255))
    flat.alpha_composite(layer)
    return flat.convert("RGB").resize((size, size), Image.LANCZOS)


class Slider(tk.Canvas):
    HEIGHT = 28
    PAD = 12

    def __init__(self, parent, minimum: int, maximum: int, value: int, step: int,
                 command, width: int = 250):
        super().__init__(parent, height=self.HEIGHT, width=width, bg=WHITE,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.min = minimum
        self.max = maximum
        self.step = step
        self.value = value
        self.command = command
        self._dragging = False
        self._hover = False
        self._image = None
        self._item = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _geometry(self):
        width = max(60, self.winfo_width())
        return self.PAD, width - self.PAD

    def _draw(self):
        width = max(60, self.winfo_width())
        ratio = (self.value - self.min) / max(1, self.max - self.min)
        image = _render_slider(width, self.HEIGHT, ratio, self._hover, self._dragging)
        self._image = ImageTk.PhotoImage(image)
        if self._item is None:
            self._item = self.create_image(0, 0, anchor="nw", image=self._image)
        else:
            self.itemconfigure(self._item, image=self._image)

    def _value_from_x(self, x: int) -> int:
        left, right = self._geometry()
        ratio = min(1.0, max(0.0, (x - left) / max(1, right - left)))
        raw = self.min + ratio * (self.max - self.min)
        snapped = int(round(raw / self.step) * self.step)
        return max(self.min, min(self.max, snapped))

    def _on_enter(self, _event):
        if not self._hover:
            self._hover = True
            self._draw()

    def _on_leave(self, _event):
        if self._hover or self._dragging:
            self._hover = False
            self._draw()

    def _on_press(self, event):
        self._dragging = True
        self.set(self._value_from_x(event.x), notify=True)

    def _on_drag(self, event):
        if self._dragging:
            self.set(self._value_from_x(event.x), notify=True)

    def _on_release(self, event):
        self._dragging = False
        # 松手时鼠标可能已经不在控件上
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._hover = inside
        self._draw()

    def set(self, value: int, notify: bool = False):
        value = max(self.min, min(self.max, int(value)))
        changed = value != self.value
        self.value = value
        self._draw()
        if notify and changed:
            self.command(value)

    def get(self) -> int:
        return self.value


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_TITLE} · 本地批量压缩")
        self.root.configure(bg=BG)

        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        self.compact = screen_h < 1000
        width = 1080 if self.compact else 1160
        height = min(720, screen_h - 80) if self.compact else min(820, screen_h - 90)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(1000, 700)

        self._set_window_icon()

        self.settings: Settings = get_preset("standard")
        self.output_mode = "same"       # same | folder
        self.output_dir = ""
        self.overwrite = False
        self.last_dir = str(Path.home())
        self.disclaimer = ""            # 已确认的免责声明版本

        self.files: list[dict] = []
        self.running = False
        self.cancel_token: CancelToken | None = None
        self.worker: threading.Thread | None = None
        self.queue: queue.Queue | None = None
        self.current_index = -1
        self.started_at = 0.0
        self.timer_job = None
        self._sync = False
        self._detail_window = None

        self._init_fonts()
        self._init_styles()
        self._load_settings()
        self._build_ui()
        self._refresh_files()
        self._settings_to_ui(initial=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-o>", lambda _e: self.add_files())
        self.root.bind("<Delete>", lambda _e: self.remove_selected())
        self.root.bind("<Control-Return>", lambda _e: self.start())

    # ------------------------------------------------------------------ 初始化
    def _set_window_icon(self):
        """Windows 用 .ico，macOS/Linux 用 .png 设置窗口图标。"""
        ico = resource_path(os.path.join("assets", "icon.ico"))
        png = resource_path(os.path.join("assets", "icon.png"))
        if IS_WINDOWS and os.path.exists(ico):
            try:
                self.root.iconbitmap(default=ico)
                return
            except Exception:
                pass
        if os.path.exists(png):
            try:
                image = Image.open(png).convert("RGBA").resize((128, 128), Image.LANCZOS)
                self._window_icon = ImageTk.PhotoImage(image)
                self.root.iconphoto(True, self._window_icon)
            except Exception:
                pass

    def _init_fonts(self):
        global FONT_FAMILY
        families = set(tkfont.families(self.root))
        for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"):
            if candidate in families:
                FONT_FAMILY = candidate
                break
        base = 9 if self.compact else 10
        self.fonts = {
            "title": (FONT_FAMILY, 18 if self.compact else 20, "bold"),
            "subtitle": (FONT_FAMILY, 9),
            "section": (FONT_FAMILY, 9, "bold"),
            "card": (FONT_FAMILY, 10, "bold"),
            "body": (FONT_FAMILY, base - 1),
            "small": (FONT_FAMILY, 8),
            "tiny": (FONT_FAMILY, 8),
            "button": (FONT_FAMILY, 10, "bold"),
            "ghost": (FONT_FAMILY, 9),
            "file": (FONT_FAMILY, 9),
            "status": (FONT_FAMILY, 10, "bold"),
            "hint": (FONT_FAMILY, 8),
            "big": (FONT_FAMILY, 15, "bold"),
        }

    def _init_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Files.Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, borderwidth=0, rowheight=38,
                        font=self.fonts["file"])
        style.configure("Files.Treeview.Heading", background="#FAFBFE", foreground=MUTED,
                        relief="flat", borderwidth=0, font=self.fonts["tiny"])
        style.map("Files.Treeview.Heading", background=[("active", "#F1F5F9")])
        style.map("Files.Treeview", background=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", TEXT)])
        style.layout("Files.Treeview", [("Files.Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Mini.Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, borderwidth=0, rowheight=24,
                        font=self.fonts["tiny"])
        style.configure("Mini.Treeview.Heading", background="#FAFBFE", foreground=FAINT,
                        relief="flat", borderwidth=0, font=self.fonts["tiny"])
        style.map("Mini.Treeview.Heading", background=[("active", "#F1F5F9")])
        style.layout("Mini.Treeview", [("Mini.Treeview.treearea", {"sticky": "nswe"})])
        style.map("Mini.Treeview", background=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", TEXT)])
        style.configure("Accent.Horizontal.TProgressbar",
                        troughcolor="#E9EEF7", background=ACCENT, bordercolor="#DCE4F0",
                        lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=1, thickness=10)
        style.configure("Vertical.TScrollbar", background="#D7DEEA", troughcolor=WHITE,
                        borderwidth=0, arrowsize=10, width=10)
        style.configure("Card.TCheckbutton", background=WHITE, foreground=TEXT,
                        font=self.fonts["body"], focuscolor=WHITE,
                        indicatorcolor=WHITE)
        style.map("Card.TCheckbutton",
                  background=[("active", WHITE)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", WHITE)])
        style.configure("Card.TCombobox", fieldbackground=WHITE, background=WHITE,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4,
                        font=self.fonts["body"])
        style.map("Card.TCombobox", fieldbackground=[("readonly", WHITE)],
                  bordercolor=[("focus", ACCENT_LINE)])

    def _load_settings(self):
        path = config_dir() / "settings.json"
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            return
        self.disclaimer = str(data.get("disclaimer") or "")
        if int(data.get("settings_version", 0)) != SETTINGS_VERSION:
            return
        try:
            self.settings = Settings.from_dict(data.get("settings") or {})
            preset = data.get("preset")
            if preset in PRESET_ORDER:
                self.settings = get_preset(preset)
        except Exception:
            pass
        self.output_mode = data.get("output_mode", "same")
        self.output_dir = data.get("output_dir", "")
        self.overwrite = bool(data.get("overwrite", False))
        self.last_dir = data.get("last_dir", str(Path.home()))

    def _save_settings(self):
        payload = {
            "settings_version": SETTINGS_VERSION,
            "preset": self.settings.preset if self.settings.preset in PRESET_ORDER else "custom",
            "settings": self.settings.to_dict(),
            "output_mode": self.output_mode,
            "output_dir": self.output_dir,
            "overwrite": self.overwrite,
            "last_dir": self.last_dir,
            "disclaimer": getattr(self, "disclaimer", ""),
        }
        try:
            (config_dir() / "settings.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------ 布局
    def _build_ui(self):
        self._build_header()
        bottom = self._build_bottom()
        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=(14, 12))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_files(body)
        self._build_settings(body)
        bottom.lift()

    def _build_header(self):
        header = tk.Frame(self.root, bg=WHITE, height=76,
                          highlightthickness=1, highlightbackground=BORDER)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=WHITE)
        inner.pack(fill="both", expand=True, padx=22, pady=10)

        logo_file = resource_path(os.path.join("assets", "icon.png"))
        self._logo_photo = None
        if os.path.exists(logo_file):
            try:
                image = Image.open(logo_file).convert("RGBA").resize((44, 44), Image.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(image)
            except Exception:
                self._logo_photo = None
        if self._logo_photo is not None:
            tk.Label(inner, image=self._logo_photo, bg=WHITE, bd=0).pack(
                side="left", padx=(0, 12))
        else:
            logo = tk.Canvas(inner, width=42, height=42, bg=WHITE, highlightthickness=0)
            logo.pack(side="left", padx=(0, 12))
            logo.create_rectangle(0, 0, 42, 42, fill=ACCENT_SOFT, outline=ACCENT_LINE)
            logo.create_polygon(12, 10, 30, 10, 34, 15, 34, 32, 12, 32,
                                fill=WHITE, outline=ACCENT, width=1.6)
            logo.create_line(12, 16, 28, 16, fill=ACCENT_LINE, width=1.4)
            logo.create_line(12, 21, 26, 21, fill=ACCENT_LINE, width=1.4)
            logo.create_line(21, 24, 21, 31, fill=ACCENT, width=2.2)
            logo.create_polygon(17, 28, 21, 32, 25, 28, fill=ACCENT, outline=ACCENT)

        text = tk.Frame(inner, bg=WHITE)
        text.pack(side="left")
        tk.Label(text, text=APP_TITLE, bg=WHITE, fg=TEXT,
                 font=self.fonts["title"]).pack(anchor="w")
        tk.Label(text, text="本地压缩 · 图像降采样 + 字体子集化 + 对象流清理",
                 bg=WHITE, fg=MUTED, font=self.fonts["subtitle"]).pack(anchor="w")

        right = tk.Frame(inner, bg=WHITE)
        right.pack(side="right")
        self._ghost_button(right, "免责声明", self.show_disclaimer_doc,
                           fg=MUTED).pack(side="right")
        self._ghost_button(right, "压缩原理", self.show_about, fg=MUTED).pack(side="right")
        tk.Label(right, text=f"v{__version__}", bg=WHITE, fg=FAINT,
                 font=self.fonts["hint"]).pack(side="right", padx=(0, 12))

    def _build_files(self, parent):
        card = tk.Frame(parent, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)

        bar = tk.Frame(card, bg=WHITE)
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        tk.Label(bar, text="文件列表", bg=WHITE, fg=TEXT,
                 font=self.fonts["section"]).pack(side="left")
        self.count_badge = tk.Label(bar, text="0", bg="#F1F5F9", fg=MUTED,
                                    font=self.fonts["tiny"], padx=7, pady=1)
        self.count_badge.pack(side="left", padx=(8, 0))

        self.btn_clear = self._ghost_button(bar, "清空", self.clear_files, fg=MUTED)
        self.btn_clear.pack(side="right")
        self.btn_folder = self._ghost_button(bar, "添加文件夹", self.add_folder)
        self.btn_folder.pack(side="right", padx=(0, 4))
        self.btn_add = self._ghost_button(bar, "添加文件", self.add_files)
        self.btn_add.pack(side="right", padx=(0, 4))

        holder = tk.Frame(card, bg=WHITE)
        holder.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(holder, style="Files.Treeview", show="headings",
                                 columns=("name", "pages", "before", "after", "ratio", "status"),
                                 selectmode="extended")
        headings = {
            "name": ("文件名", 250, "w"),
            "pages": ("页数", 52, "center"),
            "before": ("原始大小", 92, "e"),
            "after": ("压缩后", 92, "e"),
            "ratio": ("减少", 72, "e"),
            "status": ("状态", 130, "w"),
        }
        for key, (label, width, anchor) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=44, anchor=anchor,
                             stretch=(key == "name"))
        self.tree.tag_configure("ok", foreground=GREEN)
        self.tree.tag_configure("err", foreground=RED)
        self.tree.tag_configure("run", foreground=ACCENT)
        self.tree.tag_configure("wait", foreground=FAINT)
        self.tree.tag_configure("warn", foreground=AMBER)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self._on_tree_double)

        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree_scroll = scroll

        self.footer_hint = tk.Label(
            card, text="双击条目查看压缩详情 · Delete 移除选中 · Ctrl+O 添加文件",
            bg=WHITE, fg=FAINT, font=self.fonts["hint"], anchor="w")
        self.footer_hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))

        # 空状态
        self.empty = tk.Frame(holder, bg=WHITE)
        self.empty.grid(row=0, column=0, sticky="nsew")
        self.empty.rowconfigure(0, weight=1)
        self.empty.columnconfigure(0, weight=1)
        box = tk.Frame(self.empty, bg="#FBFCFE", highlightthickness=1,
                       highlightbackground="#DCE4F0", highlightcolor=ACCENT_LINE)
        box.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.82, relheight=0.62)

        content = tk.Frame(box, bg="#FBFCFE")
        content.place(relx=0.5, rely=0.5, anchor="center")

        self._empty_icon = ImageTk.PhotoImage(_render_document_icon(76))
        tk.Label(content, image=self._empty_icon, bg="#FBFCFE", bd=0).pack(pady=(0, 10))

        tk.Label(content, text="把 PDF 拖到这里", bg="#FBFCFE", fg=TEXT,
                 font=self.fonts["big"]).pack()
        tk.Label(content, text="支持批量、文件夹整体添加；双击条目查看压缩详情",
                 bg="#FBFCFE", fg=MUTED, font=self.fonts["subtitle"]).pack(pady=(6, 0))
        actions = tk.Frame(content, bg="#FBFCFE")
        actions.pack(pady=(16, 0))
        self._ghost_button(actions, "选择文件", self.add_files, fg=ACCENT).pack(side="left")
        self._ghost_button(actions, "选择文件夹", self.add_folder, fg=MUTED).pack(side="left", padx=8)
        tk.Label(content, text="所有处理都在本机完成，文件不会上传", bg="#FBFCFE",
                 fg=FAINT, font=self.fonts["hint"]).pack(pady=(18, 0))

    def _build_settings(self, parent):
        wrapper = tk.Frame(parent, bg=WHITE, width=390,
                           highlightthickness=1, highlightbackground=BORDER)
        wrapper.grid(row=0, column=1, sticky="ns")
        wrapper.grid_propagate(False)
        wrapper.pack_propagate(False)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

        canvas = tk.Canvas(wrapper, bg=WHITE, highlightthickness=0, bd=0, width=372)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        inner = tk.Frame(canvas, bg=WHITE)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))

        def on_wheel(event):
            if canvas.winfo_height() < inner.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        def bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", on_wheel)

        def unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", bind_wheel)
        canvas.bind("<Leave>", unbind_wheel)
        self.settings_canvas = canvas

        pad = tk.Frame(inner, bg=WHITE)
        pad.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        # ---------- 预设 ----------
        title = tk.Frame(pad, bg=WHITE)
        title.pack(fill="x")
        tk.Label(title, text="压缩预设", bg=WHITE, fg=TEXT,
                 font=self.fonts["section"]).pack(side="left")
        self.custom_badge = tk.Label(title, text="自定义", bg=ACCENT_SOFT, fg=ACCENT,
                                     font=self.fonts["tiny"], padx=6, pady=1)
        self.preset_hint = tk.Label(title, text="", bg=WHITE, fg=FAINT,
                                    font=self.fonts["hint"])
        self.preset_hint.pack(side="right")

        self.cards: dict[str, PresetCard] = {}
        for key in PRESET_ORDER:
            card = PresetCard(pad, key, self.fonts, self._on_preset)
            card.pack(fill="x", pady=(6, 0))
            self.cards[key] = card

        self._separator(pad)

        # ---------- 图像 ----------
        tk.Label(pad, text="图像", bg=WHITE, fg=TEXT,
                 font=self.fonts["section"]).pack(anchor="w")

        self.slider_color_dpi, self.lbl_color_dpi = self._slider_row(
            pad, "彩色 / 灰度分辨率", 48, 600, 150, 6, self._on_dpi, unit="DPI")
        self.slider_quality, self.lbl_quality = self._slider_row(
            pad, "JPEG 品质", 10, 100, 65, 1, self._on_quality, unit="")
        self.slider_mono_dpi, self.lbl_mono_dpi = self._slider_row(
            pad, "单色（扫描件）分辨率", 96, 600, 300, 6, self._on_mono_dpi, unit="DPI")

        self.advanced_toggle = tk.Label(pad, text="▸ 高级选项", bg=WHITE, fg=MUTED,
                                        font=self.fonts["small"], cursor="hand2")
        self.advanced_toggle.pack(anchor="w", pady=(6, 0))
        self.advanced_toggle.bind("<Button-1>", lambda _e: self._toggle_advanced())

        self.advanced = tk.Frame(pad, bg=WHITE)
        self._advanced_open = False
        self.combo_downsample = self._combo_row(
            self.advanced, "降采样方式", ["双立方（推荐）", "平均", "次采样"], 0)
        self.combo_downsample.bind("<<ComboboxSelected>>", lambda _e: self._on_downsample())
        self.combo_cmyk = self._combo_row(
            self.advanced, "CMYK 图像", ["保留 CMYK（原样）", "转为 RGB"], 1)
        self.combo_cmyk.bind("<<ComboboxSelected>>", lambda _e: self._on_cmyk())
        self.var_gray = tk.BooleanVar(value=False)
        self.chk_gray = ttk.Checkbutton(self.advanced, text="彩色图像转灰度（更小，但会失去颜色）",
                                        variable=self.var_gray, style="Card.TCheckbutton",
                                        command=self._on_gray)
        self.chk_gray.pack(anchor="w", pady=(4, 0), padx=(0, 0))

        self._separator(pad)

        # ---------- 清理 ----------
        tk.Label(pad, text="清理与结构", bg=WHITE, fg=TEXT,
                 font=self.fonts["section"]).pack(anchor="w")

        grid = tk.Frame(pad, bg=WHITE)
        grid.pack(fill="x", pady=(4, 0))
        grid.columnconfigure(0, weight=1, uniform="a")
        grid.columnconfigure(1, weight=1, uniform="a")

        self.var_meta = tk.BooleanVar()
        self.var_thumb = tk.BooleanVar()
        self.var_outline = tk.BooleanVar()
        self.var_attach = tk.BooleanVar()
        self.var_annot = tk.BooleanVar()
        self.var_struct = tk.BooleanVar()
        checks = [
            ("元数据", self.var_meta, "strip_metadata"),
            ("缩略图", self.var_thumb, "strip_thumbnails"),
            ("书签", self.var_outline, "strip_outlines"),
            ("附件", self.var_attach, "strip_attachments"),
            ("注释 / 表单", self.var_annot, "strip_annotations"),
            ("结构树（无障碍）", self.var_struct, "strip_structure"),
        ]
        for index, (label, var, attr) in enumerate(checks):
            box = ttk.Checkbutton(grid, text=label, variable=var,
                                  style="Card.TCheckbutton",
                                  command=lambda a=attr, v=var: self._on_flag(a, v))
            box.grid(row=index // 2, column=index % 2, sticky="w", pady=3, padx=(0, 6))

        tk.Frame(pad, bg=WHITE, height=2).pack()

        self.var_fonts = tk.BooleanVar()
        self.var_base14 = tk.BooleanVar()
        self.var_objstm = tk.BooleanVar()
        self.var_linear = tk.BooleanVar()
        flags2 = [
            ("字体子集化（只留用到的字）", self.var_fonts, "subset_fonts"),
            ("取消嵌入标准 14 字体", self.var_base14, "unembed_base14"),
            ("对象流压缩", self.var_objstm, "compress_object_streams"),
            ("线性化（快速 Web 查看）", self.var_linear, "linearize"),
        ]
        for index, (label, var, attr) in enumerate(flags2):
            box = ttk.Checkbutton(pad, text=label, variable=var, style="Card.TCheckbutton",
                                  command=lambda a=attr, v=var: self._on_flag(a, v))
            box.pack(anchor="w", pady=2)

        self._separator(pad)

        # ---------- 输出 ----------
        tk.Label(pad, text="输出", bg=WHITE, fg=TEXT,
                 font=self.fonts["section"]).pack(anchor="w", pady=(0, 6))

        self.output_frame = tk.Frame(pad, bg=WHITE)
        self.output_frame.pack(fill="x")
        self.var_output = tk.StringVar(value="same")
        for key, label in (("same", "与源文件同目录"), ("folder", "指定文件夹")):
            rb = tk.Radiobutton(self.output_frame, text=label, value=key,
                                variable=self.var_output, bg=WHITE, fg=TEXT,
                                activebackground=WHITE, selectcolor=WHITE,
                                font=self.fonts["body"], highlightthickness=0, bd=0,
                                command=self._on_output_mode)
            rb.pack(side="left", padx=(0, 14))

        self.dir_row = tk.Frame(pad, bg=WHITE)
        self.dir_row.pack(fill="x", pady=(6, 0))
        self.dir_label = tk.Label(self.dir_row, text="", bg=WHITE, fg=MUTED,
                                  font=self.fonts["hint"], anchor="w")
        self.dir_label.pack(side="left", fill="x", expand=True)
        self._ghost_button(self.dir_row, "选择…", self._choose_output_dir, fg=ACCENT).pack(side="right")

        suffix_row = tk.Frame(pad, bg=WHITE)
        suffix_row.pack(fill="x", pady=(8, 0))
        tk.Label(suffix_row, text="文件名后缀", bg=WHITE, fg=MUTED,
                 font=self.fonts["body"]).pack(side="left")
        self.var_suffix = tk.StringVar(value="-压缩")
        entry = tk.Entry(suffix_row, textvariable=self.var_suffix, bg="#FBFCFE",
                         fg=TEXT, relief="flat", font=self.fonts["body"],
                         insertbackground=TEXT, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT_LINE, width=10)
        entry.pack(side="left", padx=(10, 0), ipady=3)
        entry.bind("<FocusOut>", lambda _e: self._on_suffix())
        entry.bind("<Return>", lambda _e: self._on_suffix())

        self.var_overwrite = tk.BooleanVar()
        ttk.Checkbutton(pad, text="同名文件直接覆盖（否则自动加序号）",
                        variable=self.var_overwrite, style="Card.TCheckbutton",
                        command=self._on_overwrite).pack(anchor="w", pady=(8, 0))

    def _build_bottom(self):
        bar = tk.Frame(self.root, bg=WHITE, height=84,
                       highlightthickness=1, highlightbackground=BORDER)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=WHITE)
        inner.pack(fill="both", expand=True, padx=22, pady=10)
        inner.columnconfigure(0, weight=1)

        top = tk.Frame(inner, bg=WHITE)
        top.grid(row=0, column=0, sticky="ew")
        self.status_label = tk.Label(top, text="准备就绪 · 把 PDF 拖进来或点「添加文件」",
                                     bg=WHITE, fg=TEXT, font=self.fonts["status"], anchor="w")
        self.status_label.pack(side="left")
        self.total_label = tk.Label(top, text="", bg=WHITE, fg=MUTED,
                                    font=self.fonts["small"], anchor="e")
        self.total_label.pack(side="right")

        prog_row = tk.Frame(inner, bg=WHITE)
        prog_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        prog_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog_row, style="Accent.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.percent_label = tk.Label(prog_row, text="0%", bg=WHITE, fg=MUTED,
                                      font=self.fonts["small"], width=5, anchor="e")
        self.percent_label.grid(row=0, column=1, padx=(10, 0))
        self.timer_label = tk.Label(prog_row, text="", bg=WHITE, fg=FAINT,
                                    font=self.fonts["hint"], width=8, anchor="e")
        self.timer_label.grid(row=0, column=2, padx=(4, 0))

        actions = tk.Frame(inner, bg=WHITE)
        actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=(20, 0))
        self.btn_open = self._ghost_button(actions, "打开输出文件夹", self.open_output, fg=MUTED)
        self.btn_open.pack(side="left", padx=(0, 10))
        self.btn_open.configure(state="disabled")
        self.btn_stop = self._ghost_button(actions, "停止", self.stop, fg=RED)
        self.btn_stop.pack(side="left", padx=(0, 10))
        self.btn_stop.configure(state="disabled")
        self.btn_start = self._primary_button(actions, "开始压缩", self.start)
        self.btn_start.pack(side="left")
        return bar

    # ------------------------------------------------------------------ 控件工厂
    def _ghost_button(self, parent, text, command, fg=ACCENT):
        btn = tk.Button(parent, text=text, command=command, bg=WHITE, fg=fg,
                        activebackground="#F1F5F9", activeforeground=fg,
                        relief="flat", bd=0, highlightthickness=0,
                        font=self.fonts["ghost"], cursor="hand2", padx=9, pady=5)
        btn.bind("<Enter>", lambda _e: btn.configure(bg="#F1F5F9") if str(btn["state"]) != "disabled" else None)
        btn.bind("<Leave>", lambda _e: btn.configure(bg=WHITE))
        return btn

    def _primary_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command, bg=ACCENT, fg="white",
                        activebackground=ACCENT_DARK, activeforeground="white",
                        disabledforeground="#DBEAFE", relief="flat", bd=0,
                        highlightthickness=0, font=self.fonts["button"],
                        cursor="hand2", padx=22, pady=9)
        return btn

    def _separator(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=12)

    def _slider_row(self, parent, label: str, minimum: int, maximum: int,
                    value: int, step: int, command, unit: str = ""):
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x", pady=(8, 0))
        head = tk.Frame(row, bg=WHITE)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=WHITE, fg=TEXT,
                 font=self.fonts["body"]).pack(side="left")
        value_label = tk.Label(head, text="", bg=WHITE, fg=ACCENT,
                               font=self.fonts["small"])
        value_label.pack(side="right")
        slider = Slider(row, minimum, maximum, value, step, command)
        slider.pack(fill="x", pady=(2, 0))
        return slider, value_label

    def _combo_row(self, parent, label: str, values: list[str], row: int):
        row_frame = tk.Frame(parent, bg=WHITE)
        row_frame.pack(fill="x", pady=(6, 0))
        tk.Label(row_frame, text=label, bg=WHITE, fg=TEXT,
                 font=self.fonts["body"]).pack(anchor="w")
        combo = ttk.Combobox(row_frame, values=values, state="readonly",
                             style="Card.TCombobox", font=self.fonts["body"])
        combo.current(0)
        combo.pack(fill="x", pady=(3, 0))
        return combo

    # ------------------------------------------------------------------ 参数同步
    def _settings_to_ui(self, initial: bool = False):
        self._sync = True
        try:
            settings = self.settings
            self.slider_color_dpi.set(settings.color_dpi)
            self.lbl_color_dpi.configure(text=f"{settings.color_dpi} DPI")
            self.slider_quality.set(settings.color_quality)
            self.lbl_quality.configure(text=str(settings.color_quality))
            self.slider_mono_dpi.set(settings.mono_dpi)
            self.lbl_mono_dpi.configure(text=f"{settings.mono_dpi} DPI")

            downsample_index = {"bicubic": 0, "average": 1, "subsample": 2}.get(
                settings.downsample, 0)
            self.combo_downsample.current(downsample_index)
            self.combo_cmyk.current(1 if settings.cmyk_mode == "rgb" else 0)

            self.var_gray.set(settings.convert_gray)
            self.var_meta.set(settings.strip_metadata)
            self.var_thumb.set(settings.strip_thumbnails)
            self.var_outline.set(settings.strip_outlines)
            self.var_attach.set(settings.strip_attachments)
            self.var_annot.set(settings.strip_annotations)
            self.var_struct.set(settings.strip_structure)
            self.var_fonts.set(settings.subset_fonts)
            self.var_base14.set(settings.unembed_base14)
            self.var_objstm.set(settings.compress_object_streams)
            self.var_linear.set(settings.linearize)

            self.var_output.set(self.output_mode)
            self.var_overwrite.set(self.overwrite)
            self.var_suffix.set(settings.output_suffix)
            self._update_output_ui()
            self._refresh_presets()
        finally:
            self._sync = False

    def _mark_custom(self):
        self.settings.preset = preset_for(self.settings)
        self._refresh_presets()

    def _refresh_presets(self):
        current = self.settings.preset if self.settings.preset in PRESET_ORDER else "custom"
        for key, card in self.cards.items():
            card.set_selected(key == current)
        if current == "custom":
            self.custom_badge.pack(side="left", padx=(8, 0))
            self.preset_hint.configure(text="已按你的调整运行")
        else:
            self.custom_badge.pack_forget()
            self.preset_hint.configure(text="")

    def _on_preset(self, key: str):
        if self.running:
            return
        self.settings = get_preset(key)
        self.settings.output_suffix = self.var_suffix.get() or self.settings.output_suffix
        self._settings_to_ui()
        self._save_settings()

    def _on_dpi(self, value: int):
        if self._sync:
            return
        self.settings.color_dpi = self.settings.gray_dpi = value
        self.lbl_color_dpi.configure(text=f"{value} DPI")
        self._mark_custom()

    def _on_quality(self, value: int):
        if self._sync:
            return
        self.settings.color_quality = value
        self.settings.gray_quality = min(100, value + 5)
        self.lbl_quality.configure(text=str(value))
        self._mark_custom()

    def _on_mono_dpi(self, value: int):
        if self._sync:
            return
        self.settings.mono_dpi = value
        self.lbl_mono_dpi.configure(text=f"{value} DPI")
        self._mark_custom()

    def _on_downsample(self):
        if self._sync:
            return
        self.settings.downsample = ["bicubic", "average", "subsample"][
            self.combo_downsample.current()]
        self._mark_custom()

    def _on_cmyk(self):
        if self._sync:
            return
        self.settings.cmyk_mode = "rgb" if self.combo_cmyk.current() == 1 else "keep"
        self._mark_custom()

    def _on_gray(self):
        if self._sync:
            return
        self.settings.convert_gray = self.var_gray.get()
        self._mark_custom()

    def _on_flag(self, attr: str, var):
        if self._sync:
            return
        setattr(self.settings, attr, bool(var.get()))
        self._mark_custom()

    def _toggle_advanced(self):
        if self._advanced_open:
            self.advanced.pack_forget()
            self.advanced_toggle.configure(text="▸ 高级选项")
            self._advanced_open = False
        else:
            self.advanced.pack(fill="x", pady=(2, 0), after=self.advanced_toggle)
            self.advanced_toggle.configure(text="▾ 高级选项")
            self._advanced_open = True

    def _on_output_mode(self):
        self.output_mode = self.var_output.get()
        self._update_output_ui()
        self._save_settings()

    def _update_output_ui(self):
        if self.output_mode == "folder":
            self.dir_label.configure(
                text=self.output_dir or "尚未选择文件夹",
                fg=MUTED if self.output_dir else FAINT)
            self.dir_row.pack(fill="x", pady=(6, 0))
        else:
            self.dir_label.configure(text="输出到每个 PDF 旁边", fg=FAINT)

    def _choose_output_dir(self):
        folder = filedialog.askdirectory(title="选择输出文件夹",
                                         initialdir=self.output_dir or self.last_dir)
        if folder:
            self.output_dir = folder
            self.last_dir = folder
            self.output_mode = "folder"
            self.var_output.set("folder")
            self._update_output_ui()
            self._save_settings()

    def _on_suffix(self):
        self.settings.output_suffix = self.var_suffix.get().strip() or "-压缩"
        self._save_settings()

    def _on_overwrite(self):
        self.overwrite = bool(self.var_overwrite.get())
        self._save_settings()

    # ------------------------------------------------------------------ 文件
    def _accept_paths(self, paths: list[str]) -> list[Path]:
        accepted: list[Path] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                accepted.extend(sorted(p for p in path.glob("*.pdf") if p.is_file()))
            elif path.is_file() and path.suffix.lower() == ".pdf":
                accepted.append(path)
        return accepted

    def add_files(self):
        if self.running:
            return
        names = filedialog.askopenfilenames(
            title="选择 PDF 文件", initialdir=self.last_dir,
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        if not names:
            return
        self.last_dir = str(Path(names[0]).parent)
        self._add_paths([str(n) for n in names])

    def add_folder(self):
        if self.running:
            return
        folder = filedialog.askdirectory(title="选择包含 PDF 的文件夹", initialdir=self.last_dir)
        if not folder:
            return
        self.last_dir = folder
        paths = sorted(str(p) for p in Path(folder).glob("*.pdf") if p.is_file())
        if not paths:
            messagebox.showinfo(APP_TITLE, "这个文件夹里没有 PDF 文件。")
            return
        self._add_paths(paths)

    def _add_paths(self, paths: list[str]):
        accepted = self._accept_paths(paths)
        existing = {str(item["path"]).lower() for item in self.files}
        added = 0
        for path in accepted:
            key = str(path).lower()
            if key in existing:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            try:
                pages = quick_info(path)[0]
            except Exception:
                pages = 0
            self.files.append({
                "path": path, "size": size, "pages": pages,
                "status": "等待中", "after": None, "ratio": None,
                "result": None, "state": "wait",
            })
            existing.add(key)
            added += 1
        if added:
            self._refresh_files()
            self._set_status(f"已添加 {added} 个文件")
            self._save_settings()

    def _refresh_files(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.files):
            name = item["path"].name
            if len(name) > 46:
                name = name[:22] + "…" + name[-20:]
            after = human_size(item["after"]) if item["after"] else "—"
            ratio = percent_text(item.get("ratio"))
            self.tree.insert("", "end", iid=str(index), values=(
                name,
                item["pages"] or "—",
                human_size(item["size"]),
                after,
                ratio,
                item["status"],
            ), tags=(item.get("state", "wait"),))
        count = len(self.files)
        self.count_badge.configure(text=str(count))
        total = sum(item["size"] for item in self.files)
        self.total_label.configure(
            text=f"{count} 个文件 · 合计 {human_size(total)}" if count else "")
        if count:
            self.empty.grid_remove()
            self.tree.grid()
            self.tree_scroll.grid()
            self.footer_hint.grid()
        else:
            self.tree.grid_remove()
            self.tree_scroll.grid_remove()
            self.empty.grid()
            self.footer_hint.grid_remove()

    def remove_selected(self):
        if self.running:
            return
        chosen = sorted((int(iid) for iid in self.tree.selection()), reverse=True)
        for index in chosen:
            if 0 <= index < len(self.files):
                self.files.pop(index)
        if chosen:
            self._refresh_files()

    def clear_files(self):
        if self.running:
            return
        self.files.clear()
        self._refresh_files()
        self._set_status("准备就绪 · 把 PDF 拖进来或点「添加文件」")

    def _on_drop(self, event):
        if self.running:
            return
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [str(event.data)]
        self._add_paths([str(p) for p in paths])

    def _on_tree_double(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.files)):
            return
        item = self.files[index]
        result = item.get("result")
        if result is not None:
            if result.ok:
                self._show_detail(item)
            elif not result.cancelled:
                messagebox.showerror(APP_TITLE,
                                     f"{item['path'].name}\n\n{result.error}")
        else:
            self._show_profile(index)

    def _show_profile(self, index: int):
        """还没压缩时，双击看体检报告。"""
        item = self.files[index]
        try:
            profile = analyze(item["path"])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"无法读取这个 PDF：{exc}")
            return
        window = tk.Toplevel(self.root, bg=WHITE)
        window.title(f"文档体检 · {item['path'].name}")
        window.geometry(
            f"560x520+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 80}")
        window.transient(self.root)

        body = tk.Frame(window, bg=WHITE)
        body.pack(fill="both", expand=True, padx=22, pady=18)
        tk.Label(body, text=item["path"].name, bg=WHITE, fg=TEXT,
                 font=self.fonts["card"], wraplength=500, justify="left",
                 anchor="w").pack(fill="x")
        tk.Label(body, text=f"{human_size(profile.size)} · {profile.pages} 页 · "
                            f"{(profile.version or 'PDF')} · "
                            f"{'像扫描件' if profile.scanned else '文字/矢量为主'}",
                 bg=WHITE, fg=MUTED, font=self.fonts["small"]).pack(anchor="w", pady=(2, 12))

        for label, size, note in profile.breakdown():
            row = tk.Frame(body, bg=WHITE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=WHITE, fg=TEXT, width=12, anchor="w",
                     font=self.fonts["body"]).pack(side="left")
            tk.Label(row, text=human_size(size), bg=WHITE, fg=TEXT, width=10, anchor="e",
                     font=self.fonts["body"]).pack(side="left")
            share = size / profile.size * 100 if profile.size else 0
            tk.Label(row, text=f"{share:5.1f}%", bg=WHITE, fg=MUTED, width=7, anchor="e",
                     font=self.fonts["small"]).pack(side="left")
            tk.Label(row, text=note, bg=WHITE, fg=FAINT, anchor="w",
                     font=self.fonts["small"]).pack(side="left", padx=(6, 0))

        if profile.images:
            tk.Label(body, text="最大的图像", bg=WHITE, fg=TEXT,
                     font=self.fonts["section"]).pack(anchor="w", pady=(14, 4))
            for image in profile.biggest_images[:6]:
                row = tk.Frame(body, bg=WHITE)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=f"{image.width}×{image.height}", bg=WHITE, fg=TEXT,
                         width=14, anchor="w", font=self.fonts["small"]).pack(side="left")
                tk.Label(row, text=f"{image.kind}", bg=WHITE, fg=MUTED, width=6, anchor="w",
                         font=self.fonts["small"]).pack(side="left")
                tk.Label(row, text=f"{image.dpi:.0f} DPI", bg=WHITE, fg=MUTED, width=9,
                         anchor="w", font=self.fonts["small"]).pack(side="left")
                tk.Label(row, text=human_size(image.size), bg=WHITE, fg=TEXT, width=10,
                         anchor="e", font=self.fonts["small"]).pack(side="left")
                tk.Label(row, text=image.filter, bg=WHITE, fg=FAINT, anchor="w",
                         font=self.fonts["small"]).pack(side="left", padx=(6, 0))

        self._ghost_button(window, "关闭", window.destroy, fg=MUTED).pack(pady=12)

    # ------------------------------------------------------------------ 运行
    def _output_for(self, src: Path) -> Path:
        if self.output_mode == "folder" and self.output_dir:
            target_dir = Path(self.output_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            candidate = target_dir / default_output_path(src, self.settings).name
        else:
            candidate = default_output_path(src, self.settings)
        if candidate.exists() and not self.overwrite:
            candidate = unique_output_path(candidate)
        if candidate.resolve() == src.resolve():
            candidate = unique_output_path(candidate)
        return candidate

    def start(self):
        if self.running:
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "先添加要压缩的 PDF 文件。")
            return
        if self.output_mode == "folder" and not self.output_dir:
            messagebox.showinfo(APP_TITLE, "还没有选择输出文件夹。")
            return
        removed = []
        if self.settings.strip_metadata:
            removed.append("元数据")
        if self.settings.strip_outlines:
            removed.append("书签")
        if self.settings.strip_attachments:
            removed.append("附件")
        if self.settings.strip_annotations:
            removed.append("注释")
        if self.settings.strip_structure:
            removed.append("结构树")
        if removed:
            if not messagebox.askokcancel(
                    APP_TITLE,
                    "当前设置会删除这些内容：\n\n" + "、".join(removed) +
                    "\n\n压缩后的文件里将不再包含它们。继续吗？"):
                return

        self.running = True
        self.cancel_token = CancelToken()
        self.queue = queue.Queue()
        self.started_at = time.time()
        self.current_index = -1
        for item in self.files:
            item.update(status="等待中", after=None, ratio=None, result=None, state="wait")
        self._refresh_files()
        self._set_running(True)
        self.progress.configure(value=0)
        self.percent_label.configure(text="0%")
        self._set_status("开始压缩…")

        settings = self.settings
        overwrite = self.overwrite
        output_mode = self.output_mode
        output_dir = self.output_dir
        files = list(self.files)
        token = self.cancel_token
        q = self.queue

        def worker():
            total = len(files)
            for index, item in enumerate(files):
                if token.cancelled:
                    q.put(("cancelled", index))
                    break
                q.put(("file_start", index))
                dst = self._output_for_worker(item["path"], settings, output_mode,
                                              output_dir, overwrite)

                def progress(fraction: float, text: str = "", _i=index, _n=total):
                    q.put(("progress", _i, fraction, text))

                result = compress_file(item["path"], dst, settings,
                                       progress=progress, cancel=token)
                q.put(("file_done", index, result))
                if token.cancelled:
                    break
            q.put(("all_done",))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self._tick_timer()
        self._poll_queue()

    @staticmethod
    def _output_for_worker(src: Path, settings, mode: str, folder: str, overwrite: bool) -> Path:
        if mode == "folder" and folder:
            target_dir = Path(folder)
            target_dir.mkdir(parents=True, exist_ok=True)
            candidate = target_dir / default_output_path(src, settings).name
        else:
            candidate = default_output_path(src, settings)
        if candidate.exists() and not overwrite:
            candidate = unique_output_path(candidate)
        if candidate.resolve() == src.resolve():
            candidate = unique_output_path(candidate)
        return candidate

    def stop(self):
        if not self.running or not self.cancel_token:
            return
        self.cancel_token.cancel()
        self._set_status("正在停止…")
        self.btn_stop.configure(state="disabled")

    def _poll_queue(self):
        if not self.queue:
            return
        try:
            while True:
                event = self.queue.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass
        if self.running:
            self.root.after(60, self._poll_queue)

    def _handle(self, event):
        kind = event[0]
        if kind == "file_start":
            index = event[1]
            self.current_index = index
            if 0 <= index < len(self.files):
                item = self.files[index]
                item["status"] = "压缩中…"
                item["state"] = "run"
                self._update_row(index)
                self._set_status(f"正在压缩：{item['path'].name}")
            base = index / max(1, len(self.files))
            self._show_progress(base * 100)
        elif kind == "progress":
            _, index, fraction, text = event
            if index == self.current_index:
                total = len(self.files)
                overall = (index + max(0.0, min(1.0, fraction))) / max(1, total)
                self._show_progress(overall * 100)
                if text:
                    self._set_status(f"正在压缩：{self.files[index]['path'].name} · {text}")
        elif kind == "file_done":
            _, index, result = event
            if 0 <= index < len(self.files):
                item = self.files[index]
                item["result"] = result
                if result.ok:
                    item["after"] = result.new_size
                    item["ratio"] = result.ratio
                    item["status"] = "已是最优" if result.kept_original else "已压缩"
                    item["state"] = "ok" if not result.kept_original else "warn"
                elif result.cancelled:
                    item["status"] = "已停止"
                    item["state"] = "wait"
                else:
                    item["status"] = "失败"
                    item["state"] = "err"
                self._update_row(index)
        elif kind == "cancelled":
            self._set_status("已停止")
        elif kind == "all_done":
            self._finish()

    def _update_row(self, index: int):
        item = self.files[index]
        iid = str(index)
        if not self.tree.exists(iid):
            return
        name = item["path"].name
        if len(name) > 46:
            name = name[:22] + "…" + name[-20:]
        after = human_size(item["after"]) if item["after"] else "—"
        ratio = percent_text(item.get("ratio"))
        self.tree.item(iid, values=(name, item["pages"] or "—",
                                    human_size(item["size"]), after, ratio,
                                    item["status"]),
                       tags=(item.get("state", "wait"),))

    def _show_progress(self, value: float):
        value = max(0.0, min(100.0, value))
        self.progress.configure(value=value)
        self.percent_label.configure(text=f"{value:.0f}%")

    def _finish(self):
        self.running = False
        if self.timer_job:
            self.root.after_cancel(self.timer_job)
            self.timer_job = None
        self._set_running(False)
        ok = [item for item in self.files if item.get("result") and item["result"].ok
              and not item["result"].cancelled]
        failed = [item for item in self.files if item.get("result")
                  and not item["result"].ok and not item["result"].cancelled]
        before = sum(item["result"].orig_size for item in ok)
        after = sum(item["result"].new_size for item in ok)
        if ok and before:
            self._show_progress(100)
            self._set_status(
                f"完成 · {len(ok)} 个文件 {human_size(before)} → {human_size(after)}"
                f"（减少 {(1 - after / before) * 100:.1f}%）"
                + (f" · {len(failed)} 个失败" if failed else ""))
            self.btn_open.configure(state="normal")
        elif failed:
            self._set_status(f"完成，但有 {len(failed)} 个文件失败")
        else:
            self._set_status("已停止")
        self._refresh_files()

    def _set_running(self, running: bool):
        self.running = running
        state = "disabled" if running else "normal"
        for button in (self.btn_add, self.btn_folder, self.btn_clear):
            button.configure(state=state)
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_start.configure(text="压缩中…" if running else "开始压缩")
        for card in self.cards.values():
            card.configure(cursor="arrow" if running else "hand2")

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    def _tick_timer(self):
        if not self.running:
            return
        elapsed = time.time() - self.started_at
        minutes, seconds = divmod(int(elapsed), 60)
        self.timer_label.configure(text=f"{minutes}:{seconds:02d}")
        self.timer_job = self.root.after(1000, self._tick_timer)

    # ------------------------------------------------------------------ 详情
    def _show_detail(self, item: dict):
        result = item.get("result")
        if not result:
            return
        window = tk.Toplevel(self.root, bg=WHITE)
        window.title(f"压缩详情 · {item['path'].name}")
        window.geometry(
            f"620x600+{self.root.winfo_rootx() + 140}+{self.root.winfo_rooty() + 70}")
        window.transient(self.root)

        header = tk.Frame(window, bg=WHITE)
        header.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(header, text=item["path"].name, bg=WHITE, fg=TEXT,
                 font=self.fonts["card"], wraplength=560, justify="left",
                 anchor="w").pack(fill="x")
        tk.Label(header, text=f"{human_size(result.orig_size)} → {human_size(result.new_size)}"
                             f" · 减少 {result.ratio * 100:.1f}%",
                 bg=WHITE, fg=ACCENT, font=self.fonts["body"]).pack(anchor="w", pady=(3, 0))

        body = tk.Frame(window, bg=WHITE)
        body.pack(fill="both", expand=True, padx=22, pady=6)

        for label, value in result.detail_rows():
            row = tk.Frame(body, bg=WHITE)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=WHITE, fg=MUTED, width=12, anchor="w",
                     font=self.fonts["small"]).pack(side="left")
            tk.Label(row, text=value, bg=WHITE, fg=TEXT, anchor="w", justify="left",
                     wraplength=450, font=self.fonts["small"]).pack(side="left", fill="x")

        records = []
        if result.images:
            records = sorted(result.images.records, key=lambda r: r.before, reverse=True)[:6]
        if records:
            tk.Label(body, text="图像明细", bg=WHITE, fg=TEXT,
                     font=self.fonts["section"]).pack(anchor="w", pady=(14, 4))
            table = ttk.Treeview(body, style="Mini.Treeview", show="headings", height=min(6, len(records)),
                                 columns=("size", "kind", "q", "before", "after", "psnr"))
            for key, label, width, anchor in (
                    ("size", "尺寸", 130, "w"), ("kind", "类型", 62, "w"),
                    ("q", "品质", 52, "w"), ("before", "压缩前", 84, "e"),
                    ("after", "压缩后", 84, "e"), ("psnr", "画质", 68, "e")):
                table.heading(key, text=label, anchor=anchor)
                table.column(key, width=width, anchor=anchor, stretch=(key == "size"))
            for record in records:
                table.insert("", "end", values=(
                    f"{record.width}×{record.height}",
                    record.kind,
                    record.quality if record.kind != "mono" else "G4",
                    human_size(record.before),
                    human_size(record.after),
                    f"{record.psnr:.0f} dB" if record.psnr else "—",
                ))
            table.pack(fill="x", pady=(0, 4))

        if result.warnings:
            tk.Label(body, text="提示", bg=WHITE, fg=TEXT,
                     font=self.fonts["section"]).pack(anchor="w", pady=(14, 2))
            for warning in result.warnings[:4]:
                tk.Label(body, text="· " + warning, bg=WHITE, fg=MUTED,
                         font=self.fonts["small"], wraplength=560,
                         justify="left", anchor="w").pack(fill="x")

        actions = tk.Frame(window, bg=WHITE)
        actions.pack(fill="x", padx=22, pady=16)
        dst = Path(result.dst) if result.dst else None
        if dst and dst.exists():
            self._primary_button(actions, "打开压缩后的 PDF",
                                 lambda: open_path(dst)).pack(side="left")
            self._ghost_button(actions, "打开所在文件夹",
                               lambda: reveal_path(dst), fg=MUTED).pack(side="left", padx=8)
        self._ghost_button(actions, "关闭", window.destroy, fg=MUTED).pack(side="right")

    def show_about(self):
        window = tk.Toplevel(self.root, bg=WHITE)
        window.title("压缩原理")
        window.geometry(
            f"640x560+{self.root.winfo_rootx() + 120}+{self.root.winfo_rooty() + 60}")
        window.transient(self.root)
        text = tk.Text(window, bg=WHITE, fg=TEXT, relief="flat", wrap="word",
                       font=self.fonts["body"], padx=24, pady=20, bd=0,
                       highlightthickness=0, spacing1=2, spacing3=4)
        text.pack(fill="both", expand=True)
        content = (
            f"{APP_TITLE} v{__version__}\n\n"
            "压缩分四步，每一档预设就是这四步的不同组合：\n\n"
            "1. 体检\n"
            "   先算清楚体积花在哪：图像多少、字体多少、内容流多少、元数据/缩略图/附件多少。\n"
            "   图像的有效分辨率按「像素数 ÷ 页面上的显示尺寸」算，不是按文件里标的 DPI。\n\n"
            "2. 字体子集化\n"
            "   内嵌字体只保留文档真正用到的字形。一份全量内嵌的中文字体十几兆，"
            "子集化后常常只剩几十 KB，这是中文 PDF 缩小体积最狠的一招。\n\n"
            "3. 图像重压缩\n"
            "   · 只有有效分辨率超过目标的 1.5 倍才降采样，避免把本来就不大的图压糊；\n"
            "   · 彩色/灰度用 JPEG，单色用 CCITT G4（和 ZIP 比较取更小的）；\n"
            "   · 每张图压完都算一次 PSNR，低于闸门就提高品质重来，仍不达标就保留原图；\n"
            "   · 替换的是图像流本身，版面矩阵不动，所以位置、大小、裁剪都不会变。\n\n"
            "4. 结构清理\n"
            "   按设置删除元数据/缩略图/附件/书签/注释/结构树，取消嵌入标准 14 字体，"
            "重新压缩 Flate 流，并可选对象流压缩和线性化。\n\n"
            "最后还有一道自检：把源文件和输出文件各渲染几页，比对页数、墨量和 PSNR，"
            "确认没有丢页、白页或整页变色，才会落盘。\n\n"
            "全程本地运行，不联网、不上传。\n\n"
            "本项目仅供学习、研究与技术交流使用，不是商业产品，也不构成任何服务承诺；"
            "完整条款见随附的 DISCLAIMER.md。"
        )
        text.insert("1.0", content)
        text.configure(state="disabled")
        self._ghost_button(window, "关闭", window.destroy, fg=MUTED).pack(pady=(0, 14))

    def show_disclaimer_doc(self):
        """只读查看免责声明（首次启动时是必须确认的版本）。"""
        show_disclaimer(self.root, require_consent=False)

    # ------------------------------------------------------------------ 其他
    def open_output(self):
        ok = [item for item in self.files
              if item.get("result") and item["result"].ok and item["result"].dst]
        if not ok:
            return
        first = Path(ok[0]["result"].dst)
        if len(ok) == 1:
            reveal_path(first)
        else:
            open_path(first.parent)

    def _on_close(self):
        if self.running:
            if not messagebox.askokcancel(APP_TITLE, "压缩还在进行，确定要退出吗？"):
                return
            if self.cancel_token:
                self.cancel_token.cancel()
        self._save_settings()
        self.root.destroy()


def _apply_window_icon(window) -> None:
    ico = resource_path(os.path.join("assets", "icon.ico"))
    png = resource_path(os.path.join("assets", "icon.png"))
    try:
        if IS_WINDOWS and os.path.exists(ico):
            window.iconbitmap(default=ico)
            return
        if os.path.exists(png):
            image = Image.open(png).convert("RGBA").resize((64, 64), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            window._disclaimer_icon = photo  # 防 GC
            window.iconphoto(False, photo)
    except Exception:
        pass


def show_disclaimer(parent, require_consent: bool = True) -> bool:
    """免责声明窗口。

    require_consent=True（首次启动）时必须完整输入 CONSENT_PHRASE 才能点「同意」，
    关窗/退出返回 False；False 时只读展示，返回 True。
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"{APP_TITLE} · 免责声明")
    dialog.configure(bg=BG)
    dialog.resizable(False, False)
    width, height = 720, 600 if require_consent else 540
    screen_w, screen_h = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
    dialog.geometry(f"{width}x{height}"
                    f"+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")
    _apply_window_icon(dialog)

    tk.Label(dialog, text="免责声明", bg=BG, fg=TEXT,
             font=(FONT_FAMILY, 16, "bold")).pack(anchor="w", padx=22, pady=(16, 0))
    tk.Label(dialog,
             text="本项目仅供学习、研究与技术交流，不是商业产品。请阅读后输入下面这句话以示知悉。"
             if require_consent else
             "本项目仅供学习、研究与技术交流，不是商业产品。完整条款见随附的 DISCLAIMER.md。",
             bg=BG, fg=MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=22, pady=(2, 10))

    box = tk.Frame(dialog, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
    box.pack(fill="both", expand=True, padx=22)
    body = tk.Text(box, wrap="word", bd=0, padx=14, pady=12, bg=WHITE, fg=TEXT,
                   font=(FONT_FAMILY, 9), height=8, cursor="arrow",
                   highlightthickness=0)
    body.insert("1.0", DISCLAIMER_SUMMARY)
    body.configure(state="disabled")
    body.pack(fill="both", expand=True)

    row = tk.Frame(dialog, bg=BG)
    result = {"ok": not require_consent}

    def close(value: bool):
        result["ok"] = value
        dialog.destroy()

    if require_consent:
        tk.Label(dialog, text="为确认你已知悉，请完整输入下面这句话：", bg=BG, fg=TEXT,
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=22, pady=(12, 0))
        tk.Label(dialog, text=CONSENT_PHRASE, bg=WHITE, fg=MUTED,
                 font=(FONT_FAMILY, 9), justify="left", wraplength=600,
                 padx=12, pady=8, anchor="w", highlightthickness=1,
                 highlightbackground=BORDER).pack(fill="x", padx=22, pady=(4, 8))

        typed = tk.StringVar()
        entry = tk.Entry(dialog, textvariable=typed, font=(FONT_FAMILY, 10), bd=0,
                         bg=WHITE, fg=TEXT, insertbackground=TEXT, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(fill="x", padx=22, ipady=7)

        row.pack(fill="x", padx=22, pady=(12, 16))

        tk.Button(row, text="退出", command=lambda: close(False), font=(FONT_FAMILY, 10),
                  bd=0, relief="flat", padx=22, pady=8, bg=WHITE, fg=MUTED,
                  activebackground=WHITE, highlightthickness=1,
                  highlightbackground=BORDER, cursor="hand2").pack(side="left")

        def copy_phrase():
            try:
                dialog.clipboard_clear()
                dialog.clipboard_append(CONSENT_PHRASE)
            except Exception:
                pass
            entry.focus_set()

        tk.Button(row, text="复制这句话", command=copy_phrase, font=(FONT_FAMILY, 9),
                  bd=0, relief="flat", padx=14, pady=8, bg=WHITE, fg=ACCENT,
                  activebackground=WHITE, cursor="hand2").pack(side="left", padx=8)

        agree = tk.Button(row, text="同意", font=(FONT_FAMILY, 10, "bold"), bd=0,
                          relief="flat", padx=28, pady=8, bg=BORDER, fg=FAINT,
                          state="disabled")
        agree.pack(side="right")

        def refresh(*_args):
            ready = _consent_matches(typed.get())
            agree.configure(state="normal" if ready else "disabled",
                            bg=ACCENT if ready else BORDER,
                            fg="white" if ready else FAINT,
                            cursor="hand2" if ready else "arrow",
                            command=(lambda: close(True)) if ready else None)
            agree.configure(activebackground=ACCENT_DARK)

        typed.trace_add("write", refresh)
        entry.bind("<Return>", lambda _e: close(True) if _consent_matches(typed.get()) else None)
        entry.focus_set()
    else:
        row.pack(fill="x", padx=22, pady=(12, 16))
        doc = disclaimer_doc_path()
        if doc is not None:
            tk.Button(row, text="打开 DISCLAIMER.md",
                      command=lambda: open_path(doc), font=(FONT_FAMILY, 9), bd=0,
                      relief="flat", padx=14, pady=8, bg=WHITE, fg=ACCENT,
                      activebackground=WHITE, highlightthickness=1,
                      highlightbackground=BORDER,
                      cursor="hand2").pack(side="left")
        tk.Button(row, text="关闭", command=lambda: close(True), font=(FONT_FAMILY, 10),
                  bd=0, relief="flat", padx=28, pady=8, bg=ACCENT, fg="white",
                  activebackground=ACCENT_DARK, cursor="hand2").pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False if require_consent else True))
    dialog.bind("<Escape>", lambda _e: close(False if require_consent else True))

    dialog.transient(parent)
    dialog.update_idletasks()
    try:
        dialog.grab_set()
    except Exception:
        pass
    parent.wait_window(dialog)
    return bool(result["ok"])


def main() -> int:
    if "--cli" in sys.argv:
        index = sys.argv.index("--cli")
        from cli import main as cli_main

        return cli_main(sys.argv[index + 1:])

    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    app = App(root)
    if not disclaimer_accepted():
        if not show_disclaimer(root):
            try:
                root.destroy()
            except Exception:
                pass
            return 0
        app.disclaimer = DISCLAIMER_VERSION
        record_disclaimer_acceptance()
    if HAS_DND:
        try:
            root.drop_target_register(DND_FILES)
            root.dnd_bind("<<Drop>>", app._on_drop)
        except Exception:
            pass
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
