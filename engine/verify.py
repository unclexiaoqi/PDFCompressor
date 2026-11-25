"""压缩后自检：页数是否一致、内容是否还在、画质掉了多少。

这是本工具和「暴力重排」型压缩器的区别：改完必须能证明文件没坏、
页面没白、画质损失在预期内。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf


@dataclass
class VerifyReport:
    ok: bool = False
    pages_before: int = 0
    pages_after: int = 0
    psnr: float = 0.0
    ink_before: float = 0.0
    ink_after: float = 0.0
    checked: tuple[int, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def ink_ratio(self) -> float:
        if self.ink_before <= 1e-6:
            return 1.0
        return self.ink_after / self.ink_before


def _render(page: pymupdf.Page, dpi: int = 72) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    if mse <= 1e-9:
        return 99.0
    return float(10.0 * math.log10(255.0 ** 2 / mse))


def _sample(count: int, limit: int = 3) -> list[int]:
    if count <= 0:
        return []
    if count <= limit:
        return list(range(count))
    picks = {0, count // 2, count - 1}
    return sorted(picks)[:limit]


def verify(src: Path | str, dst: Path | str, limit: int = 3,
           destructive: bool = False) -> VerifyReport:
    """对比源文件与输出文件（抽样页渲染后算 PSNR）。"""
    report = VerifyReport()
    try:
        before = pymupdf.open(str(src))
    except Exception as exc:
        report.notes.append(f"无法打开源文件：{exc}")
        return report
    try:
        try:
            after = pymupdf.open(str(dst))
        except Exception as exc:
            report.notes.append(f"输出文件无法打开：{exc}")
            return report
        try:
            report.pages_before = before.page_count
            report.pages_after = after.page_count
            if report.pages_before != report.pages_after:
                report.notes.append(f"页数不一致：{report.pages_before} → {report.pages_after}")
                return report
            if report.pages_after == 0:
                report.notes.append("输出文件没有页面")
                return report

            picks = _sample(report.pages_after, limit)
            report.checked = tuple(picks)
            scores: list[float] = []
            inks_before: list[float] = []
            inks_after: list[float] = []
            for pno in picks:
                try:
                    img_a = _render(before[pno])
                    img_b = _render(after[pno])
                except Exception as exc:
                    report.notes.append(f"第 {pno + 1} 页渲染失败：{exc}")
                    return report
                scores.append(_psnr(img_a, img_b))
                inks_before.append(float(np.mean(255 - img_a)) / 255.0)
                inks_after.append(float(np.mean(255 - img_b)) / 255.0)

            report.psnr = float(np.mean(scores)) if scores else 0.0
            report.ink_before = float(np.mean(inks_before)) if inks_before else 0.0
            report.ink_after = float(np.mean(inks_after)) if inks_after else 0.0

            if report.ink_before > 0.005 and report.ink_ratio < 0.55:
                report.notes.append(
                    f"输出内容明显变少（墨量 {report.ink_ratio * 100:.0f}%），可能丢失了内容")
                return report
            if report.psnr < 15.0 and not destructive:
                report.notes.append(f"渲染差异过大（PSNR {report.psnr:.1f} dB）")
                return report
            report.ok = True
            return report
        finally:
            after.close()
    finally:
        before.close()


__all__ = ["VerifyReport", "verify"]
