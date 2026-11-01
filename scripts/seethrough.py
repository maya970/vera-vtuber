"""调用 see-through 官方 HuggingFace Space，把一张立绘分解成分层 PSD + 部件 PNG。

用法：
    python scripts/seethrough.py --image index002.jpg
    python scripts/seethrough.py --image art.png --resolution 1024 --seed 42 --tblr-split

说明：
- 本机显卡显存不足时（<8GB）推荐用这个远程方案；官方 Space 免费，但每天有次数限制。
- 需要能访问 huggingface.co。国内网络可先设置 HTTPS_PROXY。
- 结果保存在 assets/source/ 下：<name>.psd 与 <name>/layers/*.png
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACE = "24yearsold/see-through-demo"


def _download_gallery(gallery, out_dir: Path) -> list[dict]:
    """把 Gallery 输出落盘，返回 [{tag, file}]。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for i, item in enumerate(gallery or []):
        path = None
        label = f"layer_{i:02d}"
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            path, label = item[0], (item[1] or label)
        elif isinstance(item, dict):
            path = item.get("image") or item.get("path") or item.get("url")
            label = item.get("caption") or item.get("label") or label
        elif isinstance(item, str):
            path = item
        if not path:
            continue
        src = Path(path)
        if not src.exists():
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label))[:60] or f"layer_{i:02d}"
        dst = out_dir / f"{safe}.png"
        shutil.copy2(src, dst)
        items.append({"tag": str(label), "file": dst.name})
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="see-through 单图分层（远程 HF Space）")
    parser.add_argument("--image", required=True, help="输入立绘路径")
    parser.add_argument("--out", default="assets/source", help="输出目录")
    parser.add_argument("--resolution", type=int, default=768, help="768/1024/1280，越大越慢")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tblr-split", action="store_true", help="把手臂/眼睛/耳朵拆成左右层")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    image = Path(args.image)
    if not image.is_absolute():
        image = ROOT / image
    if not image.exists():
        print(f"[x] 找不到图片：{image}")
        return 2

    out_root = ROOT / args.out
    out_root.mkdir(parents=True, exist_ok=True)
    stem = image.stem

    from gradio_client import Client, handle_file

    print(f"[*] 连接 {SPACE} …")
    t0 = time.time()
    client = Client(SPACE, verbose=False)
    print(f"[*] 上传 {image.name}，分辨率 {args.resolution}，seed {args.seed}")
    result = client.predict(
        handle_file(str(image)),
        args.resolution,
        args.seed,
        args.tblr_split,
        api_name="/inference",
    )
    print(f"[*] 推理完成，用时 {time.time() - t0:.0f}s")

    psd_path, gallery = None, []
    if isinstance(result, tuple) and len(result) >= 2:
        psd_path, gallery = result[0], result[1]
    elif isinstance(result, (list, tuple)):
        psd_path = result[0] if result else None

    saved = {"psd": "", "layers": [], "image": str(image), "resolution": args.resolution}
    if psd_path:
        src = Path(psd_path)
        if src.exists():
            dst = out_root / f"{stem}.psd"
            shutil.copy2(src, dst)
            saved["psd"] = str(dst.relative_to(ROOT))
            print(f"[+] PSD: {dst}")
        else:
            print(f"[!] PSD 输出不存在：{psd_path}")

    if gallery:
        layer_dir = out_root / stem / "layers"
        saved["layers"] = _download_gallery(gallery, layer_dir)
        print(f"[+] 图层 {len(saved['layers'])} 个 → {layer_dir}")

    meta = out_root / f"{stem}.json"
    meta.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[+] 记录：{meta}")
    return 0 if saved["psd"] or saved["layers"] else 1


if __name__ == "__main__":
    sys.exit(main())
