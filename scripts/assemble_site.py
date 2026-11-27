"""Monta o diretório publicado pelo GitHub Pages (_site/).

O site atual é HTML escrito à mão e o blog é gerado pelo Astro em blog-src/. Este script
junta os dois:

- copia da raiz do repo SOMENTE o que está listado em site-manifest.txt (pasta nova no
  repo, como workers/ ou blog-src/, nunca vai ao ar por acidente);
- copia o build do blog (blog-src/dist) para _site/blog/;
- escreve na home (index.html) os últimos artigos do blog, que o build do Astro deixa em
  ultimos-artigos.json (issue #136), entre os marcadores <!-- ultimos-artigos --> e
  <!-- /ultimos-artigos -->. O JSON é consumido aqui e não vai ao ar: sob /blog/ só vão páginas,
  XML, mídia e o catálogo de posts (blog/catalogo.json, #193), única exceção da barreira 2 do
  contrato de conteúdo;
- verifica que cada arquivo publicado é byte a byte igual à sua origem (a home, igual à origem com
  a lista escrita) e que nada fora da lista aparece em _site/.

Uso:
    python3 scripts/assemble_site.py [--repo .] [--manifest site-manifest.txt]
                                     [--blog blog-src/dist] [--out _site]
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

BLOG_DIR = "blog"
HOME = "index.html"
LATEST_NAME = "ultimos-artigos.json"
LATEST_JSON = f"{BLOG_DIR}/{LATEST_NAME}"
LATEST_START = "<!-- ultimos-artigos -->"
LATEST_END = "<!-- /ultimos-artigos -->"
LATEST_PATH = re.compile(r"/blog/[a-z0-9][a-z0-9-]*/")
LATEST_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ManifestError(Exception):
    """Manifesto inválido ou entrada que não pode ser publicada."""


class VerifyError(Exception):
    """O diretório montado não corresponde ao manifesto e ao build do blog."""


class RenderError(Exception):
    """A home não tem os marcadores da lista ou a lista do blog é inválida."""


def render_latest(items: list[dict]) -> str:
    """A lista de últimos artigos em HTML, com o texto escapado."""
    e = html.escape
    lis = "".join(
        f'<li><a href="{e(i["path"])}" hreflang="pt-BR">{e(i["title"])}</a> '
        f'<time datetime="{e(i["date"])}">{e(i["date_label"])}</time><p>{e(i["description"])}</p></li>'
        for i in items)
    return f'<ul class="latest-list" lang="pt-BR">{lis}</ul>'


def _real_date(s: str) -> bool:
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _latest_items(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RenderError(f"{LATEST_JSON} ilegível: {err}") from err
    items = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise RenderError(f"{LATEST_JSON} sem a lista de posts")
    for i in items:
        if not (isinstance(i, dict) and all(isinstance(i.get(k), str) and i[k].strip()
                                            for k in ("title", "path", "date", "date_label", "description"))
                and LATEST_PATH.fullmatch(i["path"]) and LATEST_DATE.fullmatch(i["date"]) and _real_date(i["date"])):
            raise RenderError(f"{LATEST_JSON}: item inválido: {i!r}")
    return items


def render_home(source: bytes, latest: Path) -> bytes:
    """A home com a lista de últimos artigos entre os marcadores, que na origem ficam vazios."""
    text = source.decode("utf-8")
    if text.count(LATEST_START) != 1 or text.count(LATEST_END) != 1:
        raise RenderError(f"{HOME}: precisa de um {LATEST_START} e um {LATEST_END}")
    a = text.index(LATEST_START) + len(LATEST_START)
    b = text.index(LATEST_END)
    if b < a or text[a:b].strip():
        raise RenderError(f"{HOME}: os marcadores da lista vêm em ordem e vazios na origem")
    return (text[:a] + render_latest(_latest_items(latest)) + text[b:]).encode("utf-8")


def expected_bytes(files: dict[str, Path], blog: Path) -> dict[str, bytes]:
    """Arquivos publicados que não são cópia da origem: a home com a lista (quando a home é publicada)."""
    if HOME not in files:
        return {}
    latest = blog / LATEST_NAME
    if latest.is_symlink() or not latest.is_file():
        raise RenderError(f"build do blog sem {LATEST_NAME}: a home ficaria sem os últimos artigos")
    return {HOME: render_home(files[HOME].read_bytes(), latest)}


def parse_manifest(path: Path) -> list[str]:
    """Lê o manifesto: uma entrada por linha, pasta termina com '/', '#' comenta."""
    entries: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body = line.rstrip("/")
        if (
            line.startswith("/")
            or "\\" in line
            or any(part in ("..", ".", "") for part in body.split("/"))
        ):
            raise ManifestError(f"{path.name}:{lineno}: caminho inválido: {line!r}")
        if PurePosixPath(body).parts[0] == BLOG_DIR:
            raise ManifestError(f"{path.name}:{lineno}: '{BLOG_DIR}/' é reservado ao build do blog")
        if line in entries:
            raise ManifestError(f"{path.name}:{lineno}: entrada duplicada: {line!r}")
        entries.append(line)
    if not entries:
        raise ManifestError(f"{path.name}: manifesto vazio")
    return entries


def _no_symlink_on_path(root: Path, p: Path) -> None:
    """Recusa p se ele ou qualquer pasta entre root e p for link simbólico."""
    cur = root
    for part in p.relative_to(root).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ManifestError(f"link simbólico não é publicado: {cur}")


def _same_bytes(a: Path, b: Path) -> bool:
    """Compara o conteúdo inteiro, sem o cache de filecmp (que confia em tamanho e mtime)."""
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca, cb = fa.read(1 << 20), fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def _files_under(base: Path) -> list[Path]:
    """Lista os arquivos de uma pasta, recusando links simbólicos (podem apontar para fora)."""
    out: list[Path] = []
    for p in sorted(base.rglob("*")):
        if p.is_symlink():
            raise ManifestError(f"link simbólico não é publicado: {p}")
        if p.is_file():
            out.append(p)
    return out


def expected_files(repo: Path, manifest: Path, blog: Path) -> dict[str, Path]:
    """Mapa caminho publicado (relativo a _site) -> arquivo de origem."""
    files: dict[str, Path] = {}
    for entry in parse_manifest(manifest):
        src = repo / entry.rstrip("/")
        _no_symlink_on_path(repo, src)
        if entry.endswith("/"):
            if not src.is_dir():
                raise ManifestError(f"entrada de pasta não existe como pasta: {entry}")
            for f in _files_under(src):
                files[f.relative_to(repo).as_posix()] = f
        else:
            if not src.is_file():
                raise ManifestError(f"entrada de arquivo não existe como arquivo: {entry}")
            files[entry] = src
    if blog.is_symlink():
        raise ManifestError(f"link simbólico não é publicado: {blog}")
    if blog.is_relative_to(repo):
        # O build fica dentro do repo (blog-src/dist): nenhuma pasta no caminho pode ser link.
        _no_symlink_on_path(repo, blog)
    if not (blog / "index.html").is_file():
        raise ManifestError(f"build do blog ausente: {blog / 'index.html'}")
    for f in _files_under(blog):
        rel = f"{BLOG_DIR}/{f.relative_to(blog).as_posix()}"
        if rel != LATEST_JSON:  # entrada da home, não página do blog
            files[rel] = f
    return files


def assemble(repo: Path, manifest: Path, blog: Path, out: Path) -> dict[str, Path]:
    files = expected_files(repo, manifest, blog)
    rendered = expected_bytes(files, blog)  # antes de apagar o _site: com erro, o anterior fica
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel, src in files.items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel in rendered:
            dst.write_bytes(rendered[rel])
        else:
            shutil.copyfile(src, dst)
    return files


def verify(repo: Path, manifest: Path, blog: Path, out: Path) -> int:
    files = expected_files(repo, manifest, blog)
    published = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() or p.is_symlink()}
    extra = sorted(published - files.keys())
    missing = sorted(files.keys() - published)
    rendered = expected_bytes(files, blog)
    changed = sorted(
        rel for rel in files.keys() & published
        if (out / rel).is_symlink() or not (
            (out / rel).read_bytes() == rendered[rel] if rel in rendered else _same_bytes(files[rel], out / rel))
    )
    problems = []
    if extra:
        problems.append("fora do manifesto: " + ", ".join(extra[:20]))
    if missing:
        problems.append("faltando: " + ", ".join(missing[:20]))
    if changed:
        problems.append("diferente da origem: " + ", ".join(changed[:20]))
    if problems:
        raise VerifyError("; ".join(problems))
    return len(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monta o diretório publicado pelo GitHub Pages (_site/).")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--manifest", default="site-manifest.txt")
    ap.add_argument("--blog", default="blog-src/dist")
    ap.add_argument("--out", default="_site")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    manifest = repo / args.manifest
    blog = repo / args.blog
    out = repo / args.out
    try:
        assemble(repo, manifest, blog, out)
        n = verify(repo, manifest, blog, out)
    except (ManifestError, VerifyError, RenderError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    blog_n = sum(1 for p in (out / BLOG_DIR).rglob("*") if p.is_file())
    print(f"_site montado: {n} arquivos ({n - blog_n} do site, {blog_n} do blog), verificados byte a byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
