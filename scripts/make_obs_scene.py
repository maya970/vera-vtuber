"""生成一份可直接导入 OBS 的场景集合：浏览器源 + 音频回传。

用法：
    python scripts/make_obs_scene.py
    python scripts/make_obs_scene.py --url http://127.0.0.1:8765/stage

生成 obs/薇拉书库.json。导入方式：OBS → 场景集合 → 导入 → 选择该文件。
若你的 OBS 版本导入失败，按 README 里的手动步骤添加浏览器源即可。
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "obs" / "薇拉书库.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/stage")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    uid = str(uuid.uuid4())
    scene_name = "薇拉书库"
    source_name = "薇拉舞台"

    browser = {
        "balance": 0.5,
        "deinterlace_field_order": 0,
        "deinterlace_mode": 0,
        "enabled": True,
        "filters": [],
        "flags": 0,
        "hotkeys": {},
        "id": "browser_source",
        "mixers": 0,
        "monitoring_type": 0,
        "muted": False,
        "name": source_name,
        "prev_ver": 520093697,
        "private_settings": {},
        "push-to-mute": False,
        "push-to-mute-delay": 0,
        "push-to-talk": False,
        "push-to-talk-delay": 0,
        "settings": {
            "fps": 30,
            "height": 1080,
            "width": 1920,
            "url": args.url,
            "shutdown": False,
            "restart_when_active": False,
            "css": "body { margin: 0; background: transparent; }",
            "reroute_audio": True,
        },
        "sync": 0,
        "uuid": uid,
        "versioned_id": "browser_source",
        "volume": 1.0,
    }

    scene = {
        "name": scene_name,
        "items": [
            {
                "align": 5,
                "blend_method": "default",
                "blend_type": "normal",
                "bounds": {"type": "OBS_BOUNDS_NONE"},
                "bounds_align": 0,
                "bounds_crop": False,
                "crop_bottom": 0,
                "crop_left": 0,
                "crop_right": 0,
                "crop_top": 0,
                "id": 1,
                "locked": False,
                "name": source_name,
                "pos": {"x": 0.0, "y": 0.0},
                "private_settings": {},
                "rot": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "scale_filter": "disable",
                "source_uuid": uid,
                "visible": True,
            }
        ],
    }

    collection = {
        "current_program_scene": scene_name,
        "current_scene": scene_name,
        "current_transition": "Fade",
        "groups": [],
        "name": "薇拉书库",
        "preview_locked": False,
        "quick_transitions": [],
        "saved_projectors": [],
        "scaling_enabled": False,
        "scaling_level": 0,
        "scaling_off_x": 0.0,
        "scaling_off_y": 0.0,
        "scene_order": [{"name": scene_name}],
        "sources": [browser],
        "transition_duration": 300,
        "transitions": [],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(collection, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"[+] 已生成 OBS 场景集合：{out}")
    print(f"    OBS → 场景集合 → 导入 → 选择该文件（浏览器源 {args.url}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
