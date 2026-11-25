"""字体子集化：只保留文档真正用到的字形。

只保留文档真正用到的字形，通常是体积收益最大的一步：
一份内嵌中文字体动辄 10 MB 以上，子集化后往往只剩几十 KB。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .utils import CancelToken


@dataclass
class FontStage:
    applied: bool = False
    before: int = 0
    after: int = 0
    fonts: int = 0
    note: str = ""

    @property
    def saved(self) -> int:
        return max(0, self.before - self.after)


def subset_fonts(src: Path, dst: Path, cancel: CancelToken | None = None,
                 progress=None) -> FontStage:
    """用 MuPDF 的字体子集化能力生成一份临时文件。

    失败时不抛异常，返回 applied=False，调用方继续用原文件。
    """
    stage = FontStage(before=Path(src).stat().st_size)
    try:
        doc = pymupdf.open(str(src))
    except Exception as exc:
        stage.note = f"无法打开：{exc}"
        return stage
    try:
        if doc.needs_pass:
            stage.note = "已加密，跳过字体子集化"
            return stage
        if cancel:
            cancel.check()
        if progress:
            progress(0.25, "分析字体…")
        try:
            doc.subset_fonts(verbose=False)
        except Exception as exc:  # 个别字体不支持子集化
            stage.note = f"子集化失败：{exc}"
            return stage
        if cancel:
            cancel.check()
        if progress:
            progress(0.6, "写回子集字体…")
        doc.save(
            str(dst),
            garbage=4,           # 去重 + 删除无用对象
            deflate=True,
            deflate_images=False,
            deflate_fonts=True,
            clean=False,
            use_objstms=1,
            preserve_metadata=1,
            no_new_id=0,
        )
        stage.applied = True
        stage.after = Path(dst).stat().st_size
        try:
            stage.fonts = len({f[3] for p in range(doc.page_count) for f in doc.get_page_fonts(p)})
        except Exception:
            stage.fonts = 0
        return stage
    finally:
        doc.close()


__all__ = ["FontStage", "subset_fonts"]
