"""DeepSeek 大脑：人设、检索增强、弹幕回复与讲书稿。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import AsyncIterator

from openai import AsyncOpenAI

from .catalog import Book
from .config import ROOT, config, stream_mode

log = logging.getLogger("vera.brain")

PERSONA_PATH = ROOT / "config" / "persona.md"
PERSONA_BOOK_PATH = ROOT / "config" / "persona-book.md"

_SPEAK_BASE = """
【直播说话规则】
1. 你说的话会被直接合成语音念出来。只输出要念的内容：不要括号旁白、不要表情符号、不要 Markdown、不要编号、不要角色名前缀。
2. 回复弹幕时 1 到 3 句，总共不超过 90 个字。
3. 先接住对方说的具体内容，再回答。不要每句都用同样的开场白。
4. 不评价真实人物，不接危险、违法、色情内容，遇到就轻轻带过。
5. 不确定就说不知道，不要编造你没看到的事实。
""".strip()

_MODE_EXTRA = {
    "chat": "你现在是闲聊直播。陪观众说话、接梗、回答问题。不要主动推销书籍，除非对方先问到书。",
    "game": (
        "你现在在陪着玩游戏的直播。观众能看到游戏画面，你看不到具体操作，"
        "所以不要编造击杀、装备、比分或按键。根据弹幕聊这局游戏：加油、吐槽、解释、接梗。"
        "不要讲书，不要报书名。"
    ),
    "book": (
        "你现在在讲书。只使用下面提供的书目事实。没有的信息就说不知道，绝不编造情节、角色名、结局。"
        "不剧透。推荐时给气质、钩子、适合谁看。和书无关的敏感话题，用一句话拉回书。"
        "介绍一本书时最多 5 句。评分用口语说，例如 8.2 说成「八点二」；书名用书名号。"
    ),
}

_CLEAN_PATTERNS = [
    (re.compile(r"[*_`#>]+"), ""),
    (re.compile(r"^\s*(薇拉|Vera)\s*[:：]\s*", re.I), ""),
    (re.compile(r"[（(][^）)]{0,30}[）)]"), ""),
    (re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]"), ""),
]


def sanitize(text: str) -> str:
    out = text or ""
    for pattern, repl in _CLEAN_PATTERNS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    out = out.strip("“”\"' ")
    return out


_OPEN_BRACKETS = "「『（《“‘"
_CLOSE_BRACKETS = "」』）》”’"


def split_sentences(text: str, max_len: int = 70, limit: int = 6) -> list[str]:
    """把回复切成适合逐句合成的片段；引号/书名号里的句号不切。"""
    text = (text or "").strip()
    if not text:
        return []

    rough: list[str] = []
    buf = ""
    depth = 0
    for ch in text:
        buf += ch
        if ch in _OPEN_BRACKETS:
            depth += 1
        elif ch in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
        elif ch in "。！？!?；;" and depth == 0:
            rough.append(buf)
            buf = ""
    if buf.strip():
        rough.append(buf)

    chunks: list[str] = []
    for part in rough:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_len:
            cut = max(part.rfind("，", 0, max_len), part.rfind("、", 0, max_len), part.rfind(" ", 0, max_len))
            if cut < max_len * 0.4:
                cut = max_len
            chunks.append(part[:cut].strip())
            part = part[cut:].strip("，、 ")
        if part:
            chunks.append(part)
    return [c for c in chunks if c][:limit]


class Brain:
    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._client_key = ""
        self.persona = self._read_persona()

    @staticmethod
    def _read_persona() -> str:
        path = PERSONA_BOOK_PATH if stream_mode() == "book" and PERSONA_BOOK_PATH.exists() else PERSONA_PATH
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return "你是直播间里的虚拟主播，正在和观众说话。"

    def reload_persona(self) -> None:
        self.persona = self._read_persona()

    @property
    def ready(self) -> bool:
        return bool(config.get("deepseek.api_key"))

    def _get_client(self) -> AsyncOpenAI:
        key = config.get("deepseek.api_key") or ""
        if not key:
            raise RuntimeError("未配置 DeepSeek API Key")
        if self._client is None or self._client_key != key:
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=config.get("deepseek.base_url"),
                timeout=config.get("deepseek.timeout", 60),
                max_retries=1,
            )
            self._client_key = key
        return self._client

    # ---------- prompt ----------
    def _system_prompt(
        self,
        books: list[Book] | None = None,
        current: Book | None = None,
        history: list[dict] | None = None,
    ) -> str:
        parts = [self.persona, "", _SPEAK_BASE, _MODE_EXTRA.get(stream_mode(), _MODE_EXTRA["chat"]), ""]
        if stream_mode() == "book" and current:
            parts.append("【你此刻正在介绍的书】")
            parts.append(current.prompt_block())
            parts.append("")
        if stream_mode() == "book" and books:
            parts.append("【书库检索结果（只可使用这些事实）】")
            for b in books:
                parts.append(b.prompt_block())
            parts.append("")
        if history:
            parts.append("【最近对话】")
            for item in history[-8:]:
                speaker = item.get("user") or "观众"
                parts.append(f"{speaker}：{item.get('text', '')}")
            parts.append("")
        parts.append("【场景】你正在 B 站直播间，面对观众和滚动的弹幕。")
        return "\n".join(parts)

    @staticmethod
    def _history_text(history: list[dict] | None) -> list[dict]:
        return history or []

    async def stream_reply(
        self,
        text: str,
        user: str,
        books: list[Book] | None = None,
        current: Book | None = None,
        history: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        log.info("DeepSeek 调用（弹幕回复）：%s …", text[:24])
        messages = [
            {"role": "system", "content": self._system_prompt(books, current, history)},
            {"role": "user", "content": f"观众「{user}」发来弹幕：{text}"},
        ]
        stream = await client.chat.completions.create(
            model=config.get("deepseek.model", "deepseek-chat"),
            messages=messages,
            temperature=config.get("deepseek.temperature", 1.0),
            max_tokens=config.get("deepseek.max_tokens", 320),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def stream_book_intro(self, book: Book, style_hint: str = "") -> AsyncIterator[str]:
        client = self._get_client()
        log.info("DeepSeek 调用（讲书稿）：《%s》", book.title)
        hint = f"\n额外要求：{style_hint}" if style_hint else ""
        messages = [
            {
                "role": "system",
                "content": self.persona + "\n\n" + _SPEAK_BASE + "\n" + _MODE_EXTRA["book"],
            },
            {
                "role": "user",
                "content": (
                    "请为这本书写一段直播口播，4 到 5 句，总共不超过 160 字。\n"
                    "把【读者短评】当作主要素材：先用你自己的话概括读者为什么喜欢它"
                    "（可以引用其中最短的一句，加引号），再说它适合什么样的观众，最后一句邀请互动。\n"
                    "不要照读短评，不要复述评分以外的元数据，绝对不要编造剧情、角色或结局。\n\n"
                    f"{book.prompt_block()}{hint}"
                ),
            },
        ]
        stream = await client.chat.completions.create(
            model=config.get("deepseek.model", "deepseek-chat"),
            messages=messages,
            temperature=1.15,
            max_tokens=420,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def reply_once(
        self,
        text: str,
        user: str,
        books: list[Book] | None = None,
        current: Book | None = None,
    ) -> str:
        buf: list[str] = []
        async for piece in self.stream_reply(text, user, books, current):
            buf.append(piece)
        return sanitize("".join(buf))

    async def book_intro_once(self, book: Book) -> str:
        buf: list[str] = []
        async for piece in self.stream_book_intro(book):
            buf.append(piece)
        return sanitize("".join(buf))

    async def idle_once(self, mode: str) -> str:
        client = self._get_client()
        ask = {
            "game": "直播间安静了一会儿。用一两句口语说你还在看这局游戏，邀请弹幕聊聊刚才那波。不要编造具体操作，不要讲书。",
            "chat": "直播间安静了一会儿。用一两句口语找个轻松话题，或者邀请大家说话。不要讲书。",
        }.get(mode, "用一两句口语和观众打个招呼。")
        log.info("DeepSeek 调用（空闲）：%s", mode)
        resp = await client.chat.completions.create(
            model=config.get("deepseek.model", "deepseek-chat"),
            messages=[
                {"role": "system", "content": self.persona + "\n\n" + _SPEAK_BASE + "\n" + _MODE_EXTRA.get(mode, "")},
                {"role": "user", "content": ask},
            ],
            temperature=1.0,
            max_tokens=160,
        )
        return sanitize(resp.choices[0].message.content or "")

    async def check(self) -> tuple[bool, str]:
        if not self.ready:
            return False, "未配置 API Key"
        try:
            client = self._get_client()
            resp = await client.chat.completions.create(
                model=config.get("deepseek.model", "deepseek-chat"),
                messages=[{"role": "user", "content": "回复两个字：在线"}],
                max_tokens=8,
                temperature=0,
            )
            return True, resp.choices[0].message.content or "在线"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:200]


brain = Brain()
