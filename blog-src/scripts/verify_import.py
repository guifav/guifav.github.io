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
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

SKIP = {"politica-de-privacidade", "termos-e-condicoes-de-uso"}
SITE_HOSTS = {"guilhermefavaron.com.br", "www.guilhermefavaron.com.br", "iaaplicada.substack.com"}
CDN_HOSTS = {"substackcdn.com", "substack-post-media.s3.amazonaws.com"}
MANUAL = {"https://download-files.wixmp.com/raw/068472_873a3d1be0a44b4bb1b3f25c463cae3b.pdf": "https://arxiv.org/abs/1706.03762"}
BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "pre", "figure",
         "figcaption", "br", "hr", "aside", "table", "tr", "td", "th", "section", "video", "iframe", "picture"}
SKIP_TAGS = {"script", "style", "svg", "button", "form", "template", "noscript"}
VOID = {"img", "source", "br", "hr", "input", "wbr", "meta", "link", "col", "path", "polyline", "line"}
IMG_BUDGET = 100 * 1024 * 1024


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
    with open(os.path.join(a.cache, "report.json"), encoding="utf-8") as f:
        aliases = json.load(f)["aliases"]

    with open(os.path.join(a.cache, "report.json"), encoding="utf-8") as f:
        allowed = {d["href"] for d in json.load(f)["sem_destino"]}
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
        # G4: imagens
        o_imgs = [i for i in orig.imgs if (json.loads(i.get("data-attrs") or "{}").get("src") or i.get("src")) not in ("", "undefined")]
        if len(o_imgs) != len(pub.imgs):
            problems.append(f"G4 {s}: {len(o_imgs)} imagens no original, {len(pub.imgs)} publicadas")
        for im in pub.imgs:
            for u in [im.get("src", "")] + [c.strip().split(" ")[0] for c in im.get("srcset", "").split(",") if c.strip()]:
                if not u.startswith(f"/blog/img/{s}/") or not os.path.exists(os.path.join(dist, u[len('/blog/'):])):
                    problems.append(f"G4 {s}: imagem fora da pasta do post ou ausente: {u}")
            if not im.get("width") or not im.get("height"):
                problems.append(f"G4 {s}: imagem sem width/height")
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
            if fm.get("index") != "false" or not fm.get("spotify_url"):
                problems.append(f"G7 {s}: podcast sem spotify_url ou indexável")
            spotify[fm.get("spotify_url")] += 1
            totals["podcasts"] += 1
        for h in [h for h, _, _ in pub.links] + [i.get("src", "") for i in pub.imgs] + pub.iframes:
            host = (urlsplit(h).hostname or "").lower()
            if (host in SITE_HOSTS or host.endswith("substack.com")) and h not in allowed:
                problems.append(f"G3 {s}: href fora do relatório: {h}")
        totals["posts"] += 1
        totals["imagens"] += len(pub.imgs)
        totals["links"] += sum(p_links.values())
    dup = [u for u, n in spotify.items() if n > 1]
    if dup:
        problems.append(f"G7: episódio do Spotify repetido: {dup}")

    # G4 no build inteiro: nada do CDN do Substack. G3 no RSS e no sitemap; nos posts, foi conferido acima,
    # só no conteúdo. O link "Newsletter" do rodapé do leiaute aponta para o Substack até o cutover (#24).
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
