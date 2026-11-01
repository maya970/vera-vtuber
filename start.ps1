# 薇拉 · 书库直播间
# 没有 Python 时会先下载便携环境，再打开工作室。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==========================================" -ForegroundColor DarkYellow
Write-Host "  虚拟主播 · 开盒即用" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor DarkYellow

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\ensure_runtime.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$py = Join-Path $PSScriptRoot "runtime\python\python.exe"
if (-not (Test-Path $py)) {
    $shim = Join-Path $PSScriptRoot "runtime\python.cmd"
    if (Test-Path $shim) { $py = $shim } else { $py = "python" }
}

$mode = $args[0]
if (-not $mode) { $mode = "studio" }
Write-Host "[*] 使用 $py" -ForegroundColor Cyan

if ($mode -eq "stage") {
    Start-Process (Join-Path $PSScriptRoot "open_stage.bat")
} elseif ($mode -eq "panel") {
    Start-Process "http://127.0.0.1:8765/panel"
} else {
    Start-Process "http://127.0.0.1:8765/"
}

& $py (Join-Path $PSScriptRoot "run.py")
