"""Gera os sitemaps da raiz do guifav.dev no _site/ já montado.

- /sitemap-site.xml: as páginas do site fora do blog que devem ser indexadas, com o lastmod do último
  commit que mexeu no arquivo. Página gerada a partir de dados de fora (o /ferramentas/llm-ranking/, #113) declara a data dos
  dados na meta DATA_META, e o lastmod dela é essa data: muda quando os dados mudam, não quando o build roda. Entra a página que não é a 404, não está bloqueada no robots.txt, não tem
  noindex e cujo canonical (quando existe) aponta para ela mesma.
  A home (index.html) lista os últimos artigos do blog (issue #136): o lastmod dela é o mais recente entre o
  último commit do arquivo e o lastmod, no /blog/sitemap.xml, de cada artigo listado nela (entre os marcadores <!-- ultimos-artigos -->).
- /sitemap.xml: índice com o /sitemap-site.xml e o /blog/sitemap.xml (gerado pelo Astro). É o único endereço
  enviado ao Search Console e ao Bing. Sem lastmod: no índice ele seria a data do arquivo do sitemap, e o maior
  lastmod das páginas não muda quando uma página sai ou a regra de seleção muda.

Rodar depois do scripts/assemble_site.py, num clone com histórico completo (fetch-depth: 0):
    python3 scripts/build_sitemaps.py [--repo .] [--site _site]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

SITE_ORIGIN = "https://guifav.dev"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
SITE_SITEMAP = "sitemap-site.xml"
BLOG_SITEMAP = "blog/sitemap.xml"
INDEX = "sitemap.xml"
HOME = "index.html"
HOME_LATEST = re.compile(r"<!-- ultimos-artigos -->(.*?)<!-- /ultimos-artigos -->", re.S)
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
# Data dos dados de uma página gerada (scripts/build_llm_ranking.py), no formato AAAA-MM-DD.
DATA_META = "dados-publicados-em"
_DATA_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SitemapError(Exception):
    """Não dá para gerar um sitemap confiável: o build para."""


class _Head(HTMLParser):
    """Só o que decide se a página entra: meta robots e link canonical."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.robots: list[str] = []
        self.canonicals: list[str] = []
        self.data_dates: list[str] = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        if tag == "meta" and a.get("name", "").lower() == "robots":
            self.robots.append(a.get("content", "").lower())
        if tag == "meta" and a.get("name", "").lower() == DATA_META:
            self.data_dates.append(a.get("content", "").strip())
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


def _robots_pattern_matches(pattern: str, path: str) -> bool:
    """Casamento de padrão robots.txt (RFC 9309): prefixo com suporte a '*' e '$' final."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "^" + ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else "")
    return re.search(regex, path) is not None


def robots_blocks(robots_txt: str, path: str, agent: str = "*") -> bool:
    """True se robots.txt bloqueia `path` para `agent`, seguindo a RFC 9309.

    Agente específico ignora o grupo '*' quando há grupo próprio; grupos repetidos do mesmo
    agente se combinam; vence a regra de maior comprimento e, em empate, Allow vence Disallow.
    """
    groups: list[tuple[set[str], list[tuple[str, str]]]] = []
    cur_agents: set[str] = set()
    cur_rules: list[tuple[str, str]] = []
    seen_rule = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if seen_rule:
                groups.append((cur_agents, cur_rules))
                cur_agents, cur_rules, seen_rule = set(), [], False
            if val:
                cur_agents.add(val.lower())
        elif key in ("allow", "disallow"):
            if cur_agents:
                seen_rule = True
                if val:
                    cur_rules.append((key, val))
    if cur_agents:
        groups.append((cur_agents, cur_rules))

    target = agent.lower()
    rules: list[tuple[str, str]] = []
    if target != "*":
        for agents, grules in groups:
            if target in agents:
                rules.extend(grules)
    if not rules and (target == "*" or not any(target in agents for agents, _ in groups)):
        for agents, grules in groups:
            if "*" in agents:
                rules.extend(grules)

    best_len = -1
    best_allow = True
    for kind, pattern in rules:
        if not _robots_pattern_matches(pattern, path):
            continue
        plen = len(pattern)
        is_allow = kind == "allow"
        if plen > best_len or (plen == best_len and is_allow and not best_allow):
            best_len = plen
            best_allow = is_allow
    return best_len >= 0 and not best_allow


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
    robots_txt = robots.read_text(encoding="utf-8") if robots.is_file() else ""
    out = []
    for f in sorted(site.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in HTML_SUFFIXES:
            continue
        rel = f.relative_to(site).as_posix()
        if rel.startswith("blog/") or rel == "404.html":
            continue
        url = page_url(rel)
        if robots_blocks(robots_txt, url):
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


def data_lastmod(site: Path, rel: str) -> str | None:
    """A data dos dados declarada na página (meta DATA_META), como meia-noite UTC, ou None se ela não declara. Mais de uma, data fora do
    formato ou inválida param o build: um lastmod errado faria o IndexNow avisar à toa ou nunca."""
    h = _Head()
    h.feed((site / rel).read_text(encoding="utf-8"))
    h.close()
    if not h.data_dates:
        return None
    if len(h.data_dates) != 1:
        raise SitemapError(f"{rel}: {len(h.data_dates)} metas {DATA_META}, esperado uma")
    d = h.data_dates[0]
    if not _DATA_RE.fullmatch(d):
        raise SitemapError(f"{rel}: {DATA_META} fora do formato AAAA-MM-DD: {d!r}")
    try:
        date.fromisoformat(d)
    except ValueError as e:
        raise SitemapError(f"{rel}: {DATA_META} inválida: {d!r}") from e
    # O sitemap da raiz usa data, hora e fuso (check_site.check_root_sitemaps): a data da fonte vira meia-noite UTC.
    return f"{d}T00:00:00+00:00"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def urlset(entries: list[tuple[str, str]]) -> str:
    body = "\n".join(f"  <url><loc>{_esc(loc)}</loc><lastmod>{lm}</lastmod></url>" for loc, lm in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="{NS}">\n{body}\n</urlset>\n'


def sitemapindex(locs: list[str]) -> str:
    body = "\n".join(f"  <sitemap><loc>{_esc(loc)}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="{NS}">\n{body}\n</sitemapindex>\n'


def _when(lastmod: str) -> datetime:
    try:
        d = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
    except ValueError as e:
        raise SitemapError(f"lastmod ilegível: {lastmod!r}") from e
    if d.tzinfo is None:
        raise SitemapError(f"lastmod sem fuso: {lastmod!r}")
    return d


def home_lastmod(site: Path, git_date: str, blog_lastmods: dict[str, str]) -> str:
    """O mais recente entre o commit da home e o lastmod de cada artigo listado nela (sem a lista, o commit)."""
    m = HOME_LATEST.search((site / HOME).read_text(encoding="utf-8"))
    if not m:
        return git_date
    inner = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    paths = re.findall(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']*)["']""", inner, flags=re.I)
    dates = [git_date]
    for path in paths:
        lm = blog_lastmods.get(SITE_ORIGIN + path)
        if not lm:
            raise SitemapError(f"{HOME}: o artigo listado {path} não tem lastmod no {BLOG_SITEMAP}")
        dates.append(lm)
    return max(dates, key=_when)


def build(repo: Path, site: Path) -> tuple[int, int]:
    """Grava os dois arquivos. Devolve (páginas do site, URLs do blog)."""
    for name in (INDEX, SITE_SITEMAP):
        if (site / name).exists():
            raise SitemapError(f"_site/{name} já existe: ele é gerado aqui, tire-o do site-manifest.txt")
    try:
        blog = ET.parse(site / BLOG_SITEMAP).getroot()
    except (ET.ParseError, FileNotFoundError) as e:
        raise SitemapError(f"{BLOG_SITEMAP} inválido: {e}") from e
    if blog.tag != f"{{{NS}}}urlset":
        raise SitemapError(f"{BLOG_SITEMAP} não é um urlset")
    blog_locs = list(blog.iter(f"{{{NS}}}loc"))
    blog_lastmods = {(u.findtext(f"{{{NS}}}loc") or "").strip(): (u.findtext(f"{{{NS}}}lastmod") or "").strip()
                     for u in blog.iter(f"{{{NS}}}url")}
    pages = []
    for rel, url in indexable_pages(site):
        # #113: página gerada a partir de dados (o /ferramentas/llm-ranking/) leva a data dos dados; as outras, o último commit.
        lm = data_lastmod(site, rel) or git_lastmod(repo, rel)
        pages.append((SITE_ORIGIN + url, home_lastmod(site, lm, blog_lastmods) if rel == HOME else lm))
    if not pages:
        raise SitemapError("nenhuma página do site para o sitemap: algo está errado no _site")
    (site / SITE_SITEMAP).write_text(urlset(pages), encoding="utf-8")
    (site / INDEX).write_text(sitemapindex([f"{SITE_ORIGIN}/{SITE_SITEMAP}", f"{SITE_ORIGIN}/{BLOG_SITEMAP}"]),
                              encoding="utf-8")
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
