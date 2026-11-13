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
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

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
    # Completude do blog: a capa e todo post indexável, uma vez cada.
    if sitemap_locs is not None:
        want_blog = {SITE_ORIGIN + BLOG_PREFIX} | {f"{SITE_ORIGIN}{BLOG_PREFIX}{s}/" for s in indexable_slugs}
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


def _exec_scripts(html: str) -> list[str]:
    """Corpo dos <script> que rodam: fora de comentário, template/noscript e sem type de dado (JSON-LD)."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<(template|noscript)\b[^>]*>.*?</\1\s*>", "", html, flags=re.S | re.I)
    out = []
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, flags=re.S | re.I):
        m = re.search(r"""(?:^|\s)type\s*=\s*["']?([^"'\s>]+)""", attrs, flags=re.I)
        if m and m.group(1).lower() not in ("text/javascript", "module", "application/javascript"):
            continue
        out.append(body)
    return out


def _has_clarity(html: str) -> bool:
    """O snippet do Clarity está num <script> que roda."""
    return any(all(k in body for k in CLARITY_MARKERS) for body in _exec_scripts(html))


# Títulos (Gui, 13/11/2026): "Página | Guilherme Favaron"; a home leva o nome primeiro, "Guilherme Favaron | ...".
# As telas de exemplo do portfólio (portfolio/mockups) aparecem dentro do portfólio e ficam fora da regra.
SITE_NAME = "Guilherme Favaron"
TITLE_SEP = " | "
TITLE_EXEMPT_PREFIXES = ("portfolio/mockups/",)
# Grupos de conteúdo do GA4 (content_group), um por tipo de página; espelha CONTENT_GROUPS do consts.ts do blog.
CONTENT_GROUPS = ("home", "portfolio", "insights-lista", "insights-artigo", "expert-insights")
EXPERT_INSIGHTS_PAGES = {f"blog/{p}/index.html" for p in
                         ("assinar", "inscricao-enviada", "inscricao-confirmada", "inscricao-invalida", "privacidade")}


def expected_content_group(rel: str) -> str | None:
    if rel == "index.html":
        return "home"
    if rel == "portfolio/index.html":
        return "portfolio"
    if rel == "blog/index.html" or re.fullmatch(r"blog/pagina/\d+/index\.html", rel):
        return "insights-lista"
    if rel in EXPERT_INSIGHTS_PAGES:
        return "expert-insights"
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


def _meta_contents(html: str, prop: str) -> list[str]:
    values = []
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
        if re.search(r"""(?:^|\s)(?:property|name)\s*=\s*["']""" + re.escape(prop) + r"""["']""", tag, flags=re.I):
            m = re.search(r"""(?:^|\s)content\s*=\s*(["'])(.*?)\1""", tag, flags=re.S)
            values.append(unescape(m.group(2)).strip() if m else "")
    return values


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
