"""Gera os sitemaps da raiz do guifav.dev no _site/ já montado.

- /sitemap-site.xml: as páginas do site fora do blog que devem ser indexadas, com o lastmod do último
  commit que mexeu no arquivo. Entra a página que não é a 404, não está bloqueada no robots.txt, não tem
  noindex e cujo canonical (quando existe) aponta para ela mesma.
- /sitemap.xml: índice com o /sitemap-site.xml e o /blog/sitemap.xml (gerado pelo Astro), cada um com o
  lastmod mais recente que contém. É o único endereço enviado ao Search Console e ao Bing.

Rodar depois do scripts/assemble_site.py, num clone com histórico completo (fetch-depth: 0):
    python3 scripts/build_sitemaps.py [--repo .] [--site _site]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

SITE_ORIGIN = "https://guifav.dev"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
SITE_SITEMAP = "sitemap-site.xml"
BLOG_SITEMAP = "blog/sitemap.xml"
INDEX = "sitemap.xml"
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


class SitemapError(Exception):
    """Não dá para gerar um sitemap confiável: o build para."""


class _Head(HTMLParser):
    """Só o que decide se a página entra: meta robots e link canonical."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.robots: list[str] = []
        self.canonicals: list[str] = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        if tag == "meta" and a.get("name", "").lower() == "robots":
            self.robots.append(a.get("content", "").lower())
        if tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonicals.append(a.get("href", "").strip())


def page_url(rel: str) -> str:
    """Caminho publicado de um arquivo do _site (pasta/index.html vira pasta/)."""
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def robots_disallow(robots_txt: str) -> list[str]:
    """Prefixos Disallow do grupo User-agent: * (o único que o site usa)."""
    out: list[str] = []
    in_star = False
    seen_rule = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if seen_rule:  # começa um grupo novo
                in_star, seen_rule = False, False
            in_star = in_star or val == "*"
        elif key in ("allow", "disallow"):
            seen_rule = True
            if in_star and key == "disallow" and val:
                out.append(val)
    return out


def noindex(robots: list[str]) -> bool:
    return any({d.strip() for d in c.split(",")} & {"noindex", "none"} for c in robots)


def canonical_is_self(canonicals: list[str], url: str) -> bool:
    """Sem canonical, a página é canônica de si mesma. Com um, ele precisa apontar para ela."""
    if not canonicals:
        return True
    if len(set(canonicals)) != 1:
        return False
    c = urlsplit(canonicals[0])
    return c.scheme == "https" and c.netloc == "guifav.dev" and (c.path or "/") == url and not c.query and not c.fragment


def indexable_pages(site: Path) -> list[tuple[str, str]]:
    """(arquivo relativo ao _site, caminho publicado) das páginas fora do blog que entram no sitemap."""
    robots = site / "robots.txt"
    disallow = robots_disallow(robots.read_text(encoding="utf-8")) if robots.is_file() else []
    out = []
    for f in sorted(site.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in HTML_SUFFIXES:
            continue
        rel = f.relative_to(site).as_posix()
        if rel.startswith("blog/") or rel == "404.html":
            continue
        url = page_url(rel)
        if any(url.startswith(p) for p in disallow):
            continue
        h = _Head()
        h.feed(f.read_text(encoding="utf-8"))
        h.close()
        if noindex(h.robots) or not canonical_is_self(h.canonicals, url):
            continue
        out.append((rel, url))
    return sorted(out, key=lambda x: x[1])


def git_lastmod(repo: Path, rel: str) -> str:
    """Data do último commit que mexeu no arquivo (ISO 8601 com fuso)."""
    shallow = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True, check=False).stdout.strip()
    if shallow != "false":
        raise SitemapError("clone raso ou fora do git: o lastmod sairia errado (use fetch-depth: 0)")
    r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI", "--", rel],
                       capture_output=True, text=True, check=False)
    d = r.stdout.strip()
    if r.returncode != 0 or not d:
        raise SitemapError(f"{rel}: sem commit no git, não há lastmod")
    return d


def _when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def newest(dates: list[str]) -> str | None:
    return max(dates, key=_when) if dates else None


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def urlset(entries: list[tuple[str, str]]) -> str:
    body = "\n".join(f"  <url><loc>{_esc(loc)}</loc><lastmod>{lm}</lastmod></url>" for loc, lm in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="{NS}">\n{body}\n</urlset>\n'


def sitemapindex(entries: list[tuple[str, str | None]]) -> str:
    body = "\n".join(f"  <sitemap><loc>{_esc(loc)}</loc>{f'<lastmod>{lm}</lastmod>' if lm else ''}</sitemap>"
                     for loc, lm in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="{NS}">\n{body}\n</sitemapindex>\n'


def build(repo: Path, site: Path) -> tuple[int, int]:
    """Grava os dois arquivos. Devolve (páginas do site, URLs do blog)."""
    for name in (INDEX, SITE_SITEMAP):
        if (site / name).exists():
            raise SitemapError(f"_site/{name} já existe: ele é gerado aqui, tire-o do site-manifest.txt")
    pages = [(SITE_ORIGIN + url, git_lastmod(repo, rel)) for rel, url in indexable_pages(site)]
    if not pages:
        raise SitemapError("nenhuma página do site para o sitemap: algo está errado no _site")
    try:
        blog = ET.parse(site / BLOG_SITEMAP).getroot()
    except (ET.ParseError, FileNotFoundError) as e:
        raise SitemapError(f"{BLOG_SITEMAP} inválido: {e}") from e
    if blog.tag != f"{{{NS}}}urlset":
        raise SitemapError(f"{BLOG_SITEMAP} não é um urlset")
    blog_locs = list(blog.iter(f"{{{NS}}}loc"))
    blog_dates = [(e.text or "").strip() for e in blog.iter(f"{{{NS}}}lastmod") if (e.text or "").strip()]
    (site / SITE_SITEMAP).write_text(urlset(pages), encoding="utf-8")
    (site / INDEX).write_text(sitemapindex([
        (f"{SITE_ORIGIN}/{SITE_SITEMAP}", newest([lm for _, lm in pages])),
        (f"{SITE_ORIGIN}/{BLOG_SITEMAP}", newest(blog_dates)),
    ]), encoding="utf-8")
    return len(pages), len(blog_locs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gera /sitemap.xml (índice) e /sitemap-site.xml no _site/.")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--site", default="_site")
    args = ap.parse_args(argv)
    try:
        n_site, n_blog = build(Path(args.repo), Path(args.site))
    except SitemapError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    print(f"sitemaps: {n_site} páginas do site em /{SITE_SITEMAP}; índice /{INDEX} com ele e /{BLOG_SITEMAP} ({n_blog} URLs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
