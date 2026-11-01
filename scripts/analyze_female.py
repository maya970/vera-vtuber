"""统计目录里的女频特征标签，确定排除规则。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"

PATTERNS = {
    "晋江-言情": r"原创-言情|衍生-言情",
    "晋江-纯爱": r"原创-纯爱|衍生-纯爱",
    "晋江-百合": r"原创-百合|衍生-百合",
    "CP言情": r"^言情$",
    "CP纯爱": r"^纯爱$",
    "CP百合": r"^百合$",
    "女尊": r"^女尊$",
    "女强": r"^女强$",
    "宫斗": r"^宫斗$",
    "宅斗": r"^宅斗$",
    "甜文": r"^甜文$",
    "宠文": r"宠文",
    "总裁": r"总裁",
    "豪门": r"豪门",
    "替身": r"替身",
    "白月光": r"白月光",
    "追妻": r"追妻",
    "先婚后爱": r"先婚后爱",
    "破镜重圆": r"^破镜重圆$",
    "虐恋情深": r"^虐恋情深$",
    "情有独钟": r"^情有独钟$",
    "青梅竹马": r"^青梅竹马$",
    "女扮男装": r"女扮男装",
    "娇妻": r"娇妻",
    "宠妻": r"宠妻",
}


def main() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    books = data["books"]
    print(f"目录 {len(books)} 本")

    hits: dict[str, set[int]] = {k: set() for k in PATTERNS}
    any_hit: set[int] = set()
    for i, b in enumerate(books):
        tags = b.get("tags") or []
        for name, pat in PATTERNS.items():
            if any(re.search(pat, t) for t in tags):
                hits[name].add(i)
                any_hit.add(i)
    for name in PATTERNS:
        print(f"  {name}: {len(hits[name])}")
        samples = list(hits[name])[:3]
        for idx in samples:
            b = books[idx]
            print(f"      《{b['title']}》 {b['author']} [{','.join(b['tags'][:4])}]")
    print(f"\n任一命中：{len(any_hit)} / {len(books)} = {len(any_hit) / len(books):.0%}")

    # 只命中“言情/纯爱/百合/女尊/晋江前缀”的严格集合
    strict = set()
    for name in ("晋江-言情", "晋江-纯爱", "晋江-百合", "CP言情", "CP纯爱", "CP百合", "女尊"):
        strict |= hits[name]
    print(f"严格女频集合：{len(strict)} 本")


if __name__ == "__main__":
    main()
