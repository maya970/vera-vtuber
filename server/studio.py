"""工作室：上传立绘生成形象、交接 StandRig、弹出直播页并生成可再次打开的文件夹。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .config import ROOT, config

log = logging.getLogger("vera.studio")
router = APIRouter()

SHOW_DIR = ROOT / "直播间"
INBOX = ROOT / "assets" / "inbox"
STANDRIG_DIR = ROOT / "assets" / "standrig"
WORK_DIR = STANDRIG_DIR / "工作区"
SOURCE_DIR = ROOT / "assets" / "source"

STANDRIG_REPO = "https://github.com/sayaka-aiart/StandRig"
CONNECT_REPO = "https://github.com/sayaka-aiart/standrig-connect"
TWEET = "https://x.com/sayaka_aiart/status/2116165983090180419"

# 绘爱さやか在推文里交给 AI 的原文。
STANDRIG_PROMPT = """こちらのパーツ分けされたpsdファイルを元に
物理演算も入れた高稼働な2Dモデルを
StandRigを使って作成してみて
"""

_jobs: dict[str, dict] = {}
_stats = lambda: {"count": 0, "with_quotes": 0}


def bind(stats_fn) -> None:
    global _stats
    _stats = stats_fn


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_standrig_files() -> None:
    STANDRIG_DIR.mkdir(parents=True, exist_ok=True)
    (STANDRIG_DIR / "PROMPT.txt").write_text(STANDRIG_PROMPT, encoding="utf-8")
    readme = f"""# StandRig 交接

这是绘爱さやか（@sayaka_aiart）公开的做法：把已经分好层的 PSD 和下面这句提示词交给 AI，用 [StandRig]({STANDRIG_REPO}) 做带物理演算的高可动模型。她的记录是大约一个半小时。

推文：{TWEET}

## 提示词

```
{STANDRIG_PROMPT.strip()}
```

## 怎么用

1. 本仓库的工作室会先把一张立绘分成层，PSD 在 `assets/source/`。
2. 生成形象时，会把 PSD 和提示词抄进 `assets/standrig/工作区/`。
3. 把这个工作区交给支持 StandRig 的 AI 客户端，让它克隆 {STANDRIG_REPO} 再按提示词建模。
4. 要上摄像头追踪、口型和 OBS：用 [StandRig Connect]({CONNECT_REPO})，可把画面用 Spout2 送进 OBS。

当前直播页不依赖这一步。see-through 分层加上网页里的呼吸、眨眼、口型和视差，上传完就可以开播。StandRig 是要更高可动、带物理的时候再做。
"""
    (STANDRIG_DIR / "README.md").write_text(readme, encoding="utf-8")


def prepare_standrig(psd: Path) -> Path:
    write_standrig_files()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if psd.exists():
        target = WORK_DIR / psd.name
        if not target.exists() or target.stat().st_mtime < psd.stat().st_mtime:
            shutil.copy2(psd, target)
    note = f"""把这个文件夹交给 AI，让它用 StandRig 建模。

仓库：{STANDRIG_REPO}
直播输出（摄像头、口型、Spout2）：{CONNECT_REPO}
出处：{TWEET}

PSD：{psd.name if psd.exists() else "（还没有）"}

提示词见 PROMPT.txt，原文如下：

{STANDRIG_PROMPT.strip()}
"""
    (WORK_DIR / "交给AI.txt").write_text(note, encoding="utf-8")
    (WORK_DIR / "PROMPT.txt").write_text(STANDRIG_PROMPT, encoding="utf-8")
    return WORK_DIR


def prepare_existing_standrig() -> Path | None:
    write_standrig_files()
    if not SOURCE_DIR.exists():
        return None
    psds = sorted(SOURCE_DIR.glob("*.psd"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not psds:
        return None
    return prepare_standrig(psds[0])


def _latest_psd() -> Path | None:
    if not SOURCE_DIR.exists():
        return None
    psds = sorted(SOURCE_DIR.glob("*.psd"), key=lambda p: p.stat().st_mtime, reverse=True)
    return psds[0] if psds else None


def _manifest() -> dict | None:
    path = ROOT / str(config.get("avatar.manifest"))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _mb(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "不在"
    return f"{path.stat().st_size / 1024 / 1024:.1f} MB"


def build_info() -> dict:
    manifest = _manifest()
    psd = _latest_psd()
    stats = _stats() or {}
    sqlite = ROOT / "yousuu.sqlite"
    catalog = ROOT / str(config.get("catalog.path", "data/catalog.json"))
    port = int(config.get("server.port", 8765))
    host = str(config.get("server.host", "127.0.0.1"))
    return {
        "root": str(ROOT),
        "origin": f"http://{host}:{port}",
        "avatar": {
            "ready": manifest is not None,
            "name": (manifest or {}).get("name") or "",
            "layers": len((manifest or {}).get("layers") or []),
            "preview": "/assets/avatar/preview.png" if (ROOT / "assets" / "avatar" / "preview.png").exists() else "",
            "created": (manifest or {}).get("created") or "",
        },
        "database": {
            "sqlite": _rel(sqlite),
            "sqlite_size": _mb(sqlite),
            "sqlite_exists": sqlite.exists(),
            "catalog": _rel(catalog),
            "catalog_size": _mb(catalog),
            "books": int(stats.get("count") or 0),
            "with_quotes": int(stats.get("with_quotes") or 0),
        },
        "settings": {
            "yaml": "config/app.yaml",
            "env": ".env",
            "persona": "config/persona.md",
            "api_key_set": bool(config.get("deepseek.api_key")),
            "room_id": int(config.get("bilibili.room_id") or 0),
            "mode": str(config.get("director.mode") or "chat"),
        },
        "standrig": {
            "prompt": STANDRIG_PROMPT.strip(),
            "prompt_file": "assets/standrig/PROMPT.txt",
            "readme": "assets/standrig/README.md",
            "workdir": _rel(WORK_DIR) if WORK_DIR.exists() else "assets/standrig/工作区",
            "psd": _rel(psd) if psd else "",
            "repo": STANDRIG_REPO,
            "connect": CONNECT_REPO,
            "tweet": TWEET,
        },
        "show": {
            "dir": str(SHOW_DIR),
            "ready": (SHOW_DIR / "开始直播.bat").exists(),
        },
        "job": _running_job(),
    }


def _running_job() -> dict | None:
    for job in _jobs.values():
        if job.get("status") == "running":
            return _public_job(job)
    return None


def _public_job(job: dict) -> dict:
    log_lines = job.get("log") or []
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "log": log_lines[-80:],
        "error": job.get("error") or "",
        "fallback": bool(job.get("fallback")),
        "psd": job.get("psd") or "",
        "standrig": job.get("standrig") or "",
    }


def _bat(text: str) -> str:
    return text.replace("\n", "\r\n").lstrip("\r\n")


def _browser_candidates() -> list[Path]:
    return [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]


def open_app_window(url: str) -> str:
    for exe in _browser_candidates():
        if exe.is_file():
            subprocess.Popen(
                [str(exe), f"--app={url}", "--window-size=1920,1080", "--autoplay-policy=no-user-gesture-required"],
                close_fds=True,
            )
            return str(exe)
    if sys.platform == "win32":
        os_start(url)
        return "browser"
    subprocess.Popen(["xdg-open", url])
    return "browser"


def os_start(url: str) -> None:
    import os

    os.startfile(url)  # noqa: S606  本机打开自己的直播页


def create_show(name: str, launch: bool = True) -> dict:
    manifest = _manifest()
    if manifest is None:
        raise HTTPException(status_code=400, detail="还没有形象。请先上传一张立绘。")
    name = (name or manifest.get("name") or "薇拉").strip() or "薇拉"
    port = int(config.get("server.port", 8765))
    host = str(config.get("server.host", "127.0.0.1"))
    origin = f"http://{host}:{port}"
    SHOW_DIR.mkdir(parents=True, exist_ok=True)
    (SHOW_DIR / "设置").mkdir(exist_ok=True)
    (SHOW_DIR / "数据库").mkdir(exist_ok=True)

    yaml_src = ROOT / "config" / "app.yaml"
    if yaml_src.exists():
        shutil.copy2(yaml_src, SHOW_DIR / "设置" / "app.yaml")
    persona = ROOT / "config" / "persona.md"
    if persona.exists():
        shutil.copy2(persona, SHOW_DIR / "设置" / "persona.md")

    sqlite = ROOT / "yousuu.sqlite"
    catalog = ROOT / str(config.get("catalog.path", "data/catalog.json"))
    (SHOW_DIR / "数据库" / "说明.txt").write_text(
        "\n".join(
            [
                "书库不复制进这个文件夹，避免再占一份 600MB 以上的空间。",
                "开播时直接读项目里的这两份：",
                "",
                f"原始数据库  {sqlite}",
                f"大小        {_mb(sqlite)}",
                f"直播目录    {catalog}",
                f"大小        {_mb(catalog)}",
                "",
                "换电脑时把整个项目文件夹一起拷走，不要只拷「直播间」。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (SHOW_DIR / "设置" / "说明.txt").write_text(
        "\n".join(
            [
                "接口和人设在项目里，不在这里再存一份密钥。",
                "",
                f"参数   {ROOT / 'config' / 'app.yaml'}",
                f"密钥   {ROOT / '.env'}    （DEEPSEEK_API_KEY、B站 Cookie）",
                f"人设   {ROOT / 'config' / 'persona.md'}",
                "",
                "app.yaml 里的 api_key 保持空白。保存设置时密钥只写入 .env。",
                "这里的 app.yaml、persona.md 是一份快照，方便查看，改完要以项目里的那份为准。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    launcher = _bat(
        f"""@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
netstat -ano | findstr ":{port}" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "vera" "%ROOT%\\start.bat" stage
  exit /b 0
)
call "%ROOT%\\open_stage.bat"
"""
    )
    panel_bat = _bat(
        f"""@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
netstat -ano | findstr ":{port}" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "vera" "%ROOT%\\start.bat" panel
  exit /b 0
)
start "" {origin}/panel
"""
    )
    (SHOW_DIR / "开始直播.bat").write_text(launcher, encoding="utf-8")
    (SHOW_DIR / "打开控制台.bat").write_text(panel_bat, encoding="utf-8")

    info = {
        "name": name,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_root": str(ROOT),
        "stage": f"{origin}/stage",
        "panel": f"{origin}/panel",
        "studio": f"{origin}/",
        "avatar": str(config.get("avatar.manifest")),
        "layers": len(manifest.get("layers") or []),
        "database": _rel(sqlite),
        "catalog": _rel(catalog),
        "settings": "config/app.yaml",
        "secrets": ".env",
    }
    (SHOW_DIR / "show.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    (SHOW_DIR / "说明.txt").write_text(
        "\n".join(
            [
                f"{name} · 直播间",
                "",
                "下次直播：双击「开始直播.bat」。",
                "服务没开的话，它会自己把服务拉起来，再弹出 1920×1080 的直播窗口。",
                "改人设、密钥、房间号：双击「打开控制台.bat」，或回到工作室页面。",
                "",
                "这个文件夹要留在项目里面。数据库和接口设置在上一级：",
                f"  数据库  {sqlite}  （{_mb(sqlite)}）",
                f"  书目    {catalog}",
                f"  参数    {ROOT / 'config' / 'app.yaml'}",
                f"  密钥    {ROOT / '.env'}",
                "",
                "直播姬 / OBS 捕获弹出的那个窗口即可。",
                f"舞台地址  {origin}/stage",
                "",
            ]
        ),
        encoding="utf-8",
    )
    log.info("已生成直播间文件夹：%s", SHOW_DIR)
    if launch:
        open_app_window(f"{origin}/stage")
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", str(SHOW_DIR)])
    return info


async def _run(cmd: list[str], job: dict) -> int:
    job["log"].append("$ " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(ROOT),
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            job["log"].append(text)
            if len(job["log"]) > 400:
                del job["log"][:-300]
    return await proc.wait()


def _script(name: str) -> str:
    return str(ROOT / "scripts" / name)


async def _generate(job: dict, image: Path, name: str) -> None:
    try:
        psd: Path | None = None
        if image.suffix.lower() == ".psd":
            psd = image
            job["log"].append("[*] 这是分层 PSD，跳过 see-through，直接做成直播形象")
        else:
            code = await _run(
                [sys.executable, _script("seethrough.py"), "--image", str(image), "--resolution", "1024"],
                job,
            )
            meta = SOURCE_DIR / f"{image.stem}.json"
            if code == 0 and meta.exists():
                saved = json.loads(meta.read_text(encoding="utf-8"))
                if saved.get("psd"):
                    candidate = ROOT / saved["psd"]
                    if candidate.exists():
                        psd = candidate
            if psd is None:
                job["fallback"] = True
                job["log"].append("[!] 远程分层没有成功，改用整图单层。这样也能开播，只是没有前后发丝那些层。")
                code = await _run(
                    [
                        sys.executable,
                        _script("build_avatar.py"),
                        "--fallback-image",
                        str(image),
                        "--name",
                        name,
                        "--source",
                        str(image),
                    ],
                    job,
                )
                if code != 0:
                    raise RuntimeError("单层形象也没有生成成功")
                job["status"] = "done"
                return

        cmd = [sys.executable, _script("build_avatar.py"), "--psd", str(psd), "--name", name]
        if image.suffix.lower() != ".psd":
            cmd.extend(["--source", str(image)])
        code = await _run(cmd, job)
        if code != 0:
            raise RuntimeError("分层形象生成失败")
        work = prepare_standrig(psd)
        job["psd"] = _rel(psd)
        job["standrig"] = _rel(work)
        job["log"].append(f"[+] StandRig 工作区已写好：{work}")
        job["status"] = "done"
        log.info("形象已生成：%s", name)
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"].append(f"[x] {exc}")
        log.warning("形象生成失败：%s", exc)


@router.get("/api/studio")
async def api_studio() -> dict:
    return build_info()


@router.get("/api/avatar/job/{job_id}")
async def api_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="没有这个任务")
    return _public_job(job)


@router.post("/api/avatar/generate")
async def api_generate(file: UploadFile = File(...), name: str = Form("薇拉")) -> dict:
    if any(item.get("status") == "running" for item in _jobs.values()):
        raise HTTPException(status_code=409, detail="已经有一张图在生成，请等它结束")
    raw_name = Path(file.filename or "image.png").name
    safe = re.sub(r"[^\w.\-]+", "_", raw_name, flags=re.UNICODE).strip("._") or "image.png"
    if "." not in safe:
        safe += ".png"
    suffix = Path(safe).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".psd"}:
        raise HTTPException(status_code=400, detail="请上传 png、jpg、webp 或已经分好层的 psd")
    INBOX.mkdir(parents=True, exist_ok=True)
    dest = INBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 40MB")
    dest.write_bytes(data)
    character = (name or "薇拉").strip() or "薇拉"
    job_id = uuid.uuid4().hex[:8]
    job = {
        "id": job_id,
        "status": "running",
        "log": [f"[*] 已保存 {dest.name}"],
        "error": "",
        "fallback": False,
        "psd": "",
        "standrig": "",
    }
    _jobs[job_id] = job
    job["_task"] = asyncio.create_task(_generate(job, dest, character))
    return _public_job(job)


@router.post("/api/show/start")
async def api_show_start(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    name = ""
    if isinstance(body, dict):
        name = str(body.get("name") or "")
    info = create_show(name, launch=True)
    return {"ok": True, "show": info}


@router.post("/api/show/open")
async def api_show_open() -> dict:
    if not SHOW_DIR.exists():
        raise HTTPException(status_code=404, detail="还没有生成直播间文件夹")
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", str(SHOW_DIR)])
    return {"ok": True, "dir": str(SHOW_DIR)}
