"""短评后处理：剔除跑题/负面向的句子，并把更像好评的排前面。

处理规则：
  * 含「改编 / 电视剧 / 剧版 / 影视化 / 选角」的，属于讨论影视剧，不是书评 → 丢弃
  * 含擦边词（绿帽/绿障 等）→ 丢弃
  * 其余按「正面词数量 → 长度」排序，让卡片和提示词优先拿到好评

用法：python scripts/polish_quotes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
PROFANITY = ROOT / "config" / "profanity.txt"

OFFTOPIC = ("改编", "电视剧", "剧版", "影视化", "选角", "电影版", "动漫版", "动画版")
EXTRA_BAD = ("绿帽", "绿障", "戴绿", "被绿")
POSITIVE = (
    "喜欢", "好看", "推荐", "文笔", "人物", "剧情", "设定", "节奏", "精彩", "值得",
    "读完", "看完", "追", "仙草", "神作", "粮草", "经典", "扎实", "细腻", "流畅",
)


def load_words(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def score(q: str) -> tuple[int, int]:
    hits = sum(1 for w in POSITIVE if w in q)
    return (-hits, len(q))


def main() -> int:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    profanity = load_words(PROFANITY)
    dropped_off = dropped_bad = 0
    for book in data["books"]:
        kept = []
        for q in book.get("quotes", []):
            if any(w in q for w in OFFTOPIC):
                dropped_off += 1
                continue
            if any(w in q for w in EXTRA_BAD) or any(w in q for w in profanity):
                dropped_bad += 1
                continue
            if not (8 <= len(q) <= 60):
                dropped_bad += 1
                continue
            kept.append(q)
        kept.sort(key=score)
        book["quotes"] = kept[:3]
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(len(b["quotes"]) for b in data["books"])
    with_q = sum(1 for b in data["books"] if b["quotes"])
    print(f"[+] 丢弃：影视剧话题 {dropped_off} 条，擦边/超长 {dropped_bad} 条")
    print(f"[+] 现有短评 {total} 条，覆盖 {with_q} 本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
