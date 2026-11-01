"""语音合成：edge-tts（微软在线音色），带缓存与逐词时间轴。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from .config import ROOT, config

log = logging.getLogger("vera.tts")
AUDIO_DIR = ROOT / "data" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

_PUNCT_ONLY = re.compile(r"^[\s，。！？!?、；;：:…—\-·,.]+$")


@dataclass
class Clip:
    text: str
    file: str
    duration: float
    words: list[dict]

    def to_dict(self) -> dict:
        return {"text": self.text, "file": self.file, "duration": round(self.duration, 3), "words": self.words}


def _cache_key(text: str, voice: str, rate: str, pitch: str, volume: str) -> str:
    raw = f"{text}|{voice}|{rate}|{pitch}|{volume}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


class TTS:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def voice(self) -> str:
        return config.get("tts.voice", "zh-CN-XiaoyiNeural")

    async def health(self) -> tuple[bool, str]:
        try:
            voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=15)
            names = {v["ShortName"] for v in voices}
            ok = self.voice in names
            return ok, f"{len(names)} 个音色" + ("" if ok else f"，缺少 {self.voice}")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:160]

    async def synth(self, text: str) -> Clip:
        text = (text or "").strip()
        voice = self.voice
        rate = config.get("tts.rate", "+0%")
        pitch = config.get("tts.pitch", "+0Hz")
        volume = config.get("tts.volume", "+0%")
        key = _cache_key(text, voice, rate, pitch, volume)
        mp3 = AUDIO_DIR / f"{key}.mp3"
        meta = AUDIO_DIR / f"{key}.json"

        if config.get("tts.cache", True) and mp3.exists() and meta.exists():
            try:
                payload = json.loads(meta.read_text(encoding="utf-8"))
                return Clip(text=text, file=mp3.name, duration=payload["duration"], words=payload["words"])
            except Exception:  # noqa: BLE001
                pass

        if not text or _PUNCT_ONLY.match(text):
            return Clip(text=text, file="", duration=0.0, words=[])

        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    return await self._synth_once(text, voice, rate, pitch, volume, mp3, meta)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    log.warning("edge-tts 失败(第%d次): %s", attempt + 1, exc)
                    await asyncio.sleep(0.8)
            raise RuntimeError(f"TTS 合成失败: {last_error}")

    async def _synth_once(
        self, text: str, voice: str, rate: str, pitch: str, volume: str, mp3: Path, meta: Path
    ) -> Clip:
        communicate = edge_tts.Communicate(
            text, voice, rate=rate, pitch=pitch, volume=volume, boundary="WordBoundary"
        )
        audio = bytearray()
        words: list[dict] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(
                    {
                        "text": chunk.get("text", ""),
                        "start": round(chunk.get("offset", 0) / 10_000_000, 3),
                        "end": round((chunk.get("offset", 0) + chunk.get("duration", 0)) / 10_000_000, 3),
                    }
                )
        if not audio:
            raise RuntimeError("没有返回音频")
        tmp = mp3.with_suffix(".tmp")
        tmp.write_bytes(bytes(audio))
        tmp.replace(mp3)
        duration = words[-1]["end"] if words else max(0.6, len(text) * 0.16)
        payload = {"duration": round(duration, 3), "words": words, "voice": voice}
        meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        log.info("TTS %.1fs %s", duration, text[:24])
        return Clip(text=text, file=mp3.name, duration=duration, words=words)

    async def synth_all(self, sentences: list[str]) -> list[Clip]:
        clips: list[Clip] = []
        for sentence in sentences:
            try:
                clips.append(await self.synth(sentence))
            except Exception as exc:  # noqa: BLE001
                log.error("合成句子失败: %s / %s", sentence[:20], exc)
                clips.append(Clip(text=sentence, file="", duration=max(1.0, len(sentence) * 0.18), words=[]))
        return clips


tts = TTS()
