"""Prévia privada de um rascunho do blog em guifav.dev/admin/rascunhos (issue #62). Roda na KVM.

    python3 blog-src/scripts/draft_preview.py enviar <rascunho.md> [--imagens DIR] [--dry-run --out DIR]
    python3 blog-src/scripts/draft_preview.py status <rascunho.md> [--imagens DIR]
    python3 blog-src/scripts/draft_preview.py remover <slug>
    python3 blog-src/scripts/draft_preview.py publicar <slug> --versao <versão> --saida DIR   (só no publicar.yml)

O rascunho nunca entra no repo antes da aprovação do Gui: o Markdown e as imagens ficam fora dele (ex.:
/root/.roger/inbox/blog/rascunhos/). O "enviar":

1. lê a fonte uma vez só, para uma cópia temporária no formato <slug>.md e img/<caminho dentro de --imagens>; o
   título, o build, o source_sha256 e os bytes enviados saem todos dessa cópia (#82);
2. copia o blog-src para uma pasta temporária, põe o rascunho em src/content/posts/<slug>.md e as imagens em
   public/img/<slug>/, e faz o build do Astro com DRAFT_PREVIEW=1 (sem GA4, sem Clarity, tudo com noindex);
3. pega a página do post e os arquivos dele (img/<slug>/ e video/<slug>.*) e troca /blog/img/<slug>/ e
   /blog/video/<slug>. por /admin/rascunhos/<slug>/...; confere que nada ficou de fora e que não há analytics;
4. envia ao bucket R2 privado blog-rascunhos, numa pasta própria da versão (<slug>/v/<versão>/), os arquivos da
   prévia e a fonte em fonte/ (<slug>/v/<versão>/fonte/<slug>.md e fonte/img/...), e, por último, o manifesto
   <slug>/manifest.json, que aponta para ela. Se o envio parar no meio, o manifesto continua apontando para a
   versão anterior inteira: o painel nunca mistura duas versões.

A versão é o sha256 do HTML junto com o source_sha256 (Markdown + imagens). A aprovação no painel grava os dois.
O "status" recalcula o source_sha256 e lê no D1 do painel se existe aprovação para ele: o PR de publicação só sai
com a fonte aprovada.

Manifesto: além de "files" (caminho -> content-type, o que o painel serve), o campo "fonte" (#82) lista cada arquivo
da fonte pelo caminho relativo a fonte/, com o sha256 dele ({"<slug>.md": "...", "img/a.webp": "..."}). O Worker só
serve o que está em "files", então a fonte nunca sai pelo painel. O conteúdo de fonte/ é exatamente o que gerou o
source_sha256 da versão: baixada para uma pasta, fonte_sha256(pasta, slug) devolve o mesmo hash da aprovação. O
envio confere isso antes de mandar o primeiro objeto. Derivados (--linkedin, --derivado) levam a fonte pela mesma
regra. Manifestos de antes do #82, sem "fonte", continuam válidos. O "remover" apaga o manifesto, a prévia e a fonte
da versão atual, nessa ordem, e confere toda chave antes do primeiro delete.

A simulação (--dry-run --out DIR) grava em DIR o que seria enviado, com a fonte em DIR/fonte/; DIR precisa estar vazia
ou não existir.

O "publicar" (#82, fatia 3) é o passo central do .github/workflows/publicar.yml, que roda num checkout da main quando
o painel aprova um artigo. Ele recusa, antes de gravar qualquer arquivo, se: o slug ou a versão não têm o formato; o
post já existe na main; o D1 não tem aprovação cujo HTML e fonte deem exatamente essa versão; o manifesto atual do R2
não é dessa versão, é de um derivado ou não tem fonte; algum caminho da fonte sai de <slug>.md e img/, ou é uma
extensão que a barreira 2 do CONTRATO-DE-CONTEUDO.md não aceita em public/ (SVG, por exemplo); o sha256 de algum
arquivo baixado não bate com o manifesto; ou o fonte_sha256 da fonte baixada não é o source_sha256 da aprovação.
Passando tudo, grava o Markdown em src/content/posts/<slug>.md e as imagens em public/img/<slug>/, como o enviar
monta a prévia, e escreve em DIR o título, o corpo do PR (com a evidência) e a lista dos arquivos gravados. Quem abre
o branch publica/<slug>-<versão8> e o PR é a workflow.

Credenciais: CLOUDFLARE_API_TOKEN e CLOUDFLARE_ACCOUNT_ID no ambiente (env da KVM), usadas pelo wrangler. O
"publicar" não usa o wrangler: lê o D1 pela API REST (CLOUDFLARE_ACCOUNT_ID e CF_PUBLICAR_API_TOKEN) e o R2 pela API
S3 com o aws CLI (R2_ACCESS_KEY_ID e R2_SECRET_ACCESS_KEY), os segredos do publicar.yml, todos só de leitura.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BLOG_SRC = Path(__file__).resolve().parent.parent
REPO = BLOG_SRC.parent
ADMIN_WORKER = REPO / "workers" / "admin"
BUCKET = "blog-rascunhos"
D1_NAME = "blog-admin"
SITE = "https://guifav.dev"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
TYPES = {
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".avif": "image/avif", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
}
HTML_TYPE = "text/html; charset=utf-8"
ANALYTICS = ("googletagmanager.com", "clarity.ms", "/assets/track.js", "/assets/consent.js", "gtag(")
COPY_IGNORE = shutil.ignore_patterns("node_modules", "dist", ".astro", "__pycache__")


class DraftError(Exception):
    pass


@dataclass
class Preview:
    slug: str
    title: str
    html: bytes
    files: dict[str, tuple[bytes, str]]  # caminho relativo -> (conteúdo, content-type), sem o index.html


def slug_of(md: Path) -> str:
    slug = md.stem
    if md.suffix != ".md" or not SLUG_RE.match(slug):
        raise DraftError(f"nome do rascunho precisa ser <slug>.md com slug em minúsculas e hífens: {md.name!r}")
    return slug


def image_files(images: Path | None) -> list[Path]:
    if images is None:
        return []
    if not images.is_dir():
        raise DraftError(f"pasta de imagens não existe: {images}")
    out = []
    for p in sorted(images.rglob("*")):
        if p.is_file():
            rel = p.relative_to(images).as_posix()
            if not FILE_RE.match(rel) or p.suffix.lower() not in TYPES:
                raise DraftError(f"arquivo de imagem fora da lista ({', '.join(sorted(TYPES))}) ou com nome inválido: {rel!r}")
            out.append(p)
    return out


def source_sha256(md: Path, images: Path | None) -> str:
    """Hash do que gera a prévia: o Markdown e cada imagem, com o caminho relativo (renomear muda o hash)."""
    h = hashlib.sha256()
    h.update(b"md\0" + md.read_bytes() + b"\0")
    for p in image_files(images):
        rel = p.relative_to(images).as_posix().encode()  # type: ignore[arg-type]
        h.update(b"img\0" + rel + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


# Fonte do rascunho (#82): o Markdown e as imagens, guardados no R2 em <slug>/v/<versão>/fonte/ para o PR de
# publicação sair do bucket, sem depender de arquivo na máquina de quem enviou. O formato da pasta é o mesmo aqui
# (snapshot) e no R2: <slug>.md e img/<caminho dentro de --imagens>. Assim o source_sha256 de uma pasta fonte/ baixada
# é o fonte_sha256 dela, pelo mesmo source_sha256 de sempre, e confere byte a byte com o da aprovação.
FONTE_MD_TYPE = "text/markdown; charset=utf-8"


def fonte_paths(pasta: Path, slug: str) -> tuple[Path, Path | None]:
    """O Markdown e a pasta de imagens (None se não houver) de uma pasta no formato de fonte/."""
    img = pasta / "img"
    return pasta / f"{slug}.md", (img if img.is_dir() else None)


def fonte_sha256(pasta: Path, slug: str) -> str:
    """source_sha256 de uma pasta no formato de fonte/: a cópia local no envio, a pasta baixada do R2 na publicação."""
    return source_sha256(*fonte_paths(pasta, slug))


def fonte_files(pasta: Path, slug: str) -> dict[str, bytes]:
    """Arquivos de uma pasta no formato de fonte/, por caminho relativo: os mesmos que entram no fonte_sha256."""
    md, img = fonte_paths(pasta, slug)
    out = {f"{slug}.md": md.read_bytes()}
    for p in image_files(img):
        out["img/" + p.relative_to(img).as_posix()] = p.read_bytes()  # type: ignore[arg-type]
    return out


def snapshot(md: Path, images: Path | None, dest: Path) -> Path:
    """Cópia da fonte em dest, no formato de fonte/. O envio lê a fonte uma vez só, para esta cópia, e daí saem o
    build, o source_sha256 e os bytes enviados: um arquivo trocado no meio do envio não separa a prévia da fonte."""
    slug = slug_of(md)
    imgs = image_files(images)
    dest.mkdir(parents=True)
    shutil.copyfile(md, dest / f"{slug}.md")
    for p in imgs:
        t = dest / "img" / p.relative_to(images)  # type: ignore[arg-type]
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, t)
    return dest


def fonte_valida(fonte: dict, slug: str) -> None:
    """Caminhos da fonte com as regras de FILE_RE e do slug: o <slug>.md e imagens em img/, nada mais."""
    if not isinstance(fonte, dict) or not isinstance(fonte.get(f"{slug}.md"), bytes):
        raise DraftError(f"fonte sem o Markdown {slug}.md")
    for rel, data in fonte.items():
        ok = isinstance(rel, str) and FILE_RE.fullmatch(rel) is not None and isinstance(data, bytes) and (
            rel == f"{slug}.md" or (rel.startswith("img/") and Path(rel).suffix.lower() in TYPES))
        if not ok:
            raise DraftError(f"caminho inválido na fonte: {rel!r} (só {slug}.md e imagens em img/)")


def frontmatter_title(md_text: str) -> str:
    m = re.match(r"---\n(.*?)\n---\n", md_text, flags=re.S)
    if not m:
        raise DraftError("rascunho sem frontmatter (--- no começo)")
    t = re.search(r"^title:\s*(.+)$", m.group(1), flags=re.M)
    if not t:
        raise DraftError("frontmatter sem title")
    v = t.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def build(md: Path, images: Path | None, work: Path, run=subprocess.run, env: dict | None = None) -> Path:
    """Build do blog com o rascunho numa cópia temporária do blog-src. Devolve a pasta dist."""
    slug = slug_of(md)
    if (BLOG_SRC / "src" / "content" / "posts" / f"{slug}.md").exists():
        raise DraftError(f"já existe um post publicado com o slug {slug!r}; a prévia é só para post novo")
    src = work / "blog-src"
    shutil.copytree(BLOG_SRC, src, ignore=COPY_IGNORE, symlinks=True)
    (src / "node_modules").symlink_to(BLOG_SRC / "node_modules")
    shutil.copy2(md, src / "src" / "content" / "posts" / f"{slug}.md")
    imgs = image_files(images)
    if imgs:
        dest = src / "public" / "img" / slug
        for p in imgs:
            t = dest / p.relative_to(images)  # type: ignore[arg-type]
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)
    e = dict(os.environ if env is None else env)
    e["DRAFT_PREVIEW"] = "1"
    e["ASTRO_TELEMETRY_DISABLED"] = "1"
    r = run(["npm", "run", "build"], cwd=src, env=e, capture_output=True, text=True)
    if r.returncode != 0:
        raise DraftError("build do rascunho falhou:\n" + (r.stdout or "")[-3000:] + (r.stderr or "")[-3000:])
    return src / "dist"


def collect(dist: Path, slug: str, title: str) -> Preview:
    """Página do post com os caminhos dos arquivos dele trocados para /admin/rascunhos/<slug>/, e os arquivos."""
    page = dist / slug / "index.html"
    if not page.is_file():
        raise DraftError(f"o build não gerou /blog/{slug}/")
    html = page.read_text(encoding="utf-8")
    base = f"/admin/rascunhos/{slug}/"
    for old, new in ((f"{SITE}/blog/img/{slug}/", f"{SITE}{base}img/{slug}/"), (f"/blog/img/{slug}/", f"{base}img/{slug}/"),
                     (f"{SITE}/blog/video/{slug}.", f"{SITE}{base}video/{slug}."), (f"/blog/video/{slug}.", f"{base}video/{slug}.")):
        html = html.replace(old, new)
    files: dict[str, tuple[bytes, str]] = {}
    img_dir = dist / "img" / slug
    if img_dir.is_dir():
        for p in sorted(img_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(dist).as_posix()
                files[rel] = (p.read_bytes(), TYPES.get(p.suffix.lower(), "application/octet-stream"))
    vid_dir = dist / "video"
    if vid_dir.is_dir():
        for p in sorted(vid_dir.glob(f"{slug}.*")):
            files[p.relative_to(dist).as_posix()] = (p.read_bytes(), TYPES.get(p.suffix.lower(), "application/octet-stream"))
    verify(html, slug, files)
    return Preview(slug, title, html.encode("utf-8"), files)


# A prévia mora em /admin/rascunhos/...: nenhuma política de referrer pode mandar esse caminho para fora (#72).
# Estas só mandam a origem (https://guifav.dev/) ou nada para outros sites; o embed do Spotify e do YouTube usa
# strict-origin-when-cross-origin, igual ao blog publicado.
SAFE_REFERRER = {"", "no-referrer", "same-origin", "origin", "strict-origin", "origin-when-cross-origin",
                 "strict-origin-when-cross-origin"}
REFERRER_ATTR = re.compile(r"""\breferrerpolicy\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
META_REFERRER = re.compile(r"""<meta\b[^>]*\bname\s*=\s*["']?referrer\b""", re.I)


def verify(html: str, slug: str, files: dict[str, tuple[bytes, str]]) -> None:
    for m in REFERRER_ATTR.finditer(html):
        value = next(g for g in m.groups() if g is not None).strip().lower()
        if value not in SAFE_REFERRER:
            raise DraftError(f"a prévia tem referrerpolicy={value!r}, que mandaria o caminho /admin para outro site")
    if META_REFERRER.search(html):
        raise DraftError("a prévia tem <meta name=referrer>; a política vem só do cabeçalho do Worker")
    for marker in ANALYTICS:
        if marker in html:
            raise DraftError(f"a prévia tem analytics ({marker}); o build não usou DRAFT_PREVIEW=1")
    if not re.search(r'<meta name="robots" content="noindex[^"]*">', html):
        raise DraftError("a prévia não tem meta robots noindex")
    for leftover in (f"/blog/img/{slug}/", f"/blog/video/{slug}."):
        if leftover in html:
            raise DraftError(f"ficou caminho público de arquivo do rascunho: {leftover}")
    base = f"/admin/rascunhos/{slug}/"
    for ref in sorted(set(re.findall(re.escape(base) + r"([^\"'\s,)<>?#]+)", html))):
        if ref not in files:
            raise DraftError(f"a prévia aponta para um arquivo que não foi gerado: {base}{ref}")
    for rel, (_, ctype) in files.items():
        if not FILE_RE.match(rel) or rel in ("index.html", "manifest.json"):
            raise DraftError(f"nome de arquivo inválido na prévia: {rel!r}")
        if ctype == "application/octet-stream":
            raise DraftError(f"tipo de arquivo fora da lista: {rel!r}")


def version_id(html_sha256: str, source: str) -> str:
    """Identidade da versão: o HTML e a fonte juntos. O Worker (versionId em drafts.ts) calcula igual."""
    return hashlib.sha256(f"html:{html_sha256}\nsource:{source}".encode()).hexdigest()


# Rótulo de um derivado (#77): de 1 a 40 caracteres (pontos de código), sem caractere de controle (C0, DEL, C1) e sem
# espaço nas pontas. É a mesma expressão do Worker (ROTULO_RE em drafts.ts), com o conjunto de espaços por extenso:
# strip() do Python e trim() do JavaScript não removem os mesmos caracteres, e um rótulo aceito aqui e recusado lá
# faria o rascunho sumir do painel.
_ESPACOS = "\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
ROTULO_RE = re.compile(rf"(?!.*[\x00-\x1f\x7f-\x9f])(?![{_ESPACOS}])(?!.*[{_ESPACOS}]\Z).{{1,40}}", re.S)


def rotulo_valido(v: object) -> bool:
    return isinstance(v, str) and ROTULO_RE.fullmatch(v) is not None


def manifest(p: Preview, source: str, fonte: dict[str, bytes], now: datetime | None = None, parent: str | None = None,
             rotulo: str | None = None) -> dict:
    """Manifesto da prévia. Com parent, é um derivado do artigo `parent`, só leitura e aninhado abaixo dele no painel:
    sem rótulo, a versão LinkedIn (#73, kind "linkedin"); com rótulo, qualquer outro artefato do mesmo artigo
    (roteiro de short, vídeo, carrossel...), kind "derivado" (#77).

    `fonte` (#82) é o Markdown e as imagens que geraram o source_sha256, por caminho relativo (fonte_files). O
    manifesto guarda o sha256 de cada um no campo "fonte", separado de "files": o Worker só serve o que está em
    "files", então a fonte nunca sai pelo painel. Derivados levam a fonte pela mesma regra do artigo."""
    # fullmatch: com match, o $ do Python aceita "artigo\n" e o Worker faria o rascunho sumir (r1, Kimi P1).
    if parent is not None and (not SLUG_RE.fullmatch(parent) or parent == p.slug):
        raise DraftError(f"slug do artigo inválido para o rascunho derivado: {parent!r}")
    if rotulo is not None and (parent is None or not rotulo_valido(rotulo)):
        raise DraftError(f"rótulo inválido (1 a 40 caracteres, sem controle nem espaço nas pontas, e com --artigo): {rotulo!r}")
    fonte_valida(fonte, p.slug)
    if any(k.split("/")[0] == "fonte" for k in p.files):
        raise DraftError("arquivo da prévia dentro de fonte/, que é a pasta da fonte na versão")
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    files = {"index.html": HTML_TYPE, **{k: t for k, (_, t) in sorted(p.files.items())}}
    html = hashlib.sha256(p.html).hexdigest()
    man = {"slug": p.slug, "title": p.title, "uploaded_at": at, "source_sha256": source, "html_sha256": html,
           "version": version_id(html, source),
           "kind": "blog" if parent is None else ("derivado" if rotulo is not None else "linkedin"), "files": files,
           "fonte": {k: hashlib.sha256(b).hexdigest() for k, b in sorted(fonte.items())}}
    if parent is not None:
        man["parent"] = parent
    if rotulo is not None:
        man["rotulo"] = rotulo
    return man


def wrangler(args: list[str], run=subprocess.run, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    r = run(["npx", "--yes", "wrangler@4", *args], cwd=ADMIN_WORKER, capture_output=True, input=input_bytes)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else str(r.stderr)
        raise DraftError(f"wrangler {' '.join(args[:3])} falhou: {err[-1500:]}")
    return r


def upload(p: Preview, man: dict, fonte: dict[str, bytes], work: Path, run=subprocess.run) -> list[str]:
    """Envia ao R2: a versão inteira em <slug>/v/<versão>/ (prévia e fonte/) e o manifesto por último, apontando para
    ela. Antes do primeiro envio, confere que a fonte é a do manifesto: o sha256 de cada arquivo e o source_sha256
    recalculado dos bytes que vão subir, pelo mesmo fonte_sha256 que a publicação usa na pasta baixada."""
    if man["html_sha256"] != hashlib.sha256(p.html).hexdigest() or man["version"] != version_id(man["html_sha256"], man["source_sha256"]):
        raise DraftError("manifesto não confere com o HTML da prévia")
    fonte_valida(fonte, p.slug)
    if man.get("fonte") != {k: hashlib.sha256(b).hexdigest() for k, b in fonte.items()}:
        raise DraftError("a fonte não confere com o manifesto (arquivos ou sha256 diferentes)")
    prefix = f"{p.slug}/v/{man['version']}/"
    items: list[tuple[str, bytes, str]] = [(prefix + k, b, t) for k, (b, t) in sorted(p.files.items())]
    items += [(prefix + "fonte/" + k, b, FONTE_MD_TYPE if k == f"{p.slug}.md" else TYPES[Path(k).suffix.lower()])
              for k, b in sorted(fonte.items())]
    items.append((prefix + "index.html", p.html, HTML_TYPE))
    items.append((f"{p.slug}/manifest.json", json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"))
    staged = []
    for rel, data, ctype in items:
        f = work / "upload" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        staged.append((rel, f, ctype))
    if fonte_sha256(work / "upload" / prefix / "fonte", p.slug) != man["source_sha256"]:
        raise DraftError("a fonte não confere com o source_sha256 do manifesto")
    keys = []
    for rel, f, ctype in staged:
        key = f"{BUCKET}/{rel}"
        wrangler(["r2", "object", "put", key, "--file", str(f), "--content-type", ctype, "--remote"], run=run)
        keys.append(key)
    return keys


def approvals(slug: str, run=subprocess.run) -> list[dict]:
    if not SLUG_RE.match(slug):
        raise DraftError(f"slug inválido: {slug!r}")
    sql = f"SELECT html_sha256, source_sha256, approved_at, approved_by FROM draft_approvals WHERE slug = '{slug}' ORDER BY id"
    r = wrangler(["d1", "execute", D1_NAME, "--remote", "--json", "--command", sql], run=run)
    out = json.loads(r.stdout)
    return out[0]["results"] if out else []


def cmd_enviar(a: argparse.Namespace) -> int:
    # Presença, não verdade: --artigo "" e --derivado "" também contam (r1, Kimi P1).
    derivado = a.derivado is not None
    if a.linkedin and derivado:
        raise DraftError("--linkedin e --derivado não andam juntos: o LinkedIn já é um derivado")
    if (a.linkedin or derivado) != (a.artigo is not None):
        raise DraftError("--linkedin (ou --derivado <rótulo>) e --artigo <slug-do-artigo> andam juntos")
    parent = a.artigo if (a.linkedin or derivado) else None
    if parent is not None and not SLUG_RE.fullmatch(parent):
        raise DraftError(f"slug do artigo inválido para o rascunho derivado: {parent!r}")
    if derivado and not rotulo_valido(a.derivado):
        raise DraftError(f"rótulo inválido (1 a 40 caracteres, sem controle nem espaço nas pontas): {a.derivado!r}")
    md = Path(a.rascunho).resolve()
    images = Path(a.imagens).resolve() if a.imagens else None
    if REPO in md.parents:
        raise DraftError("o rascunho fica fora do repo até a aprovação; mova o arquivo para fora de " + str(REPO))
    slug = slug_of(md)
    # Saída da simulação só em pasta vazia: um arquivo de simulação anterior ficaria misturado com esta, e a fonte/
    # dela deixaria de reproduzir o source_sha256 do manifesto (r1, Astra P3). Conferido antes do build.
    if a.dry_run and a.out and Path(a.out).exists() and (not Path(a.out).is_dir() or any(Path(a.out).iterdir())):
        raise DraftError(f"a pasta de saída da simulação precisa estar vazia ou não existir: {a.out}")
    with tempfile.TemporaryDirectory(prefix=f"rascunho-{slug}-") as tmp:
        work = Path(tmp)
        # A fonte é lida uma vez, para a cópia no formato de fonte/ (#82): o título, o build, o source_sha256 e os
        # bytes enviados saem todos dela.
        pasta = snapshot(md, images, work / "fonte")
        fmd, fimg = fonte_paths(pasta, slug)
        title = frontmatter_title(fmd.read_text(encoding="utf-8"))
        source = fonte_sha256(pasta, slug)
        fonte = fonte_files(pasta, slug)
        p = collect(build(fmd, fimg, work), slug, title)
        man = manifest(p, source, fonte, parent=parent, rotulo=a.derivado)
        if a.dry_run:
            out = Path(a.out or work / "out")
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_bytes(p.html)
            for rel, b in [*((r, b) for r, (b, _) in p.files.items()), *((f"fonte/{r}", b) for r, b in fonte.items())]:
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(b)
            (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"simulação: {len(p.files) + len(fonte) + 2} arquivos em {out}; source_sha256 {source}")
            return 0
        keys = upload(p, man, fonte, work)
    tipo = f" ({a.derivado if derivado else 'LinkedIn'}, só leitura)" if parent else ""
    print(f"prévia enviada{tipo}: {len(keys)} objetos; {SITE}/admin/rascunhos/{slug}/ ; source_sha256 {source}")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    md = Path(a.rascunho).resolve()
    images = Path(a.imagens).resolve() if a.imagens else None
    slug = slug_of(md)
    source = source_sha256(md, images)
    rows = approvals(slug)
    ok = [r for r in rows if r.get("source_sha256") == source]
    print(json.dumps({"slug": slug, "source_sha256": source, "aprovado": bool(ok), "aprovacoes": rows}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_remover(a: argparse.Namespace) -> int:
    slug = a.slug
    if not SLUG_RE.fullmatch(slug):
        raise DraftError(f"slug inválido: {slug!r}")
    r = wrangler(["r2", "object", "get", f"{BUCKET}/{slug}/manifest.json", "--pipe", "--remote"])
    man = json.loads(r.stdout)
    version = man.get("version", "")
    if not re.fullmatch(r"[0-9a-f]{64}", str(version)):
        raise DraftError("manifesto sem versão válida")
    # Manifestos de antes do #82 não têm fonte e continuam valendo.
    fonte = man.get("fonte", {})
    if not isinstance(fonte, dict):
        raise DraftError("campo fonte do manifesto não é um objeto")
    # Manifesto primeiro: sem ele, o painel já não mostra nem serve nada do rascunho. Depois a prévia e a fonte da
    # versão. Versões anteriores ficam no bucket privado, sem nunca serem servidas (o Worker só lê a versão do
    # manifesto atual). Toda chave é conferida antes do primeiro delete.
    keys = ["manifest.json", *[f"v/{version}/{k}" for k in ["index.html", *[k for k in man.get("files", {}) if k != "index.html"]]],
            *[f"v/{version}/fonte/{k}" for k in fonte]]
    for k in keys:
        if not FILE_RE.fullmatch(k):
            raise DraftError(f"chave inválida no manifesto: {k!r}")
    for k in keys:
        wrangler(["r2", "object", "delete", f"{BUCKET}/{slug}/{k}", "--remote"])
    print(f"rascunho {slug} removido do R2 ({len(keys)} objetos)")
    return 0


# Publicação (#82, fatia 3): o .github/workflows/publicar.yml chama "publicar <slug> --versao <v>" num checkout da main.
# Nada aqui confia nas entradas: a versão só publica se o D1 tem uma aprovação cujo HTML e fonte dão exatamente essa
# versão, se o manifesto atual do R2 é dessa versão e de um artigo, e se cada arquivo baixado bate com o manifesto e,
# juntos, com o source_sha256 da aprovação. O hash é o mesmo fonte_sha256 do envio.
VERSION_RE = re.compile(r"[0-9a-f]{64}")
ACCOUNT_RE = re.compile(r"[0-9a-f]{32}")
D1_ID = "f54f10dc-960e-468a-aca1-2c74c4c6dc4d"  # database_id do blog-admin em workers/admin/wrangler.toml (teste confere)
# Barreira 2 do CONTRATO-DE-CONTEUDO.md: só esta mídia entra em public/. TYPES aceita .svg na prévia; a publicação não.
PUBLIC_EXT = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif", ".mp4", ".webm"}
SEGREDOS = ("CLOUDFLARE_ACCOUNT_ID", "CF_PUBLICAR_API_TOKEN", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
APROVACOES_SQL = "SELECT id, html_sha256, source_sha256, approved_at FROM draft_approvals WHERE slug = ? ORDER BY id"


class Cloudflare:
    """Leitura do D1 pela API REST e do R2 pela API S3 (aws s3api), com as credenciais do publicar.yml. O token e as
    chaves só vão em cabeçalho ou variável de ambiente, nunca em argumento de comando nem em mensagem de erro."""

    def __init__(self, env: dict, run=subprocess.run, http=None):
        faltam = [k for k in SEGREDOS if not env.get(k)]
        if faltam:
            raise DraftError("faltam no ambiente: " + ", ".join(faltam))
        if not ACCOUNT_RE.fullmatch(env["CLOUDFLARE_ACCOUNT_ID"]):
            raise DraftError("CLOUDFLARE_ACCOUNT_ID não tem o formato de um id de conta (32 hex)")
        self.conta = env["CLOUDFLARE_ACCOUNT_ID"]
        self._token = env["CF_PUBLICAR_API_TOKEN"]
        self._aws_env = {k: v for k, v in env.items() if not k.startswith("AWS_") and k not in SEGREDOS}
        # O endpoint leva o id da conta: vai pelo ambiente (AWS_ENDPOINT_URL_S3, aws CLI 2.13+), não por argumento (r1, Astra P2).
        self._aws_env.update({"AWS_ACCESS_KEY_ID": env["R2_ACCESS_KEY_ID"], "AWS_SECRET_ACCESS_KEY": env["R2_SECRET_ACCESS_KEY"],
                              "AWS_ENDPOINT_URL_S3": f"https://{self.conta}.r2.cloudflarestorage.com", "AWS_DEFAULT_REGION": "auto", "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
                              "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required", "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required"})
        self.run = run
        if http is None:
            import urllib.request
            http = urllib.request.urlopen
        self.http = http

    def aprovacoes(self, slug: str) -> list:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{self.conta}/d1/database/{D1_ID}/query", method="POST",
            data=json.dumps({"sql": APROVACOES_SQL, "params": [slug]}).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "User-Agent": "guifav-publicar"})
        try:
            with self.http(req, timeout=30) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise DraftError(f"o D1 respondeu {e.code} à consulta das aprovações") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise DraftError(f"falha ao consultar as aprovações no D1 ({type(e).__name__})") from None
        try:
            if body.get("success") is not True:
                raise ValueError
            rows = body["result"][0]["results"]
            if not isinstance(rows, list):
                raise ValueError
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise DraftError("resposta do D1 fora do formato esperado") from None
        return rows

    def baixar(self, key: str, work: Path) -> bytes:
        work.mkdir(parents=True, exist_ok=True)
        fd, nome = tempfile.mkstemp(dir=work)
        os.close(fd)
        dest = Path(nome)
        r = self.run(["aws", "s3api", "get-object", "--bucket", BUCKET, "--key", key, str(dest)],
                     capture_output=True, env=self._aws_env)
        if r.returncode != 0:
            # Só o código de erro do S3 sai daqui: o texto do aws CLI pode trazer o endpoint, com o id da conta, e vai
            # para o log e o resumo da execução (r1, Astra P2).
            err = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else str(r.stderr)
            codigo = re.search(r"An error occurred \(([A-Za-z0-9]{1,64})\)", err)
            motivo = f"o R2 respondeu {codigo.group(1)}" if codigo else f"o aws CLI saiu com {r.returncode}, sem código de erro do S3"
            raise DraftError(f"não consegui baixar {key} do R2: {motivo}")
        try:
            return dest.read_bytes()
        finally:
            dest.unlink()


@dataclass
class Publicacao:
    slug: str
    versao: str
    title: str
    aprovacao: dict
    html_sha256: str
    source_sha256: str
    arquivos: list[tuple[str, str]]  # (caminho no repo, sha256), na ordem em que foram gravados


def publicar(slug: str, versao: str, nuvem, blog_src: Path, work: Path) -> Publicacao:
    """Confere a versão aprovada e grava o Markdown e as imagens nos caminhos do blog. Recusa (DraftError) antes de
    gravar o primeiro arquivo se qualquer conferência falhar."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise DraftError(f"slug inválido: {slug!r}")
    if not isinstance(versao, str) or not VERSION_RE.fullmatch(versao):
        raise DraftError(f"versão inválida (64 caracteres hexadecimais minúsculos): {versao!r}")
    md_dest = blog_src / "src" / "content" / "posts" / f"{slug}.md"
    img_dest = blog_src / "public" / "img" / slug
    if os.path.lexists(md_dest):
        raise DraftError(f"o slug {slug!r} já está publicado na main ({md_dest.relative_to(blog_src.parent)})")
    if os.path.lexists(img_dest):
        raise DraftError(f"já existe {img_dest.relative_to(blog_src.parent)} na main, sem o post; nada foi gravado")

    # 1. Aprovação no D1: a versão é recalculada do HTML e da fonte de cada linha, como o painel faz.
    rows = nuvem.aprovacoes(slug)
    aprovadas = [r for r in rows if isinstance(r, dict) and isinstance(r.get("html_sha256"), str)
                 and isinstance(r.get("source_sha256"), str) and version_id(r["html_sha256"], r["source_sha256"]) == versao]
    if not aprovadas:
        raise DraftError(f"nenhuma aprovação no painel para {slug} na versão {versao}: nada a publicar")
    aprovacao = aprovadas[-1]

    # 2. Manifesto atual: tem de ser desta versão e de um artigo. Derivado não publica.
    try:
        man = json.loads(nuvem.baixar(f"{slug}/manifest.json", work / "r2"))
    except ValueError:
        raise DraftError("manifesto do rascunho ilegível") from None
    if not isinstance(man, dict) or man.get("slug") != slug:
        raise DraftError("manifesto do rascunho não é deste slug")
    if man.get("kind", "blog") != "blog" or "parent" in man:
        raise DraftError(f"{slug} é um derivado ({man.get('kind')!r}, artigo {man.get('parent')!r}); derivados não publicam")
    if man.get("version") != versao:
        raise DraftError(f"a versão atual do rascunho é {str(man.get('version'))[:8]}..., não a {versao[:8]}... aprovada; "
                         "aprove a versão atual no painel")
    if (man.get("html_sha256"), man.get("source_sha256")) != (aprovacao["html_sha256"], aprovacao["source_sha256"]):
        raise DraftError("o manifesto não tem o HTML e a fonte da aprovação")
    fonte_sha = man.get("fonte")
    if not isinstance(fonte_sha, dict) or not fonte_sha:
        raise DraftError("o manifesto não tem a fonte (enviado antes do #82); reenvie o rascunho e aprove de novo")

    # 3. Caminhos conferidos antes do primeiro download; cada arquivo conferido contra o sha256 do manifesto.
    for rel, sha in fonte_sha.items():
        if not isinstance(rel, str) or not FILE_RE.fullmatch(rel) or not (
                rel == f"{slug}.md" or (rel.startswith("img/") and Path(rel).suffix.lower() in TYPES)):
            raise DraftError(f"caminho inválido na fonte: {rel!r}")
        if rel != f"{slug}.md" and Path(rel).suffix.lower() not in PUBLIC_EXT:
            raise DraftError(f"{rel!r} não pode ir para public/ (barreira 2 do CONTRATO-DE-CONTEUDO.md: só "
                             f"{', '.join(sorted(PUBLIC_EXT))})")
        if not isinstance(sha, str) or not VERSION_RE.fullmatch(sha):
            raise DraftError(f"sha256 inválido no manifesto para {rel!r}")
    fonte: dict[str, bytes] = {}
    for rel, sha in sorted(fonte_sha.items()):
        data = nuvem.baixar(f"{slug}/v/{versao}/fonte/{rel}", work / "r2")
        if hashlib.sha256(data).hexdigest() != sha:
            raise DraftError(f"o sha256 de {rel} baixado do R2 não confere com o manifesto")
        fonte[rel] = data
    fonte_valida(fonte, slug)

    # 4. A fonte inteira, numa pasta no formato de fonte/, dá o source_sha256 da aprovação.
    work.mkdir(parents=True, exist_ok=True)
    pasta = Path(tempfile.mkdtemp(prefix="fonte-", dir=work))  # sempre nova: nada de uma execução anterior entra no hash
    for rel, data in fonte.items():
        (pasta / rel).parent.mkdir(parents=True, exist_ok=True)
        (pasta / rel).write_bytes(data)
    if set(fonte_files(pasta, slug)) != set(fonte):
        raise DraftError("a fonte baixada tem arquivos que o hash não cobre")
    source = fonte_sha256(pasta, slug)
    if source != aprovacao["source_sha256"]:
        raise DraftError(f"a fonte baixada dá source_sha256 {source}, e a aprovação é de {aprovacao['source_sha256']}")
    try:
        title = frontmatter_title(fonte[f"{slug}.md"].decode("utf-8"))
    except UnicodeDecodeError:
        raise DraftError(f"{slug}.md não é UTF-8") from None

    # 5. Gravação: só arquivos novos, dentro de posts/<slug>.md e public/img/<slug>/.
    plano = [(md_dest, fonte[f"{slug}.md"])]
    plano += [(img_dest / rel.removeprefix("img/"), data) for rel, data in sorted(fonte.items()) if rel != f"{slug}.md"]
    # Os caminhos já são do post por construção (FILE_RE não deixa ..); o que ainda desviaria a gravação é um link
    # simbólico no caminho, para fora do repo ou para outra pasta dele, como src/content/posts -> ../pages (r1, Astra P3).
    # Nenhuma pasta existente do repo até o destino pode ser link.
    repo = blog_src.parent
    for dest, _ in plano:
        for p in [dest.parent, *dest.parent.parents]:
            if p == repo:
                break
            if p.is_symlink():
                raise DraftError(f"link simbólico no caminho do destino: {p.relative_to(repo)}; nada foi gravado")
        else:
            raise DraftError(f"destino fora do repositório: {dest}")
    arquivos = []
    for dest, data in plano:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "xb") as f:
            f.write(data)
        arquivos.append((dest.relative_to(blog_src.parent).as_posix(), hashlib.sha256(data).hexdigest()))
    return Publicacao(slug, versao, title, {"id": aprovacao.get("id"), "approved_at": aprovacao.get("approved_at")},
                      man["html_sha256"], source, arquivos)


def _linha(v: object) -> str:
    """Texto de uma linha só, sem caractere de controle, para título de PR e de commit."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(v)).strip()


def corpo_pr(p: Publicacao) -> str:
    # O título vai em código: um @ nele não menciona ninguém num PR público.
    titulo = _linha(p.title).replace("`", "'")
    linhas = [
        f"Publica o artigo `{titulo}`, exatamente na versão aprovada pelo Gui no painel privado. PR aberto pela "
        "workflow `publicar.yml`, sem agente no meio e sem edição depois da aprovação.",
        "",
        "## Aprovação e integridade",
        "",
        f"- Slug: `{p.slug}`.",
        f"- Versão aprovada: `{p.versao}`.",
        f"- Aprovação no D1 `blog-admin`: linha `{_linha(p.aprovacao.get('id'))}`, em `{_linha(p.aprovacao.get('approved_at'))}`.",
        f"- HTML aprovado (sha256): `{p.html_sha256}`.",
        f"- Fonte aprovada (sha256): `{p.source_sha256}`. A workflow baixou a fonte da versão do R2, conferiu cada arquivo "
        "contra o sha256 do manifesto e recalculou o hash da fonte com o `fonte_sha256` do `draft_preview.py`: bate com "
        "o da aprovação.",
        "",
        "## Arquivos",
        "",
        "| Arquivo | sha256 |",
        "| --- | --- |",
        *[f"| `{caminho}` | `{sha}` |" for caminho, sha in p.arquivos],
        "",
        "## Depois do merge",
        "",
        "O cron do painel (minuto 17 de cada hora) tira do R2 o rascunho do artigo depois que este PR estiver "
        "mergeado e o post responder no site. Os derivados continuam no painel.",
    ]
    return "\n".join(linhas) + "\n"


def cmd_publicar(a: argparse.Namespace) -> int:
    saida = Path(a.saida)
    if saida.exists() and (not saida.is_dir() or any(saida.iterdir())):
        raise DraftError(f"a pasta de saída precisa estar vazia ou não existir: {saida}")
    nuvem = Cloudflare(dict(os.environ))
    with tempfile.TemporaryDirectory(prefix="publicar-") as tmp:
        p = publicar(a.slug, a.versao, nuvem, BLOG_SRC, Path(tmp))
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "titulo.txt").write_text(_linha(p.title)[:200] + "\n", encoding="utf-8")
    (saida / "corpo.md").write_text(corpo_pr(p), encoding="utf-8")
    (saida / "arquivos.txt").write_text("".join(c + "\n" for c, _ in p.arquivos), encoding="utf-8")
    print(f"publicação conferida: {p.slug} na versão {p.versao[:8]}, {len(p.arquivos)} arquivos gravados; source_sha256 {p.source_sha256}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enviar", help="gera a prévia e envia ao R2")
    e.add_argument("rascunho")
    e.add_argument("--imagens")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--out")
    e.add_argument("--linkedin", action="store_true", help="versão LinkedIn de um artigo: só leitura no painel (#73)")
    e.add_argument("--derivado", metavar="ROTULO",
                   help='outro artefato do artigo (ex.: "Roteiro do short"): só leitura, aninhado abaixo dele com este rótulo (#77)')
    e.add_argument("--artigo", help="slug do artigo do blog a que o derivado (LinkedIn ou --derivado) pertence")
    s = sub.add_parser("status", help="diz se a fonte atual foi aprovada")
    s.add_argument("rascunho")
    s.add_argument("--imagens")
    r = sub.add_parser("remover", help="tira a prévia do R2 depois da publicação")
    r.add_argument("slug")
    pb = sub.add_parser("publicar", help="confere a versão aprovada e grava o post nos caminhos do blog (publicar.yml, #82)")
    pb.add_argument("slug")
    pb.add_argument("--versao", required=True, help="versão aprovada, 64 hex")
    pb.add_argument("--saida", required=True, help="pasta vazia para titulo.txt, corpo.md e arquivos.txt do PR")
    a = ap.parse_args(argv)
    try:
        return {"enviar": cmd_enviar, "status": cmd_status, "remover": cmd_remover, "publicar": cmd_publicar}[a.cmd](a)
    except DraftError as err:
        print(f"ERRO: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
