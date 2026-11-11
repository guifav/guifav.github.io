"""Verifica o site montado em _site/ antes do deploy.

- Todo link interno (href, src, srcset, poster, inclusive URLs absolutas de guifav.dev) aponta para
  um arquivo publicado. Pega base errado no blog e links para pastas fora do site
  (docs/, _next/, tools/).
- Antes do cutover (PRE_CUTOVER_NOINDEX em blog-src/src/consts.ts): toda página do blog
  tem meta robots com noindex, e nenhuma página fora do blog aponta para ele.
- rss.xml e sitemap.xml do blog são XML válido; o sitemap só lista páginas que existem e
  não lista posts com index: false. Depois do cutover, esses posts precisam de noindex.
- Sob /blog/ só há .html, .xml e mídia da lista.
- Alarme secundário de conteúdo ativo nas páginas do blog (ver blog-src/CONTRATO-DE-CONTEUDO.md:
  a barreira de segurança é a limpeza no render e a lista de extensões, não este verificador).

Toda URL é resolvida pelo parser WHATWG do Node (scripts/resolve_urls.mjs), em lote. Sem Node,
o verificador falha fechado.

Uso:
    python3 scripts/check_site.py [--site _site] [--posts blog-src/src/content/posts]
                                  [--consts blog-src/src/consts.ts]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

SITE_HOST = "guifav.dev"
BLOG_PREFIX = "/blog/"
SITE_ORIGIN = "https://" + SITE_HOST
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
RESOLVER = Path(__file__).resolve().parent / "resolve_urls.mjs"
# Atributos cujo alvo precisa existir quando o link é interno.
URL_ATTRS = {"href", "src", "poster"}
ACTIVE_PROTOCOLS = {"javascript:", "vbscript:", "data:"}
ACTIVE_URL_ATTRS = {"href", "src", "srcset", "data", "action", "formaction", "poster", "xlink:href", "cite", "background"}
# Nunca aparecem numa página do blog (nem no template).
FORBIDDEN_TAGS = {"object", "embed", "applet", "base", "frame", "frameset"}
# O único formulário do blog é o de inscrição da newsletter (#21), que vem do template: marcado com
# data-site-form, POST e com destino no Worker. Fora disso, e dentro do conteúdo do post, é alarme.
SITE_FORM_ATTR = "data-site-form"
SITE_FORM_ACTIONS = {"https://newsletter.guifav.dev/subscribe"}
IFRAME_HOSTS = {
    "open.spotify.com", "creators.spotify.com", "podcasters.spotify.com",
    "www.youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
}
# Os scripts do template levam este atributo; script sem ele veio do conteúdo.
SITE_SCRIPT_ATTR = "data-site-script"
# Sob /blog/ (contrato, barreira 2): páginas, RSS e sitemap, e mídia.
BLOG_EXACT_SUFFIXES = {".html", ".xml"}
BLOG_MEDIA_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif", ".mp4", ".webm"}


class ResolverError(Exception):
    """Node ou o resolvedor de URL não rodou: o verificador falha fechado."""


def resolve_urls(pairs: list[tuple[str, str]]) -> list[dict | None]:
    """Resolve [base, ref] pelo URL do Node (WHATWG). Mesma ordem; None para URL inválida."""
    if not pairs:
        return []
    node = os.environ.get("CHECK_SITE_NODE") or shutil.which("node")
    if not node:
        raise ResolverError("node não encontrado no PATH")
    try:
        r = subprocess.run([node, str(RESOLVER)], input=json.dumps(pairs), capture_output=True,
                           text=True, timeout=120, check=False)
    except OSError as e:
        raise ResolverError(f"não foi possível rodar {node}: {e}") from e
    if r.returncode != 0:
        raise ResolverError(f"resolve_urls.mjs saiu com {r.returncode}: {r.stderr.strip()[:300]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ResolverError(f"resolve_urls.mjs devolveu algo que não é JSON: {r.stdout[:120]!r}") from e
    if not isinstance(out, list) or len(out) != len(pairs):
        raise ResolverError("resolve_urls.mjs devolveu uma lista de tamanho diferente")
    for item in out:
        # Cada item é null (URL inválida) ou {protocol, hostname, pathname}, todos strings.
        if item is None:
            continue
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("protocol", "hostname", "pathname")):
            raise ResolverError(f"resolve_urls.mjs devolveu um item malformado: {item!r}"[:200])
    return out


def _internal(res: dict | None) -> str | None:
    """Caminho no disco (sem percent-encoding) se a URL resolvida for deste site; senão None."""
    if not res or res["protocol"] not in ("http:", "https:"):
        return None
    if res["hostname"].rstrip(".") != SITE_HOST:
        return None
    return unquote(res["pathname"]) or "/"


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
    """Lê a página inteira: links, meta robots e conteúdo ativo (sem recortar por texto).

    URLs só são coletadas aqui; a interpretação é do resolvedor WHATWG, depois.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []
        self.robots: list[str] = []
        self.active: list[str] = []
        # (descrição, url) de atributos que podem carregar esquema ativo, todas as ocorrências.
        self.url_values: list[tuple[str, str]] = []
        self.iframes: list[str] = []
        self.main_depth = 0
        self.divs: list[bool] = []  # pilha de <div>: True para o div.post-body (conteúdo do post)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.main_depth = max(0, self.main_depth - 1)
        elif tag == "div" and self.divs:
            self.divs.pop()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self.main_depth += 1
        # Atributo repetido: o navegador usa a primeira ocorrência.
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        if tag == "meta" and a.get("name", "").lower() == "robots":
            self.robots.append(a.get("content", "").lower())
        for k in URL_ATTRS:
            if k in a and a[k].strip():
                self.refs.append(a[k].strip())
        if "srcset" in a:
            self.refs.extend(parse_srcset(a["srcset"]))
        if tag == "iframe":
            self.iframes.append(a.get("src", "").strip())
        # Conteúdo ativo
        if tag == "div":
            self.divs.append("post-body" in a.get("class", "").split())
        if tag in FORBIDDEN_TAGS:
            self.active.append(f"<{tag}>")
        if tag == "form" and not (
            SITE_FORM_ATTR in a and a.get("method", "").lower() == "post" and a.get("action", "") in SITE_FORM_ACTIONS
            and "formaction" not in a and not any(self.divs)
        ):
            self.active.append("<form>")
        if tag == "script" and SITE_SCRIPT_ATTR not in a:
            self.active.append("<script> fora do template")
        elif tag == "script" and self.main_depth > 0:
            # O template não tem script dentro de <main>; ali só há conteúdo.
            self.active.append("<script> dentro de <main>")
        # Todas as ocorrências, inclusive repetidas: qualquer uma ativa é problema.
        for k, v in ((k, v or "") for k, v in attrs):
            if k.startswith("on"):
                self.active.append(f"<{tag} {k}>")
            elif k == "srcdoc":
                self.active.append(f"<{tag} srcdoc>")
            elif k == "formaction":
                self.active.append(f"<{tag} formaction>")
            elif k in ACTIVE_URL_ATTRS:
                for u in (parse_srcset(v) if k == "srcset" else [v]):
                    if u.strip():
                        self.url_values.append((f"<{tag} {k}={v[:40]!r}>", u.strip()))


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


# O Pages serve estas extensões como HTML (com script), em qualquer caixa.
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


def html_files(site: Path) -> list[Path]:
    return sorted(p for p in site.rglob("*") if p.is_file() and p.suffix.lower() in HTML_SUFFIXES)


def _page_url(site: Path, f: Path) -> str:
    rel = f.relative_to(site).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


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

    # Contrato, barreira 2: sob /blog/ só páginas, RSS, sitemap e mídia da lista.
    for f in sorted((site / "blog").rglob("*")):
        if f.is_file() and f.suffix not in BLOG_EXACT_SUFFIXES and f.suffix.lower() not in BLOG_MEDIA_SUFFIXES:
            problems.append(f"{f.relative_to(site).as_posix()}: extensão fora da lista do blog "
                            f"(só .html, .xml e mídia {', '.join(sorted(BLOG_MEDIA_SUFFIXES))})")

    pages = []
    for f in html_files(site):
        p = _Page()
        p.feed(f.read_text(encoding="utf-8"))
        p.close()
        url = _page_url(site, f)
        pages.append((f.relative_to(site).as_posix(), url, url.startswith(BLOG_PREFIX), p))

    sm = site / "blog" / "sitemap.xml"
    sitemap_locs: list[str] | None = None
    try:
        sitemap_locs = [(loc.text or "").strip() for loc in ET.parse(sm).getroot().iter(f"{SITEMAP_NS}loc")]
    except (ET.ParseError, FileNotFoundError) as e:
        problems.append(f"blog/sitemap.xml inválido: {e}")

    # Uma única interpretação de URL para tudo: links, valores ativos, iframes e sitemap.
    pairs: list[tuple[str, str]] = []
    for _, url, _, p in pages:
        base = SITE_ORIGIN + url
        pairs += [(base, r) for r in p.refs] + [(base, u) for _, u in p.url_values] + [(base, i) for i in p.iframes]
    pairs += [(SITE_ORIGIN + "/", loc) for loc in sitemap_locs or []]
    unique = list(dict.fromkeys(pairs))  # sem repetição, mantendo a ordem
    resolved = dict(zip(unique, resolve_urls(unique)))

    for rel, url, in_blog, p in pages:
        base = SITE_ORIGIN + url
        if in_blog:
            active = list(p.active)
            active += [desc for desc, u in p.url_values if (resolved[(base, u)] or {}).get("protocol") in ACTIVE_PROTOCOLS]
            for src in p.iframes:
                res = resolved[(base, src)]
                if not res or res["protocol"] != "https:" or res["hostname"] not in IFRAME_HOSTS:
                    active.append(f"<iframe src={src[:60]!r}>")
            for bad in dict.fromkeys(active):
                problems.append(f"{rel}: conteúdo ativo: {bad}")
        has_noindex = any(_robots_noindex(r) for r in p.robots)
        if in_blog and pre_cutover and not has_noindex:
            problems.append(f"{rel}: página do blog sem noindex antes do cutover")
        if in_blog and not pre_cutover:
            parts = url[len(BLOG_PREFIX):].strip("/").split("/")
            if len(parts) == 1 and parts[0] in noindex_slugs and not has_noindex:
                problems.append(f"{rel}: post com index: false sem noindex")
        for ref in p.refs:
            path = _internal(resolved[(base, ref)])
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

    for href in sitemap_locs or []:
        res = resolved[(SITE_ORIGIN + "/", href)]
        path = _internal(res)
        if not res or res["protocol"] != "https:" or path is None or not path.startswith(BLOG_PREFIX):
            problems.append(f"sitemap: URL fora do blog: {href}")
            continue
        ok, _ = _resolve(site, path)
        if not ok:
            problems.append(f"sitemap: página inexistente: {href}")
        if path[len(BLOG_PREFIX):].strip("/") in noindex_slugs:
            problems.append(f"sitemap: lista post com index: false: {href}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verifica o site montado em _site/ antes do deploy.")
    ap.add_argument("--site", default="_site")
    ap.add_argument("--posts", default="blog-src/src/content/posts")
    ap.add_argument("--consts", default="blog-src/src/consts.ts")
    args = ap.parse_args(argv)
    pre = read_pre_cutover(Path(args.consts))
    try:
        problems = check(Path(args.site), Path(args.posts), pre_cutover=pre)
    except ResolverError as e:
        print(f"ERRO: resolvedor de URL indisponível, verificação não feita: {e}", file=sys.stderr)
        return 1
    for p in problems:
        print(f"ERRO: {p}", file=sys.stderr)
    if problems:
        return 1
    n = len(html_files(Path(args.site)))
    print(f"check_site: {n} páginas HTML conferidas, sem problemas (antes do cutover: {pre})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
