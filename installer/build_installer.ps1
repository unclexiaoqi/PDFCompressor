<#
.SYNOPSIS
    把 dist\PDFCompressor 封装成 Windows 安装包（Setup.exe）。

.DESCRIPTION
    前置：先运行项目根目录的 build.ps1 生成 dist\PDFCompressor。
    编译器：优先使用 -IsccPath 指定的 ISCC.exe，或本机已安装的 Inno Setup 6/7；
            都找不到时自动下载便携版编译器到
            %LOCALAPPDATA%\PDFCompressor-buildtools（无需管理员权限）。
    产物：installer\output\PDFCompressor-<版本>-Setup.exe

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 `
        -IsccPath "C:\Program Files\Inno Setup 6\ISCC.exe"
#>
[CmdletBinding()]
param(
    [string]$IsccPath = "",
    [string]$InnoVersion = "7.1.0",
    [switch]$ForceDownload
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Payload     = Join-Path $ProjectRoot "dist\PDFCompressor"
$AppExe      = Join-Path $Payload "PDFCompressor.exe"
$IssFile     = Join-Path $PSScriptRoot "PDFCompressor.iss"
$CacheRoot   = Join-Path $env:LOCALAPPDATA "PDFCompressor-buildtools"

function Find-Iscc {
    param([string]$Explicit, [string]$Version, [string]$Cache)

    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "-IsccPath 不存在：$Explicit" }
        return (Resolve-Path $Explicit).Path
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe")
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }

    $cached = Join-Path $Cache "inno-$Version\tools\ISCC.exe"
    if ($ForceDownload -and (Test-Path (Join-Path $Cache "inno-$Version"))) {
        Remove-Item (Join-Path $Cache "inno-$Version") -Recurse -Force
    }
    if (Test-Path $cached) { return $cached }

    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    $url    = "https://api.nuget.org/v3-flatcontainer/tools.innosetup/$Version/tools.innosetup.$Version.nupkg"
    $nupkg  = Join-Path $Cache "tools.innosetup.$Version.nupkg"
    $asZip  = Join-Path $Cache "tools.innosetup.$Version.zip"
    $target = Join-Path $Cache "inno-$Version"

    Write-Host "未找到 Inno Setup 编译器，正在下载便携版 $Version ..." -ForegroundColor Cyan
    try {
        Invoke-WebRequest -Uri $url -OutFile $nupkg -UseBasicParsing -TimeoutSec 300
    } catch {
        throw ("下载编译器失败：$($_.Exception.Message)`n" +
               "可以手动安装 Inno Setup 6/7，然后用 -IsccPath 指向 ISCC.exe。")
    }
    Copy-Item $nupkg $asZip -Force
    Expand-Archive -Path $asZip -DestinationPath $target -Force
    Remove-Item $asZip -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $cached)) { throw "编译器解压失败：$cached" }
    Write-Host "编译器已就绪：$cached" -ForegroundColor Green
    return $cached
}

if (-not (Test-Path $AppExe)) {
    throw "找不到 $AppExe 。请先运行 build.ps1 生成 dist\PDFCompressor。"
}
if (-not (Test-Path $IssFile)) { throw "找不到安装脚本：$IssFile" }

$match = Select-String -Path (Join-Path $ProjectRoot "engine\__init__.py") `
    -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $match) { throw "无法从 engine\__init__.py 读取 __version__" }
$version = $match.Matches[0].Groups[1].Value

$iscc = Find-Iscc -Explicit $IsccPath -Version $InnoVersion -Cache $CacheRoot

$payloadFiles = Get-ChildItem $Payload -Recurse -File
$payloadMB = [math]::Round((($payloadFiles | Measure-Object Length -Sum).Sum) / 1MB, 1)

Write-Host ""
Write-Host "PDF 压缩器 安装包构建" -ForegroundColor Cyan
Write-Host "  版本      : $version"
Write-Host "  载荷文件  : $($payloadFiles.Count) 个，$payloadMB MB"
Write-Host "  编译器    : $iscc"
Write-Host ""

$sw = [Diagnostics.Stopwatch]::StartNew()
& $iscc "/DAppVersion=$version" $IssFile
$code = $LASTEXITCODE
$sw.Stop()

if ($code -ne 0) { throw "ISCC 编译失败，退出码 $code" }

$setup = Join-Path $PSScriptRoot "output\PDFCompressor-$version-Setup.exe"
if (-not (Test-Path $setup)) { throw "编译成功但找不到产物：$setup" }

$item = Get-Item $setup
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash

Write-Host ""
Write-Host "构建完成：$($item.FullName)" -ForegroundColor Green
Write-Host ("  大小    : {0:N1} MB" -f ($item.Length / 1MB))
Write-Host "  SHA256  : $hash"
Write-Host ("  耗时    : {0:N1} 秒" -f $sw.Elapsed.TotalSeconds)

exit 0
