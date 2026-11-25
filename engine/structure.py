"""结构层面的优化：放弃对象、取消嵌入标准字体、对象流、线性化。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pikepdf

from .analyze import STANDARD_14, base_font_name
from .settings import Settings


@dataclass
class StructureStats:
    removed_bytes: dict[str, int] = field(default_factory=dict)
    removed_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    resources_removed: int = 0

    @property
    def saved(self) -> int:
        return sum(self.removed_bytes.values())

    def note(self, key: str, size: int = 0, count: int = 1) -> None:
        self.removed_bytes[key] = self.removed_bytes.get(key, 0) + max(0, size)
        self.removed_counts[key] = self.removed_counts.get(key, 0) + count


def _raw_len(obj) -> int:
    """尽量取流对象的编码长度，失败不影响清理。"""
    try:
        if isinstance(obj, pikepdf.Stream):
            try:
                return len(obj.read_raw_bytes())
            except Exception:
                length = obj.get("/Length")
                try:
                    return int(length)
                except (TypeError, ValueError):
                    return 0
    except Exception:
        pass
    return 0


def optimize_structure(pdf: pikepdf.Pdf, settings: Settings) -> StructureStats:
    stats = StructureStats()

    # ---------- 缩略图 ----------
    if settings.strip_thumbnails:
        for page in pdf.pages:
            try:
                thumb = page.obj.get("/Thumb")
            except Exception:
                thumb = None
            if thumb is not None:
                stats.note("缩略图", _raw_len(thumb))
                try:
                    del page.obj["/Thumb"]
                except (KeyError, TypeError):
                    pass

    # ---------- 元数据 ----------
    if settings.strip_metadata:
        try:
            meta = pdf.Root.get("/Metadata")
            if meta is not None:
                stats.note("元数据", _raw_len(meta))
                del pdf.Root["/Metadata"]
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            for key in list(pdf.docinfo.keys()):
                stats.removed_counts["元数据项"] = stats.removed_counts.get("元数据项", 0) + 1
                del pdf.docinfo[key]
            pdf.docinfo["/Producer"] = "PDFCompressor"
            pdf.docinfo["/Creator"] = "PDFCompressor"
        except Exception as exc:
            stats.warnings.append(f"清理文档信息失败：{exc}")

    # ---------- 附件 ----------
    if settings.strip_attachments:
        try:
            names = list(pdf.attachments.keys())
        except Exception:
            names = []
        for name in names:
            size = 0
            try:
                spec = pdf.attachments[name]
                ef = spec.get("/EF") or {}
                for key in ef.keys():
                    size += _raw_len(ef[key])
            except Exception:
                size = 0
            try:
                del pdf.attachments[name]
                stats.note("附件", size)
            except Exception as exc:
                stats.warnings.append(f"删除附件 {name} 失败：{exc}")

    # ---------- 书签 ----------
    if settings.strip_outlines:
        try:
            outlines = pdf.Root.get("/Outlines")
            if outlines is not None:
                count = 0
                try:
                    item = outlines.get("/First")
                    while item is not None and count < 10000:
                        count += 1
                        item = item.get("/Next")
                except Exception:
                    pass
                stats.note("书签", 0, max(1, count))
                del pdf.Root["/Outlines"]
                if str(pdf.Root.get("/PageMode", "")) == "/UseOutlines":
                    pdf.Root["/PageMode"] = pikepdf.Name("/UseNone")
        except (KeyError, TypeError, AttributeError):
            pass

    # ---------- 注释 / 表单 ----------
    if settings.strip_annotations:
        removed = 0
        for page in pdf.pages:
            try:
                annots = page.obj.get("/Annots")
            except Exception:
                continue
            if annots is None:
                continue
            total = 0
            try:
                for annot in annots:
                    total += _raw_len(annot.get("/AP"))
                    removed += 1
            except Exception:
                pass
            try:
                del page.obj["/Annots"]
                stats.note("注释/表单", total)
            except (KeyError, TypeError):
                pass
        if removed:
            try:
                if "/AcroForm" in pdf.Root:
                    del pdf.Root["/AcroForm"]
            except (KeyError, TypeError):
                pass

    # ---------- 结构树 ----------
    if settings.strip_structure:
        try:
            if "/StructTreeRoot" in pdf.Root:
                stats.note("结构树", 0, 1)
                del pdf.Root["/StructTreeRoot"]
            if "/MarkInfo" in pdf.Root:
                del pdf.Root["/MarkInfo"]
        except (KeyError, TypeError):
            pass

    # ---------- 页面标签 ----------
    if settings.strip_page_labels:
        try:
            if "/PageLabels" in pdf.Root:
                labels = pdf.Root.get("/PageLabels")
                count = 0
                try:
                    count = len(labels.get("/Nums") or [])
                except Exception:
                    pass
                stats.note("页面标签", 0, max(1, count))
                del pdf.Root["/PageLabels"]
        except (KeyError, TypeError):
            pass

    # ---------- 取消嵌入标准 14 字体 ----------
    if settings.unembed_base14:
        _unembed_standard_fonts(pdf, stats)

    # ---------- 未使用的资源 ----------
    try:
        before = _count_objects(pdf)
        pdf.remove_unreferenced_resources()
        stats.resources_removed = max(0, before - _count_objects(pdf))
    except Exception as exc:
        stats.warnings.append(f"清理未使用资源失败：{exc}")

    return stats


def _count_objects(pdf: pikepdf.Pdf) -> int:
    try:
        return sum(1 for _ in pdf.objects)
    except Exception:
        return 0


def _unembed_standard_fonts(pdf: pikepdf.Pdf, stats: StructureStats) -> None:
    """把 Helvetica/Times/Courier 等标准 14 字体的嵌入数据删掉，只留度量。

    阅读器自带这些字体的渲染数据，宽度表仍在，所以版面不会变。
    """
    seen: set[tuple[int, int]] = set()
    for descriptor, base in _iter_font_descriptors(pdf):
        try:
            key = descriptor.objgen
        except Exception:
            key = None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        name = base_font_name(base)
        if name not in STANDARD_14:
            continue
        removed = 0
        for field_name in ("/FontFile", "/FontFile2", "/FontFile3"):
            try:
                if field_name in descriptor:
                    removed += _raw_len(descriptor.get(field_name))
                    del descriptor[field_name]
            except (KeyError, TypeError):
                continue
        if removed:
            stats.note("标准 14 字体嵌入", removed)


def _iter_font_descriptors(pdf: pikepdf.Pdf):
    """遍历页面资源中的字体，产出 (FontDescriptor, BaseFont)。"""
    try:
        pages = list(pdf.pages)
    except Exception:
        return
    for page in pages:
        try:
            resources = page.Resources
        except Exception:
            continue
        if resources is None:
            continue
        for descriptor, base in _fonts_in_resources(resources):
            yield descriptor, base


def _fonts_in_resources(resources, depth: int = 0):
    if depth > 2 or resources is None:
        return
    try:
        fonts = resources.get("/Font")
    except Exception:
        fonts = None
    if fonts is not None:
        try:
            keys = list(fonts.keys())
        except Exception:
            keys = []
        for key in keys:
            try:
                font = fonts[key]
            except Exception:
                continue
            base = str(font.get("/BaseFont", ""))
            descriptor = None
            try:
                descendants = font.get("/DescendantFonts")
                if descendants is not None and len(descendants):
                    descriptor = descendants[0].get("/FontDescriptor")
            except Exception:
                descriptor = None
            if descriptor is None:
                try:
                    descriptor = font.get("/FontDescriptor")
                except Exception:
                    descriptor = None
            if descriptor is not None:
                yield descriptor, base
    try:
        xobjects = resources.get("/XObject")
    except Exception:
        xobjects = None
    if xobjects is not None:
        try:
            names = list(xobjects.keys())
        except Exception:
            names = []
        for name in names:
            try:
                xobj = xobjects[name]
            except Exception:
                continue
            if str(xobj.get("/Subtype", "")) != "/Form":
                continue
            try:
                inner = xobj.get("/Resources")
            except Exception:
                inner = None
            for item in _fonts_in_resources(inner, depth + 1):
                yield item


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

def _version_tuple(pdf: pikepdf.Pdf) -> tuple[int, int]:
    version = str(getattr(pdf, "pdf_version", "1.7") or "1.7")
    parts = version.split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return 1, 7


def save_pdf(pdf: pikepdf.Pdf, out_path, settings: Settings) -> None:
    """按设置写出：对象流、重新压缩 Flate、线性化。"""
    mode = pikepdf.ObjectStreamMode.generate if settings.compress_object_streams \
        else pikepdf.ObjectStreamMode.preserve
    min_version = ""
    if settings.compress_object_streams and _version_tuple(pdf) < (1, 5):
        # pikepdf 这个参数只收字符串形式的版本号
        min_version = "1.5"
    pdf.save(
        str(out_path),
        compress_streams=True,
        recompress_flate=settings.recompress_flate,
        object_stream_mode=mode,
        linearize=settings.linearize,
        min_version=min_version,
        fix_metadata_version=True,
        # 保留 PDF/A 时无法删元数据，交给用户设定
        preserve_pdfa=not settings.strip_metadata,
    )


__all__ = ["StructureStats", "optimize_structure", "save_pdf"]
