"""把 see-through 输出的分层 PSD 转换成网页 / Three.js 可用的分层资源。

生成：
    assets/avatar/manifest.json
    assets/avatar/layers/*.png
    assets/avatar/backdrop.jpg
    assets/avatar/preview.png

用法：
    python scripts/build_avatar.py --psd assets/source/index002.psd
    python scripts/build_avatar.py --psd xxx.psd --name 薇拉 --max-width 1280
    python scripts/build_avatar.py --fallback-image index002.jpg   # 没有 PSD 时兜底
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "avatar"
LAYER_DIR = OUT_DIR / "layers"

HEAD_PAT = re.compile(
    r"hair|face|eye|pupil|iris|brow|lash|mouth|lip|nose|ear|head|hat|glasses|horn|"
    r"发|头发|刘海|眼|瞳|眉|嘴|唇|脸|耳|鼻|头|帽|镜",
    re.I,
)
BODY_PAT = re.compile(
    r"body|cloth|shirt|dress|armor|arm|hand|leg|skirt|cape|cloak|shoe|foot|neck|"
    r"身体|衣|裙|手|臂|腿|脚|披风|领",
    re.I,
)
BG_PAT = re.compile(r"background|bg|backdrop|scene|背景|场景", re.I)
FRONT_HAIR = re.compile(r"hair.*(front|fore)|刘海|front.*hair", re.I)
BACK_HAIR = re.compile(r"hair.*(back|rear)|后发", re.I)


@dataclass
class Layer:
    name: str
    image: Image.Image
    x: int
    y: int
    order: int
    tag: str
    part: str
    group: str


def classify(name: str) -> tuple[str, str]:
    """返回 (part, group)。按英文词元 / 中文字符判定，避免 ear 命中 wear。"""
    low = name.lower()
    toks = set(re.findall(r"[a-z]+", low))
    cjk = re.sub(r"[a-z0-9\s_\-/]", "", low)

    def has_cjk(*chars: str) -> bool:
        return any(ch in cjk for ch in chars)

    if "hair" in toks or has_cjk("发", "刘海"):
        if "front" in toks or has_cjk("刘海"):
            return "hair_front", "head"
        if "back" in toks or has_cjk("后发"):
            return "hair_back", "head"
        return "hair", "head"
    if toks & {"eye", "eyes", "eyewhite", "eyelash", "eyelashes", "irides", "iris", "pupil", "eyeball"} or has_cjk("眼", "瞳"):
        return "eye", "head"
    if toks & {"eyebrow", "brow", "brows"} or has_cjk("眉"):
        return "brow", "head"
    if toks & {"mouth", "lip", "lips"} or has_cjk("嘴", "唇"):
        return "mouth", "head"
    if toks & {"nose"} or has_cjk("鼻"):
        return "nose", "head"
    if "face" in toks or has_cjk("脸"):
        return "face", "head"
    if toks & {"ear", "ears"} or has_cjk("耳"):
        return "ear", "head"

    wear = {t for t in toks if t.endswith("wear")}
    if wear:
        if wear & {"headwear", "earwear", "eyewear", "hat"}:
            return "headwear", "head"
        if wear & {"handwear", "glove", "gloves"}:
            return "hand", "body"
        if wear & {"neckwear"}:
            return "neckwear", "body"
        return "cloth", "body"

    if toks & {"neck"} or has_cjk("颈", "脖"):
        return "neck", "body"
    if toks & {"arm", "arms", "hand", "hands", "leg", "legs", "foot", "feet", "shoe", "shoes"} or has_cjk(
        "手", "臂", "腿", "脚"
    ):
        return "limb", "body"
    if toks & {"cloth", "clothes", "shirt", "dress", "armor", "skirt", "cape", "cloak", "coat", "jacket"} or has_cjk(
        "衣", "裙", "披风", "袍"
    ):
        return "cloth", "body"
    if toks & {"objects", "object", "prop", "props", "weapon", "book", "card"} or has_cjk("道具", "书", "卡", "武器"):
        return "prop", "body"
    if toks & {"tail", "wings", "wing"} or has_cjk("尾", "翅"):
        return "wing", "body"
    return "extra", "body"


def _clean_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_") or "layer"


def collect_psd(psd_path: Path) -> tuple[list[Layer], tuple[int, int]]:
    from psd_tools import PSDImage

    psd = PSDImage.open(str(psd_path))
    width, height = psd.width, psd.height
    flat: list[tuple[object, str]] = []

    def walk(node, prefix: str = "") -> None:
        for item in node:
            if item.is_group():
                walk(item, prefix + item.name + "/")
            else:
                flat.append((item, prefix + item.name))

    walk(psd)
    # psd-tools 按 PSD 存储顺序返回：索引 0 是最底层（最靠后），正好是渲染顺序
    layers: list[Layer] = []
    for idx, (layer, full_name) in enumerate(flat):
        if not getattr(layer, "visible", True):
            continue
        bbox = layer.bbox
        if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        try:
            img = layer.composite()
        except Exception:  # noqa: BLE001
            continue
        if img is None:
            continue
        img = img.convert("RGBA")
        if img.getbbox() is None:
            continue
        part, group = classify(full_name)
        if BG_PAT.search(full_name):
            continue
        layers.append(
            Layer(
                name=_clean_name(full_name),
                image=img,
                x=int(bbox[0]),
                y=int(bbox[1]),
                order=idx,
                tag=full_name,
                part=part,
                group=group,
            )
        )
    return layers, (width, height)


def character_bounds(layers: list[Layer], size: tuple[int, int]) -> tuple[int, int, int, int]:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for layer in layers:
        if layer.part in ("extra", "prop"):
            continue
        xs0.append(layer.x)
        ys0.append(layer.y)
        xs1.append(layer.x + layer.image.width)
        ys1.append(layer.y + layer.image.height)
    if not xs0:
        return 0, 0, size[0], size[1]
    x0, y0, x1, y1 = min(xs0), min(ys0), max(xs1), max(ys1)
    mx = int((x1 - x0) * 0.06) + 8
    my = int((y1 - y0) * 0.04) + 8
    return max(0, x0 - mx), max(0, y0 - my), min(size[0], x1 + mx), min(size[1], y1 + my)


def build_manifest(
    layers: list[Layer],
    canvas: tuple[int, int],
    crop: tuple[int, int, int, int],
    name: str,
    drop_parts: set[str] | None = None,
) -> dict:
    drop_parts = drop_parts or set()
    cx0, cy0, cx1, cy1 = crop
    cw, ch = cx1 - cx0, cy1 - cy0
    out_layers: list[dict] = []
    LAYER_DIR.mkdir(parents=True, exist_ok=True)

    depth_order = [l for l in sorted(layers, key=lambda l: l.order) if l.part not in drop_parts]
    n = max(1, len(depth_order) - 1)
    spread = 1.1
    for i, layer in enumerate(depth_order):
        lx, ly = layer.x - cx0, layer.y - cy0
        # 裁剪到画布内
        img = layer.image
        if lx < 0:
            img = img.crop((-lx, 0, img.width, img.height))
            lx = 0
        if ly < 0:
            img = img.crop((0, -ly, img.width, img.height))
            ly = 0
        if lx + img.width > cw:
            img = img.crop((0, 0, cw - lx, img.height))
        if ly + img.height > ch:
            img = img.crop((0, 0, img.width, ch - ly))
        if img.width < 1 or img.height < 1:
            continue
        fname = f"{i:02d}_{layer.name[:40]}.png"
        img.save(LAYER_DIR / fname, optimize=True)
        z = (i / n - 0.5) * spread
        out_layers.append(
            {
                "id": f"{i:02d}_{layer.name[:40]}",
                "tag": layer.tag,
                "file": f"layers/{fname}",
                "x": lx,
                "y": ly,
                "w": img.width,
                "h": img.height,
                "order": i,
                "z": round(z, 4),
                "part": layer.part,
                "group": layer.group,
            }
        )

    anchors = compute_anchors(out_layers, (cw, ch))
    return {
        "version": 1,
        "name": name,
        "created": "",
        "canvas": {"width": cw, "height": ch},
        "source": {"crop": [cx0, cy0, cx1, cy1]},
        "backdrop": "backdrop.jpg",
        "layers": out_layers,
        "anchors": anchors,
    }


def compute_anchors(layers: list[dict], canvas: tuple[int, int]) -> dict:
    cw, ch = canvas
    anchors: dict[str, dict] = {"feet": {"x": cw / 2, "y": ch * 0.99}}

    def center_of(part: str) -> dict | None:
        subset = [l for l in layers if l["part"] == part]
        if not subset:
            return None
        x0 = min(l["x"] for l in subset)
        y0 = min(l["y"] for l in subset)
        x1 = max(l["x"] + l["w"] for l in subset)
        y1 = max(l["y"] + l["h"] for l in subset)
        return {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2, "top": y0, "bottom": y1, "w": x1 - x0, "h": y1 - y0}

    head = center_of("head")
    if head is None:
        anchors["head"] = {"x": cw / 2, "y": ch * 0.30}
        anchors["neck"] = {"x": cw / 2, "y": ch * 0.42}
    else:
        anchors["head"] = {"x": head["x"], "y": head["y"]}
        anchors["neck"] = {"x": head["x"], "y": head["bottom"] - head["h"] * 0.05}

    mouth = center_of("mouth")
    if mouth:
        anchors["mouth"] = {"x": mouth["x"], "y": mouth["y"]}
    eye = center_of("eye")
    if eye:
        anchors["eyes"] = {"x": eye["x"], "y": eye["y"]}
    body = center_of("body")
    if body:
        anchors["body"] = {"x": body["x"], "y": body["y"]}
    return anchors


def make_preview(manifest: dict, out_path: Path) -> None:
    cw, ch = manifest["canvas"]["width"], manifest["canvas"]["height"]
    comp = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    for layer in sorted(manifest["layers"], key=lambda l: l["order"]):
        img = Image.open(OUT_DIR / layer["file"]).convert("RGBA")
        comp.alpha_composite(img, (layer["x"], layer["y"]))
    bg = Image.new("RGBA", (cw, ch), (18, 20, 28, 255))
    bg.alpha_composite(comp)
    bg.convert("RGB").save(out_path, quality=92)


def make_backdrop(source: Path, out_path: Path) -> None:
    img = Image.open(source).convert("RGB")
    if img.width > 960:
        img = img.resize((960, int(img.height * 960 / img.width)), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(14))
    img.save(out_path, quality=82)


def fallback(images: list[Path], name: str) -> dict:
    """没有 PSD 时：把整张图当作单层，至少能先跑通直播链路。"""
    base = images[0]
    img = Image.open(base).convert("RGBA")
    maxw = 1280
    if img.width > maxw:
        img = img.resize((maxw, int(img.height * maxw / img.width)), Image.LANCZOS)
    LAYER_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "layers").mkdir(parents=True, exist_ok=True)
    img.save(LAYER_DIR / "00_full.png", optimize=True)
    manifest = {
        "version": 1,
        "name": name,
        "created": "",
        "canvas": {"width": img.width, "height": img.height},
        "source": {"crop": [0, 0, img.width, img.height]},
        "backdrop": "backdrop.jpg",
        "layers": [
            {
                "id": "00_full",
                "tag": "full",
                "file": "layers/00_full.png",
                "x": 0,
                "y": 0,
                "w": img.width,
                "h": img.height,
                "order": 0,
                "z": 0.0,
                "part": "extra",
                "group": "body",
            }
        ],
        "anchors": {
            "head": {"x": img.width / 2, "y": img.height * 0.3},
            "neck": {"x": img.width / 2, "y": img.height * 0.45},
            "feet": {"x": img.width / 2, "y": img.height * 0.99},
        },
    }
    make_backdrop(base, OUT_DIR / "backdrop.jpg")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="分层 PSD → 网页形象资源")
    parser.add_argument("--psd", default="", help="see-through 生成的 PSD")
    parser.add_argument("--fallback-image", default="", help="没有 PSD 时用整图兜底")
    parser.add_argument("--source", default="", help="背景用的原始插画（默认自动找同名图 / index002.jpg）")
    parser.add_argument("--keep-props", action="store_true", help="保留桌面/道具等 prop 图层（默认丢弃）")
    parser.add_argument("--name", default="薇拉")
    parser.add_argument("--max-width", type=int, default=1280)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LAYER_DIR.exists():
        shutil.rmtree(LAYER_DIR)
    LAYER_DIR.mkdir(parents=True, exist_ok=True)

    source_image: Path | None = None
    if args.psd:
        psd_path = Path(args.psd)
        if not psd_path.is_absolute():
            psd_path = ROOT / psd_path
        if not psd_path.exists():
            print(f"[x] 找不到 PSD：{psd_path}")
            return 2
        print(f"[*] 读取 PSD {psd_path.name}")
        layers, size = collect_psd(psd_path)
        if not layers:
            print("[x] PSD 里没有可用的图层")
            return 3
        crop = character_bounds(layers, size)
        manifest = build_manifest(
            layers,
            (crop[2] - crop[0], crop[3] - crop[1]),
            crop,
            args.name,
            drop_parts=set() if args.keep_props else {"prop"},
        )
        manifest["source"]["width"] = size[0]
        manifest["source"]["height"] = size[1]
        # see-through 的输入图通常和 PSD 同名
        guess = psd_path.with_suffix(".png")
        if guess.exists():
            source_image = guess
        print(f"[+] 图层 {len(manifest['layers'])} 个，画布 {manifest['canvas']['width']}x{manifest['canvas']['height']}")
    elif args.fallback_image:
        image = Path(args.fallback_image)
        if not image.is_absolute():
            image = ROOT / image
        if not image.exists():
            print(f"[x] 找不到图片：{image}")
            return 2
        manifest = fallback([image], args.name)
        source_image = image
        print("[!] 使用单层兜底形象（跑通链路用，正式直播请先做 see-through 分层）")
    else:
        print("[x] 需要 --psd 或 --fallback-image")
        return 2

    import datetime

    manifest["created"] = datetime.datetime.now().isoformat(timespec="seconds")
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    make_preview(manifest, OUT_DIR / "preview.png")

    backdrop_src = Path(args.source) if args.source else None
    if backdrop_src and not backdrop_src.is_absolute():
        backdrop_src = ROOT / backdrop_src
    if backdrop_src is None or not backdrop_src.exists():
        for candidate in (source_image, ROOT / "index002.jpg"):
            if candidate and Path(candidate).exists():
                backdrop_src = Path(candidate)
                break
    if backdrop_src and backdrop_src.exists():
        make_backdrop(backdrop_src, OUT_DIR / "backdrop.jpg")
    print(f"[+] 已生成 {OUT_DIR / 'manifest.json'}")
    print(f"[+] 预览 {OUT_DIR / 'preview.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
