"""从你自己的书目网站抓高分作品，生成 data/catalog.json。

先在环境变量 BOOKBOX_BASE 里填写网站地址，再运行：
    python scripts/fetch_bookbox.py --pages 20 --enrich 30

不填 BOOKBOX_BASE 时，这个脚本不会访问任何网站。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("BOOKBOX_BASE", "").strip()
if BASE and not BASE.endswith("/"):
    BASE += "/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
JINA = "https://r.jina.ai/"


class Fetcher:
    def __init__(self, mode: str = "auto", timeout: int = 20):
        self.mode = mode
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Referer": BASE})
        self.using_jina = False

    def get(self, url: str) -> str | None:
        if self.mode in ("auto", "direct") and not self.using_jina:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200 and len(resp.text) > 500:
                    return resp.text
                print(f"    ! 直连 {resp.status_code}，改用阅读器")
            except Exception as exc:  # noqa: BLE001
                if self.mode == "direct":
                    print(f"    ! 直连失败：{exc}")
                    return None
                print(f"    ! 直连失败（{type(exc).__name__}），改用阅读器")
            self.using_jina = True
        try:
            resp = self.session.get(
                JINA + url,
                headers={"x-return-format": "html"},
                timeout=max(self.timeout, 60),
            )
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
            print(f"    ! 阅读器 {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            print(f"    ! 阅读器失败：{exc}")
        return None


def text_of(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_list(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "lxml")
    books: list[dict] = []
    for item in soup.select("div.book-item"):
        link = item.select_one(".book-title a")
        if not link:
            continue
        href = link.get("href", "")
        match = re.search(r"bookId=(\d+)", href)
        if not match:
            continue
        title = text_of(link)
        author = text_of(item.select_one(".author"))
        score_text = text_of(item.select_one(".score"))
        try:
            score = float(score_text)
        except ValueError:
            score = 0.0
        raters = 0
        rm = re.search(r"\((\d+)人评\)", item.get_text(" ", strip=True))
        if rm:
            raters = int(rm.group(1))
        series = []
        for s in item.select(".works a"):
            sm = re.search(r"bookId=(\d+)", s.get("href", ""))
            if sm:
                series.append({"id": int(sm.group(1)), "title": text_of(s)})
        books.append(
            {
                "id": int(match.group(1)),
                "title": title,
                "author": author,
                "score": score,
                "raters": raters,
                "url": urljoin(BASE, href),
                "series": series,
            }
        )
    total_pages = 1
    m = re.search(r"共\s*([\d,]+)\s*本[^0-9]*第\s*(\d+)\s*页", soup.get_text(" ", strip=True))
    page_input = soup.select_one('input[name="page"]')
    if page_input and page_input.get("max"):
        total_pages = int(page_input["max"])
    return books, total_pages


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    meta = soup.select_one(".book-meta")
    body = meta.get_text("\n", strip=True) if meta else ""
    m = re.search(r"评分：\s*([\d.]+)\s*（(\d+)人评）", body)
    if m:
        out["score"] = float(m.group(1))
        out["raters"] = int(m.group(2))
    m = re.search(r"字数：\s*([\d,]+)", body)
    if m:
        out["words"] = int(m.group(1).replace(",", ""))
    m = re.search(r"状态：\s*(\S+)", body)
    if m:
        out["status"] = m.group(1)
    m = re.search(r"作者：\s*(\S+)", body)
    if m:
        out["author"] = m.group(1)
    tags_box = soup.select_one(".book-detail .tags")
    if tags_box:
        tags = [t.get_text(strip=True).strip("，,") for t in tags_box.select("span")]
        out["tags"] = [t for t in tags if t]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取书库高分作品")
    parser.add_argument("--pages", type=int, default=20, help="翻多少页列表（每页约 30 本）")
    parser.add_argument("--min-raters", type=int, default=200, help="最低评分人数")
    parser.add_argument("--min-score", type=float, default=0.0, help="最低评分")
    parser.add_argument("--enrich", type=int, default=30, help="补详情页的前 N 本")
    parser.add_argument("--out", default="data/catalog.json")
    parser.add_argument("--delay", type=float, default=0.0, help="请求间隔（秒），自动模式会给默认值")
    parser.add_argument("--mode", choices=["auto", "direct", "jina"], default="auto")
    parser.add_argument("--merge", action="store_true", help="与已有目录合并")
    args = parser.parse_args()
    if not BASE:
        print("[x] 没有设置 BOOKBOX_BASE。这是你自己的书目网站地址，不设置就不会访问任何网站。")
        return 2

    out_path = ROOT / args.out
    existing: list[dict] = []
    if args.merge and out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8")).get("books", [])
            print(f"[*] 合并已有目录 {len(existing)} 本")
        except Exception:  # noqa: BLE001
            pass

    fetcher = Fetcher(args.mode)
    collected: dict[int, dict] = {b["id"]: b for b in existing}
    total_pages = 1
    started = time.time()

    for page in range(1, args.pages + 1):
        url = f"{BASE}bookbox.php?page={page}&minScorerCount={args.min_raters}"
        print(f"[*] 列表第 {page}/{args.pages} 页 …", end=" ", flush=True)
        html = fetcher.get(url)
        if not html:
            print("失败，跳过")
            time.sleep(2)
            continue
        books, total_pages = parse_list(html)
        new = 0
        for book in books:
            if book["id"] not in collected:
                new += 1
            collected[book["id"]] = book
        print(f"{len(books)} 本（新增 {new}），共 {total_pages} 页")
        delay = args.delay or (3.2 if fetcher.using_jina else 0.8)
        time.sleep(delay)

    books = [b for b in collected.values() if b["score"] > 0]
    books = [b for b in books if b["score"] >= args.min_score]
    books.sort(key=lambda b: (b["score"], b["raters"]), reverse=True)
    print(f"[*] 收到 {len(books)} 本有效评分作品，最高 {books[0]['score'] if books else 0:g}")

    if args.enrich:
        targets = books[: args.enrich]
        print(f"[*] 补详情：{len(targets)} 本")
        for i, book in enumerate(targets, 1):
            html = fetcher.get(book["url"])
            if html:
                detail = parse_detail(html)
                book.update({k: v for k, v in detail.items() if v})
                print(f"    {i:02d}/{len(targets)} 《{book['title']}》 {book.get('score')} "
                      f"{book.get('words', 0) / 10000:.0f}万字 {book.get('status', '')} {','.join(book.get('tags', []))}")
            else:
                print(f"    {i:02d}/{len(targets)} 《{book['title']}》 详情失败")
            time.sleep(args.delay or (3.2 if fetcher.using_jina else 0.8))

    payload = {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": f"{BASE}bookbox.php (pages={args.pages}, min_raters={args.min_raters})",
        "count": len(books),
        "books": books,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[+] 已写入 {out_path}（{len(books)} 本，用时 {time.time() - started:.0f}s）")
    if books:
        print("[+] 前五：")
        for b in books[:5]:
            print(f"    {b['score']:g}  《{b['title']}》 {b['author']}  ({b['raters']}人评)")
    return 0 if books else 1


if __name__ == "__main__":
    sys.exit(main())
