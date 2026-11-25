"""压缩流水线：体检 → 字体子集化 → 图像重压缩 → 结构清理 → 自检。

顺序是有讲究的：
  1. 先做字体子集化，因为这一步会重写对象编号，会影响后面按 xref 处理图像；
  2. 图像处理用 pikepdf 原地替换图像流，版面矩阵不动，位置/大小天然不变；
  3. 结构清理和保存放最后一次，对象流/线性化只做一遍。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import pymupdf

from .analyze import PdfProfile, analyze
from .fonts import FontStage, subset_fonts
from .imageopt import ImageStats, optimize_images, plan_images
from .settings import Settings, get_preset
from .structure import StructureStats, optimize_structure, save_pdf
from .utils import (
    CancelToken, Cancelled, atomic_replace, format_delta, human_size,
    remove_quietly, temp_path_for,
)
from .verify import VerifyReport, verify


class CompressError(Exception):
    """面向用户的错误。"""


@dataclass
class CompressResult:
    src: Path
    dst: Path | None = None
    ok: bool = False
    cancelled: bool = False
    error: str = ""
    kept_original: bool = False

    orig_size: int = 0
    new_size: int = 0
    pages: int = 0
    elapsed: float = 0.0

    profile: PdfProfile | None = None
    images: ImageStats | None = None
    fonts: FontStage | None = None
    structure: StructureStats | None = None
    verify: VerifyReport | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return max(0, self.orig_size - self.new_size)

    @property
    def ratio(self) -> float:
        if not self.orig_size or not self.new_size:
            return 0.0
        return max(0.0, (self.orig_size - self.new_size) / float(self.orig_size))

    @property
    def display_name(self) -> str:
        return self.src.name

    @property
    def status_text(self) -> str:
        if self.cancelled:
            return "已停止"
        if self.error:
            return "失败"
        if self.kept_original:
            return "已是最优"
        if not self.new_size:
            return "未处理"
        return f"已压缩 {format_delta(self.orig_size, self.new_size)}"

    def detail_rows(self) -> list[tuple[str, str]]:
        """给「压缩详情」窗口用的两列表格。"""
        rows: list[tuple[str, str]] = [
            ("原始大小", human_size(self.orig_size)),
            ("压缩后", human_size(self.new_size) if self.new_size else "-"),
            ("节省", f"{human_size(self.saved)}（{self.ratio * 100:.1f}%）" if self.new_size else "-"),
            ("页数", str(self.pages) if self.pages else "-"),
            ("耗时", f"{self.elapsed:.1f} 秒"),
        ]
        if self.images and self.images.total:
            img = self.images
            rows.append(("图像", f"{img.total} 个 · 处理 {img.processed} 个 · 跳过 {img.skipped} 个"))
            rows.append(("图像体积", f"{human_size(img.before)} → {human_size(img.after)}"))
            if img.psnr_count:
                rows.append(("图像画质", f"平均 {img.psnr_avg:.1f} dB · 最低 {img.psnr_min:.1f} dB"))
        if self.fonts and self.fonts.applied:
            rows.append(("字体子集化", f"{human_size(self.fonts.before)} → {human_size(self.fonts.after)}"))
        if self.structure:
            if self.structure.removed_counts:
                summary = "、".join(
                    f"{name} {count} 个" for name, count in self.structure.removed_counts.items())
                rows.append(("放弃的对象", summary))
            if self.structure.removed_bytes:
                rows.append(("释放空间", human_size(self.structure.saved)))
        if self.verify and self.verify.ok:
            rows.append(("内容自检", f"通过 · 抽样 {len(self.verify.checked)} 页 · PSNR {self.verify.psnr:.1f} dB"))
        elif self.verify and self.verify.notes:
            rows.append(("内容自检", "；".join(self.verify.notes)))
        if self.warnings:
            rows.append(("提示", "；".join(self.warnings[:3])))
        return rows


def default_output_path(src: Path, settings: Settings) -> Path:
    suffix = settings.output_suffix or "-压缩"
    return Path(src).with_name(f"{Path(src).stem}{suffix}.pdf")


def _mapper(progress, low: float, high: float):
    """把 0~1 的子进度映射到整条流水线的区间。"""
    if progress is None:
        return None
    span = high - low

    def report(fraction: float, text: str = "") -> None:
        try:
            value = low + max(0.0, min(1.0, float(fraction))) * span
            progress(value, text)
        except Exception:
            pass

    return report


def compress_file(
    src: str | Path,
    dst: str | Path | None = None,
    settings: Settings | None = None,
    progress=None,
    cancel: CancelToken | None = None,
) -> CompressResult:
    """压缩单个 PDF；无论成败都返回结果对象，不向外抛业务异常。"""
    started = time.time()
    src_path = Path(src)
    settings = settings or get_preset("standard")
    result = CompressResult(src=src_path)

    tmp_fonts: Path | None = None
    tmp_out: Path | None = None
    try:
        if not src_path.exists():
            raise CompressError("文件不存在")
        if src_path.suffix.lower() != ".pdf":
            raise CompressError("不是 PDF 文件")
        result.orig_size = src_path.stat().st_size

        if cancel:
            cancel.check()

        # ---------- 1. 体检 ----------
        profile = analyze(src_path, progress=_mapper(progress, 0.0, 0.05))
        result.profile = profile
        result.pages = profile.pages
        if profile.needs_password:
            raise CompressError("PDF 设置了打开密码，请先解密再压缩")
        if profile.encrypted and not settings.allow_decrypt:
            raise CompressError("PDF 受安全策略保护，已跳过（可在设置里允许解密后压缩）")

        out_path = Path(dst) if dst else default_output_path(src_path, settings)
        if out_path.resolve() == src_path.resolve():
            raise CompressError("输出文件不能和源文件相同")
        result.dst = out_path
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CompressError(f"无法创建输出目录：{exc}") from exc

        # ---------- 2. 字体子集化 ----------
        work = src_path
        font_stage = FontStage(before=result.orig_size)
        if settings.subset_fonts and profile.embedded_font_count > 0:
            tmp_fonts = temp_path_for(out_path, "fonts")
            font_stage = subset_fonts(
                src_path, tmp_fonts, cancel=cancel,
                progress=_mapper(progress, 0.05, 0.15),
            )
            if font_stage.applied and tmp_fonts.exists() and tmp_fonts.stat().st_size > 0:
                work = tmp_fonts
            else:
                remove_quietly(tmp_fonts)
                tmp_fonts = None
        result.fonts = font_stage
        if font_stage.note:
            result.warnings.append(font_stage.note)
        if cancel:
            cancel.check()

        # ---------- 3. 图像计划（字体子集化后 xref 变了，重新体检） ----------
        if work != src_path:
            plan_profile = analyze(work, progress=_mapper(progress, 0.15, 0.18))
        else:
            plan_profile = profile
        plans = plan_images(plan_profile, settings)

        # ---------- 4. 图像 + 结构 ----------
        tmp_out = temp_path_for(out_path, "out")
        with pikepdf.open(str(work)) as pdf:
            if pdf.is_encrypted and not settings.allow_decrypt:
                raise CompressError("PDF 受密码保护，请先解密")
            result.images = optimize_images(
                pdf, plans, settings,
                progress=_mapper(progress, 0.18, 0.82),
                cancel=cancel,
            )
            if cancel:
                cancel.check()
            if progress:
                progress(0.84, "清理文档结构…")
            result.structure = optimize_structure(pdf, settings)
            result.warnings.extend(result.images.warnings[:5])
            result.warnings.extend(result.structure.warnings[:5])

            if progress:
                progress(0.88, "写出文件…")
            save_pdf(pdf, tmp_out, settings)

        if cancel:
            cancel.check()
        if not tmp_out.exists() or tmp_out.stat().st_size == 0:
            raise CompressError("写出失败：输出文件为空")

        new_size = tmp_out.stat().st_size
        # 体积护栏：压完反而更大就没意义
        if settings.keep_original_if_bigger and new_size >= result.orig_size:
            shutil.copyfile(src_path, tmp_out)
            new_size = result.orig_size
            result.kept_original = True
            result.warnings.append("已是最优：再压缩不会更小，输出为原文件副本")
        result.new_size = new_size

        # ---------- 5. 自检 ----------
        if progress:
            progress(0.94, "校验内容…")
        if not result.kept_original:
            report = verify(src_path, tmp_out, destructive=settings.destructive)
            result.verify = report
            if not report.ok:
                detail = "；".join(report.notes) or "未知原因"
                raise CompressError(f"自检未通过，已放弃输出（{detail}）")

        atomic_replace(tmp_out, out_path)
        tmp_out = None
        result.ok = True
        if progress:
            progress(1.0, "完成")
        return result

    except Cancelled:
        result.cancelled = True
        result.error = "已停止"
        return result
    except CompressError as exc:
        result.error = str(exc)
        return result
    except pikepdf.PasswordError:
        result.error = "PDF 需要密码才能打开，请先解密"
        return result
    except pikepdf.PdfError as exc:
        detail = str(exc).strip().splitlines()[0][:100]
        result.error = f"PDF 结构损坏或不完整，无法处理（{detail}）"
        return result
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        result.error = "文件不是有效的 PDF，或已经损坏"
        return result
    except MemoryError:
        result.error = "内存不足：这个 PDF 的图像太大了"
        return result
    except Exception as exc:  # 兜底，避免批量任务中断
        result.error = f"处理失败：{exc.__class__.__name__}: {exc}"
        return result
    finally:
        remove_quietly(tmp_fonts)
        remove_quietly(tmp_out)
        result.elapsed = time.time() - started


__all__ = ["CompressResult", "CompressError", "compress_file", "default_output_path"]
