"""Testes dos redirects de URLs antigas (#102). Rodar: python3 scripts/test_site_redirects.py"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as C  # noqa: E402
import site_redirects as S  # noqa: E402

POSTS = Path(__file__).resolve().parent.parent / "blog-src/src/content/posts"
APEX, WWW = "guilhermefavaron.com.br", "www.guilhermefavaron.com.br"

# O plano postado na #102, escrito à parte (não usa OLD_POSTS): caminho antigo -> post.
PLAN = {
    "analise-preditiva-tomada-decisao-modelos-empresariais": "analise-preditiva-tomada-decisao-producao",
    "arquitetura-ia-empresarial-definitiva": "a-arquitetura-definitiva-de-ia-para",
    "construindo-times-ia-lideranca-tecnica-transformacao-empresarial": "construindo-times-de-ia-lideranca",
    "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1":
        "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1-0f6d9e9adf6a",
    "customer-analytics-metodologias-segmentacao-comportamental": "ab-testing-e-experimentacao-cientifica",
    "customer-analytics-segmentacao-personalizacao-dados": "customer-analytics-e-segmentacao",
    "data-pipelines-empresariais-silos-dados-business-intelligence": "data-pipelines-empresariais-como",
    "desmistificando-gpus-em-ia": "desmistificando-gpus-em-ia-79e168b03ccd",
    "fine-tuning-vs-prompt-engineering-decisoes-roi": "fine-tuning-vs-prompt-engineering",
    "ia-brasileira-sabia-2-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional":
        "ia-brasileira-sabia-3-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional",
    "ia-generativa-producao-custos-performance-escalabilidade": "ia-generativa-em-producao-custos",
    "langgraph-workflows-complexos-ia-orquestracao": "langgraph-orquestrando-workflows",
    "microservices-vs-monolith-decisoes-arquiteturais-competitividade": "microservices-vs-monolith-decisoes",
    "modernizacao-sistemas-legados-vantagem-competitiva": "modernizacao-de-sistemas-legados",
    "o-futuro-das-organizacoes-alem-da-tecnologia-e-processos": "o-futuro-das-organizacoes-alem-da",
    "o-surgimento-da-ia-geral-super-humana":
        "o-surgimento-da-ia-geral-super-humana-nao-sera-um-evento-o-avanco-sera-progressivo-dbf420240ecc",
    "streaming-data-em-escala-arquiteturas-para-analytics-sem-latencia-analitica":
        "streaming-data-em-escala-arquiteturas",
}
# Grupo C da #120 (decisão do Gui): fica em 404, fora da lista.
GROUP_C_ENCODED = "como-a-intelig%c3%aancia-artificial-vai-transformar-a-lideran%c3%a7a-e-os-neg%c3%b3cios-no-futuro"
# Location que a zona antiga devolveu ao vivo em 24/11 para o caminho com acentos (curl -I).
ENCODED_LIVE = ("https://guifav.dev/blog/como-a-intelig%C3%AAncia-artificial-vai-transformar-a-lideran%C3%A7a-e-os-"
                "neg%C3%B3cios-no-futuro/")


def end_to_end(url: str) -> str | None:
    """Caminho antigo -> regra do cutover -> lista. Devolve o destino final ou None."""
    p = url.split("://", 1)[1]
    host, path = p.split("/", 1)
    mid = C.simulate(host, "/" + path)
    return None if mid is None else S.simulate(mid)


class PlanTest(unittest.TestCase):
    def test_table_is_exactly_the_posted_plan(self):
        self.assertEqual(dict(S.OLD_POSTS), PLAN)
        self.assertEqual(len(S.OLD_POSTS), 17)

    def test_group_c_path_stays_out_of_the_list(self):
        self.assertNotIn(GROUP_C_ENCODED, dict(S.OLD_POSTS))
        self.assertIsNone(S.simulate(S.first_hop(GROUP_C_ENCODED)))

    def test_real_repo_has_no_problems(self):
        self.assertEqual(S.problems(), [])

    def test_every_target_is_an_indexable_post_file(self):
        for new in PLAN.values():
            f = POSTS / f"{new}.md"
            self.assertTrue(f.is_file(), new)
            self.assertNotRegex(f.read_text(encoding="utf-8").split("---")[1], r"(?m)^index:\s*false")

    def test_no_old_path_is_a_post_today(self):
        for old in PLAN:
            self.assertFalse((POSTS / f"{old}.md").exists(), old)

    def test_every_old_path_ends_on_its_post_from_both_hosts_with_and_without_slash(self):
        for old, new in PLAN.items():
            for host in (APEX, WWW):
                for suffix in ("", "/"):
                    for scheme in ("https", "http"):
                        url = f"{scheme}://{host}/post/{old}{suffix}"
                        self.assertEqual(end_to_end(url), f"https://guifav.dev/blog/{new}/", url)

    def test_encoded_path_matches_the_location_the_zone_really_sends(self):
        self.assertEqual(S.first_hop(GROUP_C_ENCODED), ENCODED_LIVE)
        with mock.patch.object(S, "OLD_POSTS", ((GROUP_C_ENCODED, "novo"),)):
            self.assertIn(ENCODED_LIVE.removeprefix("https://"), {r.source for r in S.redirects()})

    def test_existing_posts_keep_the_one_hop_cutover_and_are_not_in_the_list(self):
        for slug in sorted(p.stem for p in POSTS.glob("*.md"))[::20]:
            mid = C.simulate(WWW, f"/post/{slug}")
            self.assertEqual(mid, f"https://guifav.dev/blog/{slug}/")
            self.assertIsNone(S.simulate(mid), slug)

    def test_list_has_both_slash_forms_and_nothing_else(self):
        reds = S.redirects()
        self.assertEqual(len(reds), 34)
        for old, new in PLAN.items():
            src = C.simulate(WWW, f"/post/{old}").removeprefix("https://")
            got = {r.source: r.target for r in reds if r.source.rstrip("/") == src.rstrip("/")}
            self.assertEqual(got, {src: f"https://guifav.dev/blog/{new}/",
                                   src.rstrip("/"): f"https://guifav.dev/blog/{new}/"})


class SimulateTest(unittest.TestCase):
    R = [S.Redirect("guifav.dev/blog/velho/", "https://guifav.dev/blog/novo/")]

    def test_scheme_query_and_host_case_do_not_matter(self):
        for url in ("https://guifav.dev/blog/velho/", "http://guifav.dev/blog/velho/",
                    "https://guifav.dev/blog/velho/?utm_source=x", "https://GUIFAV.DEV/blog/velho/"):
            self.assertEqual(S.simulate(url, self.R), "https://guifav.dev/blog/novo/", url)

    def test_path_is_exact_case_sensitive_and_without_subpaths(self):
        for url in ("https://guifav.dev/blog/velho", "https://guifav.dev/blog/Velho/",
                    "https://guifav.dev/blog/velho/x/", "https://www.guifav.dev/blog/velho/",
                    "https://guilhermefavaron.com.br/blog/velho/"):
            self.assertIsNone(S.simulate(url, self.R), url)


def _posts(tmp: Path, files: dict[str, str]) -> Path:
    for name, fm in files.items():
        (tmp / f"{name}.md").write_text(f"---\ntitle: x\n{fm}---\ncorpo\n", encoding="utf-8")
    return tmp


class ProblemsTest(unittest.TestCase):
    def run_with(self, table, posts, reds=None):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "OLD_POSTS", table):
            p = _posts(Path(d), posts)
            return S.problems(S.redirects() if reds is None else reds, p)

    def test_target_must_be_an_indexable_post(self):
        self.assertEqual(self.run_with((("velho", "novo"),), {"novo": ""}), [])
        for posts in ({}, {"novo": "index: false\n"}, {"outro": ""}):
            with self.subTest(posts=posts):
                self.assertTrue(any("não é post indexável" in p for p in self.run_with((("velho", "novo"),), posts)))

    def test_noindex_detection_ignores_body_and_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "a.md").write_text("---\ntitle: x\nnewsletter: false\n---\nindex: false\n", encoding="utf-8")
            (p / "b.md").write_text("---\ntitle: x\nindex: false\n---\n", encoding="utf-8")
            (p / "c.md").write_text("---\ntitle: x\nindex: true\n---\n", encoding="utf-8")
            self.assertEqual(S.indexable_posts(p), {"a", "c"})

    def test_old_path_that_is_a_post_is_refused(self):
        got = self.run_with((("velho", "novo"),), {"novo": "", "velho": "index: false\n"})
        self.assertTrue(any("já é um post" in p for p in got), got)

    def test_source_that_is_an_indexable_post_is_refused(self):
        reds = [S.Redirect("guifav.dev/blog/novo/", "https://guifav.dev/blog/outro/")]
        got = self.run_with((("velho", "outro"),), {"outro": "", "novo": ""}, reds)
        self.assertTrue(any("esconderia a página" in p for p in got), got)

    def test_chain_duplicate_and_foreign_host_are_refused(self):
        cases = {
            "cadeia": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/blog/b/"),
                       S.Redirect("guifav.dev/blog/b/", "https://guifav.dev/blog/novo/")],
            "repetida": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/blog/novo/")] * 2,
            "fora de": [S.Redirect("www.guifav.dev/blog/a/", "https://guifav.dev/blog/novo/")],
            "destino fora": [S.Redirect("guifav.dev/blog/a/", "https://guifav.dev/novo")],
        }
        for word, reds in cases.items():
            with self.subTest(word):
                got = self.run_with((("velho", "novo"),), {"novo": ""}, reds)
                self.assertTrue(any(word in p for p in got), got)

    def test_bad_old_path_and_repeated_old_path_are_refused(self):
        for table, word in (((("Velho", "novo"),), "fora do formato"), ((("a b", "novo"),), "fora do formato"),
                            ((("velho", "novo"), ("velho", "novo")), "repetido")):
            with self.subTest(table=table):
                with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "OLD_POSTS", table):
                    got = S.problems([], _posts(Path(d), {"novo": ""}))
                self.assertTrue(any(word in p for p in got), got)

    def test_limit_of_the_free_plan_counts_both_lists_of_the_pair(self):
        def reds(n):
            return [S.Redirect(f"guifav.dev/blog/x{i}/", "https://guifav.dev/blog/novo/") for i in range(n)]
        half = S.MAX_ITEMS // len(S.LISTS)
        self.assertFalse(any("plano Free" in p for p in self.run_with((("velho", "novo"),), {"novo": ""}, reds(half))))
        got = self.run_with((("velho", "novo"),), {"novo": ""}, reds(half + 1))
        self.assertTrue(any("plano Free" in p for p in got), got)


class PayloadTest(unittest.TestCase):
    def test_items_are_exact_301_without_query_subdomains_or_subpaths(self):
        items = S.list_items()
        self.assertEqual(len(items), 34)
        for it in items:
            r = it["redirect"]
            self.assertEqual(set(r), {"source_url", "target_url", "status_code", "preserve_query_string",
                                      "include_subdomains", "subpath_matching"})
            self.assertEqual((r["status_code"], r["preserve_query_string"], r["include_subdomains"],
                              r["subpath_matching"]), (301, False, False, False))
            self.assertFalse(r["source_url"].startswith("http"))

    def test_rule_enables_exactly_the_given_list_of_the_pair(self):
        self.assertEqual(S.LISTS, ("guifav_redirects_a", "guifav_redirects_b"))
        for name in S.LISTS:
            r = S.rule(name)
            self.assertEqual(r["expression"], f"http.request.full_uri in ${name}")
            self.assertEqual(r["action_parameters"], {"from_list": {"name": name, "key": "http.request.full_uri"}})
            self.assertEqual((r["action"], r["enabled"], r["description"]), ("redirect", True, S.RULE_DESCRIPTION))
        with self.assertRaises(ValueError):
            S.rule("guifav_redirects")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeCF:
    """Conta Cloudflare em memória, com o registro das chamadas."""

    def __init__(self, phase=None, lists=None, op_status=("pending", "completed"), echo_items=True,
                 echo_rule=True, page=20, items=None):
        self.phase = phase  # None = fase sem ruleset
        self.lists = lists if lists is not None else []
        self.items: dict[str, list[dict]] = dict(items or {})  # itens por id de lista
        self.op_status = list(op_status)
        self.echo_items, self.echo_rule, self.page = echo_items, echo_rule, page
        self.calls: list[tuple[str, str]] = []
        self.raw_cursors: list[str] = []

    def __call__(self, req, timeout=None):
        m, url = req.get_method(), req.full_url.removeprefix(S.API)
        body = json.loads(req.data) if req.data else None
        self.calls.append((m, url.split("?")[0]))
        ok = lambda res, **kw: _Resp(json.dumps({"success": True, "result": res, "errors": [], **kw}).encode())  # noqa
        if url.endswith(f"/phases/{S.PHASE}/entrypoint"):
            if m == "GET":
                if self.phase is None:
                    return _Resp(json.dumps({"success": False, "errors": [{"code": 10003}]}).encode())
                return ok({"rules": self.phase})
            self.phase = body["rules"] if self.echo_rule else [{"description": "x"}]
            return ok({"id": "rs1", "rules": self.phase})
        if url.split("?")[0].endswith("/rules/lists") and m == "GET":
            return ok(self.lists)
        if url.endswith("/rules/lists") and m == "POST":
            new = {"id": "L-" + body["name"][-1], **body}
            self.lists.append(new)
            return ok(new)
        if "/items" in url and m == "PUT":
            self.items[url.split("/lists/")[1].split("/")[0]] = body if self.echo_items else body[:-1]
            return ok({"operation_id": "op1"})
        if "/bulk_operations/" in url:
            st = self.op_status.pop(0) if len(self.op_status) > 1 else self.op_status[0]
            return ok({"status": st, "error": "boom" if st == "failed" else None})
        if "/items" in url and m == "GET":
            start = 0
            if "cursor=" in url:
                raw = url.split("cursor=")[1]
                self.raw_cursors.append(raw)
                start = int(urllib.parse.unquote(raw).rsplit("=", 1)[1])
            items = self.items.get(url.split("/lists/")[1].split("/")[0], [])
            chunk = [{"id": str(i), "created_on": "x", "redirect": {**it["redirect"], "preserve_path_suffix": True}}
                     for i, it in enumerate(items[start:start + self.page], start)]
            after = start + self.page
            cursors = {"after": f"c+/x={after}"} if after < len(items) else {}  # opaco, como os da Cloudflare
            return ok(chunk, result_info={"cursors": cursors})
        raise AssertionError(f"chamada inesperada {m} {url}")

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class ApplyTest(unittest.TestCase):
    def apply(self, cf):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.apply("tok", "acc", opener=cf, sleep=lambda s: None)

    A, B = S.LISTS
    OLD = [{"redirect": {"source_url": "guifav.dev/blog/velho/", "target_url": "https://guifav.dev/blog/x/",
                         "status_code": 301, "preserve_query_string": False, "include_subdomains": False,
                         "subpath_matching": False}}]

    def live(self, on=None):
        """Conta com a regra ativa na lista `on` (que tem OLD) e a outra lista do par já criada."""
        lists = [{"id": "LA", "name": self.A, "kind": "redirect", "description": S.LIST_DESCRIPTION},
                 {"id": "LB", "name": self.B, "kind": "redirect", "description": S.LIST_DESCRIPTION}]
        rule = S.rule(on)
        return FakeCF(phase=[{"id": "r", **rule}], lists=lists,
                      items={"LA" if on == self.A else "LB": list(self.OLD)})

    def test_first_apply_creates_list_a_fills_it_reads_it_back_then_enables_the_rule(self):
        cf = FakeCF()
        self.assertEqual(self.apply(cf), 0)
        self.assertEqual(cf.writes(), [("POST", "/accounts/acc/rules/lists"),
                                       ("PUT", "/accounts/acc/rules/lists/L-a/items"),
                                       ("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
        put_items = cf.calls.index(("PUT", "/accounts/acc/rules/lists/L-a/items"))
        reads = [i for i, c in enumerate(cf.calls) if c == ("GET", "/accounts/acc/rules/lists/L-a/items")]
        self.assertGreaterEqual(len(reads), 2)  # 32 itens em páginas de 20: o cursor foi seguido
        self.assertTrue(put_items < reads[0] and reads[-1] < len(cf.calls) - 1)
        self.assertEqual(cf.phase, [S.rule(self.A)])
        self.assertEqual(len(cf.items["L-a"]), 34)
        self.assertEqual(cf.lists[0]["description"], S.LIST_DESCRIPTION)
        self.assertTrue(cf.raw_cursors and all("+" not in c and "/" not in c and "=" not in c for c in cf.raw_cursors),
                        cf.raw_cursors)  # cursor opaco codificado na query

    def test_update_writes_only_the_inactive_list_and_then_switches_the_rule(self):
        for on, other, other_id, on_id in ((self.A, self.B, "LB", "LA"), (self.B, self.A, "LA", "LB")):
            with self.subTest(on=on):
                cf = self.live(on)
                self.assertEqual(self.apply(cf), 0)
                self.assertEqual(cf.writes(), [("PUT", f"/accounts/acc/rules/lists/{other_id}/items"),
                                               ("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
                self.assertEqual(cf.phase, [S.rule(other)])
                self.assertEqual(cf.items[on_id], self.OLD)  # a lista ativa antes não foi tocada
                self.assertEqual(len(cf.items[other_id]), 34)

    def test_failed_update_keeps_the_active_rule_on_the_untouched_list(self):
        # Achado P1 da revisão de beb73ac: com a regra ativa, a falha de validação não pode deixá-la sobre uma
        # lista alterada e não conferida.
        for kw in ({"echo_items": False}, {"op_status": ("failed",)}, {"op_status": ("pending",)}):
            with self.subTest(**kw):
                cf = self.live(self.A)
                cf.echo_items = kw.get("echo_items", True)
                cf.op_status = list(kw.get("op_status", ("pending", "completed")))
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn(self.A, str(e.exception))
                self.assertEqual(cf.phase, [{"id": "r", **S.rule(self.A)}])
                self.assertEqual(cf.items["LA"], self.OLD)
                self.assertNotIn(("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint"), cf.calls)
                self.assertNotIn(("PUT", "/accounts/acc/rules/lists/LA/items"), cf.calls)

    def test_own_rule_on_an_unknown_list_or_two_own_rules_are_refused(self):
        weird = {**S.rule(self.A), "action_parameters": {"from_list": {"name": "outra", "key": "http.request.full_uri"}}}
        for phase in ([weird], [S.rule(self.A), S.rule(self.B)]):
            with self.subTest(phase=phase):
                cf = FakeCF(phase=phase)
                with self.assertRaises(SystemExit):
                    self.apply(cf)
                self.assertEqual(cf.writes(), [])

    def test_foreign_rule_in_the_phase_stops_before_any_write(self):
        # Inclusive regra alheia que liga uma lista do par: o PUT da fase a apagaria.
        for foreign in ({"description": "outra lista", "id": "r"},
                        {**S.rule(self.A), "description": "regra feita no painel", "id": "r"}):
            with self.subTest(foreign=foreign["description"]):
                cf = FakeCF(phase=[foreign])
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn("não são deste script", str(e.exception))
                self.assertEqual(cf.writes(), [])

    def test_list_with_the_same_name_of_another_kind_is_refused(self):
        for name in S.LISTS:
            with self.subTest(name=name):
                cf = FakeCF(lists=[{"id": "L9", "name": name, "kind": "ip", "description": S.LIST_DESCRIPTION}])
                with self.assertRaises(SystemExit) as e:
                    self.apply(cf)
                self.assertIn("de tipo 'ip'", str(e.exception))
                self.assertEqual(cf.writes(), [])

    def test_list_of_the_pair_with_another_description_is_not_adopted(self):
        for desc in (None, "", "lista feita no painel", S.LIST_DESCRIPTION + " "):
            for name in S.LISTS:
                with self.subTest(desc=desc, name=name):
                    cf = FakeCF(lists=[{"id": "L9", "name": name, "kind": "redirect", "description": desc}])
                    with self.assertRaises(SystemExit) as e:
                        self.apply(cf)
                    self.assertIn("outra descrição", str(e.exception))
                    self.assertEqual(cf.writes(), [])

    def test_list_problems_stop_before_any_call(self):
        cf = FakeCF()
        with mock.patch.object(S, "problems", return_value=["x"]), self.assertRaises(SystemExit):
            self.apply(cf)
        self.assertEqual(cf.calls, [])

    def test_rule_is_not_written_when_the_list_does_not_read_back_the_same(self):
        for cf in (FakeCF(echo_items=False), FakeCF(op_status=("failed",)), FakeCF(op_status=("pending",))):
            with self.subTest(op=cf.op_status, echo=cf.echo_items):
                with self.assertRaises(SystemExit):
                    self.apply(cf)
                self.assertNotIn(("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint"), cf.calls)

    def test_rule_answer_differing_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.apply(FakeCF(echo_rule=False))


class CfErrorsTest(unittest.TestCase):
    """Falha da API ou da rede vira SystemExit com método e caminho, sem traceback nem token."""

    def test_http_error_without_json_and_connection_errors(self):
        import urllib.error

        def http_html(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>502</html>"))

        def no_net(req, timeout=None):
            raise urllib.error.URLError("sem rede")

        def not_json(req, timeout=None):
            return _Resp(b"<html>ok</html>")

        for opener, word in ((http_html, "HTTP 502"), (no_net, "URLError"), (not_json, "JSONDecodeError")):
            with self.subTest(word=word):
                with self.assertRaises(SystemExit) as e:
                    S._cf("GET", "/accounts/acc/rules/lists", "segredo", opener=opener)
                msg = str(e.exception)
                self.assertIn("GET /accounts/acc/rules/lists", msg)
                self.assertIn(word, msg)
                self.assertNotIn("segredo", msg)


class RemoveTest(unittest.TestCase):
    def run_remove(self, cf):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.remove("tok", "acc", opener=cf)

    def test_own_rule_is_removed_and_the_list_is_kept(self):
        cf = FakeCF(phase=[S.rule(S.LISTS[0])], lists=[{"id": "L1", "name": S.LISTS[0]}])
        self.assertEqual(self.run_remove(cf), 0)
        self.assertEqual(cf.writes(), [("PUT", f"/accounts/acc/rulesets/phases/{S.PHASE}/entrypoint")])
        self.assertEqual(cf.phase, [])

    def test_nothing_to_remove_writes_nothing(self):
        for phase in (None, []):
            cf = FakeCF(phase=phase)
            self.assertEqual(self.run_remove(cf), 0)
            self.assertEqual(cf.writes(), [])

    def test_foreign_rule_is_refused(self):
        cf = FakeCF(phase=[{"description": "outra"}])
        with self.assertRaises(SystemExit):
            self.run_remove(cf)
        self.assertEqual(cf.writes(), [])


class CliTest(unittest.TestCase):
    def test_writes_need_yes_token_and_account(self):
        for cmd in ("apply", "remove"):
            with self.subTest(cmd=cmd):
                with mock.patch.object(S, cmd) as fn:
                    with self.assertRaises(SystemExit) as e:
                        S.main([cmd])
                    self.assertIn("--yes", str(e.exception))
                    for env in ({}, {"CLOUDFLARE_API_TOKEN": "t"}, {"CLOUDFLARE_ACCOUNT_ID": "a"}):
                        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit) as e:
                            S.main([cmd, "--yes"])
                        self.assertIn("CLOUDFLARE_ACCOUNT_ID", str(e.exception))
                    fn.assert_not_called()
                    with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a"},
                                         clear=True):
                        S.main([cmd, "--yes"])
                    fn.assert_called_once_with("t", "a")

    def test_rules_prints_the_payload(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(S.main(["rules"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual((data["lists"], len(data["items"])), (list(S.LISTS), 34))


class VerifyTest(unittest.TestCase):
    def live(self, break_url=None, answer=None):
        def fetch(url, method="GET"):
            if url == break_url:
                return answer
            p = url.split("://", 1)[1]
            host, path = p.split("/", 1)
            if host in (APEX, WWW):
                return 301, C.simulate(host, "/" + path)
            t = S.simulate(url)
            return (301, t) if t else (200, None)
        return fetch

    def run_verify(self, fetch):
        out = io.StringIO()
        return S.verify(fetch, pause=0, out=out), out.getvalue()

    def test_all_good_passes_and_counts_every_check(self):
        rc, out = self.run_verify(self.live())
        self.assertEqual(rc, 0, out)
        self.assertIn(f"ok: {17 * 4 + 17 + 34}; falhas: 0", out)

    def test_each_hop_is_checked(self):
        old, new = S.OLD_POSTS[0]
        mid = S.first_hop(old)
        final = f"https://guifav.dev/blog/{new}/"
        for url, answer in ((f"https://{WWW}/post/{old}", (301, "https://guifav.dev/blog/x/")),
                            (f"https://{APEX}/post/{old}/", (404, None)),
                            (mid, (404, None)), (mid.rstrip("/"), (302, final)),
                            (final, (404, None))):
            with self.subTest(url=url):
                rc, out = self.run_verify(self.live(url, answer))
                self.assertEqual(rc, 1)
                self.assertIn("FALHA " + url, out)


if __name__ == "__main__":
    unittest.main()
