"""Conversor do HTML do Substack para Markdown (issue #19).

Funções puras: rede, cache e imagens ficam em import_substack.py. Tudo o que não está no
levantamento do corpus (tag, componente, âncora viva, imagem sem arquivo) levanta
ConvertError: nada some nem entra em silêncio. O texto visível é preservado, e o
verify_import.py confere isso nos 233 posts com um parser próprio.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit


class ConvertError(Exception):
    pass


@dataclass
class Image:
    files: list  # [(largura, caminho dentro do blog: "img/<slug>/<arquivo>")], em ordem crescente de largura
    width: int  # dimensões do maior arquivo
    height: int


@dataclass
class Context:
    slug: str
    slugs: set
    images: dict  # URL original da imagem -> Image
    aliases: dict = field(default_factory=dict)  # slug antigo sem par exato -> slug (R3, só casamento inequívoco)
    replacements: dict = field(default_factory=dict)  # href exato -> href novo (R6, troca feita à mão)
    video: dict | None = None  # {"src", "poster", "width", "height"} do vídeo nativo do post
    post_ids: dict = field(default_factory=dict)  # id do post no Substack (texto) -> slug
    base: str = "/blog/"
    stats: Counter = field(default_factory=Counter)
    unresolved: list = field(default_factory=list)  # links do site antigo sem destino no blog
    ids: set = field(default_factory=set)
    anchor_ids: dict = field(default_factory=dict)  # slug -> ids do original daquele post (âncora para outro post)


SITE_HOSTS = {"guilhermefavaron.com.br", "www.guilhermefavaron.com.br", "iaaplicada.substack.com"}
CDN_HOSTS = {"substackcdn.com", "substack-post-media.s3.amazonaws.com"}
KNOWN_COMPONENTS = {
    "Image2ToDOM", "HighlightedCodeBlockToDOM", "Youtube2ToDOM", "SubscribeWidgetToDOM",
    "ButtonCreateButton", "VideoPlaceholder",
}
VOID = {"img", "source", "br", "hr", "input", "wbr", "path", "polyline", "line", "circle", "rect"}
NOISE = {"button", "svg", "form", "input", "g", "path", "polyline", "line", "circle", "rect"}
INLINE_HTML = {"s": "s", "u": "u", "sup": "sup", "sub": "sub", "del": "del", "mark": "mark"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
IMG_SIZES = "(max-width: 760px) 100vw, 720px"


# ---------------------------------------------------------------------------- árvore


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent

    def get(self, k, default=""):
        v = self.attrs.get(k)
        return default if v is None else v

    @property
    def classes(self):
        return set(self.get("class").split())

    def find(self, tag):
        for c in self.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    return c
                r = c.find(tag)
                if r is not None:
                    return r
        return None

    def ancestor(self, pred):
        n = self.parent
        while n is not None:
            if pred(n):
                return n
            n = n.parent
        return None

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.walk()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def _node(self, tag, attrs):
        d = {}
        for k, v in attrs:
            d.setdefault(k, v)  # atributo repetido: vale o primeiro, como no navegador
        n = Node(tag, d, self.stack[-1])
        self.stack[-1].children.append(n)
        return n

    def handle_starttag(self, tag, attrs):
        n = self._node(tag, attrs)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(fragment: str) -> Node:
    b = _Builder()
    b.feed(fragment)
    b.close()
    return b.root


# ---------------------------------------------------------------------------- texto

WS = re.compile(r"[ \t\n\r\f]+")  # espaço do HTML; o nbsp fica
MD_ESCAPE = set("\\`*_[]<>&~|@")


# Autolink do GFM: "www.", "http(s)://" e email em texto corrido virariam link (G3). O Satteri
# detecta o autolink depois de resolver escapes e entidades; só um comentário HTML vazio quebra a
# sequência, sem mudar o texto visível.
BREAK = "<!---->"


def esc(text: str) -> str:
    out = "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)
    out = re.sub(r"(?i)\b(www)\.", r"\1" + BREAK + ".", out)
    out = re.sub(r"(?i)\b(https?):", r"\1" + BREAK + ":", out)
    out = re.sub(r"(?<=[\w.+-])\\@(?=[\w-])", BREAK + r"\\@", out)
    return out


LINE_START = re.compile(r"^(?:(#)|([-+=>])|(\d+)([.)]))")


def esc_line_start(s: str) -> str:
    m = LINE_START.match(s)
    if not m:
        return s
    if m.group(1) or m.group(2):
        return "\\" + s
    return m.group(3) + "\\" + s[len(m.group(3)):]


def attr(v: str) -> str:
    return html.escape(v, quote=True)


def is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def is_space(ch: str) -> bool:
    return ch == "" or ch.isspace()


# ---------------------------------------------------------------------------- links


_IDN_DOTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
_C0_SPACE = "".join(chr(i) for i in range(0x21))


def browser_host(href: str) -> str:
    """Host como o navegador resolve uma URL absoluta ou relativa ao esquema (WHATWG, sem base): espaços e
    controles das pontas e tab/quebra no meio saem, barra invertida vale barra, qualquer número de barras depois
    de http:, percent-encoding e os pontos ideográficos do IDNA no host. Vazio para o resto (relativa, mailto)."""
    h = re.sub(r"[\t\n\r]", "", href.strip(_C0_SPACE)).replace("\\", "/")
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):(.*)", h, re.S)
    if m:
        if m.group(1).lower() not in ("http", "https", "ws", "wss", "ftp"):
            return ""
        rest = m.group(2).lstrip("/")
    elif h.startswith("//"):
        rest = h.lstrip("/")
    else:
        return ""
    hostport = re.split(r"[/?#]", rest, maxsplit=1)[0].rsplit("@", 1)[-1]
    host = hostport if hostport.startswith("[") else hostport.split(":", 1)[0]
    host = unicodedata.normalize("NFKC", unquote(host)).translate(_IDN_DOTS).casefold()
    return host.rstrip(".")


def is_old_host(host: str) -> bool:
    return host in SITE_HOSTS or host == "substack.com" or host.endswith(".substack.com")


def rewrite(href: str, ctx: Context) -> str | None:
    """Destino do link no blog, ou None para tirar o link e manter o texto (R5)."""
    h = href.strip()
    if h in ctx.replacements:
        ctx.stats["R6"] += 1
        return ctx.replacements[h]
    if h.startswith("#"):
        frag = h[1:]
        if frag and frag in ctx.ids:
            raise ConvertError(f"{ctx.slug}: âncora viva {h} (o Markdown não preserva o id do alvo)")
        ctx.stats["R5"] += 1
        return None
    u = urlsplit(h)
    host = (u.hostname or "").lower()
    bh = browser_host(h)
    if bh != host and is_old_host(bh):
        # O navegador iria ao domínio antigo, mas a forma não é a canônica: as regras abaixo não a reconheceriam.
        raise ConvertError(f"{ctx.slug}: link {h!r} vai a {bh} no navegador, fora da forma canônica (corrigir com R6)")
    if host not in SITE_HOSTS:
        if host == "substack.com" or host.endswith(".substack.com"):
            ctx.unresolved.append(h)  # outro autor ou outra publicação: fica, e vai para o relatório
        return h
    m = re.fullmatch(r"/(p|post)/([^/]+)/?", u.path)
    if m and u.fragment and unquote(m.group(2)).lower() == ctx.slug:
        # Fragmento em URL do próprio post vale como "#fragmento": R5 se não houver alvo. Para outro post,
        # o fragmento sai e o link vai para o topo daquele post (o Markdown não preserva os ids).
        if u.fragment in ctx.ids:
            raise ConvertError(f"{ctx.slug}: âncora viva {h} (o Markdown não preserva o id do alvo)")
        ctx.stats["R5"] += 1
        return None
    if m:
        s = unquote(m.group(2)).lower()
        target = s if s in ctx.slugs else ctx.aliases.get(s)
        if target and u.fragment and (u.fragment in ctx.anchor_ids.get(target, ()) or
                                      unquote(u.fragment) in ctx.anchor_ids.get(target, ())):
            # O fragmento só pode sair quando não tem alvo no original do outro post (o Markdown não preserva ids).
            raise ConvertError(f"{ctx.slug}: âncora viva {h} para outro post (o Markdown não preserva o id do alvo)")
        if s in ctx.slugs:
            ctx.stats["R1" if m.group(1) == "p" else "R2"] += 1
            return f"{ctx.base}{s}/"
        if s in ctx.aliases:
            ctx.stats["R3"] += 1
            return f"{ctx.base}{ctx.aliases[s]}/"
    elif u.path in ("", "/"):
        ctx.stats["R4"] += 1
        return ctx.base
    ctx.unresolved.append(h)
    return h


def md_dest(url: str) -> str:
    url = url.replace("\\", "%5C")
    if re.search(r"[\s()<>]", url):
        return "<" + url.replace("<", "%3C").replace(">", "%3E") + ">"
    return url


# ---------------------------------------------------------------------------- inline (Markdown)


class Emph:
    __slots__ = ("kind", "core", "lead", "trail")

    def __init__(self, kind, core, lead, trail):
        self.kind, self.core, self.lead, self.trail = kind, core, lead, trail


def _inline_tokens(nodes, ctx):
    toks = []
    for c in nodes:
        if isinstance(c, str):
            toks.append(esc(WS.sub(" ", c)))
            continue
        t = c.tag
        if t in NOISE:
            continue
        if t in ("strong", "b", "em", "i"):
            inner = _inline(c.children, ctx)
            core = inner.strip(" ")
            lead = " " if inner[:1] == " " else ""
            trail = " " if inner[-1:] == " " and core else ""
            if not core:
                toks.append(" " if inner else "")
                continue
            toks.append(Emph("**" if t in ("strong", "b") else "*", core, lead, trail))
        elif t == "code":
            code = WS.sub(" ", c.text())
            n = max((len(r) for r in re.findall(r"`+", code)), default=0) + 1
            pad = " " if code.startswith("`") or code.endswith("`") else ""
            toks.append("`" * n + pad + code + pad + "`" * n)
        elif t == "a":
            href = c.get("href")
            dest = rewrite(href, ctx) if href else None
            inner = _inline(c.children, ctx)
            if dest is None:
                toks.append(inner)
            elif not inner.strip(" "):
                ctx.stats["links_sem_texto"] += 1
                toks.append(inner)
            else:
                core = inner.strip(" ")
                toks.append((" " if inner[:1] == " " else "") + f"[{core}]({md_dest(dest)})" + (" " if inner[-1:] == " " else ""))
        elif t == "br":
            toks.append("<br>")
        elif t == "span":
            toks.extend(_inline_tokens(c.children, ctx))
        elif t in INLINE_HTML:
            toks.append(f"<{t}>{_inline(c.children, ctx)}</{t}>")
        else:
            raise ConvertError(f"{ctx.slug}: elemento <{t}> fora do levantamento, dentro de texto")
    return toks


def _render_tokens(toks):
    out = []
    for i, tk in enumerate(toks):
        if not isinstance(tk, Emph):
            out.append(tk)
            continue
        out.append(tk.lead)
        prev = "".join(out)[-1:]
        nxt_tok = toks[i + 1] if i + 1 < len(toks) else ""
        if isinstance(nxt_tok, Emph):
            nxt = nxt_tok.lead[:1] or "\0"
        else:
            nxt = (tk.trail or nxt_tok)[:1]
        d = tk.kind
        first, last = tk.core[0], tk.core[-1]
        opens = not is_space(first) and (not is_punct(first) or is_space(prev) or is_punct(prev))
        closes = not is_space(last) and (not is_punct(last) or is_space(nxt) or (nxt != "\0" and is_punct(nxt)))
        nested_edge = first == "*" or last == "*"
        same_next = isinstance(nxt_tok, Emph) and not nxt_tok.lead
        if opens and closes and not nested_edge and not same_next and prev != "*" and nxt != "\0":
            out.append(d + tk.core + d)
        else:
            tag = "strong" if d == "**" else "em"
            out.append(f"<{tag}>{tk.core}</{tag}>")
        out.append(tk.trail)
    return "".join(out)


def _inline(nodes, ctx):
    return _render_tokens(_inline_tokens(nodes, ctx))


def inline(nodes, ctx) -> str:
    s = re.sub(r" {2,}", " ", _inline(nodes, ctx)).strip(" ")
    return s


# ---------------------------------------------------------------------------- HTML seguro (legendas, asides)


def ser(nodes, ctx) -> str:
    out = []
    for c in nodes:
        if isinstance(c, str):
            out.append(html.escape(WS.sub(" ", c), quote=False))
            continue
        t = c.tag
        if t in NOISE:
            continue
        if t in ("strong", "b"):
            out.append(f"<strong>{ser(c.children, ctx)}</strong>")
        elif t in ("em", "i"):
            out.append(f"<em>{ser(c.children, ctx)}</em>")
        elif t == "code":
            out.append(f"<code>{ser(c.children, ctx)}</code>")
        elif t in INLINE_HTML:
            out.append(f"<{t}>{ser(c.children, ctx)}</{t}>")
        elif t == "span":
            out.append(ser(c.children, ctx))
        elif t == "br":
            out.append("<br>")
        elif t == "a":
            href = c.get("href")
            dest = rewrite(href, ctx) if href else None
            inner = ser(c.children, ctx)
            out.append(inner if dest is None else f'<a href="{attr(dest)}">{inner}</a>')
        else:
            raise ConvertError(f"{ctx.slug}: elemento <{t}> fora do levantamento, em legenda ou destaque")
    return re.sub(r" {2,}", " ", "".join(out)).strip(" ")


# ---------------------------------------------------------------------------- blocos


def _component(n: Node) -> str:
    return n.get("data-component-name")


def figure_block(n: Node, ctx: Context) -> str | None:
    img = n.find("img")
    cap = n.find("figcaption")
    parts = []
    if img is not None:
        data = {}
        if img.get("data-attrs"):
            try:
                data = json.loads(img.get("data-attrs"))
            except json.JSONDecodeError as e:
                raise ConvertError(f"{ctx.slug}: data-attrs de imagem ilegível") from e
        src = data.get("src") or img.get("src")
        if not src or src == "undefined":
            ctx.stats["imagens_quebradas_no_original"] += 1
        else:
            im = ctx.images.get(src)
            if im is None:
                raise ConvertError(f"{ctx.slug}: imagem sem arquivo local: {src}")
            alt = data.get("alt") if data.get("alt") is not None else img.get("alt")
            big = im.files[-1][1]
            # resizeWidth: largura de exibição escolhida no editor do Substack. O arquivo segue inteiro; muda
            # só o tamanho apresentado (width/height e sizes).
            rw = data.get("resizeWidth")
            w, h = im.width, im.height
            sizes = IMG_SIZES
            if isinstance(rw, int) and not isinstance(rw, bool) and 0 < rw < im.width:
                w, h = rw, round(im.height * rw / im.width)
                sizes = f"(max-width: 760px) 100vw, {rw}px"
            tag = f'<img src="{ctx.base}{big}"'
            if len(im.files) > 1:
                tag += ' srcset="' + ", ".join(f"{ctx.base}{p} {fw}w" for fw, p in im.files) + f'" sizes="{sizes}"'
            tag += f' width="{w}" height="{h}" alt="{attr(alt or "")}" loading="lazy" decoding="async">'
            link = data.get("href")
            if not link:
                a = img.ancestor(lambda x: x.tag == "a")
                link = a.get("href") if a is not None else None
            if link and (urlsplit(link).hostname or "").lower() in CDN_HOSTS:
                ctx.stats["R7"] += 1
                link = None
            dest = rewrite(link, ctx) if link else None
            parts.append(f'<a href="{attr(dest)}">{tag}</a>' if dest else tag)
            ctx.stats["imagens"] += 1
    if cap is not None:
        c = ser(cap.children, ctx)
        if c:
            parts.append(f"<figcaption>{c}</figcaption>")
    if not parts:
        return None
    return "<figure>\n" + "\n".join(parts) + "\n</figure>"


def code_block(pre: Node, lang: str = "") -> str:
    code = pre.find("code") or pre
    text = code.text()
    if not lang:
        for cl in code.classes:
            if cl.startswith("language-"):
                lang = cl[len("language-"):]
    text = text[:-1] if text.endswith("\n") else text
    n = max(3, max((len(r) for r in re.findall(r"`{3,}", text)), default=0) + 1)
    return "`" * n + lang + "\n" + text + "\n" + "`" * n


def _pre_html(nodes, ctx: Context) -> str:
    out = []
    for c in nodes:
        if isinstance(c, str):
            out.append(html.escape(c, quote=False))
        elif c.tag in ("span", "code"):
            out.append(_pre_html(c.children, ctx))
        elif c.tag in ("strong", "b"):
            out.append(f"<strong>{_pre_html(c.children, ctx)}</strong>")
        elif c.tag in ("em", "i"):
            out.append(f"<em>{_pre_html(c.children, ctx)}</em>")
        elif c.tag == "a":
            href = c.get("href")
            dest = rewrite(href, ctx) if href else None
            inner = _pre_html(c.children, ctx)
            out.append(inner if dest is None else f'<a href="{attr(dest)}">{inner}</a>')
        else:
            raise ConvertError(f"{ctx.slug}: <{c.tag}> dentro de bloco de código")
    return "".join(out)


def pre_block(pre: Node, ctx: Context) -> str:
    """Bloco de código. Com link ou ênfase dentro, sai como HTML para não perder o link (G3) nem a ênfase."""
    code = pre.find("code") or pre
    inner = [n for n in code.walk()]
    if all(n.tag in ("span", "code") for n in inner):
        return code_block(pre)
    body = _pre_html(code.children, ctx)
    body = body[1:] if body.startswith("\n") else body
    body = body[:-1] if body.endswith("\n") else body
    # Sem <code>: o realce do Shiki age em todo pre>code, inclusive no HTML cru, e achataria o link em texto.
    return f'<pre class="code-html">{body}</pre>'


def iframe_block(n: Node, ctx: Context) -> str:
    src = n.get("src")
    u = urlsplit(src)
    host = (u.hostname or "").lower()
    if host == "open.spotify.com" and re.fullmatch(r"/embed/episode/[A-Za-z0-9]{22}", u.path):
        return (f'<div class="podcast-embed"><iframe src="https://open.spotify.com{u.path}" title="Episódio no Spotify" height="102" '
                'loading="lazy" allow="encrypted-media" referrerpolicy="strict-origin-when-cross-origin"></iframe></div>')
    m = re.fullmatch(r"/embed/([A-Za-z0-9_-]{11})", u.path)
    if host in ("www.youtube-nocookie.com", "www.youtube.com") and m:
        return youtube(m.group(1))
    raise ConvertError(f"{ctx.slug}: iframe fora do levantamento: {src}")


def youtube(video_id: str) -> str:
    return (f'<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/{video_id}?rel=0" title="Vídeo do YouTube" '
            'loading="lazy" allow="fullscreen; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin">'
            "</iframe></div>")


def aside_block(n: Node, ctx: Context, cls: str) -> str:
    inner = []
    for c in n.children:
        if isinstance(c, str):
            if c.strip():
                raise ConvertError(f"{ctx.slug}: texto solto em {cls}")
            continue
        if c.tag != "p":
            raise ConvertError(f"{ctx.slug}: <{c.tag}> dentro de {cls}")
        inner.append(f"<p>{ser(c.children, ctx)}</p>")
    return f'<div class="{cls}">\n' + "\n".join(inner) + "\n</div>"


def _subscribe_href(href: str) -> bool:
    u = urlsplit(href)
    host = (u.hostname or "").lower()
    return host.endswith("substack.com") and (
        u.path.startswith("/subscribe") or "action=share" in u.query or u.path.endswith("/share") or u.path.startswith("/account"))


def _prefix(block: str, first: str, rest: str) -> str:
    lines = block.split("\n")
    return "\n".join((first if i == 0 else rest) + ln if ln else (first.rstrip() if i == 0 else rest.rstrip()) for i, ln in enumerate(lines))


def list_block(n: Node, ctx: Context) -> str:
    ordered = n.tag == "ol"
    start = int(n.get("start") or 1) if ordered else 1
    items = []
    for c in n.children:
        if isinstance(c, str):
            if c.strip():
                raise ConvertError(f"{ctx.slug}: texto solto em lista")
            continue
        if c.tag != "li":
            raise ConvertError(f"{ctx.slug}: <{c.tag}> direto em lista")
        marker = f"{start + len(items)}." if ordered else "-"
        bl = blocks(c, ctx)
        body = "\n\n".join(bl)
        pad = " " * (len(marker) + 1)
        items.append((len(bl), _prefix(body, marker + " ", pad) if body else marker))
    tight = all(k <= 1 for k, _ in items)
    return ("\n" if tight else "\n\n").join(s for _, s in items)


def blocks(node: Node, ctx: Context) -> list[str]:
    out: list[str] = []
    run: list = []

    def flush():
        if run:
            s = inline(run, ctx)
            if s:
                out.append(esc_line_start(s))
            run.clear()

    for c in node.children:
        if isinstance(c, str):
            run.append(c)
            continue
        t = c.tag
        cls = c.classes
        comp = _component(c)
        if comp and comp not in KNOWN_COMPONENTS and t != "iframe":
            raise ConvertError(f"{ctx.slug}: componente do Substack fora do levantamento: {comp}")
        if t in NOISE:
            continue
        if t in ("a", "strong", "b", "em", "i", "code", "span", "br") or t in INLINE_HTML:
            if t == "a" and ("image-link" in cls or c.find("img") is not None):
                flush()
                b = figure_block(c, ctx)
                if b:
                    out.append(b)
                continue
            run.append(c)
            continue
        flush()
        if t == "p":
            if comp == "ButtonCreateButton" or "button-wrapper" in cls:
                a = c.find("a")
                href = a.get("href") if a is not None else ""
                if not href or _subscribe_href(href):
                    ctx.stats["removidos"] += 1
                    continue
                s = inline([a], ctx)
                if s:
                    out.append(esc_line_start(s))
                continue
            s = inline(c.children, ctx)
            if s:
                out.append(esc_line_start(s))
        elif t in HEADINGS:
            level = int(t[1])
            if level == 1:
                ctx.stats["h1_para_h2"] += 1
                level = 2
            s = inline(c.children, ctx)
            if s.endswith("#"):
                s = s[:-1] + "\\#"
            if s:
                out.append("#" * level + " " + s)
        elif t in ("ul", "ol"):
            out.append(list_block(c, ctx))
        elif t == "blockquote":
            inner = "\n\n".join(blocks(c, ctx))
            if inner:
                out.append(_prefix(inner, "> ", "> ").replace("\n>\n", "\n>\n"))
        elif t == "pre":
            out.append(pre_block(c, ctx))
        elif t == "hr":
            # "***" e não "---": no começo do corpo, "---" logo depois do frontmatter engolia o bloco seguinte.
            out.append("***")
        elif t == "figure":
            b = figure_block(c, ctx)
            if b:
                out.append(b)
        elif t == "iframe":
            out.append(iframe_block(c, ctx))
        elif t == "div":
            if comp == "SubscribeWidgetToDOM" or "subscription-widget-wrap-editor" in cls or "subscription-widget-wrap" in cls:
                ctx.stats["removidos"] += 1
            elif "image-link-expand" in cls:
                continue
            elif "captioned-image-container" in cls or comp == "Image2ToDOM" or "image2" in cls:
                b = figure_block(c, ctx)
                if b:
                    out.append(b)
            elif comp == "Youtube2ToDOM" or "youtube-wrap" in cls:
                data = json.loads(c.get("data-attrs") or "{}")
                vid = data.get("videoId")
                if not vid or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                    raise ConvertError(f"{ctx.slug}: YouTube sem videoId válido")
                out.append(youtube(vid))
            elif comp == "HighlightedCodeBlockToDOM" or "highlighted_code_block" in cls:
                data = json.loads(c.get("data-attrs") or "{}")
                pre = c.find("pre")
                if pre is None:
                    raise ConvertError(f"{ctx.slug}: bloco de código sem <pre>")
                out.append(code_block(pre, data.get("language") or ""))
            elif "pullquote" in cls:
                out.append(aside_block(c, ctx, "pullquote"))
            elif "callout-block" in cls:
                out.append(aside_block(c, ctx, "callout"))
            elif "digest-post-embed" in cls:
                data = json.loads(c.get("data-attrs") or "{}")
                title, target = data.get("title"), ctx.post_ids.get(str(data.get("id")))
                if not title or not target:
                    raise ConvertError(f"{ctx.slug}: post embutido sem título ou com id fora do blog: {data.get('id')}")
                ctx.stats["R1"] += 1
                out.append(f"[{esc(WS.sub(' ', title).strip())}]({ctx.base}{target}/)")
            elif comp == "VideoPlaceholder" or "native-video-embed" in cls:
                v = ctx.video
                if not v:
                    raise ConvertError(f"{ctx.slug}: vídeo nativo sem arquivo local")
                out.append(f'<video src="{attr(v["src"])}" poster="{attr(v["poster"])}" width="{v["width"]}" height="{v["height"]}" '
                           "controls preload=\"none\" playsinline></video>")
            else:
                out.extend(blocks(c, ctx))
        else:
            raise ConvertError(f"{ctx.slug}: elemento <{t}> fora do levantamento, em bloco")
    flush()
    return _separate_lists(out)


LIST_SEPARATOR = "<!---->"


def _list_kind(block: str) -> str | None:
    if re.match(r"\d+\. ", block):
        return "ol"
    if block.startswith("- "):
        return "ul"
    return None


def _separate_lists(out: list) -> list:
    """Duas listas do mesmo tipo seguidas viram uma só em Markdown; um comentário vazio entre elas as separa."""
    res, last = [], None
    for b in out:
        if b and last is not None and _list_kind(b) and _list_kind(b) == _list_kind(last):
            res.append(LIST_SEPARATOR)
        res.append(b)
        if b:
            last = b
    return res


def anchor_ids(fragment: str) -> set:
    """Ids do HTML original de um post: alvos possíveis de âncora vindos de outros posts."""
    return {n.get("id") for n in parse(fragment).walk() if n.get("id")}


def convert(fragment: str, ctx: Context) -> str:
    root = parse(fragment)
    ctx.ids = {n.get("id") for n in root.walk() if n.get("id")}
    bl = [b for b in blocks(root, ctx) if b]
    return "\n\n".join(bl) + "\n" if bl else ""


# ---------------------------------------------------------------------------- frontmatter e podcasts


def _q(s: str) -> str:
    return json.dumps(WS.sub(" ", s).strip(), ensure_ascii=False)


def frontmatter(post: dict, cover: str | None = None, spotify_url: str | None = None) -> str:
    lines = ["---", f"title: {_q(post['title'])}"]
    if post.get("subtitle") and post["subtitle"].strip():
        lines.append(f"subtitle: {_q(post['subtitle'])}")
    lines.append(f"description: {_q(post['description'])}")
    lines.append(f"date: {post['post_date']}")
    lines.append(f"updated: {post['updated_at']}")
    lines.append(f"slug: {post['slug']}")
    if cover:
        lines.append(f"cover: {cover}")
    podcast = post.get("type") == "podcast"
    lines.append(f"type: {'podcast' if podcast else 'newsletter'}")
    if podcast:
        if not spotify_url:
            raise ConvertError(f"{post['slug']}: podcast sem spotify_url")
        lines.append(f"spotify_url: {_q(spotify_url)}")
        lines.append("index: false")
    lines.append("---")
    return "\n".join(lines) + "\n"


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


MAX_PODCAST_DAYS = 2


def match_podcasts(posts: list, episodes: list) -> dict:
    """slug -> spotifyUrl. Título normalizado, desempate pela data mais próxima, um para um."""
    by = {}
    for e in episodes:
        by.setdefault(norm_title(e["title"]), []).append(e)
    used, out = set(), {}
    for p in sorted(posts, key=lambda x: x["post_date"]):
        cands = [e for e in by.get(norm_title(p["title"]), []) if e["spotifyUrl"] not in used]
        if not cands:
            raise ConvertError(f"{p['slug']}: podcast sem episódio no Spotify com o mesmo título")
        pd = _ts(p["post_date"])
        best = min(cands, key=lambda e: abs((_ts(e["publishOn"]) - pd).total_seconds()))
        if abs((_ts(best["publishOn"]) - pd).total_seconds()) > MAX_PODCAST_DAYS * 86400:
            raise ConvertError(f"{p['slug']}: episódio do Spotify a mais de {MAX_PODCAST_DAYS} dias do post")
        used.add(best["spotifyUrl"])
        out[p["slug"]] = best["spotifyUrl"]
    return out
