"""FastAPI 服务：直播舞台、控制台、WebSocket、弹幕与导演的粘合层。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .brain import brain
from .catalog import Catalog
from .config import ROOT, config, stream_mode
from .danmaku import BiliDanmaku, DanmakuEvent
from .director import Director, StageHub
from .store import store
from . import studio
from .tts import tts

WEB_DIR = ROOT / "web"
ASSET_DIR = ROOT / "assets"
AUDIO_DIR = ROOT / "data" / "audio"
AVATAR_DIR = ASSET_DIR / "avatar"

LOG_BUFFER: deque[dict] = deque(maxlen=400)


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            LOG_BUFFER.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001
            pass


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RingHandler) for h in root.handlers):
        ring = RingHandler()
        ring.setLevel(logging.INFO)
        root.addHandler(ring)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)


setup_logging()
log = logging.getLogger("vera")

catalog = Catalog()
hub = StageHub()
director = Director(hub, catalog)


async def _bili_callback(ev: DanmakuEvent) -> None:
    await director.on_event(ev)


bili = BiliDanmaku(_bili_callback)


def load_manifest() -> dict | None:
    path = ROOT / str(config.get("avatar.manifest"))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("读取形象清单失败：%s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    for folder in (AUDIO_DIR, AVATAR_DIR, ROOT / "data", WEB_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    catalog.load()
    try:
        studio.prepare_existing_standrig()
    except Exception as exc:  # noqa: BLE001
        log.warning("StandRig 交接文件没有写好：%s", exc)
    await director.start()
    if config.get("bilibili.enable") and int(config.get("bilibili.room_id") or 0):
        await bili.start()
    log.info("薇拉已就绪：http://%s:%s", config.get("server.host"), config.get("server.port"))
    try:
        yield
    finally:
        await director.stop()
        await bili.stop()


app = FastAPI(title="薇拉 · 书库直播间", lifespan=lifespan)
studio.bind(catalog.stats)
app.include_router(studio.router)

app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")
app.mount("/assets", StaticFiles(directory=str(ASSET_DIR)), name="assets")
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(WEB_DIR / "studio.html")


@app.get("/stage")
async def stage() -> FileResponse:
    return FileResponse(WEB_DIR / "stage.html")


@app.get("/panel")
async def panel() -> FileResponse:
    return FileResponse(WEB_DIR / "panel.html")


@app.get("/api/state")
async def api_state() -> dict:
    return {
        "director": director.state(),
        "bili": bili.state(),
        "catalog": catalog.stats(),
        "brain_ready": brain.ready,
        "tts_voice": tts.voice,
        "config": config.public(),
        "manifest": bool(load_manifest()),
        "played": store.played_recent(8),
        "time": time.time(),
    }


@app.get("/api/avatar")
async def api_avatar() -> JSONResponse:
    manifest = load_manifest()
    if manifest is None:
        return JSONResponse({"error": "尚未生成形象分层"}, status_code=404)
    return JSONResponse(manifest)


@app.get("/api/catalog")
async def api_catalog(q: str = "", limit: int = 12) -> dict:
    if q:
        books = catalog.search(q, k=max(1, min(limit, 50)))
    else:
        books = catalog.top_rated(limit)
    return {"query": q, "books": [b.to_dict() for b in books], "stats": catalog.stats()}


@app.post("/api/catalog/reload")
async def api_catalog_reload() -> dict:
    catalog.load()
    return {"ok": True, "stats": catalog.stats()}


@app.post("/api/config")
async def api_config(request: Request) -> dict:
    patch = await request.json()
    allowed = {"deepseek", "bilibili", "tts", "director", "avatar", "ui", "catalog"}
    patch = {k: v for k, v in (patch or {}).items() if k in allowed}
    if "deepseek" in patch and "api_key" in patch["deepseek"] and not patch["deepseek"]["api_key"]:
        patch["deepseek"].pop("api_key")
    if "bilibili" in patch and "sessdata" in patch["bilibili"] and not patch["bilibili"]["sessdata"]:
        patch["bilibili"].pop("sessdata")
    config.update(patch)
    brain.reload_persona()
    if stream_mode() != "book":
        await hub.broadcast({"type": "book", "action": "hide"})
    await hub.broadcast({"type": "config", "config": config.public()})
    return {"ok": True, "config": config.public()}


@app.post("/api/say")
async def api_say(request: Request) -> dict:
    body = await request.json()
    text = (body.get("text") or "").strip()
    user = (body.get("user") or "测试").strip()
    if not text:
        return {"ok": False, "error": "内容为空"}
    await director.say(text, user=user)
    return {"ok": True}


@app.post("/api/next")
async def api_next() -> dict:
    await director.next_book()
    return {"ok": True}


@app.post("/api/danmaku/test")
async def api_test_danmaku(request: Request) -> dict:
    body = await request.json()
    ev = DanmakuEvent(
        "danmaku",
        user=(body.get("user") or "测试观众").strip(),
        text=(body.get("text") or "").strip(),
        uid=0,
    )
    if not ev.text:
        return {"ok": False, "error": "弹幕为空"}
    await director.on_event(ev)
    return {"ok": True}


@app.post("/api/bili/restart")
async def api_bili_restart() -> dict:
    await bili.stop()
    bili.room_id = int(config.get("bilibili.room_id") or 0)
    await bili.start()
    return {"ok": True, "bili": bili.state()}


@app.post("/api/bili/stop")
async def api_bili_stop() -> dict:
    await bili.stop()
    return {"ok": True, "bili": bili.state()}


@app.get("/api/health")
async def api_health() -> dict:
    brain_ok, brain_msg = await brain.check() if brain.ready else (False, "未配置 API Key")
    tts_ok, tts_msg = await tts.health()
    return {
        "deepseek": {"ok": brain_ok, "message": brain_msg},
        "tts": {"ok": tts_ok, "message": tts_msg},
        "bili": bili.state(),
        "catalog": catalog.stats(),
    }


@app.get("/api/voices")
async def api_voices() -> dict:
    import edge_tts

    try:
        voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=15)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "voices": []}
    items = [
        {"name": v["ShortName"], "gender": v.get("Gender"), "locale": v.get("Locale")}
        for v in voices
        if str(v.get("Locale", "")).startswith("zh")
    ]
    items.sort(key=lambda x: x["name"])
    return {"ok": True, "voices": items}


@app.get("/api/logs")
async def api_logs(since: float = 0) -> dict:
    return {"logs": [x for x in LOG_BUFFER if x["ts"] > since], "now": time.time()}


@app.get("/api/transcript")
async def api_transcript(limit: int = 30) -> dict:
    return {"items": store.recent(limit)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hub.add(ws)
    await ws.send_json(
        {
            "type": "init",
            "config": config.public(),
            "manifest": load_manifest(),
            "state": director.state(),
            "bili": bili.state(),
            "catalog": catalog.stats(),
        }
    )
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "ack":
                hub.ack(str(msg.get("id")))
            elif mtype == "viewport":
                log.info("舞台视口：%sx%s (dpr=%s)", msg.get("w"), msg.get("h"), msg.get("dpr"))
            elif mtype == "ping":
                await ws.send_json({"type": "pong", "t": time.time()})
            elif mtype == "request_init":
                await ws.send_json(
                    {
                        "type": "init",
                        "config": config.public(),
                        "manifest": load_manifest(),
                        "state": director.state(),
                        "bili": bili.state(),
                        "catalog": catalog.stats(),
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("WS 异常：%s", exc)
    finally:
        hub.remove(ws)
