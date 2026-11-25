#!/usr/bin/env bash
# macOS 构建脚本：生成 PDFCompressor.app、绿色版 zip、安装版 dmg。
#
# 用法：
#   bash build-macos.sh            # 完整构建（需要 Python 3.10+）
#   bash build-macos.sh --no-dmg   # 只出 .app 和 zip
#
# 前提：需要一个带 Tk 的 Python。系统自带的 /usr/bin/python3（Xcode 命令行工具）
#       通常没有 tkinter，请用 python.org 的安装包，或：
#           brew install python@3.12 python-tk@3.12
#
# 注意：必须在 macOS 上运行。产物未签名/未公证，首次打开需要
#       「右键 → 打开」，或执行：xattr -dr com.apple.quarantine /Applications/PDFCompressor.app
set -euo pipefail
cd "$(dirname "$0")"

WITH_DMG=1
for arg in "$@"; do
    case "$arg" in
        --no-dmg) WITH_DMG=0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done

VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("engine/__init__.py").read_text(encoding="utf-8")
print(re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1))
PY
)"
ARCH="$(uname -m)"
APP="dist/PDFCompressor.app"
STAGE="$(mktemp -d)"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "PDF 压缩器 macOS 构建"
echo "  版本    : $VERSION"
echo "  架构    : $ARCH"
echo ""

if [ ! -d ".venv-build" ]; then
    echo "→ 创建构建虚拟环境 .venv-build"
    python3 -m venv .venv-build
fi
# shellcheck disable=SC1091
source .venv-build/bin/activate

python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller >/dev/null

python make_icon.py
rm -rf build dist/PDFCompressor dist/PDFCompressor.app
python -m PyInstaller PDFCompressor-macos.spec --noconfirm --clean

if [ ! -d "$APP" ]; then
    echo "构建失败：找不到 $APP" >&2
    exit 1
fi

# Ad-hoc 签名：Apple Silicon 上未签名的可执行文件可能直接被系统拒绝
if command -v codesign >/dev/null 2>&1; then
    echo "→ ad-hoc 签名"
    codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || \
        echo "  （ad-hoc 签名失败，继续；首次打开需右键→打开）"
fi

mkdir -p dist

# 绿色版：解压得到 PDFCompressor 文件夹（.app + 说明 + 免责声明）
ZIP="dist/PDFCompressor-${VERSION}-macOS-${ARCH}-Portable.zip"
PORTABLE_DIR="$STAGE/portable/PDFCompressor"
mkdir -p "$PORTABLE_DIR"
cp -R "$APP" "$PORTABLE_DIR/"
cp README.md "$PORTABLE_DIR/使用说明.md"
cp DISCLAIMER.md "$PORTABLE_DIR/免责声明.md"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$PORTABLE_DIR" "$ZIP"
echo "✓ 绿色版：$ZIP"

# 安装版：dmg（拖进 Applications 即安装）
if [ "$WITH_DMG" = "1" ]; then
    DMG="dist/PDFCompressor-${VERSION}-macOS-${ARCH}.dmg"
    DMG_DIR="$STAGE/dmg"
    mkdir -p "$DMG_DIR"
    cp -R "$APP" "$DMG_DIR/"
    cp README.md "$DMG_DIR/使用说明.md"
    cp DISCLAIMER.md "$DMG_DIR/免责声明.md"
    ln -s /Applications "$DMG_DIR/Applications"
    rm -f "$DMG"
    hdiutil create -volname "PDF 压缩器 $VERSION" -srcfolder "$DMG_DIR" \
        -ov -format UDZO -quiet "$DMG"
    echo "✓ 安装版：$DMG"
fi

echo ""
echo "完成："
ls -lh dist/*.dmg dist/*.zip 2>/dev/null || true
echo ""
echo "提示：产物未公证，首次打开请右键 →「打开」；"
echo "      若提示已损坏，执行 xattr -dr com.apple.quarantine <应用路径>"
