<#
.SYNOPSIS
    构建 Windows 免安装版：dist\PDFCompressor\ 以及绿色的 Portable zip。

.DESCRIPTION
    前置：已安装 Python 3.10+ 与 requirements.txt 里的依赖。
    产物：
      dist\PDFCompressor\PDFCompressor.exe      （双击即用）
      dist\PDFCompressor-<版本>-Portable.zip     （绿色版，解压即用）

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path "requirements.txt")) { throw "请在项目根目录运行本脚本" }

$match = Select-String -Path (Join-Path $ProjectRoot "engine\__init__.py") `
    -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $match) { throw "无法从 engine\__init__.py 读取 __version__" }
$version = $match.Matches[0].Groups[1].Value

Write-Host ""
Write-Host "PDF 压缩器 · Windows 构建" -ForegroundColor Cyan
Write-Host "  版本 : $version"
Write-Host ""

if (-not $SkipIcon) {
    Write-Host "→ 生成图标" -ForegroundColor DarkGray
    python make_icon.py
}

Write-Host "→ PyInstaller 打包" -ForegroundColor DarkGray
$buildDir = Join-Path $ProjectRoot "dist\PDFCompressor"
if (Test-Path $buildDir) {
    # 先自己清一次：上一次运行的进程/杀软可能占着文件，早点暴露问题
    try {
        Remove-Item $buildDir -Recurse -Force -ErrorAction Stop
    } catch {
        throw ("无法清理 $buildDir ，可能还有 PDFCompressor.exe 在运行或被占用：`n" +
               $_.Exception.Message + "`n请先关闭程序后再试。")
    }
}
python -m PyInstaller PDFCompressor.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败（退出码 $LASTEXITCODE），请查看上方日志"
}

$exe = Join-Path $buildDir "PDFCompressor.exe"
if (-not (Test-Path $exe)) { throw "打包失败：找不到 $exe" }
if (-not (Test-Path (Join-Path $buildDir "_internal"))) {
    throw "打包失败：缺少 _internal 目录，产物不完整"
}

# 把说明和免责声明放到程序目录，绿色版解压后可直接看到
Copy-Item (Join-Path $ProjectRoot "README.md") `
    (Join-Path $ProjectRoot "dist\PDFCompressor\使用说明.md") -Force
Copy-Item (Join-Path $ProjectRoot "DISCLAIMER.md") `
    (Join-Path $ProjectRoot "dist\PDFCompressor\免责声明.md") -Force

$zip = Join-Path $ProjectRoot "dist\PDFCompressor-$version-Portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "→ 压缩绿色版" -ForegroundColor DarkGray
Compress-Archive -Path (Join-Path $ProjectRoot "dist\PDFCompressor\*") `
    -DestinationPath $zip -CompressionLevel Optimal

$exeSize = (Get-ChildItem (Join-Path $ProjectRoot "dist\PDFCompressor") -Recurse -File |
            Measure-Object Length -Sum).Sum
$zipSize = (Get-Item $zip).Length

Write-Host ""
Write-Host "构建完成" -ForegroundColor Green
Write-Host ("  目录版 : {0}  ({1:N1} MB)" -f $exe, ($exeSize / 1MB))
Write-Host ("  绿色版 : {0}  ({1:N1} MB)" -f $zip, ($zipSize / 1MB))
Write-Host ""
Write-Host "下一步：安装包用 installer\build_installer.ps1" -ForegroundColor DarkGray
