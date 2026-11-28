"""Verifica o site montado em _site/ antes do deploy.

- Todo link interno (href, src, srcset, poster, inclusive URLs absolutas de guifav.dev) aponta para
  um arquivo publicado. Pega base errado no blog e links para pastas fora do site
  (docs/, _next/, _mockups/).
- Antes do cutover (PRE_CUTOVER_NOINDEX em blog-src/src/consts.ts): toda página do blog
  tem meta robots com noindex, e nenhuma página fora do blog aponta para ele.
- rss.xml e sitemap.xml do blog são XML válido; o sitemap só lista páginas que existem e
  não lista posts com index: false nem as páginas da newsletter. Depois do cutover, esses
  posts e as páginas da newsletter continuam precisando de noindex.
- Sob /blog/ só há .html, .xml, mídia da lista e o catálogo de posts (blog/catalogo.json, #193).
- Ferramentas e guias (#104, #151, #166): a home (seção e rodapé) e o rodapé do blog levam os links das ferramentas e do /ferramentas/, e /ferramentas/ lista as 4 ferramentas com CollectionPage.
- /ferramentas/llm-ranking/ (#113, #166): a página sai com a tabela gerada no build, a data dos dados, o JSON-LD e a atribuição.
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
import colorsys
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sitemaps import canonical_is_self, robots_blocks, robots_disallow  # noqa: E402

SITE_HOST = "guifav.dev"
# Domínio antigo do Substack (#24): depois do cutover, nada no site aponta para ele.
OLD_DOMAIN = "guilhermefavaron.com.br"
OLD_HOSTS = {OLD_DOMAIN, "www." + OLD_DOMAIN}
BLOG_PREFIX = "/blog/"
# Toda página com o GA4 do site também carrega o Microsoft Clarity (mesmo escopo de medição).
GA4_TAG = "googletagmanager.com/gtag/js?id=G-K7Y87QNCX8"
CLARITY_MARKERS = ('"https://www.clarity.ms/tag/"+i', "zcs8f7r329")
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
SITE_FORM_ACTIONS = {"https://insights.guifav.dev/subscribe"}
# Páginas de tema (#104), /blog/tema/<id>/: indexáveis, com canonical próprio e no sitemap do blog.
TOPIC_PAGE = re.compile(r"blog/tema/[a-z0-9][a-z0-9-]*/index\.html")
# Páginas da newsletter (#21): noindex e fora do sitemap sempre, também depois do cutover (A10).
BLOG_PRIVATE_PAGES = frozenset({"assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida"})
IFRAME_HOSTS = {
    "open.spotify.com", "creators.spotify.com", "podcasters.spotify.com",
    "www.youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
}
# Os scripts do template levam este atributo; script sem ele veio do conteúdo.
SITE_SCRIPT_ATTR = "data-site-script"
# Sob /blog/ (contrato, barreira 2): páginas, RSS e sitemap, mídia e a única exceção JSON abaixo.
BLOG_EXACT_SUFFIXES = {".html", ".xml"}
BLOG_MEDIA_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif", ".mp4", ".webm"}
# Única exceção fora dessas extensões: gerado pelo template blog-src/src/pages/catalogo.json.ts (issue #193).
BLOG_CATALOG_PATH = "blog/catalogo.json"
# Rotas sob guifav.dev servidas por Workers na Cloudflare, fora do _site/ do GitHub Pages (#146, #166).
WORKER_PATHS = frozenset({"/cursos/chatgpt-no-dia-a-dia"})


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


def _old_host(res: dict | None) -> bool:
    """A URL resolvida aponta para o domínio antigo do Substack (com ou sem www e ponto final DNS)?"""
    return bool(res) and (res.get("hostname") or "").rstrip(".") in OLD_HOSTS


def _internal(res: dict | None) -> str | None:
    """Caminho no disco (sem percent-encoding) se a URL resolvida for deste site; senão None."""
    if not res or res["protocol"] not in ("http:", "https:"):
        return None
    if res["hostname"].rstrip(".") != SITE_HOST:
        return None
    return unquote(res["pathname"]) or "/"


def refresh_url(content: str) -> str | None:
    """URL de um <meta http-equiv="refresh">, pelos passos do HTML (shared declarative refresh steps): o tempo,
    depois ";", "," ou espaço, e a URL com ou sem "url=" e aspas. None se não há URL ou o conteúdo é inválido."""
    ws, i, n = " \t\n\f\r", 0, len(content)
    while i < n and content[i] in ws:
        i += 1
    j = i
    while i < n and content[i].isascii() and content[i].isdigit():
        i += 1
    if i == j and not (i < n and content[i] == "."):
        return None
    while i < n and (content[i] == "." or (content[i].isascii() and content[i].isdigit())):
        i += 1
    if i >= n:
        return None
    if content[i] not in ";," + ws:
        return None
    while i < n and content[i] in ws:
        i += 1
    if i < n and content[i] in ";,":
        i += 1
    while i < n and content[i] in ws:
        i += 1
    if i >= n:
        return None
    url = content[i:]
    k = i
    if content[k:k + 1].lower() == "u":
        k += 1
        if content[k:k + 1].lower() == "r":
            k += 1
            if content[k:k + 1].lower() == "l":
                k += 1
                while k < n and content[k] in ws:
                    k += 1
                if content[k:k + 1] == "=":
                    k += 1
                    while k < n and content[k] in ws:
                        k += 1
                    i = k
                    quote = content[i] if i < n and content[i] in "'\"" else ""
                    url = content[i + len(quote):]
                    if quote and quote in url:
                        url = url[:url.index(quote)]
    elif content[k:k + 1] in ("'", '"'):
        quote = content[k]
        url = content[k + 1:]
        if quote in url:
            url = url[:url.index(quote)]
    url = url.strip(ws)
    return url or None


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
        self.canonicals: list[str] = []
        self.main_depth = 0
        self.divs: list[bool] = []  # pilha de <div>: True para o div.post-body (conteúdo do post)
        self.post_body_links: list[str] = []  # hrefs de <a> dentro de div.post-body

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
        if tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonicals.append(a.get("href", "").strip())
        if tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            # O navegador navega para essa URL: conta como link (destino, blog antes do cutover).
            u = refresh_url(a.get("content", ""))
            if u:
                self.refs.append(u)
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
        if tag == "a" and any(self.divs):
            href = a.get("href", "").strip()
            if href:
                self.post_body_links.append(href)
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
                # Também é destino: fora do blog o conteúdo ativo não é barrado, mas o domínio antigo é (#50 r2).
                if v.strip():
                    self.url_values.append((f"<{tag} formaction={v[:40]!r}>", v.strip()))
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
    """Valor escalar simples: entre aspas (com escapes \\" e ''), ou sem aspas e sem o comentário ' #...'."""
    raw = raw.strip()
    if raw[:1] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                return "".join(out)
            out.append(ch)
            i += 1
    elif raw[:1] == "'":
        out = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out)
            out.append(ch)
            i += 1
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
    indexable_slugs: set[str] = set()
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
        else:
            indexable_slugs.add(slug)

    # Contrato, barreira 2: sob /blog/ só páginas, RSS, sitemap, mídia da lista e o catálogo de posts (#193).
    for f in sorted((site / "blog").rglob("*")):
        if not f.is_file():
            continue
        rel_blog = f.relative_to(site).as_posix()
        if rel_blog == BLOG_CATALOG_PATH:
            continue
        if f.suffix not in BLOG_EXACT_SUFFIXES and f.suffix.lower() not in BLOG_MEDIA_SUFFIXES:
            problems.append(f"{rel_blog}: extensão fora da lista do blog "
                            f"(só .html, .xml, mídia {', '.join(sorted(BLOG_MEDIA_SUFFIXES))} e {BLOG_CATALOG_PATH})")

    pages = []
    for f in html_files(site):
        p = _Page()
        text = f.read_text(encoding="utf-8")
        if GA4_TAG in text and not _has_clarity(text):
            problems.append(f"{f.relative_to(site).as_posix()}: tem o GA4 mas não o Microsoft Clarity")
        problems += check_titles_and_groups(f.relative_to(site).as_posix(), text)
        problems += check_tracking(f.relative_to(site).as_posix(), text)
        problems += check_ga_host_guard(f.relative_to(site).as_posix(), text)
        problems += check_consent(f.relative_to(site).as_posix(), text)
        problems += check_tools(f.relative_to(site).as_posix(), text)
        p.feed(text)
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

    topic_urls: list[str] = []
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
            if len(parts) == 1 and parts[0] in BLOG_PRIVATE_PAGES and not has_noindex:
                problems.append(f"{rel}: página da newsletter sem noindex")
        # Páginas de tema (#104): no sitemap (completude, abaixo), com canonical para si mesmas, sem link para
        # post fora do índice (#107) e, depois do cutover, indexáveis.
        if TOPIC_PAGE.fullmatch(rel):
            topic_urls.append(url)
            if not pre_cutover and has_noindex:
                problems.append(f"{rel}: página de tema com noindex")
            if not p.canonicals or not canonical_is_self(p.canonicals, url):
                problems.append(f"{rel}: página de tema sem canonical para si mesma: {p.canonicals}")
            bad_topic_posts: set[str] = set()
            for ref in p.refs:
                path = _internal(resolved[(base, ref)])
                if path and path.startswith(BLOG_PREFIX):
                    parts = path[len(BLOG_PREFIX):].strip("/").split("/")
                    if len(parts) == 1 and parts[0] in noindex_slugs and parts[0] not in bad_topic_posts:
                        bad_topic_posts.add(parts[0])
                        problems.append(f"{rel}: página de tema lista post com index: false: {parts[0]}")
        if not pre_cutover:
            # Fora do blog, qualquer menção literal conta (inclusive JSON-LD e texto): o código é do site.
            if not in_blog and OLD_DOMAIN in (site / rel).read_text(encoding="utf-8").lower():
                problems.append(f"{rel}: menciona o domínio antigo ({OLD_DOMAIN})")
            # Em todo o site, links e embeds pela URL resolvida: pega entidade HTML, percent-encoding e ponto final,
            # que a busca literal não vê. No blog, menção em texto corrido de post importado fica, mas todo link ou
            # embed para o domínio antigo é apontado, sem exceção (a lista de links mortos da importação saiu na #105).
            for u in dict.fromkeys(list(p.refs) + [v for _, v in p.url_values]):
                if _old_host(resolved[(base, u)]):
                    problems.append(f"{rel}: link para o domínio antigo: {u[:80]}")
        for ref in p.refs:
            path = _internal(resolved[(base, ref)])
            if path is None:
                continue
            if pre_cutover and not in_blog and (path == "/blog" or path.startswith(BLOG_PREFIX)):
                problems.append(f"{rel}: aponta para o blog antes do cutover: {ref}")
                continue
            if path in WORKER_PATHS:
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
        if path[len(BLOG_PREFIX):].strip("/") in BLOG_PRIVATE_PAGES:
            problems.append(f"sitemap: lista página da newsletter: {href}")
        if path.startswith(BLOG_PREFIX + "pagina/"):
            problems.append(f"sitemap: lista página de paginação: {href}")
    # Completude do blog: a capa, toda página de tema (#104) e todo post indexável, uma vez cada.
    if sitemap_locs is not None:
        want_blog = {SITE_ORIGIN + BLOG_PREFIX} | {f"{SITE_ORIGIN}{BLOG_PREFIX}{s}/" for s in indexable_slugs}
        want_blog |= {SITE_ORIGIN + u for u in topic_urls}
        for loc in sorted(want_blog - set(sitemap_locs)):
            problems.append(f"sitemap: falta página indexável do blog: {loc}")
        if len(sitemap_locs) != len(set(sitemap_locs)):
            problems.append("sitemap: URL repetida no sitemap do blog")
    problems += check_root_sitemaps(site, [(rel, url, p) for rel, url, in_blog, p in pages if not in_blog])
    return problems


_W3C_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)")


def _valid_datetime(s: str) -> bool:
    """Data e hora completas com fuso (a forma W3C que o gerador usa), num instante que existe."""
    if not _W3C_DATETIME.fullmatch(s):
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _live_scripts(html: str) -> list[tuple[str, str]]:
    """(atributos, corpo) dos <script> que rodam: fora de comentário, template/noscript e sem type de dado (JSON-LD)."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<(template|noscript)\b[^>]*>.*?</\1\s*>", "", html, flags=re.S | re.I)
    out = []
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, flags=re.S | re.I):
        m = re.search(r"""(?:^|\s)type\s*=\s*["']?([^"'\s>]+)""", attrs, flags=re.I)
        if m and m.group(1).lower() not in ("text/javascript", "module", "application/javascript"):
            continue
        out.append((attrs, body))
    return out


def _exec_scripts(html: str) -> list[str]:
    """Corpo dos <script> que rodam."""
    return [body for _, body in _live_scripts(html)]


def _has_clarity(html: str) -> bool:
    """O snippet do Clarity está num <script> que roda."""
    return any(all(k in body for k in CLARITY_MARKERS) for body in _exec_scripts(html))


# Títulos (Gui, 13/11/2026, PR #57): "Página | Guilherme Favaron"; a home leva o nome primeiro, "Guilherme Favaron | ...".
# Desde 23/11/2026 (#103) o post sai sem o sufixo: o Google já mostra o nome do site, que vem do WebSite da home.
# As telas de exemplo do portfólio (portfolio/mockups) aparecem dentro do portfólio e ficam fora da regra.
SITE_NAME = "Guilherme Favaron"
TITLE_SEP = " | "
TITLE_EXEMPT_PREFIXES = ("portfolio/mockups/",)
# Grupos de conteúdo do GA4 (content_group), um por tipo de página; espelha CONTENT_GROUPS do consts.ts do blog.
CONTENT_GROUPS = ("home", "portfolio", "insights-lista", "insights-artigo", "expert-insights", "materiais", "consultoria", "ferramentas", "legal", "cursos")
# Páginas dos materiais (issues #99 e #183): o formulário e a página de enviado de cada material do catálogo do Worker.
BOOK_ID = "livro-desbloqueando-a-inteligencia-artificial"
BOOK_URL = f"/materiais/{BOOK_ID}/"
BOOK_PAGE = f"materiais/{BOOK_ID}/index.html"
BOOK_ASIN = "B0D8H13BYN"
BOOK_AMAZON_URL = f"https://www.amazon.com.br/dp/{BOOK_ASIN}"
MATERIALS = {
    "planilha-custos-ia": "ferramentas/planilha-custos-ia",
    "mapa-decisao-ia": "ferramentas/mapa-decisao-ia",
    BOOK_ID: f"materiais/{BOOK_ID}",
}
MATERIAL_PAGES = {f"{path}/{p}index.html" for path in MATERIALS.values() for p in ("", "enviado/")}
# Ferramentas sem material para baixar (issues #113, #151 e #166): grupo próprio, para não misturar com o funil dos materiais.
TOOLS_INDEX_PAGE = "ferramentas/index.html"
LLM_RANKING_PAGE = "ferramentas/llm-ranking/index.html"
TOOLS_PAGES = {TOOLS_INDEX_PAGE, LLM_RANKING_PAGE}
# Página de cursos (#166): publicada com noindex e fora do sitemap enquanto o curso ChatGPT no dia a dia está em atualização.
COURSES_PAGE = "cursos/index.html"
COURSES_URL = "/cursos/"
COURSES_TITLE = "Cursos" + TITLE_SEP + SITE_NAME
CHATGPT_COURSE_URL = "/cursos/chatgpt-no-dia-a-dia"
# Link de YouTube nos rodapés (#166): todo link de YouTube dentro de <footer> aponta para @ia-para-leigos.
YOUTUBE_FOOTER_URL = "https://www.youtube.com/@ia-para-leigos"
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
# Handle antigo do YouTube (#105): substituído por @guifav e @ia-para-leigos, proibido em qualquer HTML do site.
OLD_YOUTUBE_HANDLE = "ia-aplicada-guilhermefavaron"
# Páginas legais na raiz (#146): Política de privacidade e Termos de uso, públicas, indexáveis e no sitemap.
LEGAL_PAGES = ("privacidade/index.html", "termos/index.html")
LEGAL_SPECS = (
    ("privacidade/index.html", "/privacidade/", "Política de privacidade" + TITLE_SEP + SITE_NAME, "Política de privacidade"),
    ("termos/index.html", "/termos/", "Termos de uso" + TITLE_SEP + SITE_NAME, "Termos de uso"),
)
PRIVACY_ANCHORS = ("cookies", "materiais", "origem", "prioridade", "cursos", "quem-processa")
LEGAL_FOOTER_HREFS = ("/privacidade/", "/termos/", "/privacidade/#cookies")
# Página de oposição ao heating (#197): noindex, fora do sitemap, formulário POST para insights.guifav.dev/prioridade.
PRIORITY_PAGE = "prioridade/index.html"
PRIORITY_URL = "/prioridade/"
PRIORITY_TITLE = "Não quero ser pontuado" + TITLE_SEP + SITE_NAME
PRIORITY_H1 = "Não quero ser pontuado"
PRIORITY_ACTION = "https://insights.guifav.dev/prioridade"
PRIORITY_FRAGMENTS = ("pronto", "erro-verificacao", "erro-email", "erro-limite", "erro")
# Turnstile (#197): espelha TURNSTILE_SITE_KEY de blog-src/src/consts.ts.
TURNSTILE_SITE_KEY = "0x4AAAAAAF8raRhCn4ciudgW"
TURNSTILE_SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js"
# Marcador de data pendente (#197): nenhuma página publicada pode sair com "[DATA]" literal.
DATE_PLACEHOLDER = "[DATA]"
EXPERT_INSIGHTS_PAGES = {f"blog/{p}/index.html" for p in
                         ("assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida")}


def expected_content_group(rel: str) -> str | None:
    if rel == "index.html":
        return "home"
    if rel == "portfolio/index.html":
        return "portfolio"
    if rel == "consultoria/index.html":
        return "consultoria"
    if rel in LEGAL_PAGES or rel == PRIORITY_PAGE:
        return "legal"
    if (rel == "blog/index.html" or re.fullmatch(r"blog/pagina/\d+/index\.html", rel)
            or re.fullmatch(r"blog/tema/[a-z0-9][a-z0-9-]*/index\.html", rel)):
        return "insights-lista"
    if rel in EXPERT_INSIGHTS_PAGES:
        return "expert-insights"
    if rel in MATERIAL_PAGES:
        return "materiais"
    if rel in TOOLS_PAGES:
        return "ferramentas"
    if rel == COURSES_PAGE:
        return "cursos"
    if re.fullmatch(r"blog/[a-z0-9][a-z0-9-]*/index\.html", rel):
        return "insights-artigo"
    return None


def _js_mask(js: str, keep_strings: bool = False) -> str:
    """Código JS com comentários (e, por padrão, strings) mascarados, preservando posições e novas linhas."""
    out = list(js)
    i, quote = 0, None
    while i < len(js):
        if quote:
            if js[i] != "\n" and not keep_strings:
                out[i] = " "
            if js[i] == "\\" and i + 1 < len(js):
                if js[i + 1] != "\n" and not keep_strings:
                    out[i + 1] = " "
                i += 2
                continue
            if js[i] == quote:
                quote = None
            i += 1
            continue
        if js.startswith(("//", "/*"), i):
            end = js.find("\n", i) if js.startswith("//", i) else js.find("*/", i + 2)
            end = len(js) if end < 0 else end + (0 if js.startswith("//", i) else 2)
            for j in range(i, end):
                if js[j] != "\n":
                    out[j] = " "
            i = end
            continue
        if js[i] in "'\"`":
            quote = js[i]
            if not keep_strings:
                out[i] = " "
        i += 1
    return "".join(out)


def _content_groups(html: str) -> list[str]:
    """Um valor por content_group de cada gtag('config', ...) ativo; configs sem grupo também contam."""
    found = []
    group_re = re.compile(r"""(['"]?)\bcontent_group\1\s*:\s*(?:(["'])(.*?)\2|([A-Za-z_$][\w$]*))""")
    for body in _exec_scripts(html):
        mask = _js_mask(body)
        code = _js_mask(body, keep_strings=True)  # sem comentários, com strings
        for call in re.finditer(r"\bgtag\s*\(", mask):
            if not re.match(r"""gtag\s*\(\s*(['"])config\1\s*,""", code[call.start():]):
                continue
            open_paren = mask.find("(", call.start(), call.end())
            depth, end = 0, None
            for i in range(open_paren, len(mask)):
                if mask[i] == "(":
                    depth += 1
                elif mask[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is None:
                found.append("<config sem fechamento>")
                continue
            args, depth, start = [], 0, open_paren + 1
            for i in range(start, end - 1):
                if mask[i] in "({[":
                    depth += 1
                elif mask[i] in ")}]":
                    depth -= 1
                elif mask[i] == "," and depth == 0:
                    args.append((start, i))
                    start = i + 1
            args.append((start, end - 1))
            call_groups = []
            if len(args) >= 3:
                a, b = args[2]
                third = code[a:b]
                for m in group_re.finditer(third):
                    colon = third.find(":", m.start(), m.end())
                    if mask[a + colon] != ":":
                        continue
                    # Só propriedade de primeiro nível do objeto do config: aninhada não é o content_group do GA4.
                    level = sum(1 if c in "({[" else -1 if c in ")}]" else 0 for c in mask[a:a + m.start()])
                    if level != 1:
                        continue
                    if m.group(4) is None:
                        call_groups.append(m.group(3))
                        continue
                    name = m.group(4)
                    assignment = None
                    assign_re = re.compile(r"(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1")
                    for candidate in assign_re.finditer(body):
                        equals = body.find("=", candidate.start(), candidate.end())
                        if mask[candidate.start()] != " " and mask[equals] == "=":
                            assignment = candidate
                            break
                    call_groups.append(assignment.group(2) if assignment else f"<{name} sem valor>")
            found.extend(call_groups or ["<config sem content_group>"])
    return found


def _visible_html(html: str) -> str:
    """HTML fora de comentários e containers cujo conteúdo não vira título/metadado da página."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<(template|noscript)\b[^>]*>.*?</\1\s*>", "", html, flags=re.S | re.I)
    return re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.S | re.I)


class _MetaTags(HTMLParser):
    """Atributos de cada <meta>, pelo parser de HTML: um ">" literal dentro das aspas não corta a tag (r1, agy P2)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            a: dict[str, str] = {}
            for k, v in attrs:
                a.setdefault(k, v or "")  # atributo repetido: vale o primeiro, como no navegador
            self.tags.append(a)

    handle_startendtag = handle_starttag


def _meta_contents(html: str, prop: str, *, attrs: tuple[str, ...] = ("property", "name")) -> list[str]:
    """content de cada <meta> cujo atributo em attrs é prop (sem diferença de caixa); '' quando falta o content."""
    p = _MetaTags()
    p.feed(html)
    p.close()
    want = prop.lower()
    return [t.get("content", "").strip() for t in p.tags
            if any(t.get(attr, "").lower() == want for attr in attrs)]


def check_titles_and_groups(rel: str, html: str) -> list[str]:
    """Títulos e content_group das páginas montadas.

    Contrato (Gui, 13/11/2026, PR #57): esta regra é alarme de integridade sobre o HTML que os três templates
    geram (Base.astro do blog, index.html e portfolio/index.html), não barreira contra HTML ou JS forjado. Ela lê
    os snippets nas formas que esses templates usam; construções JS ou HTML que nenhum template produz (regex
    literal, interpolação de template literal, expressão no valor, template aninhado, atributo sem aspas) ficam
    fora do escopo e estão listadas na issue de follow-up.
    """
    problems = []
    if not rel.startswith(TITLE_EXEMPT_PREFIXES):
        visible = _visible_html(html)
        m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
        if not m:
            problems.append(f"{rel}: página sem <title>")
        else:
            title = unescape(m.group(1)).strip()
            if rel == "index.html":
                home_title = SITE_NAME + TITLE_SEP + "Builder e CTO"
                if title != home_title:
                    problems.append(f"{rel}: o título da home precisa ser {home_title!r}: {title!r}")
            elif expected_content_group(rel) == "insights-artigo":
                if not title:
                    problems.append(f"{rel}: título do post vazio")
                elif title.endswith(TITLE_SEP + SITE_NAME) or title.startswith(SITE_NAME + TITLE_SEP):
                    problems.append(f"{rel}: o título do post vai sem o nome do site ('{TITLE_SEP}{SITE_NAME}'): {title!r}")
            elif not title.endswith(TITLE_SEP + SITE_NAME):
                problems.append(f"{rel}: o título precisa terminar em '{TITLE_SEP}{SITE_NAME}': {title!r}")
            for prop in ("og:title", "twitter:title"):
                for v in _meta_contents(visible, prop):
                    if v != title:
                        problems.append(f"{rel}: {prop} diferente do <title>: {v!r}")
    if GA4_TAG in html:
        expected, groups = expected_content_group(rel), _content_groups(html)
        if expected is None:
            problems.append(f"{rel}: página com GA4 sem grupo de conteúdo previsto no check_site")
        elif groups != [expected]:
            problems.append(f"{rel}: content_group do GA4 {groups}, esperado [{expected!r}]")
    return problems


# Eventos próprios do GA4 (issue #60): assets/track.js. Contrato igual ao de check_titles_and_groups: alarme de
# integridade sobre o HTML dos três templates (index.html, portfolio/index.html e Base.astro do blog).
TRACK_PATH = "/assets/track.js"
AUTH_PATH = "/assets/auth.js"
GIS_CLIENT_URL = "accounts.google.com/gsi/client"
NO_ONE_TAP_PAGES = ("404.html", "privacidade/index.html", "termos/index.html", "prioridade/index.html")
NO_ONE_TAP_AUTH_PREFIXES = ("/privacidade", "/termos", "/prioridade", "/404")
CTA_LOCATIONS = ("hero", "projetos", "consultoria", "chamada-final", "consultoria-pagina", "como-contratar")  # as duas últimas: #136
SIGNUP_PAGE = "blog/inscricao-confirmada/index.html"
CONTACT_EMAIL = "contact@guifav.dev"
SCHEDULE_HOST = "calendar.app.google"


def cta_event(href: str) -> str | None:
    """O evento que o track.js manda para um link: contact_click, schedule_click ou nenhum."""
    try:
        u = urlsplit(href.strip())
    except ValueError:
        return None
    if u.scheme.lower() == "mailto":
        return "contact_click" if unquote(u.path).lower() == CONTACT_EMAIL else None
    if u.scheme.lower() == "https" and (u.hostname or "") == SCHEDULE_HOST:
        return "schedule_click"
    return None


def _track_loads(html: str, path: str = TRACK_PATH) -> int:
    """Quantos <script> que rodam carregam o arquivo do site em `path` (relativo ou https://guifav.dev)."""
    n = 0
    for attrs, _ in _live_scripts(html):
        m = re.search(r"""(?:^|\s)src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", attrs, flags=re.I)
        if not m:
            continue
        u = urlsplit(unescape(next(g for g in m.groups() if g is not None)).strip())
        if (u.scheme, u.netloc) in (("", ""), ("https", SITE_HOST)) and u.path == path:
            n += 1
    return n


class _Tracking(HTMLParser):
    """CTAs (evento, onde, data-cta-location) e marcadores data-ga-event da página."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = {"nav": 0, "footer": 0}
        self.divs: list[bool] = []  # pilha de <div>: True para o div.post-body
        self.ctas: list[tuple[str, str, str | None]] = []
        self.ga_events: list[str] = []
        self.ga_materials: list[str | None] = []  # data-material-id de cada marcador (generate_lead, issue #99)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str | None] = {}
        for k, v in attrs:
            a.setdefault(k, v)
        if tag in self.depth:
            self.depth[tag] += 1
        if tag == "div":
            self.divs.append("post-body" in (a.get("class") or "").split())
        if "data-ga-event" in a:
            self.ga_events.append(a["data-ga-event"] or "")
            self.ga_materials.append(a.get("data-material-id"))
        if tag == "a" and a.get("href"):
            ev = cta_event(a["href"] or "")
            if ev:
                where = ("menu" if self.depth["nav"] else "rodape" if self.depth["footer"]
                         else "post" if any(self.divs) else "pagina")
                self.ctas.append((ev, where, a.get("data-cta-location")))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.depth:
            self.depth[tag] = max(0, self.depth[tag] - 1)
        elif tag == "div" and self.divs:
            self.divs.pop()


CONSENT_PATH = "/assets/consent.js"
CONSENT_KEYS = ("ad_storage", "ad_user_data", "ad_personalization", "analytics_storage")
CLARITY_GUARD = "if(window.guifavConsent==='granted')window.guifavClarity();"


class _ConsentFooter(HTMLParser):
    """Links com data-consent-open: (dentro do <footer>, tem href)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.footer = 0
        self.links: list[tuple[bool, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "footer":
            self.footer += 1
        if "data-consent-open" in a:
            self.links.append((self.footer > 0, bool((a.get("href") or "").strip())))

    def handle_endtag(self, tag: str) -> None:
        if tag == "footer":
            self.footer = max(0, self.footer - 1)


def check_consent(rel: str, html: str) -> list[str]:
    """#68: em página com GA4, Consent Mode v2 com tudo negado por padrão (uma vez, antes do primeiro config), o
    consent.js uma vez, o Clarity só pela guarda de consentimento e o link "Cookies" (data-consent-open) no rodapé.
    Página sem GA4 não carrega o consent.js. Só conta código: fora de comentário, de string e de <script> de dado."""
    problems = []
    n = _track_loads(html, CONSENT_PATH)
    if GA4_TAG not in html:
        return [f"{rel}: página sem GA4 não carrega o {CONSENT_PATH}"] if n else []
    if n != 1:
        problems.append(f"{rel}: página com GA4 precisa carregar o {CONSENT_PATH} uma vez, carrega {n}")
    defaults, first_config, bad = [], None, []
    for i, body in enumerate(_exec_scripts(html)):
        mask = _js_mask(body)
        code = _js_mask(body, keep_strings=True)
        for call in re.finditer(r"\bgtag\s*\(", mask):
            rest = code[call.start():]
            if first_config is None and re.match(r"""gtag\s*\(\s*(['"])config\1\s*,""", rest):
                first_config = (i, call.start())
            m = re.match(r"""gtag\s*\(\s*(['"])consent\1\s*,\s*(['"])default\2\s*,\s*\{([^{}]*)\}\s*\)""", rest)
            if m:
                defaults.append((i, call.start()))
                obj = m.group(3)
                for k in CONSENT_KEYS:
                    if not re.search(r"""(?:^|[,{\s])(['"]?)%s\1\s*:\s*(['"])denied\2""" % k, obj):
                        bad.append(k)
                if re.search(r"""(['"])granted\1""", obj):
                    bad.append("granted")
    if len(defaults) != 1 or bad or (first_config is not None and defaults[0] > first_config):
        problems.append(f"{rel}: página com GA4 precisa do gtag('consent','default', {{...}}) uma vez, antes do "
                        f"primeiro config e com {', '.join(CONSENT_KEYS)} 'denied' (achados: {len(defaults)}, "
                        f"problemas: {', '.join(sorted(set(bad))) or 'ordem'})")
    for body in _exec_scripts(html):
        if all(k in body for k in CLARITY_MARKERS):
            code = re.sub(r"\s+", "", _js_mask(body, keep_strings=True))
            if "window.guifavClarity=function(){" not in code or CLARITY_GUARD not in code:
                problems.append(f"{rel}: o Microsoft Clarity só pode carregar pela guarda de consentimento "
                                f"(window.guifavClarity e {CLARITY_GUARD})")
    f = _ConsentFooter()
    f.feed(re.sub(r"<!--.*?-->", "", html, flags=re.S))
    if f.links != [(True, True)]:
        problems.append(f"{rel}: página com GA4 precisa de um link Cookies (data-consent-open, com href) no rodapé, "
                        f"tem {f.links}")
    return problems


GA_HOST = "guifav.dev"
# Sem o ponto final do FQDN: https://guifav.dev./ é o mesmo site (r1, Kimi P2).
GA_HOST_GUARD = "window['ga-disable-G-K7Y87QNCX8']=location.hostname.replace(/\\.$/,'')!=='guifav.dev';"


def check_ga_host_guard(rel: str, html: str) -> list[str]:
    """#66: em página com GA4, a desativação oficial do gtag.js fora de guifav.dev, uma vez e antes do config.

    A linha só conta como código: fora de comentário (HTML ou JS), fora de string e num <script> que roda.
    """
    if GA4_TAG not in html:
        return []
    shape = re.compile(r"\bwindow\s*\[\s*\]\s*=\s*location\s*\.\s*hostname\s*\.\s*replace\s*\(\s*/\\\.\$/\s*,\s*\)\s*!==\s*;")
    guards, first_config = [], None
    for i, body in enumerate(_exec_scripts(html)):
        mask = _js_mask(body)  # sem comentários e sem strings
        code = _js_mask(body, keep_strings=True)  # sem comentários, com strings
        for m in shape.finditer(mask):
            if re.sub(r"\s+", "", code[m.start():m.end()]) == GA_HOST_GUARD:
                guards.append((i, m.start()))
        for call in re.finditer(r"\bgtag\s*\(", mask):
            if first_config is None and re.match(r"""gtag\s*\(\s*(['"])config\1\s*,""", code[call.start():]):
                first_config = (i, call.start())
    if len(guards) != 1:
        return [f"{rel}: página com GA4 precisa de {GA_HOST_GUARD} uma vez (ga-disable fora de {GA_HOST}), tem {len(guards)}"]
    if first_config is not None and guards[0] > first_config:
        return [f"{rel}: o ga-disable fora de {GA_HOST} precisa vir antes do primeiro gtag('config', ...)"]
    return []


def check_tracking(rel: str, html: str) -> list[str]:
    """track.js uma vez em toda página com GA4 (e em nenhuma outra), CTAs com posição e o marcador do sign_up."""
    problems = []
    has_ga = GA4_TAG in html
    n = _track_loads(html)
    if has_ga and n != 1:
        problems.append(f"{rel}: página com GA4 precisa carregar o {TRACK_PATH} uma vez, carrega {n}")
    if not has_ga and n:
        problems.append(f"{rel}: página sem GA4 não carrega o {TRACK_PATH}")
    t = _Tracking()
    t.feed(html)
    t.close()
    if has_ga:
        seen: set[tuple[str, str]] = set()
        for ev, where, loc in t.ctas:
            if where in ("menu", "rodape"):
                if loc is not None:
                    problems.append(f"{rel}: {ev} no {where} não leva data-cta-location (a posição vem da estrutura)")
                continue
            if where == "post" and loc is None:
                continue  # link de dentro de um post: "conteudo"
            if loc not in CTA_LOCATIONS:
                problems.append(f"{rel}: {ev} fora do menu e do rodapé sem data-cta-location da lista "
                                f"{', '.join(CTA_LOCATIONS)}: {loc!r}")
            elif (ev, loc) in seen:
                problems.append(f"{rel}: {ev} com data-cta-location={loc!r} repetido na página")
            else:
                seen.add((ev, loc))
    # generate_lead na página de enviado de cada material, com o id do material do catálogo (issues #99 e #183).
    lead = next((m for m, path in MATERIALS.items() if rel == f"{path}/enviado/index.html"), None)
    expected = ["sign_up"] if rel == SIGNUP_PAGE else ["generate_lead"] if lead else []
    if t.ga_events != expected:
        problems.append(f"{rel}: data-ga-event {t.ga_events}, esperado {expected}")
    elif lead and t.ga_materials != [lead]:
        problems.append(f"{rel}: generate_lead com data-material-id {t.ga_materials}, esperado [{lead!r}]")
    # Formulário de material (issues #99 e #183): data-ga-client-id e o campo oculto ga_cid, que o track.js preenche no envio.
    if any(rel == f"{path}/index.html" for path in MATERIALS.values()):
        # Só HTML visível: formulário ou campo dentro de comentário não conta.
        forms = [f for f in re.findall(r"<form\b[^>]*>.*?</form>", _visible_html(html), flags=re.S | re.I)
                 if 'action="https://insights.guifav.dev/material"' in f]
        if len(forms) != 1:
            problems.append(f"{rel}: esperado um formulário de material, achados {len(forms)}")
        elif not re.match(r"<form\b[^>]*\sdata-ga-client-id[\s>=]", forms[0]) or \
                forms[0].count('<input type="hidden" name="ga_cid" value="">') != 1:
            problems.append(f"{rel}: formulário de material sem data-ga-client-id ou sem o campo oculto ga_cid")
    return problems


# "Ferramentas e guias" (issues #104, #151 e #166): as ferramentas no caminho do rastreador e do leitor. A home leva os
# links na seção própria e no rodapé; toda página do blog, no rodapé (o mesmo da home, do Base.astro). A página
# /ferramentas/ lista as 4 ferramentas e traz CollectionPage com isPartOf para o WebSite da home. Alarme de integridade
# dos templates, como os de cima: só nas páginas com GA4, que são as que os templates geram.
TOOLS_INDEX_LINKS = ("/ferramentas/mapa-decisao-ia/", "/ferramentas/planilha-custos-ia/",
                     "/ferramentas/llm-ranking/",
                     "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html")
TOOLS_LINKS = ("/ferramentas/", "/ferramentas/mapa-decisao-ia/", "/ferramentas/planilha-custos-ia/",
               "/ferramentas/llm-ranking/",
               BOOK_URL,
               "/ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html",
               COURSES_URL)
TOOLS_SECTION_ID = "ferramentas"


# Elementos sem fechamento: não entram na pilha de abertos do _ToolsLinks.
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _ToolsLinks(HTMLParser):
    """hrefs dentro do <footer>, da <section id="ferramentas"> e do <main>.

    Não contam: link em comentário, dentro de <template> ou de elemento com hidden, ou com hidden no próprio <a>
    (follow-up do PR #116). O rel de cada link fica guardado para a regra recusar nofollow.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # Pilha dos elementos abertos: (tag, bloco que ele abre ou None, se ele torna o conteúdo inerte).
        self.stack: list[tuple[str, str | None, bool]] = []
        self.found: dict[str, list[str]] = {"footer": [], "section": [], "main": []}
        self.rels: dict[str, list[tuple[str, str]]] = {"footer": [], "section": [], "main": []}

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        inert = any(i for _, _, i in self.stack) or tag == "template" or "hidden" in a
        if tag == "a" and a.get("href") and not inert:
            for where in dict.fromkeys(w for _, w, _ in self.stack if w):
                href = (a["href"] or "").strip()
                self.found[where].append(href)
                self.rels[where].append((href, (a.get("rel") or "").lower()))
        if tag in VOID_TAGS:
            return
        block = "footer" if tag == "footer" else (
            "main" if tag == "main" else (
                "section" if tag == "section" and a.get("id") == TOOLS_SECTION_ID else None))
        self.stack.append((tag, block, tag == "template" or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def check_tools(rel: str, html: str) -> list[str]:
    """#104, #151 e #166: TOOLS_LINKS no rodapé da home e do blog e na seção da home; /ferramentas/ com as 4 ferramentas e CollectionPage."""
    if GA4_TAG not in html:
        return []
    if rel == TOOLS_INDEX_PAGE:
        p = _ToolsLinks()
        p.feed(html)
        p.close()
        problems = []
        for href in TOOLS_INDEX_LINKS:
            n = p.found["main"].count(href)
            if n == 0:
                problems.append(f"{rel}: página de ferramentas sem o link {href}")
            elif n > 1:
                problems.append(f"{rel}: página de ferramentas com o link {href} {n} vezes")
            if any(h == href and "nofollow" in r.split() for h, r in p.rels["main"]):
                problems.append(f"{rel}: página de ferramentas com o link {href} em rel=nofollow")
        website_id = SITE_ORIGIN + "/#website"
        tools_url = SITE_ORIGIN + "/ferramentas/"
        if not any(n.get("@type") == "CollectionPage" and n.get("url") == tools_url
                   and isinstance(n.get("isPartOf"), dict) and n["isPartOf"].get("@id") == website_id
                   for n in _ld_nodes(html)):
            problems.append(f"{rel}: sem JSON-LD CollectionPage com url {tools_url} e isPartOf {website_id}")
        return problems
    if not (rel == "index.html" or rel.startswith("blog/")):
        return []
    p = _ToolsLinks()
    p.feed(html)
    p.close()
    blocks = {"rodapé": p.found["footer"]}
    if rel == "index.html":
        blocks[f'seção id="{TOOLS_SECTION_ID}"'] = p.found["section"]
    problems = []
    rels = {"rodapé": p.rels["footer"], f'seção id="{TOOLS_SECTION_ID}"': p.rels["section"]}
    for name, hrefs in blocks.items():
        for href in TOOLS_LINKS:
            n = hrefs.count(href)
            if n == 0:
                problems.append(f"{rel}: {name} sem o link de Ferramentas e guias {href}")
            elif n > 1:
                problems.append(f"{rel}: {name} com o link de Ferramentas e guias {href} {n} vezes")
            if any(h == href and "nofollow" in r.split() for h, r in rels[name]):
                problems.append(f"{rel}: {name} com o link de Ferramentas e guias {href} em rel=nofollow")
    return problems


# "Leia também" (issue #104): todo post indexável (fora os podcasts) leva o bloco <aside class="related"> com pelo
# menos RELATED_MIN posts indexáveis na lista (<ul>) e, quando o post tem tema, o link para a página do tema principal (o primeiro de
# topics). Podcast e post com index: false ficam sem o bloco. Alarme de integridade do [slug].astro, como as regras de
# cima; a lista e a ordem dos posts são do blog-src/src/lib/topics.ts, testadas lá.
RELATED_CLASS = "related"
RELATED_MIN = 3
TOPIC_PREFIX = "/blog/tema/"


class _RelatedBlocks(HTMLParser):
    """Os <aside class="related"> da página. De cada um: os hrefs dos links da lista (<ul>), que são os posts
    relacionados, e os de todos os links do bloco, que incluem o do tema no título. Comentário não conta."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # profundidade de <aside> dentro do bloco aberto; 0 fora dele
        self.ul = 0  # profundidade de <ul> dentro do bloco aberto
        self.blocks: list[dict[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "aside":
            if self.depth:
                self.depth += 1
            elif RELATED_CLASS in (a.get("class") or "").split():
                self.depth, self.ul = 1, 0
                self.blocks.append({"posts": [], "links": []})
        elif tag == "ul" and self.depth:
            self.ul += 1
        elif tag == "a" and self.depth and (a.get("href") or "").strip():
            href = (a["href"] or "").strip()
            self.blocks[-1]["links"].append(href)
            if self.ul:
                self.blocks[-1]["posts"].append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "aside" and self.depth:
            self.depth -= 1
        elif tag == "ul" and self.depth and self.ul:
            self.ul -= 1


def post_topics(fm: dict[str, str]) -> list[str]:
    """Os temas do frontmatter, no formato simples que o check-posts.ts exige: topics: [a, b]."""
    m = re.fullmatch(r"\[([a-z0-9-]+(?:, [a-z0-9-]+)*)\]", fm.get("topics", ""))
    return m.group(1).split(", ") if m else []


def check_related(site: Path, posts: Path) -> list[str]:
    """#104: o bloco "Leia também" em todo post indexável e em nenhum outro."""
    listed: dict[str, list[str]] = {}
    others: set[str] = set()
    for md in sorted(posts.glob("*.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug") or md.stem
        if fm.get("index") == "false" or fm.get("type") == "podcast":
            others.add(slug)
        else:
            listed[slug] = post_topics(fm)
    problems = []
    for slug in sorted(listed.keys() | others):
        f = site / "blog" / slug / "index.html"
        if not f.is_file():
            continue  # check() já acusa post sem página
        p = _RelatedBlocks()
        p.feed(f.read_text(encoding="utf-8"))
        p.close()
        rel = f.relative_to(site).as_posix()
        if slug in others:
            if p.blocks:
                problems.append(f"{rel}: podcast ou post com index: false com o bloco Leia também")
            continue
        if len(p.blocks) != 1:
            problems.append(f"{rel}: post indexável precisa de um bloco Leia também, tem {len(p.blocks)}")
            continue
        hrefs = p.blocks[0]["links"]
        linked = {m.group(1) for h in p.blocks[0]["posts"] if (m := re.fullmatch(r"/blog/([a-z0-9-]+)/", h))}
        good = {s for s in linked if s in listed and s != slug}
        if len(good) < RELATED_MIN:
            problems.append(f"{rel}: bloco Leia também com {len(good)} posts indexáveis, o mínimo é {RELATED_MIN}")
        for s in sorted(linked - good):
            problems.append(f"{rel}: bloco Leia também aponta para {s}, que é o próprio post ou está fora do índice")
        if listed[slug] and f"{TOPIC_PREFIX}{listed[slug][0]}/" not in hrefs:
            problems.append(f"{rel}: bloco Leia também sem o link para o tema {listed[slug][0]}")
    return problems


# "Em resumo" (issue #108): quando o post declara summary no frontmatter, a página renderiza um único bloco
# <section class="post-summary"> com o rótulo "Em resumo" e texto não vazio logo acima de <div class="post-body">.
# Quando não declara, o bloco não aparece. Alarme de integridade do [slug].astro.
SUMMARY_CLASS = "post-summary"
SUMMARY_LABEL = "Em resumo"
_YAML_BLOCK_INDICATOR = re.compile(r"^[>|](?:[+-]?\d?|\d[+-]?)(?:\s+#.*)?$")
# YAML 1.2 Core Schema (o mesmo do Astro): só estes quatro tokens sem aspas são nulos; "nUll" é string.
_YAML_NULLS = frozenset({"null", "Null", "NULL", "~"})


def _has_frontmatter_summary(text: str) -> bool:
    """True quando o frontmatter declara summary com valor não vazio, na mesma linha, na seguinte ou em bloco >/|."""
    m = re.match(r"^---\r?\n(.*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)", text, re.S)
    if not m:
        return False
    lines = m.group(1).splitlines()
    found: bool | None = None
    i = 0
    while i < len(lines):
        km = re.match(r"""^(?:summary|"summary"|'summary')[ \t]*:[ \t]*(.*)$""", lines[i])
        if not km:
            i += 1
            continue
        raw = km.group(1).strip()
        block = bool(_YAML_BLOCK_INDICATOR.fullmatch(raw))
        i += 1
        cont: list[str] = []
        while i < len(lines) and (lines[i] == "" or lines[i][0] in " \t"):
            cont.append(lines[i])
            i += 1
        if block:
            found = any(ln.strip() for ln in cont)
        elif raw and not raw.startswith("#"):
            val = _yaml_scalar(raw)
            if raw[0] in ("'", '"'):
                found = bool(val.strip())
            else:
                found = bool(val.strip()) and val.strip() not in _YAML_NULLS
        else:
            cleaned = [ln.strip() for ln in cont if ln.strip() and not ln.strip().startswith("#")]
            if not cleaned:
                found = False
            else:
                first = cleaned[0]
                if first[0] in ("'", '"'):
                    found = bool(_yaml_scalar(first).strip()) or len(cleaned) > 1
                else:
                    found = _yaml_scalar(first).strip() not in _YAML_NULLS or len(cleaned) > 1
    return bool(found)


class _SummaryBlocks(HTMLParser):
    """Blocos <section class="post-summary"> visíveis e sua posição relativa a <div class="post-body">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, é post-summary visível, torna inerte)
        self.saw_body = False
        self.blocks: list[tuple[bool, str]] = []  # (antes de .post-body, texto coletado)
        self._buf: list[str] | None = None
        self._before = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        classes = a.get("class", "").split()
        inert = any(i for _, _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        if not inert and tag == "div" and "post-body" in classes:
            self.saw_body = True
        is_summary = not inert and tag == "section" and SUMMARY_CLASS in classes and self._buf is None
        if is_summary:
            self._buf = []
            self._before = not self.saw_body
        if tag not in VOID_TAGS:
            self.stack.append((tag, is_summary, tag in ("template", "noscript") or "hidden" in a))

    def handle_data(self, data: str) -> None:
        if self._buf is not None and not any(i for _, _, i in self.stack):
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                closed = self.stack[i:]
                del self.stack[i:]
                if any(is_s for _, is_s, _ in closed) and self._buf is not None:
                    self.blocks.append((self._before, " ".join("".join(self._buf).split())))
                    self._buf = None
                return


def check_summary(site: Path, posts: Path) -> list[str]:
    """#108: bloco 'Em resumo' acima de .post-body em todo post com summary, e em nenhum sem summary."""
    problems: list[str] = []
    for md in sorted(posts.glob("*.md")):
        md_text = md.read_text(encoding="utf-8")
        fm = frontmatter(md_text)
        slug = fm.get("slug") or md.stem
        rel = f"blog/{slug}/index.html"
        page = site / rel
        if not page.is_file():
            continue  # check() já acusa post sem página
        has_summary = _has_frontmatter_summary(md_text)
        p = _SummaryBlocks()
        p.feed(page.read_text(encoding="utf-8"))
        p.close()
        if not has_summary:
            if p.blocks:
                problems.append(f"{rel}: post sem summary com bloco {SUMMARY_CLASS}")
            continue
        if len(p.blocks) != 1:
            problems.append(f"{rel}: post com summary precisa de um bloco {SUMMARY_CLASS}, tem {len(p.blocks)}")
            continue
        before_body, text = p.blocks[0]
        if not before_body:
            problems.append(f"{rel}: bloco {SUMMARY_CLASS} precisa vir antes de .post-body")
        if not text.startswith(SUMMARY_LABEL) or not text[len(SUMMARY_LABEL):].strip():
            problems.append(f"{rel}: bloco {SUMMARY_CLASS} sem o rótulo {SUMMARY_LABEL!r} ou sem texto de resumo")
    return problems


ROOT_INDEX_CHILDREN = [SITE_ORIGIN + "/sitemap-site.xml", SITE_ORIGIN + "/blog/sitemap.xml"]


def _sitemap(site: Path, rel: str, kind: str, item: str, problems: list[str]) -> list[tuple[str, str]] | None:
    """(loc, lastmod) de um sitemap do _site, ou None com o problema anotado."""
    try:
        root = ET.parse(site / rel).getroot()
    except (ET.ParseError, FileNotFoundError) as e:
        problems.append(f"{rel} inválido: {e}")
        return None
    if root.tag != f"{SITEMAP_NS}{kind}":
        problems.append(f"{rel}: precisa ser <{kind}>, é <{root.tag.split('}')[-1]}>")
        return None
    return [((e.findtext(f"{SITEMAP_NS}loc") or "").strip(), (e.findtext(f"{SITEMAP_NS}lastmod") or "").strip())
            for e in root.iter(f"{SITEMAP_NS}{item}")]


def check_root_sitemaps(site: Path, root_pages: list[tuple[str, str, "_Page"]]) -> list[str]:
    """Índice /sitemap.xml, /sitemap-site.xml completo e robots.txt citando só o índice."""
    problems: list[str] = []
    robots = site / "robots.txt"
    robots_txt = robots.read_text(encoding="utf-8") if robots.is_file() else ""
    cited = [ln.split(":", 1)[1].strip() for ln in robots_txt.splitlines() if ln.lower().startswith("sitemap:")]
    if cited != [SITE_ORIGIN + "/sitemap.xml"]:
        problems.append(f"robots.txt: precisa citar só o índice {SITE_ORIGIN}/sitemap.xml, cita {cited}")
    index = _sitemap(site, "sitemap.xml", "sitemapindex", "sitemap", problems)
    if index is not None and sorted(loc for loc, _ in index) != sorted(ROOT_INDEX_CHILDREN):
        problems.append(f"sitemap.xml: o índice precisa citar exatamente {ROOT_INDEX_CHILDREN}, cita {[l for l, _ in index]}")
    # Páginas do site que o buscador deve indexar: fora do blog, não a 404, liberadas no robots, sem noindex.
    want = set()
    for rel, url, p in root_pages:
        if rel == "404.html" or robots_blocks(robots_txt, url) or any(_robots_noindex(r) for r in p.robots):
            continue
        if not canonical_is_self(p.canonicals, url):
            problems.append(f"{rel}: canonical aponta para outra URL ({p.canonicals}); a página não entraria no sitemap")
            continue
        want.add(SITE_ORIGIN + url)
    entries = _sitemap(site, "sitemap-site.xml", "urlset", "url", problems)
    if entries is not None:
        got = [loc for loc, _ in entries]
        for loc in sorted(want - set(got)):
            problems.append(f"sitemap-site.xml: falta página indexável: {loc}")
        for loc in sorted(set(got) - want):
            problems.append(f"sitemap-site.xml: lista o que não deve ser indexado: {loc}")
        if len(got) != len(set(got)):
            problems.append("sitemap-site.xml: URL repetida")
        for loc, lm in entries:
            if not _valid_datetime(lm):
                problems.append(f"sitemap-site.xml: lastmod ausente ou inválido (data, hora e fuso, W3C): {loc} {lm!r}")
    # #174 (C.5): nenhuma URL listada nos sitemaps (site ou blog) pode ficar bloqueada pelo robots.txt.
    blog_entries = _sitemap(site, "blog/sitemap.xml", "urlset", "url", [])
    for sm_name, sm_entries in (("sitemap-site.xml", entries), ("blog/sitemap.xml", blog_entries)):
        for loc, _ in sm_entries or []:
            u = urlsplit(loc)
            if u.scheme != "https" or u.netloc != SITE_HOST:
                continue
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            for agent in ("*", "googlebot", "bingbot"):
                if robots_blocks(robots_txt, path, agent):
                    problems.append(f"{sm_name}: URL bloqueada pelo robots.txt ({agent}): {loc}")
                    break
    return problems


# Busca (#103): o nome do site que o Google mostra vem do WebSite da home, e cada post tem description e título
# na medida. Falha só em post novo (date a partir do cutover) com description curta; o resto é aviso. Os posts
# importados (Substack e os restaurados do Wix, todos com date anterior) só recebem aviso, e só os indexáveis.
HOME_WEBSITE = {"name": SITE_NAME, "url": SITE_ORIGIN + "/"}
SCHEMA_CONTEXTS = ("https://schema.org", "https://schema.org/", "http://schema.org", "http://schema.org/")
PERSON_ID = SITE_ORIGIN + "/#person"
# Comparado em UTC, como o blog.ts e o schema.ts: date sem hora é meia-noite UTC; com fuso, convertida (r1, agy P3).
NEW_POST_SINCE = datetime(2026, 11, 12, tzinfo=timezone.utc)
DESCRIPTION_MIN_NEW = 70
DESCRIPTION_RANGE = (120, 160)
TITLE_MAX = 60


class _ArticleImages(HTMLParser):
    """Texto alternativo de cada <img> dentro de <article> (None quando falta o atributo)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.alts: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self.depth += 1
        elif tag == "img" and self.depth:
            self.alts.append(dict(attrs).get("alt"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self.depth:
            self.depth -= 1


def _ld_blocks(html: str) -> list[tuple[list, bool]]:
    """Cada raiz de JSON-LD com o @context do schema.org: (nós, se veio de um @graph).

    Ignora o que não roda na página: comentário, <template>, <noscript> e <script> com data-type em vez de type
    (#136). Vale para a home, para a /consultoria/ e para os posts (#105): JSON-LD só nesses lugares não conta."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<(template|noscript)\b[^>]*>.*?</\1\s*>", "", html, flags=re.S | re.I)
    scripts = re.findall(r"""<script\b[^>]*(?<![\w-])type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script\s*>""",
                         html, flags=re.S | re.I)
    blocks: list[tuple[list, bool]] = []
    for body in scripts:
        try:
            data = json.loads(body)
        except ValueError:
            continue
        # r1 (agy P3): cada raiz traz o @context do schema.org; o @graph, quando existe, é lista.
        for root in data if isinstance(data, list) else [data]:
            if not isinstance(root, dict) or root.get("@context") not in SCHEMA_CONTEXTS:
                continue
            if "@graph" not in root:
                blocks.append(([root], False))
            elif isinstance(root["@graph"], list):
                blocks.append((root["@graph"], True))
    return blocks


def _ld_nodes(html: str) -> list[dict]:
    """Os nós dos JSON-LD da página: as raízes e os itens de @graph de _ld_blocks, só os objetos (#136)."""
    return [n for nodes, _ in _ld_blocks(html) for n in nodes if isinstance(n, dict)]


def _iter_ld_dicts(value: object):
    """Percorre todos os objetos (dict) contidos em value, em qualquer profundidade (dicts e listas aninhados)."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _iter_ld_dicts(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_ld_dicts(item)


def _has_ld_type(node: dict, *types: str) -> bool:
    """True se @type do nó contiver algum dos tipos pedidos (string ou lista de strings)."""
    t = node.get("@type")
    if isinstance(t, str):
        return t in types
    if isinstance(t, list):
        return any(isinstance(x, str) and x in types for x in t)
    return False


def _is_zero_price(price: object) -> bool:
    """Preço exatamente a string '0' (decisão do Gui, 26/11/2026, #105: primeira conversa gratuita)."""
    return isinstance(price, str) and price == "0"


def _home_websites(html: str) -> list[dict]:
    """Os WebSite da home com HOME_WEBSITE (nome e URL); o WebSite não usa o @id da Person."""
    return [n for nodes, _ in _ld_blocks(html) for n in nodes
            if isinstance(n, dict) and n.get("@type") == "WebSite" and n.get("@id") != PERSON_ID
            and all(n.get(k) == v for k, v in HOME_WEBSITE.items())]


def _home_has_website(html: str) -> bool:
    """Algum JSON-LD da home (o que _ld_blocks lê) traz um WebSite com HOME_WEBSITE."""
    return bool(_home_websites(html))


def _home_website_id(html: str) -> str | None:
    """@id do WebSite da home, para o isPartOf dos posts e temas (#105)."""
    ids = [n["@id"] for n in _home_websites(html) if isinstance(n.get("@id"), str) and n["@id"].strip() != ""]
    return ids[0] if ids else None


# JSON-LD dos posts (#105): o @graph que o [slug].astro emite. A Person mínima (nome e URL) deixa o avaliador do
# Google, que lê uma página por vez, resolver author.name e author.url sem seguir o @id até a home.
POST_PERSON = {"@type": "Person", "@id": PERSON_ID, "name": SITE_NAME, "url": SITE_ORIGIN + "/"}
POST_LD_RULE = "post indexável sem o JSON-LD do post (#105)"
# Espelha BLOG_NAME de blog-src/src/consts.ts (conferido em TopicLdTest.test_blog_name_matches_consts).
BLOG_NAME = "Insights"
TOPIC_LD_RULE = "página de tema sem o JSON-LD do tema (#105)"
ITEM_LIST_ORDER = "https://schema.org/ItemListOrderAscending"
TOPIC_LIST_ITEM_KEYS = frozenset({"@type", "position", "name", "url"})


def post_ld_problems(html: str, url: str, website_id: str | None) -> list[str]:
    """Motivos pelos quais a página do post não traz o @graph do #105; vazio quando traz.

    Contrato: um único BlogPosting na página, dentro de um @graph com a BreadcrumbList (home, blog, post) e a Person
    mínima; author e publisher são a Person; isPartOf é o WebSite da home (website_id, lido do index.html; None
    quando a home não tem, caso acusado à parte). É alarme de integridade do template, não barreira contra HTML forjado.
    """
    postings = [(n, nodes, graph) for nodes, graph in _ld_blocks(html) for n in nodes
                if isinstance(n, dict) and n.get("@type") == "BlogPosting"]
    if not postings:
        return ["nenhum BlogPosting"]
    if len(postings) > 1:
        return [f"{len(postings)} BlogPosting na página"]
    post, nodes, graph = postings[0]
    if not graph:
        return ["o BlogPosting não está num @graph"]

    def ref(v: object) -> object:
        return v.get("@id") if isinstance(v, dict) else None

    why: list[str] = []
    if ref(post.get("author")) != PERSON_ID:
        why.append(f"author não aponta para {PERSON_ID}")
    if ref(post.get("publisher")) != PERSON_ID:
        why.append(f"publisher não aponta para {PERSON_ID}")
    if website_id is not None and ref(post.get("isPartOf")) != website_id:
        why.append(f"isPartOf não aponta para o WebSite da home ({website_id})")
    for k in ("url", "mainEntityOfPage"):
        if post.get(k) != url:
            why.append(f"{k} diferente de {url}")
    headline = post.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        why.append("headline vazio")
    persons = [n for n in nodes if isinstance(n, dict) and n.get("@id") == PERSON_ID]
    if len(persons) != 1:
        why.append(f"{len(persons)} nós Person com @id {PERSON_ID} no @graph, em vez de 1")
    elif any(persons[0].get(k) != v for k, v in POST_PERSON.items()):
        why.append(f"a Person do @graph não tem @type, name e url {POST_PERSON}")
    lists = [n for n in nodes if isinstance(n, dict) and n.get("@type") == "BreadcrumbList"]
    want = [SITE_ORIGIN + "/", SITE_ORIGIN + BLOG_PREFIX, url]
    if len(lists) != 1:
        why.append(f"{len(lists)} BreadcrumbList no @graph, em vez de 1")
    else:
        items = lists[0].get("itemListElement")
        ok = isinstance(items, list) and len(items) == len(want) and all(
            isinstance(it, dict) and it.get("@type") == "ListItem" and type(it.get("position")) is int
            and it["position"] == i + 1 and it.get("item") == want[i]
            and isinstance(it.get("name"), str) and it["name"].strip() != ""
            for i, it in enumerate(items))
        if ok and items[-1]["name"] != headline:
            ok = False
        if not ok:
            why.append(f"BreadcrumbList fora da trilha {' > '.join(want)}, com o headline no último nível")
    return why


def check_post_ld(site: Path, posts: Path) -> list[str]:
    """#105: todo post indexável sai com o @graph de BlogPosting, BreadcrumbList e Person mínima."""
    problems: list[str] = []
    home = site / "index.html"
    website_id = _home_website_id(home.read_text(encoding="utf-8")) if home.is_file() else None
    if website_id is None:
        problems.append("index.html: sem o WebSite com @id para o isPartOf dos posts (#105)")
    for md in sorted(posts.glob("**/*.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        if fm.get("index") == "false":
            continue
        slug = fm.get("slug") or md.stem
        rel = f"blog/{slug}/index.html"
        page = site / rel
        if not page.is_file():
            continue  # check() já acusa post sem página
        why = post_ld_problems(page.read_text(encoding="utf-8"), f"{SITE_ORIGIN}{BLOG_PREFIX}{slug}/", website_id)
        if why:
            problems.append(f"{rel}: {POST_LD_RULE}: {'; '.join(why)}")
    return problems


class _TopicListLinks(HTMLParser):
    """Links <a> visíveis dentro de <ol class="... topic-list ...">, em ordem."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, é ol.topic-list visível, torna inerte)
        self.lists: list[list[str]] = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        inert = any(i for _, _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        in_list = any(is_l for _, is_l, _ in self.stack)
        is_topic_list = not inert and not in_list and tag == "ol" and "topic-list" in a.get("class", "").split()
        if is_topic_list:
            self.lists.append([])
        if tag == "a" and not inert and in_list:
            href = a.get("href", "").strip()
            if href:
                self.lists[-1].append(href)
        if tag not in VOID_TAGS:
            self.stack.append((tag, is_topic_list, tag in ("template", "noscript") or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def topic_ld_problems(html: str, url: str, website_id: str | None) -> list[str]:
    """Motivos pelos quais a página de tema não traz o @graph do #105; vazio quando traz.

    Contrato: um único CollectionPage na página, dentro de um @graph com BreadcrumbList (home, blog, tema) e a
    Person mínima; @id e url iguais à própria página; author é a Person; isPartOf é o WebSite da home; breadcrumb
    aponta para <url>#breadcrumb; mainEntity é um ItemList cujas URLs batem, na mesma ordem, com os links de
    ol.topic-list.
    """
    collections = [(n, nodes, graph) for nodes, graph in _ld_blocks(html) for n in nodes
                   if isinstance(n, dict) and n.get("@type") == "CollectionPage"]
    if not collections:
        return ["nenhum CollectionPage"]
    if len(collections) > 1:
        return [f"{len(collections)} CollectionPage na página"]
    col, nodes, graph = collections[0]
    if not graph:
        return ["o CollectionPage não está num @graph"]

    def ref(v: object) -> object:
        return v.get("@id") if isinstance(v, dict) else None

    why: list[str] = []
    for k in ("@id", "url"):
        if col.get(k) != url:
            why.append(f"{k} diferente de {url}")
    name = col.get("name")
    if not isinstance(name, str) or not name.strip():
        why.append("name vazio")
    desc = col.get("description")
    if not isinstance(desc, str) or not desc.strip():
        why.append("description vazio")
    if col.get("inLanguage") != "pt-BR":
        why.append("inLanguage diferente de 'pt-BR'")
    if ref(col.get("author")) != PERSON_ID:
        why.append(f"author não aponta para {PERSON_ID}")
    if website_id is not None and ref(col.get("isPartOf")) != website_id:
        why.append(f"isPartOf não aponta para o WebSite da home ({website_id})")
    crumb_id = f"{url}#breadcrumb"
    if ref(col.get("breadcrumb")) != crumb_id:
        why.append(f"breadcrumb não aponta para {crumb_id}")

    persons = [n for n in nodes if isinstance(n, dict) and n.get("@id") == PERSON_ID]
    if len(persons) != 1:
        why.append(f"{len(persons)} nós Person com @id {PERSON_ID} no @graph, em vez de 1")
    elif any(persons[0].get(k) != v for k, v in POST_PERSON.items()):
        why.append(f"a Person do @graph não tem @type, name e url {POST_PERSON}")

    lists = [n for n in nodes if isinstance(n, dict) and n.get("@type") == "BreadcrumbList"]
    want_crumbs = [
        (SITE_NAME, SITE_ORIGIN + "/"),
        (BLOG_NAME, SITE_ORIGIN + BLOG_PREFIX),
        (name, url),
    ]
    if len(lists) != 1:
        why.append(f"{len(lists)} BreadcrumbList no @graph, em vez de 1")
    else:
        bl = lists[0]
        if bl.get("@id") != crumb_id:
            why.append(f"BreadcrumbList com @id diferente de {crumb_id}")
        items = bl.get("itemListElement")
        ok = isinstance(items, list) and len(items) == len(want_crumbs) and all(
            isinstance(it, dict) and it.get("@type") == "ListItem" and type(it.get("position")) is int
            and it["position"] == i + 1 and it.get("item") == want_url
            and isinstance(it.get("name"), str) and it["name"].strip() != "" and it["name"] == want_name
            for i, (it, (want_name, want_url)) in enumerate(zip(items, want_crumbs)))
        if not ok:
            trail_desc = " > ".join(f"{n} ({u})" for n, u in want_crumbs[:2]) + f" > name ({url})"
            why.append(f"BreadcrumbList fora da trilha {trail_desc}")

    tl = _TopicListLinks()
    tl.feed(html)
    tl.close()
    visible_urls: list[str] | None = None
    if len(tl.lists) != 1:
        why.append(f"esperava uma lista ol.topic-list visível, achou {len(tl.lists)}")
    elif not tl.lists[0]:
        why.append("lista visível ol.topic-list vazia")
    else:
        visible_urls = [urljoin(url, h) for h in tl.lists[0]]

    main = col.get("mainEntity")
    if not isinstance(main, dict) or main.get("@type") != "ItemList":
        why.append("mainEntity sem ItemList")
    else:
        if main.get("itemListOrder") != ITEM_LIST_ORDER:
            why.append(f"ItemList com itemListOrder diferente de {ITEM_LIST_ORDER}")
        elems = main.get("itemListElement")
        if not isinstance(elems, list):
            why.append("ItemList sem itemListElement em lista")
        else:
            if type(main.get("numberOfItems")) is not int or main["numberOfItems"] != len(elems):
                why.append(f"ItemList com numberOfItems {main.get('numberOfItems')!r}, tamanho da lista é {len(elems)}")
            bad_item = any(
                not isinstance(it, dict) or set(it.keys()) != TOPIC_LIST_ITEM_KEYS
                or it.get("@type") != "ListItem" or type(it.get("position")) is not int
                or it["position"] != i + 1
                or not isinstance(it.get("name"), str) or not it["name"].strip()
                or not isinstance(it.get("url"), str) or not it["url"].strip()
                for i, it in enumerate(elems)
            )
            if bad_item:
                why.append("ItemList com ListItem inválido (esperado só @type, position, name e url)")
            elif visible_urls is not None:
                ld_urls = [it["url"] for it in elems]
                if ld_urls != visible_urls:
                    why.append(f"ItemList com URLs diferentes da lista visível ol.topic-list "
                               f"({len(ld_urls)} no JSON-LD, {len(visible_urls)} na lista)")
    return why


def check_topic_ld(site: Path) -> list[str]:
    """#105: toda página de tema sai com o @graph de CollectionPage, ItemList, BreadcrumbList e Person mínima."""
    problems: list[str] = []
    home = site / "index.html"
    website_id = _home_website_id(home.read_text(encoding="utf-8")) if home.is_file() else None
    if website_id is None:
        problems.append("index.html: sem o WebSite com @id para o isPartOf dos temas (#105)")
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        if not TOPIC_PAGE.fullmatch(rel):
            continue
        url = SITE_ORIGIN + _page_url(site, f)
        why = topic_ld_problems(f.read_text(encoding="utf-8"), url, website_id)
        if why:
            problems.append(f"{rel}: {TOPIC_LD_RULE}: {'; '.join(why)}")
    return problems


# Framework SEO + AEO/GEO (#105, decisão 26/11/2026): documento gratuito (DigitalDocument), sem Product nem Offer.
FRAMEWORK_PAGE = "ferramentas/seo-geo-aeo-framework/framework-seo-aeo.html"
FRAMEWORK_URL = SITE_ORIGIN + "/" + FRAMEWORK_PAGE
FRAMEWORK_LD_RULE = "framework de SEO e AEO sem o JSON-LD DigitalDocument (#105)"
FRAMEWORK_FORBIDDEN_FIELDS = ("offers", "price", "brand", "productID")


def framework_ld_problems(html: str, url: str = FRAMEWORK_URL, website_id: str | None = None) -> list[str]:
    """Motivos pelos quais a página do framework não traz o JSON-LD DigitalDocument do #105; vazio quando traz.

    Contrato: exatamente um DigitalDocument (num @graph com a Person mínima), com isAccessibleForFree true, @id e url
    iguais à própria página, author e publisher na Person, isPartOf no WebSite da home, e nenhum nó Product ou Offer
    em qualquer profundidade.
    """
    blocks = _ld_blocks(html)
    why: list[str] = []

    forbidden: list[str] = []
    for nodes, _ in blocks:
        for node in nodes:
            for d in _iter_ld_dicts(node):
                for bad_type in ("Product", "Offer"):
                    if _has_ld_type(d, bad_type) and bad_type not in forbidden:
                        forbidden.append(bad_type)
    if forbidden:
        why.append(f"nó proibido no JSON-LD: {', '.join(forbidden)}")

    all_docs = [d for nodes, _ in blocks for node in nodes for d in _iter_ld_dicts(node)
                if _has_ld_type(d, "DigitalDocument")]
    top_docs = [(n, nodes, graph) for nodes, graph in blocks for n in nodes
                if isinstance(n, dict) and _has_ld_type(n, "DigitalDocument")]
    if not all_docs:
        why.append("nenhum DigitalDocument")
        return why
    if len(all_docs) > 1:
        why.append(f"{len(all_docs)} DigitalDocument na página")
        return why
    if len(top_docs) != 1:
        why.append("DigitalDocument aninhado fora do nível principal")
        return why

    doc, nodes, graph = top_docs[0]
    if not graph:
        why.append("o DigitalDocument não está num @graph")

    def ref(v: object) -> object:
        return v.get("@id") if isinstance(v, dict) else None

    if doc.get("isAccessibleForFree") is not True:
        why.append("isAccessibleForFree diferente de true")
    for k in ("@id", "url"):
        if doc.get(k) != url:
            why.append(f"{k} diferente de {url}")
    if ref(doc.get("author")) != PERSON_ID:
        why.append(f"author não aponta para {PERSON_ID}")
    if ref(doc.get("publisher")) != PERSON_ID:
        why.append(f"publisher não aponta para {PERSON_ID}")
    if website_id is not None and ref(doc.get("isPartOf")) != website_id:
        why.append(f"isPartOf não aponta para o WebSite da home ({website_id})")
    for bad_key in FRAMEWORK_FORBIDDEN_FIELDS:
        if bad_key in doc:
            why.append(f"campo proibido no DigitalDocument: {bad_key}")

    persons = [n for n in nodes if isinstance(n, dict) and n.get("@id") == PERSON_ID]
    if len(persons) != 1:
        why.append(f"{len(persons)} nós Person com @id {PERSON_ID} no @graph, em vez de 1")
    elif any(persons[0].get(k) != v for k, v in POST_PERSON.items()):
        why.append(f"a Person do @graph não tem @type, name e url {POST_PERSON}")
    return why


def check_framework_ld(site: Path) -> list[str]:
    """#105: a página do framework sai com @graph de DigitalDocument gratuito e Person mínima, sem Product nem Offer."""
    rel = FRAMEWORK_PAGE
    page = site / rel
    if not page.is_file():
        return [f"{rel}: página do framework ausente no site montado (site-manifest.txt)"]
    home = site / "index.html"
    website_id = _home_website_id(home.read_text(encoding="utf-8")) if home.is_file() else (SITE_ORIGIN + "/#website")
    problems: list[str] = []
    if home.is_file() and website_id is None:
        problems.append("index.html: sem o WebSite com @id para o isPartOf do framework (#105)")
    why = framework_ld_problems(page.read_text(encoding="utf-8"), FRAMEWORK_URL, website_id)
    if why:
        problems.append(f"{rel}: {FRAMEWORK_LD_RULE}: {'; '.join(why)}")
    return problems


def check_page_og_site_name(rel: str, html: str) -> list[str]:
    """#105: a home e toda página HTML sob blog/ levam exatamente um og:site_name igual a SITE_NAME."""
    if rel != "index.html" and not rel.startswith("blog/"):
        return []
    vals = _meta_contents(_visible_html(html), "og:site_name", attrs=("property",))
    if not vals:
        return [f"{rel}: sem meta og:site_name (esperado {SITE_NAME!r})"]
    if len(vals) > 1:
        return [f"{rel}: esperado 1 meta og:site_name, achados {len(vals)}: {vals!r}"]
    if vals[0] != SITE_NAME:
        return [f"{rel}: og:site_name com valor {vals[0]!r}, esperado {SITE_NAME!r}"]
    return []


def check_og_site_name(site: Path) -> list[str]:
    """#105: a home e toda página HTML sob blog/ saem com exatamente um og:site_name com SITE_NAME."""
    problems: list[str] = []
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_page_og_site_name(rel, f.read_text(encoding="utf-8"))
    return problems


# Resíduos legados nos posts indexáveis (critério de pronto da #105): busca na fonte sem comentários HTML
# na mesma linha (<!--...-->) e sem diferença de caixa.
POST_RESIDUE_TERMS = (
    "guilhermefavaron.com.br",
    "mindapps.ai",
    "VP de Tecnologia",
    "ia-aplicada.substack.com",
    "iaaplicada.substack.com",
    "guifav.github.io",
)
GITHUB_REPO_HOST_PREFIX = "github.com/guifav/"
_HTML_LINE_COMMENT = re.compile(r"<!--.*?-->")


def _has_forbidden_github_io(low: str) -> bool:
    """True quando a linha (em minúsculas) traz guifav.github.io fora da exceção github.com/guifav/guifav.github.io."""
    term = "guifav.github.io"
    prefix = GITHUB_REPO_HOST_PREFIX
    plen = len(prefix)
    for m in re.finditer(re.escape(term), low):
        start = m.start()
        if low[:start].endswith(prefix):
            before = start - plen - 1
            if before < 0 or not (low[before].isalnum() or low[before] in ".-"):
                continue
        return True
    return False


def check_post_residues(posts: Path) -> list[str]:
    """#105: posts indexáveis não podem conter resíduos legados na fonte (ignorando <!--...--> na linha e caixa)."""
    problems: list[str] = []
    for md in sorted(posts.glob("**/*.md")):
        text = md.read_text(encoding="utf-8")
        fm = frontmatter(text)
        if fm.get("index", "").lower() == "false":
            continue
        rel = md.relative_to(posts).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            low = _HTML_LINE_COMMENT.sub("", line).lower()
            for term in POST_RESIDUE_TERMS:
                term_low = term.lower()
                if term_low not in low:
                    continue
                if term_low == "guifav.github.io" and not _has_forbidden_github_io(low):
                    continue
                problems.append(f"{rel}:{lineno}: resíduo legado em post indexável (#105): {term!r}")
    return problems


def check_seo(site: Path, posts: Path) -> tuple[list[str], list[str], list[str]]:
    """(problemas, avisos dos posts novos, avisos dos posts importados) da #103, sobre o HTML montado.

    Description e título saem do HTML publicado (o que o buscador lê), não do frontmatter; a data sai do
    frontmatter, que o schema do blog já valida.
    """
    problems: list[str] = []
    warn_new: list[str] = []
    warn_old: list[str] = []
    home = site / "index.html"
    if not home.is_file() or not _home_has_website(home.read_text(encoding="utf-8")):
        problems.append(f"index.html: sem o JSON-LD WebSite com {HOME_WEBSITE} (nome do site no Google)")
    lo, hi = DESCRIPTION_RANGE
    for md in sorted(posts.glob("**/*.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug") or md.stem
        rel = f"blog/{slug}/index.html"
        page = site / rel
        if not page.is_file():
            continue  # check() já acusa post sem página
        try:
            date = datetime.fromisoformat(fm.get("date", ""))
        except ValueError:
            problems.append(f"{rel}: date do post ilegível no frontmatter: {fm.get('date', '')!r}")
            continue
        date = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
        new = date >= NEW_POST_SINCE
        if not new and fm.get("index") == "false":
            continue
        visible = _visible_html(page.read_text(encoding="utf-8"))
        descs = _meta_contents(visible, "description")
        desc = descs[0] if descs else ""
        m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
        title = unescape(m.group(1)).strip() if m else ""
        warns = warn_new if new else warn_old
        if len(desc) < DESCRIPTION_MIN_NEW and new:
            problems.append(f"{rel}: post novo com description de {len(desc)} caracteres; o mínimo é "
                            f"{DESCRIPTION_MIN_NEW} e o alvo, de {lo} a {hi}")
        elif not lo <= len(desc) <= hi:
            warns.append(f"{rel}: description com {len(desc)} caracteres, fora da faixa de {lo} a {hi}")
        if len(title) > TITLE_MAX:
            warns.append(f"{rel}: título com {len(title)} caracteres, acima de {TITLE_MAX}")
        imgs = _ArticleImages()
        imgs.feed(visible)
        imgs.close()
        no_alt = sum(1 for a in imgs.alts if not (a or "").strip())
        if no_alt:
            warns.append(f"{rel}: {no_alt} de {len(imgs.alts)} imagens sem texto alternativo")
    return problems, warn_new, warn_old


# Página de consultoria (#136, consulta 18 do mapa da #112) e regras dos R$ 500 (decisão do Gui, 26/11/2026, #105):
# a /consultoria/ existe com o título, o H1 aprovados e o JSON-LD Service do Gui (provider pelo @id da Person) com
# Offer gratuita (price "0" em BRL). Em todo o site, o link antigo da agenda paga não pode aparecer e nenhum Service
# pode trazer offers com price diferente da string "0".
CONSULTING_PAGE = "consultoria/index.html"
CONSULTING_TITLE = "Consultoria em IA para empresas" + TITLE_SEP + SITE_NAME
CONSULTING_H1 = "Consultoria em inteligência artificial para empresas"
OLD_PAID_SCHEDULE_ID = "DmYwGeyh67m2nBD39"


# Service, subtipos em https://schema.org/Service (BroadcastService, CableOrSatelliteService, FinancialProduct e seus
# subtipos de serviço CurrencyConversionService e PaymentService, FoodService, GovernmentService, TaxiService, WebAPI)
# e tipos de serviço sob https://schema.org/LocalBusiness usados com frequência para consultoria e serviços profissionais
# (ProfessionalService, FinancialService, AccountingService, LegalService).
SERVICE_TYPES = frozenset({
    "Service",
    "BroadcastService",
    "CableOrSatelliteService",
    "FinancialProduct",
    "CurrencyConversionService",
    "PaymentService",
    "FoodService",
    "GovernmentService",
    "TaxiService",
    "WebAPI",
    "ProfessionalService",
    "FinancialService",
    "AccountingService",
    "LegalService",
})
OFFER_TYPES = frozenset({"Offer", "AggregateOffer"})


def _check_price_spec(spec: object, why: list[str]) -> None:
    """Valida priceSpecification (objeto ou lista) de Offer de Service: todo preço precisa ser a string '0'."""
    if isinstance(spec, list):
        if not spec:
            why.append("Offer em Service com priceSpecification vazio")
            return
        for item in spec:
            _check_price_spec(item, why)
        return
    if not isinstance(spec, dict):
        why.append(f"Offer em Service com priceSpecification inválido {spec!r}, esperado objeto com price '0'")
        return
    if "price" not in spec:
        why.append("Offer em Service com priceSpecification sem price '0'")
    elif not _is_zero_price(spec["price"]):
        why.append(f"Offer em Service com priceSpecification de price {spec['price']!r}, esperado '0'")
    for bound in ("minPrice", "maxPrice"):
        if bound in spec and not _is_zero_price(spec[bound]):
            why.append(f"Offer em Service com priceSpecification de {bound} {spec[bound]!r}, esperado '0'")
    for nested_key in ("priceSpecification", "priceComponent"):
        if nested_key in spec:
            _check_price_spec(spec[nested_key], why)


def _check_service_offer_entry(entry: object, why: list[str], seen: set[int]) -> None:
    """Valida um objeto ou lista de Offer ligada a Service: price precisa ser '0' e sem priceSpecification paga."""
    if isinstance(entry, list):
        if not entry:
            why.append("offers vazio em Service")
            return
        for item in entry:
            _check_service_offer_entry(item, why, seen)
        return
    if not isinstance(entry, dict):
        why.append(f"offers em Service com valor inválido {entry!r}, esperado objeto com price '0'")
        return
    if id(entry) in seen:
        return
    seen.add(id(entry))
    if "price" not in entry:
        why.append("Offer em Service sem price '0'")
    elif not _is_zero_price(entry["price"]):
        why.append(f"Offer em Service com price {entry['price']!r}, esperado '0'")
    for bound in ("lowPrice", "highPrice"):
        if bound in entry and not _is_zero_price(entry[bound]):
            why.append(f"Offer em Service com {bound} {entry[bound]!r}, esperado '0'")
    if "priceSpecification" in entry:
        _check_price_spec(entry["priceSpecification"], why)
    if "offers" in entry:
        _check_service_offer_entry(entry["offers"], why, seen)


def _is_service_ref(value: object, service_ids: set[str]) -> bool:
    """True se value (objeto ou lista) for um nó de SERVICE_TYPES ou referência @id a um deles na página."""
    if isinstance(value, list):
        return any(_is_service_ref(item, service_ids) for item in value)
    if not isinstance(value, dict):
        return False
    if _has_ld_type(value, *SERVICE_TYPES):
        return True
    ref_id = value.get("@id")
    return isinstance(ref_id, str) and ref_id in service_ids


def _service_offer_problems(service: dict, checked: set[int] | None = None) -> list[str]:
    """Problemas de offers dentro de um nó Service ou subtipo (em qualquer profundidade): todo preço precisa ser '0'."""
    why: list[str] = []
    seen = checked if checked is not None else set()
    for sub in _iter_ld_dicts(service):
        if "offers" in sub:
            _check_service_offer_entry(sub["offers"], why, seen)
        if _has_ld_type(sub, *OFFER_TYPES):
            _check_service_offer_entry(sub, why, seen)
    return why


def _page_service_offer_problems(html: str) -> list[str]:
    """Problemas de offers em nós Service (e subtipos) ou em Offer cujo itemOffered é Service (em qualquer profundidade)."""
    why: list[str] = []
    checked: set[int] = set()
    all_dicts = [d for node in _ld_nodes(html) for d in _iter_ld_dicts(node)]
    service_ids = {
        d["@id"] for d in all_dicts
        if _has_ld_type(d, *SERVICE_TYPES) and isinstance(d.get("@id"), str) and d["@id"].strip()
    }
    for d in all_dicts:
        if _has_ld_type(d, *SERVICE_TYPES):
            why += _service_offer_problems(d, checked)
        if ("itemOffered" in d
                and ("@type" not in d or _has_ld_type(d, *OFFER_TYPES))
                and _is_service_ref(d["itemOffered"], service_ids)):
            _check_service_offer_entry(d, why, checked)
    return why


def check_page_paid_consulting(rel: str, html: str) -> list[str]:
    """#105 (decisão 26/11/2026): sem o link antigo da agenda paga (texto, atributo ou JSON-LD) e sem Service com price != '0'."""
    problems: list[str] = []
    needle = OLD_PAID_SCHEDULE_ID.lower()
    if needle in html.lower() or needle in unescape(html).lower():
        problems.append(f"{rel}: contém o link antigo da consultoria paga ({OLD_PAID_SCHEDULE_ID})")
    for reason in _page_service_offer_problems(html):
        problems.append(f"{rel}: {reason}")
    return problems


def check_paid_consulting(site: Path) -> list[str]:
    """#105 (decisão 26/11/2026): nenhuma página HTML do site montado traz o link antigo nem Service com price diferente de '0'."""
    problems: list[str] = []
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_page_paid_consulting(rel, f.read_text(encoding="utf-8"))
    return problems


def check_consulting(site: Path) -> list[str]:
    """#136 e #105: título, H1 único e JSON-LD Service (provider: a Person, com tipo, nome e url) com Offer gratuita
    (price '0' em BRL) na /consultoria/, sem FAQPage (#108)."""
    rel = CONSULTING_PAGE
    page = site / rel
    if not page.is_file():
        return [f"{rel}: a página de consultoria sumiu do site montado (site-manifest.txt)"]
    html = page.read_text(encoding="utf-8")
    visible = _visible_html(html)
    problems = []
    m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
    title = unescape(m.group(1)).strip() if m else None
    if title != CONSULTING_TITLE:
        problems.append(f"{rel}: o título precisa ser {CONSULTING_TITLE!r}: {title!r}")
    h1s = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", h))).strip()
           for h in re.findall(r"<h1\b[^>]*>(.*?)</h1\s*>", visible, flags=re.S | re.I)]
    if h1s != [CONSULTING_H1]:
        problems.append(f"{rel}: a página precisa de um H1 {CONSULTING_H1!r}, tem {h1s}")

    def offers(n: dict) -> list:
        o = n.get("offers")
        return o if isinstance(o, list) else [o]

    def priced(o) -> bool:
        return (isinstance(o, dict) and _has_ld_type(o, "Offer")
                and _is_zero_price(o.get("price")) and o.get("priceCurrency") == "BRL")

    def person(pr) -> bool:
        return (isinstance(pr, dict) and pr.get("@id") == PERSON_ID and pr.get("@type") == "Person"
                and isinstance(pr.get("name"), str) and bool(pr["name"].strip())
                and pr.get("url") in (SITE_ORIGIN, SITE_ORIGIN + "/"))

    nodes = _ld_nodes(html)
    if not any(n.get("@type") == "Service" and n.get("@id") != PERSON_ID and person(n.get("provider"))
               and any(priced(o) for o in offers(n)) and not _service_offer_problems(n) for n in nodes):
        problems.append(f"{rel}: sem o JSON-LD Service com provider Person ({PERSON_ID!r}, name e url) e Offer "
                        "gratuita com price '0' e priceCurrency BRL")
    if any(n.get("@type") == "FAQPage" for n in nodes):
        problems.append(f"{rel}: FAQPage fora da decisão da #108 (perguntas só em texto visível)")
    return problems


# Home como hub (#136): os últimos artigos, que o scripts/assemble_site.py escreve a partir do build do blog, batem com
# os posts listados (indexáveis e fora dos podcasts) mais recentes segundo o frontmatter, na ordem do blog; e a seção
# de temas da home linka cada página de tema publicada, uma vez. Leitura independente do build: pega lista velha,
# vazia ou com a regra de seleção trocada.
HOME_TOPICS_ID = "temas"


def read_home_latest(consts: Path) -> int | None:
    """Quantos artigos a home lista: HOME_LATEST do consts.ts do blog, a mesma constante que o build usa."""
    m = re.search(r"^export const HOME_LATEST = (\d+);", consts.read_text(encoding="utf-8"), flags=re.M)
    return int(m.group(1)) if m else None


def _post_when(fm: dict[str, str]) -> datetime | None:
    try:
        d = datetime.fromisoformat(fm.get("date", "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _listed_posts(posts: Path) -> tuple[list[tuple[datetime, str]], list[str]]:
    """(data, slug) dos posts listados (indexáveis e fora dos podcasts) e os arquivos com data ilegível."""
    listed, bad = [], []
    for md in sorted(posts.glob("**/*.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        if fm.get("index", "").lower() == "false" or fm.get("type") == "podcast":
            continue
        when = _post_when(fm)
        if when is None:
            bad.append(md.name)
            continue
        listed.append((when, fm.get("slug") or md.stem))
    listed.sort(key=lambda x: (-x[0].timestamp(), x[1]))
    return listed, bad


def expected_home_latest(posts: Path, n: int) -> list[str]:
    """Caminhos dos n posts listados mais recentes: data decrescente e, na mesma data, slug crescente."""
    return [f"/blog/{slug}/" for _, slug in _listed_posts(posts)[0][:n]]


def home_person_problems(html: str) -> list[str]:
    """#105: a Person da home (@id PERSON_ID) traz alternateName com SITE_NAME, knowsAbout e disambiguatingDescription."""
    persons = [n for n in _ld_nodes(html) if _has_ld_type(n, "Person") and n.get("@id") == PERSON_ID]
    if len(persons) != 1:
        return [f"{len(persons)} nós Person com @id {PERSON_ID}, em vez de 1"]
    person = persons[0]
    why: list[str] = []
    alt = person.get("alternateName")
    if isinstance(alt, list):
        if not (all(isinstance(x, str) and x.strip() for x in alt) and SITE_NAME in alt):
            why.append(f"alternateName precisa conter {SITE_NAME!r} (lista de strings não vazias)")
    elif not (isinstance(alt, str) and alt == SITE_NAME):
        why.append(f"alternateName precisa conter {SITE_NAME!r}")
    knows = person.get("knowsAbout")
    if not (isinstance(knows, list) and knows and all(isinstance(x, str) and x.strip() for x in knows)):
        why.append("knowsAbout precisa ser lista não vazia de strings")
    disamb = person.get("disambiguatingDescription")
    if not (isinstance(disamb, str) and disamb.strip()):
        why.append("disambiguatingDescription ausente ou vazio")
    return why


def check_home(site: Path, posts: Path, n: int | None) -> list[str]:
    """#136 e #105: últimos artigos, links das páginas de tema e campos obrigatórios da Person na home montada."""
    rel = "index.html"
    page = site / rel
    if not page.is_file():
        return [f"{rel}: home ausente no site montado"]
    if not n:
        return ["blog-src/src/consts.ts: sem export const HOME_LATEST = <n>; (quantos artigos a home lista)"]
    raw_html = page.read_text(encoding="utf-8")
    html = re.sub(r"<!--(?! /?ultimos-artigos -->).*?-->", "", raw_html, flags=re.S)
    problems = [f"posts: {name} com date ilegível no frontmatter" for name in _listed_posts(posts)[1]]
    m = re.search(r"<!-- ultimos-artigos -->(.*?)<!-- /ultimos-artigos -->", html, flags=re.S)
    got = re.findall(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']*)["']""", m.group(1), flags=re.I) if m else None
    want = expected_home_latest(posts, n)
    if got != want:
        problems.append(f"{rel}: os últimos artigos da home precisam ser {want}, são {got}")
    sec = re.search(r"""<section\b[^>]*\bid\s*=\s*["']%s["'][^>]*>(.*?)</section\s*>""" % HOME_TOPICS_ID, html,
                    flags=re.S | re.I)
    links = re.findall(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']*)["']""", sec.group(1), flags=re.I) if sec else []
    topics = sorted((site / "blog" / "tema").glob("*/index.html"))
    if not topics:
        problems.append("blog/tema/: nenhuma página de tema publicada para a home linkar")
    for t in topics:
        href = f"/blog/tema/{t.parent.name}/"
        if links.count(href) != 1:
            problems.append(f'{rel}: a seção id="{HOME_TOPICS_ID}" precisa linkar {href} uma vez, linka '
                            f"{links.count(href)}")
    person_why = home_person_problems(raw_html)
    if person_why:
        problems.append(f"{rel}: Person da home ({PERSON_ID}) inválida (#105): {'; '.join(person_why)}")
    return problems


def _gh_escape(msg: str) -> str:
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


# Contraste (#119): o botão "Assinar" do blog saiu com color e fundo iguais no modo escuro. Para cada página, as folhas
# de estilo entram na ordem do documento (<link rel="stylesheet"> internos e <style>, fora os só de impressão), os tokens
# de cor vêm de :root/html/body (tema claro) e do seletor do tema escuro ([data-theme=dark], com ou sem aspas, com ou
# sem body/html na frente: o site.js põe o atributo no body e o Astro minifica as aspas), e todo seletor que declara
# color e fundo opaco, nas suas regras ou nas do mesmo seletor sem o estado (:hover, :focus-visible etc. herdam do
# seletor base), precisa de CONTRAST_MIN nos dois temas. background e background-color são o mesmo fundo: vale o
# vencedor da cascata.
# Alarme de integridade sobre o CSS do próprio site: só entram as páginas que carregam SITE_CSS (os mockups do
# portfólio têm paleta própria e ficam de fora). Não avalia cor herdada de outro elemento, fundo
# translúcido, imagem ou gradiente, style="" inline, nem cores fora de hex, rgb(), hsl() e dos nomes de _CSS_NAMED.
# Cascata: !important, especificidade (aproximada: argumentos de :is()/:not() não contam) e ordem do texto, por
# propriedade (color, cor de fundo, imagem de fundo).
CONTRAST_MIN = 4.5
SITE_CSS = "/assets/site.css"
THEMES = ("claro", "escuro")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 128, 0),
              "blue": (0, 0, 255), "yellow": (255, 255, 0), "orange": (255, 165, 0), "gray": (128, 128, 128),
              "grey": (128, 128, 128), "silver": (192, 192, 192), "navy": (0, 0, 128), "purple": (128, 0, 128)}
_CSS_STATE = re.compile(r":(?:hover|focus-visible|focus-within|focus|active|visited)(?![\w-])")
_CSS_STATE_RUN = re.compile(r"(?::(?:hover|focus-visible|focus-within|focus|active|visited)(?![\w-])){2,}")
_CSS_THEME = re.compile(
    r"""(?:(?:html|:root)(?:\s*>\s*|\s+)(?P<tag_a>body)?|(?P<tag_b>html|body|:root)?)\[data-theme=(?:"(?P<a>dark|light)"|'(?P<b>dark|light)'|(?P<c>dark|light))\]"""
)
_THEME_OF = {"dark": "escuro", "light": "claro"}
_CSS_ROOTS = (":root", "html", "body")
_CSS_GROUPS = ("@media", "@supports", "@layer")


def _print_only(media: str) -> bool:
    """A lista de consultas só vale na impressão: toda consulta é print (com ou sem only) e nenhuma é negada."""
    qs = [q.strip().lower() for q in media.split(",") if q.strip()]
    return bool(qs) and all(re.match(r"(?:only\s+)?print\b", q) for q in qs)


def _css_selector(s: str) -> str:
    """Forma canônica de um seletor: espaços colapsados, sem espaço em volta de > + ~, valor de atributo sem aspas."""
    s = re.sub(r"\s+", " ", s.strip())
    s = re.sub(r"\s*([>+~])\s*", r"\1", s)
    return re.sub(r"""\[([\w-]+)([~|^$*]?=)(["'])([\w-]+)\3\]""", r"[\1\2\4]", s)


def _statement_end(css: str, i: int, stop: int) -> int | None:
    """Posição do ";" que encerra uma at-rule de uma linha antes de stop, fora de aspas e parênteses."""
    depth, quote, j = 0, "", i
    while j < stop:
        ch = css[j]
        if quote:
            if ch == "\\" and j + 1 < stop:
                j += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            return j
        j += 1
    return None


def _split_outside(s: str, sep: str) -> list[str]:
    """s partido em sep fora de aspas e parênteses (:is(.a, .b), content:"a;b", url(a;b))."""
    out, cur, depth, quote, i, n = [], [], 0, "", 0, len(s)
    while i < n:
        ch = s[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                cur.append(s[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _find_open_brace(css: str, i: int) -> int:
    """Posição do primeiro "{" a partir de i fora de aspas (respeitando escapes em strings), ou -1."""
    quote, n = "", len(css)
    while i < n:
        ch = css[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            return i
        i += 1
    return -1


def _css_rules(css: str, theme: str | None = None) -> list[tuple[list[str], list[tuple[str, str, bool]], str | None]]:
    """(seletores canônicos, declarações em ordem, tema só) na ordem do texto. @media/@supports/@layer com bloco entram
    (fora os só de impressão); at-rules de uma linha (@charset, @import, @layer a, b;) e @keyframes e afins não."""
    css = _CSS_COMMENT.sub("", css)
    out: list[tuple[list[str], list[tuple[str, str, bool]], str | None]] = []
    i, n = 0, len(css)
    while i < n:
        b = _find_open_brace(css, i)
        if b < 0:
            break
        if css[i:b].lstrip().startswith("@"):
            semi = _statement_end(css, i, b)
            if semi is not None:
                i = semi + 1
                continue
        head, depth, j, quote = css[i:b].strip(), 0, b, ""
        while j < n:
            ch = css[j]
            if quote:
                if ch == "\\" and j + 1 < n:
                    j += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            else:
                depth += {"{": 1, "}": -1}.get(ch, 0)
                if depth == 0:
                    break
            j += 1
        body = css[b + 1:j]
        if head.startswith("@"):
            if head.startswith(_CSS_GROUPS) and not (head.startswith("@media") and _print_only(head[6:])):
                only = theme
                m = re.search(r"prefers-color-scheme\s*:\s*(dark|light)", head)
                if m:
                    only = "escuro" if m.group(1) == "dark" else "claro"
                out += _css_rules(body, only)
        else:
            decls = []
            for d in _split_outside(body, ";"):
                if ":" in d:
                    k, v = d.split(":", 1)
                    k = k.strip()
                    imp = bool(re.search(r"!\s*important\s*$", v, re.I))
                    v = re.sub(r"!\s*important\s*$", "", v.strip(), flags=re.I).strip()
                    decls.append((k if k.startswith("--") else k.lower(), v, imp))
            out.append(([_css_selector(s) for s in _split_outside(head, ",") if s.strip()], decls, theme))
        i = j + 1
    return out


def _split_top(v: str) -> list[str]:
    """Tokens separados por espaço fora de parênteses."""
    out, depth, cur = [], 0, ""
    for ch in v:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch.isspace() and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + ([cur] if cur else [])


def _parse_var(v: str) -> tuple[str, str | None] | None:
    """Se v for uma única chamada var(--nome[, fallback]) com parênteses balanceados, devolve (nome, fallback)."""
    if v[:4].lower() != "var(" or not v.endswith(")"):
        return None
    depth, quote, i, n = 0, "", 3, len(v)
    while i < n:
        ch = v[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                if i != n - 1:
                    return None
                break
        i += 1
    if depth != 0:
        return None
    m = re.fullmatch(r"\s*(--[\w-]+)\s*(?:,\s*(.*))?", v[4:-1], re.S)
    if not m:
        return None
    return m.group(1), m.group(2)


def css_color(value: str | None, tokens: dict[str, str], _depth: int = 0) -> tuple[int, int, int] | None:
    """Cor opaca em RGB, resolvendo var() pelos tokens; None para translúcido, desconhecido ou não-cor."""
    if value is None or _depth > 16:
        return None
    v = value.strip()
    parsed = _parse_var(v)
    if parsed is not None:
        name, fallback = parsed
        if name in tokens:
            return css_color(tokens[name], tokens, _depth + 1)
        return css_color(fallback, tokens, _depth + 1)
    if v.lower() in _CSS_NAMED:
        return _CSS_NAMED[v.lower()]
    m = re.fullmatch(r"#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) == 8:
            if h[6:].lower() != "ff":
                return None
            h = h[:6]
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,?\s*(\d+)\s*,?\s*(\d+)\s*(?:[,/]\s*(\d+(?:\.\d+)?|\.\d+)(%?))?\s*\)", v, re.I)
    if m:
        if m.group(4) is not None and float(m.group(4)) != (100.0 if m.group(5) else 1.0):
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    num = r"(\d+(?:\.\d+)?|\.\d+)"
    m = re.fullmatch(rf"hsla?\(\s*{num}(?:deg)?\s*,?\s*{num}%\s*,?\s*{num}%\s*(?:[,/]\s*{num}(%?))?\s*\)", v, re.I)
    if m:
        if m.group(4) is not None and float(m.group(4)) != (100.0 if m.group(5) else 1.0):
            return None
        h, s, l = float(m.group(1)) % 360 / 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
    return None


def _css_background(raw: str, tokens: dict[str, str]) -> tuple[int, int, int] | None:
    """Cor do fundo de um valor de background/background-color (atalho com um só componente de cor)."""
    if "url(" in raw.lower() or "gradient(" in raw.lower():
        return None
    whole = css_color(raw, tokens)
    if whole is not None:
        return whole
    found = [c for c in (css_color(tok, tokens) for tok in _split_top(raw)) if c is not None]
    return found[0] if len(found) == 1 else None


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Razão de contraste da WCAG 2 entre duas cores RGB."""
    def lum(rgb):
        c = [x / 255 for x in rgb]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _theme_prefix(s: str) -> tuple[str | None, str, bool]:
    """(tema, elemento, só o elemento do tema) de um seletor:
    - [data-theme=X] Y (com ou sem ancestral html/:root antes): (X, Y, False);
    - [data-theme=X], body[data-theme=X], html[data-theme=X], :root[data-theme=X] ou html body[data-theme=X] sozinho:
      (X, "html" ou "body", True). É onde moram os tokens do tema;
    - [data-theme=X].y composto: (X, o seletor inteiro, False). O atributo está no próprio elemento, que só existe
      nesse tema e não se mistura com .y;
    - :root puro sem prefixo: (None, "html", False), na mesma chave que html e html[data-theme=X];
    - sem prefixo: (None, s, False)."""
    m = _CSS_THEME.match(s)
    if not m:
        return None, ("html" if s == ":root" else s), False
    theme = _THEME_OF[m.group("a") or m.group("b") or m.group("c")]
    rest = s[m.end():]
    if not rest:
        tag = m.group("tag_a") or m.group("tag_b") or "body"
        return theme, ("html" if tag == ":root" else tag), True
    if rest[0] not in " >+~":
        return theme, s, False
    return theme, rest.lstrip(" >+~"), False


def _specificity(sel: str) -> tuple[int, int, int]:
    """(ids, classes/atributos/pseudo-classes, tipos/pseudo-elementos). Aproximação: o argumento de :is()/:not()/
    :where()/:has() não conta, e a pseudo-classe conta como uma classe."""
    s = re.sub(r"\[[^\]]*\]", "[]", sel)
    while re.search(r"\([^()]*\)", s):
        s = re.sub(r"\([^()]*\)", "", s)
    ids = len(re.findall(r"#[\w-]+", s))
    cls = len(re.findall(r"\.[\w-]+|\[\]|(?<!:):(?!:)[\w-]+", s))
    types = len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][\w-]*", s)) + len(re.findall(r"::[\w-]+", s))
    return ids, cls, types


def _bg_longhands(k: str, v: str) -> list[tuple[str, object]]:
    """O que cada declaração de fundo define: a cor ("bg", valor cru) e se há imagem ou gradiente ("img", bool).
    background define os dois (sem imagem, o background-image volta a none)."""
    low = v.lower()
    if k == "background":
        return [("bg", v), ("img", "url(" in low or "gradient(" in low)]
    if k == "background-image":
        return [("img", low.strip() not in ("none", "initial", "unset"))]
    return [("bg", v)]


def _canon_states(s: str) -> str:
    """Ordena e deduplica sequências contíguas de pseudo-classes de estado (:hover:active == :active:hover)."""
    return _CSS_STATE_RUN.sub(lambda m: "".join(sorted(dict.fromkeys(_CSS_STATE.findall(m.group(0))))), s)


def _state_subsets(s: str) -> list[str]:
    """Todos os seletores com subconjunto dos estados de s (base, intermediários e o próprio s)."""
    ms = list(_CSS_STATE.finditer(s))
    if not ms:
        return []
    if len(ms) > 6:
        return [_CSS_STATE.sub("", s), s]
    out = []
    for mask in range(1 << len(ms)):
        parts, pos = [], 0
        for idx, m in enumerate(ms):
            parts.append(s[pos:m.start()])
            if mask & (1 << idx):
                parts.append(m.group(0))
            pos = m.end()
        parts.append(s[pos:])
        out.append("".join(parts))
    return out


def _css_pairs(sheets: list[str]) -> dict[str, dict[str, tuple]]:
    """Por tema, os seletores com color e fundo opacos resolvidos: {tema: {seletor: (color, fundo, color cru, fundo cru)}}.
    Cascata do navegador para color, cor de fundo e imagem de fundo: !important, depois especificidade, depois ordem.
    Tokens seguem a mesma cascata por camada, com body vencendo :root/html (mais próximo do elemento).
    O seletor com estado (:hover, :hover:active etc.) herda do seletor base e dos estados intermediários."""
    rules = [r for css in sheets for r in _css_rules(css)]
    tok_won: dict[tuple[str, str], dict[str, tuple]] = {}
    tok_order = 0
    for sels, decls, only in rules:
        vs = [(k, v, imp) for k, v, imp in decls if k.startswith("--")]
        for s in sels if vs else ():
            theme, elem, root = _theme_prefix(s)
            if theme is None and s in _CSS_ROOTS:
                layer = "body" if s == "body" else "html"
                themes = THEMES if only is None else (only,)
            elif root and only in (None, theme):
                layer = elem
                themes = (theme,)
            else:
                continue
            spec = _specificity(s)
            for t in themes:
                slot = tok_won.setdefault((t, layer), {})
                for k, v, imp in vs:
                    tok_order += 1
                    cand = (imp, spec, tok_order, v)
                    if k not in slot or cand[:3] > slot[k][:3]:
                        slot[k] = cand
    html_tokens = {t: {k: c[3] for k, c in tok_won.get((t, "html"), {}).items()} for t in THEMES}
    tokens = {t: {**html_tokens[t], **{k: c[3] for k, c in tok_won.get((t, "body"), {}).items()}} for t in THEMES}
    # {(tema, elemento): {propriedade: (importante, especificidade, ordem, valor)}}, já com o vencedor de cada uma.
    won: dict[tuple[str, str], dict[str, tuple]] = {}
    order = 0
    for sels, decls, only in rules:
        props = []
        for k, v, imp in decls:
            if k == "color":
                props.append(("color", v, imp))
            elif k in ("background", "background-color", "background-image"):
                props += [(p, x, imp) for p, x in _bg_longhands(k, v)]
        for s in sels if props else ():
            theme, elem, _ = _theme_prefix(s)
            spec = _specificity(s)
            themes = THEMES if only is None else (only,)
            for t in themes if theme is None else tuple(x for x in themes if x == theme):
                slot = won.setdefault((t, elem), {})
                for p, x, imp in props:
                    order += 1
                    cand = (imp, spec, order, x)
                    if p not in slot or cand[:3] > slot[p][:3]:
                        slot[p] = cand
    by_canon: dict[tuple[str, str], list[dict[str, tuple]]] = {}
    for (t, s), d in won.items():
        by_canon.setdefault((t, _canon_states(s)), []).append(d)
    out: dict[str, dict[str, tuple]] = {t: {} for t in THEMES}
    for (t, s), d in won.items():
        subsets = _state_subsets(s)
        if subsets:
            full: dict[str, tuple] = {}
            for key in dict.fromkeys(_canon_states("html" if sub == ":root" else sub) for sub in subsets):
                for sub_d in by_canon.get((t, key), ()):
                    for p, cand in sub_d.items():
                        if p not in full or cand[:3] > full[p][:3]:
                            full[p] = cand
        else:
            full = d
        if "color" not in full or "bg" not in full or (full.get("img") and full["img"][3]):
            continue
        c_raw, b_raw = full["color"][3], full["bg"][3]
        base_sel = _CSS_STATE.sub("", s)
        toks = html_tokens[t] if base_sel in ("html", ":root") else tokens[t]
        fg, bg = css_color(c_raw, toks), _css_background(b_raw, toks)
        if fg is not None and bg is not None:
            out[t][s] = (fg, bg, c_raw, b_raw)
    return out


def css_cascade(sheets: list[str]) -> dict[str, dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]]]:
    """Por tema, os seletores com color e fundo opacos resolvidos: {tema: {seletor: (color, fundo)}}."""
    return {t: {s: p[:2] for s, p in ps.items()} for t, ps in _css_pairs(sheets).items()}


def contrast_problems(sheets: list[str], minimum: float = CONTRAST_MIN) -> list[tuple[str, str, float, str, str]]:
    """(seletor, tema, razão, color, fundo) de cada par abaixo do mínimo, em ordem estável."""
    pairs = _css_pairs(sheets)
    out = []
    for t, s in sorted((t, s) for t in THEMES for s in pairs[t]):
        fg, bg, fg_raw, bg_raw = pairs[t][s]
        r = contrast_ratio(fg, bg)
        if r < minimum:
            out.append((s, t, r, fg_raw, bg_raw))
    return out


class _Sheets(HTMLParser):
    """<link rel="stylesheet"> (sem alternate) e <style>, em ordem, fora os só de impressão. O conteúdo de <script> é
    texto para o parser, então um "<style>" dentro de uma string de JS não conta."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []  # ("link", href) ou ("style", css)
        self._style: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        if _print_only(a.get("media", "") or "all"):
            return
        if tag == "link":
            rel = a.get("rel", "").lower().split()
            if "stylesheet" in rel and "alternate" not in rel and a.get("href"):
                self.items.append(("link", a["href"]))
        elif tag == "style":
            self._style = []

    def handle_data(self, data):
        if self._style is not None:
            self._style.append(data)

    def handle_endtag(self, tag):
        if tag == "style" and self._style is not None:
            self.items.append(("style", "".join(self._style)))
            self._style = None


def _page_sheets(site: Path, f: Path, html: str) -> tuple[list[str], bool]:
    """As folhas da página, na ordem do documento (<link rel=stylesheet> internos, lidos do _site, e <style>), e se
    uma delas é o SITE_CSS."""
    p = _Sheets()
    p.feed(html)
    p.close()
    sheets, uses_site_css = [], False
    for kind, value in p.items:
        if kind == "style":
            sheets.append(value)
            continue
        path = urlsplit(value)
        if path.scheme or path.netloc:
            continue
        target = (site / path.path.lstrip("/")) if path.path.startswith("/") else (f.parent / path.path)
        if target.is_file():
            sheets.append(target.read_text(encoding="utf-8"))
            uses_site_css |= target.resolve() == (site / SITE_CSS.lstrip("/")).resolve()
    return sheets, uses_site_css


def check_contrast(site: Path) -> list[str]:
    """Contraste de color sobre fundo nas páginas do _site que usam o SITE_CSS, uma mensagem por conjunto de folhas."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for f in html_files(site):
        sheets, uses_site_css = _page_sheets(site, f, f.read_text(encoding="utf-8"))
        if uses_site_css:
            sheets = tuple(sheets)
            groups.setdefault(sheets, []).append(f.relative_to(site).as_posix())
    problems = []
    for sheets, pages in groups.items():
        extra = len(pages) - 1
        tail = f" (e mais {extra} página{'s' if extra > 1 else ''} com as mesmas folhas)" if extra else ""
        for sel, theme, r, fg, bg in contrast_problems(list(sheets)):
            problems.append(f"{pages[0]}: contraste {r:.2f}:1 abaixo de {CONTRAST_MIN}:1 no tema {theme} em "
                            f"'{sel}' (color {fg}, fundo {bg}){tail}")
    return problems


# /ferramentas/llm-ranking/ (issues #113 e #166): a página é gerada no build (scripts/build_llm_ranking.py) a partir do snapshot da Arena.
# O build já falha sem snapshot válido; esta regra é o alarme de que a página publicada saiu como deve: a tabela com
# LLM_RANKING_ROWS linhas legíveis sem JavaScript, a data dos dados (que é o lastmod do sitemap), o JSON-LD com a mesma
# data, a atribuição da CC BY 4.0 e nenhum marcador do template sobrando.
LLM_RANKING_ROWS = 100
LLM_RANKING_COLS = 7
LLM_DATA_META = "dados-publicados-em"
LLM_VERSAO_META = "dados-versao"  # sha256 do snapshot; o snapshot da KVM o compara para saber se a página está atrás
LLM_DATASET_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
LLM_LICENSE_PREFIX = "https://creativecommons.org/licenses/by/4.0/"


class _LlmRanking(HTMLParser):
    """Linhas e células da tabela id="ranking" (tbody), as metas DATA_META, os JSON-LD e os links da página."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables = 0
        self.rows: list[list[str]] = []
        self.data_dates: list[str] = []
        self.jsonld: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.versions: list[str] = []
        self._depth = 0  # profundidade de <table> dentro da tabela do ranking (0 = fora dela)
        self._tbody = False
        self._cell: list[str] | None = None
        self._script: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "table":
            if self._depth:
                self._depth += 1
            elif a.get("id") == "ranking":
                self.tables += 1
                self._depth = 1
        elif self._depth == 1 and tag == "tbody":
            self._tbody = True
        elif self._depth == 1 and self._tbody and tag == "tr":
            self.rows.append([])
        elif self._depth == 1 and self._tbody and tag == "td" and self.rows:
            self._cell = []
        elif tag == "meta" and a.get("name", "").lower() == LLM_DATA_META:
            self.data_dates.append(a.get("content", "").strip())
        elif tag == "meta" and a.get("name", "").lower() == LLM_VERSAO_META:
            self.versions.append(a.get("content", ""))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._script = []
        if tag == "a":
            self.links.append((a.get("href", ""), a.get("rel", "")))

    def handle_startendtag(self, tag, attrs):
        if tag == "meta":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
            if not self._depth:
                self._tbody = False
        elif tag == "tbody" and self._depth == 1:
            self._tbody = False
        elif tag == "td" and self._depth == 1 and self._cell is not None:
            self.rows[-1].append("".join(self._cell).strip())
            self._cell = None
        elif tag == "script" and self._script is not None:
            self.jsonld.append("".join(self._script))
            self._script = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._script is not None:
            self._script.append(data)


def check_llm_ranking(site: Path) -> list[str]:
    rel = LLM_RANKING_PAGE
    f = site / rel
    if not f.is_file():
        return [f"{rel}: página não existe no _site"]
    html = f.read_text(encoding="utf-8")
    problems = []
    if "<!--LLM:" in html:
        problems.append(f"{rel}: marcador do template sobrando (<!--LLM:...-->): o build_llm_ranking.py não rodou?")
    p = _LlmRanking()
    p.feed(html)
    p.close()
    if p.tables != 1:
        problems.append(f'{rel}: esperava uma <table id="ranking">, achou {p.tables}')
    elif len(p.rows) != LLM_RANKING_ROWS:
        problems.append(f"{rel}: a tabela tem {len(p.rows)} linhas no tbody, esperado {LLM_RANKING_ROWS}")
    else:
        for i, row in enumerate(p.rows, 1):
            if len(row) != LLM_RANKING_COLS or not all(row):
                problems.append(f"{rel}: linha {i} da tabela com {len(row)} células ou célula vazia")
                break
            if row[0] != str(i):
                problems.append(f"{rel}: linha {i} da tabela com a posição {row[0]!r}")
                break
    data = None
    if len(p.data_dates) != 1:
        problems.append(f"{rel}: esperava uma meta {LLM_DATA_META}, achou {len(p.data_dates)}")
    else:
        try:
            data = datetime.strptime(p.data_dates[0], "%Y-%m-%d").date().isoformat()
        except ValueError:
            problems.append(f"{rel}: meta {LLM_DATA_META} inválida: {p.data_dates[0]!r}")
        if data and data != p.data_dates[0]:
            problems.append(f"{rel}: meta {LLM_DATA_META} fora do formato AAAA-MM-DD: {p.data_dates[0]!r}")
            data = None
    if len(p.versions) != 1:
        problems.append(f"{rel}: esperava uma meta {LLM_VERSAO_META}, achou {len(p.versions)}")
    elif not re.fullmatch(r"[0-9a-f]{64}", p.versions[0]):
        problems.append(f"{rel}: meta {LLM_VERSAO_META} inválida (esperado sha256 em hexadecimal minúsculo): "
                        f"{p.versions[0]!r}")
    modified = []
    for raw in p.jsonld:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            problems.append(f"{rel}: JSON-LD inválido")
            continue
        nodes = doc.get("@graph", [doc]) if isinstance(doc, dict) else []
        if not isinstance(nodes, list):
            nodes = []
        modified += [n.get("dateModified") for n in nodes if isinstance(n, dict) and n.get("@type") == "WebPage"]
    if len(modified) != 1:
        problems.append(f"{rel}: esperava um WebPage no JSON-LD, achou {len(modified)}")
    elif data and modified[0] != data:
        problems.append(f"{rel}: dateModified do JSON-LD ({modified[0]!r}) diferente da data dos dados ({data!r})")
    hrefs = [h for h, _ in p.links]
    if LLM_DATASET_URL not in hrefs:
        problems.append(f"{rel}: sem o link da fonte ({LLM_DATASET_URL}), exigido pela CC BY 4.0")
    if not any(h.startswith(LLM_LICENSE_PREFIX) and "license" in r.lower().split() for h, r in p.links):
        problems.append(f'{rel}: sem o link rel="license" da CC BY 4.0')
    return problems


class _LiveIds(HTMLParser):
    """Atributos id fora de comentário, <template>, <noscript> e elementos hidden."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.ids: set[str] = set()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        inert = any(i for _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        if not inert and a.get("id", "").strip():
            self.ids.add(a["id"].strip())
        if tag not in VOID_TAGS:
            self.stack.append((tag, tag in ("template", "noscript") or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


class _LegalFooter(HTMLParser):
    """Links <a> visíveis dentro de <footer>: (href, tem data-consent-open, tokens de rel)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, é footer visível, torna inerte)
        self.footers = 0
        self.links: list[tuple[str, bool, list[str]]] = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        inert = any(i for _, _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        if tag == "footer" and not inert:
            self.footers += 1
        if tag == "a" and not inert and any(is_f for _, is_f, _ in self.stack):
            href = a.get("href", "").strip()
            if href:
                self.links.append((href, "data-consent-open" in a, a.get("rel", "").lower().split()))
        if tag not in VOID_TAGS:
            self.stack.append((tag, tag == "footer" and not inert, tag in ("template", "noscript") or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def check_legal_footer(rel: str, html: str) -> list[str]:
    """#146: todo rodapé (fora os mockups do portfólio) leva Privacidade, Termos e Cookies."""
    if rel.startswith(TITLE_EXEMPT_PREFIXES):
        return []
    p = _LegalFooter()
    p.feed(html)
    p.close()
    if p.footers == 0:
        return [f"{rel}: página sem <footer> com os links legais"]
    problems: list[str] = []
    has_ga = GA4_TAG in html
    for href in LEGAL_FOOTER_HREFS:
        matches = [(c, r) for h, c, r in p.links if h == href]
        if len(matches) != 1:
            problems.append(f"{rel}: rodapé precisa do link legal {href} uma vez, tem {len(matches)}")
            continue
        has_open, rels = matches[0]
        if "nofollow" in rels:
            problems.append(f"{rel}: rodapé com o link legal {href} em rel=nofollow")
        if href == "/privacidade/#cookies":
            if has_ga and not has_open:
                problems.append(f"{rel}: link Cookies no rodapé de página com GA4 precisa de data-consent-open")
            elif not has_ga and has_open:
                problems.append(f"{rel}: link Cookies no rodapé de página sem GA4 não leva data-consent-open")
        elif has_open:
            problems.append(f"{rel}: link legal {href} não leva data-consent-open")
    return problems


def check_legal(site: Path) -> list[str]:
    """#146: /privacidade/ e /termos/ existem no build, são indexáveis, estão no sitemap e todo rodapé tem os três links."""
    problems: list[str] = []
    entries = _sitemap(site, "sitemap-site.xml", "urlset", "url", problems)
    sitemap_locs = {loc for loc, _ in entries} if entries is not None else None
    for rel, url, want_title, want_h1 in LEGAL_SPECS:
        if sitemap_locs is not None and SITE_ORIGIN + url not in sitemap_locs:
            problems.append(f"sitemap-site.xml: falta página legal {SITE_ORIGIN}{url}")
        page = site / rel
        if not page.is_file():
            problems.append(f"{rel}: página legal ausente no site montado (site-manifest.txt)")
            continue
        html = page.read_text(encoding="utf-8")
        p = _Page()
        p.feed(html)
        p.close()
        if any(_robots_noindex(r) for r in p.robots):
            problems.append(f"{rel}: página legal não pode ter noindex")
        if not p.canonicals or not canonical_is_self(p.canonicals, url):
            problems.append(f"{rel}: página legal precisa de canonical próprio ({SITE_ORIGIN}{url}), tem {p.canonicals}")
        visible = _visible_html(html)
        m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
        title = unescape(m.group(1)).strip() if m else None
        if title != want_title:
            problems.append(f"{rel}: o título precisa ser {want_title!r}: {title!r}")
        h1s = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", h))).strip()
               for h in re.findall(r"<h1\b[^>]*>(.*?)</h1\s*>", visible, flags=re.S | re.I)]
        if h1s != [want_h1]:
            problems.append(f"{rel}: a página precisa de um H1 {want_h1!r}, tem {h1s}")
        if rel == "privacidade/index.html":
            ids = _LiveIds()
            ids.feed(html)
            ids.close()
            for anchor in PRIVACY_ANCHORS:
                if anchor not in ids.ids:
                    problems.append(f"{rel}: falta a âncora #{anchor}")
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_legal_footer(rel, f.read_text(encoding="utf-8"))
    return problems


LLMS_TXT_NAME = "llms.txt"
LLMS_REQUIRED_SECTIONS = ("Consultoria", "Ferramentas", "Temas", "Portfólio e Expert Insights")
_MD_LINK_RE = re.compile(r"\[((?:\\.|[^\]])+)\]\(([^)\s]+)\)")


def check_llms_txt(site: Path, posts: Path) -> list[str]:
    """#174: /llms.txt existe no _site, segue o formato e aponta só para páginas existentes e indexáveis."""
    llms = site / LLMS_TXT_NAME
    if llms.is_symlink():
        return [f"{LLMS_TXT_NAME}: link simbólico não permitido no _site"]
    if not llms.is_file():
        return [f"{LLMS_TXT_NAME}: arquivo ausente no _site (rode scripts/build_llms_txt.py)"]
    try:
        text = llms.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [f"{LLMS_TXT_NAME}: UTF-8 inválido: {e}"]
    if not text.strip():
        return [f"{LLMS_TXT_NAME}: arquivo vazio"]

    problems: list[str] = []
    if OLD_DOMAIN in text.lower():
        problems.append(f"{LLMS_TXT_NAME}: menciona o domínio antigo ({OLD_DOMAIN})")

    lines = text.splitlines()
    if not lines or not lines[0].startswith("# ") or not lines[0][2:].strip():
        problems.append(f"{LLMS_TXT_NAME}: precisa abrir com título H1 (# ...)")
    if not any(ln.startswith("> ") and ln[2:].strip() for ln in lines):
        problems.append(f"{LLMS_TXT_NAME}: precisa de citação curta (> ...)")

    section_links: dict[str, list[str]] = {}
    current_section: str | None = None
    all_urls: list[str] = []
    for lineno, line in enumerate(lines, 1):
        if line.startswith("## "):
            current_section = line[3:].strip()
            section_links.setdefault(current_section, [])
            continue
        matches = list(_MD_LINK_RE.finditer(line))
        for m in matches:
            url = m.group(2)
            all_urls.append(url)
            if current_section is not None:
                section_links[current_section].append(url)
        stripped = line.lstrip()
        if stripped.startswith("- ") and matches:
            tail = line[matches[0].end():]
            if not tail.startswith(": ") or not tail[2:].strip():
                problems.append(f"{LLMS_TXT_NAME}:{lineno}: item sem descrição em uma linha")

    for req in LLMS_REQUIRED_SECTIONS:
        if req not in section_links:
            problems.append(f"{LLMS_TXT_NAME}: falta a seção obrigatória '## {req}'")
        elif not section_links[req]:
            problems.append(f"{LLMS_TXT_NAME}: seção '## {req}' sem links")
    if not all_urls:
        problems.append(f"{LLMS_TXT_NAME}: nenhum link encontrado")
        return problems

    # Frontmatter dos posts para dupla verificação de index: false e podcast.
    non_indexable_slugs: set[str] = set()
    if posts.is_dir():
        for md in sorted(posts.glob("**/*.md")):
            fm = frontmatter(md.read_text(encoding="utf-8"))
            slug = fm.get("slug") or md.stem
            if fm.get("index", "").lower() == "false" or fm.get("type", "").lower() == "podcast":
                non_indexable_slugs.add(slug)

    robots_path = site / "robots.txt"
    robots_txt = robots_path.read_text(encoding="utf-8") if robots_path.is_file() else ""

    pairs = [(SITE_ORIGIN + "/", u) for u in dict.fromkeys(all_urls)]
    resolved = dict(zip([u for _, u in pairs], resolve_urls(pairs)))

    for url in dict.fromkeys(all_urls):
        res = resolved[url]
        u = urlsplit(url)
        if _old_host(res):
            problems.append(f"{LLMS_TXT_NAME}: link para o domínio antigo: {url}")
            continue
        path = _internal(res)
        if not res or u.scheme != "https" or u.netloc != SITE_HOST or res["protocol"] != "https:" or path is None or u.query or u.fragment:
            problems.append(f"{LLMS_TXT_NAME}: URL precisa ser absoluta e canônica em {SITE_ORIGIN} (sem query/fragment): {url}")
            continue
        ok, dir_without_slash = _resolve(site, path)
        if dir_without_slash:
            problems.append(f"{LLMS_TXT_NAME}: link para pasta sem barra final: {url}")
            continue
        if not ok:
            problems.append(f"{LLMS_TXT_NAME}: aponta para página que não existe no _site: {url}")
            continue
        target = (site / path.lstrip("/") / "index.html") if path.endswith("/") else (site / path.lstrip("/"))
        rel = target.relative_to(site).as_posix()
        if target.suffix.lower() not in HTML_SUFFIXES:
            problems.append(f"{LLMS_TXT_NAME}: link não aponta para página HTML: {url}")
            continue
        if rel == "404.html":
            problems.append(f"{LLMS_TXT_NAME}: lista a página 404: {url}")
            continue
        for agent in ("*", "googlebot", "bingbot"):
            if robots_blocks(robots_txt, path, agent):
                problems.append(f"{LLMS_TXT_NAME}: página bloqueada pelo robots.txt ({agent}): {url}")
                break
        p = _Page()
        p.feed(target.read_text(encoding="utf-8"))
        p.close()
        if any(_robots_noindex(r) for r in p.robots):
            problems.append(f"{LLMS_TXT_NAME}: lista página com noindex: {url}")
        if not canonical_is_self(p.canonicals, path):
            problems.append(f"{LLMS_TXT_NAME}: página com canonical para outra URL: {url}")
        if path.startswith(BLOG_PREFIX):
            if path.startswith(BLOG_PREFIX + "pagina/"):
                problems.append(f"{LLMS_TXT_NAME}: lista página de paginação do blog: {url}")
            parts = path[len(BLOG_PREFIX):].strip("/").split("/")
            if len(parts) == 1 and parts[0]:
                if parts[0] in BLOG_PRIVATE_PAGES:
                    problems.append(f"{LLMS_TXT_NAME}: lista página privada da newsletter: {url}")
                if parts[0] in non_indexable_slugs:
                    problems.append(f"{LLMS_TXT_NAME}: lista post não indexável ou podcast: {url}")
    return problems


class _CoursesLinks(HTMLParser):
    """Links <a> visíveis no <nav> principal (fora do <footer>), no <main> e no <footer>: (href, tokens de rel)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, bool]] = []  # (tag, bloco, inerte)
        self.footers = 0
        self.found: dict[str, list[tuple[str, list[str]]]] = {"nav": [], "main": [], "footer": []}

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        inert = any(i for _, _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        in_footer = any(b == "footer" for _, b, _ in self.stack)
        if tag == "footer" and not inert:
            self.footers += 1
        if tag == "a" and not inert:
            href = a.get("href", "").strip()
            if href:
                rels = a.get("rel", "").lower().split()
                for where in dict.fromkeys(b for _, b, _ in self.stack if b):
                    self.found[where].append((href, rels))
        if tag not in VOID_TAGS:
            block = ("footer" if tag == "footer" else
                     "main" if tag == "main" else
                     "nav" if tag == "nav" and not in_footer else None)
            self.stack.append((tag, block if not inert else None,
                               tag in ("template", "noscript") or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def check_courses_footer(rel: str, html: str) -> list[str]:
    """#166: /cursos/ entra em todos os rodapés (fora /cursos/ e mockups) e não entra no menu principal."""
    if rel.startswith(TITLE_EXEMPT_PREFIXES):
        return []
    p = _CoursesLinks()
    p.feed(html)
    p.close()
    problems: list[str] = []
    if any(h in (COURSES_URL, SITE_ORIGIN + COURSES_URL) for h, _ in p.found["nav"]):
        problems.append(f"{rel}: {COURSES_URL} não entra no menu principal")
    if rel == COURSES_PAGE:
        return problems
    if p.footers == 0:
        return problems + [f"{rel}: página sem <footer> com o link {COURSES_URL}"]
    matches = [r for h, r in p.found["footer"] if h == COURSES_URL]
    if len(matches) != 1:
        problems.append(f"{rel}: rodapé precisa do link {COURSES_URL} uma vez, tem {len(matches)}")
    elif "nofollow" in matches[0]:
        problems.append(f"{rel}: rodapé com o link {COURSES_URL} em rel=nofollow")
    return problems


def check_courses(site: Path) -> list[str]:
    """#166: /cursos/ existe com noindex, canonical próprio, título e link para /cursos/chatgpt-no-dia-a-dia, fora do sitemap;
    /cursos/ está nos rodapés do site e fora do menu principal."""
    problems: list[str] = []
    entries = _sitemap(site, "sitemap-site.xml", "urlset", "url", problems)
    sitemap_locs = {loc for loc, _ in entries} if entries is not None else None
    if sitemap_locs is not None and SITE_ORIGIN + COURSES_URL in sitemap_locs:
        problems.append(f"sitemap-site.xml: {SITE_ORIGIN}{COURSES_URL} tem noindex e não entra no sitemap")
    page = site / COURSES_PAGE
    if not page.is_file():
        problems.append(f"{COURSES_PAGE}: página de cursos ausente no site montado (site-manifest.txt)")
    else:
        html = page.read_text(encoding="utf-8")
        p = _Page()
        p.feed(html)
        p.close()
        if not any(_robots_noindex(r) for r in p.robots):
            problems.append(f"{COURSES_PAGE}: página de cursos precisa de noindex enquanto o curso está em atualização")
        if not p.canonicals or not canonical_is_self(p.canonicals, COURSES_URL):
            problems.append(f"{COURSES_PAGE}: página de cursos precisa de canonical próprio "
                            f"({SITE_ORIGIN}{COURSES_URL}), tem {p.canonicals}")
        visible = _visible_html(html)
        m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
        title = unescape(m.group(1)).strip() if m else None
        if title != COURSES_TITLE:
            problems.append(f"{COURSES_PAGE}: o título precisa ser {COURSES_TITLE!r}: {title!r}")
        links = _CoursesLinks()
        links.feed(html)
        links.close()
        course = [r for h, r in links.found["main"] if h == CHATGPT_COURSE_URL]
        if len(course) != 1:
            problems.append(f"{COURSES_PAGE}: página de cursos precisa do link {CHATGPT_COURSE_URL} uma vez em <main>, "
                            f"tem {len(course)}")
        elif "nofollow" in course[0]:
            problems.append(f"{COURSES_PAGE}: link {CHATGPT_COURSE_URL} em rel=nofollow")
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_courses_footer(rel, f.read_text(encoding="utf-8"))
    return problems


def _is_youtube_url(href: str) -> bool:
    """True quando o href aponta para um domínio do YouTube (com ou sem subdomínio ou ponto final DNS)."""
    try:
        u = urlsplit(href.strip())
        host = (u.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in YOUTUBE_HOSTS or any(host.endswith("." + h) for h in YOUTUBE_HOSTS)


def check_youtube_footer(rel: str, html: str) -> list[str]:
    """#166: todo link de YouTube visível dentro de <footer> (fora mockups) aponta para YOUTUBE_FOOTER_URL."""
    if rel.startswith(TITLE_EXEMPT_PREFIXES):
        return []
    p = _LegalFooter()
    p.feed(html)
    p.close()
    problems: list[str] = []
    for href, _, _ in p.links:
        if _is_youtube_url(href) and href != YOUTUBE_FOOTER_URL:
            problems.append(
                f"{rel}: link de YouTube no rodapé precisa apontar para {YOUTUBE_FOOTER_URL}, aponta para {href}"
            )
    return problems


def check_youtube(site: Path) -> list[str]:
    """#166: todo link de YouTube dentro de <footer> nas páginas do site montado aponta para YOUTUBE_FOOTER_URL."""
    problems: list[str] = []
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_youtube_footer(rel, f.read_text(encoding="utf-8"))
    return problems


def check_book(site: Path) -> list[str]:
    """#183: a página do livro traz @graph com WebPage e Book gratuito ligado à Person, workExample PDF e Kindle (ASIN) e sem offers nem FAQPage."""
    rel = BOOK_PAGE
    page = site / rel
    if not page.is_file():
        return [f"{rel}: a página do livro sumiu do site montado (site-manifest.txt)"]
    html = page.read_text(encoding="utf-8")
    problems: list[str] = []
    nodes = _ld_nodes(html)
    if any(n.get("@type") == "FAQPage" for n in nodes):
        problems.append(f"{rel}: FAQPage fora da decisão da #183 (perguntas só em texto visível)")
    books = [(n, gnodes, graph) for gnodes, graph in _ld_blocks(html) for n in gnodes
             if isinstance(n, dict) and n.get("@type") == "Book"]
    if len(books) != 1:
        problems.append(f"{rel}: esperado um nó Book no JSON-LD, achados {len(books)}")
        return problems
    book, gnodes, graph = books[0]

    def ref(v: object) -> object:
        return v.get("@id") if isinstance(v, dict) else None

    book_url = SITE_ORIGIN + BOOK_URL
    book_id = book_url + "#book"
    website_id = SITE_ORIGIN + "/#website"
    if not graph:
        problems.append(f"{rel}: o nó Book precisa estar num @graph com o WebPage")
    if book.get("@id") != book_id:
        problems.append(f"{rel}: Book precisa de @id {book_id!r}, tem {book.get('@id')!r}")
    if book.get("isAccessibleForFree") is not True:
        problems.append(f"{rel}: Book precisa de isAccessibleForFree: true")
    if ref(book.get("author")) != PERSON_ID:
        problems.append(f"{rel}: Book.author precisa apontar para {PERSON_ID}")
    if ref(book.get("publisher")) != PERSON_ID:
        problems.append(f"{rel}: Book.publisher precisa apontar para {PERSON_ID}")
    if "offers" in book or any(isinstance(n, dict) and ("offers" in n or n.get("@type") == "Offer") for n in gnodes):
        problems.append(f"{rel}: Book não pode declarar offers")
    examples = book.get("workExample")
    if not isinstance(examples, list):
        problems.append(f"{rel}: Book precisa de workExample em lista com o PDF e o Kindle")
    else:
        if any(isinstance(ex, dict) and "offers" in ex for ex in examples):
            problems.append(f"{rel}: workExample do Book não pode declarar offers")
        has_pdf = any(
            isinstance(ex, dict)
            and ex.get("@type") == "Book"
            and ex.get("bookFormat") == "https://schema.org/EBook"
            and ex.get("encodingFormat") == "application/pdf"
            and ex.get("isAccessibleForFree") is True
            and ex.get("url") == book_url + "#baixar"
            for ex in examples
        )
        has_kindle = any(
            isinstance(ex, dict)
            and ex.get("@type") == "Book"
            and ex.get("bookFormat") == "https://schema.org/EBook"
            and ex.get("url") == BOOK_AMAZON_URL
            and isinstance(ex.get("identifier"), dict)
            and ex["identifier"].get("@type") == "PropertyValue"
            and ex["identifier"].get("propertyID") == "ASIN"
            and ex["identifier"].get("value") == BOOK_ASIN
            for ex in examples
        )
        if not has_pdf:
            problems.append(f"{rel}: workExample sem a edição PDF gratuita ({book_url}#baixar)")
        if not has_kindle:
            problems.append(f"{rel}: workExample sem a edição Kindle ({BOOK_AMAZON_URL}, ASIN {BOOK_ASIN})")
    if not any(
        isinstance(n, dict)
        and n.get("@type") == "WebPage"
        and n.get("url") == book_url
        and ref(n.get("isPartOf")) == website_id
        and ref(n.get("about")) == book_id
        and ref(n.get("mainEntity")) == book_id
        for n in gnodes
    ):
        problems.append(f"{rel}: @graph sem WebPage com url {book_url}, isPartOf {website_id}, about e mainEntity {book_id}")
    return problems


def check_page_old_youtube_handle(rel: str, html: str) -> list[str]:
    """#105: o handle antigo do YouTube não pode aparecer em nenhum HTML do site montado (atributo, texto ou script)."""
    needle = OLD_YOUTUBE_HANDLE.lower()
    decoded = unescape(html)
    if (needle in html.lower() or needle in decoded.lower()
            or needle in unquote(html).lower() or needle in unquote(decoded).lower()):
        return [f"{rel}: contém o handle antigo do YouTube ({OLD_YOUTUBE_HANDLE})"]
    return []


def check_old_youtube_handle(site: Path) -> list[str]:
    """#105: nenhuma página HTML do site montado traz o handle antigo do YouTube."""
    problems: list[str] = []
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_page_old_youtube_handle(rel, f.read_text(encoding="utf-8"))
    return problems


class _PriorityPage(HTMLParser):
    """Coleta <script src>, elementos com id, <form> e seus <input type=email name=email> e widgets Turnstile fora de comentário/template/noscript/hidden."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.scripts: list[str] = []
        self.id_attrs: dict[str, dict[str, str]] = {}
        self.forms: list[dict[str, object]] = []
        self.active_form: dict[str, object] | None = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        inert = any(i for _, i in self.stack) or tag in ("template", "noscript") or "hidden" in a
        if not inert:
            eid = a.get("id", "").strip()
            if eid:
                self.id_attrs.setdefault(eid, a)
            if tag == "script":
                stype = a.get("type", "").strip().lower()
                if not stype or stype in ("text/javascript", "module", "application/javascript"):
                    src = a.get("src", "").strip()
                    if src:
                        self.scripts.append(src)
            elif tag == "form":
                form: dict[str, object] = {"attrs": a, "email_inputs": 0, "turnstile_sitekeys": []}
                self.forms.append(form)
                self.active_form = form
            elif self.active_form is not None:
                if tag == "input" and a.get("type", "").strip().lower() == "email" and a.get("name", "") == "email":
                    self.active_form["email_inputs"] = int(self.active_form["email_inputs"]) + 1
                elif tag == "div" and "cf-turnstile" in a.get("class", "").split():
                    self.active_form["turnstile_sitekeys"].append(a.get("data-sitekey", ""))
        if tag not in VOID_TAGS:
            self.stack.append((tag, tag in ("template", "noscript") or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                closed = self.stack[i:]
                del self.stack[i:]
                if any(t == "form" for t, _ in closed):
                    self.active_form = None
                return


def check_priority(site: Path) -> list[str]:
    """#197: /prioridade/ existe com noindex, canonical próprio, título, H1, formulário com Turnstile e os 5 fragmentos, fora do sitemap."""
    problems: list[str] = []
    entries = _sitemap(site, "sitemap-site.xml", "urlset", "url", problems)
    sitemap_locs = {loc for loc, _ in entries} if entries is not None else None
    if sitemap_locs is not None and SITE_ORIGIN + PRIORITY_URL in sitemap_locs:
        problems.append(f"sitemap-site.xml: {SITE_ORIGIN}{PRIORITY_URL} tem noindex e não entra no sitemap")
    page = site / PRIORITY_PAGE
    if not page.is_file():
        return problems + [f"{PRIORITY_PAGE}: página de oposição ausente no site montado (site-manifest.txt)"]
    html = page.read_text(encoding="utf-8")
    p = _Page()
    p.feed(html)
    p.close()
    if not any(_robots_noindex(r) for r in p.robots):
        problems.append(f"{PRIORITY_PAGE}: página de oposição precisa de noindex")
    if not p.canonicals or not canonical_is_self(p.canonicals, PRIORITY_URL):
        problems.append(f"{PRIORITY_PAGE}: página de oposição precisa de canonical próprio "
                        f"({SITE_ORIGIN}{PRIORITY_URL}), tem {p.canonicals}")
    visible = _visible_html(html)
    m = re.search(r"<title[^>]*>(.*?)</title\s*>", visible, flags=re.S | re.I)
    title = unescape(m.group(1)).strip() if m else None
    if title != PRIORITY_TITLE:
        problems.append(f"{PRIORITY_PAGE}: o título precisa ser {PRIORITY_TITLE!r}: {title!r}")
    h1s = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", h))).strip()
           for h in re.findall(r"<h1\b[^>]*>(.*?)</h1\s*>", visible, flags=re.S | re.I)]
    if h1s != [PRIORITY_H1]:
        problems.append(f"{PRIORITY_PAGE}: a página precisa de um H1 {PRIORITY_H1!r}, tem {h1s}")
    if "/privacidade/#prioridade" not in p.refs:
        problems.append(f"{PRIORITY_PAGE}: falta link para /privacidade/#prioridade")
    pf = _PriorityPage()
    pf.feed(html)
    pf.close()
    for frag in PRIORITY_FRAGMENTS:
        if frag not in pf.id_attrs:
            problems.append(f"{PRIORITY_PAGE}: falta a mensagem #{frag}")
        elif pf.id_attrs[frag].get("tabindex", "").strip() != "-1":
            problems.append(f'{PRIORITY_PAGE}: mensagem #{frag} precisa de tabindex="-1"')
    has_focus_script = any(
        "location.hash" in masked and ".focus(" in masked
        for masked in (_js_mask(body) for body in _exec_scripts(html))
    )
    if not has_focus_script:
        problems.append(f"{PRIORITY_PAGE}: falta o script de foco nas mensagens de fragmento")
    if TURNSTILE_SCRIPT_SRC not in pf.scripts:
        problems.append(f"{PRIORITY_PAGE}: falta o script do Turnstile ({TURNSTILE_SCRIPT_SRC})")
    forms = [f for f in pf.forms if f["attrs"].get("action") == PRIORITY_ACTION]
    if len(forms) != 1:
        problems.append(f"{PRIORITY_PAGE}: esperado um formulário para {PRIORITY_ACTION}, achados {len(forms)}")
    else:
        form = forms[0]
        attrs = form["attrs"]
        if attrs.get("method", "").lower() != "post":
            problems.append(f'{PRIORITY_PAGE}: formulário precisa usar method="post"')
        if attrs.get("data-clarity-mask") != "true":
            problems.append(f'{PRIORITY_PAGE}: formulário precisa de data-clarity-mask="true"')
        if int(form["email_inputs"]) < 1:
            problems.append(f"{PRIORITY_PAGE}: formulário sem campo de email")
        if form["turnstile_sitekeys"] != [TURNSTILE_SITE_KEY]:
            problems.append(f'{PRIORITY_PAGE}: formulário sem widget do Turnstile com data-sitekey="{TURNSTILE_SITE_KEY}"')
    return problems


def check_page_date_placeholder(rel: str, html: str) -> list[str]:
    """#197: o marcador '[DATA]' não pode aparecer em nenhuma página HTML publicada (texto, atributo, entidade ou script)."""
    decoded = unescape(html)
    if DATE_PLACEHOLDER in html or DATE_PLACEHOLDER in decoded:
        return [f"{rel}: contém o marcador de data não substituído ({DATE_PLACEHOLDER})"]
    return []


def check_date_placeholder(site: Path) -> list[str]:
    """#197: nenhuma página HTML do site montado pode sair com '[DATA]' literal."""
    problems: list[str] = []
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_page_date_placeholder(rel, f.read_text(encoding="utf-8"))
    return problems


GA_UID_BLOCK = (
    "if(window.guifavConsent==='granted'){gtag('consent','update',{ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted',analytics_storage:'granted'});"
    "(function(){varp=function(x){try{returnJSON.parse(localStorage.getItem(x)||'null')}catch(e){returnnull}},k=p('heating-link-v1'),g=p('heating-login-v1'),n=Date.now(),l=!!(k&&k.v==='granted'&&typeofk.uid==='string'&&/^[0-9A-Za-z]{1,128}$/.test(k.uid)&&typeofk.t==='number'&&n-k.t>=0&&n-k.t<31536e6);"
    "if(l||(g&&typeofg.t==='number'&&n-g.t>=0&&n-g.t<31536e6)){gtag('consent','update',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});"
    "gtag('set','allow_google_signals',false);"
    "gtag('set','allow_ad_personalization_signals',false);"
    "}if(l){varu=p('heating-uid-v1');"
    "if(u&&u.uid===k.uid&&typeofu.t==='number'&&n-u.t>=0&&n-u.t<18e5)gtag('set',{user_id:u.uid});"
    "}})();"
    "}"
)
AUTH_AD_CONSENT_DENIED = "sendGtag('consent','update',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'})"
AUTH_AD_CONSENT_GRANTED = "sendGtag('consent','update',{ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted'})"


def _has_js_code(js: str, compact_needle: str) -> bool:
    """compact_needle (sem espaços) aparece em código JS real (fora de comentário e não dentro de string)."""
    mask = _js_mask(js)
    code = _js_mask(js, keep_strings=True)
    chars: list[str] = []
    idx_map: list[int] = []
    for pos, ch in enumerate(code):
        if not ch.isspace():
            chars.append(ch)
            idx_map.append(pos)
    compact = "".join(chars)
    start = 0
    while True:
        hit = compact.find(compact_needle, start)
        if hit < 0:
            return False
        if mask[idx_map[hit]] == compact_needle[0]:
            return True
        start = hit + len(compact_needle)


def check_ga_uid_snippet(rel: str, html: str) -> list[str]:
    """#197 corte 2: em página com GA4, o snippet inline lê consent-v1 e heating-link-v1 (vínculo ligado nega anúncios e desliga os sinais) e só define user_id com o heating-uid-v1 do mesmo uid, tudo antes do config."""
    if GA4_TAG not in html:
        return []
    blocks: list[tuple[int, int]] = []
    first_config: tuple[int, int] | None = None
    for i, body in enumerate(_exec_scripts(html)):
        mask = _js_mask(body)
        code = _js_mask(body, keep_strings=True)
        for call in re.finditer(r"\bgtag\s*\(", mask):
            if first_config is None and re.match(r"""gtag\s*\(\s*(['"])config\1\s*,""", code[call.start():]):
                first_config = (i, call.start())
        chars: list[str] = []
        idx_map: list[int] = []
        for pos, ch in enumerate(code):
            if not ch.isspace():
                chars.append(ch)
                idx_map.append(pos)
        compact = "".join(chars)
        start = 0
        while True:
            hit = compact.find(GA_UID_BLOCK, start)
            if hit < 0:
                break
            orig_pos = idx_map[hit]
            if mask[orig_pos] == "i":
                blocks.append((i, orig_pos))
            start = hit + len(GA_UID_BLOCK)
    if len(blocks) != 1:
        return [f"{rel}: página com GA4 precisa do bloco de heating-uid-v1 (consentimento e sinais de anúncio desligados e user_id antes do config) uma vez, achados {len(blocks)}"]
    if first_config is None or blocks[0] > first_config:
        return [f"{rel}: o bloco de heating-uid-v1 precisa vir antes do primeiro gtag('config', ...)"]
    return []


def check_auth(site: Path) -> list[str]:
    """#197 corte 2: track.js carrega auth.js com versão (?v= de 12 dígitos), snippet GA4 lê heating-uid-v1 antes do config, auth.js alterna consentimento de anúncios na sessão identificada, e 404, /privacidade/, /termos/ e /prioridade/ ficam sem One Tap."""
    problems: list[str] = []
    track_file = site / "assets/track.js"
    auth_file = site / "assets/auth.js"
    if not auth_file.is_file():
        problems.append("assets/auth.js: arquivo ausente no site montado")
    else:
        auth_src = auth_file.read_text(encoding="utf-8")
        for pref in NO_ONE_TAP_AUTH_PREFIXES:
            if f"'{pref}'" not in auth_src and f'"{pref}"' not in auth_src:
                problems.append(f"assets/auth.js: falta exclusão de One Tap para {pref}")
        if not _has_js_code(auth_src, AUTH_AD_CONSENT_DENIED):
            problems.append("assets/auth.js: falta negar ad_storage, ad_user_data e ad_personalization ao identificar")
        if not _has_js_code(auth_src, AUTH_AD_CONSENT_GRANTED):
            problems.append("assets/auth.js: falta devolver ad_storage, ad_user_data e ad_personalization para granted ao sair da identificação")
    if not track_file.is_file():
        problems.append("assets/track.js: arquivo ausente no site montado")
    else:
        track_src = track_file.read_text(encoding="utf-8")
        if not re.search(r"""['"]/assets/auth\.js\?v=\d{12}['"]""", track_src):
            problems.append("assets/track.js: precisa carregar /assets/auth.js com versão ?v= de 12 dígitos")
    for rel in NO_ONE_TAP_PAGES:
        f = site / rel
        if not f.is_file():
            continue
        html = f.read_text(encoding="utf-8")
        if GIS_CLIENT_URL in html:
            problems.append(f"{rel}: não pode carregar o Google Identity Services (One Tap)")
        if _track_loads(html, AUTH_PATH):
            problems.append(f"{rel}: não pode carregar {AUTH_PATH} diretamente")
        if rel == "404.html" and _track_loads(html, TRACK_PATH):
            problems.append(f"{rel}: página 404 não pode carregar {TRACK_PATH}")
    for f in html_files(site):
        rel = f.relative_to(site).as_posix()
        problems += check_ga_uid_snippet(rel, f.read_text(encoding="utf-8"))
    return problems


# Séries de artigos com links cruzados obrigatórios no corpo do post (#106), na ordem de leitura.
SERIES: tuple[tuple[str, ...], ...] = (
    (
        "rag-llm-ai-o-que-e",
        "o-que-e-rag-llms-ia",
        "guia-praticos-implementacao-rag-llms-ai",
        "rag-knowledge-bases-corporativas-implementacao-pratica",
    ),
    (
        "modernizacao-de-sistemas-legados",
        "arquitetura-consolidacao-dados-ia-sem-reescrever-sistemas-legados",
        "como-integrar-ia-nos-seus-sistemas-legados",
    ),
    (
        "memoria-agentes-solucao-definitiva",
        "a-memoria-dos-meus-agentes-na-pratica",
    ),
)


def _series_targets(slug: str, hrefs: list[str]) -> set[str]:
    """Caminhos internos (sem query/fragmento) dos links de /blog/<slug>/."""
    base = f"{SITE_ORIGIN}{BLOG_PREFIX}{slug}/"
    targets: set[str] = set()
    for href in hrefs:
        try:
            u = urlsplit(urljoin(base, href))
        except ValueError:
            continue
        if u.scheme in ("http", "https") and (u.hostname or "").lower().rstrip(".") == SITE_HOST:
            targets.add(unquote(u.path) or "/")
    return targets


def check_series(site: Path, series: tuple[tuple[str, ...], ...] = SERIES) -> list[str]:
    """#106: todo par consecutivo (A, B) de cada série precisa de links mútuos dentro de div.post-body."""
    problems: list[str] = []
    targets_by_slug: dict[str, set[str] | None] = {}
    for slugs in series:
        for slug in slugs:
            if slug in targets_by_slug:
                continue
            rel = f"blog/{slug}/index.html"
            f = site / rel
            if not f.is_file():
                problems.append(f"{rel}: página de post da série ausente no site montado")
                targets_by_slug[slug] = None
                continue
            p = _Page()
            p.feed(f.read_text(encoding="utf-8"))
            p.close()
            targets_by_slug[slug] = _series_targets(slug, p.post_body_links)
        for prev_slug, next_slug in zip(slugs, slugs[1:]):
            prev_targets = targets_by_slug[prev_slug]
            next_targets = targets_by_slug[next_slug]
            want_next = f"{BLOG_PREFIX}{next_slug}/"
            want_prev = f"{BLOG_PREFIX}{prev_slug}/"
            if prev_targets is not None and want_next not in prev_targets:
                problems.append(
                    f"blog/{prev_slug}/index.html: sem link em div.post-body para a próxima parte da série ({want_next})"
                )
            if next_targets is not None and want_prev not in next_targets:
                problems.append(
                    f"blog/{next_slug}/index.html: sem link em div.post-body para a parte anterior da série ({want_prev})"
                )
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
        problems += check_related(Path(args.site), Path(args.posts))
        problems += check_summary(Path(args.site), Path(args.posts))
        problems += check_contrast(Path(args.site))
        problems += check_consulting(Path(args.site))
        problems += check_book(Path(args.site))
        problems += check_paid_consulting(Path(args.site))
        problems += check_home(Path(args.site), Path(args.posts), read_home_latest(Path(args.consts)))
        problems += check_post_ld(Path(args.site), Path(args.posts))
        problems += check_topic_ld(Path(args.site))
        problems += check_framework_ld(Path(args.site))
        problems += check_og_site_name(Path(args.site))
        problems += check_post_residues(Path(args.posts))
        problems += check_llm_ranking(Path(args.site))
        problems += check_legal(Path(args.site))
        problems += check_priority(Path(args.site))
        problems += check_auth(Path(args.site))
        problems += check_date_placeholder(Path(args.site))
        problems += check_llms_txt(Path(args.site), Path(args.posts))
        problems += check_courses(Path(args.site))
        problems += check_youtube(Path(args.site))
        problems += check_old_youtube_handle(Path(args.site))
        problems += check_series(Path(args.site))
    except ResolverError as e:
        print(f"ERRO: resolvedor de URL indisponível, verificação não feita: {e}", file=sys.stderr)
        return 1
    seo_problems, warn_new, warn_old = check_seo(Path(args.site), Path(args.posts))
    problems += seo_problems
    gh = os.environ.get("GITHUB_ACTIONS") == "true"
    for w in warn_new:
        print(f"::warning title=check_site (#103)::{_gh_escape(w)}" if gh else f"AVISO: {w}")
    if warn_old:
        if gh:
            print(f"::group::Avisos dos posts importados ({len(warn_old)})")
        for w in warn_old:
            print(f"AVISO (importado): {w}")
        if gh:
            print("::endgroup::")
    for p in problems:
        print(f"ERRO: {p}", file=sys.stderr)
    if problems:
        return 1
    n = len(html_files(Path(args.site)))
    print(f"check_site: {n} páginas HTML conferidas, sem problemas (antes do cutover: {pre})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
