"""Testes do sync das conversões do site com o Twenty (issue #97, fase 2). Rodar em blog-src/: python3 scripts/test_conversoes_sync.py

O D1 falso é um SQLite em memória com as migrações reais do painel (0004 em diante), e o Twenty falso guarda estado como o
servidor: devolve cópias, recusa eventoId repetido (campo único, 400 como no Twenty 2.32) e cria o campo inverso da relação.
"""
import ast
import contextlib
import copy
import fcntl
import tempfile
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
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

    def __init__(self, people=(), schema=False, fail=None, listing=None, db_path=":memory:"):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        for name in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n >= "0004"):
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
            verified=None, ga=None, request_id=None, file=None, synced=None, raw_interests=None, origem=None):
        if verified is None:
            verified = 0 if action == "material_request" else 1
        if action == "material_download" and file is None:
            file = material
        o = origem or {}  # colunas da migração 0006 (issue #114), pelo nome da coluna
        self.db.execute("INSERT INTO conversions (id, email, action, material, interests, occurred_at, synced_at, verified, "
                        "ga_client_id, request_id, file, " + ", ".join(S.ORIGEM_COLS) + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (hexid(n) if isinstance(n, int) else n, email, action, material,
                         raw_interests if raw_interests is not None else json.dumps(list(interests)), when, synced, verified,
                         ga, request_id, file, *(o.get(k) for k in S.ORIGEM_COLS)))

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
            # Como o Twenty 2.32 (sonda ao vivo): sem filtro, a lixeira some da listagem; o filtro WITH_TRASH traz tudo.
            if q.get("filter") is None:
                items = [p for p in items if not p.get("deletedAt")]
            elif q["filter"] != S.WITH_TRASH:
                return 400, {"messages": ["filtro inesperado"]}
            if "starting_after" in q:
                items = [p for p in items if p["id"] > q["starting_after"]]
            n = int(q["limit"])
            page = items[:n]
            return 200, {"data": {key: page}, "pageInfo": {"hasNextPage": len(items) > n, "endCursor": page[-1]["id"] if page else None}}
        if u.path == "/rest/people" and method == "POST":
            # emails é campo único no Twenty, inclusive contra a lixeira
            if any((p.get("emails") or {}).get("primaryEmail") == data["emails"]["primaryEmail"] for p in self.people.values()):
                return 400, {"statusCode": 400, "error": "BadRequestException", "messages": ["A duplicate entry was detected"]}
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


def forget(fake, email, apply=False, forget_key=None):
    out = io.StringIO()
    key = FORGET_KEY if forget_key is None else forget_key
    counts = S.forget(TS.Clients(ENV, fake, sleep=lambda s: None), email, apply=apply, out=out, forget_key=key)
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
        self.assertEqual(counts["campos_a_criar"], 4 + 15 + 1)
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
                          "gaClientId": "TEXT", "origemCanal": "SELECT", "origemFonte": "SELECT", "utmSource": "TEXT",
                          "utmMedium": "TEXT", "utmCampaign": "TEXT", "paginaEntrada": "TEXT", "pessoa": "RELATION"})
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
                             "pedidoId": hexid(99), "gaClientId": "123.456", "origemCanal": None, "origemFonte": None,
                             "utmSource": None, "utmMedium": None, "utmCampaign": None, "paginaEntrada": None, "pessoaId": p["id"]})
        self.assertEqual(f.pending(), [])
        self.assertRegex(f.row(1)["synced_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
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
            # r1 (Astra P1, Kimi P2): o email segue o normalizeEmail do Worker e os dígitos do client_id são só ASCII.
            "email com NUL": dict(email="a\x00b@x.com"),
            "email com letra Unicode": dict(email="ä@x.com"),
            "domínio com hífen na ponta": dict(email="a@-x.com"),
            "domínio com rótulo vazio": dict(email="a@x..com"),
            "parte local com 65": dict(email="a" * 65 + "@x.com"),
            "email com mais de 254": dict(email="a@" + ".".join(["b" * 60] * 5) + ".com"),  # 310
            "ga_client_id com dígitos Unicode": dict(action="material_request", ga="\u0661.\u0662"),
            # r1 (Astra P2): id fora do formato.
            "id não hex": dict(n="z" * 64),
            "id hex em maiúsculas": dict(n="A" * 64),
            "id curto": dict(n="a" * 63),
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
            f.add(kw.pop("n", i), email, **kw)
        f.add(1, "ok@x.com", action="newsletter_confirm", material=None, interests=(EXPERT,))
        f.add(2, "a" * 64 + "@" + "b" * 63 + ".com", action="newsletter_confirm", material=None, interests=(EXPERT,))  # no limite: vale
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["linha_invalida"], len(bad))
        self.assertEqual(sorted(conv_by_event(f)), [hexid(1), hexid(2)])
        self.assertEqual(len(f.pending()), len(bad))
        self.assertEqual(sorted(p["emails"]["primaryEmail"][:2] for p in f.people.values()), ["aa", "ok"])
        c = conv_by_event(f)[hexid(1)]
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

    def test_milliseconds_decide_oldest_person_and_newest_client_id(self):
        # r1 (Astra P1, Kimi P1): o Worker grava toISOString(); no mesmo segundo, quem decide é o milissegundo.
        f = Fake(people=[person("p-a", "a@x.com", created="2026-01-01T00:00:00.900Z"),
                         person("p-z", "z@x.com", extra=["a@x.com", "b@x.com"], created="2026-01-01T00:00:00.100Z")])
        f.add("f" * 64, "a@x.com", action="material_request", ga="111.111", when="2026-11-24T10:00:00.100Z")
        f.add("0" * 64, "b@x.com", action="material_request", ga="999.999", when="2026-11-24T10:00:00.900Z")
        run(f, apply=True)
        self.assertEqual({c["pessoaId"] for c in f.convs.values()}, {"p-z"})  # a mais antiga, por 800 ms
        p = f.people["p-z"]
        self.assertEqual((p["siteGaClientId"], p["siteUltimaConversao"]), ("999.999", "2026-11-24T10:00:00.900Z"))
        self.assertEqual(sorted(c["ocorreuEm"] for c in f.convs.values()), ["2026-11-24T10:00:00.100Z", "2026-11-24T10:00:00.900Z"])
        # entre rodadas: uma conversão 1 ms mais antiga que a última conhecida não troca o client_id
        f.add(3, "a@x.com", action="material_request", ga="555.555", when="2026-11-24T10:00:00.899Z")
        run(f, apply=True)
        self.assertEqual((f.people["p-z"]["siteGaClientId"], f.people["p-z"]["siteUltimaConversao"]),
                         ("999.999", "2026-11-24T10:00:00.900Z"))
        self.assertEqual(S.iso_ms("2026-11-11 10:00:00+00"), "2026-11-11T10:00:00.000Z")
        self.assertEqual(S.iso_ms("2026-11-11T07:00:00.250-03:00"), "2026-11-11T10:00:00.250Z")
        self.assertEqual((S.iso_ms(""), S.iso_ms("lixo")), ("", ""))

    def test_synced_at_keeps_milliseconds(self):
        # r2 (Kimi P1): a marca no D1 também tem milissegundos (invariante 10).
        from datetime import datetime, timezone
        f = Fake()
        f.add(1, "a@x.com")
        real = S._now
        S._now = lambda: datetime(2026, 11, 24, 12, 0, 0, 123456, tzinfo=timezone.utc)
        try:
            run(f, apply=True)
        finally:
            S._now = real
        self.assertEqual(f.row(1)["synced_at"], "2026-11-24T12:00:00.123Z")

    def test_date_overflow_is_an_invalid_row_not_a_crash(self):
        # r2 (Astra P2, Kimi P2): um fuso que leva a data para fora do ano 1..9999 é linha inválida; a válida segue.
        for bad in ("9999-12-31T23:59:59.999-01:00", "0001-01-01T00:00:00.000+01:00"):
            f = Fake()
            f.add(1, "a@x.com", when=bad)
            f.add(2, "b@x.com")
            counts, _ = run(f, apply=True)
            self.assertEqual((counts["linha_invalida"], f.pending(), list(conv_by_event(f))), (1, [hexid(1)], [hexid(2)]), bad)
            self.assertEqual(S.iso_ms(bad), "")

    def test_years_below_1000_keep_four_digits_and_order(self):
        # r3 (Astra P1, Kimi P2): ano com quatro algarismos, senão "2-..." > "10-..." > "2026-..." como texto.
        self.assertEqual(S.iso_ms("0001-01-01T00:00:00.001Z"), "0001-01-01T00:00:00.001Z")
        self.assertEqual(S.iso_ms("0999-12-31T23:59:59.999Z"), "0999-12-31T23:59:59.999Z")
        f = Fake(people=[person("p1", "a@x.com", siteGaClientId="9.9", siteUltimaConversao="2026-11-24T10:00:00.000Z")])
        f.add(1, "a@x.com", action="material_request", ga="2.2", when="0002-01-01T00:00:00.002Z")
        f.add(2, "a@x.com", action="material_request", ga="10.10", when="0010-01-01T00:00:00.010Z")
        run(f, apply=True)
        p = f.people["p1"]
        self.assertEqual((p["siteUltimaConversao"], p["siteGaClientId"]), ("2026-11-24T10:00:00.000Z", "9.9"))
        self.assertEqual(sorted(c["ocorreuEm"] for c in f.convs.values()), ["0002-01-01T00:00:00.002Z", "0010-01-01T00:00:00.010Z"])
        g = Fake(people=[person("p1", "a@x.com")])
        g.add(1, "a@x.com", action="material_request", ga="2.2", when="0002-01-01T00:00:00.002Z")
        g.add(2, "a@x.com", action="material_request", ga="10.10", when="0010-01-01T00:00:00.010Z")
        run(g, apply=True)
        self.assertEqual((g.people["p1"]["siteUltimaConversao"], g.people["p1"]["siteGaClientId"]), ("0010-01-01T00:00:00.010Z", "10.10"))

    def test_fresh_crm_with_failing_reads_gets_no_schema(self):
        # r1 (Astra P2): com o esquema por criar, uma falha em qualquer leitura do Twenty não deixa escrita nenhuma.
        def people_broken(method, path, data):
            return (200, {"data": {}}) if method == "GET" and path == "/rest/people" else None

        def meta_broken(method, path, data):
            return (500, {"errors": [{"message": "x"}]}) if path == "/metadata" else None
        for why, fail in (("pessoas", people_broken), ("metadados", meta_broken)):
            f = Fake(fail=fail)
            f.add(1, "a@x.com")
            with self.assertRaises(S.SyncError, msg=why):
                run(f, apply=True)
            self.assertEqual((f.twenty_writes(), f.d1_writes(), f.conv_obj, sorted(f.person_fields)),
                             ([], [], None, ["company", "emails", "name"]), why)
        # r2 (Astra P2): objeto existente com campos por criar e a listagem das Conversões falhando: nada é criado.
        f = Fake(people=[person("p1", "a@x.com")], schema=True,
                 listing=lambda q: (200, {"data": {}, "pageInfo": {"hasNextPage": False}}))
        del f.conv_fields["gaClientId"], f.person_fields["siteGaClientId"]
        f.add(1, "a@x.com", ga="1.2")
        with self.assertRaisesRegex(S.SyncError, "listagem de siteConversions inválida"):
            run(f, apply=True)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertNotIn("gaClientId", f.conv_fields)

    def test_trashed_conversion_is_not_recreated_and_the_row_is_marked(self):
        # shadow review (P1 no d991fa2, 13c90f4 e 17008a6): a Conversão na lixeira some da listagem padrão, mas o eventoId
        # único continua valendo; recriar dá 400 e travaria o sync.
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        f.convs["c-lixo"] = {"id": "c-lixo", "eventoId": hexid(1), "pessoaId": "p1", "deletedAt": "2026-11-24T00:00:00.000Z"}
        f.add(1, "a@x.com")
        f.add(2, "a@x.com", when="2026-11-24T11:00:00.000Z")
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["conversoes_na_lixeira"], counts["conversoes_ja_no_crm"], counts["conversoes_a_criar"]), (1, 1, 1))
        self.assertEqual(f.pending(), [])
        self.assertEqual(sorted(c["eventoId"] for c in f.convs.values()), [hexid(1), hexid(2)])
        self.assertTrue(f.convs["c-lixo"]["deletedAt"])  # continua na lixeira: o sync não restaura

    def test_person_in_trash_holds_only_that_email(self):
        # Pessoa na lixeira: o email principal continua único, então criar outra daria 400 e travaria o lote inteiro.
        # A linha fica pendente e conta em pessoa_na_lixeira; o resto do lote segue.
        f = Fake(people=[person("p1", "a@x.com", deletedAt="2026-11-24T00:00:00.000Z")])
        f.add(1, "a@x.com")
        f.add(2, "b@x.com")
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["pessoa_na_lixeira"], f.pending()), (1, [hexid(1)]))
        self.assertEqual(sorted(p["emails"]["primaryEmail"] for p in f.people.values()), ["a@x.com", "b@x.com"])
        self.assertEqual([c["eventoId"] for c in f.convs.values()], [hexid(2)])
        self.assertNotIn("siteInteresses", f.people["p1"])
        # uma Pessoa ativa com o mesmo email ganha a conversão, mesmo havendo outra na lixeira
        g = Fake(people=[person("p1", "a@x.com", deletedAt="2026-11-24T00:00:00.000Z", created="2025-01-01T00:00:00.000Z"),
                         person("p2", "z@x.com", extra=["a@x.com"], created="2026-01-01T00:00:00.000Z")])
        g.add(1, "a@x.com")
        counts, _ = run(g, apply=True)
        self.assertEqual((counts["pessoa_na_lixeira"], [c["pessoaId"] for c in g.convs.values()]), (0, ["p2"]))

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
        # a ordem (r1): o Twenty pelos ids do D1, a releitura e só então o D1
        seq = [("d1" if p.endswith("/query") else "tw") for m, p, d, _ in f.calls
               if (p.endswith("/query") and d["sql"].startswith("DELETE")) or m == "DELETE"]
        self.assertEqual(seq, ["tw", "tw", "d1"])
        self.assertIn(FORGET := S.FORGET_MARK, out)
        self.assertTrue(FORGET)
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["conversoes_a_criar"], 0)  # o sync seguinte não recria

    def test_twenty_failure_keeps_d1_and_a_rerun_finishes_even_after_the_email_changed(self):
        # r1 (Astra P1): o D1 é o diário da remoção; uma queda no Twenty deixa o D1 intacto, e a nova tentativa acha as
        # Conversões pelos ids, mesmo com o email da Pessoa trocado no CRM.
        f = self.setup()
        f.people["p1"]["emails"] = {"primaryEmail": "novo@x.com", "additionalEmails": ["b@x.com"]}
        state = {"armed": True}

        def fail(method, path, data):
            if state["armed"] and method == "DELETE":
                state["armed"] = False
                return 500, {"errors": [{"message": "x"}]}
            return None
        f.fail = fail
        with self.assertRaises(S.SyncError):
            forget(f, "a@x.com", apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 2)
        self.assertEqual([s for s in f.d1_writes() if s.startswith("DELETE")], [])  # nenhum DELETE no D1
        self.assertEqual(len([s for s in f.d1_writes() if s.startswith("INSERT INTO forgotten")]), 1)  # marcador já gravado (#134)
        counts, out = forget(f, "a@x.com", apply=True)
        self.assertEqual((counts["d1_linhas"], counts["conversoes_no_crm"], counts["pessoas_mantidas"]), (2, 2, 0))
        self.assertEqual(sorted(conv_by_event(f)), [hexid(3), hexid(4)])
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 0)
        self.assertIn(S.FORGET_MARK, out)

    def test_leftover_conversion_in_twenty_keeps_the_d1(self):
        # Um DELETE que responde 200 sem apagar: a releitura acha a sobra, o D1 fica e nada é dado como concluído.
        f = self.setup()
        real = f._twenty

        def no_op_delete(method, u, data):
            if method == "DELETE":
                return 200, {"data": {}}
            return real(method, u, data)
        f._twenty = no_op_delete
        with self.assertRaisesRegex(S.SyncError, "o D1 ficou intacto"):
            forget(f, "a@x.com", apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 2)

    def test_forget_removes_conversions_in_the_trash_for_good(self):
        # shadow review: a Conversão que alguém mandou para a lixeira também sai, de vez, antes do D1.
        f = self.setup()
        trashed = next(c for c in f.convs.values() if c["eventoId"] == hexid(1))
        trashed["deletedAt"] = "2026-11-24T00:00:00.000Z"
        counts, _ = forget(f, "a@x.com", apply=True)
        self.assertEqual(counts["conversoes_no_crm"], 2)
        self.assertEqual(sorted(conv_by_event(f)), [hexid(3), hexid(4)])
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 0)

    def test_invalid_email_is_refused(self):
        f = self.setup()
        for bad in ("", "sem-arroba", "a@x"):
            with self.assertRaisesRegex(S.SyncError, "email inválido"):
                forget(f, bad, apply=True)
        self.assertEqual(len(f.convs), 4)


# ---------------------------------------------------------------- Origem da visita (issue #114)

ORIGEM = {"origin_channel": "assistente", "origin_source": "chatgpt", "utm_source": "chatgpt.com", "utm_medium": None,
          "utm_campaign": "lancamento", "landing_path": "/ferramentas/mapa-decisao-ia/"}
NEWSLETTER = os.path.join(HERE, "..", "..", "workers", "newsletter")
ORIGEM_TWENTY = ("origemCanal", "origemFonte", "utmSource", "utmMedium", "utmCampaign", "paginaEntrada")


def ponta_a_ponta() -> dict:
    """Roda o roteiro do Worker (workers/newsletter/tests/ponta-a-ponta.ts): track.js, formulário, Worker e D1 de verdade."""
    node = shutil.which("node")
    if not node:
        raise AssertionError("o teste ponta a ponta precisa do node (o mesmo dos testes do Worker)")
    r = subprocess.run([node, "--no-warnings", "tests/ponta-a-ponta.ts"], cwd=NEWSLETTER, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"ponta-a-ponta.ts falhou: {r.stderr[-2000:]}")
    return json.loads(r.stdout)


class OrigemTest(unittest.TestCase):
    def test_origin_goes_to_the_conversion_and_never_to_the_person(self):
        f = Fake(people=[person("p1", "a@x.com")])
        f.add(1, "a@x.com", origem=ORIGEM)
        f.add(2, "nova@x.com", action="newsletter_confirm", material=None, interests=(EXPERT,),
              origem={"origin_channel": "direto"})
        run(f, apply=True)
        ev = conv_by_event(f)
        self.assertEqual({k: ev[hexid(1)][k] for k in ORIGEM_TWENTY},
                         {"origemCanal": "ASSISTENTE", "origemFonte": "CHATGPT", "utmSource": "chatgpt.com", "utmMedium": None,
                          "utmCampaign": "lancamento", "paginaEntrada": "/ferramentas/mapa-decisao-ia/"})
        self.assertEqual({k: ev[hexid(2)][k] for k in ORIGEM_TWENTY},
                         {"origemCanal": "DIRETO", "origemFonte": None, "utmSource": None, "utmMedium": None, "utmCampaign": None,
                          "paginaEntrada": None})
        # Na Pessoa, só os campos site* de antes: nenhuma origem (decisão do Gui na #114).
        for m, path, d, _ in f.calls:
            if path.startswith("/rest/people") and m in ("POST", "PATCH"):
                self.assertLessEqual(set(d) - {"emails"}, set(S.PERSON_FIELDS), path)
        self.assertNotIn("chatgpt", json.dumps(list(f.people.values())))
        opts = lambda n: [(o["value"], o["label"]) for o in f.conv_fields[n]["options"]]  # noqa: E731
        self.assertEqual([v for v, _ in opts("origemCanal")], [v for v, _ in S.CANAIS.values()])
        self.assertEqual(opts("origemFonte")[-1], ("OUTRO", "Outro"))

    def test_lists_match_the_worker_and_the_migration(self):
        # As listas são as mesmas nos quatro lugares (origem.ts, track.js, migração 0006 e aqui): conferidas pelo texto.
        with open(os.path.join(NEWSLETTER, "src", "origem.ts"), encoding="utf-8") as fh:
            ts = fh.read()
        with open(os.path.join(MIGRATIONS, "0006_conversions_origem.sql"), encoding="utf-8") as fh:
            sql = fh.read()
        with open(os.path.join(HERE, "..", "..", "assets", "track.js"), encoding="utf-8") as fh:
            js = fh.read()
        lst = lambda keys: ", ".join(f"'{k}'" for k in keys)  # noqa: E731
        self.assertIn(f"CANAIS = [{lst(S.CANAIS)}]", " ".join(ts.split()))
        self.assertIn(f"FONTES = [{lst(S.FONTES)}]", " ".join(ts.replace("[\n", "[").split()).replace("[ ", "["))
        self.assertIn(f"IN ({lst(S.CANAIS)})", " ".join(sql.split()))
        self.assertIn(f"IN ({lst(S.FONTES)})", " ".join(sql.split()).replace("( ", "("))
        self.assertIn(f"var CANAIS = [{lst(S.CANAIS)}]", js)
        for fonte in S.FONTES:
            self.assertIn(f"{fonte}: ", js.split("var FONTE_CANAL")[1].split("};")[0])

    def test_row_before_114_syncs_with_empty_origin(self):
        f = Fake()
        f.add(1, "a@x.com")  # sem origem: linha gravada antes da migração 0006
        counts, _ = run(f, apply=True)
        self.assertEqual(counts["linha_invalida"], 0)
        (c,) = f.convs.values()
        self.assertTrue(all(c[k] is None for k in ORIGEM_TWENTY))

    def test_malformed_origin_is_an_invalid_row_not_written(self):
        # O CHECK da 0006 já barra lista e padrão no D1; estas combinações ele aceita, e o sync recusa.
        f = Fake()
        f.add(1, "a@x.com", origem={"origin_source": "google"})  # fonte sem canal
        f.add(2, "a@x.com", origem={"origin_channel": "direto", "origin_source": "google"})  # direto não tem fonte
        f.add(3, "a@x.com", origem={"utm_source": "google"})  # UTM sem canal
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["linha_invalida"], f.convs, len(f.pending())), (3, {}, 3))
        # E as que o CHECK barra, se algum dia chegarem por outro caminho:
        base = {k: None for k in S.ORIGEM_COLS}
        for bad in ({"origin_channel": "buscador"}, {"origin_channel": "busca", "origin_source": "altavista"},
                    {"origin_channel": "busca", "utm_campaign": "https://google.com/search?q=termo"},
                    {"origin_channel": "busca", "utm_source": "Google"}, {"origin_channel": "busca", "utm_medium": "x" * 65},
                    {"origin_channel": "busca", "landing_path": "/blog/?q=termo"},
                    {"origin_channel": "busca", "landing_path": "https://guifav.dev/"},
                    {"origin_channel": "busca", "landing_path": "//evil.example"},
                    {"origin_channel": "busca", "landing_path": "/" + "a" * 200}):
            self.assertIsNone(S.parse_origem({**base, **bad}), bad)
        self.assertEqual(S.parse_origem({**base, "origin_channel": "busca", "landing_path": "/" + "a" * 199})["landing_path"],
                         "/" + "a" * 199)

    def test_d1_check_refuses_full_url_and_search_term(self):
        f = Fake()
        for bad in ({"landing_path": "/blog/?q=termo"}, {"utm_campaign": "https://google.com/search?q=termo"},
                    {"origin_channel": "x"}, {"origin_source": "altavista"}, {"utm_source": "A B"}):
            with self.assertRaises(sqlite3.IntegrityError, msg=str(bad)):
                f.add(9, "a@x.com", origem={"origin_channel": "busca", **bad})

    def test_crm_from_before_114_gets_only_the_origin_fields(self):
        f = Fake(schema=True)
        for k in ORIGEM_TWENTY:
            del f.conv_fields[k]
        f.add(1, "a@x.com", origem=ORIGEM)
        counts, _ = run(f, apply=True)
        created = [d["variables"]["input"]["field"]["name"] for m, p, d, _ in f.calls
                   if p == "/metadata" and "createOneField" in d["query"]]
        self.assertEqual(created, list(ORIGEM_TWENTY))
        self.assertEqual(counts["campos_a_criar"], 6)
        self.assertEqual(conv_by_event(f)[hexid(1)]["origemFonte"], "CHATGPT")

    def test_select_without_an_option_is_refused_before_any_write(self):
        f = Fake(schema=True)
        f.conv_fields["origemCanal"]["options"] = [o for o in f.conv_fields["origemCanal"]["options"] if o["value"] != "PAGO"]
        f.add(1, "a@x.com", origem=ORIGEM)
        with self.assertRaisesRegex(S.SyncError, "origemCanal existe sem as opções PAGO"):
            run(f, apply=True)
        self.assertEqual((f.twenty_writes(), f.d1_writes(), f.convs), ([], [], {}))

    def test_forget_removes_the_origin_from_d1_and_twenty(self):
        f = Fake(people=[person("p1", "a@x.com")])
        f.add(1, "a@x.com", origem=ORIGEM)
        f.add(2, "a@x.com", action="material_request", origem=ORIGEM)
        f.add(3, "b@x.com", origem={"origin_channel": "busca", "origin_source": "google", "landing_path": "/blog/"})
        run(f, apply=True)
        self.assertEqual(len(f.convs), 3)
        forget(f, "a@x.com", apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = 'a@x.com'").fetchone()[0], 0)
        state = json.dumps([list(f.convs.values()), list(f.people.values()),
                            [dict(r) for r in f.db.execute("SELECT * FROM conversions")]])
        for v in ("chatgpt", "CHATGPT", "lancamento", "/ferramentas/mapa-decisao-ia/", "ASSISTENTE"):
            self.assertNotIn(v, state, f"a origem do email apagado sobrou: {v}")
        self.assertEqual([c["origemFonte"] for c in f.convs.values()], ["GOOGLE"])  # a de outro email fica

    def test_end_to_end_signup_and_material_request_record_only_the_agreed_fields(self):
        e2e = ponta_a_ponta()
        linhas = e2e["linhas"]
        f = Fake(people=[person("p1", "leitora@exemplo.com")])
        cols = list(linhas[0])
        for ln in linhas:
            f.db.execute(f"INSERT INTO conversions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [ln[k] for k in cols])
        counts, out = run(f, apply=True)
        self.assertEqual((counts["linha_invalida"], counts["conversoes_a_criar"], f.pending()), (0, 5, []))
        got = sorted((c["acao"], *(c[k] for k in ORIGEM_TWENTY)) for c in f.convs.values())
        self.assertEqual(got, sorted([
            ("NEWSLETTER_CONFIRM", "PAGO", "GOOGLE", None, None, None, "/blog/rag-em-producao/"),
            ("MATERIAL_REQUEST", "ASSISTENTE", "CHATGPT", "chatgpt.com", None, None, "/ferramentas/mapa-decisao-ia/"),
            ("MATERIAL_DOWNLOAD", "ASSISTENTE", "CHATGPT", "chatgpt.com", None, None, "/ferramentas/mapa-decisao-ia/"),
            ("MATERIAL_DOWNLOAD", "ASSISTENTE", "CHATGPT", "chatgpt.com", None, None, "/ferramentas/mapa-decisao-ia/"),
            ("NEWSLETTER_CONFIRM", "ASSISTENTE", "CHATGPT", "chatgpt.com", None, None, "/ferramentas/mapa-decisao-ia/"),
        ]))
        # Nada além dos campos combinados: as chaves de cada Conversão são as de antes mais as seis da origem.
        antes = {"name", "acao", "comprovado", "material", "arquivo", "interesses", "ocorreuEm", "eventoId", "pedidoId", "gaClientId",
                 "pessoaId"}
        for c in f.convs.values():
            self.assertEqual(set(c) - {"id"}, antes | set(ORIGEM_TWENTY))
        crm = json.dumps([list(f.convs.values()), list(f.people.values())])
        for p in ("termo", "secreto", "GCLIDSECRETO", "search", "http", "?", "#", "=", "203.0.113", "Mozilla"):
            self.assertNotIn(p, crm, f"o Twenty não guarda {p}")
        self.assertNotIn("@", out)
        # E o --forget de cada email apaga a origem nos dois lugares.
        for email in ("inscrita@exemplo.com", "leitora@exemplo.com"):
            forget(f, email, apply=True)
        self.assertEqual((f.convs, f.db.execute("SELECT count(*) FROM conversions").fetchone()[0]), ({}, 0))
        self.assertNotIn("CHATGPT", json.dumps(list(f.people.values())))


class CliTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="conversoes-lock-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def cli(self, fake, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in [*ENV, "CONVERSOES_SYNC_LOCK", "FORGET_HMAC_KEY"]}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        if "--forget" in args:
            os.environ["FORGET_HMAC_KEY"] = FORGET_KEY
        else:
            os.environ.pop("FORGET_HMAC_KEY", None)
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

    def test_sync_and_forget_never_run_together(self):
        # r1: o lock compartilhado impede o sync de criar uma Conversão entre a limpeza do Twenty e a do D1.
        f = Fake(people=[person("p1", "a@x.com")])
        f.add(1, "a@x.com")
        with open(self.lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for args, stdin in ((("--apply",), None), (("--forget", "--apply"), "a@x.com\n")):
                rc, text = self.cli(f, *args, stdin=stdin)
                self.assertEqual(rc, 1)
                self.assertIn("em andamento", text)
            self.assertEqual(f.calls, [])
        rc, _ = self.cli(f, "--apply")  # lock solto: roda
        self.assertEqual((rc, f.pending()), (0, []))
        rc, _ = self.cli(f, "--apply")  # e solta o lock no fim de cada execução
        self.assertEqual(rc, 0)

    def test_lock_is_held_until_the_last_write(self):
        # r2 (Astra P2): o lock vale do começo ao fim, inclusive na última escrita de cada modo (a marca no D1 do sync e
        # o DELETE no D1 do --forget, a janela em que um sync concorrente deixaria Conversão órfã).
        seen = []

        def probe(method, path, data):
            if path.endswith("/query") and data["sql"].split()[0] in ("UPDATE", "DELETE"):
                with open(self.lock, "a") as other:
                    try:
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        seen.append((data["sql"].split()[0], "livre"))
                    except BlockingIOError:
                        seen.append((data["sql"].split()[0], "preso"))
            return None
        f = Fake(people=[person("p1", "a@x.com")], fail=probe)
        f.add(1, "a@x.com")
        self.assertEqual(self.cli(f, "--apply")[0], 0)
        self.assertEqual(self.cli(f, "--forget", "--apply", stdin="a@x.com\n")[0], 0)
        self.assertEqual(seen, [("UPDATE", "preso"), ("DELETE", "preso")])

    def test_twenty_sync_d1_without_params_keeps_the_old_payload(self):
        seen = []

        def t(method, url, headers, body):
            seen.append(json.loads(body))
            return 200, {"success": True, "result": [{"results": []}]}
        c = TS.Clients(ENV, t)
        c.d1("SELECT 1")
        c.d1("SELECT ?", ["x"])
        self.assertEqual(seen, [{"sql": "SELECT 1"}, {"sql": "SELECT ?", "params": ["x"]}])


# ---------------------------------------------------------------- Marcador de remoção (issue #134)

FORGET_KEY = "chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789"
VETOR_EMAIL = "leitor@exemplo.com"
# Mesmo vetor fixo de workers/newsletter/tests/forget.test.ts: prova que TS e Python dão o mesmo hex.
VETOR_HMAC = "3d6844d8f0921e015a40170ae9c2fbafa051a7abe38dc79f9c29e498c4281b3e"


def ponta_a_ponta_cmd(cmd: str, db_path: str, now_ms: int, payload=None) -> dict:
    node = shutil.which("node")
    if not node:
        raise AssertionError("o teste ponta a ponta precisa do node")
    r = subprocess.run(
        [node, "--no-warnings", "tests/ponta-a-ponta.ts", cmd, db_path, str(now_ms)],
        cwd=NEWSLETTER,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise AssertionError(f"ponta-a-ponta.ts {cmd} falhou: {r.stderr[-2000:]}")
    return json.loads(r.stdout)


class ForgetMarcadorTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="conversoes-lock-forget-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def _run_cli(self, fake, *args, stdin=None, forget_key=...):
        out, err = io.StringIO(), io.StringIO()
        keys = [*ENV, "CONVERSOES_SYNC_LOCK", "FORGET_HMAC_KEY"]
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        if forget_key is None:
            os.environ.pop("FORGET_HMAC_KEY", None)
        elif forget_key is not ...:
            os.environ["FORGET_HMAC_KEY"] = forget_key
        else:
            os.environ.pop("FORGET_HMAC_KEY", None)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = S.main(list(args), transport=fake, sleep=lambda s: None, stdin=io.StringIO(stdin) if stdin is not None else None)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_forget_hmac_matches_fixed_vector_and_validates_inputs(self):
        self.assertEqual(S.forget_hmac(VETOR_EMAIL, FORGET_KEY), VETOR_HMAC)
        self.assertEqual(S.forget_hmac("  Leitor@Exemplo.COM ", FORGET_KEY), VETOR_HMAC)
        with self.assertRaises(S.SyncError):
            S.forget_hmac(VETOR_EMAIL, "")
        with self.assertRaises(S.SyncError):
            S.forget_hmac(VETOR_EMAIL, "x" * 31)
        with self.assertRaises(S.SyncError):
            S.forget_hmac("sem-arroba", FORGET_KEY)

    def test_forget_retention_covers_longest_link_ttl_plus_one_day(self):
        # Issue #134: FORGET_TTL_DAYS precisa cobrir o maior TTL de workers/newsletter/src/token.ts mais 1 dia de folga.
        with open(os.path.join(NEWSLETTER, "src", "token.ts"), encoding="utf-8") as fh:
            ts = fh.read()

        def eval_ms(name: str) -> float:
            m = re.search(rf"\bexport\s+const\s+{name}\s*=\s*([^;]+);", ts)
            self.assertIsNotNone(m, f"{name} não encontrada em token.ts")
            node = ast.parse(m.group(1).strip(), mode="eval").body

            def walk(n):
                if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                    return float(n.value)
                if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Add, ast.Sub, ast.Div, ast.FloorDiv)):
                    a, b = walk(n.left), walk(n.right)
                    return {ast.Mult: a * b, ast.Add: a + b, ast.Sub: a - b, ast.Div: a / b, ast.FloorDiv: a // b}[type(n.op)]
                raise AssertionError(f"expressão inesperada em {name}: {m.group(1)!r}")

            val = walk(node)
            self.assertGreater(val, 0, name)
            return val

        ttl_ms = eval_ms("TTL_MS")
        material_ttl_ms = eval_ms("MATERIAL_TTL_MS")
        day_ms = 24 * 3600 * 1000
        retention_ms = S.FORGET_TTL_DAYS * day_ms
        self.assertGreaterEqual(
            retention_ms,
            max(ttl_ms, material_ttl_ms) + day_ms,
            f"FORGET_TTL_DAYS ({S.FORGET_TTL_DAYS}d) precisa cobrir max(TTL_MS, MATERIAL_TTL_MS) + 1 dia",
        )

    def test_forget_apply_writes_marker_before_any_read_with_d1_clock_and_upserts_latest(self):
        from datetime import datetime, timezone
        iso_re = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$"
        f = Fake(people=[person("p1", VETOR_EMAIL)])
        f.add(1, VETOR_EMAIL)
        f.add(2, VETOR_EMAIL, action="material_request", when="2026-11-23T10:00:00.000Z")
        run(f, apply=True)
        start_calls = len(f.calls)
        # Relógio da KVM propositalmente deslocado: forgotten_at precisa vir do relógio do D1 (#134).
        real_now = S._now
        S._now = lambda: datetime(2000, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        try:
            rc, out = self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
        finally:
            S._now = real_now
        self.assertEqual(rc, 0, out)
        self.assertNotIn(VETOR_EMAIL, out.lower())
        new_calls = f.calls[start_calls:]
        # Prova da ordem (#134): o INSERT INTO forgotten é a primeira chamada, antes de qualquer leitura do D1 ou do Twenty.
        first_m, first_p, first_d, _ = new_calls[0]
        self.assertTrue(first_p.endswith("/query"), new_calls[0])
        self.assertTrue(first_d["sql"].lstrip().upper().startswith("INSERT INTO FORGOTTEN"), first_d["sql"])
        self.assertNotIn(VETOR_EMAIL, json.dumps(first_d).lower())
        self.assertEqual(first_d["params"], [VETOR_HMAC])
        self.assertIn("strftime('%Y-%m-%dT%H:%M:%fZ','now')", first_d["sql"])
        ops = []
        for m, p, d, _ in new_calls:
            if p.endswith("/query"):
                sql = d["sql"].lstrip().upper()
                if sql.startswith("INSERT INTO FORGOTTEN"):
                    ops.append("mark")
                elif sql.startswith("DELETE FROM CONVERSIONS"):
                    ops.append("del_d1")
            elif m == "DELETE":
                ops.append("del_tw")
        self.assertEqual(ops, ["mark", "del_tw", "del_tw", "del_d1"])
        marks = [dict(r) for r in f.db.execute("SELECT * FROM forgotten")]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["email_hmac"], VETOR_HMAC)
        self.assertRegex(marks[0]["forgotten_at"], iso_re)
        self.assertFalse(marks[0]["forgotten_at"].startswith("2000-"), marks[0]["forgotten_at"])
        # Upsert mantém a data mais recente: marcador futuro não retrocede; marcador antigo sobe para o relógio do D1.
        f.db.execute("UPDATE forgotten SET forgotten_at = ? WHERE email_hmac = ?", ("2099-01-01T00:00:00.000Z", VETOR_HMAC))
        f.db.commit()
        self.assertEqual(self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)[0], 0)
        self.assertEqual(f.db.execute("SELECT forgotten_at FROM forgotten WHERE email_hmac = ?", (VETOR_HMAC,)).fetchone()[0],
                         "2099-01-01T00:00:00.000Z")
        f.db.execute("UPDATE forgotten SET forgotten_at = ? WHERE email_hmac = ?", ("2020-01-01T00:00:00.000Z", VETOR_HMAC))
        f.db.commit()
        self.assertEqual(self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)[0], 0)
        atualizado = f.db.execute("SELECT forgotten_at FROM forgotten WHERE email_hmac = ?", (VETOR_HMAC,)).fetchone()[0]
        self.assertRegex(atualizado, iso_re)
        self.assertGreater(atualizado, "2020-01-01T00:00:00.000Z")

    def test_forget_apply_keeps_marker_when_twenty_read_fails(self):
        # Issue #134: se a leitura do Twenty falhar, o marcador já está gravado e `conversions` fica intacta.
        f = Fake(people=[person("p1", VETOR_EMAIL)])
        f.add(1, VETOR_EMAIL)
        run(f, apply=True)
        start_calls = len(f.calls)
        f.fail = lambda m, p, d: (500, {"errors": [{"message": "twenty indisponível"}]}) if p == "/metadata" else None
        with self.assertRaises(S.SyncError):
            forget(f, VETOR_EMAIL, apply=True)
        new_calls = f.calls[start_calls:]
        self.assertTrue(new_calls[0][1].endswith("/query"))
        self.assertTrue(new_calls[0][2]["sql"].lstrip().upper().startswith("INSERT INTO FORGOTTEN"))
        marks = [dict(r) for r in f.db.execute("SELECT * FROM forgotten")]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["email_hmac"], VETOR_HMAC)
        self.assertRegex(marks[0]["forgotten_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = ?", (VETOR_EMAIL,)).fetchone()[0], 1)
        self.assertEqual(len(f.convs), 1)

    def test_expired_markers_over_8_days_are_deleted_on_apply_and_only_counted_without_apply(self):
        from datetime import datetime, timezone
        # Relógio da KVM deslocado para provar que o corte de 8 dias vem do relógio do D1 (#134).
        real_now = S._now
        S._now = lambda: datetime(2000, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        try:
            # 1) Sync normal (sem conversões pendentes): sem --apply só conta; com --apply apaga só os > 8 dias.
            f = Fake()
            for n, mod in ((1, "'-8 days','-1 minute'"), (2, "'-9 days'"),
                           (3, "'-8 days','+1 minute'"), (4, "'-1 day'")):
                f.db.execute(f"INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now',{mod}))", (hexid(n),))
            f.db.commit()
            counts, out = run(f, apply=False)
            self.assertEqual(counts["marcadores_vencidos"], 2)
            self.assertIn("marcadores_vencidos: 2", out)
            self.assertEqual(f.db.execute("SELECT count(*) FROM forgotten").fetchone()[0], 4)
            counts, out = run(f, apply=True)
            self.assertEqual(counts["marcadores_vencidos"], 2)
            self.assertIn("marcadores_vencidos: 2", out)
            self.assertEqual(sorted(r[0] for r in f.db.execute("SELECT email_hmac FROM forgotten")), [hexid(3), hexid(4)])

            # 2) --forget: sem --apply só conta e não grava nem apaga; com --apply grava o novo e apaga os vencidos.
            g = Fake()
            for n, mod in ((1, "'-8 days','-1 minute'"), (2, "'-8 days','+1 minute'")):
                g.db.execute(f"INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now',{mod}))", (hexid(n),))
            g.db.commit()
            rc, out = self._run_cli(g, "--forget", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
            self.assertEqual(rc, 0, out)
            self.assertIn("marcadores_vencidos: 1", out)
            self.assertEqual(sorted(r[0] for r in g.db.execute("SELECT email_hmac FROM forgotten")), [hexid(1), hexid(2)])
            rc, out = self._run_cli(g, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
            self.assertEqual(rc, 0, out)
            self.assertIn("marcadores_vencidos: 1", out)
            self.assertEqual(sorted(r[0] for r in g.db.execute("SELECT email_hmac FROM forgotten")), sorted([hexid(2), VETOR_HMAC]))
        finally:
            S._now = real_now

    def test_sync_prunes_expired_markers_even_when_twenty_is_down(self):
        # Issue #134: em run() com --apply, a limpeza dos vencidos acontece antes de ler o Twenty.
        f = Fake(fail=lambda m, p, d: (500, {"errors": [{"message": "twenty fora do ar"}]}) if p == "/metadata" else None)
        f.add(1, "a@x.com")  # pendente, força run() a ir até o Twenty
        f.db.execute("INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now','-8 days','-1 minute'))", (hexid(1),))
        f.db.execute("INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 day'))", (hexid(2),))
        f.db.commit()
        with self.assertRaises(S.SyncError):
            run(f, apply=False)
        self.assertEqual(sorted(r[0] for r in f.db.execute("SELECT email_hmac FROM forgotten")), [hexid(1), hexid(2)])
        with self.assertRaises(S.SyncError):
            run(f, apply=True)
        self.assertEqual(sorted(r[0] for r in f.db.execute("SELECT email_hmac FROM forgotten")), [hexid(2)])
        self.assertEqual(f.pending(), [hexid(1)])
        self.assertEqual(f.twenty_writes(), [])

    def test_forget_without_or_short_key_fails_without_writing_while_sync_works_without_key(self):
        f = Fake(people=[person("p1", VETOR_EMAIL)])
        f.add(1, VETOR_EMAIL)
        # Sync normal funciona sem FORGET_HMAC_KEY.
        rc, out = self._run_cli(f, "--apply", forget_key=None)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(f.convs), 1)
        # --forget sem chave ou com chave curta falha antes de qualquer leitura ou escrita.
        for bad_key in (None, "", "segredo-curto-com-31-caracteres"):
            n_calls = len(f.calls)
            rc, out = self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=bad_key)
            self.assertNotEqual(rc, 0, f"deveria falhar com chave={bad_key!r}")
            self.assertEqual(len(f.calls), n_calls, "não pode ler nem escrever sem FORGET_HMAC_KEY válida")
            if bad_key:
                self.assertNotIn(bad_key, out)
        self.assertEqual(len(f.convs), 1)

    def test_end_to_end_24_11_scenario_forget_blocks_old_download_and_confirm_links(self):
        # Cenário real de 24/11 (issue #134): pedido com a caixa dos Expert Insights, --forget --apply (gravando
        # forgotten_at pelo relógio do próprio D1), e depois clique nos links antigos de download e de confirmação no
        # Worker real, sobre o mesmo SQLite com as migrações reais.
        fd, db_path = tempfile.mkstemp(prefix="conversoes-e2e-134-", suffix=".sqlite")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(db_path) and os.unlink(db_path))
        f = Fake(people=[person("p1", VETOR_EMAIL)], db_path=db_path)
        now_ms = int(time.time() * 1000) - 60_000  # pedido 1 minuto antes do relógio atual do D1

        # 1) Pedido real no Worker com a caixa dos Expert Insights.
        pedido = ponta_a_ponta_cmd("pedido", db_path, now_ms)
        dl_links = [u for u in pedido["links"] if "/material/download?" in u]
        conf_links = [u for u in pedido["links"] if "/confirm?" in u]
        self.assertEqual((len(dl_links), len(conf_links)), (2, 1))
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = ?", (VETOR_EMAIL,)).fetchone()[0], 1)

        # 2) Sync leva o pedido ao Twenty.
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["conversoes_a_criar"], len(f.convs)), (1, 1))

        # 3) --forget --apply 1 minuto depois do pedido: limpa Twenty e D1 e grava marcador pelo relógio do D1.
        rc, out = self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
        self.assertEqual(rc, 0, out)
        self.assertEqual((len(f.convs), f.db.execute("SELECT count(*) FROM conversions").fetchone()[0]), (0, 0))
        forgotten_at = f.db.execute("SELECT forgotten_at FROM forgotten WHERE email_hmac = ?", (VETOR_HMAC,)).fetchone()[0]
        self.assertRegex(forgotten_at, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

        # 4) Cliques nos links antigos (downloads e confirmação) 2 minutos depois do pedido, no Worker real.
        cliques = ponta_a_ponta_cmd("clicar", db_path, now_ms + 120_000, {"links": pedido["links"]})
        for r in cliques["respostas"]:
            self.assertEqual(r["status"], 303, r)
            if "/material/download?" in r["url"]:
                self.assertEqual(r["location"], "https://guifav.dev/ferramentas/mapa-decisao-ia/#link-expirado", r)
            else:
                self.assertEqual(r["location"], "https://guifav.dev/blog/inscricao-invalida/", r)
        self.assertEqual(cliques["resendContacts"], [])
        self.assertEqual(cliques["ga4"], [])
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions").fetchone()[0], 0)

        # 5) Sync seguinte não encontra nem leva nada ao Twenty.
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["pendentes_lidas"], len(f.convs)), (0, 0))


if __name__ == "__main__":
    unittest.main()
