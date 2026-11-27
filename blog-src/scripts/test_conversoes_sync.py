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
# Esquema da migração 0013 (`site_identities`, do PR #203): criado apenas enquanto 0013_*.sql não estiver em MIGRATIONS.
# Após o merge do #203, os testes passam a usar a migração real automaticamente.
SITE_IDENTITIES_0013_SQL = (
    "CREATE TABLE IF NOT EXISTS site_identities ("
    "uid TEXT PRIMARY KEY NOT NULL CHECK (length(uid) BETWEEN 1 AND 128 AND uid NOT GLOB '*[^0-9A-Za-z]*'), "
    "email TEXT NOT NULL CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)), "
    "linked_at TEXT NOT NULL CHECK (linked_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')); "
    "CREATE INDEX IF NOT EXISTS site_identities_email ON site_identities (email);"
)
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
        mig_files = sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n >= "0004")
        if not any(n.startswith("0013_") for n in mig_files):
            self.db.executescript(SITE_IDENTITIES_0013_SQL)
        for name in mig_files:
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
            verified=None, ga=None, request_id=None, file=None, synced=None, raw_interests=None, origem=None, twenty_person_id=None):
        if verified is None:
            verified = 0 if action == "material_request" else 1
        if action == "material_download" and file is None:
            file = material
        o = origem or {}  # colunas da migração 0006 (issue #114), pelo nome da coluna
        self.db.execute("INSERT INTO conversions (id, email, action, material, interests, occurred_at, synced_at, verified, "
                        "ga_client_id, request_id, file, " + ", ".join(S.ORIGEM_COLS) + ", twenty_person_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (hexid(n) if isinstance(n, int) else n, email, action, material,
                         raw_interests if raw_interests is not None else json.dumps(list(interests)), when, synced, verified,
                         ga, request_id, file, *(o.get(k) for k in S.ORIGEM_COLS), twenty_person_id))

    def __del__(self):
        self.db_close()

    def row(self, n):
        r = self.db.execute("SELECT * FROM conversions WHERE id = ?", (hexid(n),)).fetchone()
        return dict(r) if r else None

    def pending(self):
        return sorted(r[0] for r in self.db.execute("SELECT id FROM conversions WHERE synced_at IS NULL"))

    def add_identity(self, uid: str, email: str, linked_at: str = "2026-11-27T10:00:00.000Z"):
        self.db.execute(
            "INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)",
            (uid, email.strip().lower(), linked_at),
        )
        self.db.commit()

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
        # a ordem (r1): o Twenty pelos ids do D1, a releitura e só então o D1 (conversions, engagement_events e heating de site)
        seq = [("d1" if p.endswith("/query") else "tw") for m, p, d, _ in f.calls
               if (p.endswith("/query") and d["sql"].startswith("DELETE")) or m == "DELETE"]
        self.assertEqual(seq, ["tw", "tw", "d1", "d1", "d1"])
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
        self.assertEqual(seen, [("UPDATE", "preso"), ("DELETE", "preso"), ("DELETE", "preso"), ("DELETE", "preso")])

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

    def test_forget_apply_records_permanent_heating_optout_before_reads_and_deletes(self):
        # Decisão do Gui (2026-11-27): --forget grava também a oposição permanente ao heating (`heating_optout`,
        # HMAC `heating-optout-v1\n` + email com FORGET_HMAC_KEY, mesmo upsert do Worker) antes de ler ou apagar o resto.
        import hashlib
        import hmac
        from datetime import datetime, timezone

        iso_re = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$"
        expected_optout_hmac = hmac.new(
            FORGET_KEY.encode("utf-8"),
            f"heating-optout-v1\n{VETOR_EMAIL}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(S.heating_optout_hmac(VETOR_EMAIL, FORGET_KEY), expected_optout_hmac)
        self.assertEqual(S.heating_optout_hmac("  Leitor@Exemplo.COM ", FORGET_KEY), expected_optout_hmac)
        self.assertNotEqual(expected_optout_hmac, VETOR_HMAC)
        with self.assertRaises(S.SyncError):
            S.heating_optout_hmac(VETOR_EMAIL, "x" * 31)
        with self.assertRaises(S.SyncError):
            S.heating_optout_hmac("invalido", FORGET_KEY)

        f = Fake(people=[person("p1", VETOR_EMAIL)])
        f.add(1, VETOR_EMAIL)
        run(f, apply=True)

        # 1) Simulação (--forget sem --apply) não grava heating_optout.
        rc_dry, out_dry = self._run_cli(f, "--forget", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
        self.assertEqual(rc_dry, 0, out_dry)
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 0)

        # 2) --forget --apply grava forgotten e heating_optout antes de qualquer leitura ou DELETE, usando o relógio do D1.
        start_calls = len(f.calls)
        real_now = S._now
        S._now = lambda: datetime(2000, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        try:
            rc, out = self._run_cli(f, "--forget", "--apply", stdin=f"  {VETOR_EMAIL.upper()} \n", forget_key=FORGET_KEY)
        finally:
            S._now = real_now
        self.assertEqual(rc, 0, out)
        self.assertNotIn(VETOR_EMAIL, out.lower())

        new_calls = f.calls[start_calls:]
        self.assertTrue(new_calls[0][1].endswith("/query"))
        self.assertTrue(new_calls[0][2]["sql"].lstrip().upper().startswith("INSERT INTO FORGOTTEN"))
        self.assertTrue(new_calls[1][1].endswith("/query"))
        opt_sql = new_calls[1][2]["sql"]
        self.assertTrue(opt_sql.lstrip().upper().startswith("INSERT INTO HEATING_OPTOUT"), opt_sql)
        self.assertIn("ON CONFLICT(email_hmac) DO UPDATE SET opted_out_at = excluded.opted_out_at", opt_sql)
        self.assertIn("strftime('%Y-%m-%dT%H:%M:%fZ','now')", opt_sql)
        self.assertEqual(new_calls[1][2]["params"], [expected_optout_hmac])
        self.assertNotIn(VETOR_EMAIL, json.dumps(new_calls[1][2]).lower())

        # Confirma ordem: os dois INSERTs antes de qualquer SELECT, leitura do Twenty ou DELETE.
        ops = []
        for m, p, d, _ in new_calls:
            if p.endswith("/query"):
                sql_u = d["sql"].lstrip().upper()
                if sql_u.startswith("INSERT INTO FORGOTTEN"):
                    ops.append("ins_forgotten")
                elif sql_u.startswith("INSERT INTO HEATING_OPTOUT"):
                    ops.append("ins_optout")
                elif sql_u.startswith("SELECT") and "select" not in ops:
                    ops.append("select")
                elif sql_u.startswith("DELETE FROM CONVERSIONS"):
                    ops.append("del_d1")
            elif m == "DELETE":
                ops.append("del_tw")
        self.assertEqual(ops, ["ins_forgotten", "ins_optout", "select", "del_tw", "del_d1"])

        opt_rows = [dict(r) for r in f.db.execute("SELECT email_hmac, opted_out_at FROM heating_optout")]
        self.assertEqual(len(opt_rows), 1)
        self.assertEqual(opt_rows[0]["email_hmac"], expected_optout_hmac)
        self.assertRegex(opt_rows[0]["opted_out_at"], iso_re)
        self.assertFalse(opt_rows[0]["opted_out_at"].startswith("2000-"), opt_rows[0]["opted_out_at"])

        # 3) Upsert idempotente: repetir --forget --apply atualiza opted_out_at mantendo 1 linha.
        f.db.execute("UPDATE heating_optout SET opted_out_at = ? WHERE email_hmac = ?", ("2020-01-01T00:00:00.000Z", expected_optout_hmac))
        f.db.commit()
        rc2, out2 = self._run_cli(f, "--forget", "--apply", stdin=f"{VETOR_EMAIL}\n", forget_key=FORGET_KEY)
        self.assertEqual(rc2, 0, out2)
        opt_rows2 = [dict(r) for r in f.db.execute("SELECT email_hmac, opted_out_at FROM heating_optout")]
        self.assertEqual(len(opt_rows2), 1)
        self.assertRegex(opt_rows2[0]["opted_out_at"], iso_re)
        self.assertGreater(opt_rows2[0]["opted_out_at"], "2020-01-01T00:00:00.000Z")

        # 4) A marca em heating_optout é permanente: a poda de marcadores vencidos em forgotten (> 8d) não a apaga.
        f.db.execute(
            "UPDATE forgotten SET forgotten_at = strftime('%Y-%m-%dT%H:%M:%fZ','now','-9 days') WHERE email_hmac = ?",
            (VETOR_HMAC,),
        )
        f.db.commit()
        run(f, apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM forgotten").fetchone()[0], 0)
        self.assertEqual(
            [r[0] for r in f.db.execute("SELECT email_hmac FROM heating_optout")],
            [expected_optout_hmac],
        )

        # 5) Se o Twenty falhar na leitura, heating_optout já fica gravado e conversions permanece intacta.
        g = Fake(people=[person("p1", VETOR_EMAIL)])
        g.add(1, VETOR_EMAIL)
        run(g, apply=True)
        g.fail = lambda m, p, d: (500, {"errors": [{"message": "twenty fora"}]}) if p == "/metadata" else None
        with self.assertRaises(S.SyncError):
            forget(g, VETOR_EMAIL, apply=True)
        self.assertEqual(
            [r[0] for r in g.db.execute("SELECT email_hmac FROM heating_optout")],
            [expected_optout_hmac],
        )
        self.assertEqual(g.db.execute("SELECT count(*) FROM conversions WHERE email = ?", (VETOR_EMAIL,)).fetchone()[0], 1)

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


# ---------------------------------------------------------------- Id da Pessoa no D1 (issue #193)


class TwentyPersonIdTest(unittest.TestCase):
    def test_sync_records_twenty_person_id_for_new_existing_multi_email_and_duplicate_person(self):
        f = Fake(people=[person("p1", "existente@x.com", extra=["extra@x.com"])])
        f.add(1, "existente@x.com")
        f.add(2, "extra@x.com", when="2026-11-25T10:00:00.000Z")
        f.add(3, "nova@x.com")
        counts, _ = run(f, apply=True)
        nova = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "nova@x.com")
        self.assertEqual(f.row(1)["twenty_person_id"], "p1")
        self.assertEqual(f.row(2)["twenty_person_id"], "p1")
        self.assertEqual(f.row(3)["twenty_person_id"], nova["id"])
        self.assertEqual(counts["pessoa_id_invalido"], 0)
        # Email em duas Pessoas: grava o id da mais antiga, a mesma que recebeu as Conversões.
        g = Fake(people=[person("p2", "z@x.com", extra=["a@x.com"], created="2025-01-01T00:00:00.000Z"),
                         person("p1", "a@x.com", created="2026-01-01T00:00:00.000Z")])
        g.add(1, "a@x.com")
        run(g, apply=True)
        self.assertEqual(g.row(1)["twenty_person_id"], "p2")
        self.assertEqual(list(g.convs.values())[0]["pessoaId"], "p2")

    def test_pending_request_without_person_and_trashed_person_keep_null_id(self):
        f = Fake(people=[person("p-lixo", "lixo@x.com", deletedAt="2026-11-24T00:00:00.000Z")])
        f.add(1, "pedido@x.com", action="material_request")
        f.add(2, "lixo@x.com")
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["pedido_sem_pessoa"], counts["pessoa_na_lixeira"]), (1, 1))
        self.assertIsNone(f.row(1)["synced_at"])
        self.assertIsNone(f.row(1)["twenty_person_id"])
        self.assertIsNone(f.row(2)["synced_at"])
        self.assertIsNone(f.row(2)["twenty_person_id"])

    def test_dry_run_writes_no_twenty_person_id_and_prints_new_counters(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        f.add(1, "a@x.com")  # pendente
        f.add(2, "b@x.com", synced="2026-11-20T10:00:00.000Z")  # antiga com Conversão
        f.add(3, "c@x.com", synced="2026-11-20T11:00:00.000Z")  # antiga sem Conversão
        f.convs["c2"] = {"id": "c2", "eventoId": hexid(2), "pessoaId": "p1"}
        counts, out = run(f, apply=False)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertIsNone(f.row(1)["twenty_person_id"])
        self.assertIsNone(f.row(2)["twenty_person_id"])
        self.assertIsNone(f.row(3)["twenty_person_id"])
        self.assertEqual((counts["ids_a_preencher"], counts["sem_conversao_no_crm"], counts["pessoa_id_invalido"]), (1, 1, 0))
        for line in ("ids_a_preencher: 1", "sem_conversao_no_crm: 1", "pessoa_id_invalido: 0"):
            self.assertIn(line, out)
        # No retorno cedo (nada pendente nem a preencher), os três contadores também aparecem.
        g = Fake()
        counts, out = run(g, apply=False)
        for line in ("ids_a_preencher: 0", "sem_conversao_no_crm: 0", "pessoa_id_invalido: 0", "nada pendente"):
            self.assertIn(line, out)

    def test_backfill_fills_old_rows_without_pending_or_twenty_writes(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        synced = "2026-11-20T10:00:00.000Z"
        f.add(1, "a@x.com", synced=synced)  # ativa no CRM
        f.add(2, "a@x.com", synced=synced)  # na lixeira no CRM
        f.add(3, "b@x.com", synced=synced)  # sem Conversão no CRM
        f.add(4, "c@x.com", synced=synced)  # Conversão sem pessoaId
        f.add(5, "d@x.com", synced=synced)  # Conversão com pessoaId com espaço
        f.add(6, "e@x.com", synced=synced)  # Conversão com pessoaId de 65 caracteres
        f.add(7, "f@x.com", synced=synced, twenty_person_id="p-ja-tem")  # já preenchida: não é tocada
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "550e8400-e29b-41d4-a716-446655440000"}
        f.convs["c2"] = {"id": "c2", "eventoId": hexid(2), "pessoaId": "p-lixeira", "deletedAt": "2026-11-21T00:00:00.000Z"}
        f.convs["c4"] = {"id": "c4", "eventoId": hexid(4), "pessoaId": None}
        f.convs["c5"] = {"id": "c5", "eventoId": hexid(5), "pessoaId": "x y"}
        f.convs["c6"] = {"id": "c6", "eventoId": hexid(6), "pessoaId": "a" * 65}
        f.convs["c7"] = {"id": "c7", "eventoId": hexid(7), "pessoaId": "outro-id"}
        counts, out = run(f, apply=True)
        self.assertEqual(f.twenty_writes(), [])
        self.assertEqual((counts["pendentes_lidas"], counts["ids_a_preencher"], counts["sem_conversao_no_crm"]), (0, 2, 4))
        self.assertEqual(f.row(1)["twenty_person_id"], "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(f.row(2)["twenty_person_id"], "p-lixeira")
        for n in (3, 4, 5, 6):
            self.assertIsNone(f.row(n)["twenty_person_id"], n)
        self.assertEqual(f.row(7)["twenty_person_id"], "p-ja-tem")
        self.assertEqual(f.row(1)["synced_at"], synced)
        self.assertIn("gravado", out)

    def test_backfill_only_skips_people_listing(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        f.add(1, "a@x.com", synced="2026-11-20T10:00:00.000Z")
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p1"}
        counts, _ = run(f, apply=True)
        self.assertEqual([c for c in f.calls if c[1] == "/rest/people"], [])
        self.assertEqual((counts["pendentes_lidas"], counts["ids_a_preencher"]), (0, 1))
        self.assertEqual(f.row(1)["twenty_person_id"], "p1")
        self.assertEqual(f.twenty_writes(), [])

    def test_backfill_when_site_conversion_object_does_not_exist_yet(self):
        f = Fake(schema=False)
        f.add(1, "a@x.com", synced="2026-11-20T10:00:00.000Z")
        f.add(2, "b@x.com", synced="2026-11-20T11:00:00.000Z")
        counts, _ = run(f, apply=True)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertEqual((counts["ids_a_preencher"], counts["sem_conversao_no_crm"]), (0, 2))
        self.assertIsNone(f.row(1)["twenty_person_id"])
        self.assertIsNone(f.row(2)["twenty_person_id"])

    def test_backfill_chunks_and_runs_after_normal_sync_writes(self):
        f = Fake(people=[person("p1", "a@x.com")], schema=True)
        synced = "2026-11-20T10:00:00.000Z"
        for i in range(1, 56):
            f.add(i, "a@x.com", synced=synced)
            f.convs[f"c{i:03d}"] = {"id": f"c{i:03d}", "eventoId": hexid(i), "pessoaId": "p1"}
        f.add(100, "a@x.com", when="2026-11-25T10:00:00.000Z")  # pendente na mesma rodada
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["linhas_marcadas"], counts["ids_a_preencher"]), (1, 55))
        self.assertEqual(f.row(100)["twenty_person_id"], "p1")
        self.assertEqual(f.row(1)["twenty_person_id"], "p1")
        self.assertEqual(f.row(55)["twenty_person_id"], "p1")
        writes = f.d1_writes()
        self.assertEqual(len(writes), 3)  # 1 do sync normal + 2 lotes (50 + 5) do preenchimento
        self.assertIn("synced_at = ?", writes[0])
        self.assertNotIn("synced_at = ?", writes[1])
        self.assertNotIn("synced_at = ?", writes[2])

    def test_invalid_person_id_from_twenty_marks_null_and_counts(self):
        # Pessoa nova cuja criação no Twenty devolve id fora do padrão do CHECK.
        def bad_create(method, path, data):
            if method == "POST" and path == "/rest/people":
                return 201, {"data": {"createPerson": {"id": "id inválido"}}}
            return None
        f = Fake(fail=bad_create)
        f.add(1, "nova@x.com")
        counts, out = run(f, apply=True)
        self.assertEqual(len(f.convs), 1)
        self.assertEqual(list(f.convs.values())[0]["pessoaId"], "id inválido")
        self.assertEqual(f.pending(), [])
        self.assertIsNone(f.row(1)["twenty_person_id"])
        self.assertEqual(counts["pessoa_id_invalido"], 1)
        self.assertIn("pessoa_id_invalido: 1", out)
        # Pessoa existente cujo id está fora do padrão: conta na simulação e no --apply.
        g = Fake(people=[person("id invalido", "a@x.com")])
        g.add(1, "a@x.com")
        counts_dry, out_dry = run(g, apply=False)
        self.assertEqual(counts_dry["pessoa_id_invalido"], 1)
        self.assertIn("pessoa_id_invalido: 1", out_dry)
        counts_apply, _ = run(g, apply=True)
        self.assertEqual(counts_apply["pessoa_id_invalido"], 1)
        self.assertEqual(g.pending(), [])
        self.assertIsNone(g.row(1)["twenty_person_id"])

    def test_d1_check_refuses_invalid_twenty_person_id_and_accepts_uuid(self):
        f = Fake()
        for i, bad in enumerate(("a b", "a/b", "", "a" * 65, "id_com_underscore", "ação"), start=1):
            with self.assertRaises(sqlite3.IntegrityError, msg=repr(bad)):
                f.add(i, "a@x.com", twenty_person_id=bad)
        f.add(10, "a@x.com", twenty_person_id="550e8400-e29b-41d4-a716-446655440000")
        f.add(11, "a@x.com", twenty_person_id="A" * 64)
        f.add(12, "a@x.com", twenty_person_id=None)
        self.assertEqual(f.row(10)["twenty_person_id"], "550e8400-e29b-41d4-a716-446655440000")
        self.assertEqual(f.row(11)["twenty_person_id"], "A" * 64)
        self.assertIsNone(f.row(12)["twenty_person_id"])

    def test_forget_deletes_twenty_person_id_only_for_that_email(self):
        f = Fake(people=[person("p1", "a@x.com", extra=["b@x.com"])])
        f.add(1, "a@x.com")
        f.add(2, "a@x.com", action="material_request", when="2026-11-23T10:00:00.000Z")
        f.add(3, "b@x.com", when="2026-11-25T10:00:00.000Z")
        run(f, apply=True)
        self.assertEqual((f.row(1)["twenty_person_id"], f.row(2)["twenty_person_id"], f.row(3)["twenty_person_id"]),
                         ("p1", "p1", "p1"))
        forget(f, "a@x.com", apply=True)
        self.assertEqual(f.db.execute("SELECT count(*) FROM conversions WHERE email = ?", ("a@x.com",)).fetchone()[0], 0)
        self.assertEqual(
            f.db.execute("SELECT count(*) FROM conversions WHERE twenty_person_id = ? AND email = ?", ("p1", "a@x.com")).fetchone()[0],
            0,
        )
        self.assertEqual(f.row(3)["twenty_person_id"], "p1")

    def test_missing_migration_0008_fails_before_any_write(self):
        def no_0008(method, path, data):
            if path.endswith("/query") and "twenty_person_id" in data["sql"]:
                return 400, {"success": False, "errors": [{"message": "no such column: twenty_person_id"}]}
            return None
        f = Fake(people=[person("p1", "a@x.com")], fail=no_0008)
        f.add(1, "a@x.com")
        f.db.execute("INSERT INTO forgotten (email_hmac, forgotten_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now','-9 days'))", (hexid(1),))
        f.db.commit()
        with self.assertRaises(S.SyncError):
            run(f, apply=True)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertEqual(f.db.execute("SELECT count(*) FROM forgotten").fetchone()[0], 1)

    def test_pending_with_existing_conversion_uses_its_pessoa_id_not_current_email_person(self):
        # Conversão já existe ligada a 'p-old', mas hoje o email resolve para 'p-new': grava 'p-old' sem recriar.
        f = Fake(people=[person("p-new", "a@x.com")], schema=True)
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p-old"}
        f.add(1, "a@x.com")
        counts_dry, _ = run(f, apply=False)
        self.assertEqual(counts_dry["pessoa_id_invalido"], 0)
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["pessoa_id_invalido"]), (0, 1, 0))
        self.assertEqual(len(f.convs), 1)
        self.assertEqual(f.pending(), [])
        self.assertIsNotNone(f.row(1)["synced_at"])
        self.assertEqual(f.row(1)["twenty_person_id"], "p-old")

    def test_pending_with_existing_conversion_without_pessoa_id_marks_null_and_counts_invalid(self):
        # Mesma situação com pessoaId ausente na Conversão existente: marca nulo e conta 1 na simulação e no --apply.
        f = Fake(people=[person("p-new", "a@x.com")], schema=True)
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1)}
        f.add(1, "a@x.com")
        counts_dry, _ = run(f, apply=False)
        self.assertEqual(counts_dry["pessoa_id_invalido"], 1)
        counts, out = run(f, apply=True)
        self.assertEqual((counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["pessoa_id_invalido"]), (0, 1, 1))
        self.assertIn("pessoa_id_invalido: 1", out)
        self.assertEqual(f.pending(), [])
        self.assertIsNotNone(f.row(1)["synced_at"])
        self.assertIsNone(f.row(1)["twenty_person_id"])

    def test_mixed_group_uses_existing_conversion_id_and_current_person_id_for_new(self):
        # Grupo misto: existente ligada a 'p-old' fica com 'p-old'; nova recebe o pid da Pessoa atual ('p-new').
        f = Fake(people=[person("p-new", "a@x.com")], schema=True)
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p-old"}
        f.add(1, "a@x.com", when="2026-11-24T10:00:00.000Z")
        f.add(2, "a@x.com", when="2026-11-25T10:00:00.000Z")
        counts, _ = run(f, apply=True)
        self.assertEqual((counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["pessoa_id_invalido"]), (1, 1, 0))
        self.assertEqual(f.pending(), [])
        self.assertEqual(f.row(1)["twenty_person_id"], "p-old")
        self.assertEqual(f.row(2)["twenty_person_id"], "p-new")
        self.assertEqual(conv_by_event(f)[hexid(2)]["pessoaId"], "p-new")
        # Pessoa existente com id fora do padrão + grupo misto: conta só a linha nova, igual na simulação e no --apply.
        g = Fake(people=[person("id invalido", "a@x.com")], schema=True)
        g.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p-old"}
        g.add(1, "a@x.com", when="2026-11-24T10:00:00.000Z")
        g.add(2, "a@x.com", when="2026-11-25T10:00:00.000Z")
        counts_dry, _ = run(g, apply=False)
        self.assertEqual(counts_dry["pessoa_id_invalido"], 1)
        counts_apply, _ = run(g, apply=True)
        self.assertEqual(counts_apply["pessoa_id_invalido"], 1)
        self.assertEqual(g.pending(), [])
        self.assertEqual(g.row(1)["twenty_person_id"], "p-old")
        self.assertIsNone(g.row(2)["twenty_person_id"])

    def test_pending_with_trashed_existing_conversion_uses_its_pessoa_id(self):
        # Conversão já existe na lixeira ligada a 'p-old', com email hoje na Pessoa ativa 'p-new': grava 'p-old' sem recriar.
        f = Fake(people=[person("p-new", "a@x.com")], schema=True)
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p-old", "deletedAt": "2026-11-21T00:00:00.000Z"}
        f.add(1, "a@x.com")
        counts, _ = run(f, apply=True)
        self.assertEqual(
            (counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["conversoes_na_lixeira"], counts["pessoa_id_invalido"]),
            (0, 1, 1, 0),
        )
        self.assertEqual(len(f.convs), 1)
        self.assertEqual(f.pending(), [])
        self.assertEqual(f.row(1)["twenty_person_id"], "p-old")

    def test_pending_with_existing_conversion_and_new_person_keeps_existing_pessoa_id(self):
        # Conversão já existe ligada a 'p-old', email sem Pessoa (target None): cria Pessoa nova, mas mantém 'p-old' na linha.
        f = Fake(schema=True)
        f.convs["c1"] = {"id": "c1", "eventoId": hexid(1), "pessoaId": "p-old"}
        f.add(1, "a@x.com")
        counts, _ = run(f, apply=True)
        (nova,) = f.people.values()
        self.assertEqual(
            (counts["pessoas_a_criar"], counts["conversoes_a_criar"], counts["conversoes_ja_no_crm"], counts["pessoa_id_invalido"]),
            (1, 0, 1, 0),
        )
        self.assertEqual(len(f.convs), 1)
        self.assertEqual(f.pending(), [])
        self.assertEqual(f.row(1)["twenty_person_id"], "p-old")
        self.assertNotEqual(f.row(1)["twenty_person_id"], nova["id"])

    def test_forget_clears_site_heating_and_keeps_course_context_intact(self):
        # Épico #197: --forget apaga engagement_events e heating do contexto `site` e limpa os 4 campos heating* da
        # Pessoa, mantendo os campos site* e deixando intactos os contextos de curso (`curso:<slug>`).
        f = Fake(people=[person(
            "p1", "a@x.com",
            heatingNota=42, heatingFaixa="QUENTE", heatingSinais="Download de material (12,0)",
            heatingCalculadoEm="2026-11-27T10:00:00.000Z",
        )])
        for k, t in (("heatingNota", "NUMBER"), ("heatingFaixa", "SELECT"), ("heatingSinais", "TEXT"), ("heatingCalculadoEm", "DATE_TIME")):
            f.person_fields[k] = {"id": f"pf-{k}", "name": k, "type": t, "options": None}
        f.add(1, "a@x.com")
        run(f, apply=True)
        # Eventos e notas nos dois contextos (site e curso) + outro email no site.
        f.db.execute(
            "INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at) VALUES "
            "('ev-site-a', 'site', 'a@x.com', 'material_baixado', 'conversoes', 'planilha-custos-ia', '2026-11-26T10:00:00.000Z', '2026-11-27T10:00:00.000Z'),"
            "('ev-curso-a', 'curso:chatgpt-no-dia-a-dia', 'a@x.com', 'curso_cadastro', 'curso', NULL, '2026-11-26T10:00:00.000Z', '2026-11-27T10:00:00.000Z'),"
            "('ev-site-b', 'site', 'b@x.com', 'material_baixado', 'conversoes', 'planilha-custos-ia', '2026-11-26T10:00:00.000Z', '2026-11-27T10:00:00.000Z')"
        )
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'a@x.com', 42, 'quente', '[]', '2026-11-27T10:00:00.000Z', 'p1'),"
            "('curso:chatgpt-no-dia-a-dia', 'a@x.com', 15, 'morno', '[]', '2026-11-27T10:00:00.000Z', 'p1'),"
            "('site', 'b@x.com', 12, 'frio', '[]', '2026-11-27T10:00:00.000Z', NULL)"
        )
        f.db.commit()

        # Simulação não apaga nada nem limpa a Pessoa.
        counts_dry, out_dry = forget(f, "a@x.com", apply=False)
        self.assertEqual((counts_dry["eventos_site"], counts_dry["heating_site"], counts_dry["pessoas_heating_limpas"]), (1, 1, 1))
        self.assertEqual(f.people["p1"]["heatingNota"], 42)
        self.assertIn("simulação", out_dry)

        # Com --apply, limpa apenas o contexto site e os 4 campos heating* da Pessoa.
        counts, _ = forget(f, "a@x.com", apply=True)
        self.assertEqual((counts["eventos_site"], counts["heating_site"], counts["pessoas_heating_limpas"]), (1, 1, 1))
        self.assertEqual([r[0] for r in f.db.execute("SELECT id FROM engagement_events ORDER BY id")], ["ev-curso-a", "ev-site-b"])
        self.assertEqual(
            [(r[0], r[1]) for r in f.db.execute("SELECT context, email FROM heating ORDER BY context, email")],
            [("curso:chatgpt-no-dia-a-dia", "a@x.com"), ("site", "b@x.com")],
        )
        p1 = f.people["p1"]
        for k in S.HEATING_PERSON_FIELDS:
            self.assertIsNone(p1[k], k)
        # Campos site* da Pessoa continuam preservados (decisão de 23/11).
        self.assertEqual(p1["siteInteresses"], ["CUSTO_IA"])
        self.assertIs(p1["siteEmailComprovado"], True)

    def test_forget_clears_heating_by_twenty_person_id_when_email_changed_and_treats_empty_string_as_empty(self):
        # Codex P2 + agy P2: --forget limpa heating* nas Pessoas ativas referenciadas por twenty_person_id em
        # conversions e heating (site), mesmo quando o email da Pessoa mudou no Twenty; trata '' como vazio e 0 como preenchido.
        f = Fake(people=[
            person(
                "p1", "novo1@x.com",
                heatingNota=30, heatingFaixa="MORNO", heatingSinais="Download de material",
                heatingCalculadoEm="2026-11-27T10:00:00.000Z",
            ),
            person(
                "p2", "novo2@x.com",
                heatingNota=0, heatingFaixa="FRIO", heatingSinais="Reclamou de spam: não abordar",
                heatingCalculadoEm="2026-11-27T10:00:00.000Z",
            ),
            person(
                "p-lixo", "novo3@x.com", deletedAt="2026-11-20T00:00:00.000Z",
                heatingNota=10, heatingFaixa="FRIO", heatingSinais="x",
                heatingCalculadoEm="2026-11-27T10:00:00.000Z",
            ),
            person(
                "p-vazio", "vazio@x.com",
                heatingNota=None, heatingFaixa="", heatingSinais="", heatingCalculadoEm=None,
            ),
        ], schema=True)
        for k, t in (("heatingNota", "NUMBER"), ("heatingFaixa", "SELECT"), ("heatingSinais", "TEXT"), ("heatingCalculadoEm", "DATE_TIME")):
            f.person_fields[k] = {"id": f"pf-{k}", "name": k, "type": t, "options": None}
        # conversions referencia p1 e p-lixo; heating (site) referencia p2.
        f.add(1, "a@x.com", synced="2026-11-26T10:00:00.000Z", twenty_person_id="p1")
        f.add(2, "a@x.com", synced="2026-11-26T11:00:00.000Z", twenty_person_id="p-lixo")
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'a@x.com', 0, 'frio', '[]', '2026-11-27T10:00:00.000Z', 'p2'),"
            "('curso:chatgpt-no-dia-a-dia', 'a@x.com', 20, 'morno', '[]', '2026-11-27T10:00:00.000Z', 'p-curso')"
        )
        f.db.commit()

        # Simulação conta as 2 Pessoas ativas (p1 e p2), sem contar p-lixo.
        counts_dry, _ = forget(f, "a@x.com", apply=False)
        self.assertEqual(counts_dry["pessoas_heating_limpas"], 2)
        self.assertEqual(f.people["p1"]["heatingNota"], 30)
        self.assertEqual(f.people["p2"]["heatingNota"], 0)

        # --apply limpa p1 e p2 (inclusive nota 0) e não toca em p-lixo.
        counts, _ = forget(f, "a@x.com", apply=True)
        self.assertEqual(counts["pessoas_heating_limpas"], 2)
        for pid in ("p1", "p2"):
            for k in S.HEATING_PERSON_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        self.assertEqual(f.people["p-lixo"]["heatingNota"], 10)
        self.assertEqual([c for c in f.twenty_writes() if c[1] == "/rest/people/p-lixo"], [])

        # Pessoa com campos '' e None não gera PATCH em --forget.
        n_before = len(f.twenty_writes())
        counts_v, _ = forget(f, "vazio@x.com", apply=True)
        self.assertEqual(counts_v["pessoas_heating_limpas"], 0)
        self.assertEqual(len(f.twenty_writes()), n_before)


class ForgetGooglePurgeTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="conversoes-lock-ga4-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def _run_cli(self, fake, *args, stdin=None, ga4_delete=None, bq_delete=None, now_dt=None, sa_file=None):
        out, err = io.StringIO(), io.StringIO()
        keys = [*ENV, "CONVERSOES_SYNC_LOCK", "FORGET_HMAC_KEY", "HEATING_GCP_SA_FILE"]
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        os.environ["FORGET_HMAC_KEY"] = FORGET_KEY
        if sa_file is None:
            os.environ.pop("HEATING_GCP_SA_FILE", None)
        else:
            os.environ["HEATING_GCP_SA_FILE"] = sa_file
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = S.main(
                    list(args),
                    transport=fake,
                    sleep=lambda s: None,
                    stdin=io.StringIO(stdin) if stdin is not None else None,
                    ga4_delete=ga4_delete,
                    bq_delete=bq_delete,
                    now_dt=now_dt,
                )
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_forget_site_chama_ga4_e_bq_agenda_fila_e_apaga_site_identities(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
        f = Fake(people=[person("p1", "segredo@x.com")])
        f.add(1, "segredo@x.com")
        run(f, apply=True)
        f.add_identity("UidSiteAlpha1", "segredo@x.com")
        f.add_identity("UidSiteBeta2", "segredo@x.com")
        f.add_identity("UidOutroEmail3", "outro@x.com")

        ga4_calls: list[str] = []
        bq_calls: list[tuple[str, list]] = []

        # Simulação: conta identidades_site, não chama Google, não apaga site_identities nem dispara trigger.
        rc_dry, out_dry = self._run_cli(
            f, "--forget", stdin="segredo@x.com\n",
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
            now_dt=now,
        )
        self.assertEqual((rc_dry, ga4_calls, bq_calls), (0, [], []))
        self.assertIn("identidades_site: 2", out_dry)
        self.assertEqual(f.db.execute("SELECT count(*) FROM site_identities").fetchone()[0], 3)
        self.assertEqual(f.db.execute("SELECT count(*) FROM ga4_purge_queue").fetchone()[0], 0)

        # --apply: apaga site_identities (trigger enfileira), processa GA4 e BigQuery na hora, salva sessoes e agenda D+1.
        rc, out = self._run_cli(
            f, "--forget", "--apply", stdin="segredo@x.com\n",
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {
                "tabelas_limpas": 2, "adiadas": 1, "sessoes": [("ps-" + u, 1001)],
            },
            now_dt=now,
        )
        self.assertEqual(rc, 0, out)
        self.assertEqual(ga4_calls, ["UidSiteAlpha1", "UidSiteBeta2"])
        self.assertEqual(bq_calls, [("UidSiteAlpha1", []), ("UidSiteBeta2", [])])
        self.assertIn("ga4_removidos: 2", out)
        self.assertIn("bq_tabelas_limpas: 4", out)
        self.assertIn("bq_remocao_adiada: 2", out)
        self.assertNotIn(S.GA4_PURGE_PENDENTE_MSG, out)
        self.assertNotIn(S.GA4_FILA_SEM_CRED_MSG, out)
        for secret in ("segredo@x.com", "UidSiteAlpha1", "UidSiteBeta2"):
            self.assertNotIn(secret.lower(), out.lower())
        self.assertEqual([r[0] for r in f.db.execute("SELECT uid FROM site_identities")], ["UidOutroEmail3"])
        queue = [
            dict(r)
            for r in f.db.execute(
                "SELECT uid, ga4_pendente, sessoes, bq_tentativas, proxima_em FROM ga4_purge_queue ORDER BY uid"
            )
        ]
        self.assertEqual(
            queue,
            [
                {
                    "uid": "UidSiteAlpha1",
                    "ga4_pendente": 0,
                    "sessoes": '[["ps-UidSiteAlpha1",1001]]',
                    "bq_tentativas": 1,
                    "proxima_em": S.stamp(now + timedelta(days=1)),
                },
                {
                    "uid": "UidSiteBeta2",
                    "ga4_pendente": 0,
                    "sessoes": '[["ps-UidSiteBeta2",1001]]',
                    "bq_tentativas": 1,
                    "proxima_em": S.stamp(now + timedelta(days=1)),
                },
            ],
        )

    def test_forget_ga4_403_bq_falha_e_sem_sa_file(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)

        # 1) 403 no GA4: código 2 + mensagem fixa; BigQuery roda e salva sessões; item fica na fila com ga4_pendente=1
        #    para o job diário retomar (e conclui quando o GA4 passar a responder 200).
        f = Fake(people=[person("p1", "erro403@x.com")])
        f.add(1, "erro403@x.com")
        run(f, apply=True)
        f.add_identity("UidErro403", "erro403@x.com")
        bq_calls: list[tuple[str, list]] = []
        rc_403, out_403 = self._run_cli(
            f, "--forget", "--apply", stdin="erro403@x.com\n",
            ga4_delete=lambda _u: False,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {
                "tabelas_limpas": 1, "adiadas": 0, "sessoes": [("ps403", 55)],
            },
            now_dt=now,
        )
        self.assertEqual(rc_403, 2, out_403)
        self.assertIn("ga4_remocao_pendente: 1", out_403)
        self.assertIn(S.GA4_PURGE_PENDENTE_MSG, out_403)
        self.assertEqual(bq_calls, [("UidErro403", [])])
        self.assertEqual((f.convs, f.db.execute("SELECT count(*) FROM conversions").fetchone()[0]), ({}, 0))
        self.assertEqual(f.db.execute("SELECT count(*) FROM site_identities").fetchone()[0], 0)
        q403 = dict(f.db.execute(
            "SELECT uid, ga4_pendente, sessoes, bq_tentativas, proxima_em FROM ga4_purge_queue WHERE uid = 'UidErro403'"
        ).fetchone())
        self.assertEqual(
            q403,
            {
                "uid": "UidErro403",
                "ga4_pendente": 1,
                "sessoes": '[["ps403",55]]',
                "bq_tentativas": 1,
                "proxima_em": S.stamp(now + timedelta(days=1)),
            },
        )
        for secret in ("erro403@x.com", "UidErro403"):
            self.assertNotIn(secret.lower(), out_403.lower())
        # Ao corrigir o papel de Editor e processar a fila em D+1, GA4 zera ga4_pendente e BQ recebe as sessões salvas.
        retried_ga4: list[str] = []
        retried_bq: list[tuple[str, list]] = []
        r_d1 = S.processar_fila_ga4(
            TS.Clients(ENV, f, sleep=lambda s: None),
            only_due=True,
            ga4_delete=lambda u: retried_ga4.append(u) or True,
            bq_delete=lambda u, s: retried_bq.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
            now_dt=now + timedelta(days=1),
        )
        self.assertEqual((retried_ga4, retried_bq), (["UidErro403"], [("UidErro403", [("ps403", 55)])]))
        self.assertEqual((r_d1["ga4_removidos"], r_d1["ga4_remocao_pendente"]), (1, 0))
        q403_d1 = dict(f.db.execute(
            "SELECT ga4_pendente, bq_tentativas, proxima_em FROM ga4_purge_queue WHERE uid = 'UidErro403'"
        ).fetchone())
        self.assertEqual(q403_d1, {"ga4_pendente": 0, "bq_tentativas": 2, "proxima_em": S.stamp(now + timedelta(days=3))})

        # 2) GA4 200 mas BigQuery falha: código 2 + BQ_PURGE_PENDENTE_MSG; ga4_pendente=0, bq_tentativas=0 e
        #    proxima_em=agora (tentativa imediata na próxima rodada). Mata mutações M15/M16.
        h = Fake(people=[person("p1", "bqfalha@x.com")])
        h.add(1, "bqfalha@x.com")
        run(h, apply=True)
        h.add_identity("UidBqFalha1", "bqfalha@x.com")

        def fail_bq(_u, _s):
            raise S.GoogleIndisponivel("BQ indisponível")

        rc_bq, out_bq = self._run_cli(
            h, "--forget", "--apply", stdin="bqfalha@x.com\n",
            ga4_delete=lambda _u: True,
            bq_delete=fail_bq,
            now_dt=now,
        )
        self.assertEqual(rc_bq, 2, out_bq)
        self.assertIn("bq_remocao_falhou: 1", out_bq)
        self.assertIn(S.BQ_PURGE_PENDENTE_MSG, out_bq)
        self.assertNotIn(S.GA4_PURGE_PENDENTE_MSG, out_bq)
        qbq = dict(h.db.execute(
            "SELECT uid, ga4_pendente, bq_tentativas, proxima_em FROM ga4_purge_queue WHERE uid = 'UidBqFalha1'"
        ).fetchone())
        self.assertEqual(
            qbq,
            {"uid": "UidBqFalha1", "ga4_pendente": 0, "bq_tentativas": 0, "proxima_em": S.stamp(now)},
        )
        for secret in ("bqfalha@x.com", "UidBqFalha1"):
            self.assertNotIn(secret.lower(), out_bq.lower())

        # 3) Sem HEATING_GCP_SA_FILE (ex.: no Mac): apaga site_identities (trigger enfileira), informa que ficou
        #    enfileirado para o job diário e sai com código 0 (sem chamar Google).
        g = Fake(people=[person("p1", "mac@x.com")])
        g.add(1, "mac@x.com")
        run(g, apply=True)
        g.add_identity("UidSemSa1", "mac@x.com")
        rc_mac, out_mac = self._run_cli(g, "--forget", "--apply", stdin="mac@x.com\n", now_dt=now, sa_file=None)
        self.assertEqual(rc_mac, 0, out_mac)
        self.assertIn("ga4_fila_enfileirados: 1", out_mac)
        self.assertIn(S.GA4_FILA_SEM_CRED_MSG, out_mac)
        self.assertNotIn(S.GA4_PURGE_PENDENTE_MSG, out_mac)
        self.assertEqual((g.convs, g.db.execute("SELECT count(*) FROM site_identities").fetchone()[0]), ({}, 0))
        qmac = dict(g.db.execute(
            "SELECT uid, ga4_pendente, sessoes, bq_tentativas FROM ga4_purge_queue WHERE uid = 'UidSemSa1'"
        ).fetchone())
        self.assertEqual(qmac, {"uid": "UidSemSa1", "ga4_pendente": 1, "sessoes": "[]", "bq_tentativas": 0})
        for secret in ("mac@x.com", "UidSemSa1"):
            self.assertNotIn(secret.lower(), out_mac.lower())

    def test_ga4_submit_user_deletion_e_bq_apagar_uid_producao(self):
        import types

        def norm_sql(sql: str) -> str:
            return re.sub(r"\s+", " ", sql).strip()

        ga4_posts: list[tuple] = []
        sa_scopes: list = []
        bq_queries: list[tuple] = []

        class BadRequest(Exception):
            __module__ = "google.api_core.exceptions"
            code = 400

        class NotFound(Exception):
            __module__ = "google.api_core.exceptions"
            code = 404

        class _ScalarParam:
            def __init__(self, name, typ, value):
                self.name, self.type_, self.value = name, typ, value

        class _StructParam:
            def __init__(self, name, *sub):
                self.name, self.sub = name, list(sub)

        class _ArrayParam:
            def __init__(self, name, array_type, values):
                self.name, self.array_type, self.values = name, array_type, list(values)

        class _JobCfg:
            def __init__(self, query_parameters=()):
                self.query_parameters = list(query_parameters)

        class _QueryJob:
            def __init__(self, rows=()):
                self._rows = list(rows)

            def result(self):
                return self._rows

        ga4_status = {"code": 200}
        bq_mode = {"mode": "full"}

        class _AuthSession:
            def __init__(self, creds):
                self.creds = creds

            def post(self, url, json=None, timeout=None):
                ga4_posts.append((url, json, timeout))
                return types.SimpleNamespace(status_code=ga4_status["code"])

        class _Creds:
            @staticmethod
            def from_service_account_file(path, scopes=None):
                sa_scopes.append((path, scopes))
                return object()

        class _BqClient:
            def __init__(self, project=None, credentials=None):
                self.project = project

            def query(self, sql, job_config=None, location=None):
                bq_queries.append((sql, job_config, location))
                if bq_mode["mode"] == "dataset_not_found":
                    raise NotFound("Not found: Dataset mindapps:analytics_341040810")
                if "INFORMATION_SCHEMA.TABLES" in sql:
                    if bq_mode["mode"] == "no_users_tables":
                        return _QueryJob([{"table_name": "pseudonymous_users_20261125"}])
                    return _QueryJob([
                        {"table_name": "users_20261125"},
                        {"table_name": "users_20261126"},
                        {"table_name": "pseudonymous_users_20261125"},
                        {"table_name": "pseudonymous_users_20261126"},
                        {"table_name": "users_invalido"},
                    ])
                if sql.lstrip().startswith("WITH eventos"):
                    if bq_mode["mode"] == "events_not_found":
                        raise NotFound("Not found: Table events_*")
                    return _QueryJob([
                        {"table_suffix": "20261125", "user_pseudo_id": "ps_exclusivo", "ga_session_id": 1001},
                        {"table_suffix": "20261126", "user_pseudo_id": "ps_compartilhado", "ga_session_id": 1002},
                        {"table_suffix": "intraday_20261127", "user_pseudo_id": "ps_exclusivo", "ga_session_id": 1003},
                        {"table_suffix": "20261125; DROP TABLE x", "user_pseudo_id": "ps_evil", "ga_session_id": 9999},
                    ])
                if "AND user_id != @uid" in sql:
                    # `ps_compartilhado` já apareceu com outro user_id -> NÃO pode ser apagado de pseudonymous_users_*
                    return _QueryJob([{"user_pseudo_id": "ps_compartilhado"}])
                if "FROM `mindapps.analytics_341040810.users_*`" in sql:
                    return _QueryJob([
                        {"table_suffix": "20261125"},
                        {"table_suffix": "20261199"},  # fora das tabelas existentes -> ignorado
                    ])
                if "FROM `mindapps.analytics_341040810.pseudonymous_users_*`" in sql:
                    return _QueryJob([{"table_suffix": "20261125"}])
                if "events_intraday_20261127" in sql:
                    raise BadRequest("UPDATE or DELETE statement over table would affect rows in the streaming buffer")
                return _QueryJob()

        fake_google = types.ModuleType("google")
        fake_auth = types.ModuleType("google.auth")
        fake_auth_tr = types.ModuleType("google.auth.transport")
        fake_auth_req = types.ModuleType("google.auth.transport.requests")
        fake_oauth = types.ModuleType("google.oauth2")
        fake_sa = types.ModuleType("google.oauth2.service_account")
        fake_cloud = types.ModuleType("google.cloud")
        fake_bq = types.ModuleType("google.cloud.bigquery")

        fake_auth_req.AuthorizedSession = _AuthSession
        fake_sa.Credentials = _Creds
        fake_bq.Client = _BqClient
        fake_bq.QueryJobConfig = _JobCfg
        fake_bq.ScalarQueryParameter = _ScalarParam
        fake_bq.ArrayQueryParameter = _ArrayParam
        fake_bq.StructQueryParameter = _StructParam

        mod_map = {
            "google": fake_google,
            "google.auth": fake_auth,
            "google.auth.transport": fake_auth_tr,
            "google.auth.transport.requests": fake_auth_req,
            "google.oauth2": fake_oauth,
            "google.oauth2.service_account": fake_sa,
            "google.cloud": fake_cloud,
            "google.cloud.bigquery": fake_bq,
        }
        saved = {k: sys.modules.get(k) for k in mod_map}
        sys.modules.update(mod_map)
        try:
            # 1) GA4 200 -> True; 403 -> False; confere URL, escopo e payload.
            self.assertTrue(S._ga4_submit_user_deletion("UidProd99", "/tmp/sa.json"))
            self.assertEqual(
                ga4_posts[-1],
                ("https://analyticsadmin.googleapis.com/v1alpha/properties/341040810:submitUserDeletion", {"userId": "UidProd99"}, 30),
            )
            self.assertEqual(sa_scopes[-1], ("/tmp/sa.json", ["https://www.googleapis.com/auth/analytics.edit"]))
            ga4_status["code"] = 403
            self.assertFalse(S._ga4_submit_user_deletion("UidProd99", "/tmp/sa.json"))

            # 2) BigQuery completo: events_* + users_YYYYMMDD + pseudonymous_users_YYYYMMDD (só exclusivos) + sessoes_conhecidas.
            res = S._bq_apagar_uid("UidProd99", "/tmp/sa.json", sessoes_conhecidas=[("ps_antigo", 500)])
            self.assertEqual(
                res,
                {
                    "tabelas_limpas": 4,  # events_20261125, events_20261126, users_20261125, pseudonymous_users_20261125
                    "adiadas": 1,         # events_intraday_20261127
                    "sessoes": [
                        ("ps_antigo", 500),
                        ("ps_compartilhado", 1002),
                        ("ps_exclusivo", 1001),
                        ("ps_exclusivo", 1003),
                    ],
                },
            )
            # Verifica estrutura exata da SQL de descoberta (mata M8a, M8b e P0 de sessão mista).
            disc_sql = norm_sql(bq_queries[1][0])
            self.assertIn(
                "FROM eventos WHERE user_id = @uid AND user_pseudo_id IS NOT NULL AND ga_session_id IS NOT NULL",
                disc_sql,
            )
            self.assertIn("UNION DISTINCT SELECT s.user_pseudo_id, s.ga_session_id FROM UNNEST(@sessoes_conhecidas) AS s", disc_sql)
            self.assertIn("LEFT JOIN sessoes AS s ON e.user_pseudo_id = s.user_pseudo_id AND e.ga_session_id = s.ga_session_id", disc_sql)
            self.assertIn("WHERE e.user_id = @uid OR ((e.user_id IS NULL OR e.user_id = '') AND s.user_pseudo_id IS NOT NULL)", disc_sql)

            # Verifica DELETEs de events_*: nunca wildcard, nunca sufixo malicioso (mata M8c), só anônimos pelo par de sessão (P0).
            ev_dels = [q for q in bq_queries if q[0].startswith("DELETE FROM `mindapps.analytics_341040810.events_")]
            self.assertEqual(len(ev_dels), 3)
            for dsql, _cfg, _loc in ev_dels:
                nsql = norm_sql(dsql)
                self.assertNotIn("events_*", nsql)
                self.assertNotIn("DROP TABLE", nsql)
                self.assertIn("WHERE user_id = @uid OR ( (user_id IS NULL OR user_id = '') AND STRUCT(", nsql)

            # Verifica DELETE de users_YYYYMMDD (somente tabela existente com linhas).
            u_dels = [q for q in bq_queries if q[0].startswith("DELETE FROM `mindapps.analytics_341040810.users_")]
            self.assertEqual(len(u_dels), 1)
            self.assertEqual(
                norm_sql(u_dels[0][0]),
                "DELETE FROM `mindapps.analytics_341040810.users_20261125` WHERE user_id = @uid",
            )

            # Verifica DELETE de pseudonymous_users_YYYYMMDD: usa pseudo_user_id e exclui ps_compartilhado!
            p_dels = [q for q in bq_queries if q[0].startswith("DELETE FROM `mindapps.analytics_341040810.pseudonymous_users_")]
            self.assertEqual(len(p_dels), 1)
            self.assertEqual(
                norm_sql(p_dels[0][0]),
                "DELETE FROM `mindapps.analytics_341040810.pseudonymous_users_20261125` WHERE pseudo_user_id IN UNNEST(@pseudo_ids)",
            )
            p_param = [p for p in p_dels[0][1].query_parameters if p.name == "pseudo_ids"][0]
            self.assertEqual(p_param.values, ["ps_antigo", "ps_exclusivo"])
            self.assertNotIn("ps_compartilhado", p_param.values)

            # 3) Quando não existem tabelas `users_YYYYMMDD` ainda, não falha nem consulta `users_*`.
            bq_queries.clear()
            bq_mode["mode"] = "no_users_tables"
            res_no_u = S._bq_apagar_uid("UidProd99", "/tmp/sa.json")
            self.assertEqual(res_no_u["tabelas_limpas"], 3)
            self.assertFalse(any(".users_*" in q[0] for q in bq_queries))

            # 4) Dataset ou tabela `events_*` não encontrados na descoberta -> erro (GoogleIndisponivel), NÃO sucesso!
            bq_mode["mode"] = "dataset_not_found"
            with self.assertRaises(S.GoogleIndisponivel):
                S._bq_apagar_uid("UidProd99", "/tmp/sa.json")
            bq_mode["mode"] = "events_not_found"
            with self.assertRaises(S.GoogleIndisponivel):
                S._bq_apagar_uid("UidProd99", "/tmp/sa.json")
        finally:
            for k, v in saved.items():
                sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)

    def test_bq_sessao_mista_nao_apaga_outro_uid_nem_outra_sessao(self):
        """Executa a lógica real de `_bq_discover_events_sql`, `_bq_delete_events_sql` e `_bq_shared_pseudo_ids_sql`
        em SQLite sobre dados sintéticos, provando que:
        - em sessão mista (UidA + UidB + anônimos na mesma sessão), apagar UidA remove UidA e anônimos, mas mantém UidB;
        - anônimos de outra sessão do mesmo user_pseudo_id permanecem intactos;
        - em D+1 (após UidA já ter sido apagado), eventos anônimos atrasados da sessão salva ainda são descobertos e
          apagados usando `sessoes_conhecidas`, sem tocar em UidB;
        - em `pseudonymous_users`, `user_pseudo_id` compartilhado com UidB é preservado e o exclusivo de UidA sai."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE events (
              id TEXT PRIMARY KEY,
              table_suffix TEXT NOT NULL,
              user_id TEXT,
              user_pseudo_id TEXT,
              ga_session_id INTEGER
            );
            CREATE TABLE pseudo_users (
              day TEXT NOT NULL,
              pseudo_user_id TEXT NOT NULL
            );
            """
        )
        # Sessão mista (ps_mix, 1001): anônimo NULL, anônimo '', UidA e UidB na mesma sessão!
        # Outra sessão (ps_mix, 2002): anônimo que NÃO pertence à sessão de UidA.
        # Sessão exclusiva de UidA: (ps_solo, 3001) com anônimo + UidA.
        # Outra pessoa UidC em (ps_c, 4001) + linha de UidA sem ga_session_id.
        seed = [
            ("e1", "20261125", None, "ps_mix", 1001),
            ("e2", "20261125", "", "ps_mix", 1001),
            ("e3", "20261125", "UidA", "ps_mix", 1001),
            ("e4", "20261125", "UidB", "ps_mix", 1001),
            ("e5", "20261125", None, "ps_mix", 2002),
            ("e6", "20261125", None, "ps_solo", 3001),
            ("e7", "20261125", "UidA", "ps_solo", 3001),
            ("e8", "20261125", "UidC", "ps_c", 4001),
            ("e9", "20261126", "UidA", "ps_solo", None),
        ]
        db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", seed)
        db.executemany(
            "INSERT INTO pseudo_users VALUES (?, ?)",
            [("20261125", "ps_mix"), ("20261125", "ps_solo"), ("20261125", "ps_c")],
        )

        def run_discover(uid: str, known: list[tuple[str, int]]):
            # Traduz apenas os marcadores de BigQuery da SQL real produzida pelo código para SQLite.
            sql = S._bq_discover_events_sql(bool(known))
            self.assertIn("WHERE user_id = @uid", sql)
            self.assertIn("AND e.ga_session_id = s.ga_session_id", sql)
            self.assertIn("(e.user_id IS NULL OR e.user_id = '')", sql)
            db.execute("DROP TABLE IF EXISTS _known")
            db.execute("CREATE TEMP TABLE _known (user_pseudo_id TEXT, ga_session_id INTEGER)")
            if known:
                db.executemany("INSERT INTO _known VALUES (?, ?)", known)
            q = (
                sql.replace("`mindapps.analytics_341040810.events_*`", "events")
                .replace("_TABLE_SUFFIX AS table_suffix", "table_suffix")
                .replace("(SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id", "ga_session_id")
                .replace("UNION DISTINCT", "UNION")
                .replace("UNNEST(@sessoes_conhecidas)", "_known")
                .replace("@uid", ":uid")
            )
            return [dict(r) for r in db.execute(q, {"uid": uid})]

        def run_delete(suffix: str, uid: str, sessoes: list[tuple[str, int]]):
            sql = S._bq_delete_events_sql(f"`mindapps.analytics_341040810.events_{suffix}`", bool(sessoes))
            if sessoes:
                self.assertIn("(user_id IS NULL OR user_id = '')", sql)
            db.execute("DROP TABLE IF EXISTS _sess")
            db.execute("CREATE TEMP TABLE _sess (user_pseudo_id TEXT, ga_session_id INTEGER)")
            if sessoes:
                db.executemany("INSERT INTO _sess VALUES (?, ?)", sessoes)
            q = (
                sql.replace(f"DELETE FROM `mindapps.analytics_341040810.events_{suffix}`", "DELETE FROM events")
                .replace(
                    "STRUCT(\n       user_pseudo_id,\n       (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id')\n     ) IN (\n       SELECT AS STRUCT s.user_pseudo_id, s.ga_session_id\n       FROM UNNEST(@sessoes) AS s\n     )",
                    "EXISTS (SELECT 1 FROM _sess AS s WHERE s.user_pseudo_id = events.user_pseudo_id AND s.ga_session_id = events.ga_session_id)",
                )
                .replace("@uid", ":uid")
            )
            q = f"{q} AND table_suffix = :suf" if "OR" not in q else q.replace("WHERE ", "WHERE table_suffix = :suf AND (", 1) + ")"
            db.execute(q, {"uid": uid, "suf": suffix})

        # Passada D0: descobre sessões, checa pseudo_ids compartilhados e apaga events + pseudo_users exclusivos.
        disc0 = run_discover("UidA", [])
        discovered_sessoes = sorted({(r["user_pseudo_id"], r["ga_session_id"]) for r in disc0 if r["ga_session_id"] is not None})
        self.assertEqual(discovered_sessoes, [("ps_mix", 1001), ("ps_solo", 3001)])
        cand_pseudos = sorted({r["user_pseudo_id"] for r in disc0 if r["user_pseudo_id"]})
        shared_sql = (
            S._bq_shared_pseudo_ids_sql()
            .replace("`mindapps.analytics_341040810.events_*`", "events")
            .replace("IN UNNEST(@pseudo_ids)", f"IN ({','.join('?' * len(cand_pseudos))})")
            .replace("@uid", "?")
        )
        shared = {r["user_pseudo_id"] for r in db.execute(shared_sql, [*cand_pseudos, "UidA"])}
        self.assertEqual(shared, {"ps_mix"})
        excl = sorted(set(cand_pseudos) - shared)
        self.assertEqual(excl, ["ps_solo"])

        by_suf: dict[str, set[tuple[str, int]]] = {}
        for r in disc0:
            s = by_suf.setdefault(r["table_suffix"], set())
            if r["ga_session_id"] is not None:
                s.add((r["user_pseudo_id"], r["ga_session_id"]))
        for suf, ss in sorted(by_suf.items()):
            run_delete(suf, "UidA", sorted(ss))
        db.execute(f"DELETE FROM pseudo_users WHERE pseudo_user_id IN ({','.join('?' * len(excl))})", excl)

        remaining_ids = [r["id"] for r in db.execute("SELECT id FROM events ORDER BY id")]
        # e4 (UidB na sessão mista!), e5 (anônimo de outra sessão 2002!) e e8 (UidC) DEVEM permanecer!
        self.assertEqual(remaining_ids, ["e4", "e5", "e8"])
        # ps_mix (compartilhado com UidB) e ps_c permanecem; ps_solo foi apagado!
        self.assertEqual([r[0] for r in db.execute("SELECT pseudo_user_id FROM pseudo_users ORDER BY pseudo_user_id")], ["ps_c", "ps_mix"])

        # Passada D+1: chega um evento anônimo atrasado na sessão (ps_mix, 1001) quando UidA já não existe em events!
        db.execute("INSERT INTO events VALUES ('e10', '20261126', NULL, 'ps_mix', 1001)")
        disc1 = run_discover("UidA", discovered_sessoes)
        self.assertEqual([(r["table_suffix"], r["user_pseudo_id"], r["ga_session_id"]) for r in disc1], [("20261126", "ps_mix", 1001)])
        run_delete("20261126", "UidA", [("ps_mix", 1001)])
        self.assertEqual([r["id"] for r in db.execute("SELECT id FROM events ORDER BY id")], ["e4", "e5", "e8"])
        db.close()


if __name__ == "__main__":
    unittest.main()
