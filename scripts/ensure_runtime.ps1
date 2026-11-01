# 准备便携 Python，让没有安装环境的电脑也能双击开播。
# 成功后写出 runtime\ready.txt：bundled（自带解释器）或 system（本机 Python）。
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime"
$PyDir = Join-Path $Runtime "python"
$PyExe = Join-Path $PyDir "python.exe"
$Ready = Join-Path $Runtime "ready.txt"
$Stamp = Join-Path $Runtime "requirements.sha256"
$Req = Join-Path $Root "requirements.txt"
$Version = "3.11.9"
$ImportCheck = "import fastapi,uvicorn,yaml,dotenv,PIL,edge_tts,aiohttp,gradio_client,psd_tools,openai"

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

function Write-Ready([string]$Mode) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Ready, $Mode, $utf8)
}

function Test-Exe([string]$Exe) {
    & $Exe -c $ImportCheck
    return $LASTEXITCODE -eq 0
}

function Test-PyLauncher {
    & py -3 -c $ImportCheck
    return $LASTEXITCODE -eq 0
}

function Save-Url([string]$Url, [string]$Dest) {
    Write-Host "[*] 下载 $Url"
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --fail --retry 2 --connect-timeout 20 -o $Dest $Url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $Dest) -and ((Get-Item $Dest).Length -gt 10000)) {
            return
        }
        Write-Host "[!] curl 失败，改用 PowerShell 下载"
    }
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    if (-not (Test-Path $Dest) -or (Get-Item $Dest).Length -lt 10000) {
        throw "下载失败：$Url"
    }
}

function Install-Requirements([string]$Exe) {
    Write-Host "[*] 安装依赖（首次大约一两分钟）"
    & $Exe -m pip install -q -r $Req -i "https://pypi.tuna.tsinghua.edu.cn/simple" --timeout 60 --retries 2
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] 镜像失败，改用官方源"
        & $Exe -m pip install -q -r $Req --timeout 60 --retries 2
        if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
    }
    $hash = (Get-FileHash $Req -Algorithm SHA256).Hash
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Stamp, $hash, $utf8)
}

function Enable-SitePackages {
    $pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pth) { throw "便携包里没有 python._pth" }
    $zipName = "python311.zip"
    foreach ($line in (Get-Content $pth.FullName)) {
        $trim = $line.Trim()
        if ($trim -match "\.zip$") { $zipName = $trim; break }
    }
    $site = Join-Path $PyDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $site | Out-Null
    @(
        $zipName
        "."
        "Lib\site-packages"
        "import site"
    ) | Set-Content -Path $pth.FullName -Encoding ascii
}

$reqHash = ""
if (Test-Path $Req) { $reqHash = (Get-FileHash $Req -Algorithm SHA256).Hash }
$oldHash = ""
if (Test-Path $Stamp) { $oldHash = (Get-Content $Stamp -Raw).Trim() }

if ((Test-Path $PyExe) -and $reqHash -eq $oldHash -and (Test-Exe $PyExe)) {
    Write-Ready "bundled"
    Write-Host "[+] 便携环境已就绪"
    exit 0
}

if (-not (Test-Path $PyExe)) {
    $zip = Join-Path $Runtime "python-$Version-embed-amd64.zip"
    $urls = @(
        "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip",
        "https://npmmirror.com/mirrors/python/$Version/python-$Version-embed-amd64.zip"
    )
    $got = $false
    foreach ($url in $urls) {
        try {
            Save-Url $url $zip
            $got = $true
            break
        } catch {
            Write-Host "[!] $url 失败：$($_.Exception.Message)"
        }
    }
    if (-not $got) {
        Write-Host "[!] 下载便携 Python 失败，尝试使用本机 Python"
    } else {
        if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
        New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
        Expand-Archive -Path $zip -DestinationPath $PyDir -Force
        Enable-SitePackages
        $getPip = Join-Path $Runtime "get-pip.py"
        Save-Url "https://bootstrap.pypa.io/get-pip.py" $getPip
        & $PyExe $getPip -q -i "https://pypi.tuna.tsinghua.edu.cn/simple" --timeout 60 --retries 2
        if ($LASTEXITCODE -ne 0) {
            & $PyExe $getPip --timeout 60 --retries 2
            if ($LASTEXITCODE -ne 0) { throw "pip 安装失败" }
        }
    }
}

if (Test-Path $PyExe) {
    Install-Requirements $PyExe
    if (-not (Test-Exe $PyExe)) { throw "便携环境装好了，但导入失败" }
    Write-Ready "bundled"
    Write-Host "[+] 便携 Python 已放进 runtime\python"
    exit 0
}

$hasPython = [bool](Get-Command python -ErrorAction SilentlyContinue)
$hasPy = [bool](Get-Command py -ErrorAction SilentlyContinue)
if (-not $hasPython -and -not $hasPy) {
    Write-Host "[x] 没有 Python，也没能下载便携环境。请检查网络后重新双击 打开.bat"
    exit 1
}

if ($hasPython) {
    & python -m pip install -q -r $Req -i "https://pypi.tuna.tsinghua.edu.cn/simple"
    if ($LASTEXITCODE -ne 0) { & python -m pip install -q -r $Req }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Exe "python")) { throw "本机 Python 依赖安装失败" }
} else {
    & py -3 -m pip install -q -r $Req -i "https://pypi.tuna.tsinghua.edu.cn/simple"
    if ($LASTEXITCODE -ne 0) { & py -3 -m pip install -q -r $Req }
    if ($LASTEXITCODE -ne 0 -or -not (Test-PyLauncher)) { throw "本机 Python 依赖安装失败" }
    $shim = Join-Path $Runtime "python.cmd"
    "@echo off`r`npy -3 %*`r`n" | Set-Content -Path $shim -Encoding ascii
}

$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Stamp, $reqHash, $utf8)
Write-Ready "system"
Write-Host "[+] 使用本机 Python"
exit 0
