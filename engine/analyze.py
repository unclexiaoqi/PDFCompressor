"""用 PyMuPDF 做「压缩前体检」：页面、图像、字体、元数据、结构。

分析结果既用于给用户看（体积都花在哪），也用于驱动图像重压缩计划。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_REF = re.compile(r"^\s*(\d+)\s+(\d+)\s+R\s*$")

# PDF 标准 14 字体：阅读器一般自带，可以不嵌入
STANDARD_14 = {
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldItalic",
    "Symbol", "ZapfDingbats",
}


def base_font_name(name: str) -> str:
    """去掉 PDF 的 ABCDEF+ 子集前缀。"""
    return _SUBSET_PREFIX.sub("", (name or "").strip())


def _stream_len(doc: pymupdf.Document, xref: int, depth: int = 0) -> int:
    """读取流对象编码后的字节数，避免把整个流读进内存。"""
    if xref <= 0 or depth > 4:
        return 0
    try:
        kind, value = doc.xref_get_key(xref, "Length")
    except (ValueError, RuntimeError):
        return 0
    if kind in ("int", "real"):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
    match = _REF.match(value or "")
    if match:
        return _stream_len(doc, int(match.group(1)), depth + 1)
    try:
        return len(doc.xref_stream_raw(xref))
    except (ValueError, RuntimeError):
        return 0


def _ref(doc: pymupdf.Document, xref: int, key: str) -> int | None:
    try:
        kind, value = doc.xref_get_key(xref, key)
    except (ValueError, RuntimeError):
        return None
    if kind == "xref":
        match = _REF.match(value or "")
        return int(match.group(1)) if match else None
    match = _REF.match(value or "")
    if match and kind != "array":
        return int(match.group(1))
    if kind == "array":  # 例如 DescendantFonts [17 0 R]
        inner = re.findall(r"(\d+)\s+0\s+R", value or "")
        return int(inner[0]) if inner else None
    return None


@dataclass
class ImageEntry:
    """一个图像 XObject 的体检结果。"""

    xref: int
    width: int
    height: int
    bpc: int
    colorspace: str
    filter: str
    size: int
    dpi: float               # 所有使用位置中最小的有效分辨率（最保守）
    pages: tuple[int, ...] = ()
    has_smask: bool = False
    has_mask: bool = False
    inline: bool = False
    kind: str = "color"      # color | gray | mono

    @property
    def label(self) -> str:
        return f"{self.width}×{self.height} · {self.bpc}bit"


@dataclass
class FontEntry:
    xref: int
    name: str
    base: str
    ftype: str
    embedded: bool
    subset: bool
    size: int
    pages: tuple[int, ...] = ()

    @property
    def display(self) -> str:
        return self.base or self.name or "(未命名)"


@dataclass
class PdfProfile:
    """文档体检报告。"""

    path: Path
    size: int
    pages: int = 0
    version: str = ""
    encrypted: bool = False
    needs_password: bool = False
    repaired: bool = False

    images: list[ImageEntry] = field(default_factory=list)
    fonts: list[FontEntry] = field(default_factory=list)

    image_bytes: int = 0
    font_bytes: int = 0
    content_bytes: int = 0
    metadata_bytes: int = 0
    thumbnail_bytes: int = 0
    attachment_bytes: int = 0
    other_bytes: int = 0

    annotations: int = 0
    form_fields: int = 0
    outlines: int = 0
    attachments: int = 0
    has_structure_tree: bool = False

    text_chars: int = 0
    image_coverage: float = 0.0   # 页面上被图像覆盖的面积比例（抽样）
    page_size: tuple[float, float] = (0.0, 0.0)

    # ------------------------------------------------------------------
    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def embedded_font_count(self) -> int:
        return sum(1 for f in self.fonts if f.embedded)

    @property
    def scanned(self) -> bool:
        """是否像扫描件：文字很少、图像铺满页面。"""
        if not self.pages:
            return False
        return self.image_coverage >= 0.55 and self.text_chars < 120 * self.pages

    def breakdown(self) -> list[tuple[str, int, str]]:
        """[(名称, 字节, 说明)]，用于界面里的体积构成。"""
        rows: list[tuple[str, int, str]] = []
        if self.images:
            rows.append(("图像", self.image_bytes, f"{len(self.images)} 个"))
        if self.fonts:
            rows.append(("字体", self.font_bytes, f"{self.embedded_font_count} 个已嵌入"))
        if self.content_bytes:
            rows.append(("页面内容流", self.content_bytes, "文字/矢量绘图指令"))
        if self.metadata_bytes:
            rows.append(("元数据", self.metadata_bytes, "XMP / 文档信息"))
        if self.thumbnail_bytes:
            rows.append(("缩略图", self.thumbnail_bytes, ""))
        if self.attachment_bytes:
            rows.append(("附件", self.attachment_bytes, f"{self.attachments} 个"))
        if self.other_bytes:
            rows.append(("交叉引用表等", self.other_bytes, ""))
        return rows

    @property
    def biggest_images(self) -> list[ImageEntry]:
        return sorted(self.images, key=lambda e: e.size, reverse=True)[:8]


def classify_image(bpc: int, colorspace: str, ncolors: int) -> str:
    """先按色深粗分，执行阶段还会用调色板再确认一次。"""
    cs = (colorspace or "").lower()
    if bpc == 1 and "indexed" not in cs:
        return "mono"
    if "gray" in cs or (ncolors == 1 and "iccbased" not in cs):
        return "gray" if bpc <= 8 else "gray"
    if "indexed" in cs:
        return "color"
    if ncolors in (1, 2):
        return "gray"
    return "color"


def _sample_pages(total: int, limit: int = 8) -> list[int]:
    if total <= limit:
        return list(range(total))
    step = max(1, total // limit)
    picks = list(range(0, total, step))[:limit]
    if total - 1 not in picks:
        picks[-1] = total - 1
    return sorted(set(picks))


def analyze(path: str | Path, progress=None) -> PdfProfile:
    """打开 PDF 并收集体检信息；加密文件只报告基本信息。"""
    path = Path(path)
    profile = PdfProfile(path=path, size=path.stat().st_size if path.exists() else 0)
    doc = pymupdf.open(str(path))
    try:
        profile.version = (doc.metadata or {}).get("format", "") or ""
        profile.encrypted = bool(doc.is_encrypted)
        profile.needs_password = bool(doc.needs_pass)
        profile.repaired = bool(getattr(doc, "is_repaired", False))
        if profile.needs_password:
            return profile

        profile.pages = doc.page_count
        if not doc.page_count:
            return profile

        page0 = doc[0]
        profile.page_size = (round(page0.rect.width, 1), round(page0.rect.height, 1))

        # ---------- 图像 ----------
        images: dict[int, ImageEntry] = {}
        coverage_acc = 0.0
        sampled = _sample_pages(doc.page_count)
        for pno in range(doc.page_count):
            page = doc[pno]
            page_area = max(1.0, page.rect.width * page.rect.height)
            covered = 0.0
            try:
                infos = page.get_image_info(xrefs=True)
            except Exception:
                infos = []
            for info in infos:
                bbox = info.get("bbox") or (0, 0, 0, 0)
                covered += max(0.0, (bbox[2] - bbox[0])) * max(0.0, (bbox[3] - bbox[1]))
                xref = int(info.get("xref") or 0)
                if not xref:
                    continue
                w = int(info.get("width") or 0)
                h = int(info.get("height") or 0)
                if w <= 0 or h <= 0:
                    continue
                dpi = _effective_dpi(info, w, h)
                entry = images.get(xref)
                if entry is None:
                    bpc = int(info.get("bpc") or 8)
                    cs_name = info.get("cs-name") or ""
                    ncolors = int(info.get("colorspace") or 0)
                    entry = ImageEntry(
                        xref=xref, width=w, height=h, bpc=bpc,
                        colorspace=cs_name, filter=_image_filter(doc, xref),
                        size=_stream_len(doc, xref), dpi=dpi,
                        pages=(pno,), has_smask=bool(info.get("has-mask")),
                        kind=classify_image(bpc, cs_name, ncolors),
                    )
                    images[xref] = entry
                else:
                    entry.dpi = min(entry.dpi, dpi)
                    if pno not in entry.pages:
                        entry.pages = entry.pages + (pno,)
            if pno in sampled:
                coverage_acc += min(1.0, covered / page_area)
            if progress and pno % 16 == 0:
                progress(0.15 + 0.5 * pno / max(1, doc.page_count), f"扫描第 {pno + 1}/{doc.page_count} 页")
        profile.images = list(images.values())
        profile.image_bytes = sum(e.size for e in profile.images)
        profile.image_coverage = coverage_acc / max(1, len(sampled))

        # ---------- 字体 ----------
        profile.fonts, profile.font_bytes = _collect_fonts(doc)

        # ---------- 内容流 ----------
        content = 0
        for pno in range(doc.page_count):
            try:
                for xref in doc[pno].get_contents() or ():
                    content += _stream_len(doc, xref)
            except Exception:
                continue
        profile.content_bytes = content

        # ---------- 元数据 / 缩略图 / 附件 ----------
        try:
            profile.metadata_bytes = len(doc.get_xml_metadata().encode("utf-8"))
        except Exception:
            profile.metadata_bytes = 0
        info = doc.metadata or {}
        profile.metadata_bytes += sum(len(str(v).encode("utf-8")) for v in info.values() if isinstance(v, str))

        thumbs = 0
        for pno in range(doc.page_count):
            xref = _ref(doc, doc[pno].xref, "Thumb")
            if xref:
                thumbs += _stream_len(doc, xref)
        profile.thumbnail_bytes = thumbs

        try:
            profile.attachments = doc.embfile_count()
            att = 0
            for name in doc.embfile_names():
                try:
                    att += int((doc.embfile_info(name) or {}).get("size") or 0)
                except Exception:
                    att += len(doc.embfile_get(name) or b"")
            profile.attachment_bytes = att
        except Exception:
            profile.attachments = 0

        # ---------- 结构 ----------
        catalog = doc.pdf_catalog()
        try:
            profile.has_structure_tree = doc.xref_get_key(catalog, "StructTreeRoot")[1] != "null"
        except Exception:
            profile.has_structure_tree = False

        annotations = 0
        forms = 0
        for pno in range(doc.page_count):
            page = doc[pno]
            try:
                annotations += len(list(page.annots() or []))
            except Exception:
                pass
            if page.first_widget:
                forms += 1
        profile.annotations = annotations
        profile.form_fields = forms
        try:
            profile.outlines = len(doc.get_toc(simple=True))
        except Exception:
            profile.outlines = 0

        # ---------- 文本量（抽样） ----------
        chars = 0
        for pno in sampled:
            try:
                chars += len(doc[pno].get_text("text"))
            except Exception:
                continue
        profile.text_chars = int(chars * doc.page_count / max(1, len(sampled)))

        profile.other_bytes = max(
            0, profile.size - profile.image_bytes - profile.font_bytes - profile.content_bytes
            - profile.metadata_bytes - profile.thumbnail_bytes - profile.attachment_bytes
        )
        return profile
    finally:
        doc.close()


def quick_info(path: str | Path) -> tuple[int, int, str]:
    """轻量读取：(页数, 字节数, 版本号)，用于文件列表，不做完整体检。"""
    path = Path(path)
    size = path.stat().st_size
    doc = pymupdf.open(str(path))
    try:
        version = (doc.metadata or {}).get("format", "") or ""
        return int(doc.page_count), size, version
    finally:
        doc.close()


def _effective_dpi(info: dict, width: int, height: int) -> float:
    """图像实际显示分辨率：像素尺寸 / 页面上的显示尺寸。

    注意不能用 PyMuPDF 的 xres/yres —— 那是图像自带的标称分辨率，
    图像被缩放后并不会跟着变。
    """
    a, b, c, d, _e, _f = (float(v) for v in (info.get("transform") or (1, 0, 0, 1, 0, 0)))
    span_x = math.hypot(a, b)
    span_y = math.hypot(c, d)
    candidates = []
    if span_x > 0.01:
        candidates.append(width * 72.0 / span_x)
    if span_y > 0.01:
        candidates.append(height * 72.0 / span_y)
    if not candidates:
        bbox = info.get("bbox") or (0, 0, 0, 0)
        w_pt = max(0.01, bbox[2] - bbox[0])
        h_pt = max(0.01, bbox[3] - bbox[1])
        candidates = [width * 72.0 / w_pt, height * 72.0 / h_pt]
    dpi = min(candidates)
    if dpi <= 1 or dpi > 20000:
        return 72.0
    return dpi


def _image_filter(doc: pymupdf.Document, xref: int) -> str:
    try:
        kind, value = doc.xref_get_key(xref, "Filter")
    except (ValueError, RuntimeError):
        return "?"
    if kind == "name":
        return value.lstrip("/")
    if kind == "array":
        names = re.findall(r"/([A-Za-z0-9]+)", value or "")
        return "+".join(names)
    return value or "?"


def _collect_fonts(doc: pymupdf.Document) -> tuple[list[FontEntry], int]:
    fonts: dict[tuple[int, str], FontEntry] = {}
    for pno in range(doc.page_count):
        try:
            entries = doc.get_page_fonts(pno)
        except Exception:
            continue
        for item in entries:
            try:
                xref, ext, ftype, basefont, name, _enc = item[:6]
            except (TypeError, ValueError):
                continue
            key = (int(xref), basefont or name)
            entry = fonts.get(key)
            if entry is None:
                entry = FontEntry(
                    xref=int(xref), name=name or "", base=basefont or "",
                    ftype=ftype or "", embedded=bool(ext),
                    subset=bool(_SUBSET_PREFIX.match(basefont or "")),
                    size=0, pages=(pno,),
                )
                fonts[key] = entry
            elif pno not in entry.pages:
                entry.pages = entry.pages + (pno,)

    total = 0
    for entry in fonts.values():
        entry.size = _font_stream_size(doc, entry.xref)
        total += entry.size
    return list(fonts.values()), total


def _font_stream_size(doc: pymupdf.Document, font_xref: int) -> int:
    if font_xref <= 0:
        return 0
    sizes = 0
    desc = None
    try:
        kind, value = doc.xref_get_key(font_xref, "DescendantFonts")
    except (ValueError, RuntimeError):
        value = "null"
    if value and value != "null":
        cid = _ref(doc, font_xref, "DescendantFonts")
        desc = _ref(doc, cid, "FontDescriptor") if cid else None
    if desc is None:
        desc = _ref(doc, font_xref, "FontDescriptor")
    if desc is None:
        return 0
    for key in ("FontFile", "FontFile2", "FontFile3"):
        xref = _ref(doc, desc, key)
        if xref:
            sizes += _stream_len(doc, xref)
    return sizes


def font_descriptor_xref(doc: pymupdf.Document, font_xref: int) -> int | None:
    """字体对象的 FontDescriptor（Type0 走 DescendantFonts）。"""
    kind, value = doc.xref_get_key(font_xref, "DescendantFonts")
    if kind == "array" or (value and value != "null"):
        cid = _ref(doc, font_xref, "DescendantFonts")
        if cid:
            desc = _ref(doc, cid, "FontDescriptor")
            if desc:
                return desc
    return _ref(doc, font_xref, "FontDescriptor")


__all__ = [
    "PdfProfile", "ImageEntry", "FontEntry", "analyze", "quick_info",
    "classify_image", "base_font_name", "STANDARD_14", "font_descriptor_xref",
]
