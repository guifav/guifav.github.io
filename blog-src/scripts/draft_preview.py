"""Prévia privada de um rascunho do blog em guifav.dev/admin/rascunhos (issue #62). Roda na KVM.

    python3 blog-src/scripts/draft_preview.py enviar <rascunho.md> [--imagens DIR] [--dry-run --out DIR]
    python3 blog-src/scripts/draft_preview.py status <rascunho.md> [--imagens DIR]
    python3 blog-src/scripts/draft_preview.py remover <slug>

O rascunho nunca entra no repo antes da aprovação do Gui: o Markdown e as imagens ficam fora dele (ex.:
/root/.roger/inbox/blog/rascunhos/). O "enviar":

1. copia o blog-src para uma pasta temporária, põe o rascunho em src/content/posts/<slug>.md e as imagens em
   public/img/<slug>/, e faz o build do Astro com DRAFT_PREVIEW=1 (sem GA4, sem Clarity, tudo com noindex);
2. pega a página do post e os arquivos dele (img/<slug>/ e video/<slug>.*) e troca /blog/img/<slug>/ e
   /blog/video/<slug>. por /admin/rascunhos/<slug>/...; confere que nada ficou de fora e que não há analytics;
3. envia os arquivos ao bucket R2 privado blog-rascunhos numa pasta própria da versão (<slug>/v/<versão>/) e, por
   último, o manifesto <slug>/manifest.json, que aponta para ela. Se o envio parar no meio, o manifesto continua
   apontando para a versão anterior inteira: o painel nunca mistura duas versões.

A versão é o sha256 do HTML junto com o source_sha256 (Markdown + imagens). A aprovação no painel grava os dois.
O "status" recalcula o source_sha256 e lê no D1 do painel se existe aprovação para ele: o PR de publicação só sai
com a fonte aprovada.

Credenciais: CLOUDFLARE_API_TOKEN e CLOUDFLARE_ACCOUNT_ID no ambiente (env da KVM), usadas pelo wrangler.
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
ANALYTICS = ("googletagmanager.com", "clarity.ms", "/assets/track.js", "gtag(")
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


def verify(html: str, slug: str, files: dict[str, tuple[bytes, str]]) -> None:
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


def manifest(p: Preview, source: str, now: datetime | None = None) -> dict:
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    files = {"index.html": HTML_TYPE, **{k: t for k, (_, t) in sorted(p.files.items())}}
    html = hashlib.sha256(p.html).hexdigest()
    return {"slug": p.slug, "title": p.title, "uploaded_at": at, "source_sha256": source, "html_sha256": html,
            "version": version_id(html, source), "files": files}


def wrangler(args: list[str], run=subprocess.run, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    r = run(["npx", "--yes", "wrangler@4", *args], cwd=ADMIN_WORKER, capture_output=True, input=input_bytes)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else str(r.stderr)
        raise DraftError(f"wrangler {' '.join(args[:3])} falhou: {err[-1500:]}")
    return r


def upload(p: Preview, man: dict, work: Path, run=subprocess.run) -> list[str]:
    """Envia ao R2: a versão inteira em <slug>/v/<versão>/ e o manifesto por último, apontando para ela."""
    if man["html_sha256"] != hashlib.sha256(p.html).hexdigest() or man["version"] != version_id(man["html_sha256"], man["source_sha256"]):
        raise DraftError("manifesto não confere com o HTML da prévia")
    prefix = f"{p.slug}/v/{man['version']}/"
    items: list[tuple[str, bytes, str]] = [(prefix + k, b, t) for k, (b, t) in sorted(p.files.items())]
    items.append((prefix + "index.html", p.html, HTML_TYPE))
    items.append((f"{p.slug}/manifest.json", json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"))
    keys = []
    for rel, data, ctype in items:
        f = work / "upload" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
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
    md = Path(a.rascunho).resolve()
    images = Path(a.imagens).resolve() if a.imagens else None
    if REPO in md.parents:
        raise DraftError("o rascunho fica fora do repo até a aprovação; mova o arquivo para fora de " + str(REPO))
    slug = slug_of(md)
    title = frontmatter_title(md.read_text(encoding="utf-8"))
    source = source_sha256(md, images)
    with tempfile.TemporaryDirectory(prefix=f"rascunho-{slug}-") as tmp:
        work = Path(tmp)
        p = collect(build(md, images, work), slug, title)
        man = manifest(p, source)
        if a.dry_run:
            out = Path(a.out or work / "out")
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_bytes(p.html)
            for rel, (b, _) in p.files.items():
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(b)
            (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"simulação: {len(p.files) + 2} arquivos em {out}; source_sha256 {source}")
            return 0
        keys = upload(p, man, work)
    print(f"prévia enviada: {len(keys)} objetos; {SITE}/admin/rascunhos/{slug}/ ; source_sha256 {source}")
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
    if not SLUG_RE.match(slug):
        raise DraftError(f"slug inválido: {slug!r}")
    r = wrangler(["r2", "object", "get", f"{BUCKET}/{slug}/manifest.json", "--pipe", "--remote"])
    man = json.loads(r.stdout)
    version = man.get("version", "")
    if not re.fullmatch(r"[0-9a-f]{64}", str(version)):
        raise DraftError("manifesto sem versão válida")
    # Manifesto primeiro: sem ele, o painel já não mostra nem serve nada do rascunho. Versões anteriores ficam no
    # bucket privado, sem nunca serem servidas (o Worker só lê a versão do manifesto atual).
    keys = ["manifest.json", *[f"v/{version}/{k}" for k in ["index.html", *[k for k in man.get("files", {}) if k != "index.html"]]]]
    for k in keys:
        if not FILE_RE.match(k):
            raise DraftError(f"chave inválida no manifesto: {k!r}")
        wrangler(["r2", "object", "delete", f"{BUCKET}/{slug}/{k}", "--remote"])
    print(f"rascunho {slug} removido do R2 ({len(keys)} objetos)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enviar", help="gera a prévia e envia ao R2")
    e.add_argument("rascunho")
    e.add_argument("--imagens")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--out")
    s = sub.add_parser("status", help="diz se a fonte atual foi aprovada")
    s.add_argument("rascunho")
    s.add_argument("--imagens")
    r = sub.add_parser("remover", help="tira a prévia do R2 depois da publicação")
    r.add_argument("slug")
    a = ap.parse_args(argv)
    try:
        return {"enviar": cmd_enviar, "status": cmd_status, "remover": cmd_remover}[a.cmd](a)
    except DraftError as err:
        print(f"ERRO: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
