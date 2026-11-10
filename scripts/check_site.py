"""Verifica o site montado em _site/ antes do deploy.

- Todo link interno (href, src, srcset, inclusive URLs absolutas de guifav.dev) aponta para
  um arquivo publicado. Pega base errado no blog e links para pastas fora do site
  (docs/, _next/, tools/).
- Antes do cutover (PRE_CUTOVER_NOINDEX em blog-src/src/consts.ts): toda página do blog
  tem meta robots com noindex, e nenhuma página fora do blog aponta para ele.
- rss.xml e sitemap.xml do blog são XML válido; o sitemap só lista páginas que existem e
  não lista posts com index: false. Depois do cutover, esses posts precisam de noindex.

Uso:
    python3 scripts/check_site.py [--site _site] [--posts blog-src/src/content/posts]
                                  [--consts blog-src/src/consts.ts]
"""
from __future__ import annotations

import argparse
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

SITE_HOST = "guifav.dev"
BLOG_PREFIX = "/blog/"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
URL_ATTRS = {"href", "src"}
ACTIVE_SCHEMES = ("javascript:", "vbscript:", "data:")
ACTIVE_URL_ATTRS = {"href", "src", "srcset", "data", "action", "formaction", "poster", "xlink:href", "cite", "background"}
# Nunca aparecem numa página do blog (nem no template).
FORBIDDEN_TAGS = {"object", "embed", "applet", "form", "base", "frame", "frameset"}
IFRAME_HOSTS = {
    "open.spotify.com", "creators.spotify.com", "podcasters.spotify.com",
    "www.youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
}
# Os scripts do template levam este atributo; script sem ele veio do conteúdo.
SITE_SCRIPT_ATTR = "data-site-script"


def _scheme(url: str) -> str:
    return re.sub(r"[\s\x00-\x1f\x7f]", "", url).lower()


def parse_srcset(value: str) -> list[str]:
    """URLs de um srcset, pela regra do HTML: a URL vai até um espaço; vírgula no fim da URL encerra o candidato."""
    urls: list[str] = []
    i, n = 0, len(value)
    ws = " \t\n\f\r"
    while i < n:
        while i < n and (value[i] in ws or value[i] == ","):
            i += 1
        if i >= n:
            break
        j = i
        while j < n and value[j] not in ws:
            j += 1
        url = value[i:j]
        i = j
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            depth = 0
            while i < n:
                c = value[i]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth = max(0, depth - 1)
                elif c == "," and depth == 0:
                    break
                i += 1
        if url:
            urls.append(url)
    return urls


class _Page(HTMLParser):
    """Lê a página inteira: links, meta robots e conteúdo ativo (sem recortar por texto)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []
        self.robots: list[str] = []
        self.active: list[str] = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name", "").lower() == "robots":
            self.robots.append(a.get("content", "").lower())
        for k in URL_ATTRS:
            if k in a and a[k].strip():
                self.refs.append(a[k].strip())
        if "srcset" in a:
            self.refs.extend(parse_srcset(a["srcset"]))
        # Conteúdo ativo
        if tag in FORBIDDEN_TAGS:
            self.active.append(f"<{tag}>")
        if tag == "script" and SITE_SCRIPT_ATTR not in a:
            self.active.append("<script> fora do template")
        for k, v in a.items():
            if k.startswith("on"):
                self.active.append(f"<{tag} {k}>")
            elif k == "srcdoc":
                self.active.append(f"<{tag} srcdoc>")
            elif k in ACTIVE_URL_ATTRS:
                urls = parse_srcset(v) if k == "srcset" else [v]
                if any(_scheme(u).startswith(ACTIVE_SCHEMES) for u in urls):
                    self.active.append(f"<{tag} {k}={v[:40]!r}>")
        if tag == "iframe":
            u = urlsplit(a.get("src", ""))
            if u.scheme != "https" or _host(u) not in IFRAME_HOSTS:
                self.active.append(f"<iframe src={a.get('src', '')[:60]!r}>")


def frontmatter(text: str) -> dict[str, str]:
    """Campos simples (chave: valor) do bloco --- inicial de um markdown."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km:
            out[km.group(1)] = _yaml_scalar(km.group(2))
    return out


def _yaml_scalar(raw: str) -> str:
    """Valor escalar simples: entre aspas, ou sem aspas e sem o comentário ' #...'."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end > 0:
            return raw[1:end]
    return re.sub(r"\s+#.*$", "", raw).strip()


def read_pre_cutover(consts: Path) -> bool:
    m = re.search(r"export const PRE_CUTOVER_NOINDEX\s*=\s*(true|false)\s*;", consts.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"PRE_CUTOVER_NOINDEX não encontrado em {consts}")
    return m.group(1) == "true"


def _robots_noindex(content: str) -> bool:
    directives = {d.strip() for d in content.lower().split(",")}
    return bool(directives & {"noindex", "none"})


def _page_url(site: Path, f: Path) -> str:
    rel = f.relative_to(site).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def _normalize(path: str) -> str:
    """Remove segmentos . e .. como o navegador faz (WHATWG), mantendo a barra final.

    Um último segmento "." ou ".." (também codificado, %2e) aponta para a pasta: "/a/." é "/a/".
    """
    path = unquote(path) or "/"
    last = path.rsplit("/", 1)[-1]
    dir_like = path.endswith("/") or last in (".", "..")
    norm = posixpath.normpath(path)
    if norm.startswith("//"):
        norm = "/" + norm.lstrip("/")
    if dir_like and norm != "/":
        norm += "/"
    return norm


def _host(parts) -> str:
    """Host como o navegador compara: sem percent-encoding, minúsculo, sem ponto final."""
    return unquote(parts.netloc.rsplit("@", 1)[-1].split(":", 1)[0]).lower().rstrip(".")


def _internal_path(page_url: str, ref: str) -> str | None:
    """Caminho interno normalizado do link, ou None se for externo, âncora, mailto etc."""
    if ref.startswith("#"):
        return None
    # Resolve como o navegador: relativo, absoluto, relativo ao protocolo (//host/...).
    parts = urlsplit(urljoin("https://" + SITE_HOST + page_url, ref))
    if parts.scheme not in ("http", "https") or _host(parts) != SITE_HOST:
        return None
    return _normalize(parts.path)


def _resolve(site: Path, path: str) -> tuple[bool, bool]:
    """(existe, existe_só_como_pasta_sem_barra)."""
    target = site / path.lstrip("/")
    if path.endswith("/"):
        return (target / "index.html").is_file(), False
    if target.is_file():
        return True, False
    if (target / "index.html").is_file():
        return False, True
    return False, False


def check(site: Path, posts: Path, pre_cutover: bool) -> list[str]:
    problems: list[str] = []
    noindex_slugs: set[str] = set()
    seen: dict[str, str] = {}
    for md in sorted(posts.glob("**/*.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug") or md.stem
        rel_md = md.relative_to(posts).as_posix()
        if slug in seen:
            problems.append(f"posts: slug duplicado {slug!r}: {seen[slug]}, {rel_md}")
        seen[slug] = rel_md
        if not (site / "blog" / slug / "index.html").is_file():
            problems.append(f"posts: {rel_md} sem página publicada em /blog/{slug}/")
        if fm.get("index") == "false":
            noindex_slugs.add(slug)

    for f in sorted(site.rglob("*.html")):
        rel = f.relative_to(site).as_posix()
        url = _page_url(site, f)
        in_blog = url.startswith(BLOG_PREFIX)
        html = f.read_text(encoding="utf-8")
        p = _Page()
        p.feed(html)
        p.close()
        if in_blog:
            for bad in dict.fromkeys(p.active):
                problems.append(f"{rel}: conteúdo ativo: {bad}")
        has_noindex = any(_robots_noindex(r) for r in p.robots)
        if in_blog and pre_cutover and not has_noindex:
            problems.append(f"{rel}: página do blog sem noindex antes do cutover")
        if in_blog and not pre_cutover:
            parts = url[len(BLOG_PREFIX):].strip("/").split("/")
            if len(parts) == 1 and parts[0] in noindex_slugs and not has_noindex:
                problems.append(f"{rel}: post com index: false sem noindex")
        for ref in p.refs:
            if _scheme(ref).startswith(ACTIVE_SCHEMES):
                continue
            path = _internal_path(url, ref)
            if path is None:
                continue
            if pre_cutover and not in_blog and (path == "/blog" or path.startswith(BLOG_PREFIX)):
                problems.append(f"{rel}: aponta para o blog antes do cutover: {ref}")
                continue
            ok, dir_without_slash = _resolve(site, path)
            if dir_without_slash:
                problems.append(f"{rel}: link para pasta sem barra final: {ref}")
            elif not ok:
                problems.append(f"{rel}: link interno quebrado: {ref}")

    rss = site / "blog" / "rss.xml"
    try:
        ET.parse(rss)
    except (ET.ParseError, FileNotFoundError) as e:
        problems.append(f"blog/rss.xml inválido: {e}")

    sm = site / "blog" / "sitemap.xml"
    try:
        root = ET.parse(sm).getroot()
    except (ET.ParseError, FileNotFoundError) as e:
        problems.append(f"blog/sitemap.xml inválido: {e}")
    else:
        for loc in root.iter(f"{SITEMAP_NS}loc"):
            href = (loc.text or "").strip()
            u = urlsplit(href)
            if u.scheme != "https" or u.hostname != SITE_HOST or not u.path.startswith(BLOG_PREFIX):
                problems.append(f"sitemap: URL fora do blog: {href}")
                continue
            ok, _ = _resolve(site, _normalize(u.path))
            if not ok:
                problems.append(f"sitemap: página inexistente: {href}")
            slug = u.path[len(BLOG_PREFIX):].strip("/")
            if slug in noindex_slugs:
                problems.append(f"sitemap: lista post com index: false: {href}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verifica o site montado em _site/ antes do deploy.")
    ap.add_argument("--site", default="_site")
    ap.add_argument("--posts", default="blog-src/src/content/posts")
    ap.add_argument("--consts", default="blog-src/src/consts.ts")
    args = ap.parse_args(argv)
    pre = read_pre_cutover(Path(args.consts))
    problems = check(Path(args.site), Path(args.posts), pre_cutover=pre)
    for p in problems:
        print(f"ERRO: {p}", file=sys.stderr)
    if problems:
        return 1
    n = sum(1 for _ in Path(args.site).rglob("*.html"))
    print(f"check_site: {n} páginas HTML conferidas, sem problemas (antes do cutover: {pre})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
