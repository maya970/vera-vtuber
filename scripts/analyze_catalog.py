"""统计书库分布，用来确定高分目录的筛选阈值与历史文占比。"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "yousuu.sqlite"

HISTORY_PAT = re.compile(
    r"历史|王朝争霸|两宋元明|秦汉三国|上古先秦|两晋隋唐|五代十国|元明清|清史民国|近代国史|"
    r"架空历史|宫廷侯爵|布衣生活|市井生活|穿越|种田|基建|权谋|争霸"
)


def split_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").replace("，", ",").split(",") if t.strip()]


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    print("=== 总量 ===")
    total = cur.execute("select count(*) from books").fetchone()[0]
    print("books:", total)

    for raters in (20, 50, 100, 200, 300):
        for score in (6.0, 6.5, 7.0, 7.5, 8.0):
            n = cur.execute(
                "select count(*) from books where score>=? and scorerCount>=? and score>0",
                (score, raters),
            ).fetchone()[0]
            print(f"  score>={score} raters>={raters}: {n}")

    rows = cur.execute(
        "select bookId,title,author,score,scorerCount,tags,countWord,status from books "
        "where score>=6.5 and scorerCount>=20 and score>0"
    ).fetchall()
    print(f"\n=== 候选 {len(rows)} 本，标签分布 ===")
    tag_counter = Counter()
    history = 0
    for r in rows:
        tags = split_tags(r[5])
        tag_counter.update(tags)
        if any(HISTORY_PAT.search(t) for t in tags):
            history += 1
    for tag, n in tag_counter.most_common(30):
        print(f"  {tag}: {n}")
    print(f"历史相关：{history} / {len(rows)} = {history / max(1, len(rows)):.0%}")

    print("\n=== 历史文高分样例（>=7.5, >=100人）===")
    for r in cur.execute(
        "select title,author,score,scorerCount,tags from books "
        "where score>=7.5 and scorerCount>=100 and score>0 order by score desc limit 15"
    ):
        if any(HISTORY_PAT.search(t) for t in split_tags(r[4])):
            print(f"  {r[2]:g} ({r[3]}人) 《{r[0]}》 {r[1]} [{r[4]}]")

    print("\n=== 无标签的高分书（可能是名著）===")
    for r in cur.execute(
        "select title,author,score,scorerCount from books "
        "where score>=8.0 and scorerCount>=100 and (tags is null or tags='') order by score desc limit 20"
    ):
        print(f"  {r[2]:g} ({r[3]}人) 《{r[0]}》 {r[1]}")


if __name__ == "__main__":
    main()
