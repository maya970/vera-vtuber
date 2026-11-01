"""直播导演：弹幕优先、空闲讲书、礼物答谢、打断与节奏控制。"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .brain import brain, sanitize, split_sentences
from .catalog import Book, Catalog
from .config import config, stream_mode
from .danmaku import DanmakuEvent
from .store import store
from .tts import Clip, tts

log = logging.getLogger("vera.director")


@dataclass(order=True)
class Job:
    priority: int
    seq: int
    payload: dict = field(compare=False)


class StageHub:
    """负责把语音/状态推给舞台页面，并等待播放完成。"""

    def __init__(self) -> None:
        self.clients: set[Any] = set()
        self._ack: dict[str, asyncio.Future] = {}

    def add(self, ws: Any) -> None:
        self.clients.add(ws)

    def remove(self, ws: Any) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def ack(self, speech_id: str) -> None:
        fut = self._ack.get(speech_id)
        if fut and not fut.done():
            fut.set_result(True)

    async def play_speech(self, clips: list[dict], meta: dict | None = None) -> None:
        total = sum(float(c.get("duration") or 0) for c in clips) or 1.0
        speech_id = f"s{int(time.time() * 1000) % 10_000_000}"
        payload = {"type": "speech", "id": speech_id, "clips": clips, "meta": meta or {}}
        if not self.clients:
            await asyncio.sleep(min(total, 12))
            return
        fut = asyncio.get_running_loop().create_future()
        self._ack[speech_id] = fut
        await self.broadcast(payload)
        try:
            await asyncio.wait_for(fut, timeout=total + 2.5)
        except asyncio.TimeoutError:
            log.debug("等待播放完成超时 %s", speech_id)
        finally:
            self._ack.pop(speech_id, None)


class Director:
    def __init__(self, hub: StageHub, catalog: Catalog):
        self.hub = hub
        self.catalog = catalog
        self.queue: asyncio.PriorityQueue[Job] = asyncio.PriorityQueue()
        self._seq = 0
        self._task: asyncio.Task | None = None
        self._running = False
        self.busy = False
        self.thinking = False
        self.current_book: Book | None = None
        self.now_playing: dict | None = None
        self.last_activity = time.time()
        self._user_ts: dict[str, float] = {}
        self._last_global = 0.0
        self._recent_texts: list[str] = []
        self.stats = {"replies": 0, "books": 0, "skipped": 0, "errors": 0, "api_calls": 0}
        self.log: list[dict] = []
        self._idle_notice = False
        self.viewers = 0
        self.audience_until = 0.0

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="director")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ---------- 对外 ----------
    def _touch_audience(self) -> None:
        self.audience_until = time.time() + float(config.get("director.audience_window", 600))

    def audience_active(self) -> bool:
        """有在线观众，或最近有人互动（弹幕/进场/礼物）。"""
        return self.viewers > 0 or time.time() < self.audience_until

    async def on_event(self, ev: DanmakuEvent) -> None:
        if ev.kind == "watching":
            try:
                online = int(ev.meta.get("online") or 0)
                watched = int(ev.meta.get("watched") or 0)
            except (TypeError, ValueError):
                return
            self.viewers = max(online, watched)
            if self.viewers > 0:
                self._touch_audience()
            return
        self._touch_audience()
        if ev.kind in ("enter", "follow"):
            await self._on_enter(ev)
            return
        if ev.kind == "gift":
            await self._on_gift(ev)
            return
        if ev.kind == "guard":
            await self._enqueue(1, {"kind": "thanks", "event": ev})
            return
        if ev.kind == "super_chat":
            await self._enqueue(0, {"kind": "danmaku", "event": ev, "super": True})
            return
        await self._enqueue(2, {"kind": "danmaku", "event": ev})

    async def say(self, text: str, user: str = "测试") -> None:
        ev = DanmakuEvent("danmaku", user=user, text=text)
        await self._enqueue(2, {"kind": "danmaku", "event": ev, "force": True})

    async def next_book(self) -> None:
        self._touch_audience()
        if stream_mode() == "book":
            await self._enqueue(3, {"kind": "book", "force": True, "force_api": True})
        else:
            await self._enqueue(3, {"kind": "idle", "force": True})

    def state(self) -> dict:
        return {
            "busy": self.busy,
            "thinking": self.thinking,
            "queue": self.queue.qsize(),
            "current_book": self.current_book.to_dict() if self.current_book else None,
            "now_playing": self.now_playing,
            "stats": self.stats,
            "viewers": self.viewers,
            "audience": self.audience_active(),
            "last_activity": self.last_activity,
        }

    # ---------- 队列 ----------
    async def _enqueue(self, priority: int, payload: dict) -> None:
        if self.queue.qsize() >= int(config.get("director.max_queue", 6)):
            self.stats["skipped"] += 1
            return
        self._seq += 1
        await self.queue.put(Job(priority, self._seq, payload))
        await self.hub.broadcast({"type": "queue", "size": self.queue.qsize()})

    async def _loop(self) -> None:
        while self._running:
            try:
                job = await asyncio.wait_for(self.queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                await self._maybe_idle()
                continue
            except asyncio.CancelledError:
                raise

            self.busy = True
            self._idle_notice = False
            try:
                await self._handle(job.payload)
                self.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                log.exception("导演任务异常：%s", exc)
                await asyncio.sleep(0.5)
            finally:
                self.busy = False
                self.thinking = False
                self.last_activity = time.time()
                await self.hub.broadcast({"type": "status", **self.state()})

    # ---------- 空闲 ----------
    async def _maybe_idle(self) -> None:
        idle_for = time.time() - self.last_activity
        threshold = float(config.get("director.idle_book_after", 30))
        if idle_for >= threshold and not self._idle_notice:
            self._idle_notice = True
            kind = "book" if stream_mode() == "book" else "idle"
            await self._enqueue(3, {"kind": kind, "auto": True})

    # ---------- 处理 ----------
    async def _handle(self, payload: dict) -> None:
        kind = payload.get("kind")
        if kind == "danmaku":
            await self._handle_danmaku(payload)
        elif kind == "book":
            await self._handle_book(payload)
        elif kind == "idle":
            await self._handle_idle(payload)
        elif kind == "thanks":
            await self._handle_gift(payload["event"])

    async def _handle_danmaku(self, payload: dict) -> None:
        ev: DanmakuEvent = payload["event"]
        text = ev.text.strip()
        if not payload.get("force"):
            if len(text) > 120:
                return
            now = time.time()
            if now - self._last_global < float(config.get("director.global_cooldown", 1.5)):
                return
            cooldown = float(config.get("director.per_user_cooldown", 25))
            if ev.user and now - self._user_ts.get(ev.user, 0) < cooldown and ev.kind != "super_chat":
                return
            if text in self._recent_texts[-20:]:
                return
            self._user_ts[ev.user] = now
            self._last_global = now
            self._recent_texts.append(text)

        self._idle_notice = False
        self.thinking = True
        await self.hub.broadcast(
            {"type": "danmaku", "user": ev.user, "text": text, "kind": ev.kind, "price": ev.price}
        )
        await self.hub.broadcast({"type": "status", **self.state()})

        mode = stream_mode()
        books: list = []

        try:
            if mode == "book":
                books = self.catalog.search(text, k=int(config.get("catalog.top_k", 6)) * 2)
                books = [b for b in books if b.score >= 6.5]
                if len(books) < 2:
                    seen = {b.id for b in books}
                    books += [b for b in self.catalog.top_rated(4) if b.id not in seen]
                if not books:
                    books = self.catalog.top_rated(4)
                if brain.ready:
                    self.stats["api_calls"] += 1
                    reply = await brain.reply_once(text, ev.user or "观众", books, self.current_book)
                else:
                    reply = self._local_reply(text, ev.user, books)
            elif brain.ready:
                self.stats["api_calls"] += 1
                reply = await brain.reply_once(text, ev.user or "观众", None, None)
            else:
                reply = self._local_chat(text, ev.user, mode)
        except Exception as exc:  # noqa: BLE001
            log.warning("DeepSeek 失败：%s", exc)
            self.stats["errors"] += 1
            reply = self._local_reply(text, ev.user, books) if mode == "book" else self._local_chat(text, ev.user, mode)

        if not reply:
            return
        self.thinking = False
        self.stats["replies"] += 1
        store.log("assistant", reply, user=ev.user, kind=ev.kind)
        if books:
            store.remember(ev.user or str(ev.uid), ev.user, f"问过：{text[:20]}")
        await self._speak_text(reply, meta={"kind": "danmaku", "user": ev.user})

    async def _handle_book(self, payload: dict) -> None:
        self._idle_notice = False
        played = store.played_ids()
        book = self.catalog.pick_next(
            played,
            min_score=float(config.get("director.intro_min_score", 7.5)),
            min_raters=int(config.get("director.intro_min_raters", 200)),
            history_ratio=float(config.get("director.history_ratio", 0.75)),
        )
        if not book:
            await self._speak_text("书库的目录还没建好，等一下再来翻。", meta={"kind": "system"})
            return
        self.current_book = book
        self.now_playing = book.to_dict()
        await self.hub.broadcast({"type": "book", "action": "show", "book": book.to_dict()})
        await self.hub.broadcast({"type": "status", **self.state()})

        try:
            use_api = brain.ready and (self.audience_active() or payload.get("force_api"))
            if use_api:
                self.stats["api_calls"] += 1
                text = await brain.book_intro_once(book)
            else:
                text = self._local_intro(book)
                log.info("直播间没人，讲书用本地话术（省 token）")
        except Exception as exc:  # noqa: BLE001
            log.warning("讲书稿失败：%s", exc)
            self.stats["errors"] += 1
            text = self._local_intro(book)

        self.stats["books"] += 1
        store.mark_played(book.id, book.title, book.score)
        store.log("assistant", text, kind="book")
        await self._speak_text(text, meta={"kind": "book", "book": book.to_dict()})
        self.now_playing = None

    async def _handle_idle(self, payload: dict) -> None:
        self._idle_notice = False
        mode = stream_mode()
        if mode == "book":
            await self._handle_book(payload)
            return
        lines = self._GAME_IDLE if mode == "game" else self._CHAT_IDLE
        text = lines[self.stats["replies"] % len(lines)]
        try:
            if brain.ready and (self.audience_active() or payload.get("force")):
                self.stats["api_calls"] += 1
                spoken = await brain.idle_once(mode)
                if spoken:
                    text = spoken
            elif not brain.ready:
                log.info("没有 API Key，空闲时用固定话术")
        except Exception as exc:  # noqa: BLE001
            log.warning("空闲发言失败：%s", exc)
            self.stats["errors"] += 1
        self.stats["replies"] += 1
        store.log("assistant", text, kind="idle")
        await self._speak_text(text, meta={"kind": "idle"})

    async def _handle_gift(self, ev: DanmakuEvent) -> None:
        if ev.price and ev.price < 1:
            return
        name = ev.text or "礼物"
        if stream_mode() == "book":
            line = f"谢谢{ev.user}的{name}，我记下了。"
        else:
            line = f"谢谢{ev.user}的{name}。"
        await self._speak_text(line, meta={"kind": "gift", "user": ev.user})

    async def _on_enter(self, ev: DanmakuEvent) -> None:
        await self.hub.broadcast({"type": "enter", "user": ev.user, "kind": ev.kind})
        if ev.kind == "follow" and not self.busy:
            if stream_mode() == "book":
                line = f"欢迎{ev.user}，进来就坐。"
            else:
                line = f"欢迎{ev.user}。"
            await self._speak_text(line, meta={"kind": "welcome"})

    async def _on_gift(self, ev: DanmakuEvent) -> None:
        await self.hub.broadcast({"type": "gift", "user": ev.user, "text": ev.text, "price": ev.price})
        await self._enqueue(1, {"kind": "thanks", "event": ev})

    # ---------- 说话 ----------
    async def _speak_text(self, text: str, meta: dict | None = None) -> None:
        limit = int(config.get("director.max_sentences", 5))
        sentences = split_sentences(text, limit=limit)
        if not sentences:
            return
        await self.hub.broadcast({"type": "thinking", "on": False})
        clips: list[Clip] = []
        try:
            clips = await tts.synth_all(sentences)
        except Exception as exc:  # noqa: BLE001
            log.error("TTS 全部失败：%s", exc)
        payload = [c.to_dict() for c in clips]
        if not any(c.file for c in clips):
            payload = [
                {"text": s, "file": "", "duration": max(1.0, len(s) * 0.18), "words": []}
                for s in sentences
            ]
        await self.hub.play_speech(payload, meta=meta)

    # ---------- 无 Key / 无人时兜底 ----------
    _CHAT_IDLE = (
        "直播间安静了一会儿。想聊什么，直接打在弹幕里。",
        "我先坐这儿。有人来了我就接话。",
        "不着急，你们想到什么再说。",
    )
    _GAME_IDLE = (
        "这局我看着呢。有操作想让我评，打在弹幕里。",
        "游戏画面在旁边。我看不见具体按键，你们喊我我再接。",
        "打得乱也好，打得好也好，说一声我就知道。",
    )
    _INTRO_LEADS = ("今天翻到一本", "书架上抽到一本", "这本得说说")
    _OPINION_WORDS = ("喜欢", "好看", "推荐", "文笔", "人物", "剧情", "设定", "节奏",
                      "精彩", "值得", "读完", "看完", "追", "仙草", "神作", "粮草")
    _INTRO_TEMPLATES = (
        "今天从书架上抽到这本《{title}》，{author}写的。"
        "书库给它打了{score}分，{raters}个人评过。"
        "先说好，不剧透——合口味的，扣个一，我记在借阅卡上。",
        "《{title}》，{author}。{score}分，{raters}人评过，{words}万字，{status}。"
        "标签是{tags}。想听的扣一，我翻给你看。",
        "翻到一本老书——《{title}》。{score}分在书库里算高的，{raters}个人给它评过。"
        "{tags}这一挂的，应该会喜欢。",
    )

    @classmethod
    def _local_intro(cls, book: Book) -> str:
        quotes = [q for q in (book.quotes or []) if 8 <= len(q) <= 60]
        # 优先挑「像评价」的句子，避免把书中台词当成读者感受
        opinions = [q for q in quotes if any(w in q for w in cls._OPINION_WORDS)]
        quote = min(opinions or quotes, key=len) if quotes else ""
        if quote:
            quote = quote.rstrip("。！？!?；;，,、 　")
            if len(quote) > 34:
                quote = quote[:34].rstrip("，。、；：,.") + "…"
            lead = cls._INTRO_LEADS[book.id % len(cls._INTRO_LEADS)]
            return (
                f"{lead}——《{book.title}》，{book.author or '佚名'}。"
                f"{book.score:g}分，{book.raters}人评过。"
                f"有读者说「{quote}」。先说好，不剧透，想看的扣个一。"
            )
        template = cls._INTRO_TEMPLATES[book.id % len(cls._INTRO_TEMPLATES)]
        return template.format(
            title=book.title,
            author=book.author or "佚名",
            score=f"{book.score:g}",
            raters=book.raters,
            words=f"{book.words / 10000:.0f}" if book.words else "不祥",
            status=book.status or "状态未知",
            tags="、".join(book.tags[:3]) or "没贴标签",
        )

    @staticmethod
    def _local_reply(text: str, user: str, books: list[Book]) -> str:
        if "推荐" in text or "好看" in text or "高分" in text:
            if books:
                b = books[0]
                return f"要我说，先看《{b.title}》，{b.score:g}分，{b.raters}人评过。不合口味你回来找我换。"
            return "书库的目录还没建好，等我整理完这排书架。"
        names = "、".join(f"《{b.title}》" for b in books[:2])
        if names:
            return f"{user}，你这一说我想起{names}。想听哪本，我翻给你看。"
        return f"{user}，这个我得查查，别急。"

    @staticmethod
    def _local_chat(text: str, user: str, mode: str) -> str:
        snippet = (text or "").strip()
        if len(snippet) > 18:
            snippet = snippet[:18] + "…"
        if mode == "game":
            return f"{user}，这句我听见了。游戏画面的细节我看不见，你再说具体一点，我跟着聊。"
        if snippet:
            return f"{user}，我在。你说的是「{snippet}」。还想接着聊哪句？"
        return f"{user}，我在，你说。"
