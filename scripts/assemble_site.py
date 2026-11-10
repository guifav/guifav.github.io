"""Monta o diretório publicado pelo GitHub Pages (_site/).

O site atual é HTML escrito à mão e o blog é gerado pelo Astro em blog-src/. Este script
junta os dois:

- copia da raiz do repo SOMENTE o que está listado em site-manifest.txt (pasta nova no
  repo, como workers/ ou blog-src/, nunca vai ao ar por acidente);
- copia o build do blog (blog-src/dist) para _site/blog/;
- verifica que cada arquivo publicado é byte a byte igual à sua origem e que nada fora
  da lista aparece em _site/.

Uso:
    python3 scripts/assemble_site.py [--repo .] [--manifest site-manifest.txt]
                                     [--blog blog-src/dist] [--out _site]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path, PurePosixPath

BLOG_DIR = "blog"


class ManifestError(Exception):
    """Manifesto inválido ou entrada que não pode ser publicada."""


class VerifyError(Exception):
    """O diretório montado não corresponde ao manifesto e ao build do blog."""


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
        files[f"{BLOG_DIR}/{f.relative_to(blog).as_posix()}"] = f
    return files


def assemble(repo: Path, manifest: Path, blog: Path, out: Path) -> dict[str, Path]:
    files = expected_files(repo, manifest, blog)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel, src in files.items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return files


def verify(repo: Path, manifest: Path, blog: Path, out: Path) -> int:
    files = expected_files(repo, manifest, blog)
    published = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() or p.is_symlink()}
    extra = sorted(published - files.keys())
    missing = sorted(files.keys() - published)
    changed = sorted(
        rel for rel in files.keys() & published
        if (out / rel).is_symlink() or not _same_bytes(files[rel], out / rel)
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
    except (ManifestError, VerifyError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    blog_n = sum(1 for p in (out / BLOG_DIR).rglob("*") if p.is_file())
    print(f"_site montado: {n} arquivos ({n - blog_n} do site, {blog_n} do blog), verificados byte a byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
