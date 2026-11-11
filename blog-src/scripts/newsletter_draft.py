"""Cria o rascunho do email de um post do blog no Resend (issue #21, parte B). Nunca envia.

    RESEND_API_KEY=... NEWSLETTER_SEGMENT_ID=... python3 blog-src/scripts/newsletter_draft.py <slug>
    python3 blog-src/scripts/newsletter_draft.py <slug> --dry-run --out /tmp/email    # só gera os arquivos

Roda na KVM, por linha de comando (decisão do Gui em 2026-11-10: sem GitHub Actions). A chave do Resend
fica só no ambiente da KVM.

Fonte: a página publicada em https://guifav.dev/blog/<slug>/ (ou blog-src/dist com --source dist). O email
sai do mesmo HTML que o leitor vê no site, já limpo pelo contrato da #17. A conversão é por lista fechada,
como no importador: tag, classe, atributo ou esquema de link fora da lista interrompe o script.

Garantias:
- um único broadcast por post, chamado "blog/<slug>": se já existe um rascunho, nada muda (ou, com --update,
  ele é reescrito); se já existe um enviado, agendado ou em envio, o script para;
- o único POST é a criação do rascunho, sem "send"; a classe Api recusa a rota de envio, /emails, DELETE e
  qualquer corpo com "send" ou "scheduled_at";
- HTML acima do limite em que o Gmail corta a mensagem (102 KB) leva só o começo do post e o link;
- rodapé com o link de descadastro do Resend ({{{RESEND_UNSUBSCRIBE_URL}}}) no HTML e no texto.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import html as H
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

SITE = "https://guifav.dev"
BLOG = SITE + "/blog"
RESEND = "https://api.resend.com"
DEFAULT_FROM = "IA Aplicada <newsletter@expert.guifav.dev>"
DEFAULT_REPLY_TO = "contact@guifav.dev"
SENDER_DOMAIN = "expert.guifav.dev"
GMAIL_CLIP_BYTES = 102 * 1024
SIZE_MARGIN = 4 * 1024  # folga para a reescrita de links do rastreamento de clique
IMG_MAX = 560
TRUNCATED_NOTE = "Este email traz só o começo do texto. A versão completa está no site."
PREHEADER_MAX = 200
DEFAULT_LOCK_DIR = os.path.expanduser("~/.cache/guifav-newsletter-draft")
CODE_START, CODE_END = "\x02", "\x03"  # delimitam código no texto puro, que não é normalizado


class DraftError(Exception):
    pass


# ---------------------------------------------------------------- árvore mínima

VOID = {"img", "br", "hr", "source", "meta", "link", "input"}


@dataclass
class El:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = El("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        el = El(tag, {k: (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(el)
        if tag not in VOID:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(El(tag, {k: (v if v is not None else "") for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(s: str) -> El:
    t = _Tree()
    t.feed(s)
    t.close()
    return t.root


def find(el: El, pred) -> El | None:
    for c in el.children:
        if isinstance(c, El):
            if pred(c):
                return c
            r = find(c, pred)
            if r:
                return r
    return None


# ---------------------------------------------------------------- post

@dataclass
class Post:
    slug: str
    title: str
    subtitle: str
    description: str
    url: str
    cover: str
    spotify: str
    body: El


def _spotify_link(src: str) -> str:
    m = re.fullmatch(r"https://open\.spotify\.com/embed/episode/([A-Za-z0-9]+)(?:\?.*)?", src)
    if not m:
        raise DraftError(f"iframe do Spotify fora do formato: {src}")
    return f"https://open.spotify.com/episode/{m.group(1)}"


def parse_post(page: str, slug: str) -> Post:
    root = parse_html(page)
    url = f"{BLOG}/{slug}/"
    canon = find(root, lambda e: e.tag == "link" and e.attrs.get("rel") == "canonical")
    if not canon or canon.attrs.get("href") != url:
        raise DraftError(f"{slug}: a página não é o post (canonical {canon.attrs.get('href') if canon else None})")
    art = find(root, lambda e: e.tag == "article" and e.attrs.get("class") == "post")
    body = find(art, lambda e: e.tag == "div" and e.attrs.get("class") == "post-body") if art else None
    if art is None or body is None:
        raise DraftError(f"{slug}: página sem div.post-body")
    head = find(art, lambda e: e.tag == "header")
    h1 = head and find(head, lambda e: e.tag == "h1")
    if not h1 or not h1.text().strip():
        raise DraftError(f"{slug}: post sem título")
    sub = head and find(head, lambda e: e.tag == "p" and e.attrs.get("class") == "post-sub")
    desc = find(root, lambda e: e.tag == "meta" and e.attrs.get("name") == "description")
    og = find(root, lambda e: e.tag == "meta" and e.attrs.get("property") == "og:image")
    pod = next((c for c in art.children if isinstance(c, El) and c.attrs.get("class") == "podcast-embed"), None)
    spotify = ""
    if pod:
        fr = find(pod, lambda e: e.tag == "iframe")
        spotify = _spotify_link(fr.attrs.get("src", "")) if fr else ""
    post = Post(slug, h1.text().strip(), sub.text().strip() if sub else "",
                desc.attrs.get("content", "").strip() if desc else "", url, "", spotify, body)
    if og and og.attrs.get("content", "").strip():
        post.cover = absolute(og.attrs["content"], post, link=False)
    return post


# ---------------------------------------------------------------- conversão

S_P = "margin:0 0 16px;font-size:17px;line-height:1.6;color:#1a1a1a;"
STYLE = {
    "p": S_P,
    "h2": "margin:32px 0 12px;font-size:24px;line-height:1.3;color:#111;",
    "h3": "margin:28px 0 10px;font-size:20px;line-height:1.35;color:#111;",
    "h4": "margin:24px 0 8px;font-size:18px;line-height:1.4;color:#111;",
    "h5": "margin:20px 0 8px;font-size:17px;line-height:1.4;color:#111;",
    "h6": "margin:20px 0 8px;font-size:16px;line-height:1.4;color:#333;",
    "ul": "margin:0 0 16px;padding-left:24px;",
    "ol": "margin:0 0 16px;padding-left:24px;",
    "li": "margin:0 0 6px;font-size:17px;line-height:1.6;color:#1a1a1a;",
    "a": "color:#0b57d0;text-decoration:underline;",
    "blockquote": "margin:0 0 16px;padding:4px 16px;border-left:3px solid #d0d0d0;color:#444;",
    "figure": "margin:0 0 20px;",
    "figcaption": "margin:6px 0 0;font-size:14px;line-height:1.4;color:#666;",
    # width:100% com max-width na largura real: numa célula de tabela, max-width:100% sozinho não encolhe.
    "img": "display:block;width:100%;height:auto;border:0;",
    "hr": "border:0;border-top:1px solid #e0e0e0;margin:28px 0;",
    "pre": ("margin:0 0 16px;padding:12px;background:#f6f8fa;border-radius:6px;font-family:Menlo,Consolas,monospace;"
            "font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word;color:#1a1a1a;"),
    "code": "font-family:Menlo,Consolas,monospace;font-size:0.9em;background:#f3f3f3;padding:1px 4px;border-radius:3px;",
    "pullquote": "margin:0 0 16px;padding:8px 20px;border-left:4px solid #0b57d0;font-size:19px;font-style:italic;color:#222;",
    "callout": "margin:0 0 16px;padding:12px 16px;background:#f4f6fb;border-radius:6px;",
}
INLINE = {"strong", "em", "b", "i"}
ALLOWED_ATTRS = {
    "a": {"href"}, "img": {"src", "srcset", "sizes", "width", "height", "alt", "loading", "decoding"},
    "ol": {"start"}, "pre": {"class", "style", "tabindex", "data-language"},
    "h2": {"id"}, "h3": {"id"}, "h4": {"id"}, "h5": {"id"}, "h6": {"id"},
    "iframe": {"src", "title", "loading", "allow", "referrerpolicy", "allowfullscreen", "height"},
    "video": {"src", "poster", "width", "height", "controls", "preload", "playsinline"},
    "div": {"class"}, "span": {"class", "style"},
}
DIV_CLASSES = {"pullquote", "callout", "video", "podcast-embed"}


def _attr(v: str) -> str:
    return H.escape(v, quote=True)


def absolute(href: str, post: Post, *, link: bool) -> str:
    href = href.strip()
    if href.startswith("#") and link:
        return post.url + href
    if href.startswith("/") and not href.startswith("//"):
        return SITE + href
    scheme = urllib.parse.urlsplit(href).scheme.lower()
    if scheme == "https" or (scheme == "http" and link) or (scheme == "mailto" and link):
        return href
    raise DraftError(f"{post.slug}: URL fora da lista: {href!r}")


def _check_attrs(el: El, post: Post):
    allowed = ALLOWED_ATTRS.get(el.tag, set())
    extra = set(el.attrs) - allowed
    if extra:
        raise DraftError(f"{post.slug}: atributo fora da lista em <{el.tag}>: {sorted(extra)}")


def _link_block(href: str, label: str) -> str:
    return f'<p style="{S_P}"><a href="{_attr(href)}" style="{STYLE["a"]}">{H.escape(label)}</a></p>'


def _iframe(el: El, post: Post) -> tuple[str, str]:
    src = el.attrs.get("src", "")
    m = re.fullmatch(r"https://www\.youtube-nocookie\.com/embed/([A-Za-z0-9_-]{11})(?:\?.*)?", src)
    if m:
        u = f"https://www.youtube.com/watch?v={m.group(1)}"
        return _link_block(u, "Assistir ao vídeo no YouTube"), f"Assistir ao vídeo no YouTube: {u}"
    if src.startswith("https://open.spotify.com/embed/"):
        u = _spotify_link(src)
        return _link_block(u, "Ouvir no Spotify"), f"Ouvir no Spotify: {u}"
    raise DraftError(f"{post.slug}: iframe fora da lista: {src}")


def _img(el: El, post: Post) -> str:
    src = absolute(el.attrs.get("src", ""), post, link=False)
    try:
        w, h = int(el.attrs.get("width", "0")), int(el.attrs.get("height", "0"))
    except ValueError:
        raise DraftError(f"{post.slug}: dimensão inválida em {src}")
    size, nw = "", IMG_MAX
    if w > 0:
        nw = min(w, IMG_MAX)
        size = f' width="{nw}"' + (f' height="{round(h * nw / w)}"' if h > 0 else "")
    return (f'<img src="{_attr(src)}" alt="{_attr(el.attrs.get("alt", ""))}"{size} '
            f'style="{STYLE["img"]}max-width:{nw}px;">')


class Converter:
    """Converte o corpo em blocos (html, texto). Cada bloco é um filho direto de div.post-body."""

    def __init__(self, post: Post):
        self.post = post

    # --- html
    def html(self, node, in_pre=False) -> str:
        if isinstance(node, str):
            return H.escape(node, quote=False)
        el = node
        _check_attrs(el, self.post)
        t = el.tag
        if in_pre:
            # Bloco realçado: os spans do Shiki saem, fica o texto. Bloco code-html (#19): link e ênfase ficam.
            if t in ("span", "code"):
                return "".join(self.html(c, True) for c in el.children)
            if t in INLINE:
                return f"<{t}>" + "".join(self.html(c, True) for c in el.children) + f"</{t}>"
            if t == "a":
                href = absolute(el.attrs.get("href", ""), self.post, link=True)
                return f'<a href="{_attr(href)}" style="{STYLE["a"]}">' + "".join(self.html(c, True) for c in el.children) + "</a>"
            raise DraftError(f"{self.post.slug}: <{t}> dentro de bloco de código")
        inner = lambda: "".join(self.html(c) for c in el.children)  # noqa: E731
        if t in STYLE and t not in ("img", "hr", "pre", "a", "code", "pullquote", "callout"):
            start = f' start="{_attr(el.attrs["start"])}"' if t == "ol" and "start" in el.attrs else ""
            return f'<{t}{start} style="{STYLE[t]}">{inner()}</{t}>'
        if t in INLINE:
            return f"<{t}>{inner()}</{t}>"
        if t == "a":
            href = absolute(el.attrs.get("href", ""), self.post, link=True)
            return f'<a href="{_attr(href)}" style="{STYLE["a"]}">{inner()}</a>'
        if t == "code":
            return f'<code style="{STYLE["code"]}">{inner()}</code>'
        if t == "br":
            return "<br>"
        if t == "hr":
            return f'<hr style="{STYLE["hr"]}">'
        if t == "img":
            return _img(el, self.post)
        if t == "pre":
            return f'<pre style="{STYLE["pre"]}">' + "".join(self.html(c, True) for c in el.children) + "</pre>"
        if t == "iframe":
            return _iframe(el, self.post)[0]
        if t == "video":
            poster = el.attrs.get("poster", "")
            img = ""
            if poster:
                img = (f'<a href="{_attr(self.post.url)}"><img src="{_attr(absolute(poster, self.post, link=False))}" '
                       f'alt="Vídeo do post" width="{IMG_MAX}" style="{STYLE["img"]}max-width:{IMG_MAX}px;"></a>')
            return f'<div style="{STYLE["figure"]}">{img}{_link_block(self.post.url, "Assistir ao vídeo no site")}</div>'
        if t == "div":
            cls = el.attrs.get("class", "")
            if cls not in DIV_CLASSES:
                raise DraftError(f"{self.post.slug}: div com classe fora da lista: {cls!r}")
            if cls in ("pullquote", "callout"):
                return f'<div style="{STYLE[cls]}">{inner()}</div>'
            return inner()
        if t == "span":
            raise DraftError(f"{self.post.slug}: <span> fora de bloco de código")
        raise DraftError(f"{self.post.slug}: tag fora da lista: <{t}>")

    # --- texto
    def text(self, node, ctx=None) -> str:
        ctx = ctx or {}
        if isinstance(node, str):
            return node if ctx.get("pre") else re.sub(r"\s+", " ", node)
        el, t = node, node.tag
        if ctx.get("pre"):
            inner_pre = "".join(self.text(c, ctx) for c in el.children)
            if t == "a":
                href = absolute(el.attrs.get("href", ""), self.post, link=True)
                return inner_pre if inner_pre.strip() in ("", href) else f"{inner_pre} ({href})"
            return inner_pre
        inner = lambda c=ctx: "".join(self.text(x, c) for x in el.children)  # noqa: E731
        if t in ("h2", "h3", "h4", "h5", "h6"):
            return "\n\n" + inner().strip() + "\n\n"
        if t in ("p", "figcaption", "blockquote", "figure") or (t == "div" and el.attrs.get("class") in ("pullquote", "callout")):
            return "\n\n" + inner().strip() + "\n\n"
        if t in ("ul", "ol"):
            n = int(el.attrs.get("start", "1") or 1)
            items = []
            for c in el.children:
                if isinstance(c, El) and c.tag == "li":
                    mark = f"{n}." if t == "ol" else "-"
                    items.append(f"{mark} " + re.sub(r"\n{2,}", "\n", self.text(c, ctx).strip()))
                    n += 1
            return "\n\n" + "\n".join(items) + "\n\n"
        if t == "li":
            return inner()
        if t == "a":
            raw = inner()
            label = raw.strip()
            href = absolute(el.attrs.get("href", ""), self.post, link=True)
            if not label:
                img = next((c for c in el.children if isinstance(c, El) and c.tag == "img"), None)
                label = (img.attrs.get("alt", "") if img else "").strip()
            out = href if label in ("", href) else f"{label} ({href})"
            lead = " " if raw[:1].isspace() else ""
            trail = " " if raw[-1:].isspace() else ""
            return lead + out + trail
        if t == "br":
            return "\n"
        if t == "hr":
            return "\n\n* * *\n\n"
        if t == "img":
            return ""
        if t == "pre":
            code = "".join(self.text(c, {"pre": True}) for c in el.children).strip("\n")
            return "\n\n" + CODE_START + code + CODE_END + "\n\n"
        if t == "iframe":
            return "\n\n" + _iframe(el, self.post)[1] + "\n\n"
        if t == "video":
            return f"\n\nAssistir ao vídeo no site: {self.post.url}\n\n"
        return inner()

    def blocks(self) -> list[tuple[str, str]]:
        out = []
        for c in self.post.body.children:
            if isinstance(c, str):
                if c.strip():
                    raise DraftError(f"{self.post.slug}: texto solto no corpo: {c.strip()[:40]!r}")
                continue
            out.append((self.html(c), _clean_text(self.text(c))))
        return out


def _clean_text(s: str) -> str:
    """Normaliza espaços e linhas em branco entre blocos; o código (entre CODE_START e CODE_END) fica literal."""
    out = []
    for i, part in enumerate(re.split(f"{CODE_START}(.*?){CODE_END}", s, flags=re.S)):
        if i % 2:
            out.append(part)
        else:
            part = re.sub(r"[ \t]+\n", "\n", part)
            out.append(re.sub(r"\n{3,}", "\n\n", part))
    return "".join(out).strip()


# ---------------------------------------------------------------- envelope

def _envelope(post: Post, body_html: str, truncated: bool) -> str:
    e = H.escape
    pre_text = post.description or post.subtitle
    if len(pre_text) > PREHEADER_MAX:
        pre_text = pre_text[:PREHEADER_MAX - 3].rstrip() + "..."
    pre = e(pre_text)
    cover = (f'<tr><td style="padding:0 0 24px;"><img src="{_attr(post.cover)}" alt="" width="{IMG_MAX}" '
             f'style="{STYLE["img"]}max-width:{IMG_MAX}px;"></td></tr>') if post.cover else ""
    sub = f'<p style="margin:0 0 20px;font-size:19px;line-height:1.45;color:#555;">{e(post.subtitle)}</p>' if post.subtitle else ""
    pod = _link_block(post.spotify, "Ouvir o episódio no Spotify") if post.spotify else ""
    note = f'<p style="{S_P}font-style:italic;color:#555;">{e(TRUNCATED_NOTE)}</p>' if truncated else ""
    cta = ('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 8px;"><tr>'
           f'<td style="background:#111;border-radius:6px;"><a href="{_attr(post.url)}" '
           'style="display:inline-block;padding:12px 22px;color:#fff;text-decoration:none;font-size:16px;font-weight:bold;">'
           'Ler no site</a></td></tr></table>')
    return (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{e(post.title)}</title></head>'
        '<body style="margin:0;padding:0;background:#f5f5f5;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{pre}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;"><tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;">'
        '<tr><td style="padding:28px 20px 0;font-family:Georgia,\'Times New Roman\',serif;overflow-wrap:break-word;word-wrap:break-word;">'
        f'<p style="margin:0 0 20px;font-family:Arial,sans-serif;font-size:13px;letter-spacing:1px;text-transform:uppercase;">'
        f'<a href="{BLOG}/" style="color:#666;text-decoration:none;">IA Aplicada</a></p>'
        f'<h1 style="margin:0 0 12px;font-size:30px;line-height:1.25;color:#111;">{e(post.title)}</h1>{sub}'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + cover + '</table>'
        f'{pod}{body_html}{note}{cta}'
        '</td></tr>'
        '<tr><td style="padding:24px 20px 28px;border-top:1px solid #eee;font-family:Arial,sans-serif;font-size:13px;line-height:1.5;color:#777;">'
        'Você recebe este email porque assinou a newsletter IA Aplicada, de Guilherme Favaron. '
        'Para responder, é só responder a este email.<br>'
        '<a href="{{{RESEND_UNSUBSCRIBE_URL}}}" style="color:#777;">Sair da lista</a> &middot; '
        f'<a href="{BLOG}/privacidade/" style="color:#777;">Privacidade</a>'
        '</td></tr></table></td></tr></table></body></html>'
    )


def _text(post: Post, body_text: str, truncated: bool) -> str:
    parts = ["IA APLICADA", post.title]
    if post.subtitle:
        parts.append(post.subtitle)
    if post.spotify:
        parts.append(f"Ouvir o episódio no Spotify: {post.spotify}")
    parts.append(body_text)
    if truncated:
        parts.append(TRUNCATED_NOTE)
    parts.append(f"Ler no site: {post.url}")
    parts.append("Você recebe este email porque assinou a newsletter IA Aplicada, de Guilherme Favaron.\n"
                 "Sair da lista: {{{RESEND_UNSUBSCRIBE_URL}}}\n"
                 f"Privacidade: {BLOG}/privacidade/")
    return "\n\n".join(p for p in parts if p) + "\n"


def render_email(post: Post) -> tuple[str, str]:
    limit = GMAIL_CLIP_BYTES - SIZE_MARGIN
    blocks = Converter(post).blocks()
    full = _envelope(post, "".join(h for h, _ in blocks), False)
    if len(full.encode("utf-8")) < limit:
        return full, _text(post, "\n\n".join(t for _, t in blocks if t), False)
    budget = limit - len(_envelope(post, "", True).encode("utf-8"))
    if budget <= 0:
        raise DraftError(f"{post.slug}: título e subtítulo sozinhos passam do limite do Gmail")
    used, keep = 0, []
    for h, t in blocks:
        n = len(h.encode("utf-8"))
        if used + n >= budget:
            break
        used += n
        keep.append((h, t))
    if not keep:
        raise DraftError(f"{post.slug}: o primeiro bloco do post sozinho passa do limite do Gmail; o email não teria o começo do texto")
    html = _envelope(post, "".join(h for h, _ in keep), True)
    if len(html.encode("utf-8")) >= limit:
        raise DraftError(f"{post.slug}: email com {len(html.encode('utf-8'))} bytes, acima do limite")
    return html, _text(post, "\n\n".join(t for _, t in keep if t), True)


# ---------------------------------------------------------------- Resend

@dataclass
class Config:
    api_key: str
    segment_id: str
    sender: str = DEFAULT_FROM
    reply_to: str = DEFAULT_REPLY_TO

    @classmethod
    def from_env(cls, env) -> "Config":
        key, seg = env.get("RESEND_API_KEY", ""), env.get("NEWSLETTER_SEGMENT_ID", "")
        if not key or not seg:
            raise DraftError("faltam RESEND_API_KEY e NEWSLETTER_SEGMENT_ID no ambiente")
        sender = env.get("NEWSLETTER_FROM", DEFAULT_FROM)
        if not re.search(r"@" + re.escape(SENDER_DOMAIN) + r">?$", sender):
            raise DraftError(f"o remetente precisa ser de {SENDER_DOMAIN}: {sender}")
        return cls(key, seg, sender, env.get("NEWSLETTER_REPLY_TO", DEFAULT_REPLY_TO))


def _urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


class Api:
    """Só as chamadas que o rascunho precisa. Envio, agendamento e exclusão são recusados aqui."""

    ALLOWED = {("GET", r"/broadcasts"), ("POST", r"/broadcasts"), ("PATCH", r"/broadcasts/[A-Za-z0-9-]+")}

    def __init__(self, cfg: Config, transport=_urllib_transport):
        self.cfg = cfg
        self.transport = transport

    def request(self, method: str, path: str, body: dict | None = None):
        if not any(m == method and re.fullmatch(p, path) for m, p in self.ALLOWED):
            raise DraftError(f"chamada recusada: {method} {path}")
        if body and ({"send", "scheduled_at"} & set(body)):
            raise DraftError(f"chamada recusada: corpo com {sorted({'send', 'scheduled_at'} & set(body))}")
        headers = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": "application/json",
                   "User-Agent": "guifav-blog-newsletter-draft"}
        status, data = self.transport(method, RESEND + path, headers, json.dumps(body) if body is not None else None)
        if not 200 <= status < 300:
            raise DraftError(f"Resend respondeu {status} em {method} {path}: {data.get('message') or data.get('name')}")
        return data


@contextlib.contextmanager
def slug_lock(lock_dir: str, slug: str):
    """Exclusão mútua entre execuções na KVM, do GET ao POST/PATCH: o nome do broadcast não é único no Resend."""
    os.makedirs(lock_dir, exist_ok=True)
    with open(os.path.join(lock_dir, f"{slug}.lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def create_draft(api: Api, post: Post, cfg: Config, *, update: bool = False,
                 lock_dir: str = DEFAULT_LOCK_DIR) -> tuple[str, str]:
    with slug_lock(lock_dir, post.slug):
        return _create_draft(api, post, cfg, update=update)


def _create_draft(api: Api, post: Post, cfg: Config, *, update: bool = False) -> tuple[str, str]:
    html, text = render_email(post)
    name = f"blog/{post.slug}"
    data = api.request("GET", "/broadcasts").get("data") or []
    same = [b for b in data if b.get("name") == name]
    if len(same) > 1:
        raise DraftError(f"{len(same)} broadcasts chamados {name}: resolva no painel do Resend")
    content = {"from": cfg.sender, "reply_to": cfg.reply_to, "subject": post.title, "html": html, "text": text}
    if same:
        b = same[0]
        if b.get("status") != "draft":
            raise DraftError(f"{name} já existe com status {b.get('status')} ({b.get('id')}): nada foi alterado")
        if not update:
            return "existe", b["id"]
        api.request("PATCH", f"/broadcasts/{b['id']}", content)
        return "atualizado", b["id"]
    r = api.request("POST", "/broadcasts", {"segment_id": cfg.segment_id, "name": name, **content})
    return "criado", r["id"]


# ---------------------------------------------------------------- CLI

def fetch_page(slug: str, source: str, dist: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise DraftError(f"slug inválido: {slug!r}")
    if source == "dist":
        path = os.path.join(dist, slug, "index.html")
        if not os.path.exists(path):
            raise DraftError(f"{path} não existe: rode o build antes")
        return open(path, encoding="utf-8").read()
    url = f"{BLOG}/{slug}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "guifav-blog-newsletter-draft"}), timeout=60) as r:
            if r.status != 200 or r.geturl() != url:
                raise DraftError(f"{url} respondeu {r.status} em {r.geturl()}")
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise DraftError(f"{url} respondeu {e.code}: o post precisa estar publicado antes do rascunho")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cria o rascunho do email de um post no Resend. Nunca envia.")
    ap.add_argument("slug")
    ap.add_argument("--source", choices=["live", "dist"], default="live")
    ap.add_argument("--dist", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist"))
    ap.add_argument("--update", action="store_true", help="reescreve o rascunho que já existe")
    ap.add_argument("--dry-run", action="store_true", help="não chama o Resend; grava email.html e email.txt em --out")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    try:
        post = parse_post(fetch_page(a.slug, a.source, a.dist), a.slug)
        html, text = render_email(post)
        size = len(html.encode("utf-8"))
        cut = TRUNCATED_NOTE in html
        if a.dry_run:
            os.makedirs(a.out, exist_ok=True)
            for n, c in (("email.html", html), ("email.txt", text)):
                with open(os.path.join(a.out, n), "w", encoding="utf-8") as f:
                    f.write(c)
            print(f"{a.slug}: {size} bytes de HTML{' (cortado)' if cut else ''}; arquivos em {a.out}")
            return 0
        cfg = Config.from_env(os.environ)
        what, bid = create_draft(Api(cfg), post, cfg, update=a.update)
        print(f"{a.slug}: rascunho {what} ({bid}); {size} bytes de HTML{' (cortado)' if cut else ''}. "
              "O envio é feito pelo Gui no painel do Resend.")
        return 0
    except DraftError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
