"""Testes do snapshot da Arena para o /tools/llm-ranking/ (scripts/llm_ranking_snapshot.py, issue #113).

Rodar: python3 scripts/test_llm_ranking_snapshot.py
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_llm_ranking as B  # noqa: E402
import llm_ranking_snapshot as S  # noqa: E402

LICS = ("Proprietary", "MIT", "Apache 2.0", "Proprietary", "Llama 4")


def row(rank: int, date: str, category: str = "overall", name: str | None = None, rating: float | None = None,
        lic: str | None = None, votes=1000.0) -> dict:
    r = 1600.0 - 2 * rank if rating is None else rating
    return {"model_name": name or f"m-{rank:03d}", "organization": "org", "license": lic or LICS[rank % len(LICS)],
            "rating": r, "rating_lower": r - 4, "rating_upper": r + 4, "variance": 1.0, "vote_count": votes,
            "rank": rank, "category": category, "leaderboard_publish_date": date}


def dataset(latest: str = "2026-11-22", full_dates=("2026-11-08", "2026-11-11", "2026-11-19", "2026-11-22"),
            n: int = 130) -> dict[str, list[dict]]:
    """split -> linhas. O latest tem overall e todas as categorias; o full tem só overall por publicação."""
    lat = [row(i, latest) for i in range(1, n + 1)]
    for c in B.CATEGORIAS:
        lat += [row(i, latest, c, name=f"{c}-{i}") for i in range(1, 6)]
    full = [row(i, d, name=f"m-{i:03d}" if d != "2026-11-11" else f"old-{i:03d}") for d in full_dates
            for i in range(1, n + 1)]
    return {"latest": lat, "full": full}


class FakeServer:
    """Imita o /filter do datasets-server para os quatro filtros que o script usa."""

    def __init__(self, data, respostas=None):
        self.data, self.urls, self.respostas = data, [], list(respostas or [])

    def where(self, w: str):
        conds = re.findall(r'"(\w+)"\s*(=|<=)\s*(\'(?:[^\']|\'\')*\'|\d+)', w)
        out = []
        for col, op, raw in conds:
            val = raw[1:-1].replace("''", "'") if raw.startswith("'") else int(raw)
            out.append((col, op, val))
        return lambda r: all((r[c] == v) if op == "=" else (r[c] <= v) for c, op, v in out)

    def __call__(self, url: str):
        self.urls.append(url)
        if self.respostas:
            return self.respostas.pop(0)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert q["dataset"] == B.DATASET and q["config"] == B.CONFIG, q
        rows = [r for r in self.data[q["split"]] if self.where(q["where"])(r)]
        col, *desc = q["orderby"].replace('"', "").split()
        rows.sort(key=lambda r: r[col], reverse=bool(desc))
        off, length = int(q["offset"]), int(q["length"])
        body = {"rows": [{"row_idx": i, "row": r} for i, r in enumerate(rows[off:off + length])],
                "num_rows_total": len(rows)}
        return 200, json.dumps(body).encode(), {}


def fonte(server) -> S.Fonte:
    f = S.Fonte(get=server, sleep=lambda s: f.dormiu.append(s))
    f.dormiu = []
    return f


class ColetarTest(unittest.TestCase):
    def test_collects_and_validates(self):
        srv = FakeServer(dataset())
        snap = S.coletar(fonte(srv))
        B.validar(snap)
        self.assertEqual(snap["publicacao"], "2026-11-22")
        self.assertEqual(len(snap["modelos"]), 130)
        self.assertEqual(snap["modelos"][0], {"posicao": 1, "modelo": "m-001", "organizacao": "org", "licenca": "MIT",
                                              "pontuacao": 1598.0, "inferior": 1594.0, "superior": 1602.0, "votos": 1000})
        self.assertIsInstance(snap["modelos"][0]["votos"], int)
        self.assertEqual({c: len(v) for c, v in snap["categorias"].items()}, {c: 3 for c in B.CATEGORIAS})
        self.assertEqual(snap["categorias"]["coding"][0]["modelo"], "coding-1")

    def test_previous_is_the_latest_at_least_seven_days_before(self):
        snap = S.coletar(fonte(FakeServer(dataset())))
        self.assertEqual(snap["anterior"]["publicacao"], "2026-11-11")
        self.assertEqual(snap["anterior"]["modelos"][0]["modelo"], "old-001")
        self.assertEqual(len(snap["anterior"]["modelos"]), 130)
        snap = S.coletar(fonte(FakeServer(dataset(full_dates=("2026-11-08", "2026-11-22")))))
        self.assertEqual(snap["anterior"]["publicacao"], "2026-11-08")
        snap = S.coletar(fonte(FakeServer(dataset(full_dates=("2026-11-16", "2026-11-22")))))
        self.assertIsNone(snap["anterior"])
        snap = S.coletar(fonte(FakeServer(dataset(full_dates=("2026-11-15", "2026-11-22")))))
        self.assertEqual(snap["anterior"]["publicacao"], "2026-11-15")

    def test_pages_of_100_and_a_pause_between_calls(self):
        f = fonte(FakeServer(dataset()))
        S.coletar(f)
        # 2 páginas do latest, 17 categorias, 1 de datas, 2 páginas do anterior
        self.assertEqual(f.chamadas, 2 + len(B.CATEGORIAS) + 1 + 2)
        self.assertEqual(f.dormiu, [S.PAUSA] * (f.chamadas - 1))

    def test_more_than_one_date_in_latest_fails(self):
        d = dataset()
        d["latest"][5]["leaderboard_publish_date"] = "2026-11-19"
        with self.assertRaises(S.SnapshotError) as cm:
            S.coletar(fonte(FakeServer(d)))
        self.assertIn("mais de uma data", str(cm.exception))

    def test_category_with_another_date_fails(self):
        d = dataset()
        for r in d["latest"]:
            if r["category"] == "math":
                r["leaderboard_publish_date"] = "2026-11-19"
        with self.assertRaises(S.SnapshotError):
            S.coletar(fonte(FakeServer(d)))

    def test_bad_publish_date_in_latest_fails(self):
        # r1 (agy P3): data ausente ou fora do formato vira SnapshotError, não exceção solta.
        for v in (None, "22/11/2026", "2026-11-31"):
            d = dataset()
            for r in d["latest"]:
                r["leaderboard_publish_date"] = v
            with self.subTest(v=v):
                with self.assertRaises(S.SnapshotError):
                    S.coletar(fonte(FakeServer(d)))

    def test_unknown_license_is_caught_by_validation(self):
        d = dataset()
        d["latest"][0]["license"] = "Licença Nova"
        snap = S.coletar(fonte(FakeServer(d)))
        with self.assertRaises(B.RankingError):
            B.validar(snap)


class LinhaTest(unittest.TestCase):
    def test_votes(self):
        self.assertEqual(S.linha(row(1, "2026-11-22", votes=4255.0))["votos"], 4255)
        self.assertEqual(S.linha(row(1, "2026-11-22", votes=7))["votos"], 7)
        for v in (4255.5, "10", None, True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(v=v):
                with self.assertRaises(S.SnapshotError):
                    S.linha(row(1, "2026-11-22", votes=v))

    def test_missing_field_and_bad_row(self):
        r = row(1, "2026-11-22")
        del r["rating_upper"]
        with self.assertRaises(S.SnapshotError):
            S.linha(r)
        with self.assertRaises(S.SnapshotError):
            S.linha(None)

    def test_quote(self):
        self.assertEqual(S._q("a'b"), "'a''b'")


class TransporteTest(unittest.TestCase):
    def ok(self):
        return 200, json.dumps({"rows": [], "num_rows_total": 0}).encode(), {}

    def test_retries_429_with_retry_after_and_5xx(self):
        srv = FakeServer({}, [(429, b"", {"retry-after": "7"}), (503, b"", {}), self.ok()])
        f = fonte(srv)
        self.assertEqual(f.filtrar("latest", "x", "y", 0, 1)["rows"], [])
        self.assertEqual(f.dormiu, [7.0, S.PAUSA, 20.0, S.PAUSA])
        self.assertEqual(len(srv.urls), 3)

    def test_retry_after_is_capped(self):
        f = fonte(FakeServer({}, [(429, b"", {"retry-after": "100000"}), self.ok()]))
        f.filtrar("latest", "x", "y", 0, 1)
        self.assertIn(120.0, f.dormiu)

    def test_gives_up_without_sleeping_after_the_last_attempt(self):
        f = fonte(FakeServer({}, [(429, b"", {})] * S.TENTATIVAS))
        with self.assertRaises(S.SnapshotError) as cm:
            f.filtrar("latest", "x", "y", 0, 1)
        self.assertIn("depois de 5 tentativas (último status 429)", str(cm.exception))
        self.assertEqual(f.dormiu, [10.0, S.PAUSA, 20.0, S.PAUSA, 30.0, S.PAUSA, 40.0, S.PAUSA])

    def test_transport_failure_is_retried(self):
        # r1 (agy P2, Claude P3): falha de rede volta como status 0 e entra na nova tentativa, como um 5xx.
        f = fonte(FakeServer({}, [(0, b"", {}), self.ok()]))
        self.assertEqual(f.filtrar("latest", "x", "y", 0, 1)["rows"], [])
        self.assertEqual(f.dormiu, [10.0, S.PAUSA])

    def test_http_get_maps_transport_errors_to_status_0(self):
        import http.client
        import urllib.error

        def raising(exc):
            def urlopen(req, timeout):
                raise exc
            return urlopen
        for exc in (urllib.error.URLError("dns"), TimeoutError("lento"), ConnectionResetError("reset"),
                    http.client.IncompleteRead(b"x"), http.client.RemoteDisconnected("fim")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(S.http_get("https://exemplo/", urlopen=raising(exc)), (0, b"", {}))

    def test_http_get_keeps_the_http_status(self):
        import urllib.error

        class Broken(io.BytesIO):
            def read(self, *a):
                raise ConnectionResetError("no meio")

        def urlopen(req, timeout):
            raise urllib.error.HTTPError("https://exemplo/", 503, "x", {"Retry-After": "3"}, Broken())
        status, body, headers = S.http_get("https://exemplo/", urlopen=urlopen)
        self.assertEqual((status, body, headers.get("retry-after")), (503, b"", "3"))

    def test_other_status_fails_at_once(self):
        srv = FakeServer({}, [(404, b"", {}), self.ok()])
        with self.assertRaises(S.SnapshotError) as cm:
            fonte(srv).filtrar("latest", "x", "y", 0, 1)
        self.assertIn("HTTP 404", str(cm.exception))
        self.assertEqual(len(srv.urls), 1)

    def test_bad_bodies(self):
        for body in (b"{", b"[]", json.dumps({"rows": None}).encode()):
            with self.subTest(body=body):
                with self.assertRaises(S.SnapshotError):
                    fonte(FakeServer({}, [(200, body, {})])).filtrar("latest", "x", "y", 0, 1)

    def test_paging_guards(self):
        f = fonte(FakeServer({}, [(200, json.dumps({"rows": [], "num_rows_total": 5}).encode(), {})]))
        with self.assertRaises(S.SnapshotError):
            f.todas("latest", "x")
        f = fonte(FakeServer({}, [(200, json.dumps({"rows": []}).encode(), {})]))
        with self.assertRaises(S.SnapshotError):
            f.todas("latest", "x")


class GitTest(unittest.TestCase):
    """Repositório de dados de verdade: um bare como origin e um clone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
        self.origin, self.dados = base / "origin.git", base / "dados"
        self.sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.sh("git", "clone", "-q", str(self.origin), str(self.dados))
        (self.dados / "README.md").write_text("x\n")
        self.sh("git", "-C", str(self.dados), "add", "README.md")
        self.sh("git", "-C", str(self.dados), "commit", "-q", "-m", "init")
        self.sh("git", "-C", str(self.dados), "push", "-q", "origin", "HEAD:main")
        self.sh("git", "-C", str(self.dados), "branch", "-q", "--set-upstream-to=origin/main")
        self.old = os.environ.copy()
        os.environ.update(self.env)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        self.tmp.cleanup()

    def sh(self, *cmd):
        subprocess.run(cmd, check=True, capture_output=True, env=self.env)

    def origin_log(self) -> list[str]:
        r = subprocess.run(["git", "--git-dir", str(self.origin), "log", "--format=%s", "main"], capture_output=True,
                           text=True, check=True)
        return r.stdout.split("\n")[:-1]

    def test_writes_only_when_changed(self):
        self.assertTrue(S.gravar(self.dados, "a\n", "2026-11-22"))
        self.assertEqual(self.origin_log()[0], "Arena, arena de texto: publicação de 2026-11-22")
        self.assertFalse(S.gravar(self.dados, "a\n", "2026-11-22"))
        self.assertEqual(len(self.origin_log()), 2)
        self.assertTrue(S.gravar(self.dados, "b\n", "2026-11-25"))
        self.assertEqual(len(self.origin_log()), 3)
        r = subprocess.run(["git", "--git-dir", str(self.origin), "show", f"main:{S.ARQUIVO}"], capture_output=True,
                           text=True, check=True)
        self.assertEqual(r.stdout, "b\n")

    def test_not_a_clone(self):
        with self.assertRaises(S.SnapshotError):
            S.gravar(Path(self.tmp.name), "a\n", "2026-11-22")

    def test_failures_stop_before_writing(self):
        def git(dados, *args):
            return subprocess.CompletedProcess(args, 1 if args[0] == "fetch" else 0, "", "sem rede")
        with self.assertRaises(S.SnapshotError) as cm:
            S.gravar(self.dados, "a\n", "2026-11-22", git=git)
        self.assertIn("git fetch falhou: sem rede", str(cm.exception))
        self.assertFalse((self.dados / S.ARQUIVO).exists())

    def falha(self, etapa: str):
        """git de verdade, menos a etapa pedida, que falha."""
        def git(dados, *args):
            if args[0] == etapa:
                return subprocess.CompletedProcess(args, 1, "", "falhou de propósito")
            return S._git(dados, *args)
        return git

    def origin_file(self):
        r = subprocess.run(["git", "--git-dir", str(self.origin), "show", f"main:{S.ARQUIVO}"], capture_output=True,
                           text=True)
        return r.stdout if r.returncode == 0 else None

    def test_push_failure_is_published_on_the_next_run(self):
        # r1 (agy e Claude P1): o commit que ficou só local não pode fazer a rodada seguinte dizer "sem mudança".
        with self.assertRaises(S.SnapshotError) as cm:
            S.gravar(self.dados, "a\n", "2026-11-22", git=self.falha("push"))
        self.assertIn("git push falhou", str(cm.exception))
        self.assertIsNone(self.origin_file())
        self.assertTrue(S.gravar(self.dados, "a\n", "2026-11-22"))
        self.assertEqual(self.origin_file(), "a\n")
        self.assertFalse(S.gravar(self.dados, "a\n", "2026-11-22"))

    def test_commit_or_add_failure_is_published_on_the_next_run(self):
        for etapa in ("commit", "add"):
            with self.subTest(etapa=etapa):
                texto = f"{etapa}\n"
                with self.assertRaises(S.SnapshotError):
                    S.gravar(self.dados, texto, "2026-11-22", git=self.falha(etapa))
                self.assertNotEqual(self.origin_file(), texto)
                self.assertTrue(S.gravar(self.dados, texto, "2026-11-22"))
                self.assertEqual(self.origin_file(), texto)

    def test_local_commit_ahead_is_discarded_and_remote_wins(self):
        self.assertTrue(S.gravar(self.dados, "a\n", "2026-11-22"))
        (self.dados / S.ARQUIVO).write_text("lixo\n")
        self.sh("git", "-C", str(self.dados), "commit", "-qam", "commit local que não subiu")
        (self.dados / "sobra.txt").write_text("x")
        self.assertFalse(S.gravar(self.dados, "a\n", "2026-11-22"))
        self.assertEqual((self.dados / S.ARQUIVO).read_text(), "a\n")
        self.assertFalse((self.dados / "sobra.txt").exists())


class MainTest(GitTest):
    def main(self, *argv, data=None):
        f = fonte(FakeServer(data or dataset()))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main(["--dados", str(self.dados), *argv], fonte=f)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_touches_nothing(self):
        code, out, _ = self.main()
        self.assertEqual(code, 0)
        self.assertIn("publicacao: 2026-11-22\nmodelos: 130\nanterior: 2026-11-11\n", out)
        self.assertIn("simulação: nada foi gravado", out)
        self.assertFalse((self.dados / S.ARQUIVO).exists())
        self.assertEqual(len(self.origin_log()), 1)

    def test_apply_then_no_change(self):
        code, out, _ = self.main("--apply")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "gravado"))
        snap = json.loads((self.dados / S.ARQUIVO).read_text(encoding="utf-8"))
        B.validar(snap)
        code, out, _ = self.main("--apply")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "sem mudança"))
        self.assertEqual(len(self.origin_log()), 2)

    def test_invalid_source_writes_nothing(self):
        d = dataset()
        d["latest"][0]["license"] = "Licença Nova"
        code, out, err = self.main("--apply", data=d)
        self.assertEqual(code, 1)
        self.assertIn("licença desconhecida", err)
        self.assertNotIn("gravado", out)
        self.assertFalse((self.dados / S.ARQUIVO).exists())

    def test_serialization_is_stable(self):
        snap = S.coletar(fonte(FakeServer(dataset())))
        self.assertEqual(S.serializar(snap), S.serializar(json.loads(S.serializar(snap))))
        self.assertTrue(S.serializar(snap).endswith("}\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
