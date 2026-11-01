"""配置读写：config/app.yaml，支持 .env 环境变量覆盖敏感项。"""
from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "app.yaml"
ENV_PATH = ROOT / ".env"


def _write_env(updates: dict[str, str]) -> None:
    """把密钥写进 .env，避免保存配置时落进 app.yaml。"""
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    done: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                done.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in done:
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

_DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "temperature": 1.0,
        "max_tokens": 320,
        "timeout": 60,
    },
    "bilibili": {
        "enable": True,
        "room_id": 0,
        "uid": 0,
        "sessdata": "",
        "buvid3": "",
        "reconnect": True,
        "read_gifts": True,
        "read_enter": True,
        "ignore_users": [],
    },
    "tts": {
        "engine": "edge",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": "+10%",
        "pitch": "+12Hz",
        "volume": "+0%",
        "cache": True,
    },
    "director": {
        "mode": "chat",
        "idle_book_after": 30,
        "audience_window": 600,
        "danmaku_priority": True,
        "max_queue": 6,
        "per_user_cooldown": 25,
        "global_cooldown": 1.5,
        "intro_min_score": 6.5,
        "intro_min_raters": 20,
        "history_ratio": 0.75,
        "max_sentences": 5,
        "super_chat_first": True,
    },
    "avatar": {
        "manifest": "assets/avatar/manifest.json",
        "camera_fov": 26,
        "parallax": 1.0,
        "blink_interval": [3.2, 7.5],
        "idle_sway": 1.0,
    },
    "catalog": {"path": "data/catalog.json", "top_k": 6},
    "game": {"exe": ""},
    "chat": {"platform": "bilibili", "target": ""},
    "twitch": {"token": ""},
    "youtube": {"api_key": ""},
    "ui": {"show_danmaku": True, "show_book_card": True, "subtitle": True, "theme": "studio"},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        raw: dict[str, Any] = {}
        if self.path.exists():
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._data = deep_merge(_DEFAULTS, raw)
        # 环境变量优先
        if os.environ.get("DEEPSEEK_API_KEY"):
            self._data["deepseek"]["api_key"] = os.environ["DEEPSEEK_API_KEY"]
        if os.environ.get("BILI_SESSDATA"):
            self._data["bilibili"]["sessdata"] = os.environ["BILI_SESSDATA"]
        if os.environ.get("BILI_BUVID3"):
            self._data["bilibili"]["buvid3"] = os.environ["BILI_BUVID3"]
        if os.environ.get("BILI_ROOM_ID"):
            self._data["bilibili"]["room_id"] = int(os.environ["BILI_ROOM_ID"] or 0)
        if "GAME_EXE" in os.environ:
            self._data.setdefault("game", {})["exe"] = os.environ.get("GAME_EXE", "").strip()
        if "CHAT_TARGET" in os.environ:
            self._data.setdefault("chat", {})["target"] = os.environ.get("CHAT_TARGET", "").strip()
        if os.environ.get("TWITCH_TOKEN"):
            self._data.setdefault("twitch", {})["token"] = os.environ["TWITCH_TOKEN"].strip()
        if os.environ.get("YOUTUBE_API_KEY"):
            self._data.setdefault("youtube", {})["api_key"] = os.environ["YOUTUBE_API_KEY"].strip()

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        with self._lock:
            node = self._data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def update(self, patch: dict[str, Any]) -> None:
        with self._lock:
            self._data = deep_merge(self._data, patch)
            self.save()

    def _dump_public(self) -> dict[str, Any]:
        """磁盘上的配置不留密钥，密钥只进 .env。"""
        data = copy.deepcopy(self._data)
        updates: dict[str, str] = {}
        deepseek = data.setdefault("deepseek", {})
        key = str(deepseek.get("api_key") or "")
        if key:
            updates["DEEPSEEK_API_KEY"] = key
        deepseek["api_key"] = ""
        bili = data.setdefault("bilibili", {})
        sess = str(bili.get("sessdata") or "")
        if sess:
            updates["BILI_SESSDATA"] = sess
        bili["sessdata"] = ""
        room = int(bili.get("room_id") or 0)
        if room:
            updates["BILI_ROOM_ID"] = str(room)
        bili["room_id"] = 0
        buvid = str(bili.get("buvid3") or "")
        if buvid:
            updates["BILI_BUVID3"] = buvid
        bili["buvid3"] = ""
        game = data.setdefault("game", {})
        exe = str(game.get("exe") or "").strip()
        updates["GAME_EXE"] = exe
        game["exe"] = ""
        if exe:
            os.environ["GAME_EXE"] = exe
        else:
            os.environ.pop("GAME_EXE", None)
        chat_cfg = data.setdefault("chat", {})
        target = str(chat_cfg.get("target") or "").strip()
        updates["CHAT_TARGET"] = target
        chat_cfg["target"] = ""
        if target:
            os.environ["CHAT_TARGET"] = target
        else:
            os.environ.pop("CHAT_TARGET", None)
        twitch = data.setdefault("twitch", {})
        token = str(twitch.get("token") or "").strip()
        if token:
            updates["TWITCH_TOKEN"] = token
            os.environ["TWITCH_TOKEN"] = token
        twitch["token"] = ""
        youtube = data.setdefault("youtube", {})
        yt_key = str(youtube.get("api_key") or "").strip()
        if yt_key:
            updates["YOUTUBE_API_KEY"] = yt_key
            os.environ["YOUTUBE_API_KEY"] = yt_key
        youtube["api_key"] = ""
        if updates:
            _write_env(updates)
        return data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            dumped = self._dump_public()
            self.path.write_text(
                yaml.safe_dump(dumped, allow_unicode=True, sort_keys=False, width=120),
                encoding="utf-8",
            )

    def public(self) -> dict[str, Any]:
        """给前端的安全视图：隐藏密钥。"""
        data = copy.deepcopy(self._data)
        key = data.get("deepseek", {}).get("api_key") or ""
        data["deepseek"]["api_key_set"] = bool(key)
        data["deepseek"]["api_key"] = ""
        sess = data.get("bilibili", {}).get("sessdata") or ""
        data["bilibili"]["sessdata_set"] = bool(sess)
        data["bilibili"]["sessdata"] = ""
        twitch = data.setdefault("twitch", {})
        twitch["token_set"] = bool(twitch.get("token"))
        twitch["token"] = ""
        youtube = data.setdefault("youtube", {})
        youtube["api_key_set"] = bool(youtube.get("api_key"))
        youtube["api_key"] = ""
        return data

    @property
    def root(self) -> Path:
        return ROOT


config = Config()


def stream_mode() -> str:
    """chat 闲聊，game 玩游戏，book 讲书。认不出的值都当成闲聊。"""
    mode = str(config.get("director.mode") or "chat").strip().lower()
    return mode if mode in {"chat", "game", "book"} else "chat"
