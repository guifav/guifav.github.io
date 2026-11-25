"""Gera o /llms.txt no _site/ já montado (issue #174, épico #111).

Lê as páginas publicadas em _site/ e o frontmatter dos posts em blog-src/src/content/posts/,
selecionando somente páginas indexáveis (sem noindex, sem index: false, sem podcasts, sem
404, sem canonical para outra URL e fora dos Disallow do robots.txt).

Formato: título H1, citação curta, abertura fixa e quatro seções H2 com listas de links e
uma linha de descrição:
- Consultoria (/consultoria/);
- Ferramentas (páginas indexáveis sob /ferramentas/);
- Temas (cada /blog/tema/<id>/ com os seus posts principais e o summary do frontmatter);
- Portfólio e Expert Insights (/portfolio/ e /blog/).

Rodar depois de scripts/assemble_site.py e scripts/build_sitemaps.py, antes de check_site.py:
    python3 scripts/build_llms_txt.py [--site _site] [--posts blog-src/src/content/posts]
"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sitemaps import HTML_SUFFIXES, SITE_ORIGIN, canonical_is_self, noindex, page_url, robots_blocks  # noqa: E402

OUTPUT_NAME = "llms.txt"
TITLE_SUFFIX = " | Guilherme Favaron"
ROBOTS_AGENTS = ("*", "googlebot", "bingbot")
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INERT_TAGS = {"script", "style", "template", "noscript"}
TOPIC_PAGE_RE = re.compile(r"^blog/tema/([a-z0-9][a-z0-9-]*)/index\.html$")
TOPIC_HREF_RE = re.compile(r"^/blog/tema/([a-z0-9][a-z0-9-]*)/$")
POST_HREF_RE = re.compile(r"^/blog/([a-z0-9][a-z0-9-]*)/$")
_YAML_BLOCK_INDICATOR = re.compile(r"^[|>][+-]?(?:\d+)?(?:\s+#.*)?$")
_YAML_NULLS = {"~", "null", "Null", "NULL"}

# Abertura fixa (aprovada pelo Gui na #174, seguindo voz-gui).
SITE_TITLE = "Guilherme Favaron"
SITE_QUOTE = (
    "Site oficial de Guilherme Favaron (guifav.dev), builder e CTO do GRI Institute: "
    "consultoria, ferramentas gratuitas, portfólio e artigos sobre dados e IA aplicada em produção."
)
SITE_INTRO = (
    "Guilherme Favaron é builder e CTO do GRI Institute, com mais de 18 anos liderando tecnologia.\n"
    "Este site reúne artigos técnicos sobre IA aplicada e dados, ferramentas abertas para decisões de "
    "arquitetura e custo, mais de 20 sistemas construídos no portfólio e cinco formatos de consultoria "
    "conduzidos pessoalmente por ele para CEOs, CFOs e CTOs.\n"
    "O trabalho documentado aqui começa pelo problema do negócio e escolhe a ferramenta por último, com "
    "dono, custo e métrica definidos desde o início."
)


class LlmsTxtError(Exception):
    """Não dá para gerar um /llms.txt íntegro: o build para."""


class _PageMeta(HTMLParser):
    """Lê metadados e links estruturais de uma página HTML, ignorando comentários e tags inertes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.description: str = ""
        self.h1: str = ""
        self.robots: list[str] = []
        self.canonicals: list[str] = []
        self.main_links: list[str] = []
        self.topic_nav_links: list[str] = []
        self.topic_post_links: list[str] = []
        # pilha: (tag, inert, in_main, in_topic_nav, in_topic_list)
        self._stack: list[tuple[str, bool, bool, bool, bool]] = []
        self._in_title = False
        self._title_buf: list[str] = []
        self._in_h1 = False
        self._h1_buf: list[str] = []

    def _is_inert(self) -> bool:
        return any(inert for _, inert, _, _, _ in self._stack)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a: dict[str, str] = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        classes = set(a.get("class", "").split())
        parent_inert = self._is_inert()
        self_inert = parent_inert or tag in INERT_TAGS or "hidden" in a
        if not self_inert:
            if tag == "title" and not self.title:
                self._in_title = True
                self._title_buf = []
            elif tag == "h1" and not self.h1:
                self._in_h1 = True
                self._h1_buf = []
            elif tag == "meta":
                name = a.get("name", "").lower()
                if name == "description" and not self.description:
                    self.description = a.get("content", "").strip()
                elif name == "robots":
                    self.robots.append(a.get("content", "").lower())
            elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
                self.canonicals.append(a.get("href", "").strip())
            elif tag == "a":
                href = a.get("href", "").strip()
                if href:
                    if any(m for _, _, m, _, _ in self._stack):
                        self.main_links.append(href)
                    if any(tn for _, _, _, tn, _ in self._stack):
                        self.topic_nav_links.append(href)
                    if any(tl for _, _, _, _, tl in self._stack):
                        self.topic_post_links.append(href)
        if tag not in VOID_TAGS:
            self._stack.append((
                tag,
                self_inert,
                tag == "main" and not self_inert,
                tag == "nav" and "topic-nav" in classes and not self_inert,
                tag == "ol" and "topic-list" in classes and not self_inert,
            ))

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._is_inert():
            self._title_buf.append(data)
        if self._in_h1 and not self._is_inert():
            self._h1_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self.title = "".join(self._title_buf).strip()
            self._in_title = False
        elif tag == "h1" and self._in_h1:
            self.h1 = "".join(self._h1_buf).strip()
            self._in_h1 = False
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


def _parse_html(path: Path) -> _PageMeta:
    meta = _PageMeta()
    meta.feed(path.read_text(encoding="utf-8"))
    meta.close()
    return meta


def _yaml_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
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


def parse_frontmatter(text: str) -> dict[str, str]:
    """Lê campos escalares do frontmatter YAML, incluindo blocos dobrados/literais (> / |)."""
    m = re.match(r"^---\r?\n(.*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)", text, re.S)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        km = re.match(r"""^([A-Za-z_][\w-]*)[ \t]*:[ \t]*(.*)$""", lines[i])
        if not km:
            i += 1
            continue
        key, raw = km.group(1), km.group(2).strip()
        i += 1
        cont: list[str] = []
        while i < len(lines) and (lines[i] == "" or lines[i][0] in " \t"):
            cont.append(lines[i])
            i += 1
        if _YAML_BLOCK_INDICATOR.fullmatch(raw):
            val = " ".join(ln.strip() for ln in cont if ln.strip())
            out[key] = val
        elif raw and not raw.startswith("#"):
            val = _yaml_scalar(raw)
            if raw[0] not in ("'", '"') and val in _YAML_NULLS:
                val = ""
            out[key] = val
        else:
            cleaned = [ln.strip() for ln in cont if ln.strip() and not ln.strip().startswith("#")]
            if not cleaned:
                out[key] = ""
            else:
                first = cleaned[0]
                if first[0] in ("'", '"'):
                    out[key] = _yaml_scalar(" ".join(cleaned))
                else:
                    val = _yaml_scalar(" ".join(cleaned))
                    out[key] = "" if val in _YAML_NULLS else val
    return out


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(title: str) -> str:
    t = _clean_line(title)
    if t.endswith(TITLE_SUFFIX):
        t = t[: -len(TITLE_SUFFIX)].strip()
    return t


def _escape_link_text(text: str) -> str:
    return _clean_line(text).replace("[", r"\[").replace("]", r"\]")


def _format_item(title: str, url: str, desc: str, *, indent: str = "") -> str:
    clean_title = _escape_link_text(title)
    clean_desc = _clean_line(desc)
    if not clean_title:
        raise LlmsTxtError(f"{url}: título vazio")
    if not clean_desc:
        raise LlmsTxtError(f"{url}: descrição vazia")
    return f"{indent}- [{clean_title}]({SITE_ORIGIN}{url}): {clean_desc}"


def _is_blocked(robots_txt: str, url: str) -> bool:
    return any(robots_blocks(robots_txt, url, agent) for agent in ROBOTS_AGENTS)


def _is_indexable(rel: str, url: str, meta: _PageMeta, robots_txt: str) -> bool:
    if rel == "404.html":
        return False
    if _is_blocked(robots_txt, url):
        return False
    if noindex(meta.robots):
        return False
    if not canonical_is_self(meta.canonicals, url):
        return False
    return True


def _load_required_page(site: Path, rel: str, robots_txt: str) -> tuple[str, _PageMeta]:
    path = site / rel
    if path.is_symlink() or not path.is_file():
        raise LlmsTxtError(f"{rel}: página obrigatória ausente no _site")
    url = page_url(rel)
    meta = _parse_html(path)
    if not _is_indexable(rel, url, meta, robots_txt):
        raise LlmsTxtError(f"{rel}: página obrigatória não é indexável")
    return url, meta


def _load_posts(posts_dir: Path) -> dict[str, dict[str, str]]:
    if not posts_dir.is_dir():
        raise LlmsTxtError(f"pasta de posts ausente: {posts_dir}")
    out: dict[str, dict[str, str]] = {}
    for md in sorted(posts_dir.glob("**/*.md")):
        fm = parse_frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug") or md.stem
        if slug in out:
            raise LlmsTxtError(f"posts: slug duplicado {slug!r}")
        out[slug] = fm
    return out


def _normalize_internal_path(href: str) -> str | None:
    u = urlsplit(href)
    if u.scheme or u.netloc:
        if u.scheme != "https" or u.netloc != "guifav.dev":
            return None
    if u.query or u.fragment:
        return None
    return u.path or None


def _build_tools_section(site: Path, robots_txt: str) -> list[str]:
    tools_dir = site / "ferramentas"
    if not tools_dir.is_dir():
        raise LlmsTxtError("ferramentas/: pasta ausente no _site")
    index_url, index_meta = _load_required_page(site, "ferramentas/index.html", robots_txt)
    discovered: dict[str, _PageMeta] = {index_url: index_meta}
    for f in sorted(tools_dir.rglob("*")):
        if f.is_symlink() or not f.is_file() or f.suffix.lower() not in HTML_SUFFIXES:
            continue
        rel = f.relative_to(site).as_posix()
        url = page_url(rel)
        if url in discovered:
            continue
        meta = _parse_html(f)
        if _is_indexable(rel, url, meta, robots_txt):
            discovered[url] = meta

    ordered: list[str] = [index_url]
    for href in index_meta.main_links:
        p = _normalize_internal_path(href)
        if p and p in discovered and p not in ordered:
            ordered.append(p)
    for url in sorted(discovered):
        if url not in ordered:
            ordered.append(url)
    if len(ordered) < 2:
        raise LlmsTxtError("ferramentas/: nenhuma ferramenta indexável encontrada além do índice")
    return [_format_item(_clean_title(discovered[u].title), u, discovered[u].description) for u in ordered]


def _build_topics_section(site: Path, posts_meta: dict[str, dict[str, str]], blog_meta: _PageMeta, robots_txt: str) -> list[str]:
    tema_dir = site / "blog" / "tema"
    if not tema_dir.is_dir():
        raise LlmsTxtError("blog/tema/: pasta de temas ausente no _site")
    topics: dict[str, tuple[str, _PageMeta]] = {}
    for f in sorted(tema_dir.glob("*/index.html")):
        if f.is_symlink() or not f.is_file():
            continue
        rel = f.relative_to(site).as_posix()
        m = TOPIC_PAGE_RE.fullmatch(rel)
        if not m:
            continue
        tid = m.group(1)
        url = page_url(rel)
        meta = _parse_html(f)
        if _is_indexable(rel, url, meta, robots_txt):
            topics[tid] = (url, meta)
    if not topics:
        raise LlmsTxtError("blog/tema/: nenhuma página de tema indexável encontrada")

    ordered_ids: list[str] = []
    for href in blog_meta.topic_nav_links:
        p = _normalize_internal_path(href)
        m = TOPIC_HREF_RE.fullmatch(p) if p else None
        if m and m.group(1) in topics and m.group(1) not in ordered_ids:
            ordered_ids.append(m.group(1))
    for tid in sorted(topics):
        if tid not in ordered_ids:
            ordered_ids.append(tid)

    lines: list[str] = []
    total_posts = 0
    for tid in ordered_ids:
        t_url, t_meta = topics[tid]
        t_title = _clean_line(t_meta.h1) or _clean_title(t_meta.title)
        lines.append(_format_item(t_title, t_url, t_meta.description))
        seen_slugs: set[str] = set()
        for href in t_meta.topic_post_links:
            p = _normalize_internal_path(href)
            m = POST_HREF_RE.fullmatch(p) if p else None
            if not m:
                continue
            slug = m.group(1)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            post_rel = f"blog/{slug}/index.html"
            post_file = site / post_rel
            if post_file.is_symlink() or not post_file.is_file():
                raise LlmsTxtError(f"tema {tid}: post {slug!r} ausente no _site ({post_rel})")
            if slug not in posts_meta:
                raise LlmsTxtError(f"tema {tid}: post {slug!r} sem arquivo em posts/")
            fm = posts_meta[slug]
            # Nunca incluir posts com index: false (inclui os 33 da poda) nem podcasts.
            if fm.get("index", "").lower() == "false" or fm.get("type", "").lower() == "podcast":
                continue
            post_url = f"/blog/{slug}/"
            post_html = _parse_html(post_file)
            if not _is_indexable(post_rel, post_url, post_html, robots_txt):
                continue
            title = _clean_line(fm.get("title", "")) or _clean_title(post_html.title)
            desc = _clean_line(fm.get("summary", "")) or _clean_line(fm.get("description", "")) or _clean_line(post_html.description)
            lines.append(_format_item(title, post_url, desc, indent="  "))
            total_posts += 1
    if total_posts == 0:
        raise LlmsTxtError("blog/tema/: nenhum post indexável listado nos temas")
    return lines


def build(site: Path, posts: Path) -> tuple[str, int]:
    """Gera _site/llms.txt e devolve (conteúdo, quantidade de links)."""
    out = site / OUTPUT_NAME
    if out.exists() or out.is_symlink():
        raise LlmsTxtError(f"_site/{OUTPUT_NAME} já existe: ele é gerado aqui, tire-o do site-manifest.txt")
    robots_path = site / "robots.txt"
    robots_txt = robots_path.read_text(encoding="utf-8") if robots_path.is_file() else ""
    posts_meta = _load_posts(posts)

    cons_url, cons_meta = _load_required_page(site, "consultoria/index.html", robots_txt)
    port_url, port_meta = _load_required_page(site, "portfolio/index.html", robots_txt)
    blog_url, blog_meta = _load_required_page(site, "blog/index.html", robots_txt)

    consulting_lines = [_format_item(_clean_title(cons_meta.title), cons_url, cons_meta.description)]
    tools_lines = _build_tools_section(site, robots_txt)
    topics_lines = _build_topics_section(site, posts_meta, blog_meta, robots_txt)
    portfolio_blog_lines = [
        _format_item(_clean_title(port_meta.title), port_url, port_meta.description),
        _format_item(_clean_title(blog_meta.title), blog_url, blog_meta.description),
    ]

    sections = [
        f"# {SITE_TITLE}",
        f"> {SITE_QUOTE}",
        SITE_INTRO,
        "## Consultoria\n\n" + "\n".join(consulting_lines),
        "## Ferramentas\n\n" + "\n".join(tools_lines),
        "## Temas\n\n" + "\n".join(topics_lines),
        "## Portfólio e Expert Insights\n\n" + "\n".join(portfolio_blog_lines),
    ]
    text = "\n\n".join(sections) + "\n"
    out.write_text(text, encoding="utf-8")
    count = len(consulting_lines) + len(tools_lines) + len(topics_lines) + len(portfolio_blog_lines)
    return text, count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gera /llms.txt no _site/ a partir das páginas e posts indexáveis.")
    ap.add_argument("--site", default="_site")
    ap.add_argument("--posts", default="blog-src/src/content/posts")
    args = ap.parse_args(argv)
    try:
        _, count = build(Path(args.site), Path(args.posts))
    except LlmsTxtError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    print(f"llms.txt: {count} links indexáveis gravados em /{OUTPUT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
