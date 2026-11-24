"""Verifica o site montado em _site/ antes do deploy.

- Todo link interno (href, src, srcset, poster, inclusive URLs absolutas de guifav.dev) aponta para
  um arquivo publicado. Pega base errado no blog e links para pastas fora do site
  (docs/, _next/, tools/).
- Antes do cutover (PRE_CUTOVER_NOINDEX em blog-src/src/consts.ts): toda página do blog
  tem meta robots com noindex, e nenhuma página fora do blog aponta para ele.
- rss.xml e sitemap.xml do blog são XML válido; o sitemap só lista páginas que existem e
  não lista posts com index: false nem as páginas da newsletter. Depois do cutover, esses
  posts e as páginas da newsletter continuam precisando de noindex.
- Sob /blog/ só há .html, .xml e mídia da lista.
- Ferramentas e guias (#104): a home (seção e rodapé) e o rodapé do blog levam os links das quatro ferramentas.
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
import math
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
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sitemaps import canonical_is_self, robots_disallow  # noqa: E402

SITE_HOST = "guifav.dev"
# Domínio antigo do Substack (#24): depois do cutover, nada no site aponta para ele.
OLD_DOMAIN = "guilhermefavaron.com.br"
OLD_HOSTS = {OLD_DOMAIN, "www." + OLD_DOMAIN}
# Links de posts importados que já davam 404 no Substack antes do cutover (caminhos da era Wix, conferidos em
# 2026-11-12). A importação não altera texto, então eles ficam; o 301 genérico os leva à raiz do blog.
_W = "https://www." + OLD_DOMAIN
OLD_DOMAIN_DEAD_LINKS = (
    ("blog/desbloqueando-a-inteligencia-artificial-o-guia-definitivo-para-empresarios-na-era-da-ia-livro-da-ia/index.html",
     _W + "/livro-ia-guia-estrategico-empresarios-era-digital"),
    ("blog/decifrando-o-codigo-da-ia-estrategias-para-mitigar-vieses-em-negocios-inteligentes/index.html",
     _W + "/livro-ia-guia-estrategico-empresarios-era-digital"),
    ("blog/microservices-vs-monolith-decisoes/index.html", _W + "/post/modernizacao-sistemas-legados-vantagem-competitiva"),
    ("blog/como-designers-profissionais-usam-ia-guia-completo-2024/index.html", _W + "/pagina-inicial/hashtags/FF0000"),
    ("blog/como-designers-profissionais-usam-ia-guia-completo-2024/index.html", _W + "/pagina-inicial/hashtags/000000"),
    ("blog/modernizacao-de-sistemas-legados/index.html", _W + "/pagina-inicial"),
    ("blog/data-pipelines-empresariais-como/index.html", _W + "/post/machine-learning-producao-modelos-empresariais-roi"),
)
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
BLOG_PRIVATE_PAGES = frozenset({"assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida", "privacidade"})
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

    # Contrato, barreira 2: sob /blog/ só páginas, RSS, sitemap e mídia da lista.
    for f in sorted((site / "blog").rglob("*")):
        if f.is_file() and f.suffix not in BLOG_EXACT_SUFFIXES and f.suffix.lower() not in BLOG_MEDIA_SUFFIXES:
            problems.append(f"{f.relative_to(site).as_posix()}: extensão fora da lista do blog "
                            f"(só .html, .xml e mídia {', '.join(sorted(BLOG_MEDIA_SUFFIXES))})")

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
        # Páginas de tema (#104): no sitemap (completude, abaixo), com canonical para si mesmas e, depois do
        # cutover, indexáveis.
        if TOPIC_PAGE.fullmatch(rel):
            topic_urls.append(url)
            if not pre_cutover and has_noindex:
                problems.append(f"{rel}: página de tema com noindex")
            if not p.canonicals or not canonical_is_self(p.canonicals, url):
                problems.append(f"{rel}: página de tema sem canonical para si mesma: {p.canonicals}")
        if not pre_cutover:
            # Fora do blog, qualquer menção literal conta (inclusive JSON-LD e texto): o código é do site.
            if not in_blog and OLD_DOMAIN in (site / rel).read_text(encoding="utf-8").lower():
                problems.append(f"{rel}: menciona o domínio antigo ({OLD_DOMAIN})")
            # Em todo o site, links e embeds pela URL resolvida: pega entidade HTML, percent-encoding e ponto final,
            # que a busca literal não vê. No blog, menção em texto corrido de post importado fica, e só os links
            # mortos conhecidos passam, cada um na sua página (todas do blog; o teste confere contra os fontes).
            for u in dict.fromkeys(list(p.refs) + [v for _, v in p.url_values]):
                if _old_host(resolved[(base, u)]) and (rel, u) not in OLD_DOMAIN_DEAD_LINKS:
                    problems.append(f"{rel}: link para o domínio antigo: {u[:80]}")
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
CONTENT_GROUPS = ("home", "portfolio", "insights-lista", "insights-artigo", "expert-insights", "materiais", "consultoria")
# Páginas dos materiais do canal (issue #99): o formulário e a página de enviado de cada material do catálogo do Worker.
MATERIALS = ("planilha-custos-ia", "mapa-decisao-ia")
MATERIAL_PAGES = {f"{m}/{p}index.html" for m in MATERIALS for p in ("", "enviado/")}
EXPERT_INSIGHTS_PAGES = {f"blog/{p}/index.html" for p in
                         ("assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida", "privacidade")}


def expected_content_group(rel: str) -> str | None:
    if rel == "index.html":
        return "home"
    if rel == "portfolio/index.html":
        return "portfolio"
    if rel == "consultoria/index.html":
        return "consultoria"
    if (rel == "blog/index.html" or re.fullmatch(r"blog/pagina/\d+/index\.html", rel)
            or re.fullmatch(r"blog/tema/[a-z0-9][a-z0-9-]*/index\.html", rel)):
        return "insights-lista"
    if rel in EXPERT_INSIGHTS_PAGES:
        return "expert-insights"
    if rel in MATERIAL_PAGES:
        return "materiais"
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


def _meta_contents(html: str, prop: str) -> list[str]:
    """content de cada <meta> cujo property ou name é prop (sem diferença de caixa); "" quando falta o content."""
    p = _MetaTags()
    p.feed(html)
    p.close()
    return [t.get("content", "").strip() for t in p.tags
            if prop.lower() in (t.get("property", "").lower(), t.get("name", "").lower())]


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
    # generate_lead na página de enviado de cada material, com o id do material igual ao da pasta (issue #99).
    lead = next((m for m in MATERIALS if rel == f"{m}/enviado/index.html"), None)
    expected = ["sign_up"] if rel == SIGNUP_PAGE else ["generate_lead"] if lead else []
    if t.ga_events != expected:
        problems.append(f"{rel}: data-ga-event {t.ga_events}, esperado {expected}")
    elif lead and t.ga_materials != [lead]:
        problems.append(f"{rel}: generate_lead com data-material-id {t.ga_materials}, esperado [{lead!r}]")
    # Formulário de material (issue #99): data-ga-client-id e o campo oculto ga_cid, que o track.js preenche no envio.
    if any(rel == f"{m}/index.html" for m in MATERIALS):
        # Só HTML visível: formulário ou campo dentro de comentário não conta.
        forms = [f for f in re.findall(r"<form\b[^>]*>.*?</form>", _visible_html(html), flags=re.S | re.I)
                 if 'action="https://insights.guifav.dev/material"' in f]
        if len(forms) != 1:
            problems.append(f"{rel}: esperado um formulário de material, achados {len(forms)}")
        elif not re.match(r"<form\b[^>]*\sdata-ga-client-id[\s>=]", forms[0]) or \
                forms[0].count('<input type="hidden" name="ga_cid" value="">') != 1:
            problems.append(f"{rel}: formulário de material sem data-ga-client-id ou sem o campo oculto ga_cid")
    return problems


# "Ferramentas e guias" (issue #104, item 1): as ferramentas no caminho do rastreador e do leitor. A home leva os
# quatro links na seção própria e no rodapé; toda página do blog, no rodapé (o mesmo da home, do Base.astro). Alarme
# de integridade dos templates, como os de cima: só nas páginas com GA4, que são as que os templates geram.
TOOLS_LINKS = ("/mapa-decisao-ia/", "/planilha-custos-ia/",
               "/blog/livro-ia-inteligencia-artificial-portugues-gratuito/",
               "/seo-geo-aeo-framework/framework-seo-aeo.html")
TOOLS_SECTION_ID = "ferramentas"


# Elementos sem fechamento: não entram na pilha de abertos do _ToolsLinks.
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _ToolsLinks(HTMLParser):
    """hrefs dentro do <footer> e dentro da <section id="ferramentas">.

    Não contam: link em comentário, dentro de <template> ou de elemento com hidden, ou com hidden no próprio <a>
    (follow-up do PR #116). O rel de cada link fica guardado para a regra recusar nofollow.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # Pilha dos elementos abertos: (tag, bloco que ele abre ou None, se ele torna o conteúdo inerte).
        self.stack: list[tuple[str, str | None, bool]] = []
        self.found: dict[str, list[str]] = {"footer": [], "section": []}
        self.rels: dict[str, list[tuple[str, str]]] = {"footer": [], "section": []}

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
            "section" if tag == "section" and a.get("id") == TOOLS_SECTION_ID else None)
        self.stack.append((tag, block, tag == "template" or "hidden" in a))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def check_tools(rel: str, html: str) -> list[str]:
    """#104: os quatro links de TOOLS_LINKS, uma vez cada, no rodapé da home e do blog e na seção da home."""
    if GA4_TAG not in html or not (rel == "index.html" or rel.startswith("blog/")):
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
    disallow = robots_disallow(robots_txt)
    want = set()
    for rel, url, p in root_pages:
        if rel == "404.html" or any(url.startswith(d) for d in disallow) or any(_robots_noindex(r) for r in p.robots):
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
    """Cada raiz de JSON-LD fora de comentário, com o @context do schema.org: (nós, se veio de um @graph)."""
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


def _home_websites(html: str) -> list[dict]:
    """Os WebSite da home com HOME_WEBSITE (nome e URL); o WebSite não usa o @id da Person."""
    return [n for nodes, _ in _ld_blocks(html) for n in nodes
            if isinstance(n, dict) and n.get("@type") == "WebSite" and n.get("@id") != PERSON_ID
            and all(n.get(k) == v for k, v in HOME_WEBSITE.items())]


def _home_has_website(html: str) -> bool:
    """Algum JSON-LD da home (fora de comentário) traz um WebSite com HOME_WEBSITE."""
    return bool(_home_websites(html))


def _home_website_id(html: str) -> str | None:
    """@id do WebSite da home, para o isPartOf dos posts (#105)."""
    ids = [n["@id"] for n in _home_websites(html) if isinstance(n.get("@id"), str) and n["@id"]]
    return ids[0] if ids else None


# JSON-LD dos posts (#105): o @graph que o [slug].astro emite. A Person mínima (nome e URL) deixa o avaliador do
# Google, que lê uma página por vez, resolver author.name e author.url sem seguir o @id até a home.
POST_PERSON = {"@type": "Person", "@id": PERSON_ID, "name": SITE_NAME, "url": SITE_ORIGIN + "/"}
POST_LD_RULE = "post indexável sem o JSON-LD do post (#105)"


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


# Página de consultoria (#136, consulta 18 do mapa da #112): a página existe, com o título e o H1 aprovados e o
# JSON-LD Service do Gui (provider pelo @id da Person) com uma Offer. Alarme de integridade da página estática.
CONSULTING_PAGE = "consultoria/index.html"
CONSULTING_TITLE = "Consultoria em IA para empresas" + TITLE_SEP + SITE_NAME
CONSULTING_H1 = "Consultoria em inteligência artificial para empresas"


def check_consulting(site: Path) -> list[str]:
    """#136: título, H1 único e JSON-LD Service (provider: a Person, com tipo, nome e url) com Offer de preço em BRL na
    /consultoria/, sem FAQPage (#108)."""
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
        # Preço numérico, finito e não negativo (0 vale: a primeira conversa é gratuita), em número ou texto; null,
        # booleano, "abc", negativo, NaN e Infinity não.
        price = o.get("price") if isinstance(o, dict) else None
        numeric = (isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)
                   and price >= 0) or (
            isinstance(price, str) and re.fullmatch(r"\d+(?:\.\d+)?", price.strip()) is not None)
        return isinstance(o, dict) and o.get("@type") == "Offer" and numeric and o.get("priceCurrency") == "BRL"

    def person(pr) -> bool:
        return (isinstance(pr, dict) and pr.get("@id") == PERSON_ID and pr.get("@type") == "Person"
                and isinstance(pr.get("name"), str) and bool(pr["name"].strip())
                and pr.get("url") in (SITE_ORIGIN, SITE_ORIGIN + "/"))

    nodes = _ld_nodes(html)
    if not any(n.get("@type") == "Service" and n.get("@id") != PERSON_ID and person(n.get("provider"))
               and any(priced(o) for o in offers(n)) for n in nodes):
        problems.append(f"{rel}: sem o JSON-LD Service com provider Person ({PERSON_ID!r}, name e url) e uma Offer "
                        "com price e priceCurrency BRL")
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


def check_home(site: Path, posts: Path, n: int | None) -> list[str]:
    """#136: a lista de últimos artigos e os links das páginas de tema na home montada."""
    rel = "index.html"
    page = site / rel
    if not page.is_file():
        return [f"{rel}: home ausente no site montado"]
    if not n:
        return ["blog-src/src/consts.ts: sem export const HOME_LATEST = <n>; (quantos artigos a home lista)"]
    html = re.sub(r"<!--(?! /?ultimos-artigos -->).*?-->", "", page.read_text(encoding="utf-8"), flags=re.S)
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
# portfólio e o /llm-ranking/ têm paleta própria e ficam de fora). Não avalia cor herdada de outro elemento, fundo
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
_CSS_THEME = re.compile(r"""(?P<tag>html|body|:root)?\[data-theme=(?:"(?P<a>dark|light)"|'(?P<b>dark|light)'|(?P<c>dark|light))\]""")
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
    depth, quote = 0, ""
    for j in range(i, stop):
        ch = css[j]
        if quote:
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
    return None


def _split_outside(s: str, sep: str) -> list[str]:
    """s partido em sep fora de aspas e parênteses (:is(.a, .b), content:"a;b", url(a;b))."""
    out, cur, depth, quote = [], [], 0, ""
    for ch in s:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def _css_rules(css: str, theme: str | None = None) -> list[tuple[list[str], list[tuple[str, str, bool]], str | None]]:
    """(seletores canônicos, declarações em ordem, tema só) na ordem do texto. @media/@supports/@layer com bloco entram
    (fora os só de impressão); at-rules de uma linha (@charset, @import, @layer a, b;) e @keyframes e afins não."""
    css = _CSS_COMMENT.sub("", css)
    out: list[tuple[list[str], list[tuple[str, str, bool]], str | None]] = []
    i, n = 0, len(css)
    while i < n:
        b = css.find("{", i)
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
                quote = "" if ch == quote else quote
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


def css_color(value: str | None, tokens: dict[str, str], _depth: int = 0) -> tuple[int, int, int] | None:
    """Cor opaca em RGB, resolvendo var() pelos tokens; None para translúcido, desconhecido ou não-cor."""
    if value is None or _depth > 16:
        return None
    v = value.strip()
    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", v)
    if m:
        if m.group(1) in tokens:
            return css_color(tokens[m.group(1)], tokens, _depth + 1)
        return css_color(m.group(2), tokens, _depth + 1)
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
    - [data-theme=X] Y: (X, Y, False);
    - [data-theme=X] ou body[data-theme=X] sozinho: (X, a tag, ou body, True). É onde moram os tokens do tema;
    - [data-theme=X].y composto: (X, o seletor inteiro, False). O atributo está no próprio elemento, que só existe
      nesse tema e não se mistura com .y;
    - sem prefixo: (None, s, False)."""
    m = _CSS_THEME.match(s)
    if not m:
        return None, s, False
    theme = _THEME_OF[m.group("a") or m.group("b") or m.group("c")]
    rest = s[m.end():]
    if not rest:
        return theme, {":root": "html"}.get(m.group("tag") or "body", m.group("tag") or "body"), True
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


def _css_pairs(sheets: list[str]) -> dict[str, dict[str, tuple]]:
    """Por tema, os seletores com color e fundo opacos resolvidos: {tema: {seletor: (color, fundo, color cru, fundo cru)}}.
    Cascata do navegador para color, cor de fundo e imagem de fundo: !important, depois especificidade, depois ordem.
    O seletor com estado (:hover etc.) recebe também as declarações do seletor base."""
    rules = [r for css in sheets for r in _css_rules(css)]
    base: dict[str, str] = {}
    by_theme: dict[str, dict[str, str]] = {"claro": {}, "escuro": {}}
    for sels, decls, only in rules:
        vs = {k: v for k, v, _ in decls if k.startswith("--")}
        for s in sels if vs else ():
            theme, _, root = _theme_prefix(s)
            if theme is None and s in _CSS_ROOTS:
                (base if only is None else by_theme[only]).update(vs)
            elif root and only in (None, theme):
                by_theme[theme].update(vs)
    tokens = {t: {**base, **by_theme[t]} for t in THEMES}
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
    out: dict[str, dict[str, tuple]] = {t: {} for t in THEMES}
    for (t, s), d in won.items():
        base_sel = _CSS_STATE.sub("", s)
        full = dict(won.get((t, base_sel), {})) if base_sel != s else {}
        for p, cand in d.items():
            if p not in full or cand[:3] > full[p][:3]:
                full[p] = cand
        if "color" not in full or "bg" not in full or (full.get("img") and full["img"][3]):
            continue
        c_raw, b_raw = full["color"][3], full["bg"][3]
        fg, bg = css_color(c_raw, tokens[t]), _css_background(b_raw, tokens[t])
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
        problems += check_contrast(Path(args.site))
        problems += check_consulting(Path(args.site))
        problems += check_home(Path(args.site), Path(args.posts), read_home_latest(Path(args.consts)))
        problems += check_post_ld(Path(args.site), Path(args.posts))
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
