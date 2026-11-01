"""从 yoususu.sqlite 生成高分作品目录 data/catalog.json。

默认策略（对齐直播定位）：
  * 只收评分人数 >= 20 的书（用户要求下限）
  * 按贝叶斯加权分取前 20000 本
  * 剔除名著 / 出版物 / 教材（config/classics.txt + 书名/作者启发式）
  * 每本带最多 3 条真实读者短评，作为口播话术的主要素材
  * 标记 category = history / other，直播时优先讲历史文

用法：
    python scripts/build_catalog_from_db.py
    python scripts/build_catalog_from_db.py --limit 8000 --min-score 6.5
    python scripts/build_catalog_from_db.py --no-exclude
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "yousuu.sqlite"
OUT = ROOT / "data" / "catalog.json"
CLASSICS = ROOT / "config" / "classics.txt"
FEMALE_TAGS = ROOT / "config" / "female_tags.txt"

SPOILER_WORDS = ("结局", "剧透", "最后死", "死光", "烂尾", "太监", "死了")
NON_BOOK = re.compile(r"全集|大全集|选集|文集|诗集|辞典|百科全书|概论|教材|课本|大纲|真题")

HISTORY_PAT = re.compile(
    r"历史|王朝争霸|两宋元明|秦汉三国|上古先秦|两晋隋唐|五代十国|元明清|清史民国|近代国史|"
    r"架空历史|宫廷侯爵|布衣生活|市井生活|种田文|基建|权谋|争霸"
)


def clean_tags(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("，", ",").split(",")]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen and len(p) <= 16:
            seen.add(p)
            out.append(p)
    return out[:8]


def clean_quote(text: str) -> str:
    text = " ".join((text or "").split())
    text = text.strip("“”\"' ")
    return text[:90]


def load_blacklist(path: Path) -> list[str]:
    if not path.exists():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def is_classic(title: str, author: str, blacklist: list[str]) -> bool:
    if author.endswith("_出版") or "_出版" in author:
        return True
    if NON_BOOK.search(title):
        return True
    for word in blacklist:
        if word in title or word in author:
            return True
    return False


def is_female(tags: list[str], keywords: list[str]) -> bool:
    for tag in tags:
        for word in keywords:
            if word in tag:
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="从本地数据库生成高分目录")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--min-raters", type=int, default=20)
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--quotes", type=int, default=3, help="每本书最多带几条读者短评")
    parser.add_argument("--exclude-file", default=str(CLASSICS))
    parser.add_argument("--female-tags-file", default=str(FEMALE_TAGS))
    parser.add_argument("--keep-female", action="store_true", help="保留女频（默认排除）")
    parser.add_argument("--no-exclude", action="store_true")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[x] 找不到数据库：{db_path}")
        return 2

    blacklist = [] if args.no_exclude else load_blacklist(Path(args.exclude_file))
    female_keywords = [] if args.keep_female else load_blacklist(Path(args.female_tags_file))
    print(f"[*] 名著排除词 {len(blacklist)} 条，女频排除词 {len(female_keywords)} 条")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        "select bookId, title, author, score, scorerCount, countWord, status, tags, cover, updateAt "
        "from books where score is not null and score > 0 and scorerCount >= ? and score >= ?",
        (args.min_raters, args.min_score),
    ).fetchall()
    print(f"[*] 评分>0 / {args.min_raters}人评：{len(rows)} 本")

    prior_mean, prior_n = 6.8, 250.0
    scored = []
    excluded = 0
    female_excluded = 0
    for r in rows:
        title = (r["title"] or "").strip()
        author = (r["author"] or "").strip()
        if is_classic(title, author, blacklist):
            excluded += 1
            continue
        tags = clean_tags(r["tags"])
        if female_keywords and is_female(tags, female_keywords):
            female_excluded += 1
            continue
        weighted = (r["score"] * r["scorerCount"] + prior_mean * prior_n) / (r["scorerCount"] + prior_n)
        scored.append((weighted, r))
    print(f"[*] 剔除名著/出版物 {excluded} 本、女频 {female_excluded} 本，剩余 {len(scored)} 本")

    scored.sort(key=lambda x: (-x[0], -x[1]["scorerCount"]))
    selected = scored[: args.limit]
    ids = [r["bookId"] for _, r in selected]
    print(f"[*] 取前 {len(selected)} 本，加权分 {selected[-1][0]:.2f} ~ {selected[0][0]:.2f}")

    # ---------- 读者短评（单次扫描，按点赞排序，每本最多 N 条） ----------
    quotes: dict[int, list[str]] = {}
    if args.quotes and ids:
        want = set(ids)
        t0 = time.time()
        qrows = cur.execute(
            "select bookId, content, praiseCount, score from comments "
            "where inReview = 0 and score >= 4 and praiseCount >= 2 "
            "and length(content) between 10 and 90 "
            "order by praiseCount desc limit 500000"
        )
        for q in qrows:
            bid = q["bookId"]
            if bid not in want:
                continue
            bucket = quotes.setdefault(bid, [])
            if len(bucket) >= args.quotes:
                continue
            text = clean_quote(q["content"])
            if not text or any(w in text for w in SPOILER_WORDS) or text in bucket:
                continue
            bucket.append(text)
        print(f"[*] 短评覆盖 {len(quotes)} 本（用时 {time.time() - t0:.1f}s）")

    books = []
    history_n = 0
    for weighted, r in selected:
        tags = clean_tags(r["tags"])
        category = "history" if any(HISTORY_PAT.search(t) for t in tags) else "other"
        history_n += category == "history"
        books.append(
            {
                "id": r["bookId"],
                "title": r["title"],
                "author": (r["author"] or "").strip(),
                "score": round(float(r["score"]), 2),
                "rank_score": round(weighted, 3),
                "raters": int(r["scorerCount"] or 0),
                "words": int(r["countWord"] or 0),
                "status": {0: "连载中", 1: "已完结", 2: "连载中"}.get(r["status"], "状态未知"),
                "tags": tags,
                "category": category,
                "cover": r["cover"] or "",
                "updated": (r["updateAt"] or "")[:10],
                "url": (
                    f"{os.environ.get('BOOKBOX_BASE', '').rstrip('/')}/book.php?bookId={r['bookId']}"
                    if os.environ.get("BOOKBOX_BASE")
                    else ""
                ),
                "quotes": quotes.get(r["bookId"], []),
            }
        )

    payload = {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": (
            f"{db_path.name} (limit={len(books)}, min_raters={args.min_raters}, "
            f"excluded={excluded}, history={history_n})"
        ),
        "count": len(books),
        "books": books,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"[+] 已写入 {out_path}（{len(books)} 本，{size_mb:.1f} MB）")
    print(f"    历史文 {history_n} 本（{history_n / max(1, len(books)):.0%}）")
    print(f"    带短评 {len(quotes)} 本")

    print("\n[+] 历史文高分前 8：")
    shown = 0
    for b in books:
        if b["category"] != "history":
            continue
        print(f"    {b['score']:.1f}（{b['raters']}人）《{b['title']}》 {b['author']} {'/'.join(b['tags'][:3])}")
        shown += 1
        if shown >= 8:
            break
    if not shown:
        print("    （请放宽 --min-score 或检查标签）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
