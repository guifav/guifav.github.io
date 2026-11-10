"""Testes do montador do site publicado (scripts/assemble_site.py).

Rodar: python3 scripts/test_assemble_site.py
"""
import os
import tempfile
import unittest
from pathlib import Path

import assemble_site as A


def write(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.blog = root / "blog-dist"
        self.out = root / "_site"
        write(self.repo / "index.html", b"<html>home</html>")
        write(self.repo / "CNAME", b"guifav.dev\n")
        write(self.repo / "portfolio" / "index.html", b"<html>portfolio</html>")
        write(self.repo / "portfolio" / "img" / "a.webp", b"\x00\x01webp")
        # Pastas que existem no repo e nunca podem ir ao ar.
        write(self.repo / "docs" / "plan.md", b"plano interno")
        write(self.repo / "_next" / "static" / "x.js", b"js")
        write(self.repo / "tools" / "mockups" / "README.md", b"readme")
        write(self.repo / "blog-src" / "package.json", b"{}")
        write(self.repo / "workers" / "newsletter" / "index.js", b"segredo-nao")
        write(self.blog / "index.html", b"<html>blog</html>")
        write(self.blog / "rss.xml", b"<rss/>")
        self.manifest = self.repo / "site-manifest.txt"
        self.manifest.write_text("# comentario\nindex.html\nCNAME\n\nportfolio/\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def assemble(self):
        return A.assemble(self.repo, self.manifest, self.blog, self.out)


class ParseManifestTest(Fixture):
    def test_ignores_comments_and_blank_lines(self):
        self.assertEqual(A.parse_manifest(self.manifest), ["index.html", "CNAME", "portfolio/"])

    def test_rejects_traversal_absolute_and_blog_entries(self):
        for bad in ["../segredo", "/etc/passwd", "portfolio/../docs/", "blog/", "blog", "./index.html"]:
            with self.subTest(bad=bad):
                self.manifest.write_text(bad + "\n", encoding="utf-8")
                with self.assertRaises(A.ManifestError):
                    A.parse_manifest(self.manifest)

    def test_rejects_duplicates(self):
        self.manifest.write_text("index.html\nindex.html\n", encoding="utf-8")
        with self.assertRaises(A.ManifestError):
            A.parse_manifest(self.manifest)


class AssembleTest(Fixture):
    def test_copies_only_manifest_entries_and_blog(self):
        self.assemble()
        got = sorted(str(p.relative_to(self.out)) for p in self.out.rglob("*") if p.is_file())
        self.assertEqual(got, [
            "CNAME",
            "blog/index.html",
            "blog/rss.xml",
            "index.html",
            "portfolio/img/a.webp",
            "portfolio/index.html",
        ])

    def test_excluded_folders_never_published(self):
        self.assemble()
        for name in ["docs", "_next", "tools", "blog-src", "workers"]:
            self.assertFalse((self.out / name).exists(), name)

    def test_root_files_are_byte_identical(self):
        self.assemble()
        for rel in ["index.html", "CNAME", "portfolio/index.html", "portfolio/img/a.webp"]:
            self.assertEqual((self.out / rel).read_bytes(), (self.repo / rel).read_bytes(), rel)

    def test_missing_manifest_entry_fails(self):
        self.manifest.write_text("index.html\nnao-existe.html\n", encoding="utf-8")
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_directory_entry_needs_trailing_slash_and_vice_versa(self):
        self.manifest.write_text("portfolio\n", encoding="utf-8")
        with self.assertRaises(A.ManifestError):
            self.assemble()
        self.manifest.write_text("index.html/\n", encoding="utf-8")
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_missing_blog_build_fails(self):
        (self.blog / "index.html").unlink()
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_repo_blog_folder_cannot_shadow_build(self):
        write(self.repo / "blog" / "index.html", b"antigo")
        self.manifest.write_text("index.html\n", encoding="utf-8")
        self.assemble()
        self.assertEqual((self.out / "blog" / "index.html").read_bytes(), b"<html>blog</html>")

    def test_symlink_in_published_dir_is_rejected(self):
        os.symlink(self.repo / "docs" / "plan.md", self.repo / "portfolio" / "atalho.md")
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_symlinked_ancestor_of_file_entry_is_rejected(self):
        outside = Path(self.tmp.name) / "fora"
        write(outside / "index.html", b"fora do repo")
        os.symlink(outside, self.repo / "public")
        self.manifest.write_text("index.html\npublic/index.html\n", encoding="utf-8")
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_symlinked_blog_build_root_is_rejected(self):
        real = Path(self.tmp.name) / "dist-real"
        self.blog.rename(real)
        os.symlink(real, self.blog)
        with self.assertRaises(A.ManifestError):
            self.assemble()

    def test_output_dir_is_recreated_clean(self):
        write(self.out / "lixo-de-build-anterior.html", b"velho")
        self.assemble()
        self.assertFalse((self.out / "lixo-de-build-anterior.html").exists())


class VerifyTest(Fixture):
    def test_verify_passes_on_fresh_assembly(self):
        self.assemble()
        A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_verify_detects_changed_root_file(self):
        self.assemble()
        (self.out / "portfolio" / "index.html").write_bytes(b"<html>alterado</html>")
        with self.assertRaises(A.VerifyError):
            A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_verify_detects_file_outside_manifest(self):
        self.assemble()
        write(self.out / "docs" / "plan.md", b"plano interno")
        with self.assertRaises(A.VerifyError):
            A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_verify_detects_missing_file(self):
        self.assemble()
        (self.out / "portfolio" / "img" / "a.webp").unlink()
        with self.assertRaises(A.VerifyError):
            A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_repeated_verify_sees_same_size_same_mtime_change(self):
        self.assemble()
        A.verify(self.repo, self.manifest, self.blog, self.out)
        dst = self.out / "index.html"
        st = dst.stat()
        dst.write_bytes(b"<html>HOME</html>")
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        with self.assertRaises(A.VerifyError):
            A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_verify_detects_changed_blog_file(self):
        self.assemble()
        (self.out / "blog" / "rss.xml").write_bytes(b"<rss>trocado</rss>")
        with self.assertRaises(A.VerifyError):
            A.verify(self.repo, self.manifest, self.blog, self.out)


class RealManifestTest(unittest.TestCase):
    """O manifesto versionado precisa bater com o que o site publica hoje."""

    REPO = Path(__file__).resolve().parent.parent

    def test_real_manifest_parses_and_every_entry_exists(self):
        entries = A.parse_manifest(self.REPO / "site-manifest.txt")
        for e in entries:
            p = self.REPO / e.rstrip("/")
            self.assertTrue(p.exists(), e)
            self.assertEqual(e.endswith("/"), p.is_dir(), e)

    def test_real_manifest_excludes_hidden_folders(self):
        entries = A.parse_manifest(self.REPO / "site-manifest.txt")
        for hidden in ["docs/", "_next/", "tools/", "blog-src/", "scripts/", ".github/", "workers/"]:
            self.assertNotIn(hidden, entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
