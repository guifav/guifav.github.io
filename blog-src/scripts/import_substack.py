#!/usr/bin/env python3
"""Importador dos posts do Substack para o blog (issue #19).

Rodar em blog-src/:
    python3 scripts/import_substack.py --export <pasta do export do Substack> --video <mp4>

Rodar de novo, com o cache, produz exatamente os mesmos arquivos (G5). O cache das respostas
da API e das imagens originais fica em --cache, fora do repo. --offline usa só o cache.

Requer Python 3.11+, Pillow (com WebP) e ffmpeg (pôster do vídeo). Não roda no CI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlsplit

from PIL import Image as PILImage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substack_convert as C  # noqa: E402

PUB = "https://www.guilhermefavaron.com.br"
SPOTIFY_PROFILE = "https://creators.spotify.com/pod/profile/ia-aplicada"
UA = "Mozilla/5.0 (guifav.dev importador do blog)"
SKIP = {"politica-de-privacidade", "termos-e-condicoes-de-uso"}  # a #24 redireciona as duas para a política nova
PAGES = {"livro-ia-inteligencia-artificial-portugues-gratuito"}  # página que vira post; o archive da API não lista páginas
# R6: a única troca de link feita à mão (link morto do Wix; indicação do Gui na #19).
REPLACEMENTS_BY_PREFIX = {
    ("transformers-inteligencia-artificial", "https://download-files.wixmp.com/raw/068472_873a3d1be0a44b4bb1b3f25c463cae3b.pdf"):
        "https://arxiv.org/abs/1706.03762",
}
VIDEO_POST = "ia-google-sheets-integracao-llm-codigo-aberto"
WIDTHS = (728, 1456)
COVER_WIDTH = 1200
WEBP_QUALITY = 80
JPEG_QUALITY = 82


# ---------------------------------------------------------------------------- rede e cache


def fetch(url: str, retries: int = 4) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - rede: tenta de novo e relata a última falha
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"falha ao baixar {url}: {last}")


def cached_json(path: str, url: str | None, offline: bool):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if offline or url is None:
        raise SystemExit(f"ERRO: {path} não está no cache e --offline foi pedido")
    data = json.loads(fetch(url))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def load_archive(cache: str, offline: bool) -> list:
    path = os.path.join(cache, "api", "archive.json")
    if os.path.exists(path) or offline:
        return cached_json(path, None, offline)
    items, cursor = [], None
    while True:
        url = f"{PUB}/api/v1/archive?sort=new&limit=50" + (f"&cursor={cursor}" if cursor else "")
        page = json.loads(fetch(url))
        batch = page["items"] if isinstance(page, dict) else page
        items.extend(batch)
        cursor = page.get("nextCursor") if isinstance(page, dict) else None
        if not cursor or not batch:
            break
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return items


def load_spotify(cache: str, offline: bool) -> list:
    path = os.path.join(cache, "spotify-episodes.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if offline:
        raise SystemExit(f"ERRO: {path} não está no cache e --offline foi pedido")
    page = fetch(SPOTIFY_PROFILE).decode("utf-8").replace("\\u002F", "/")
    eps = [json.loads(o) for o in re.findall(r'\{[^{}]*"spotifyUrl":"https://open\.spotify\.com/episode/[A-Za-z0-9]{22}"[^{}]*\}', page)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(eps, f, ensure_ascii=False, indent=1)
    return eps


def original_url(url: str) -> str:
    """URL original no S3 a partir da URL de transformação do CDN do Substack."""
    m = re.search(r"/image/fetch/[^/]+/(https?%3A.*)$", url)
    return unquote(m.group(1)) if m else url


def cached_file(cache: str, url: str, offline: bool) -> str:
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = os.path.join(cache, "orig", key)
    if not os.path.exists(path):
        if offline:
            raise SystemExit(f"ERRO: imagem fora do cache com --offline: {url}")
        data = fetch(url)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------- escrita idempotente


class Writer:
    def __init__(self, root: str):
        self.root = root
        self.written: set[str] = set()
        self.changed = 0

    def write(self, rel: str, data: bytes):
        path = os.path.join(self.root, rel)
        self.written.add(os.path.normpath(path))
        if os.path.exists(path):
            with open(path, "rb") as f:
                if f.read() == data:
                    return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        self.changed += 1

    def prune(self, rel_dir: str, keep=lambda p: False) -> list[str]:
        """Apaga o que o importador não gerou nesta execução (posts de teste, imagens antigas)."""
        removed = []
        base = os.path.join(self.root, rel_dir)
        for dirpath, _, files in os.walk(base):
            for fn in files:
                p = os.path.normpath(os.path.join(dirpath, fn))
                if p not in self.written and not keep(p):
                    os.remove(p)
                    removed.append(os.path.relpath(p, self.root))
        for dirpath, dirs, files in sorted(os.walk(base), reverse=True):
            if dirpath != base and not os.listdir(dirpath):
                os.rmdir(dirpath)
        return removed


# ---------------------------------------------------------------------------- imagens


ENC_CACHE: str | None = None  # pasta de cache das codificações; None = codifica sempre (prova da G5)


def _encode_webp(im: PILImage.Image, key: str | None = None) -> bytes:
    path = None
    if ENC_CACHE and key:
        h = hashlib.sha256(f"{key}|webp|{WEBP_QUALITY}|6|{PILImage.__version__}".encode()).hexdigest()[:32]
        path = os.path.join(ENC_CACHE, h + ".webp")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
    data = buf.getvalue()
    if path:
        os.makedirs(ENC_CACHE, exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return data


def _norm_mode(im: PILImage.Image) -> PILImage.Image:
    if im.mode in ("RGB", "RGBA"):
        return im
    if im.mode in ("P", "LA", "PA") or (im.mode == "L" and "transparency" in im.info):
        return im.convert("RGBA")
    return im.convert("RGB")


def make_image(src_path: str, slug: str, stem: str, w: Writer) -> tuple[C.Image, int]:
    """Gera os arquivos de uma imagem do corpo. Devolve o Image e os bytes publicados."""
    with PILImage.open(src_path) as im:
        if im.format == "GIF":
            with open(src_path, "rb") as f:
                data = f.read()
            rel = f"img/{slug}/{stem}.gif"
            w.write(os.path.join("public", rel), data)
            return C.Image(files=[(im.width, rel)], width=im.width, height=im.height), len(data)
        im.load()
        base = _norm_mode(im)
        W, H = base.size
        widths = sorted({x for x in WIDTHS if x < W} | {min(W, WIDTHS[-1])})
        files, total, out_h = [], 0, H
        for x in widths:
            h = round(H * x / W)
            img = base if x == W else base.resize((x, h), PILImage.Resampling.LANCZOS)
            data = _encode_webp(img, key=f"{os.path.basename(src_path)}|{x}")
            rel = f"img/{slug}/{stem}-{x}.webp"
            w.write(os.path.join("public", rel), data)
            files.append((x, rel))
            total += len(data)
            out_h = h
        return C.Image(files=files, width=widths[-1], height=out_h), total


def make_cover(src_path: str, slug: str, w: Writer) -> tuple[str, int]:
    with PILImage.open(src_path) as im:
        im.seek(0)
        im.load()
        base = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
        if base.mode == "RGBA":
            bg = PILImage.new("RGB", base.size, (255, 255, 255))
            bg.paste(base, mask=base.getchannel("A"))
            base = bg
        W, H = base.size
        if W > COVER_WIDTH:
            base = base.resize((COVER_WIDTH, round(H * COVER_WIDTH / W)), PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    rel = f"img/{slug}/capa.jpg"
    data = buf.getvalue()
    w.write(os.path.join("public", rel), data)
    return rel, len(data)


def make_video(video: str, cache: str, w: Writer) -> tuple[dict, int]:
    with open(video, "rb") as f:
        data = f.read()
    w.write(os.path.join("public", "video", f"{VIDEO_POST}.mp4"), data)
    frame = os.path.join(cache, "video-poster.png")
    if not os.path.exists(frame):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", video, "-frames:v", "1", frame], check=True)
    with PILImage.open(frame) as im:
        im.load()
        base = _norm_mode(im)
        W, H = base.size
        x = min(W, WIDTHS[-1])
        img = base if x == W else base.resize((x, round(H * x / W)), PILImage.Resampling.LANCZOS)
        poster = _encode_webp(img)
    rel = f"img/{VIDEO_POST}/video-poster.webp"
    w.write(os.path.join("public", rel), poster)
    return {"src": f"/blog/video/{VIDEO_POST}.mp4", "poster": f"/blog/{rel}", "width": W, "height": H}, len(data) + len(poster)


# ---------------------------------------------------------------------------- importação


def image_srcs(body: str) -> list[str]:
    out = []
    for n in C.parse(body).walk():
        if n.tag != "img":
            continue
        try:
            data = json.loads(n.get("data-attrs") or "{}")
        except json.JSONDecodeError:
            data = {}
        src = data.get("src") or n.get("src")
        if src and src != "undefined":
            out.append(src)
    return out


def published_from_export(export: str) -> dict:
    with open(os.path.join(export, "posts.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["is_published"] == "true"]
    return {r["post_id"].split(".", 1)[1]: r for r in rows}


def aliases_for(unknown: set, slugs: set) -> dict:
    """R3: slug antigo que é prefixo de exatamente um slug atual."""
    out = {}
    for u in unknown:
        cands = [s for s in slugs if s.startswith(u + "-")]
        if len(cands) == 1:
            out[u] = cands[0]
    return out


def old_site_slugs(posts: list) -> set:
    found = set()
    for p in posts:
        for m in re.finditer(r'href="https?://(?:www\.)?guilhermefavaron\.com\.br/post/([^"/?#]+)', p["body_html"]):
            found.add(unquote(m.group(1)).lower())
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/root/.roger/cache/blog-import")
    ap.add_argument("--export", required=True, help="pasta do export do Substack (posts.csv)")
    ap.add_argument("--video", required=True, help=f"MP4 do post {VIDEO_POST}")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-enc-cache", action="store_true", help="codifica todas as imagens de novo (prova de idempotência)")
    ap.add_argument("--root", default=".", help="blog-src/")
    a = ap.parse_args(argv)
    global ENC_CACHE
    ENC_CACHE = None if a.no_enc_cache else os.path.join(a.cache, "enc")

    archive = load_archive(a.cache, a.offline)
    published = published_from_export(a.export)
    arch_slugs = {p["slug"] for p in archive} | PAGES | SKIP
    if arch_slugs != set(published):
        raise SystemExit(f"ERRO: API e export divergem: só na API {sorted(arch_slugs - set(published))[:5]}, "
                         f"só no export {sorted(set(published) - arch_slugs)[:5]}")
    posts = []
    for slug in sorted(arch_slugs - SKIP):
        posts.append(cached_json(os.path.join(a.cache, "api", "posts", slug + ".json"),
                                 f"{PUB}/api/v1/posts/{slug}", a.offline))
    slugs = {p["slug"] for p in posts}
    post_ids = {str(p["id"]): p["slug"] for p in posts}
    aliases = aliases_for(old_site_slugs(posts) - slugs, slugs)
    pods = C.match_podcasts([p for p in posts if p.get("type") == "podcast"], load_spotify(a.cache, a.offline))

    # Downloads em paralelo; a conversão e a escrita seguem em ordem fixa.
    urls = sorted({original_url(u) for p in posts for u in image_srcs(p["body_html"])} |
                  {original_url(p["cover_image"]) for p in posts if p.get("cover_image")})
    with ThreadPoolExecutor(8) as ex:
        paths = dict(zip(urls, ex.map(lambda u: cached_file(a.cache, u, a.offline), urls)))

    w = Writer(a.root)
    report = {"posts": 0, "por_tipo": {}, "imagens": 0, "imagens_bytes": 0, "capas": 0, "capas_bytes": 0,
              "originais_bytes": 0, "video_bytes": 0, "regras": {}, "sem_destino": [], "aliases": aliases}
    video, vbytes = make_video(a.video, a.cache, w)
    report["video_bytes"] = vbytes
    for p in posts:
        slug = p["slug"]
        images, seen = {}, set()
        for src in image_srcs(p["body_html"]):
            if src in images:
                continue
            o = original_url(src)
            stem = hashlib.sha256(o.encode()).hexdigest()[:10]
            images[src], size = make_image(paths[o], slug, stem, w)
            if o not in seen:
                seen.add(o)
                report["imagens_bytes"] += size
                report["originais_bytes"] += os.path.getsize(paths[o])
        cover = None
        if p.get("cover_image"):
            cover, size = make_cover(paths[original_url(p["cover_image"])], slug, w)
            report["capas"] += 1
            report["capas_bytes"] += size
        hrefs = [html.unescape(m) for m in re.findall(r'href="([^"]+)"', p["body_html"])]
        replacements = {h: new for (s, prefix), new in REPLACEMENTS_BY_PREFIX.items() if s == slug
                        for h in hrefs if h.startswith(prefix)}
        ctx = C.Context(slug=slug, slugs=slugs, images=images, aliases=aliases, replacements=replacements,
                        video=video if slug == VIDEO_POST else None, post_ids=post_ids)
        body = C.convert(p["body_html"], ctx)
        meta = dict(p)
        meta["description"] = (p.get("search_engine_description") or p.get("description") or p.get("subtitle") or "").strip()
        fm = C.frontmatter(meta, cover=cover, spotify_url=pods.get(slug))
        w.write(os.path.join("src", "content", "posts", f"{slug}.md"), (fm + "\n" + body).encode("utf-8"))
        report["posts"] += 1
        t = "podcast" if p.get("type") == "podcast" else ("pagina" if p.get("type") == "page" else "newsletter")
        report["por_tipo"][t] = report["por_tipo"].get(t, 0) + 1
        report["imagens"] += ctx.stats["imagens"]
        for k, v in ctx.stats.items():
            report["regras"][k] = report["regras"].get(k, 0) + v
        report["sem_destino"] += [{"post": slug, "href": u} for u in ctx.unresolved]

    if report["regras"].get("R6", 0) != len(REPLACEMENTS_BY_PREFIX):
        raise SystemExit(f"ERRO: R6 aplicada {report['regras'].get('R6', 0)} vez(es), esperado {len(REPLACEMENTS_BY_PREFIX)}")
    removed = w.prune(os.path.join("src", "content", "posts")) + w.prune(os.path.join("public", "img")) + w.prune(os.path.join("public", "video"))
    report["removidos_do_repo"] = removed
    report["arquivos_alterados"] = w.changed
    with open(os.path.join(a.cache, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps({k: v for k, v in report.items() if k not in ("sem_destino", "removidos_do_repo")}, ensure_ascii=False, sort_keys=True))
    print(f"sem destino: {len(report['sem_destino'])}; removidos do repo: {len(removed)}; alterados: {w.changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
