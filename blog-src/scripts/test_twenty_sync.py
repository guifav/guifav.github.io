"""Testes do sync dos assinantes com o Twenty (issue #40, parte E). Rodar em blog-src/: python3 scripts/test_twenty_sync.py"""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twenty_sync as T  # noqa: E402

ENV = {"CLOUDFLARE_API_TOKEN": "cf", "CLOUDFLARE_ACCOUNT_ID": "acc", "D1_DATABASE_ID": "db", "TWENTY_API_KEY": "tw",
       "TWENTY_LOCAL_URL": "http://127.0.0.1:3100"}


class Fake:
    """D1 pela API da Cloudflare e Twenty local (metadados GraphQL e REST de pessoas), em memória."""

    def __init__(self, rows=None, people=None, fields=(), meta=None, fail=None, listing=None, rate429=0):
        self.rows = rows or []
        self.meta = meta or {"last_sync_at": "2026-11-12T11:17:00Z", "last_sync_error": ""}
        self.people = {p["id"]: p for p in (people or [])}
        self.fields = set(fields)
        self.calls = []
        self.urls = []
        self.fail = fail  # (method, prefixo do caminho, status)
        self.created = 0
        self.listing = listing  # substitui a resposta da listagem de pessoas: f(query) -> (status, corpo)
        self.rate429 = rate429

    def __call__(self, method, url, headers, body):
        data = json.loads(body) if body else None
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.path, data))
        self.urls.append(url)
        if self.fail and method == self.fail[0] and u.path.startswith(self.fail[1]):
            return self.fail[2], {"errors": [{"message": "erro com segredo@x.com"}]}
        if u.netloc == "api.cloudflare.com":
            assert headers["Authorization"] == "Bearer cf" and u.path == "/client/v4/accounts/acc/d1/database/db/query"
            res = self.rows if "FROM subscribers" in data["sql"] else [{"k": k, "v": v} for k, v in self.meta.items()]
            return 200, {"success": True, "result": [{"results": res}]}
        assert headers["Authorization"] == "Bearer tw" and u.netloc == "127.0.0.1:3100"
        if self.rate429 > 0:
            self.rate429 -= 1
            return 429, {"messages": ["Too Many Requests"]}
        if u.path == "/metadata":
            if "createOneField" in data["query"]:
                f = data["variables"]["input"]["field"]
                self.fields.add(f["name"])
                return 200, {"data": {"createOneField": {"id": "f"}}}
            fields = [{"node": {"name": n}} for n in ["name", "emails", *sorted(self.fields)]]
            return 200, {"data": {"objects": {"edges": [{"node": {"id": "obj-company", "nameSingular": "company", "fields": {"edges": []}}},
                                                        {"node": {"id": "obj-person", "nameSingular": "person", "fields": {"edges": fields}}}]}}}
        if u.path == "/rest/people" and method == "GET":
            q = dict(urllib.parse.parse_qsl(u.query))
            if self.listing:
                return self.listing(q)
            items = sorted(self.people.values(), key=lambda p: p["id"])
            if "starting_after" in q:
                items = [p for p in items if p["id"] > q["starting_after"]]
            n = int(q["limit"])
            page = items[:n]
            return 200, {"data": {"people": page}, "pageInfo": {"hasNextPage": len(items) > n, "endCursor": page[-1]["id"] if page else None}}
        if u.path == "/rest/people" and method == "POST":
            self.created += 1
            pid = f"new-{self.created:04d}"
            self.people[pid] = {"id": pid, **data}
            return 201, {"data": {"createPerson": {"id": pid}}}
        if u.path.startswith("/rest/people/") and method == "PATCH":
            self.people[urllib.parse.unquote(u.path.rsplit("/", 1)[1])].update(data)
            return 200, {"data": {}}
        return 500, {"errors": [{"message": "rota inesperada"}]}

    def writes(self):
        return [(m, p) for m, p, d in self.calls if m != "GET" and not (p == "/metadata" and "createOneField" not in d["query"])
                and "api.cloudflare.com" not in p and not p.endswith("/query")]


def sub(email, unsubscribed=0, origin="substack", joined="2024-02-18T15:38:10"):
    return {"email": email, "unsubscribed": unsubscribed, "origin": origin, "joined_at": joined}


def person(pid, email, extra=(), **fields):
    return {"id": pid, "name": {"firstName": "Nome", "lastName": "X"}, "jobTitle": "CTO",
            "emails": {"primaryEmail": email, "additionalEmails": list(extra)}, **fields}


def run(fake, apply=False):
    out = io.StringIO()
    counts = T.run(T.Clients(ENV, fake, sleep=lambda s: None), apply=apply, out=out)
    return counts, out.getvalue()


class SyncTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        f = Fake(rows=[sub("a@x.com"), sub("b@x.com")], people=[person("p1", "A@X.com")])
        counts, out = run(f)
        self.assertEqual(f.writes(), [])
        self.assertEqual((counts["campos_a_criar"], counts["criar"], counts["atualizar"]), (3, 1, 1))
        self.assertIn("simulação", out)

    def test_creates_fields_people_and_updates_only_the_three_fields(self):
        f = Fake(rows=[sub("a@x.com"), sub("b@x.com", 1, "site", "2026-11-11 10:00:00+00"), sub("c@x.com", origin="outra", joined="")],
                 people=[person("p1", "outro@y.com", extra=["A@x.com"])])
        counts, _ = run(f, apply=True)
        self.assertEqual(f.fields, set(T.FIELDS))
        creates = [d["variables"]["input"]["field"] for m, p, d in f.calls if p == "/metadata" and "createOneField" in d["query"]]
        self.assertEqual({c["name"]: (c["type"], c["objectMetadataId"]) for c in creates},
                         {"iaAplicadaStatus": ("SELECT", "obj-person"), "iaAplicadaOrigem": ("SELECT", "obj-person"),
                          "iaAplicadaEntrada": ("DATE_TIME", "obj-person")})
        p1 = f.people["p1"]
        self.assertEqual((p1["name"], p1["jobTitle"], p1["emails"]["primaryEmail"]), ({"firstName": "Nome", "lastName": "X"}, "CTO", "outro@y.com"))
        self.assertEqual((p1["iaAplicadaStatus"], p1["iaAplicadaOrigem"], p1["iaAplicadaEntrada"]), ("INSCRITO", "SUBSTACK", "2024-02-18T15:38:10.000Z"))
        patch = [d for m, p, d in f.calls if m == "PATCH"]
        self.assertEqual([sorted(d) for d in patch], [["iaAplicadaEntrada", "iaAplicadaOrigem", "iaAplicadaStatus"]])
        new = {p["emails"]["primaryEmail"]: p for p in f.people.values() if p["id"].startswith("new-")}
        self.assertEqual(sorted(new), ["b@x.com", "c@x.com"])
        self.assertEqual((new["b@x.com"]["iaAplicadaStatus"], new["b@x.com"]["iaAplicadaOrigem"], new["b@x.com"]["iaAplicadaEntrada"]),
                         ("DESCADASTRADO", "SITE", "2026-11-11T10:00:00.000Z"))
        self.assertIsNone(new["c@x.com"]["iaAplicadaEntrada"])
        self.assertNotIn("name", new["b@x.com"])  # sem nome inventado
        self.assertEqual((counts["criar"], counts["atualizar"]), (2, 1))

    def test_second_run_is_a_no_op(self):
        f = Fake(rows=[sub("a@x.com"), sub("b@x.com")], people=[person("p1", "a@x.com")])
        run(f, apply=True)
        n = len(f.writes())
        counts, _ = run(f, apply=True)
        self.assertEqual(len(f.writes()), n)
        self.assertEqual(counts["sem_mudanca"], 2)

    def test_every_person_with_the_email_gets_the_fields(self):
        f = Fake(rows=[sub("a@x.com")], people=[person("p1", "a@x.com"), person("p2", "z@x.com", extra=["a@x.com"])], fields=T.FIELDS)
        counts, _ = run(f, apply=True)
        self.assertEqual(f.people["p1"]["iaAplicadaStatus"], "INSCRITO")
        self.assertEqual(f.people["p2"]["iaAplicadaStatus"], "INSCRITO")
        self.assertEqual((counts["email_em_mais_de_uma_pessoa"], counts["criar"]), (1, 0))

    def test_people_listing_is_paginated(self):
        people = [person(f"p{i:04d}", f"p{i}@x.com") for i in range(450)]
        f = Fake(rows=[sub(p["emails"]["primaryEmail"]) for p in people], people=people, fields=T.FIELDS)
        counts, _ = run(f)
        self.assertEqual((counts["criar"], counts["atualizar"]), (0, 450))
        lists = [x for x in f.urls if urllib.parse.urlparse(x).path == "/rest/people"]
        self.assertTrue(lists and all(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(x).query))["limit"] == "200" for x in lists))
        self.assertEqual(len([1 for m, p, _ in f.calls if m == "GET" and p == "/rest/people"]), 3)

    def test_incomplete_or_malformed_people_listing_is_an_error_and_creates_nobody(self):
        # Uma listagem incompleta tratada como fim faria criar de novo quem já está no CRM (duplicatas).
        existing = person("p1", "ana@x.com")
        page1 = {"data": {"people": [existing]}, "pageInfo": {"hasNextPage": True, "endCursor": "p1"}}
        cases = {
            "sem data.people": lambda q: (200, {"data": {}, "pageInfo": {"hasNextPage": False, "endCursor": None}}),
            "people não é lista": lambda q: (200, {"data": {"people": {}}, "pageInfo": {"hasNextPage": False, "endCursor": None}}),
            "sem pageInfo": lambda q: (200, {"data": {"people": [existing]}}),
            "hasNextPage não booleano": lambda q: (200, {"data": {"people": [existing]}, "pageInfo": {"hasNextPage": "false", "endCursor": "p1"}}),
            "cursor repetido": lambda q: (200, page1),
            "hasNextPage sem cursor": lambda q: (200, {"data": {"people": []}, "pageInfo": {"hasNextPage": True, "endCursor": None}}),
        }
        for why, listing in cases.items():
            f = Fake(rows=[sub("ana@x.com"), sub("bia@x.com")], people=[existing], fields=T.FIELDS, listing=listing)
            with self.assertRaises(T.SyncError, msg=why):
                run(f, apply=True)
            self.assertEqual([w for w in f.writes() if w[0] in ("POST", "PATCH")], [], why)

    def test_429_has_limited_retries(self):
        f = Fake(rows=[sub("ana@x.com")], fields=T.FIELDS, rate429=2)
        sleeps = []
        counts = T.run(T.Clients(ENV, f, sleep=sleeps.append), apply=True, out=io.StringIO())
        self.assertEqual(counts["criar"], 1)
        self.assertEqual(sleeps, [2, 2])
        g = Fake(rows=[sub("ana@x.com")], fields=T.FIELDS, rate429=1000)
        with self.assertRaisesRegex(T.SyncError, "429 persistente"):
            T.run(T.Clients(ENV, g, sleep=lambda s: None), apply=True, out=io.StringIO())
        self.assertEqual(len(g.calls), 5)

    def test_unsubscribed_status_updates_and_nobody_is_deleted(self):
        f = Fake(rows=[sub("a@x.com", 1)], people=[person("p1", "a@x.com", iaAplicadaStatus="INSCRITO", iaAplicadaOrigem="SUBSTACK",
                                                          iaAplicadaEntrada="2024-02-18T15:38:10.000Z"), person("p9", "fora@x.com", iaAplicadaStatus="INSCRITO")],
                 fields=T.FIELDS)
        run(f, apply=True)
        self.assertEqual(f.people["p1"]["iaAplicadaStatus"], "DESCADASTRADO")
        self.assertEqual(f.people["p9"]["iaAplicadaStatus"], "INSCRITO")
        self.assertNotIn("DELETE", [m for m, _ in f.writes()])

    def test_empty_copy_is_refused_and_last_copy_error_is_flagged(self):
        f = Fake(rows=[], fields=T.FIELDS)
        with self.assertRaises(T.SyncError):
            run(f, apply=True)
        self.assertEqual(f.writes(), [])
        f = Fake(rows=[sub("a@x.com")], fields=T.FIELDS, meta={"last_sync_error": "2026-11-12: Resend 500"})
        counts, _ = run(f)
        self.assertEqual(counts["aviso_ultima_copia_com_erro"], 1)


class OutputTest(unittest.TestCase):
    def cli(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in ENV}
        os.environ.update(ENV)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = T.main(list(args), transport=fake, sleep=lambda s: None)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_output_never_contains_emails(self):
        rc, text = self.cli(Fake(rows=[sub("segredo@x.com")], fields=T.FIELDS), "--apply")
        self.assertEqual(rc, 0)
        self.assertIn("criar: 1", text)
        self.assertNotIn("segredo", text)
        rc, text = self.cli(Fake(rows=[sub("segredo@x.com")], fields=T.FIELDS, fail=("POST", "/rest/people", 400)), "--apply")
        self.assertEqual(rc, 1)
        self.assertNotIn("segredo", text)
        def broken(*a):
            raise KeyError("segredo@x.com")
        rc, text = self.cli(broken)
        self.assertEqual((rc, "segredo" in text), (1, False))

    def test_argument_errors_do_not_echo(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(os.path.dirname(os.path.abspath(__file__)), "twenty_sync.py"), "segredo@x.com"],
                           capture_output=True, text=True, env={**os.environ, **ENV})
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("segredo", r.stdout + r.stderr)

    def test_unknown_origin_maps_to_outra(self):
        self.assertEqual(T.wanted(sub("a@x.com", origin="desconhecida"))["iaAplicadaOrigem"], "OUTRA")

    def test_iso_dates(self):
        self.assertEqual(T.iso("2024-02-18T15:38:10"), "2024-02-18T15:38:10.000Z")
        self.assertEqual(T.iso("2026-11-11 10:00:00+00"), "2026-11-11T10:00:00.000Z")
        self.assertEqual(T.iso("2026-11-12T09:00:00.000Z"), "2026-11-12T09:00:00.000Z")
        self.assertEqual(T.iso("2026-11-11T07:00:00-03:00"), "2026-11-11T10:00:00.000Z")
        self.assertEqual((T.iso(""), T.iso("lixo")), ("", ""))


if __name__ == "__main__":
    unittest.main()
