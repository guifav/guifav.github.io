"""Testes do sync das conversões do site com o Twenty (issue #97, fase 2). Rodar em blog-src/: python3 scripts/test_conversoes_sync.py

O D1 falso é um SQLite em memória com as migrações reais do painel (0004 e 0005), e o Twenty falso guarda estado como o
servidor: devolve cópias, recusa eventoId repetido (campo único, 400 como no Twenty 2.32) e cria o campo inverso da relação.
"""
import contextlib
import copy
import io
import json
import os
import sqlite3
import sys
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conversoes_sync as S  # noqa: E402
import twenty_sync as TS  # noqa: E402

ENV = {"CLOUDFLARE_API_TOKEN": "cf", "CLOUDFLARE_ACCOUNT_ID": "acc", "D1_DATABASE_ID": "db", "TWENTY_API_KEY": "tw",
       "TWENTY_LOCAL_URL": "http://127.0.0.1:3100"}
MIGRATIONS = os.path.join(HERE, "..", "..", "workers", "admin", "migrations")
CUSTO = {"id": "CUSTO_IA", "rotulo": "Custo de IA"}
ARQ = {"id": "ARQUITETURA_IA", "rotulo": "Arquitetura de IA (RAG, SQL, contexto longo)"}
EXPERT = {"id": "EXPERT_INSIGHTS", "rotulo": "Expert Insights"}


def hexid(n: int) -> str:
    return f"{n:064x}"


class Fake:
    """D1 pela API da Cloudflare (SQLite com as migrações reais) e Twenty local (metadata GraphQL e REST), em memória."""

    def __init__(self, people=(), schema=False, fail=None, listing=None):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        for name in ("0004_conversions.sql", "0005_conversions_pedido.sql"):
            with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as fh:
                self.db.executescript(fh.read())
        self.people = {p["id"]: copy.deepcopy(p) for p in people}
        self.person_fields = {n: {"id": f"pf-{n}", "name": n, "type": t, "options": None}
                              for n, t in (("name", "FULL_NAME"), ("emails", "EMAILS"), ("company", "RELATION"))}
        self.conv_obj = None
        self.conv_fields: dict = {}
        self.convs: dict = {}
        self.calls = []
        self.fail = fail  # f(method, path, data) -> (status, corpo) ou None
        self.listing = listing  # substitui a listagem de conversões: f(query) -> (status, corpo)
        self.n = 0
        self.db_close = self.db.close
        if schema:
            S.ensure_schema(TS.Clients(ENV, self, sleep=lambda s: None),
                            {"person_id": "obj-person", "person": self._fields_of("person"), "conv_id": None, "conv": {}},
                            {"CUSTO_IA": "Custo de IA"})
            self.calls.clear()

    # ---- D1 ----
    def add(self, n, email, action="material_download", material="planilha-custos-ia", interests=(CUSTO,), when="2026-11-24T10:00:00.000Z",
            verified=None, ga=None, request_id=None, file=None, synced=None, raw_interests=None):
        if verified is None:
            verified = 0 if action == "material_request" else 1
        if action == "material_download" and file is None:
            file = material
        self.db.execute("INSERT INTO conversions (id, email, action, material, interests, occurred_at, synced_at, verified, "
                        "ga_client_id, request_id, file) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (hexid(n) if isinstance(n, int) else n, email, action, material,
                         raw_interests if raw_interests is not None else json.dumps(list(interests)), when, synced, verified,
                         ga, request_id, file))

    def __del__(self):
        self.db_close()

    def row(self, n):
        r = self.db.execute("SELECT * FROM conversions WHERE id = ?", (hexid(n),)).fetchone()
        return dict(r) if r else None

    def pending(self):
        return sorted(r[0] for r in self.db.execute("SELECT id FROM conversions WHERE synced_at IS NULL"))

    # ---- transporte ----
    def __call__(self, method, url, headers, body):
        data = json.loads(body) if body else None
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.path, data, u.query))
        if self.fail:
            hit = self.fail(method, u.path, data)
            if hit:
                return hit
        if u.netloc == "api.cloudflare.com":
            assert headers["Authorization"] == "Bearer cf" and u.path == "/client/v4/accounts/acc/d1/database/db/query"
            assert "@" not in data["sql"], "email só por parâmetro"
            cur = self.db.execute(data["sql"], data.get("params") or [])
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            self.db.commit()
            return 200, {"success": True, "result": [{"results": rows}]}
        assert headers["Authorization"] == "Bearer tw" and u.netloc == "127.0.0.1:3100"
        st, res = self._twenty(method, u, data)
        return st, json.loads(json.dumps(res))  # como HTTP: o cliente nunca recebe os objetos do servidor

    def _fields_of(self, obj):
        src = self.person_fields if obj == "person" else self.conv_fields
        return {k: copy.deepcopy(v) for k, v in src.items()}

    def _twenty(self, method, u, data):
        if u.path == "/metadata":
            q = data["query"]
            v = data.get("variables") or {}
            if "createOneObject" in q:
                assert self.conv_obj is None
                o = v["input"]["object"]
                self.conv_obj = {"id": "obj-conv", **o}
                self.conv_fields = {"name": {"id": "cf-name", "name": "name", "type": "TEXT", "options": None}}
                return 200, {"data": {"createOneObject": {"id": "obj-conv"}}}
            if "createOneField" in q:
                f = v["input"]["field"]
                target = self.person_fields if f["objectMetadataId"] == "obj-person" else self.conv_fields
                assert f["objectMetadataId"] in ("obj-person", "obj-conv") and f["name"] not in target
                opts = [{"id": f"opt-{f['name']}-{o['value']}", **o} for o in f.get("options") or []] or None
                node = {"id": f"fid-{f['name']}", "name": f["name"], "type": f["type"], "options": opts, "_spec": f}
                target[f["name"]] = node
                if f["type"] == "RELATION":
                    rp = f["relationCreationPayload"]
                    assert rp["targetObjectMetadataId"] == "obj-person" and rp["type"] == "MANY_TO_ONE"
                    self.person_fields["conversoesDoSite"] = {"id": "pf-inv", "name": "conversoesDoSite", "type": "RELATION", "options": None}
                return 200, {"data": {"createOneField": {k: v for k, v in node.items() if k != "_spec"}}}
            if "updateOneField" in q:
                i = v["input"]
                for src in (self.person_fields, self.conv_fields):
                    for node in src.values():
                        if node["id"] == i["id"]:
                            old = {o["id"] for o in node["options"] or []}
                            sent = i["update"]["options"]
                            assert old <= {o.get("id") for o in sent}, "opção antiga sumiu"
                            node["options"] = [o if o.get("id") else {"id": f"opt-{node['name']}-{o['value']}", **o} for o in sent]
                            return 200, {"data": {"updateOneField": {"id": node["id"], "options": node["options"]}}}
                return 200, {"errors": [{"message": "campo não encontrado"}]}

            def obj(oid, ns, np, fields):
                return {"node": {"id": oid, "nameSingular": ns, "namePlural": np,
                                 "fields": {"edges": [{"node": {k: v for k, v in f.items() if k != "_spec"}} for f in fields.values()]}}}
            edges = [obj("obj-company", "company", "companies", {}), obj("obj-person", "person", "people", self.person_fields)]
            if self.conv_obj:
                edges.append(obj("obj-conv", self.conv_obj["nameSingular"], self.conv_obj["namePlural"], self.conv_fields))
            return 200, {"data": {"objects": {"edges": edges}}}
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path in ("/rest/people", "/rest/siteConversions") and method == "GET":
            if u.path == "/rest/siteConversions":
                assert self.conv_obj, "objeto inexistente"
                if self.listing:
                    return self.listing(q)
            store, key = (self.people, "people") if u.path == "/rest/people" else (self.convs, "siteConversions")
            items = sorted(store.values(), key=lambda p: p["id"])
            if "starting_after" in q:
                items = [p for p in items if p["id"] > q["starting_after"]]
            n = int(q["limit"])
            page = items[:n]
            return 200, {"data": {key: page}, "pageInfo": {"hasNextPage": len(items) > n, "endCursor": page[-1]["id"] if page else None}}
        if u.path == "/rest/people" and method == "POST":
            self.n += 1
            pid = f"new-{self.n:04d}"
            for k in data:
                assert k == "emails" or k in self.person_fields, f"campo inexistente {k}"
            self.people[pid] = {"id": pid, "createdAt": f"2026-11-24T12:00:{self.n:02d}.000Z", **data}
            return 201, {"data": {"createPerson": {"id": pid}}}
        if u.path.startswith("/rest/people/") and method == "PATCH":
            pid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            for k in data:
                assert k in self.person_fields, f"campo inexistente {k}"
            self.people[pid].update(data)
            return 200, {"data": {}}
        if u.path == "/rest/siteConversions" and method == "POST":
            assert self.conv_obj
            for k in data:
                assert k in self.conv_fields or (k == "pessoaId" and "pessoa" in self.conv_fields), f"campo inexistente {k}"
            if any(c["eventoId"] == data["eventoId"] for c in self.convs.values()):
                return 400, {"statusCode": 400, "error": "BadRequestException", "messages": ["A duplicate entry was detected"]}
            self.n += 1
            cid = f"conv-{self.n:04d}"
            self.convs[cid] = {"id": cid, **data}
            return 201, {"data": {"createSiteConversion": {"id": cid}}}
        if u.path.startswith("/rest/siteConversions/") and method == "DELETE":
            cid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            del self.convs[cid]
            return 200, {"data": {"deleteSiteConversion": {"id": cid}}}
        return 500, {"errors": [{"message": "rota inesperada"}]}

    def twenty_writes(self):
        return [(m, p) for m, p, d, _ in self.calls
                if "cloudflare" not in p and not p.endswith("/query") and m != "GET"
                and not (p == "/metadata" and not any(x in d["query"] for x in ("createOne", "updateOne")))]

    def d1_writes(self):
        return [d["sql"] for m, p, d, _ in self.calls if p.endswith("/query") and not d["sql"].lstrip().upper().startswith("SELECT")]


def person(pid, email, extra=(), created="2026-01-01T00:00:00.000Z", **fields):
    return {"id": pid, "createdAt": created, "name": {"firstName": "Nome", "lastName": "X"}, "jobTitle": "CTO",
            "emails": {"primaryEmail": email, "additionalEmails": list(extra)}, **fields}


def run(fake, apply=False):
    out = io.StringIO()
    counts = S.run(TS.Clients(ENV, fake, sleep=lambda s: None), apply=apply, out=out)
    return counts, out.getvalue()


def forget(fake, email, apply=False):
    out = io.StringIO()
    counts = S.forget(TS.Clients(ENV, fake, sleep=lambda s: None), email, apply=apply, out=out)
    return counts, out.getvalue()


def conv_by_event(fake):
    return {c["eventoId"]: c for c in fake.convs.values()}


class SyncTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        f = Fake(people=[person("p1", "a@x.com")])
        f.add(1, "a@x.com")
        f.add(2, "b@x.com")
        counts, out = run(f)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertEqual((counts["objeto_a_criar"], counts["pessoas_a_criar"], counts["pessoas_a_atualizar"], counts["conversoes_a_criar"]),
                         (1, 1, 1, 2))
        self.assertEqual(counts["campos_a_criar"], 4 + 9 + 1)
        self.assertEqual(counts["opcoes_a_criar"], 2)  # CUSTO_IA nas duas multi-seleções
        self.assertEqual(len(f.pending()), 2)
        self.assertIn("simulação", out)

    def test_fresh_crm_gets_schema_new_person_and_linked_conversion(self):
        f = Fake()
        f.add(1, "nova@x.com", ga="123.456", request_id=hexid(99), file="planilha-custos-ia")
        counts, _ = run(f, apply=True)
        self.assertEqual(f.conv_obj["nameSingular"], "siteConversion")
        self.assertEqual(f.conv_obj["namePlural"], "siteConversions")
        self.assertEqual({k: v["type"] for k, v in f.conv_fields.items()},
                         {"name": "TEXT", "acao": "SELECT", "comprovado": "BOOLEAN", "material": "TEXT", "arquivo": "TEXT",
                          "interesses": "MULTI_SELECT", "ocorreuEm": "DATE_TIME", "eventoId": "TEXT", "pedidoId": "TEXT",
                          "gaClientId": "TEXT", "pessoa": "RELATION"})
        self.assertTrue(f.conv_fields["eventoId"]["_spec"]["isUnique"])
        self.assertEqual([o["value"] for o in f.conv_fields["acao"]["options"]], ["MATERIAL_REQUEST", "MATERIAL_DOWNLOAD", "NEWSLETTER_CONFIRM"])
        for name, t in (("siteInteresses", "MULTI_SELECT"), ("siteUltimaConversao", "DATE_TIME"), ("siteEmailComprovado", "BOOLEAN"),
                        ("siteGaClientId", "TEXT")):
            self.assertEqual(f.person_fields[name]["type"], t)
        self.assertEqual([(o["value"], o["label"]) for o in f.person_fields["siteInteresses"]["options"]], [("CUSTO_IA", "Custo de IA")])
        self.assertEqual([(o["value"], o["label"]) for o in f.conv_fields["interesses"]["options"]], [("CUSTO_IA", "Custo de IA")])
        (p,) = f.people.values()
        self.assertEqual(p["emails"], {"primaryEmail": "nova@x.com", "additionalEmails": []})
        self.assertNotIn("name", p)  # sem nome inventado
        self.assertEqual((p["siteInteresses"], p["siteUltimaConversao"], p["siteEmailComprovado"], p["siteGaClientId"]),
                         (["CUSTO_IA"], "2026-11-24T10:00:00.000Z", True, "123.456"))
        (c,) = f.convs.values()
        self.assertEqual(c, {"id": c["id"], "name": "Download de material: planilha-custos-ia", "acao": "MATERIAL_DOWNLOAD",
                             "comprovado": True, "material": "planilha-custos-ia", "arquivo": "planilha-custos-ia",
                             "interesses": ["CUSTO_IA"], "ocorreuEm": "2026-11-24T10:00:00.000Z", "eventoId": hexid(1),
                             "pedidoId": hexid(99), "gaClientId": "123.456", "pessoaId": p["id"]})
        self.assertEqual(f.pending(), [])
        self.assertIsNotNone(f.row(1)["synced_at"])
        self.assertEqual((counts["pessoas_a_criar"], counts["conversoes_a_criar"], counts["linhas_marcadas"]), (1, 1, 1))

    def test_second_run_is_a_no_op_and_two_requests_make_two_conversions(self):
        f = Fake()
        f.add(1, "a@x.com", when="2026-11-24T10:00:00.000Z")
        f.add(2, "a@x.com", when="2026-11-25T10:00:00.000Z")  # outro pedido do mesmo material, outro dia: outro id
        run(f, apply=True)
        self.assertEqual(len(f.convs), 2)
        self.assertEqual(len(f.people), 1)
        n = len(f.twenty_writes())
        counts, out = run(f, apply=True)
        self.assertEqual(len(f.twenty_writes()), n)
        self.assertIn("nada pendente", out)
        self.assertEqual(counts["pendentes_lidas"], 0)

    def test_crash_after_conversion_before_mark_does_not_duplicate(self):
        state = {"armed": True}

        def fail(method, path, data):
            if state["armed"] and path.endswith("/query") and data["sql"].startswith("UPDATE"):
                state["armed"] = False
                return 500, {"success": False}
            return None
        f = Fake(fail=fail)
        f.add(1, "a@x.com")
        with self.assertRaisesRegex(S.SyncError, "D1 respondeu 500"):
            run(f, apply=True)
        self.assertEqual((len(f.convs), f.pending()), (1, [hexid(1)]))
        counts, _ = run(f, apply=True)
        self.assertEqual((len(f.convs), len(f.people), f.pending()), (1, 1, []))
        self.assertEqual((counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["pessoas_a_atualizar"]), (0, 1, 0))

    def test_existing_person_only_gets_site_fields(self):
        f = Fake(people=[person("p1", "outro@y.com", extra=["A@x.com"], iaAplicadaStatus="INSCRITO")])
        f.add(1, "a@x.com")
        run(f, apply=True)
        p = f.people["p1"]
        self.assertEqual((p["name"], p["jobTitle"], p["emails"]["primaryEmail"], p["iaAplicadaStatus"]),
                         ({"firstName": "Nome", "lastName": "X"}, "CTO", "outro@y.com", "INSCRITO"))
        patches = [d for m, path, d, _ in f.calls if m == "PATCH"]
        self.assertEqual([sorted(d) for d in patches], [["siteEmailComprovado", "siteInteresses", "siteUltimaConversao"]])
        self.assertEqual(len(f.people), 1)
        self.assertEqual(list(f.convs.values())[0]["pessoaId"], "p1")

    def test_request_without_person_stays_pending_and_never_creates_a_person(self):
        f = Fake()
        f.add(1, "a@x.com", action="material_request", ga="111.222")
        counts, _ = run(f, apply=True)
        self.assertEqual((f.people, f.convs, f.pending()), ({}, {}, [hexid(1)]))
        self.assertEqual(counts["pedido_sem_pessoa"], 1)
        self.assertEqual(f.twenty_writes(), [])  # nem o esquema é criado sem nada a gravar
        # o download chega depois: a Pessoa nasce dele, e o pedido vai junto
        f.add(2, "a@x.com", request_id=hexid(1), when="2026-11-24T11:00:00.000Z")
        counts, _ = run(f, apply=True)
        (p,) = f.people.values()
        ev = conv_by_event(f)
        self.assertEqual((ev[hexid(1)]["acao"], ev[hexid(1)]["comprovado"], ev[hexid(1)]["gaClientId"]), ("MATERIAL_REQUEST", False, "111.222"))
        self.assertEqual((ev[hexid(2)]["acao"], ev[hexid(2)]["pedidoId"]), ("MATERIAL_DOWNLOAD", hexid(1)))
        self.assertEqual((p["siteEmailComprovado"], p["siteGaClientId"], p["siteUltimaConversao"]), (True, "111.222", "2026-11-24T11:00:00.000Z"))
        self.assertEqual(f.pending(), [])

    def test_request_with_existing_person_becomes_unproven_conversion(self):
        f = Fake(people=[person("p1", "a@x.com")])
        f.add(1, "a@x.com", action="material_request", interests=(ARQ, CUSTO), material="mapa-decisao-ia")
        run(f, apply=True)
        (c,) = f.convs.values()
        self.assertEqual((c["acao"], c["comprovado"], c["arquivo"], c["interesses"], c["name"]),
                         ("MATERIAL_REQUEST", False, None, ["ARQUITETURA_IA", "CUSTO_IA"], "Pedido de material: mapa-decisao-ia"))
        p = f.people["p1"]
        self.assertEqual((bool(p.get("siteEmailComprovado")), p["siteInteresses"]), (False, ["ARQUITETURA_IA", "CUSTO_IA"]))
        # comprovado nunca volta a falso
        f.people["p1"]["siteEmailComprovado"] = True
        f.add(2, "a@x.com", action="material_request", when="2026-11-26T10:00:00.000Z")
        run(f, apply=True)
        self.assertIs(f.people["p1"]["siteEmailComprovado"], True)

    def test_interests_only_grow_and_new_catalog_interest_becomes_option(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        f.add(1, "a@x.com")
        run(f, apply=True)
        f.people["p1"]["siteInteresses"] = []  # tirada à mão
        f.add(2, "a@x.com", interests=(ARQ,), material="mapa-decisao-ia", when="2026-11-25T10:00:00.000Z")
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["opcoes_a_criar"], 2)
        self.assertEqual(f.people["p1"]["siteInteresses"], ["ARQUITETURA_IA"])  # só volta com conversão do tema
        for field in (f.person_fields["siteInteresses"], f.conv_fields["interesses"]):
            self.assertEqual([(o["value"], o["label"]) for o in field["options"]],
                             [("CUSTO_IA", "Custo de IA"), ("ARQUITETURA_IA", "Arquitetura de IA (RAG, SQL, contexto longo)")])
            self.assertEqual(field["options"][0]["id"], f"opt-{field['name']}-CUSTO_IA")  # a antiga ficou com o mesmo id
        f.add(3, "a@x.com", when="2026-11-26T10:00:00.000Z")
        run(f, apply=True)
        self.assertEqual(f.people["p1"]["siteInteresses"], ["ARQUITETURA_IA", "CUSTO_IA"])

    def test_two_emails_of_one_person_make_one_write_with_the_union(self):
        f = Fake(people=[person("p1", "a@x.com", extra=["b@x.com"])])
        f.add(1, "a@x.com", interests=(CUSTO,))
        f.add(2, "b@x.com", interests=(ARQ,), material="mapa-decisao-ia", when="2026-11-25T10:00:00.000Z")
        run(f, apply=True)
        self.assertEqual([m for m, p, d, _ in f.calls if m == "PATCH"], ["PATCH"])
        self.assertEqual(sorted(f.people["p1"]["siteInteresses"]), ["ARQUITETURA_IA", "CUSTO_IA"])
        self.assertEqual({c["pessoaId"] for c in f.convs.values()}, {"p1"})

    def test_email_in_two_people_goes_to_the_oldest(self):
        f = Fake(people=[person("p2", "z@x.com", extra=["a@x.com"], created="2025-01-01T00:00:00.000Z"),
                         person("p1", "a@x.com", created="2026-01-01T00:00:00.000Z")])
        f.add(1, "a@x.com")
        counts, _ = run(f, apply=True)
        self.assertEqual(list(f.convs.values())[0]["pessoaId"], "p2")
        self.assertNotIn("siteInteresses", f.people["p1"])
        self.assertEqual(counts["email_em_mais_de_uma_pessoa"], 1)

    def test_malformed_rows_are_counted_not_written_nor_marked(self):
        f = Fake(schema=True)
        bad = {
            "ação desconhecida": dict(action="material_view"),
            "pedido comprovado": dict(action="material_request", verified=1),
            "download não comprovado": dict(verified=0),
            "email com maiúscula": dict(email="A@x.com"),
            "email sem domínio": dict(email="a@x"),
            "material ausente no download": dict(material=None, file="x"),
            "material na confirmação": dict(action="newsletter_confirm"),
            "interesses não JSON": dict(raw_interests="nada"),
            "interesses objeto": dict(raw_interests="{}"),
            "interesse id minúsculo": dict(interests=({"id": "custo", "rotulo": "C"},)),
            "interesse sem rótulo": dict(interests=({"id": "CUSTO_IA", "rotulo": " "},)),
            "data inválida": dict(when="ontem"),
            "ga_client_id fora do formato": dict(ga="abc"),
            "request_id fora do formato": dict(request_id="xyz"),
            "arquivo no pedido": dict(action="material_request", file="planilha-custos-ia"),
        }
        for i, (why, kw) in enumerate(bad.items(), start=10):
            kw = {"email": "a@x.com", **kw}
            email = kw.pop("email")
            if why == "material ausente no download":
                f.add(i, email, material=None, file="x")
                continue
            if why == "material na confirmação":
                f.add(i, email, action="newsletter_confirm", material="planilha-custos-ia", interests=(EXPERT,))
                continue
            if why == "arquivo no pedido":
                f.add(i, email, action="material_request", file="planilha-custos-ia")
                continue
            f.add(i, email, **kw)
        f.add(1, "ok@x.com", action="newsletter_confirm", material=None, interests=(EXPERT,))
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["linha_invalida"], len(bad))
        self.assertEqual(list(conv_by_event(f)), [hexid(1)])
        self.assertEqual(len(f.pending()), len(bad))
        self.assertEqual(list(f.people.values())[0]["emails"]["primaryEmail"], "ok@x.com")
        (c,) = f.convs.values()
        self.assertEqual((c["acao"], c["material"], c["name"]), ("NEWSLETTER_CONFIRM", None, "Inscrição confirmada"))

    def test_only_invalid_rows_reads_nothing_from_twenty(self):
        f = Fake()
        f.add(1, "a@x.com", ga="abc")
        counts, out = run(f, apply=True)
        self.assertEqual(counts["linha_invalida"], 1)
        self.assertEqual([p for m, p, d, _ in f.calls if "cloudflare" not in p and not p.endswith("/query")], [])
        self.assertIn("só linhas inválidas", out)

    def test_ga_client_id_from_the_newest_conversion(self):
        f = Fake(people=[person("p1", "a@x.com", siteGaClientId="9.9", siteUltimaConversao="2026-11-30T00:00:00.000Z")])
        f.add(1, "a@x.com", action="material_request", ga="1.1", when="2026-11-24T10:00:00.000Z")
        run(f, apply=True)
        self.assertEqual(f.people["p1"]["siteGaClientId"], "9.9")  # conversão mais antiga não troca o client_id
        self.assertEqual(f.people["p1"]["siteUltimaConversao"], "2026-11-30T00:00:00.000Z")
        f.add(2, "a@x.com", action="material_request", ga="2.2", when="2026-12-01T10:00:00.000Z")
        f.add(3, "a@x.com", action="material_request", ga="3.3", when="2026-12-02T10:00:00.000Z")
        f.add(4, "a@x.com", action="material_request", when="2026-12-03T10:00:00.000Z")  # sem client_id: não apaga
        run(f, apply=True)
        self.assertEqual((f.people["p1"]["siteGaClientId"], f.people["p1"]["siteUltimaConversao"]), ("3.3", "2026-12-03T10:00:00.000Z"))
        g = Fake(people=[person("p1", "a@x.com", siteUltimaConversao="2026-11-30T00:00:00.000Z")])
        g.add(1, "a@x.com", action="material_request", ga="1.1", when="2026-11-24T10:00:00.000Z")
        run(g, apply=True)
        self.assertEqual(g.people["p1"]["siteGaClientId"], "1.1")  # sem client_id gravado, qualquer um serve

    def test_field_with_wrong_type_is_refused_before_any_write(self):
        f = Fake(people=[person("p1", "a@x.com")])
        f.person_fields["siteInteresses"] = {"id": "x", "name": "siteInteresses", "type": "TEXT", "options": None}
        f.add(1, "a@x.com")
        with self.assertRaisesRegex(S.SyncError, "siteInteresses existe com outro tipo"):
            run(f, apply=True)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))

    def test_invalid_or_endless_conversion_listing_writes_nothing(self):
        cases = {  # o motivo esperado de cada recusa: um mutante que troca uma guarda pela outra também cai
            "sem lista": (lambda q: (200, {"data": {}, "pageInfo": {"hasNextPage": False}}), "listagem de siteConversions inválida"),
            "sem pageInfo": (lambda q: (200, {"data": {"siteConversions": []}}), "listagem de siteConversions inválida"),
            "cursor repetido": (lambda q: (200, {"data": {"siteConversions": []}, "pageInfo": {"hasNextPage": True, "endCursor": "c"}}),
                                "sem progresso"),
            "sem fim": (lambda q: (200, {"data": {"siteConversions": []},
                                         "pageInfo": {"hasNextPage": True, "endCursor": q.get("starting_after", "") + "x"}}),
                        "paginação sem fim"),
        }
        for why, (listing, reason) in cases.items():
            f = Fake(people=[person("p1", "a@x.com")], schema=True, listing=listing)
            f.add(1, "a@x.com")
            with self.assertRaisesRegex(S.SyncError, reason, msg=why):
                run(f, apply=True)
            self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []), why)

    def test_conversion_listing_is_paginated(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        for i in range(450):
            f.convs[f"c{i:04d}"] = {"id": f"c{i:04d}", "eventoId": hexid(1000 + i), "pessoaId": "p1"}
        f.add(1000 + 449, "a@x.com")  # já no CRM, na terceira página
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"]), (0, 1))
        lists = [q for m, p, d, q in f.calls if m == "GET" and p == "/rest/siteConversions"]
        self.assertEqual(len(lists), 3)
        self.assertEqual(f.pending(), [])

    def test_many_rows_are_marked_in_chunks(self):
        f = Fake(people=[person("p1", "a@x.com")])
        for i in range(120):
            f.add(i + 1, "a@x.com", when=f"2026-11-24T10:{i // 60:02d}:{i % 60:02d}.000Z")
        run(f, apply=True)
        self.assertEqual(f.pending(), [])
        self.assertEqual(len(f.d1_writes()), 3)  # 50 + 50 + 20


class ForgetTest(unittest.TestCase):
    def setup(self):
        f = Fake(people=[person("p1", "a@x.com", extra=["b@x.com"])])
        f.add(1, "a@x.com")
        f.add(2, "a@x.com", action="material_request", when="2026-11-23T10:00:00.000Z")
        f.add(3, "b@x.com", interests=(ARQ,), material="mapa-decisao-ia")
        f.add(4, "c@x.com")
        run(f, apply=True)
        self.assertEqual(len(f.convs), 4)
        return f

    def test_simulation_deletes_nothing(self):
        f = self.setup()
        n = len(f.calls)
        counts, out = forget(f, "A@x.com ")
        self.assertEqual((counts["d1_linhas"], counts["conversoes_no_crm"], counts["pessoas_mantidas"]), (2, 2, 1))
        self.assertEqual([c for c in f.calls[n:] if c[0] != "GET" and not (c[1].endswith("/query") and c[2]["sql"].startswith("SELECT"))
                          and c[1] != "/metadata"], [])
        self.assertEqual(len(f.convs), 4)
        self.assertIn("simulação", out)

    def test_forget_deletes_only_that_emails_conversions_and_keeps_the_person(self):
        f = self.setup()
        before = copy.deepcopy(f.people["p1"])
        counts, out = forget(f, "a@x.com", apply=True)
        self.assertEqual(sorted(conv_by_event(f)), [hexid(3), hexid(4)])  # b@ (mesma Pessoa) e c@ ficam
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 0)
        self.assertEqual(f.people["p1"], before)  # Pessoa e campos site* como estavam
        self.assertNotIn("DELETE", [m for m, p, d, _ in f.calls if p.startswith("/rest/people")])
        # a ordem: D1 antes do Twenty
        seq = [("d1" if p.endswith("/query") else "tw") for m, p, d, _ in f.calls
               if (p.endswith("/query") and d["sql"].startswith("DELETE")) or m == "DELETE"]
        self.assertEqual(seq[0], "d1")
        self.assertIn(FORGET := S.FORGET_MARK, out)
        self.assertTrue(FORGET)
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["conversoes_a_criar"], 0)  # o sync seguinte não recria

    def test_interrupted_forget_is_finished_by_a_rerun(self):
        f = self.setup()
        state = {"armed": True}

        def fail(method, path, data):
            if state["armed"] and method == "DELETE":
                state["armed"] = False
                return 500, {"errors": [{"message": "x"}]}
            return None
        f.fail = fail
        with self.assertRaises(S.SyncError):
            forget(f, "a@x.com", apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 0)
        self.assertEqual(len(f.convs), 4)  # D1 já limpo, Twenty não
        counts, _ = forget(f, "a@x.com", apply=True)
        self.assertEqual(counts["conversoes_no_crm"], 2)
        self.assertEqual(sorted(conv_by_event(f)), [hexid(3), hexid(4)])

    def test_invalid_email_is_refused(self):
        f = self.setup()
        for bad in ("", "sem-arroba", "a@x"):
            with self.assertRaisesRegex(S.SyncError, "email inválido"):
                forget(f, bad, apply=True)
        self.assertEqual(len(f.convs), 4)


class CliTest(unittest.TestCase):
    def cli(self, fake, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in ENV}
        os.environ.update(ENV)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = S.main(list(args), transport=fake, sleep=lambda s: None, stdin=io.StringIO(stdin) if stdin is not None else None)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_apply_through_main_and_output_never_has_emails(self):
        f = Fake()
        f.add(1, "segredo@x.com")
        rc, text = self.cli(f, "--apply")
        self.assertEqual(rc, 0)
        self.assertIn("conversoes_a_criar: 1", text)
        self.assertNotIn("segredo", text)
        self.assertEqual(f.pending(), [])

    def test_without_apply_main_writes_nothing(self):
        f = Fake()
        f.add(1, "a@x.com")
        rc, _ = self.cli(f)
        self.assertEqual((rc, f.twenty_writes(), f.d1_writes()), (0, [], []))

    def test_errors_do_not_echo_emails(self):
        f = Fake(fail=lambda m, p, d: (400, {"errors": [{"message": "erro com segredo@x.com"}]}) if m == "POST" and p == "/rest/people" else None)
        f.add(1, "segredo@x.com")
        rc, text = self.cli(f, "--apply")
        self.assertEqual(rc, 1)
        self.assertNotIn("segredo", text)

        def broken(*a):
            raise KeyError("segredo@x.com")
        rc, text = self.cli(broken)
        self.assertEqual((rc, "segredo" in text), (1, False))

    def test_forget_reads_exactly_one_email_from_stdin(self):
        f = Fake(people=[person("p1", "segredo@x.com")])
        f.add(1, "segredo@x.com")
        self.cli(f, "--apply")
        rc, text = self.cli(f, "--forget", stdin="segredo@x.com\nsegundo@x.com\n")
        self.assertEqual(rc, 1)
        self.assertIn("exatamente um email", text)
        rc, text = self.cli(f, "--forget", stdin="")
        self.assertEqual(rc, 1)
        rc, text = self.cli(f, "--forget", stdin="Segredo@x.com\n")
        self.assertEqual((rc, len(f.convs)), (0, 1))  # sem --apply
        self.assertIn("simulação", text)
        rc, text = self.cli(f, "--forget", "--apply", stdin="Segredo@x.com\n")
        self.assertEqual((rc, f.convs), (0, {}))
        self.assertIn(S.FORGET_MARK, text)
        self.assertNotIn("segredo", text.lower())
        self.assertEqual(f.pending(), [])
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions").fetchone()[0], 0)

    def test_twenty_sync_d1_without_params_keeps_the_old_payload(self):
        seen = []

        def t(method, url, headers, body):
            seen.append(json.loads(body))
            return 200, {"success": True, "result": [{"results": []}]}
        c = TS.Clients(ENV, t)
        c.d1("SELECT 1")
        c.d1("SELECT ?", ["x"])
        self.assertEqual(seen, [{"sql": "SELECT 1"}, {"sql": "SELECT ?", "params": ["x"]}])


if __name__ == "__main__":
    unittest.main()
