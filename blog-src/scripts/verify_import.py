#!/usr/bin/env python3
"""Verificação da importação do Substack (issue #19), independente do conversor.

Rodar em blog-src/ depois de `npm run build` (lê dist/):
    python3 scripts/verify_import.py --export <pasta do export>

Compara o HTML original da API (no cache) com o HTML publicado de cada post, usando um
parser próprio, e não o substack_convert.py. Garantias verificadas: G1 (lista de posts),
G2 (texto), G3 (links), G4 (imagens e bytes), G7 (podcasts) e G8 (datas).
Não roda no CI: o cache das respostas da API fica fora do repo.
"""
from __future__ import annotations

import argparse
import csv
import html as H
import hashlib
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit

from PIL import Image

SKIP = {"politica-de-privacidade", "termos-e-condicoes-de-uso"}
SITE_HOSTS = {"guilhermefavaron.com.br", "www.guilhermefavaron.com.br", "iaaplicada.substack.com"}
CDN_HOSTS = {"substackcdn.com", "substack-post-media.s3.amazonaws.com"}
MANUAL = {"https://download-files.wixmp.com/raw/068472_873a3d1be0a44b4bb1b3f25c463cae3b.pdf": "https://arxiv.org/abs/1706.03762"}
BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "pre", "figure",
         "figcaption", "br", "hr", "aside", "table", "tr", "td", "th", "section", "video", "iframe", "picture"}
SKIP_TAGS = {"script", "style", "svg", "button", "form", "template", "noscript"}
VOID = {"img", "source", "br", "hr", "input", "wbr", "meta", "link", "col", "path", "polyline", "line"}
IMG_BUDGET = 100 * 1024 * 1024
WIDTHS = (728, 1456)
COVER_WIDTH = 1200
# Identidade da imagem: diferença média de cinza (0 a 255) entre miniaturas 16x16. No corpus da #19, a mesma
# imagem recodificada fica em no máximo 5,3 (p99 de 0,9; o pior caso é um PNG em tons de cinza com muito
# detalhe) e imagens diferentes em no mínimo 6,1 (mediana de 79). Por isso são duas condições: um teto
# absoluto folgado, contra imagem de outro post, e a relativa, contra troca dentro do post: o arquivo
# publicado precisa estar mais perto do seu original do que de qualquer outro original diferente do post.
SAME_IMAGE_MAX_DIFF = 12.0
DISTINCT_ORIGINALS = 2.0
EPISODE_DATE_TOLERANCE = timedelta(days=2)
# R3 com referência revisada, independente do importador: post renomeado depois de publicado. O card do
# gui-brain com o slug antigo tem o mesmo título do post atual.
RENAMED = {"sistema-de-registros-para-agentes": "a-memoria-dos-meus-agentes-na-pratica"}
# Único href para o domínio antigo fora do conteúdo: o link "Newsletter" do rodapé, que aponta para o
# Substack até o formulário novo ser ligado (#21).
FOOTER_NEWSLETTER = '<a href="https://guilhermefavaron.com.br" target="_blank" rel="noopener">Newsletter</a>'
OLD_URL_ATTR = re.compile(r'\s(?:href|src|srcset|poster|content|action)="([^"]*)"')


def subscribe_href(h: str) -> bool:
    u = urlsplit(h)
    return (u.hostname or "").endswith("substack.com") and (
        u.path.startswith("/subscribe") or "action=share" in u.query or u.path.endswith("/share") or u.path.startswith("/account"))


class Extract(HTMLParser):
    """Texto visível, links, imagens e iframes de um trecho de HTML.

    root_class: só lê dentro do elemento com essa classe (a página publicada); sem ela, lê tudo (o original).
    """

    def __init__(self, root_class: str | None = None, post_ids: dict | None = None):
        super().__init__(convert_charrefs=True)
        self.root_class = root_class
        self.post_ids = post_ids or {}
        self.depth_root = 0 if root_class is None else -1
        self.stack: list[tuple[str, bool]] = []  # (tag, pula o conteúdo)
        self.text: list[str] = []
        self.links: list[tuple[str, str, bool]] = []  # (href, texto, é link de imagem)
        self.open_links: list[tuple[str, list, bool]] = []
        self.imgs: list[dict] = []
        self.iframes: list[str] = []
        self.videos = 0
        self.embeds: list[str] = []

    @property
    def active(self) -> bool:
        return self.depth_root >= 0 and not any(s for _, s in self.stack)

    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        cls = set(a.get("class", "").split())
        if self.depth_root < 0:
            if self.root_class in cls:
                self.depth_root = 0
                if tag not in VOID:
                    self.stack.append((tag, False))
            return
        skip = bool(tag in SKIP_TAGS or (
            tag == "div" and (cls & {"subscription-widget-wrap-editor", "subscription-widget-wrap", "image-link-expand"})) or (
            tag == "p" and "button-wrapper" in cls and subscribe_href(json.loads(a.get("data-attrs") or "{}").get("url") or "")))
        if self.active:
            if tag in BLOCK:
                self.text.append(" ")
            if tag == "img":
                self.imgs.append(a)
            elif tag == "iframe":
                self.iframes.append(a.get("src", ""))
            elif tag == "video":
                self.videos += 1
            elif tag == "a":
                self.open_links.append((a.get("href", ""), [], "image-link" in cls))
            if tag == "div" and "digest-post-embed" in cls:
                d = json.loads(a.get("data-attrs") or "{}")
                self.text.append(" " + d.get("title", "") + " ")
                self.embeds.append(self.post_ids.get(str(d.get("id")), "?"))
        if tag not in VOID:
            self.stack.append((tag, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.depth_root < 0:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                was_active = self.active
                del self.stack[i:]
                if self.root_class is not None and not self.stack:
                    self.depth_root = -2  # saiu do elemento raiz
                if was_active or self.active:
                    if tag in BLOCK:
                        self.text.append(" ")
                    if tag == "a" and self.open_links:
                        href, buf, is_img = self.open_links.pop()
                        self.links.append((href, "".join(buf), is_img))
                return

    def handle_data(self, data):
        if self.active:
            self.text.append(data)
            for _, buf, _ in self.open_links:
                buf.append(data)

    def norm_text(self) -> str:
        return re.sub(r"[ \t\n\r\f]+", " ", "".join(self.text)).strip()


def aliases_for(old: set, slugs: set) -> dict:
    """R3, calculada aqui: slug antigo sem par exato que é prefixo de exatamente um slug atual, ou RENAMED."""
    out = {}
    for u in old - slugs:
        if u in RENAMED and RENAMED[u] in slugs:
            out[u] = RENAMED[u]
            continue
        cands = [x for x in slugs if x.startswith(u + "-")]
        if len(cands) == 1:
            out[u] = cands[0]
    return out


def old_slugs(html: str) -> set:
    out = set()
    for h in re.findall(r'href="([^"]+)"', html):
        u = urlsplit(h)
        m = re.fullmatch(r"/(p|post)/([^/]+)/?", u.path)
        if (u.hostname or "").lower() in SITE_HOSTS and m:
            out.add(unquote(m.group(2)).lower())
    return out


def is_old(h: str) -> bool:
    host = (urlsplit(h.strip()).hostname or "").lower()
    return host in SITE_HOSTS or host.endswith(".substack.com")


def original_url(url: str) -> str:
    m = re.search(r"/image/fetch/[^/]+/(https?%3A.*)$", url)
    return unquote(m.group(1)) if m else url


def cache_path(cache: str, url: str) -> str:
    return os.path.join(cache, "orig", hashlib.sha256(original_url(url).encode()).hexdigest()[:24])


def thumb(path: str) -> list:
    with Image.open(path) as im:
        im.seek(0)
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return list(bg.convert("L").resize((16, 16), Image.Resampling.LANCZOS).tobytes())


def img_diff(a: list, b: list) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def srcset(v: str) -> list:
    out = []
    for c in v.split(","):
        parts = c.strip().split()
        if parts:
            out.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return out


def check_images(s: str, o_imgs: list, p_imgs: list, dist: str, cache: str) -> list:
    """G4, imagem a imagem e na ordem: arquivo de origem, alt, variantes, dimensões reais e identidade."""
    probs = []
    refs = []
    for oi in o_imgs:
        d = json.loads(oi.get("data-attrs") or "{}")
        path = cache_path(cache, d.get("src") or oi.get("src"))
        refs.append(thumb(path) if os.path.exists(path) else None)
    for k, (oi, pi) in enumerate(zip(o_imgs, p_imgs), 1):
        data = json.loads(oi.get("data-attrs") or "{}")
        osrc = data.get("src") or oi.get("src")
        oalt = data.get("alt") if data.get("alt") is not None else oi.get("alt", "")
        tag = f"G4 {s}: imagem {k}"
        if (pi.get("alt") or "") != (oalt or ""):
            probs.append(f"{tag}: alt {pi.get('alt')!r} diferente do original {oalt!r}")
        orig = cache_path(cache, osrc)
        if not os.path.exists(orig):
            probs.append(f"{tag}: original fora do cache: {osrc}")
            continue
        with Image.open(orig) as im:
            fmt, (W, H) = im.format, im.size
        src = pi.get("src", "")
        cands = srcset(pi.get("srcset", "")) or [(src, "")]
        paths = {}
        for u, _ in cands + [(src, "")]:
            if not u.startswith(f"/blog/img/{s}/") or not os.path.exists(os.path.join(dist, u[len("/blog/"):])):
                probs.append(f"{tag}: arquivo fora da pasta do post ou ausente: {u}")
            else:
                paths[u] = os.path.join(dist, u[len("/blog/"):])
        if len(paths) != len({u for u, _ in cands} | {src}):
            continue
        if fmt == "GIF":
            if not src.endswith(".gif") or pi.get("srcset"):
                probs.append(f"{tag}: GIF publicado como {src} com srcset {pi.get('srcset')!r}")
            elif open(paths[src], "rb").read() != open(orig, "rb").read():
                probs.append(f"{tag}: GIF diferente do original")
            if (pi.get("width"), pi.get("height")) != (str(W), str(H)):
                probs.append(f"{tag}: width/height {pi.get('width')}x{pi.get('height')} diferentes de {W}x{H}")
            continue
        widths = sorted({x for x in WIDTHS if x < W} | {min(W, WIDTHS[-1])})
        got = sorted(int(d[:-1]) for _, d in cands if d.endswith("w") and d[:-1].isdigit())
        if len(widths) > 1 and got != widths:
            probs.append(f"{tag}: srcset com larguras {got}, esperado {widths}")
        ref = refs[k - 1]
        others = [(j + 1, r) for j, r in enumerate(refs) if j != k - 1 and r is not None and img_diff(r, ref) > DISTINCT_ORIGINALS]
        dims = {}
        for u, path in paths.items():
            with Image.open(path) as im:
                dims[u] = im.size
                if im.format != "WEBP":
                    probs.append(f"{tag}: {u} não é WebP ({im.format})")
            x, y = dims[u]
            if x > W or abs(y - round(H * x / W)) > 1:
                probs.append(f"{tag}: {u} com {x}x{y}, fora da proporção do original {W}x{H} ou ampliado")
            tp = thumb(path)
            own = img_diff(tp, ref)
            near = [j for j, r in others if img_diff(tp, r) < own]
            if own > SAME_IMAGE_MAX_DIFF or near:
                probs.append(f"{tag}: {u} não é a imagem do original (diferença {own:.1f}; mais perto das imagens {near})")
        for u, d in cands:
            if d.endswith("w") and u in dims and dims[u][0] != int(d[:-1]):
                probs.append(f"{tag}: {u} declarado {d} com largura real {dims[u][0]}")
        big = max(dims.values()) if dims else (0, 0)
        if src in dims and dims[src] != big:
            probs.append(f"{tag}: src {src} não é a maior variante")
        if (pi.get("width"), pi.get("height")) != (str(big[0]), str(big[1])):
            probs.append(f"{tag}: width/height {pi.get('width')}x{pi.get('height')} diferentes do arquivo {big[0]}x{big[1]}")
    return probs


def check_cover(s: str, p: dict, page: str, dist: str, cache: str) -> list:
    og = re.search(r'<meta property="og:image" content="([^"]*)"', page)
    url = f"https://guifav.dev/blog/img/{s}/capa.jpg"
    if not p.get("cover_image"):
        return [f"G4 {s}: og:image da pasta do post sem capa no original"] if og and f"/blog/img/{s}/" in og.group(1) else []
    if not og or og.group(1) != url:
        return [f"G4 {s}: og:image {og.group(1) if og else None} diferente de {url}"]
    path = os.path.join(dist, "img", s, "capa.jpg")
    orig = cache_path(cache, p["cover_image"])
    if not os.path.exists(path) or not os.path.exists(orig):
        return [f"G4 {s}: capa ou original da capa ausente"]
    with Image.open(path) as im, Image.open(orig) as om:
        if im.format != "JPEG" or im.size[0] != min(om.size[0], COVER_WIDTH):
            return [f"G4 {s}: capa {im.format} {im.size}, esperado JPEG com largura {min(om.size[0], COVER_WIDTH)}"]
    if img_diff(thumb(path), thumb(orig)) > SAME_IMAGE_MAX_DIFF:
        return [f"G4 {s}: a capa não é a imagem do original"]
    return []


def norm_title(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def expected_href(h: str, slugs: set, aliases: dict) -> str | None:
    h = h.strip()
    for prefix, new in MANUAL.items():
        if h.startswith(prefix):
            return new
    if h.startswith("#"):
        return None
    u = urlsplit(h)
    if (u.hostname or "").lower() not in SITE_HOSTS:
        return h
    m = re.fullmatch(r"/(p|post)/([^/]+)/?", u.path)
    if m:
        s = unquote(m.group(2)).lower()
        if s in slugs:
            return f"/blog/{s}/"
        if s in aliases:
            return f"/blog/{aliases[s]}/"
        return h
    if u.path in ("", "/"):
        return "/blog/"
    return h


def original_links(ex: Extract, slugs, aliases) -> Counter:
    out = Counter()
    for href, text, is_img in ex.links:
        if not href:
            continue
        if is_img and ((urlsplit(href).hostname or "") in CDN_HOSTS or href == "undefined"):
            continue  # R7, ou link da imagem que já está quebrada no original
        if subscribe_href(href):
            continue  # botão de assinar removido
        if not is_img and text == "":
            continue
        e = expected_href(href, slugs, aliases)
        if e is not None:
            out[e] += 1
    for s in ex.embeds:
        out[f"/blog/{s}/"] += 1
    return out


def read_fm(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        t = f.read()
    m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
    if m is None:
        raise SystemExit(f"ERRO: {path} sem frontmatter")
    out = {}
    for ln in m.group(1).split("\n"):
        k, _, v = ln.partition(": ")
        out[k] = json.loads(v) if v.startswith('"') else v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/root/.roger/cache/blog-import")
    ap.add_argument("--export", required=True)
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    dist = os.path.join(a.root, "dist")
    problems: list[str] = []

    with open(os.path.join(a.export, "posts.csv"), encoding="utf-8") as f:
        published = {r["post_id"].split(".", 1)[1] for r in csv.DictReader(f) if r["is_published"] == "true"}
    expected = published - SKIP
    md_dir = os.path.join(a.root, "src", "content", "posts")
    md_slugs = {fn[:-3] for fn in os.listdir(md_dir) if fn.endswith(".md")}
    built = {d for d in os.listdir(dist) if os.path.exists(os.path.join(dist, d, "index.html"))
             and 'class="post"' in open(os.path.join(dist, d, "index.html"), encoding="utf-8").read()}
    for name, got in (("markdown", md_slugs), ("páginas publicadas", built)):
        if got != expected:
            problems.append(f"G1 {name}: faltam {sorted(expected - got)[:5]}, sobram {sorted(got - expected)[:5]}")

    posts = {}
    for s in expected:
        with open(os.path.join(a.cache, "api", "posts", s + ".json"), encoding="utf-8") as f:
            posts[s] = json.load(f)
    post_ids = {str(p["id"]): s for s, p in posts.items()}
    aliases = aliases_for(set().union(*(old_slugs(p["body_html"]) for p in posts.values())), set(expected))
    # O relatório do importador é conferido contra o cálculo daqui, nunca usado como referência.
    with open(os.path.join(a.cache, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    if report.get("aliases") != aliases:
        problems.append(f"G3 relatório: aliases {report.get('aliases')} diferentes do calculado {aliases}")
    report_unresolved = Counter((d["post"], d["href"]) for d in report.get("sem_destino", []))
    unresolved = Counter()
    with open(os.path.join(a.cache, "spotify-episodes.json"), encoding="utf-8") as f:
        episodes = {e["spotifyUrl"]: e for e in json.load(f) if e.get("spotifyUrl")}
    totals = Counter()
    spotify = Counter()
    for s in sorted(expected & built):
        p = posts[s]
        orig = Extract(post_ids=post_ids)
        orig.feed(p["body_html"])
        pub = Extract(root_class="post-body")
        with open(os.path.join(dist, s, "index.html"), encoding="utf-8") as f:
            page = f.read()
        pub.feed(page)
        # G2: texto
        t1, t2 = orig.norm_text(), pub.norm_text()
        if t1 != t2:
            i = next((k for k in range(min(len(t1), len(t2))) if t1[k] != t2[k]), min(len(t1), len(t2)))
            problems.append(f"G2 {s}: texto diverge no caractere {i}: original {t1[max(0, i - 40):i + 40]!r} | publicado {t2[max(0, i - 40):i + 40]!r}")
        # G3: links
        o_links = original_links(orig, set(expected), aliases)
        p_links = Counter(h for h, _, _ in pub.links)
        if o_links != p_links:
            problems.append(f"G3 {s}: links divergem: só no original {dict(o_links - p_links)}, só no publicado {dict(p_links - o_links)}")
        for h, n in o_links.items():
            if is_old(h):
                unresolved[(s, h)] += n
        # G4: imagens
        o_imgs = [i for i in orig.imgs if (json.loads(i.get("data-attrs") or "{}").get("src") or i.get("src")) not in ("", "undefined")]
        if len(o_imgs) != len(pub.imgs):
            problems.append(f"G4 {s}: {len(o_imgs)} imagens no original, {len(pub.imgs)} publicadas")
        problems += check_images(s, o_imgs, pub.imgs, dist, a.cache)
        problems += check_cover(s, p, page, dist, a.cache)
        # iframes e vídeo
        if len(orig.iframes) != len(pub.iframes):
            problems.append(f"G3 {s}: iframes: original {len(orig.iframes)}, publicado {len(pub.iframes)}")
        if ('native-video-embed' in p["body_html"]) != (pub.videos == 1):
            problems.append(f"G4 {s}: vídeo nativo não publicado")
        # G7 e G8
        fm = read_fm(os.path.join(md_dir, s + ".md"))
        if fm.get("date") != p["post_date"] or fm.get("updated") != p["updated_at"]:
            problems.append(f"G8 {s}: datas {fm.get('date')} {fm.get('updated')} diferentes de {p['post_date']} {p['updated_at']}")
        if p.get("type") == "podcast":
            url = fm.get("spotify_url")
            ep = episodes.get(url)
            if fm.get("index") != "false" or not url:
                problems.append(f"G7 {s}: podcast sem spotify_url ou indexável")
            elif ep is None:
                problems.append(f"G7 {s}: episódio fora do catálogo do Spotify: {url}")
            elif norm_title(ep["title"]) != norm_title(p["title"]) or abs(ts(ep["publishOn"]) - ts(p["post_date"])) > EPISODE_DATE_TOLERANCE:
                problems.append(f"G7 {s}: episódio {url} é {ep['title']!r} de {ep['publishOn'][:10]}, post {p['title']!r} de {p['post_date'][:10]}")
            m = re.search(r'<div class="podcast-embed"><iframe src="([^"]+)"', page)
            emb = "https://open.spotify.com/embed/episode/" + (url or "").rstrip("/").rsplit("/", 1)[-1]
            if not m or m.group(1) != emb:
                problems.append(f"G7 {s}: player publicado {m.group(1) if m else None}, esperado {emb}")
            spotify[url] += 1
            totals["podcasts"] += 1
        totals["posts"] += 1
        totals["imagens"] += len(pub.imgs)
        totals["links"] += sum(p_links.values())
    dup = [u for u, n in spotify.items() if n > 1]
    if dup:
        problems.append(f"G7: episódio do Spotify repetido: {dup}")

    if unresolved != report_unresolved:
        problems.append(f"G3 relatório: links sem destino divergem: só no calculado {dict(unresolved - report_unresolved)}, "
                        f"só no relatório {dict(report_unresolved - unresolved)}")

    # G3 em todo HTML do build, fora e dentro do conteúdo: cada URL do domínio antigo ou do Substack em
    # atributo (href, src, srcset, poster, content, action) ou no JSON-LD precisa ser um link sem destino
    # daquele post, na mesma quantidade, ou o link "Newsletter" do rodapé (um por página).
    for dirpath, _, files in os.walk(dist):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(dirpath, fn)
            t = open(path, encoding="utf-8").read()
            rel = os.path.relpath(path, dist)
            urls = []
            for v in OLD_URL_ATTR.findall(t):
                urls += [u for u, _ in srcset(v)] if " " in v.strip() and "," in v else [v]
            for js in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
                urls += re.findall(r'https?://[^"\s]+', js)
            got = Counter(H.unescape(u) for u in urls if is_old(H.unescape(u)))
            if FOOTER_NEWSLETTER in t:
                got["https://guilhermefavaron.com.br"] -= 1
            slug = rel.split(os.sep)[0] if rel.endswith(os.sep + "index.html") else None
            allowed_here = Counter({h: n for (ps, h), n in unresolved.items() if ps == slug})
            extra = +(got - allowed_here)
            if extra:
                problems.append(f"G3 {rel}: endereço antigo fora do relatório: {dict(extra)}")

    # G4 no build inteiro: nada do CDN do Substack. G3 no RSS e no sitemap.
    for dirpath, _, files in os.walk(os.path.join(dist)):
        for fn in files:
            if not fn.endswith((".html", ".xml")):
                continue
            t = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            if re.search(r"substackcdn\.com|substack-post-media", t):
                problems.append(f"G4 {fn}: referência ao CDN do Substack")
            if fn.endswith(".xml"):
                for h in re.findall(r'(?:href|src)="([^"]+)"|<link>([^<]+)</link>|<loc>([^<]+)</loc>', t):
                    hh = next(x for x in h if x).replace("&amp;", "&")
                    host = (urlsplit(hh).hostname or "").lower()
                    if host in SITE_HOSTS or host.endswith("substack.com"):
                        problems.append(f"G3 {fn}: endereço antigo: {hh}")
    img_bytes = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fs in os.walk(os.path.join(a.root, "public", "img")) for fn in fs)
    if img_bytes > IMG_BUDGET:
        problems.append(f"G4: imagens somam {img_bytes / 1e6:.1f} MB, acima de 100 MB")

    for p in problems:
        print("ERRO:", p)
    print(f"verify_import: {totals['posts']} posts ({totals['podcasts']} podcasts), {totals['imagens']} imagens, "
          f"{totals['links']} links, imagens em public/img: {img_bytes / 1e6:.1f} MB; problemas: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
