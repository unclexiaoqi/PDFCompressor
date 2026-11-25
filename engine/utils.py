"""通用工具：体积格式化、临时文件、取消令牌、安全替换。"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

# 命令行/控制台用的一句免责声明（与 README、DISCLAIMER.md 保持一致）
DISCLAIMER_LINE = (
    "PDF 压缩器：仅供学习与技术交流，请只处理你拥有合法权利的文件；"
    "压缩会改变图像画质，请保留原件；完整条款见 DISCLAIMER.md。"
)


def human_size(num: float | int | None) -> str:
    """把字节数格式化成 1.2 MB 这样的字符串。"""
    if num is None:
        return "-"
    value = float(num)
    if value < 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(round(value))} B"
            if value < 10:
                return f"{value:.2f} {unit}"
            if value < 100:
                return f"{value:.1f} {unit}"
            return f"{value:.0f} {unit}"
        value /= 1024.0
    return f"{value:.0f} TB"


def reduction(before: int | None, after: int | None) -> float | None:
    """压缩率（0~1，表示减少的比例）；未知返回 None。"""
    if not before or after is None:
        return None
    return max(0.0, (before - after) / float(before))


def format_delta(before: int | None, after: int | None) -> str:
    """“-63%”样式。"""
    ratio = reduction(before, after)
    if ratio is None:
        return "-"
    return f"-{ratio * 100:.0f}%"


class Cancelled(Exception):
    """用户主动停止。"""


class CancelToken:
    """跨线程取消开关。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


def temp_path_for(target: Path, tag: str, suffix: str = ".pdf") -> Path:
    """在目标文件同目录创建唯一临时路径，便于事后原子改名。"""
    target = Path(target)
    folder = target.parent if target.parent.exists() else Path(tempfile.gettempdir())
    try:
        fd, name = tempfile.mkstemp(prefix=f".{target.stem}.{tag}.", suffix=suffix, dir=str(folder))
        os.close(fd)
        return Path(name)
    except OSError:
        fd, name = tempfile.mkstemp(prefix=f"pdfc-{tag}.", suffix=suffix)
        os.close(fd)
        return Path(name)


def remove_quietly(path: Path | str | None) -> None:
    if not path:
        return
    try:
        os.unlink(str(path))
    except OSError:
        pass


def atomic_replace(src: Path, dst: Path) -> None:
    """用 src 覆盖 dst，尽量原子。"""
    src, dst = Path(src), Path(dst)
    try:
        os.replace(str(src), str(dst))
    except OSError:
        shutil.copyfile(str(src), str(dst))
        remove_quietly(src)


def unique_output_path(target: Path) -> Path:
    """若目标已存在，追加 (1)(2)… 避免覆盖。"""
    target = Path(target)
    if not target.exists():
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    for i in range(1, 1000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem} ({os.getpid()}){suffix}"


def clamp(value, low, high):
    return max(low, min(high, value))
