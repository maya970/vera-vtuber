"""统计目录里“不文明”短评的规模，决定清洗策略。"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"

PROFANITY = [
    line.strip()
    for line in (ROOT / "config" / "profanity.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
]

POSITIVE = ["喜欢", "好看", "推荐", "文笔", "人物", "剧情", "设定", "节奏", "细腻", "精彩", "感动", "经典", "仙草", "神作"]


def main() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    books = data["books"]
    eligible = [b for b in books if b["score"] >= 6.5]
    with_q = [b for b in books if b["quotes"]]
    elig_q = [b for b in eligible if b["quotes"]]

    print(f"全部 {len(books)} 本，有短评 {len(with_q)} 本，短评 {sum(len(b['quotes']) for b in with_q)} 条")
    print(f"可推荐(>=6.5) {len(eligible)} 本，其中有短评 {len(elig_q)} 本，短评 {sum(len(b['quotes']) for b in elig_q)} 条")

    hit_books = []
    hit_quotes = []
    for b in with_q:
        bad = [q for q in b["quotes"] if any(w in q for w in PROFANITY)]
        if bad:
            hit_books.append(b)
            hit_quotes.extend(bad)
    elig_hits = [q for b in elig_q for q in b["quotes"] if any(w in q for w in PROFANITY)]
    print(f"命中脏话词的书 {len(hit_books)} 本，短评 {len(hit_quotes)} 条")
    print(f"其中可推荐(>=6.5) 书里的命中：{len(elig_hits)} 条")

    no_pos = []
    for b in elig_q:
        for q in b["quotes"]:
            if not any(w in q for w in POSITIVE):
                no_pos.append(q)
    print(f"可推荐书里“没有正面词”的短评：{len(no_pos)} / {sum(len(b['quotes']) for b in elig_q)}")

    print("\n=== 命中样例 30 条 ===")
    random.seed(1)
    for q in random.sample(hit_quotes, min(30, len(hit_quotes))):
        print("  -", q)

    print("\n=== 可推荐书短评样例 15 条 ===")
    sample = [q for b in elig_q for q in b["quotes"]]
    for q in random.sample(sample, min(15, len(sample))):
        print("  -", q)


if __name__ == "__main__":
    main()
