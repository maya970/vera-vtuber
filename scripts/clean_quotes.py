"""批量净化读者短评：删脏话 / 人身攻击 / 性暗示 / 剧透，改写成文明短句。

策略：
  * 默认只处理会被推荐的书（score >= 6.5），避免浪费 token
  * 每 15 条一批，带书名上下文，要求只做净化与压缩，不允许编造
  * 结果按「原文哈希」缓存到 data/quote_clean.json，可断点续跑
  * 改不动的返回空字符串，合并时直接丢弃

用法：
    python scripts/clean_quotes.py --limit 30        # 先试 30 条
    python scripts/clean_quotes.py                  # 全量（推荐书）
    python scripts/clean_quotes.py --min-score 0     # 连不推荐的书一起洗
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import config  # noqa: E402

CATALOG = ROOT / "data" / "catalog.json"
CACHE = ROOT / "data" / "quote_clean.json"

SYSTEM = "你是直播间的文案编辑。你的任务是把网络读者短评改写成文明、克制、可上直播的短句。只做净化与压缩，绝不添加原文没有的信息。"

RULES = """改写要求：
1. 每条改写成 1 句，18~36 个汉字，读起来像真实读者的感受。
2. 保留原文的具体意思（文笔、人物、节奏、设定、氛围），不要泛泛而谈。
3. 删除：脏话、人身攻击、性暗示与露骨内容、CP/性向露骨描述、剧透（结局/死亡/关键转折）、对作者的诅咒。
4. 不要出现「我」「作者」开头，不要书名号，不要感叹号连用，不要网络烂梗。
5. 如果一条短评主要是骂人、剧透、广告或无意义内容，text 返回空字符串。
6. 不评价作者本人，不写「想打/拍扁/去死/滚」这类攻击动作，不出现具体论坛、平台、人名。
7. 涉及现实政治、宗教、色情、暴力、自残、地域或性别歧视的内容，text 返回空字符串。
8. 只输出 JSON，格式：{"results":[{"i":0,"text":"..."}]}，i 与输入序号一致。"""

PROFANITY_PATH = ROOT / "config" / "profanity.txt"


def load_profanity() -> list[str]:
    if PROFANITY_PATH.exists():
        words = []
        for line in PROFANITY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
        if words:
            return words
    return ["傻逼", "脑残", "恶心", "垃圾", "卧槽", "装逼", "婊", "淫", "嫖", "屎"]


PROFANITY = load_profanity()


def is_dirty(text: str) -> bool:
    return any(w in text for w in PROFANITY)


def h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def local_safe(text: str) -> str:
    if is_dirty(text):
        return ""
    return text[:36]


def load_cache() -> dict[str, str]:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_cache(cache: dict[str, str]) -> None:
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


def parse_json(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("results", [])
        if isinstance(data, list):
            return data
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            return []
    return []


async def clean_batch(client: AsyncOpenAI, items: list[dict], sem: asyncio.Semaphore) -> dict[str, str]:
    lines = "\n".join(f'{it["i"]}. 《{it["title"]}》：{it["text"]}' for it in items)
    prompt = f"{RULES}\n\n待处理短评：\n{lines}"
    async with sem:
        for attempt in range(2):
            try:
                resp = await client.chat.completions.create(
                    model=config.get("deepseek.model", "deepseek-chat"),
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=1600,
                )
                content = resp.choices[0].message.content or ""
                results = parse_json(content)
                out: dict[str, str] = {}
                for row in results:
                    try:
                        idx = int(row.get("i"))
                        text = (row.get("text") or "").strip()
                    except Exception:  # noqa: BLE001
                        continue
                    if 0 <= idx < len(items):
                        out[items[idx]["text"]] = text[:60]
                if out:
                    return out
            except Exception as exc:  # noqa: BLE001
                print(f"    ! 批次失败（{attempt + 1}/2）：{type(exc).__name__} {exc}")
                await asyncio.sleep(1.5 + attempt * 2)
    # 兜底：本地过滤
    return {it["text"]: local_safe(it["text"]) for it in items}


async def main_async(args) -> int:
    key = config.get("deepseek.api_key") or ""
    if not key:
        print("[x] 没有 DeepSeek API Key")
        return 2
    client = AsyncOpenAI(api_key=key, base_url=config.get("deepseek.base_url"), timeout=90, max_retries=2)

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    books = data["books"]
    pool = [b for b in books if b["quotes"] and b["score"] >= args.min_score]
    if args.book_limit:
        pool = pool[: args.book_limit]
    print(f"[*] 待处理 {len(pool)} 本")

    cache = load_cache()
    clean_values = set(cache.values())
    tasks: list[dict] = []
    for b in pool:
        for q in b["quotes"]:
            if q in cache or q in clean_values:
                continue
            tasks.append({"i": 0, "title": b["title"][:20], "text": q})
    print(f"[*] 未缓存短评 {len(tasks)} 条（缓存 {len(cache)} 条）")
    if not tasks:
        merge(pool, cache)
        return 0
    if args.limit:
        tasks = tasks[: args.limit]

    random.shuffle(tasks)
    sem = asyncio.Semaphore(args.concurrency)
    batches = [tasks[i : i + args.batch] for i in range(0, len(tasks), args.batch)]
    print(f"[*] {len(batches)} 批，并发 {args.concurrency}")

    done = 0
    started = time.time()
    for chunk_start in range(0, len(batches), args.concurrency * 2):
        wave = batches[chunk_start : chunk_start + args.concurrency * 2]
        results = await asyncio.gather(*(clean_batch(client, b, sem) for b in wave))
        for res in results:
            cache.update(res)
        done += len(wave)
        save_cache(cache)
        if done % 20 == 0 or done == len(batches):
            spent = time.time() - started
            print(f"    {done}/{len(batches)} 批，{spent:.0f}s，缓存 {len(cache)} 条")
    save_cache(cache)
    merge(pool, cache)
    return 0


def merge(pool: list[dict], cache: dict[str, str]) -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    cache = load_cache()
    clean_values = set(cache.values())
    changed = 0
    dropped = 0
    for b in data["books"]:
        if not b["quotes"]:
            continue
        new_quotes = []
        for q in b["quotes"]:
            if q in cache:
                cleaned = cache[q]
                if cleaned and not is_dirty(cleaned):
                    new_quotes.append(cleaned)
                else:
                    dropped += 1
            elif q in clean_values:
                if is_dirty(q):
                    dropped += 1
                else:
                    new_quotes.append(q)
            elif b["score"] >= 6.5:
                # 推荐范围内的书，没洗净的原文一律不要
                dropped += 1
            elif is_dirty(q):
                dropped += 1
            else:
                new_quotes.append(q)
        if new_quotes != b["quotes"]:
            changed += 1
        b["quotes"] = new_quotes
    CATALOG.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"[+] 合并完成：改动 {changed} 本，丢弃 {dropped} 条短评")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, default=6.5)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    parser.add_argument("--book-limit", type=int, default=0)
    parser.add_argument("--merge-only", action="store_true")
    args = parser.parse_args()
    if args.merge_only:
        merge([], load_cache())
        return 0
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
