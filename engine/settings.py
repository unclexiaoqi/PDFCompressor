"""压缩参数与预设。

四档预设：最小文件大小 / 标准 / 高质量 / 印刷质量。
每一档都对应一组「图像降采样 + 重压缩 + 结构清理」的组合。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class Settings:
    """一次压缩任务的全部参数。"""

    preset: str = "standard"

    # ---------- 图像降采样（缩减像素采样） ----------
    color_dpi: int = 150
    gray_dpi: int = 150
    mono_dpi: int = 300
    # 只有有效分辨率超过 目标*trigger 才降采样（1.5 倍规则）
    downsample_trigger: float = 1.5
    # bicubic(双立方) / average(平均) / subsample(次采样)
    downsample: str = "bicubic"

    # ---------- 图像重压缩 ----------
    color_quality: int = 65      # JPEG 品质 1-100
    gray_quality: int = 70
    color_codec: str = "jpeg"    # jpeg | zip
    mono_codec: str = "auto"     # auto | ccitt | zip
    convert_gray: bool = False   # 彩色图转灰度
    cmyk_mode: str = "keep"      # keep（原样保留 CMYK）| rgb（转 RGB，颜色可能轻微偏移）

    # ---------- 字体 ----------
    subset_fonts: bool = True        # 仅嵌入用到的字符（字体子集化）
    unembed_base14: bool = False     # 取消嵌入标准 14 字体

    # ---------- 结构 / 清理 ----------
    compress_object_streams: bool = True   # 对象流压缩
    recompress_flate: bool = True          # 重新压缩 Flate 流
    linearize: bool = False                # 快速 Web 查看（线性化）
    strip_metadata: bool = False           # 删除 XMP/文档信息
    strip_thumbnails: bool = True          # 删除缩略图
    strip_attachments: bool = False        # 删除附件
    strip_outlines: bool = False           # 删除书签
    strip_annotations: bool = False        # 删除注释/表单
    strip_structure: bool = False          # 删除结构树（无障碍标记）
    strip_page_labels: bool = False        # 删除页面标签

    # ---------- 策略 ----------
    min_psnr: float = 28.0          # 单图质量闸门（低于该值自动提高品质/放弃）
    min_image_bytes: int = 4096     # 太小的图不动
    size_guard: float = 0.95        # 重压缩后体积必须小于原来的这个比例才替换
    keep_original_if_bigger: bool = True
    allow_decrypt: bool = False     # 允许解密受保护 PDF
    output_suffix: str = "-压缩"

    def __post_init__(self) -> None:
        self.color_dpi = int(self.color_dpi)
        self.gray_dpi = int(self.gray_dpi)
        self.mono_dpi = int(self.mono_dpi)
        self.color_quality = int(self.color_quality)
        self.gray_quality = int(self.gray_quality)

    # ---------- 便捷查询 ----------
    def dpi_for(self, kind: str) -> int:
        if kind == "mono":
            return self.mono_dpi
        if kind == "gray":
            return self.gray_dpi
        return self.color_dpi

    def quality_for(self, kind: str) -> int:
        return self.gray_quality if kind == "gray" else self.color_quality

    @property
    def destructive(self) -> bool:
        """是否会删除用户可见的内容（缩略图不算，纯内部缓存）。"""
        return any((
            self.strip_metadata, self.strip_attachments, self.strip_outlines,
            self.strip_annotations, self.strip_structure, self.strip_page_labels,
        ))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Settings":
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        # 数值型字段兜底
        out = cls()
        for key, value in clean.items():
            try:
                setattr(out, key, value)
            except (TypeError, ValueError):
                continue
        return out

    def describe(self) -> list[tuple[str, str]]:
        """给界面用的中文摘要。"""
        rows: list[tuple[str, str]] = []
        if self.color_dpi or self.gray_dpi:
            rows.append(("彩色/灰度图像", f"{self.color_dpi} / {self.gray_dpi} DPI · JPEG 品质 {self.color_quality}/{self.gray_quality}"))
        rows.append(("单色图像", f"{self.mono_dpi} DPI · " + {"auto": "自动（CCITT/ZIP 取小）", "ccitt": "CCITT G4", "zip": "ZIP"}[self.mono_codec]))
        if self.convert_gray:
            rows.append(("色彩", "彩色图像转灰度"))
        rows.append(("字体", "子集化（仅保留用到的字符）" + ("，取消嵌入标准 14 字体" if self.unembed_base14 else "") if self.subset_fonts else "保持原样"))
        picked = []
        if self.strip_metadata:
            picked.append("元数据")
        if self.strip_thumbnails:
            picked.append("缩略图")
        if self.strip_attachments:
            picked.append("附件")
        if self.strip_outlines:
            picked.append("书签")
        if self.strip_annotations:
            picked.append("注释/表单")
        if self.strip_structure:
            picked.append("结构树")
        if self.strip_page_labels:
            picked.append("页面标签")
        rows.append(("放弃的对象", "、".join(picked) if picked else "全部保留"))
        rows.append(("流与对象", "对象流压缩" + ("，重新压缩 Flate" if self.recompress_flate else "") + ("，线性化" if self.linearize else "")))
        rows.append(("画质闸门", f"PSNR ≥ {self.min_psnr:.0f} dB，否则自动提品质或放弃替换"))
        return rows


# ---------------------------------------------------------------------------
# 预设
# ---------------------------------------------------------------------------

PRESET_ORDER = ("smallest", "standard", "high", "print")

PRESET_LABELS = {
    "smallest": "最小文件大小",
    "standard": "标准（推荐）",
    "high": "高质量",
    "print": "印刷质量",
}

PRESET_HINTS = {
    "smallest": "96 DPI，清掉元数据、附件与书签，只为最小体积。",
    "standard": "150 DPI，保留书签与元数据，日常分享/归档首选。",
    "high": "300 DPI，几乎看不出损失，适合存档与再编辑。",
    "print": "300 DPI，保留 CMYK 和全部文档信息，送印前传阅。",
}


def _preset(name: str, **overrides: Any) -> Settings:
    base = Settings()
    for key, value in overrides.items():
        setattr(base, key, value)
    base.preset = name
    return base


PRESETS: dict[str, Settings] = {
    "smallest": _preset(
        "smallest",
        color_dpi=96, gray_dpi=96, mono_dpi=200,
        color_quality=45, gray_quality=50,
        unembed_base14=True,
        strip_metadata=True, strip_thumbnails=True, strip_attachments=True,
        strip_outlines=True, strip_structure=True,
        min_psnr=24.0,
    ),
    "standard": _preset(
        "standard",
        color_dpi=150, gray_dpi=150, mono_dpi=300,
        color_quality=65, gray_quality=70,
        strip_thumbnails=True,
        min_psnr=28.0,
    ),
    "high": _preset(
        "high",
        color_dpi=300, gray_dpi=300, mono_dpi=400,
        color_quality=85, gray_quality=88,
        strip_thumbnails=False,
        min_psnr=32.0,
    ),
    "print": _preset(
        "print",
        color_dpi=300, gray_dpi=300, mono_dpi=600,
        color_quality=92, gray_quality=92,
        strip_thumbnails=False,
        min_psnr=34.0,
        output_suffix="-压缩",
    ),
}


def get_preset(name: str) -> Settings:
    """返回预设副本（可安全修改）。"""
    preset = PRESETS.get(name, PRESETS["standard"])
    return Settings.from_dict(preset.to_dict())


def preset_for(settings: Settings) -> str:
    """若参数与某个预设一致则返回预设名，否则 custom（输出后缀不参与比较）。"""
    ignore = {"preset", "output_suffix"}
    current = {k: v for k, v in settings.to_dict().items() if k not in ignore}
    for name, preset in PRESETS.items():
        snapshot = {k: v for k, v in preset.to_dict().items() if k not in ignore}
        if current == snapshot:
            return name
    return "custom"


__all__ = [
    "Settings",
    "PRESETS",
    "PRESET_ORDER",
    "PRESET_LABELS",
    "PRESET_HINTS",
    "get_preset",
    "preset_for",
]
