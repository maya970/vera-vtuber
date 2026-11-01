"""作品目录：加载、检索（中英混排 BM25）、挑选下一本。"""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import ROOT

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII = re.compile(r"[a-zA-Z0-9]+")


def _load_profanity() -> list[str]:
    path = ROOT / "config" / "profanity.txt"
    if not path.exists():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


_PROFANITY = _load_profanity()


def _safe_quotes(quotes: list[str]) -> list[str]:
    """运行期兜底：任何带脏话的短评都不进入提示词。"""
    if not _PROFANITY:
        return quotes
    return [q for q in quotes if not any(w in q for w in _PROFANITY)]


def tokenize(text: str) -> list[str]:
    """中文按二元组 + 单字，英文按单词。足够做书名/标签检索。"""
    text = (text or "").lower()
    tokens: list[str] = []
    for run in _ASCII.findall(text):
        tokens.append(run)
    cjk_chars = _CJK.findall(text)
    for i, ch in enumerate(cjk_chars):
        tokens.append(ch)
        if i + 1 < len(cjk_chars):
            tokens.append(ch + cjk_chars[i + 1])
    return tokens


@dataclass
class Book:
    id: int
    title: str
    author: str = ""
    score: float = 0.0
    rank_score: float = 0.0
    raters: int = 0
    words: int = 0
    status: str = ""
    tags: list[str] = field(default_factory=list)
    url: str = ""
    cover: str = ""
    updated: str = ""
    category: str = "other"
    quotes: list[str] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        return cls(
            id=int(d.get("id") or 0),
            title=(d.get("title") or "").strip(),
            author=(d.get("author") or "").strip(),
            score=float(d.get("score") or 0),
            rank_score=float(d.get("rank_score") or 0),
            raters=int(d.get("raters") or 0),
            words=int(d.get("words") or 0),
            status=(d.get("status") or "").strip(),
            tags=[t for t in (d.get("tags") or []) if t],
            url=d.get("url") or "",
            cover=d.get("cover") or "",
            updated=d.get("updated") or "",
            category=d.get("category") or "other",
            quotes=_safe_quotes([q for q in (d.get("quotes") or []) if q]),
            series=d.get("series") or [],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "score": self.score,
            "rank_score": self.rank_score,
            "raters": self.raters,
            "words": self.words,
            "status": self.status,
            "tags": self.tags,
            "url": self.url,
            "cover": self.cover,
            "updated": self.updated,
            "category": self.category,
            "quotes": self.quotes,
            "series": self.series,
        }

    def brief(self) -> str:
        tags = "、".join(self.tags[:4]) or "未标注"
        bits = [f"《{self.title}》", f"作者{self.author or '佚名'}", f"评分{self.score:g}", f"{self.raters}人评"]
        if self.words:
            bits.append(f"{self.words / 10000:.0f}万字")
        if self.status:
            bits.append(self.status)
        bits.append(f"标签：{tags}")
        return "，".join(bits)

    def prompt_block(self) -> str:
        line = (
            f"- 《{self.title}》｜作者：{self.author or '佚名'}｜评分：{self.score:g}（{self.raters}人评）"
            f"｜{self.words / 10000:.0f}万字｜{self.status or '状态未知'}｜标签：{'、'.join(self.tags) or '无'}"
        )
        if self.updated:
            line += f"｜最近更新：{self.updated}"
        if self.quotes:
            line += "\n  读者短评（口播的主要素材，可转述或引用一句，但不要展开情节）：" + " / ".join(self.quotes[:3])
        return line


class Catalog:
    def __init__(self, path: Path | None = None):
        self.path = path or (ROOT / "data" / "catalog.json")
        self.books: list[Book] = []
        self.updated_at = ""
        self.source = ""
        self._index: dict[str, list[tuple[int, int]]] | None = None
        self._doc_len: list[int] = []
        self._avg_len = 0.0
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.books = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = {"books": raw}
        self.updated_at = raw.get("updated_at", "")
        self.source = raw.get("source", "")
        self.books = [Book.from_dict(b) for b in raw.get("books", []) if b.get("title")]
        self._index = None

    def save(self, books: Iterable[Book] | None = None) -> None:
        if books is not None:
            self.books = list(books)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self.updated_at,
            "source": self.source,
            "count": len(self.books),
            "books": [b.to_dict() for b in self.books],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        self._index = None

    # ---------- 检索 ----------
    def _build(self) -> None:
        index: dict[str, list[tuple[int, int]]] = {}
        doc_len: list[int] = []
        for i, book in enumerate(self.books):
            text = " ".join([book.title, book.title, book.author, " ".join(book.tags)])
            toks = tokenize(text)
            doc_len.append(len(toks) or 1)
            for tok, tf in Counter(toks).items():
                index.setdefault(tok, []).append((i, tf))
        self._index = index
        self._doc_len = doc_len
        self._avg_len = sum(doc_len) / len(doc_len) if doc_len else 0.0

    def search(self, query: str, k: int = 6) -> list[Book]:
        if not self.books:
            return []
        if self._index is None:
            self._build()
        assert self._index is not None
        q = [t for t in tokenize(query) if len(t) > 0]
        if not q:
            return self.top_rated(k)
        n = len(self.books)
        avg = self._avg_len or 1.0
        scores: dict[int, float] = {}
        k1, b = 1.4, 0.75
        for tok in set(q):
            postings = self._index.get(tok)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for doc, tf in postings:
                dl = self._doc_len[doc]
                denom = tf + k1 * (1 - b + b * dl / avg)
                scores[doc] = scores.get(doc, 0.0) + idf * tf * (k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[: k * 3]
        # 同分时偏向高评分、多评分人数
        ranked.sort(key=lambda item: (item[1], self.books[item[0]].score), reverse=True)
        return [self.books[i] for i, _ in ranked[:k]]

    def top_rated(self, k: int = 6, min_raters: int = 100) -> list[Book]:
        pool = [b for b in self.books if b.raters >= min_raters and b.score > 0]
        pool.sort(key=lambda b: (b.rank_score or b.score, math.log10(max(b.raters, 1))), reverse=True)
        return pool[:k]

    def get(self, book_id: int) -> Book | None:
        for b in self.books:
            if b.id == book_id:
                return b
        return None

    def pick_next(
        self,
        played_ids: set[int],
        min_score: float = 7.5,
        min_raters: int = 200,
        history_ratio: float = 0.75,
    ) -> Book | None:
        pool = [
            b
            for b in self.books
            if b.id not in played_ids and b.score >= min_score and b.raters >= min_raters
        ]
        if not pool:
            pool = [b for b in self.top_rated(60) if b.id not in played_ids]
        if not pool:
            return None

        # 主推历史文：按比例优先从历史类里挑，池子空了再退回其他
        history = [b for b in pool if b.category == "history"]
        others = [b for b in pool if b.category != "history"]
        if history and (not others or random.random() < history_ratio):
            pool = history
        elif others:
            pool = others

        # 前 15 本里加权随机，避免每次直播顺序完全一样
        pool.sort(key=lambda b: (b.rank_score or b.score, b.raters), reverse=True)
        head = pool[: min(15, len(pool))]
        weights = [max(1.0, 12 - i) for i in range(len(head))]
        return random.choices(head, weights=weights, k=1)[0]

    def stats(self) -> dict:
        scores = [b.score for b in self.books if b.score > 0]
        return {
            "count": len(self.books),
            "updated_at": self.updated_at,
            "source": self.source,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "with_quotes": sum(1 for b in self.books if b.quotes),
        }
