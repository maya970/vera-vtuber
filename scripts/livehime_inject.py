"""把「薇拉舞台」作为浏览器源注入 B 站直播姬（LiveHime）的场景集合。

直播姬 8.x 的场景集合就是 OBS 格式，且自带 obs-browser 插件，
所以可以直接把网页放进去当素材，不需要窗口捕获。

用法（先退出直播姬）：
    python scripts/livehime_inject.py
    python scripts/livehime_inject.py --restore

注入后会：
  * 在 Scene 1 里加一个 1920x1080 的浏览器源「薇拉舞台」
  * 指向 http://127.0.0.1:8765/stage，并开启音频回传（reroute_audio）
原文件备份为 untitledv2.json.bak-vera
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_DATA_ROOT = Path.home() / "AppData" / "Local" / "bililive" / "User Data"
SOURCE_NAME = "薇拉舞台"
BACKUP_SUFFIX = ".bak-vera"


def find_collection() -> Path:
    if not USER_DATA_ROOT.exists():
        raise SystemExit(f"找不到直播姬用户目录：{USER_DATA_ROOT}")
    candidates = sorted(USER_DATA_ROOT.glob("*/untitledv2.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        candidates = sorted(USER_DATA_ROOT.glob("*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("找不到直播姬的场景集合文件，请先启动一次直播姬")
    return candidates[0]


def browser_source(url: str) -> dict:
    return {
        "prev_ver": 536870916,
        "name": SOURCE_NAME,
        "uuid": str(uuid.uuid4()),
        "id": "browser_source",
        "versioned_id": "browser_source",
        "settings": {
            "url": url,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "css": "body { margin: 0; background: transparent; }",
            "reroute_audio": True,
            "shutdown": False,
            "restart_when_active": False,
        },
        "mixers": 255,
        "sync": 0,
        "flags": 0,
        "volume": 1.0,
        "balance": 0.5,
        "enabled": True,
        "muted": False,
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "hotkeys": {},
        "deinterlace_mode": 0,
        "deinterlace_field_order": 0,
        "monitoring_type": 0,
        "private_settings": {},
    }


def scene_item(item_id: int) -> dict:
    return {
        "id": item_id,
        "name": SOURCE_NAME,
        "source": SOURCE_NAME,
        "visible": True,
        "locked": False,
        "rot": 0.0,
        "pos": {"x": 0.0, "y": 0.0},
        "scale": {"x": 1.0, "y": 1.0},
        "align": 5,
        "bounds": {"type": "OBS_BOUNDS_NONE", "alignment": 0, "x": 0.0, "y": 0.0},
        "bounds_align": 0,
        "bounds_crop": False,
        "crop_left": 0,
        "crop_top": 0,
        "crop_right": 0,
        "crop_bottom": 0,
        "scale_filter": "disable",
        "blend_method": "default",
        "blend_type": "normal",
        "show": True,
    }


def inject(path: Path, url: str) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.setdefault("sources", [])

    scene = next((s for s in sources if s.get("id") == "scene"), None)
    if scene is None:
        print("[x] 场景集合里没有 scene")
        return 2

    scene_name = scene.get("name", "Scene 1")
    settings = scene.setdefault("settings", {})
    items = settings.setdefault("items", [])

    # 更新或新增浏览器源
    existing = next((s for s in sources if s.get("name") == SOURCE_NAME), None)
    if existing:
        existing["settings"]["url"] = url
        existing["settings"]["reroute_audio"] = True
        print(f"[*] 更新已有浏览器源 {SOURCE_NAME}")
    else:
        sources.append(browser_source(url))
        print(f"[+] 新增浏览器源 {SOURCE_NAME}")

    if not any(i.get("source") == SOURCE_NAME for i in items):
        next_id = max([i.get("id", 0) for i in items], default=0) + 1
        items.append(scene_item(next_id))
        print(f"[+] 已加入场景「{scene_name}」")
    else:
        print(f"[*] 场景「{scene_name}」里已有该素材")

    data["current_scene"] = scene_name
    if {"name": scene_name} not in data.get("scene_order", []):
        data.setdefault("scene_order", []).append({"name": scene_name})

    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"[+] 已备份 {backup.name}")
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[+] 已写入 {path}")
    return 0


def restore(path: Path) -> int:
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        print(f"[x] 没有备份：{backup}")
        return 2
    shutil.copy2(backup, path)
    print(f"[+] 已恢复 {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/stage")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    path = find_collection()
    print(f"[*] 场景集合：{path}")
    if args.restore:
        return restore(path)
    return inject(path, args.url)


if __name__ == "__main__":
    sys.exit(main())
