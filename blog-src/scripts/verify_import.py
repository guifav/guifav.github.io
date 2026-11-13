#!/usr/bin/env python3
"""Verificação da importação do Substack (issue #19), independente do conversor.

Rodar em blog-src/ depois de `npm run build` (lê dist/):
    python3 scripts/verify_import.py --export <pasta do export>

Compara o HTML original da API (no cache) com o HTML publicado de cada post, usando um
parser próprio, e não o substack_convert.py. Garantias verificadas: G1 (lista de posts),
G2 (texto), G3 (links), G4 (imagens e bytes), G7 (podcasts) e G8 (datas).
Não roda no CI: o cache das respostas da API fica fora do repo. Requer Python 3.11+, Pillow com WebP, NumPy e ffmpeg.
"""
from __future__ import annotations

import argparse
import csv
import html as H
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from html.parser import HTMLParser
from datetime import datetime, timedelta
from urllib.parse import unquote, urljoin, urlsplit

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import check_site as CS  # noqa: E402  resolve_urls: o parser WHATWG do Node, que falha fechado sem Node

SKIP = {"politica-de-privacidade", "termos-e-condicoes-de-uso"}
SITE_HOSTS = {"guilhermefavaron.com.br", "www.guilhermefavaron.com.br", "iaaplicada.substack.com"}
CDN_HOSTS = {"substackcdn.com", "substack-post-media.s3.amazonaws.com"}
MANUAL = {"https://download-files.wixmp.com/raw/068472_873a3d1be0a44b4bb1b3f25c463cae3b.pdf": "https://arxiv.org/abs/1706.03762"}
BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "pre", "figure",
         "figcaption", "br", "hr", "aside", "table", "tr", "td", "th", "section", "video", "iframe", "picture"}
SKIP_TAGS = {"script", "style", "svg", "button", "form", "template", "noscript"}
VOID = {"img", "source", "br", "hr", "input", "wbr", "meta", "link", "col", "path", "polyline", "line"}
IMG_BUDGET = 100 * 1024 * 1024
WIDTHS = (728, 1456)
COVER_WIDTH = 1200
# Identidade da imagem: diferença média por canal (0 a 255) entre miniaturas 32x32 em RGB. O arquivo publicado
# precisa ficar abaixo de um teto absoluto e mais perto do seu original do que de QUALQUER outro original do
# corpus (corpo e capas de todos os posts), ignorando os originais praticamente iguais ao dele. Medido na
# #19: a mesma imagem recodificada fica em no máximo 5,5; nenhum dos 1.175 arquivos publicados fica mais perto
# de outro original do que do próprio (menor margem de 0,7, entre capturas quase iguais do mesmo notebook).
SAME_IMAGE_MAX_DIFF = 12.0
DISTINCT_ORIGINALS = 1.0
EPISODE_DATE_TOLERANCE = timedelta(days=2)
# R3 com referência revisada, independente do importador: post renomeado depois de publicado. O card do
# gui-brain com o slug antigo tem o mesmo título do post atual.
RENAMED = {"sistema-de-registros-para-agentes": "a-memoria-dos-meus-agentes-na-pratica"}
# Único href para o domínio antigo fora do conteúdo: o link "Expert Insights" do rodapé, que aponta para o
# Substack só enquanto o formulário novo está desligado (#21). Ligado, o rodapé aponta para a assinatura.
# O texto do link segue EMAIL_LIST_NAME (consts.ts), que trocou "Newsletter" em 12/11/2026 (#55).
FOOTER_LIST_NAME = "Expert Insights"
FOOTER_NEWSLETTER = f'<a href="https://guilhermefavaron.com.br" target="_blank" rel="noopener">{FOOTER_LIST_NAME}</a>'
FOOTER_SUBSCRIBE = f'<a href="/blog/assinar/">{FOOTER_LIST_NAME}</a>'
# Atributos que o navegador lê como URL (srcset, imagesrcset e ping são listas).
URL_ATTRS = {"href", "src", "srcset", "imagesrcset", "poster", "action", "formaction", "cite", "data", "background",
             "xlink:href", "longdesc", "ping", "icon", "manifest", "itemid", "codebase", "archive", "usemap",
             "profile", "lowsrc", "dynsrc"}
# Base dos originais: relativas não viram o domínio antigo por acaso (o corpus só tem "undefined" relativo).
ORIG_BASE = "https://original.invalid/"
ASCII_WS = " \t\n\r\f"


# O que o conversor e o build geram dentro de div.post-body, levantado no corpus. Qualquer outro elemento, classe,
# atributo ou estilo no publicado é conteúdo que não veio do original (ex.: wrapper que esconde o texto).
PUB_TAGS = {"a", "blockquote", "br", "code", "div", "em", "figcaption", "figure", "h2", "h3", "h4", "h5", "h6", "hr",
            "iframe", "img", "li", "ol", "p", "pre", "span", "strong", "ul", "video"}
PUB_CLASSES = {"div": {"callout", "podcast-embed", "pullquote", "video"},
               "pre": {"astro-code", "astro-code-themes", "github-light", "github-dark", "code-html"},
               "span": {"line"}}
PUB_ATTRS = {"a": {"href"}, "div": {"class"}, "h2": {"id"}, "h3": {"id"}, "h4": {"id"}, "h5": {"id"}, "h6": {"id"},
             "iframe": {"allow", "allowfullscreen", "height", "loading", "referrerpolicy", "src", "title"},
             "img": {"alt", "decoding", "height", "loading", "sizes", "src", "srcset", "width"},
             "ol": {"start"}, "pre": {"class", "data-language", "style", "tabindex"}, "span": {"class", "style"},
             "video": {"controls", "height", "playsinline", "poster", "preload", "src", "width"}}
# Estilos que o Shiki (temas github-light e github-dark) gera no corpus: o do <pre> e os seis pares de cor dos
# <span>. Lista exata: cor arbitrária ou transparente (ex.: #0000) não passa.
SHIKI_PRE_STYLE = "--shiki-light:#24292e;--shiki-dark:#e1e4e8;--shiki-light-bg:#fff;--shiki-dark-bg:#24292e; overflow-x: auto;"
SHIKI_SPAN_STYLES = {f"--shiki-light:{a};--shiki-dark:{b}" for a, b in (
    ("#24292E", "#E1E4E8"), ("#D73A49", "#F97583"), ("#005CC5", "#79B8FF"), ("#6A737D", "#6A737D"),
    ("#6F42C1", "#B392F0"), ("#E36209", "#FFAB70"))}
SHIKI_PRE_CLASSES = {"astro-code", "astro-code-themes", "github-light", "github-dark"}


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
        self.video_tags: list[dict] = []
        self.embeds: list[str] = []
        self.ids: set = set()
        # Estrutura: listas (tipo, início, itens), níveis de título e linguagem de cada bloco de código.
        self.lists: list[list] = []
        self.list_stack: list[int] = []
        self.heads: list[int] = []
        self.head_texts: list[tuple[int, str]] = []
        self.captions: list[tuple[int, str]] = []  # (imagens até a legenda, texto)
        self.cap_mark: int | None = None
        self.foreign: list[str] = []
        self.pub_pre_stack: list[tuple[str, bool]] = []
        self.media_depth = 0
        self.li_texts: list[tuple[int, int, str]] = []  # (lista, item, texto do item)
        self.li_marks: list[tuple[int, int, int]] = []
        self.embed_pairs: list[tuple[str, str]] = []  # cards de post embutido: (título, destino)  # no publicado: elemento, classe, atributo ou estilo fora do que a importação gera
        self.img_hrefs: list[str | None] = []  # destino do link que envolve cada imagem, na ordem
        self.pre_mark: int | None = None
        self.pre_texts: list[str] = []  # texto bruto de cada <pre>, com espaços e indentação
        self.sources = 0  # <picture>/<source>: trocariam a imagem exibida sem passar pela conferência
        self.iframe_tags: list[dict] = []
        self.pres: list = []
        self.link_seq: list[tuple] = []  # ("a", href, texto, é imagem) ao fechar; ("card", slug, título) ao abrir
        self.nonws = 0  # caracteres de texto não brancos até aqui (posição estrutural de <hr> e <br>)
        self.marks: list[tuple[str, int]] = []

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
                # A raiz é exatamente <div class="post-body">: classe ou atributo a mais pode esconder o artigo.
                if tag != "div" or cls != {self.root_class} or set(a) != {"class"}:
                    self.foreign.append(f"raiz <{tag} {sorted(a.items())}>")
                if tag not in VOID:
                    self.stack.append((tag, False))
            return
        # Os componentes que a importação remove só são descontados no original; no publicado, tudo conta.
        skip = bool(tag in SKIP_TAGS or (self.root_class is None and (
            (tag == "div" and (cls & {"subscription-widget-wrap-editor", "subscription-widget-wrap", "image-link-expand"})) or (
             tag == "p" and "button-wrapper" in cls and subscribe_href(json.loads(a.get("data-attrs") or "{}").get("url") or "")))))
        if self.active:
            if self.root_class is not None:
                bad = []
                if tag not in PUB_TAGS:
                    bad.append(f"<{tag}>")
                bad += [f"<{tag} {k}>" for k in a if k not in PUB_ATTRS.get(tag, set())]
                bad += [f"<{tag} class={c}>" for c in cls if c not in PUB_CLASSES.get(tag, set())]
                in_shiki = any(t == "pre" and k for t, k in self.pub_pre_stack)
                if tag == "pre":
                    shiki = cls == SHIKI_PRE_CLASSES and a.get("style") == SHIKI_PRE_STYLE and bool(a.get("data-language"))
                    plain = cls == {"code-html"} and set(a) == {"class"}
                    if not (shiki or plain):
                        bad.append(f"<pre {sorted(a.items())}> fora das duas formas geradas (Shiki ou code-html)")
                    self.pub_pre_stack.append(("pre", shiki))
                elif "style" in a and not (tag == "span" and in_shiki and a["style"] in SHIKI_SPAN_STYLES):
                    bad.append(f"<{tag} style={a['style'][:60]!r}>")
                if tag == "span" and "line" in cls and not in_shiki:
                    bad.append("<span class=line> fora do bloco do Shiki")
                if self.media_depth:
                    bad.append(f"<{tag}> dentro de <video>/<iframe> (vira conteúdo alternativo, não aparece)")
                if tag in ("video", "iframe"):
                    self.media_depth += 1
                self.foreign += bad
            if tag in BLOCK:
                self.text.append(" ")
            if tag in ("hr", "br"):
                self.marks.append((tag, self.nonws))
            if tag == "img":
                self.imgs.append(a)
                self.img_hrefs.append(self.open_links[-1][0] if self.open_links else None)
            elif tag == "iframe":
                self.iframes.append(a.get("src", ""))
                self.iframe_tags.append(a)
            elif tag in ("picture", "source"):
                self.sources += 1
            elif tag == "figcaption":
                self.cap_mark = len(self.text)
            elif tag == "video":
                self.videos += 1
                self.video_tags.append(a)
            if a.get("id"):
                self.ids.add(a["id"])
            if tag == "a" and a.get("name"):
                self.ids.add(a["name"])
            if tag in ("ol", "ul"):
                parent = [self.list_stack[-1], self.lists[self.list_stack[-1]][2]] if self.list_stack else None
                self.list_stack.append(len(self.lists))
                self.lists.append([tag, int(a.get("start") or 1) if tag == "ol" else 1, 0, "reversed" in a, [], parent])
            elif tag == "li" and self.list_stack:
                self.lists[self.list_stack[-1]][2] += 1
                self.li_marks.append((self.list_stack[-1], self.lists[self.list_stack[-1]][2], len(self.text)))
                if "value" in a:
                    self.lists[self.list_stack[-1]][4].append((self.lists[self.list_stack[-1]][2], a["value"]))
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.heads.append(int(tag[1]))
                self.head_mark = len(self.text)
            elif tag == "pre":
                self.pres.append(a.get("data-language") if self.root_class else None)
                self.pre_mark = len(self.text)
            elif tag == "code" and self.root_class is None and self.stack and self.stack[-1][0] == "pre":
                lang = next((c[len("language-"):] for c in cls if c.startswith("language-")), None)
                self.pres[-1] = lang
            elif tag == "a":
                self.open_links.append((a.get("href", ""), [], "image-link" in cls))
            if tag == "div" and "digest-post-embed" in cls:
                d = json.loads(a.get("data-attrs") or "{}")
                self.text.append(" " + d.get("title", "") + " ")
                self.nonws += len(re.sub(r"[ \t\n\r\f]", "", d.get("title", "")))
                self.embeds.append(self.post_ids.get(str(d.get("id")), "?"))
                self.embed_pairs.append((" ".join((d.get("title") or "").split()), f"/blog/{self.embeds[-1]}/"))
                self.link_seq.append(("card", self.embeds[-1], self.embed_pairs[-1][0]))
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
                if (was_active or self.active) and tag in ("ol", "ul") and self.list_stack:
                    self.list_stack.pop()
                if (was_active or self.active) and tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.heads:
                    ht = " ".join("".join(self.text[self.head_mark:]).split())
                    if not ht:
                        self.heads.pop()  # título vazio no original não tem conteúdo a preservar
                    else:
                        self.head_texts.append((self.heads[-1], ht))
                if (was_active or self.active) and tag == "li" and self.li_marks:
                    li, n, mark = self.li_marks.pop()
                    self.li_texts.append((li, n, " ".join("".join(self.text[mark:]).split())))
                if (was_active or self.active) and tag == "pre" and self.pub_pre_stack:
                    self.pub_pre_stack.pop()
                if (was_active or self.active) and tag in ("video", "iframe") and self.media_depth:
                    self.media_depth -= 1
                if (was_active or self.active) and tag == "pre" and self.pre_mark is not None:
                    raw = "".join(self.text[self.pre_mark:])
                    # O conversor tira as quebras de linha das pontas de cada bloco (39 blocos em 8 posts do corpus);
                    # a comparação também as ignora. Espaços, indentação e quebras internas contam.
                    self.pre_texts.append(raw.strip("\n").replace("\r\n", "\n"))
                    self.pre_mark = None
                if (was_active or self.active) and tag == "figcaption" and self.cap_mark is not None:
                    self.captions.append((len(self.imgs), " ".join("".join(self.text[self.cap_mark:]).split())))
                    self.cap_mark = None
                if was_active or self.active:
                    if tag in BLOCK:
                        self.text.append(" ")
                    if tag == "a" and self.open_links:
                        href, buf, is_img = self.open_links.pop()
                        self.links.append((href, "".join(buf), is_img))
                        self.link_seq.append(("a", href, "".join(buf), is_img))
                return

    def handle_data(self, data):
        if self.active:
            if self.media_depth and data.strip():
                self.foreign.append(f"texto dentro de <video>/<iframe>: {data.strip()[:40]!r}")
            self.text.append(data)
            self.nonws += len(re.sub(r"[ \t\n\r\f]", "", data))
            for _, buf, _ in self.open_links:
                buf.append(data)

    def norm_text(self) -> str:
        return re.sub(r"[ \t\n\r\f]+", " ", "".join(self.text)).strip()


# Estrutura dos componentes no corpo publicado, levantada no corpus: filhos e pai permitidos, e onde não pode haver
# texto solto. Um wrapper que engole parágrafos (ex.: div.video aberto antes do texto, com o iframe absoluto por
# cima) falha mesmo com elementos e classes da lista fechada.
COMP_CLASSES = ("video", "podcast-embed", "callout", "pullquote")
CHILDREN = {"div.video": {"iframe"}, "div.podcast-embed": {"iframe"}, "div.callout": {"p"}, "div.pullquote": {"p"},
            "figure": {"img", "figure>a", "figcaption"}, "figure>a": {"img"}}
EXACT_CHILDREN = {"div.video": [("iframe", 1)], "div.podcast-embed": [("iframe", 1)], "figure>a": [("img", 1)]}
FIGURE_FORMS = ([("img", 1)], [("figcaption", 1), ("img", 1)], [("figure>a", 1)])
PARENTS = {"div.video": {"ROOT", "li"}, "div.podcast-embed": {"ROOT"}, "div.callout": {"ROOT"},
           "div.pullquote": {"ROOT"}, "figure": {"ROOT"}, "figcaption": {"figure"}, "figure>a": {"figure"},
           "iframe": {"div.video", "div.podcast-embed"}, "img": {"figure", "figure>a"}, "video": {"p"}}
NO_TEXT = {"ROOT", "div.video", "div.podcast-embed", "div.callout", "div.pullquote", "figure", "figure>a", "ul", "ol"}
STRUCT_VOID = {"img", "br", "hr", "source", "wbr"}


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[list] = []  # [chave, Counter de filhos]
        self.on = False
        self.done = False
        self.bad: list[str] = []

    def key(self, tag: str, a: dict) -> str:
        cls = (a.get("class") or "").split()
        if tag == "div":
            c = [x for x in cls if x in COMP_CLASSES]
            return "div." + c[0] if c else "div"
        if tag == "a" and self.stack and self.stack[-1][0] == "figure":
            return "figure>a"
        return tag

    def handle_starttag(self, tag, attrs):
        a = dict((k, v or "") for k, v in attrs)
        if not self.on:
            if not self.done and (a.get("class") or "").split() == ["post-body"]:
                self.on = True
                self.stack = [["ROOT", Counter()]]
            return
        k = self.key(tag, a)
        parent = self.stack[-1][0]
        self.stack[-1][1][k] += 1
        if parent in CHILDREN and k not in CHILDREN[parent]:
            self.bad.append(f"<{k}> dentro de <{parent}>")
        if k in PARENTS and parent not in PARENTS[k]:
            self.bad.append(f"<{k}> com pai <{parent}>")
        if tag not in STRUCT_VOID:
            self.stack.append([k, Counter()])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.on and tag not in STRUCT_VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.on or tag in STRUCT_VOID:
            return
        k, kids = self.stack.pop()
        got = sorted(kids.items())
        if k in EXACT_CHILDREN and got != EXACT_CHILDREN[k]:
            self.bad.append(f"<{k}> com filhos {dict(kids)}")
        if k == "figure" and got not in FIGURE_FORMS:
            self.bad.append(f"<figure> com filhos {dict(kids)}")
        if len(self.stack) == 0:
            self.on, self.done = False, True

    def handle_data(self, data):
        if self.on and self.stack[-1][0] in NO_TEXT and data.strip(" \t\n\r\f"):
            self.bad.append(f"texto solto dentro de <{self.stack[-1][0]}>: {data.strip()[:30]!r}")


def structure_problems(page: str) -> list:
    st = Structure()
    st.feed(page)
    return sorted(set(st.bad))


# Atributos exatos dos iframes que a importação e o leiaute geram (Kimi r8 #4: altura zero escondia o player).
BODY_IFRAME_ATTRS = {
    "youtube": {"allow": "fullscreen; picture-in-picture", "allowfullscreen": "", "loading": "lazy",
                "referrerpolicy": "strict-origin-when-cross-origin", "title": "Vídeo do YouTube"},
    "spotify": {"allow": "encrypted-media", "height": "102", "loading": "lazy",
                "referrerpolicy": "strict-origin-when-cross-origin", "title": "Episódio no Spotify"},
}
BODY_IFRAME_HOSTS = {"youtube": "www.youtube-nocookie.com", "spotify": "open.spotify.com"}


class TopPlayer(HTMLParser):
    """Iframes fora do corpo, com a cadeia de pais (tag e atributos)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.found: list[tuple[dict, list]] = []
        self.body = False

    def handle_starttag(self, tag, attrs):
        a = dict((k, v or "") for k, v in attrs)
        if self.body or "post-body" in (a.get("class") or "").split():
            self.body = True
            return
        if tag == "iframe":
            self.found.append((a, list(self.stack)))
        if tag not in VOID:
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        if self.body:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def aliases_for(old: set, slugs: set) -> dict:
    """R3, calculada aqui: slug antigo sem par exato que é prefixo de exatamente um slug atual, ou RENAMED."""
    out = {}
    for u in old - slugs:
        if u in RENAMED and RENAMED[u] in slugs:
            out[u] = RENAMED[u]
            continue
        cands = [x for x in slugs if x.startswith(u + "-")]
        if len(cands) == 1:
            out[u] = cands[0]
    return out


def old_slugs(html: str) -> set:
    out = set()
    for h in re.findall(r'href="([^"]+)"', html):
        u = urlsplit(h)
        m = re.fullmatch(r"/(p|post)/([^/]+)/?", u.path)
        if (u.hostname or "").lower() in SITE_HOSTS and m:
            out.add(unquote(m.group(2)).lower())
    return out


class Resolver:
    """URLs resolvidas como o navegador (WHATWG, via check_site.resolve_urls), em lote e com memória."""

    def __init__(self):
        self.memo: dict = {}

    def prefetch(self, pairs) -> None:
        todo = sorted({(b, r) for b, r in pairs if (b, r) not in self.memo})
        for k, v in zip(todo, CS.resolve_urls([list(x) for x in todo])):
            self.memo[k] = v

    def get(self, ref: str, base: str = ORIG_BASE) -> dict | None:
        if (base, ref) not in self.memo:
            self.prefetch([(base, ref)])
        return self.memo[(base, ref)]


RES = Resolver()


def url_host(h: str, base: str = ORIG_BASE) -> str:
    """Host como o navegador o resolve (WHATWG): três barras, pontos ideográficos, barra invertida, maiúsculas."""
    r = RES.get(h, base)
    return unquote(r["hostname"]).lower().rstrip(".") if r else ""


def url_path(h: str, base: str = ORIG_BASE) -> str:
    r = RES.get(h, base)
    return r["pathname"] if r else ""


def is_old(h: str, base: str = ORIG_BASE) -> bool:
    host = url_host(h, base)
    return host in SITE_HOSTS or host == "substack.com" or host.endswith(".substack.com")


def css_unescape(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = re.sub(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?", lambda m: chr(int(m.group(1), 16)) if 0 < int(m.group(1), 16) <= 0x10FFFF else "\ufffd", t)
    return re.sub(r"\\(.)", r"\1", t, flags=re.S)


def css_refs(t: str) -> list:
    """Tudo o que o CSS pode carregar: url(...) e strings (image-set, @import), depois dos escapes do CSS."""
    t = css_unescape(t)
    out = [next(g for g in m.groups() if g is not None).strip(ASCII_WS)
           for m in re.finditer(r'url\(\s*(?:"([^"]*)"|\'([^\']*)\'|([^)]*?))\s*\)', t, re.I)]
    # finditer: no findall, o grupo que não participou vem como "" e as strings entre aspas simples se perdiam.
    out += [m.group(1) if m.group(1) is not None else m.group(2) for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'', t)]
    return out


class Refs(HTMLParser):
    """Cada referência a URL de uma página: atributos, CSS (style e <style>) e meta refresh. O tipo separa as
    três referências em que a capa pode estar (og:image, twitter:image) do resto."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            a.setdefault(k, v or "")
        kind = "outro"
        if tag == "meta" and a.get("property") == "og:image":
            kind = "og:image"
        elif tag == "meta" and a.get("name") == "twitter:image":
            kind = "twitter:image"
        for k, v in a.items():
            if k in ("srcset", "imagesrcset"):
                self.refs += [(kind, u) for u in raw_srcset_urls(v)]
            elif k == "ping":
                self.refs += [(kind, u) for u in re.split(f"[{ASCII_WS}]+", v) if u]
            elif k in URL_ATTRS:
                self.refs.append((kind, v))
            elif k == "style":
                self.refs += [("css", u) for u in css_refs(v)]
            elif k == "content" and tag == "meta":
                if a.get("http-equiv", "").lower() == "refresh":
                    # Pela regra do HTML, com ou sem "url=": a mesma função do check_site.
                    u = CS.refresh_url(v)
                    if u:
                        self.refs.append((kind, u))
                else:
                    self.refs.append((kind, v))
        self.in_style = tag == "style"

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.refs += [("css", u) for u in css_refs(data)]


def page_refs(page: str) -> list:
    r = Refs()
    r.feed(page)
    return r.refs + [("JSON-LD", x) for x in ld_strings(page)]


def raw_srcset_urls(v: str) -> list:
    """O primeiro token de cada candidato, mesmo inválido: um endereço antigo num candidato ruim também conta."""
    return [re.split(f"[{ASCII_WS}]+", c.strip(ASCII_WS))[0] for c in v.split(",") if c.strip(ASCII_WS)]


def original_url(url: str) -> str:
    m = re.search(r"/image/fetch/[^/]+/(https?%3A.*)$", url)
    return unquote(m.group(1)) if m else url


def cache_path(cache: str, url: str) -> str:
    return os.path.join(cache, "orig", hashlib.sha256(original_url(url).encode()).hexdigest()[:24])


def thumb(path: str) -> np.ndarray:
    with Image.open(path) as im:
        im.seek(0)
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return np.frombuffer(bg.convert("RGB").resize((32, 32), Image.Resampling.BOX).tobytes(), dtype=np.uint8).astype(np.int16)


def img_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


class Corpus:
    """Miniaturas de todos os originais (corpo e capas), para a identidade contra o corpus inteiro."""

    def __init__(self, cache: str, posts: dict):
        paths = []
        for p in posts.values():
            ex = Extract()
            ex.feed(p["body_html"])
            for i in ex.imgs:
                src = json.loads(i.get("data-attrs") or "{}").get("src") or i.get("src")
                if src and src != "undefined":
                    paths.append(cache_path(cache, src))
            if p.get("cover_image"):
                paths.append(cache_path(cache, p["cover_image"]))
        self.paths = sorted({x for x in paths if os.path.exists(x)})
        self.index = {x: k for k, x in enumerate(self.paths)}
        self.mat = np.stack([thumb(x) for x in self.paths])

    def check(self, path: str, orig: str) -> str | None:
        """None se o arquivo é a imagem do original; senão, a razão."""
        t = thumb(path)
        d = np.abs(self.mat - t).mean(axis=1)
        k = self.index[orig]
        own = float(d[k])
        same = np.abs(self.mat - self.mat[k]).mean(axis=1) <= DISTINCT_ORIGINALS
        others = np.where(~same, d, np.inf)
        j = int(others.argmin())
        if own > SAME_IMAGE_MAX_DIFF or others[j] <= own:
            return f"diferença {own:.1f} para o original; o mais perto é {os.path.basename(self.paths[j])} ({float(others[j]):.1f})"
        return None


def srcset(v: str) -> list:
    """Candidatos (URL, descritor). Exatamente "URL Nw" por candidato: token a mais ou descritor de outro tipo
    invalida o candidato no navegador, então vira ("", "") e falha na comparação das larguras."""
    out = []
    for c in v.split(","):
        # Só os espaços ASCII separam (HTML); NBSP e outros espaços Unicode invalidam o candidato no navegador.
        parts = [x for x in re.split(f"[{ASCII_WS}]+", c) if x]
        if not parts:
            continue
        ok = len(parts) == 2 and re.fullmatch(r"[1-9][0-9]*w", parts[1])
        out.append((parts[0], parts[1]) if ok else ("", ""))
    return out


def check_images(s: str, o_imgs: list, p_imgs: list, dist: str, cache: str, corpus: "Corpus") -> list:
    """G4, imagem a imagem e na ordem: arquivo de origem, alt, variantes, dimensões reais e identidade."""
    probs = []
    for k, (oi, pi) in enumerate(zip(o_imgs, p_imgs), 1):
        data = json.loads(oi.get("data-attrs") or "{}")
        osrc = data.get("src") or oi.get("src")
        oalt = data.get("alt") if data.get("alt") is not None else oi.get("alt", "")
        tag = f"G4 {s}: imagem {k}"
        if "alt" not in pi:
            probs.append(f"{tag}: sem atributo alt")
        elif pi["alt"] != (oalt or ""):
            probs.append(f"{tag}: alt {pi.get('alt')!r} diferente do original {oalt!r}")
        orig = cache_path(cache, osrc)
        if not os.path.exists(orig):
            probs.append(f"{tag}: original fora do cache: {osrc}")
            continue
        with Image.open(orig) as im:
            fmt, (W, H) = im.format, im.size
        src = pi.get("src", "")
        cands = srcset(pi.get("srcset", "")) or [(src, "")]
        paths = {}
        for u, _ in cands + [(src, "")]:
            if not u.startswith(f"/blog/img/{s}/") or not os.path.exists(os.path.join(dist, u[len("/blog/"):])):
                probs.append(f"{tag}: arquivo fora da pasta do post ou ausente: {u}")
            else:
                paths[u] = os.path.join(dist, u[len("/blog/"):])
        if len(paths) != len({u for u, _ in cands} | {src}):
            continue
        rw = data.get("resizeWidth")
        if fmt == "GIF":
            pw, ph = (rw, round(H * rw / W)) if isinstance(rw, int) and not isinstance(rw, bool) and 0 < rw < W else (W, H)
            if not src.endswith(".gif") or pi.get("srcset"):
                probs.append(f"{tag}: GIF publicado como {src} com srcset {pi.get('srcset')!r}")
            elif open(paths[src], "rb").read() != open(orig, "rb").read():
                probs.append(f"{tag}: GIF diferente do original")
            if (pi.get("width"), pi.get("height")) != (str(pw), str(ph)):
                probs.append(f"{tag}: width/height {pi.get('width')}x{pi.get('height')} diferentes de {pw}x{ph}")
            continue
        widths = sorted({x for x in WIDTHS if x < W} | {min(W, WIDTHS[-1])})
        got = sorted(int(d[:-1]) for _, d in cands if d.endswith("w") and d[:-1].isdigit())
        if (len(widths) > 1 or pi.get("srcset")) and got != widths:
            probs.append(f"{tag}: srcset com larguras {got}, esperado {widths}")
        dims = {}
        for u, path in paths.items():
            with Image.open(path) as im:
                dims[u] = im.size
                if im.format != "WEBP":
                    probs.append(f"{tag}: {u} não é WebP ({im.format})")
            x, y = dims[u]
            if x > W or abs(y - round(H * x / W)) > 1:
                probs.append(f"{tag}: {u} com {x}x{y}, fora da proporção do original {W}x{H} ou ampliado")
            why = corpus.check(path, orig)
            if why:
                probs.append(f"{tag}: {u} não é a imagem do original ({why})")
        for u, d in cands:
            if d.endswith("w") and u in dims and dims[u][0] != int(d[:-1]):
                probs.append(f"{tag}: {u} declarado {d} com largura real {dims[u][0]}")
        if sorted({d[0] for d in dims.values()}) != widths:
            probs.append(f"{tag}: arquivos com larguras {sorted({d[0] for d in dims.values()})}, esperado {widths}")
        big = max(dims.values()) if dims else (0, 0)
        if src in dims and dims[src] != big:
            probs.append(f"{tag}: src {src} não é a maior variante")
        # Tamanho de exibição: o resizeWidth do editor do Substack, se menor que o arquivo; senão o arquivo.
        pw, ph = big
        if isinstance(rw, int) and not isinstance(rw, bool) and 0 < rw < big[0]:
            pw, ph = rw, round(big[1] * rw / big[0])
        if (pi.get("width"), pi.get("height")) != (str(pw), str(ph)):
            probs.append(f"{tag}: width/height {pi.get('width')}x{pi.get('height')} diferentes de {pw}x{ph}")
        if pi.get("srcset") and pi.get("sizes") != f"(max-width: 760px) 100vw, {pw if pw < big[0] else 720}px":
            probs.append(f"{tag}: sizes {pi.get('sizes')!r} incoerente com a largura de exibição {pw}")
    return probs


def check_cover(s: str, p: dict, page: str, dist: str, cache: str, corpus: "Corpus") -> list:
    og = re.search(r'<meta property="og:image" content="([^"]*)"', page)
    url = f"https://guifav.dev/blog/img/{s}/capa.jpg"
    if not p.get("cover_image"):
        tw = re.findall(r'<meta name="twitter:image" content="([^"]*)"', page)
        bad = [x for x in ([og.group(1)] if og else []) + ld_post_images(page) + tw if x and f"/blog/img/{s}/" in x]
        return [f"G4 {s}: imagem da pasta do post sem capa no original: {bad}"] if bad else []
    if not og or og.group(1) != url:
        return [f"G4 {s}: og:image {og.group(1) if og else None} diferente de {url}"]
    path = os.path.join(dist, "img", s, "capa.jpg")
    orig = cache_path(cache, p["cover_image"])
    if not os.path.exists(path) or not os.path.exists(orig):
        return [f"G4 {s}: capa ou original da capa ausente"]
    with Image.open(path) as im, Image.open(orig) as om:
        cw = min(om.size[0], COVER_WIDTH)
        ch = round(om.size[1] * cw / om.size[0])
        if im.format != "JPEG" or im.size[0] != cw or abs(im.size[1] - ch) > 1:
            return [f"G4 {s}: capa {im.format} {im.size}, esperado JPEG {cw}x{ch} (proporção do original)"]
    why = corpus.check(path, orig)
    if why:
        return [f"G4 {s}: a capa não é a imagem do original ({why})"]
    imgs = ld_post_images(page)
    if imgs != [url]:
        return [f"G4 {s}: BlogPosting.image {imgs} diferente de [{url}]"]
    tw = re.findall(r'<meta name="twitter:image" content="([^"]*)"', page)
    if tw != [url]:
        return [f"G4 {s}: twitter:image {tw} diferente de [{url}]"]
    return []


def ld_objects(page: str) -> list:
    out = []
    for m in re.finditer(r'<script\b([^>]*)>(.*?)</script>', page, re.S):
        if re.search(r'\btype="application/ld\+json"', m.group(1)):
            try:
                v = json.loads(m.group(2))
            except ValueError:
                continue
            for o in (v if isinstance(v, list) else v.get("@graph", [v]) if isinstance(v, dict) else []):
                if isinstance(o, dict):
                    out.append(o)
    return out


def ld_post_images(page: str) -> list:
    """URLs de image dos BlogPosting do JSON-LD (string, lista ou ImageObject)."""
    out = []
    for o in ld_objects(page):
        if o.get("@type") != "BlogPosting":
            continue
        v = o.get("image")
        for x in (v if isinstance(v, list) else [v] if v is not None else []):
            out.append(x.get("url") if isinstance(x, dict) else x)
    return out


def embed_id(src: str) -> tuple | None:
    """Plataforma e identificador de um iframe do corpo (os dois tipos do corpus)."""
    u = urlsplit(H.unescape(src))
    m = re.fullmatch(r"/embed/([A-Za-z0-9_-]{11})", u.path)
    if (u.hostname or "") in ("www.youtube-nocookie.com", "www.youtube.com", "youtube.com") and m:
        return ("youtube", m.group(1))
    m = re.fullmatch(r"/embed/(episode|show|track)/([A-Za-z0-9]+)", u.path)
    if (u.hostname or "") == "open.spotify.com" and m:
        return ("spotify", m.group(1), m.group(2))
    return None


def ld_strings(page: str) -> list:
    """Todas as strings dos blocos JSON-LD, achados pelo atributo type (o leiaute põe outros atributos antes)."""
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for m in re.finditer(r'<script\b([^>]*)>(.*?)</script>', page, re.S):
        if re.search(r'\btype="application/ld\+json"', m.group(1)):
            try:
                walk(json.loads(m.group(2)))
            except ValueError:
                out.append("JSON-LD inválido")
    return out


def header_text(page: str, pattern: str) -> str | None:
    m = re.search(pattern, page, re.S)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else None


def norm_title(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def expected_href(h: str, slugs: set, aliases: dict, ids: frozenset = frozenset(), own: str = "") -> str | None:
    h = h.strip()
    for prefix, new in MANUAL.items():
        if h.startswith(prefix):
            return new
    if h.startswith("#"):
        # R5: âncora sem alvo no original sai; âncora com alvo precisa ser preservada.
        return h if unquote(h[1:]) in ids else None
    u = urlsplit(h)
    if url_host(h) not in SITE_HOSTS:
        return h
    path = url_path(h)
    m = re.fullmatch(r"/(p|post)/([^/]+)/?", path)
    if m and u.fragment and unquote(m.group(2)).lower() == own:
        # Fragmento em URL do próprio post vale como "#fragmento" (R5 se não houver alvo).
        return "#" + u.fragment if unquote(u.fragment) in ids else None
    if m:
        s = unquote(m.group(2)).lower()
        if s in slugs:
            return f"/blog/{s}/"
        if s in aliases:
            return f"/blog/{aliases[s]}/"
        return h
    if path in ("", "/"):
        return "/blog/"
    return h


def link_text(t: str) -> str:
    return " ".join(t.split())


def original_link_seq(ex: Extract, slugs, aliases, own: str) -> list:
    """Cada link do original como (texto visível, destino esperado), na ordem do documento, com os cards de post
    embutido no lugar deles: trocar destinos entre dois links de mesmo texto também falha."""
    out = []
    for item in ex.link_seq:
        if item[0] == "card":
            out.append((item[2], f"/blog/{item[1]}/"))
            continue
        _, href, text, is_img = item
        if not href:
            continue
        if is_img and (url_host(href) in CDN_HOSTS or href == "undefined"):
            continue
        if subscribe_href(href) or (not is_img and text == ""):
            continue
        e = expected_href(href, slugs, aliases, frozenset(ex.ids), own)
        if e is not None:
            out.append((link_text(text), e))
    return out


def original_links(ex: Extract, slugs, aliases, own: str = "") -> Counter:
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
        e = expected_href(href, slugs, aliases, frozenset(ex.ids), own)
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
    ap.add_argument("--video", default=None, help="MP4 do vídeo nativo (padrão: o .mp4 da pasta do export)")
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
    corpus = Corpus(a.cache, posts)
    # Originais lidos uma vez: ids de cada post (âncora para outro post) e todos os hrefs, resolvidos em lote.
    orig_ex = {}
    for s in expected:
        orig_ex[s] = Extract(post_ids=post_ids)
        orig_ex[s].feed(posts[s]["body_html"])
    RES.prefetch((ORIG_BASE, h) for ex in orig_ex.values() for h in [x[0] for x in ex.links] + [x for x in ex.img_hrefs if x])
    # Toda referência a URL de cada página do build (atributos, CSS, meta refresh, JSON-LD), resolvida em lote.
    page_text, refs_by = {}, {}
    for dirpath, _, files in os.walk(dist):
        for fn in files:
            if fn.endswith(".html"):
                rel = os.path.relpath(os.path.join(dirpath, fn), dist)
                page_text[rel] = open(os.path.join(dist, rel), encoding="utf-8").read()
                refs_by[rel] = page_refs(page_text[rel])
    page_base = {rel: urljoin("https://guifav.dev/blog/", rel.replace(os.sep, "/").removesuffix("index.html")) for rel in refs_by}
    RES.prefetch((page_base[rel], v) for rel, refs in refs_by.items() for _, v in refs)
    with open(os.path.join(a.root, "src", "consts.ts"), encoding="utf-8") as f:
        form = re.search(r"^export const NEWSLETTER_FORM_ENABLED = (true|false);", f.read(), re.M)
    if form is None:
        problems.append("G3 consts.ts: NEWSLETTER_FORM_ENABLED não encontrado (o rodapé depende dele)")
    form_on = bool(form) and form.group(1) == "true"
    video = a.video or next((os.path.join(a.export, f) for f in sorted(os.listdir(a.export)) if f.endswith(".mp4")), None)
    aliases = aliases_for(set().union(*(old_slugs(p["body_html"]) for p in posts.values())), set(expected))
    # O relatório do importador é conferido contra o cálculo daqui, nunca usado como referência.
    with open(os.path.join(a.cache, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    if report.get("aliases") != aliases:
        problems.append(f"G3 relatório: aliases {report.get('aliases')} diferentes do calculado {aliases}")
    report_unresolved = Counter((d["post"], d["href"]) for d in report.get("sem_destino", []))
    unresolved = Counter()
    with open(os.path.join(a.cache, "spotify-episodes.json"), encoding="utf-8") as f:
        episodes = {e["spotifyUrl"]: e for e in json.load(f) if e.get("spotifyUrl")}
    totals = Counter()
    spotify = Counter()
    for s in sorted(expected & built):
        p = posts[s]
        orig = orig_ex[s]
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
        o_links = original_links(orig, set(expected), aliases, s)
        p_links = Counter(h for h, _, _ in pub.links)
        if o_links != p_links:
            problems.append(f"G3 {s}: links divergem: só no original {dict(o_links - p_links)}, só no publicado {dict(p_links - o_links)}")
        else:
            # Cada link, na ordem do documento e com os cards de post embutido no lugar deles, tem o mesmo texto e o
            # mesmo destino do original: trocar destinos entre links de mesmo texto também falha.
            o_seq = original_link_seq(orig, set(expected), aliases, s)
            p_seq = [(link_text(t), h) for h, t, _ in pub.links]
            if o_seq != p_seq:
                k = next((i for i, (x, y) in enumerate(zip(o_seq, p_seq)) if x != y), min(len(o_seq), len(p_seq)))
                problems.append(f"G3 {s}: link {k + 1} diverge (texto, destino, na ordem): original "
                                f"{o_seq[k] if k < len(o_seq) else None}, publicado {p_seq[k] if k < len(p_seq) else None}")
        # Âncora para outro post: o fragmento só pode sair quando não tem alvo no original daquele post.
        for href, _, _ in orig.links:
            if url_host(href) not in SITE_HOSTS or not urlsplit(href).fragment:
                continue
            m = re.fullmatch(r"/(p|post)/([^/]+)/?", url_path(href))
            t = unquote(m.group(2)).lower() if m else None
            t = t if t in expected else aliases.get(t)
            frag = urlsplit(href).fragment
            if t and t != s and (frag in orig_ex[t].ids or unquote(frag) in orig_ex[t].ids):
                problems.append(f"G3 {s}: âncora viva {href} para outro post perdida (o alvo existe no original de {t})")
        for h, n in o_links.items():
            if is_old(h):
                unresolved[(s, h)] += n
        # G4: imagens
        o_imgs = [i for i in orig.imgs if (json.loads(i.get("data-attrs") or "{}").get("src") or i.get("src")) not in ("", "undefined")]
        if len(o_imgs) != len(pub.imgs):
            problems.append(f"G4 {s}: {len(o_imgs)} imagens no original, {len(pub.imgs)} publicadas")
        problems += check_images(s, o_imgs, pub.imgs, dist, a.cache, corpus)
        o_img_hrefs = []
        for im, h in zip(orig.imgs, orig.img_hrefs):
            if (json.loads(im.get("data-attrs") or "{}").get("src") or im.get("src")) in ("", "undefined"):
                continue
            if h is None or url_host(h) in CDN_HOSTS or h == "undefined":
                o_img_hrefs.append(None)  # sem link, ou R7
            else:
                o_img_hrefs.append(expected_href(h, set(expected), aliases, frozenset(orig.ids), s))
        if o_img_hrefs != pub.img_hrefs:
            k = next((i for i, (x, y) in enumerate(zip(o_img_hrefs, pub.img_hrefs)) if x != y), None)
            problems.append(f"G3 {s}: destino do link da imagem {k + 1 if k is not None else '?'} diverge: "
                            f"original {o_img_hrefs[k] if k is not None else o_img_hrefs}, publicado {pub.img_hrefs[k] if k is not None else pub.img_hrefs}")
        problems += check_cover(s, p, page, dist, a.cache, corpus)
        # iframes e vídeo
        o_emb, p_emb = [embed_id(x) for x in orig.iframes], [embed_id(x) for x in pub.iframes]
        if o_emb != p_emb or None in p_emb:
            problems.append(f"G3 {s}: iframes do corpo: original {o_emb}, publicado {p_emb}")
        n_vid = p["body_html"].count("native-video-embed")
        if n_vid != pub.videos:
            problems.append(f"G4 {s}: {n_vid} vídeos nativos no original, {pub.videos} publicados")
        for vt in pub.video_tags:
            vsrc, vpos = vt.get("src", ""), vt.get("poster", "")
            vpath = os.path.join(dist, vsrc[len("/blog/"):]) if vsrc.startswith("/blog/") else ""
            ppath = os.path.join(dist, vpos[len("/blog/"):]) if vpos.startswith("/blog/") else ""
            if "controls" not in vt or vt.get("preload") != "none" or "autoplay" in vt:
                problems.append(f"G4 {s}: vídeo sem controls, com preload {vt.get('preload')!r} ou com autoplay")
            if not video or not os.path.exists(vpath) or not os.path.exists(ppath):
                problems.append(f"G4 {s}: vídeo ou pôster ausente ({vsrc}, {vpos})")
                continue
            if hashlib.sha256(open(vpath, "rb").read()).digest() != hashlib.sha256(open(video, "rb").read()).digest():
                problems.append(f"G4 {s}: vídeo publicado diferente do MP4 fornecido")
            vdims = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                    "-of", "csv=p=0", video], capture_output=True, text=True, check=True).stdout.strip()
            if f"{vt.get('width')},{vt.get('height')}" != vdims:
                problems.append(f"G4 {s}: vídeo com width/height {vt.get('width')}x{vt.get('height')}, o MP4 tem {vdims}")
            vw, vh = (int(x) for x in vdims.split(","))
            with Image.open(ppath) as pim:
                pw, ph = pim.size
            if pw != min(vw, WIDTHS[-1]) or abs(ph - round(vh * pw / vw)) > 1:
                problems.append(f"G4 {s}: pôster {pw}x{ph} fora da proporção do vídeo {vw}x{vh}")
            # Quadro extraído aqui mesmo do MP4 fornecido (1 s), sem usar o que o importador gravou no cache.
            with tempfile.TemporaryDirectory() as td:
                ref = os.path.join(td, "quadro.png")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", video, "-frames:v", "1", ref], check=True)
                if img_diff(thumb(ppath), thumb(ref)) > SAME_IMAGE_MAX_DIFF:
                    problems.append(f"G4 {s}: o pôster não é o quadro do vídeo fornecido")
        # G2: estrutura (listas, títulos, linguagem do código). O h1 do corpo vira h2 (documentado).
        o_heads = [2 if h == 1 else h for h in orig.heads]
        if orig.lists != pub.lists:
            problems.append(f"G2 {s}: listas (tipo, início, itens, reversed, value, lista e item pai) divergem: "
                            f"original {orig.lists}, publicado {pub.lists}")
        if orig.li_texts and sorted(orig.li_texts) != sorted(pub.li_texts):
            k = next((x for x, y in zip(sorted(orig.li_texts), sorted(pub.li_texts)) if x != y), None)
            problems.append(f"G2 {s}: texto de item de lista diverge: original {k}, publicado "
                            f"{next((y for x, y in zip(sorted(orig.li_texts), sorted(pub.li_texts)) if x != y), None)}")
        if pub.foreign:
            problems.append(f"G2 {s}: corpo publicado com o que a importação não gera: {sorted(set(pub.foreign))[:6]}")
        if len(orig.pre_texts) == len(pub.pre_texts):
            for k, (po, pp) in enumerate(zip(orig.pre_texts, pub.pre_texts)):
                if po != pp:
                    i = next((j for j in range(min(len(po), len(pp))) if po[j] != pp[j]), min(len(po), len(pp)))
                    problems.append(f"G2 {s}: bloco de código {k + 1} diverge no caractere {i} (espaços e indentação contam): "
                                    f"original {po[max(0, i - 30):i + 30]!r} | publicado {pp[max(0, i - 30):i + 30]!r}")
                    break
        if o_heads != pub.heads:
            problems.append(f"G2 {s}: níveis de título divergem: original {o_heads}, publicado {pub.heads}")
        else:
            o_ht = [(2 if lv == 1 else lv, t) for lv, t in orig.head_texts]
            for (lo, to), (lp, tp) in zip(o_ht, pub.head_texts):
                if (lo, to) != (lp, tp):
                    problems.append(f"G2 {s}: título h{lo} {to[:60]!r} publicado como h{lp} {tp[:60]!r}")
                    break
        if orig.captions != pub.captions:
            problems.append(f"G2 {s}: legendas (imagem, texto) divergem: original {orig.captions[:3]}, publicado {pub.captions[:3]}")
        if pub.sources:
            problems.append(f"G4 {s}: {pub.sources} <picture>/<source> no corpo; a imagem exibida não passaria pela conferência")
        for h, _, _ in pub.links:
            if h.startswith("#") and unquote(h[1:]) not in pub.ids:
                problems.append(f"G3 {s}: âncora {h} sem alvo no HTML publicado")
        for it in pub.iframe_tags:
            kind = (embed_id(it.get("src", "")) or ("?",))[0]
            want = BODY_IFRAME_ATTRS.get(kind)
            got = {k: v for k, v in it.items() if k != "src"}
            if want is None or got != want or url_host(it.get("src", ""), f"https://guifav.dev/blog/{s}/") != BODY_IFRAME_HOSTS[kind]:
                problems.append(f"G4 {s}: iframe {it.get('src')} com atributos {got}; esperado {want} em {BODY_IFRAME_HOSTS.get(kind)}")
        # Estrutura dos componentes (filhos, pai, texto solto) e posição de cada <hr> e <br>.
        st = structure_problems(page)
        if st:
            problems.append(f"G2 {s}: estrutura dos componentes fora do que a importação gera: {st[:6]}")
        if orig.marks != pub.marks:
            k = next((i for i, (x, y) in enumerate(zip(orig.marks, pub.marks)) if x != y), min(len(orig.marks), len(pub.marks)))
            problems.append(f"G2 {s}: quebras e divisórias divergem (tipo, caracteres antes): original {orig.marks[k:k + 2]}, "
                            f"publicado {pub.marks[k:k + 2]} ({len(orig.marks)} e {len(pub.marks)})")
        decoded = unquote(H.unescape(page))
        n_cover = len(re.findall(re.escape(s) + r"/(?:\./)*capa\.", decoded))
        want_cover = 3 if p.get("cover_image") else 0
        if n_cover != want_cover:
            problems.append(f"G4 {s}: a capa aparece {n_cover} vezes na página decodificada; esperado {want_cover} "
                            f"(og:image, twitter:image e JSON-LD; nunca em mídia, estilo ou outro lugar)")
        # Toda referência da página (atributos, CSS de style e <style> com escapes, meta refresh), resolvida como o
        # navegador (WHATWG: "x/../", "./", barra invertida, percent-encoding): a capa só pode estar em og:image,
        # twitter:image e no JSON-LD.
        cover_path = f"/blog/img/{s}/capa.jpg"
        rel = os.path.join(s, "index.html")
        for kind, v in refs_by.get(rel, []):
            r = RES.get(v, page_base[rel])
            if kind not in ("og:image", "twitter:image", "JSON-LD") and r and r["hostname"] == "guifav.dev" \
                    and unquote(r["pathname"]) == cover_path:
                problems.append(f"G4 {s}: a capa aparece fora de og:image, twitter:image e JSON-LD ({kind}: {v[:80]!r})")
                break
        p_pres = [None if x in (None, "", "plaintext", "text") else x for x in pub.pres]
        if orig.pres != p_pres:
            problems.append(f"G2 {s}: linguagem dos blocos de código diverge: original {orig.pres}, publicado {p_pres}")
        fm = read_fm(os.path.join(md_dir, s + ".md"))
        # G2: título e subtítulo, no frontmatter e no cabeçalho publicado
        title = re.sub(r"\s+", " ", p["title"]).strip()
        sub = re.sub(r"\s+", " ", p.get("subtitle") or "").strip()
        h1 = header_text(page, r'<header class="post-head">.*?<h1>(.*?)</h1>')
        ps = header_text(page, r'<header class="post-head">.*?<p class="post-sub">(.*?)</p>') or ""
        if h1 != title or ps != sub:
            problems.append(f"G2 {s}: cabeçalho publicado {h1!r} / {ps!r} diferente da API {title!r} / {sub!r}")
        # G7 e G8
        if re.sub(r"\s+", " ", fm.get("title", "")).strip() != title or re.sub(r"\s+", " ", fm.get("subtitle", "")).strip() != sub:
            problems.append(f"G2 {s}: frontmatter {fm.get('title')!r} / {fm.get('subtitle')!r} diferente da API")
        want_type = "podcast" if p.get("type") == "podcast" else "newsletter"
        if fm.get("type") != want_type:
            problems.append(f"G7 {s}: type {fm.get('type')!r} no frontmatter, esperado {want_type!r} pela API")
        # Player do topo: antes do corpo (o Spotify dentro do corpo de uma newsletter usa a mesma classe). Atributos
        # exatos do iframe e do div que o envolve, dentro do artigo: altura zero ou wrapper a mais falham.
        tp = TopPlayer()
        tp.feed(page)
        players = [f["src"] for f, _ in tp.found]
        for f, chain in tp.found:
            want = {"src": f.get("src", ""), "title": f"Episódio: {fm.get('title', '')}", "height": "102", "loading": "lazy",
                    "allow": "encrypted-media", "referrerpolicy": "strict-origin-when-cross-origin"}
            if f != want or [(t, x) for t, x in chain[-2:]] != [("article", {"class": "post"}), ("div", {"class": "podcast-embed"})]:
                problems.append(f"G7 {s}: player do topo com atributos {f} dentro de {[t for t, _ in chain[-3:]]}; esperado {want} "
                                f"em article.post > div.podcast-embed")
        if want_type != "podcast" and players:
            problems.append(f"G7 {s}: player de podcast num post que não é podcast: {players}")
        if fm.get("date") != p["post_date"] or fm.get("updated") != p["updated_at"]:
            problems.append(f"G8 {s}: datas {fm.get('date')} {fm.get('updated')} diferentes de {p['post_date']} {p['updated_at']}")
        if p.get("type") == "podcast":
            url = fm.get("spotify_url")
            ep = episodes.get(url)
            if fm.get("index") != "false" or not url:
                problems.append(f"G7 {s}: podcast sem spotify_url ou indexável")
            elif ep is None:
                problems.append(f"G7 {s}: episódio fora do catálogo do Spotify: {url}")
            elif norm_title(ep["title"]) != norm_title(p["title"]) or abs(ts(ep["publishOn"]) - ts(p["post_date"])) > EPISODE_DATE_TOLERANCE:
                problems.append(f"G7 {s}: episódio {url} é {ep['title']!r} de {ep['publishOn'][:10]}, post {p['title']!r} de {p['post_date'][:10]}")
            emb = "https://open.spotify.com/embed/episode/" + (url or "").rstrip("/").rsplit("/", 1)[-1]
            if players != [emb]:
                problems.append(f"G7 {s}: player publicado {players}, esperado [{emb}]")
            spotify[url] += 1
            totals["podcasts"] += 1
        totals["posts"] += 1
        totals["imagens"] += len(pub.imgs)
        totals["links"] += sum(p_links.values())
    dup = [u for u, n in spotify.items() if n > 1]
    if dup:
        problems.append(f"G7: episódio do Spotify repetido: {dup}")

    if unresolved != report_unresolved:
        problems.append(f"G3 relatório: links sem destino divergem: só no calculado {dict(unresolved - report_unresolved)}, "
                        f"só no relatório {dict(report_unresolved - unresolved)}")

    # G3 em todo HTML do build, fora e dentro do conteúdo: cada referência a URL (qualquer atributo de URL, CSS,
    # meta refresh, JSON-LD) que o navegador resolve para o domínio antigo ou o Substack precisa ser um link sem
    # destino daquele post, na mesma quantidade. O link "Newsletter" do rodapé só pode ir ao Substack com o formulário
    # desligado; ligado, toda página com rodapé aponta para /blog/assinar/.
    for rel, refs in sorted(refs_by.items()):
        t = page_text[rel]
        got = Counter(v for _, v in refs if is_old(v, page_base[rel]))
        if not form_on and FOOTER_NEWSLETTER in t:
            got["https://guilhermefavaron.com.br"] -= 1
        if form_on and "<footer>" in t and t.count(FOOTER_SUBSCRIBE) != 1:
            problems.append(f"G3 {rel}: com o formulário ligado, o rodapé precisa apontar para /blog/assinar/ "
                            f"({t.count(FOOTER_SUBSCRIBE)} links)")
        slug = rel.split(os.sep)[0] if rel.endswith(os.sep + "index.html") else None
        allowed_here = Counter({h: n for (ps, h), n in unresolved.items() if ps == slug})
        extra = +(got - allowed_here)
        if extra:
            problems.append(f"G3 {rel}: endereço antigo fora do relatório: {dict(extra)}")

    # G4 no build inteiro: nada do CDN do Substack. G3 no RSS e no sitemap.
    for dirpath, _, files in os.walk(os.path.join(dist)):
        for fn in files:
            if not fn.endswith((".html", ".xml")):
                continue
            t = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            if re.search(r"substackcdn\.com|substack-post-media", t):
                problems.append(f"G4 {fn}: referência ao CDN do Substack")
            if fn.endswith(".xml"):
                for h in re.findall(r'(?:href|src)="([^"]+)"|<link>([^<]+)</link>|<loc>([^<]+)</loc>', t):
                    hh = H.unescape(next(x for x in h if x))
                    if is_old(hh, "https://guifav.dev/blog/"):
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
