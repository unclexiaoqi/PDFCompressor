<#
.SYNOPSIS
    一键发布：Windows 目录版 + 绿色版 zip + 安装包，并生成 SHA256SUMS.txt。

.DESCRIPTION
    会把 build.ps1 和 installer\build_installer.ps1 串起来跑一遍。
    产物集中在 dist\ 与 installer\output\。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File release.ps1
#>
[CmdletBinding()]
param(
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

$match = Select-String -Path (Join-Path $ProjectRoot "engine\__init__.py") `
    -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
$version = $match.Matches[0].Groups[1].Value

& (Join-Path $ProjectRoot "build.ps1")

$cliArgs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $ProjectRoot "installer\build_installer.ps1"))
if ($IsccPath) { $cliArgs += @("-IsccPath", $IsccPath) }
& powershell @cliArgs

$setup = Join-Path $ProjectRoot "installer\output\PDFCompressor-$version-Setup.exe"
$zip   = Join-Path $ProjectRoot "dist\PDFCompressor-$version-Portable.zip"

$files = @($setup, $zip) | Where-Object { Test-Path $_ }
$sums = Join-Path $ProjectRoot "dist\SHA256SUMS.txt"
$lines = foreach ($file in $files) {
    $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    "{0}  {1}" -f $hash, (Split-Path $file -Leaf)
}
$lines | Set-Content -Path $sums -Encoding utf8

Write-Host ""
Write-Host "发布产物" -ForegroundColor Green
foreach ($file in $files) {
    Write-Host ("  {0,-52} {1,8:N1} MB" -f (Split-Path $file -Leaf), ((Get-Item $file).Length / 1MB))
}
Write-Host ("  校验文件 {0}" -f $sums)
