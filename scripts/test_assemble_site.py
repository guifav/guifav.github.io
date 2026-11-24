"""Testes do montador do site publicado (scripts/assemble_site.py).

Rodar: python3 scripts/test_assemble_site.py
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import assemble_site as A


HOME_SRC = b"<html>home<!-- ultimos-artigos --><!-- /ultimos-artigos --></html>"
LATEST_ITEM = {"title": "A & B <c>", "path": "/blog/um-post/", "date": "2026-11-24",
               "date_label": "24 de novembro de 2026", "description": "Desc \"x\""}


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
        write(self.repo / "index.html", HOME_SRC)
        write(self.repo / "CNAME", b"guifav.dev\n")
        write(self.repo / "portfolio" / "index.html", b"<html>portfolio</html>")
        write(self.repo / "portfolio" / "img" / "a.webp", b"\x00\x01webp")
        # Pastas que existem no repo e nunca podem ir ao ar.
        write(self.repo / "docs" / "plan.md", b"plano interno")
        write(self.repo / "_next" / "static" / "x.js", b"js")
        write(self.repo / "_mockups" / "README.md", b"readme")
        write(self.repo / "blog-src" / "package.json", b"{}")
        write(self.repo / "workers" / "newsletter" / "index.js", b"segredo-nao")
        write(self.blog / "index.html", b"<html>blog</html>")
        write(self.blog / "rss.xml", b"<rss/>")
        write(self.blog / "ultimos-artigos.json", json.dumps({"posts": [LATEST_ITEM]}).encode())
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
        for name in ["docs", "_next", "_mockups", "blog-src", "workers"]:
            self.assertFalse((self.out / name).exists(), name)

    def test_root_files_are_byte_identical(self):
        self.assemble()
        # A home leva a lista de últimos artigos (HomeLatestTest); o resto é cópia exata.
        for rel in ["CNAME", "portfolio/index.html", "portfolio/img/a.webp"]:
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

    def test_symlinked_ancestor_of_blog_build_is_rejected(self):
        outside = Path(self.tmp.name) / "fora"
        write(outside / "dist" / "index.html", b"<html>de fora</html>")
        os.symlink(outside, self.repo / "blog-src-link")
        with self.assertRaises(A.ManifestError):
            A.assemble(self.repo, self.manifest, self.repo / "blog-src-link" / "dist", self.out)

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
        # Arquivo copiado (a home é renderizada): mesmo tamanho e mesmo mtime, conteúdo trocado.
        dst = self.out / "portfolio" / "index.html"
        st = dst.stat()
        dst.write_bytes(b"<html>PORTFOLIO</html>")
        self.assertEqual(dst.stat().st_size, st.st_size)
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
        for hidden in ["docs/", "_next/", "_mockups/", "blog-src/", "scripts/", ".github/", "workers/"]:
            self.assertNotIn(hidden, entries)



class HomeLatestTest(Fixture):
    """#136: a home publicada traz os últimos artigos do blog entre os marcadores; o resto é cópia exata."""

    def test_home_gets_the_list_escaped_and_verify_accepts_it(self):
        self.assemble()
        self.assertEqual(A.verify(self.repo, self.manifest, self.blog, self.out), 6)
        self.assertFalse((self.out / "blog" / "ultimos-artigos.json").exists())  # consumido, não vai ao ar
        got = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertEqual(got, '<html>home<!-- ultimos-artigos --><ul class="latest-list" lang="pt-BR"><li><a href="/blog/um-post/" '
                         'hreflang="pt-BR">A &amp; B &lt;c&gt;</a> <time datetime="2026-11-24">24 de novembro de 2026'
                         '</time><p>Desc &quot;x&quot;</p></li></ul><!-- /ultimos-artigos --></html>')

    def test_verify_rejects_a_home_copied_without_the_list_or_edited(self):
        self.assemble()
        for data in (HOME_SRC, (self.out / "index.html").read_bytes() + b" "):
            with self.subTest(data=data[:40]):
                (self.out / "index.html").write_bytes(data)
                with self.assertRaisesRegex(A.VerifyError, "diferente da origem: index.html"):
                    A.verify(self.repo, self.manifest, self.blog, self.out)

    def test_markers_must_exist_once_in_order_and_empty(self):
        for src in (b"<html>home</html>", HOME_SRC + b"<!-- ultimos-artigos -->",
                    b"<!-- /ultimos-artigos --><!-- ultimos-artigos -->",
                    b"<!-- ultimos-artigos --><ul>velha</ul><!-- /ultimos-artigos -->"):
            with self.subTest(src=src):
                write(self.repo / "index.html", src)
                with self.assertRaises(A.RenderError):
                    self.assemble()

    def test_blog_build_must_bring_a_valid_list(self):
        bad = [None, {"posts": []}, {"posts": "x"}, [LATEST_ITEM],
               {"posts": [dict(LATEST_ITEM, path="https://outro.site/x/")]},
               {"posts": [dict(LATEST_ITEM, path="/blog/../x/")]},
               {"posts": [dict(LATEST_ITEM, date="24/11/2026")]},
               {"posts": [dict(LATEST_ITEM, date="2026-02-30")]},
               {"posts": [dict(LATEST_ITEM, title="  ")]},
               {"posts": [{k: v for k, v in LATEST_ITEM.items() if k != "description"}]}]
        for data in bad:
            with self.subTest(data=data):
                write(self.blog / "ultimos-artigos.json", json.dumps(data).encode())
                with self.assertRaises(A.RenderError):
                    self.assemble()
        (self.blog / "ultimos-artigos.json").unlink()
        with self.assertRaisesRegex(A.RenderError, "sem ultimos-artigos.json"):
            self.assemble()
        write(self.blog / "ultimos-artigos.json", b"{quebrado")
        with self.assertRaisesRegex(A.RenderError, "ilegível"):
            self.assemble()

    def test_render_error_keeps_the_previous_site(self):
        self.assemble()
        write(self.blog / "ultimos-artigos.json", b"{quebrado")
        with self.assertRaises(A.RenderError):
            self.assemble()
        self.assertTrue((self.out / "index.html").is_file())

    def test_without_home_in_the_manifest_nothing_is_rendered(self):
        self.manifest.write_text("CNAME\n", encoding="utf-8")
        (self.blog / "ultimos-artigos.json").unlink()
        self.assemble()
        self.assertEqual(A.verify(self.repo, self.manifest, self.blog, self.out), 3)

    def test_main_reports_render_errors(self):
        import contextlib
        import io
        write(self.repo / "index.html", b"<html>sem marcadores</html>")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = A.main(["--repo", str(self.repo), "--blog", str(self.blog), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("ERRO: index.html: precisa de um", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
