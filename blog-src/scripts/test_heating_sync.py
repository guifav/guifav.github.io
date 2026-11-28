"""Testes do heating de contatos (épico #197, cortes 1 e 2). Rodar em blog-src/: python3 scripts/test_heating_sync.py

O D1 falso é um SQLite em memória com as migrações reais do painel (0001 em diante, incluindo 0010, 0011, 0012 e 0014),
com suporte ao payload `batch` da API REST do Cloudflare D1 (incluindo simulação de falha no meio do batch sem rollback),
e o Twenty falso guarda estado como o servidor (lixeira, campos únicos `emails` e `inscricaoId`, objeto `courseEnrollment`
e relação `pessoa`). A `site_identities` vem da migração real 0013 (PR #203).
"""
import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import types
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conversoes_sync as CS  # noqa: E402
import heating_sync as H  # noqa: E402
import twenty_sync as TS  # noqa: E402

FORGET_KEY = "chave-hmac-de-teste-com-32-bytes-ou-mais-0123456789"
ENV = {
    "CLOUDFLARE_API_TOKEN": "cf",
    "CLOUDFLARE_ACCOUNT_ID": "acc",
    "D1_DATABASE_ID": "db",
    "TWENTY_API_KEY": "tw",
    "TWENTY_LOCAL_URL": "http://127.0.0.1:3100",
    "FORGET_HMAC_KEY": FORGET_KEY,
}
MIGRATIONS = os.path.join(HERE, "..", "..", "workers", "admin", "migrations")
# O D1 de produção recusa padrão de GLOB/LIKE acima de 50 caracteres ("LIKE or GLOB pattern too complex", medido em
# 28/11: 50 passa, 51 falha); o SQLite padrão aceita 50000. A migração 0015 trouxe os CHECKs de data para esse limite.
D1_LIKE_PATTERN_MAX = 50
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = CS.stamp(NOW)
CUSTO = {"id": "CUSTO_IA", "rotulo": "Custo de IA"}
SLUG = "chatgpt-no-dia-a-dia"
CTX_CURSO = f"curso:{SLUG}"


def hexid(n: int) -> str:
    return f"{n:064x}"


def ago(days: float = 0, seconds: float = 0) -> str:
    return CS.stamp(NOW - timedelta(days=days, seconds=seconds))


def sql_normalizado(sql: str) -> str:
    """Texto de um CREATE do sqlite_master para comparar esquemas.

    Só três coisas são normalizadas, e cada uma numa posição conhecida:
    - o cabeçalho `CREATE <tipo> [IF NOT EXISTS] <nome>`: sai o `IF NOT EXISTS` e as aspas do nome, se ele for um
      identificador simples (o RENAME acrescenta essas aspas);
    - comentários (`--` até o fim da linha e `/* */`, como o SQLite os lê);
    - diferenças de espaço fora de literal.

    Todo o resto fica como está, inclusive aspas duplas no corpo (`"NOT" NULL` e `NOT NULL` são diferentes), nomes
    entre colchetes ou crases e literais entre aspas simples, com espaços e parênteses de dentro. Nomes não são reescritos: depois do RENAME o sqlite_master já guarda o nome final, então um
    `_new` que sobre em tabela, índice ou trigger aparece como diferença."""
    m = re.match(r'\s*CREATE\s+(TABLE|INDEX|UNIQUE\s+INDEX|TRIGGER|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?("(?:[^"]|"")*"|\w+)',
                 sql, re.I)
    cabeca = ""
    if m:
        nome = m.group(2)
        if nome.startswith('"') and re.fullmatch(r'"[A-Za-z_][A-Za-z0-9_]*"', nome):
            nome = nome[1:-1]
        cabeca = f"CREATE {' '.join(m.group(1).upper().split())} {nome}"
        sql = sql[m.end():]
    partes, fora, i = [cabeca], [], 0

    def fecha_fora():
        partes.append(" ".join("".join(fora).split()).replace("( ", "(").replace(" )", ")"))
        fora.clear()

    def fim_entre(aspa: str, ini: int) -> int:
        fim = ini + 1
        while True:  # aspa dobrada dentro é parte do conteúdo
            fim = sql.index(aspa, fim)
            if sql.startswith(aspa * 2, fim):
                fim += 2
                continue
            return fim

    while i < len(sql):
        if sql.startswith("--", i):
            fim = sql.find("\n", i)
            i = len(sql) if fim < 0 else fim
        elif sql.startswith("/*", i):
            fim = sql.find("*/", i + 2)
            i = len(sql) if fim < 0 else fim + 2  # o SQLite aceita comentário em bloco aberto até o fim
            fora.append(" ")
        elif sql[i] in "'\"`[":
            fim = sql.index("]", i + 1) if sql[i] == "[" else fim_entre(sql[i], i)
            fecha_fora()
            partes.append(sql[i:fim + 1])
            i = fim + 1
        else:
            fora.append(sql[i])
            i += 1
    fecha_fora()
    return " ".join(p for p in partes if p)


class Fake:
    """D1 pela API REST (SQLite com migrações reais + batch transacional) e Twenty local com estado e lixeira."""

    def __init__(self, people=(), schema=False, fail=None, db_path=":memory:"):
        self.db = sqlite3.connect(db_path)
        # O D1 de produção aceita no máximo 5 termos num SELECT composto (medido em 28/11); o SQLite padrão aceita 500.
        self.db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, H.D1_COMPOUND_SELECT_MAX)
        self.db.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, D1_LIKE_PATTERN_MAX)
        self.db.row_factory = sqlite3.Row
        mig_files = sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql"))
        for name in mig_files:
            with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as fh:
                self.db.executescript(fh.read())
        self.people = {p["id"]: copy.deepcopy(p) for p in people}
        self.person_fields = {
            n: {"id": f"pf-{n}", "name": n, "type": t, "options": None}
            for n, t in (("name", "FULL_NAME"), ("emails", "EMAILS"), ("company", "RELATION"))
        }
        self.enroll_obj = None
        self.enroll_fields: dict = {}
        self.enrolls: dict = {}
        self.conv_obj = None
        self.conv_fields: dict = {}
        self.convs: dict = {}
        self.calls = []
        self.fail = fail
        self.n = 0
        self.max_params = 0
        self._db_close = self.db.close
        if schema:
            c = TS.Clients(ENV, self, sleep=lambda s: None)
            meta = H.read_metadata(c)
            H.ensure_schema(c, meta, need_person=True, need_enroll=True)
            self.calls.clear()

    def __del__(self):
        self._db_close()

    # ---- Helpers de seed no D1 ----
    def add_conv(
        self,
        n,
        email,
        action="material_download",
        material="planilha-custos-ia",
        when=None,
        verified=None,
        file=None,
        interests=(CUSTO,),
    ):
        if verified is None:
            verified = 0 if action == "material_request" else 1
        if action == "material_download" and file is None:
            file = material
        if action in ("newsletter_confirm", "site_login"):
            material = None
            file = None
        self.db.execute(
            "INSERT INTO conversions (id, email, action, material, interests, occurred_at, verified, file) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                hexid(n) if isinstance(n, int) else n,
                email,
                action,
                material,
                json.dumps(list(interests)),
                when or ago(days=1),
                verified,
                file,
            ),
        )
        self.db.commit()

    def add_event(
        self,
        ev_id: str,
        email: str,
        signal: str,
        *,
        context: str = "site",
        source: str = "resend",
        ref: str | None = "em-1",
        when: str | None = None,
    ):
        self.db.execute(
            "INSERT INTO engagement_events (id, context, email, signal, source, ref, occurred_at, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ev_id, context, email, signal, source, ref, when or ago(days=1), NOW_ISO),
        )
        self.db.commit()

    def add_subscriber(self, sid: str, email: str, unsubscribed: int = 0):
        self.db.execute(
            "INSERT INTO subscribers (id, email, unsubscribed, origin, joined_at, paid_until, site_confirmed_at, "
            "admin_unsubscribed_at, created_at, run, rev) VALUES (?, ?, ?, 'site', '2026-01-01', '', '', '', '', 'r0', 0)",
            (sid, email, unsubscribed),
        )
        self.db.commit()

    def add_optout(self, email: str, opted_out_at: str = NOW_ISO):
        mac = H.heating_optout_hmac(email, FORGET_KEY)
        self.db.execute("INSERT OR REPLACE INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)", (mac, opted_out_at))
        self.db.commit()

    def add_identity(self, uid: str, email: str, linked_at: str = NOW_ISO):
        self.db.execute(
            "INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)",
            (uid, email.strip().lower(), linked_at),
        )
        self.db.commit()

    def add_purge_queue(
        self,
        uid: str,
        proxima_em: str = NOW_ISO,
        *,
        ga4_pendente: int = 1,
        sessoes: str = "[]",
        bq_tentativas: int = 0,
        bq_concluido: int = 0,
        geracao: int = 0,
        bq_erro: str | None = None,
        criado_em: str | None = None,
    ):
        self.db.execute(
            "INSERT INTO ga4_purge_queue "
            "(uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, geracao, bq_erro, proxima_em, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, geracao, bq_erro, proxima_em, criado_em or NOW_ISO),
        )
        self.db.commit()

    def purge_queue_rows(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT uid, ga4_pendente, sessoes, bq_tentativas, bq_concluido, geracao, proxima_em, criado_em "
                "FROM ga4_purge_queue ORDER BY uid"
            )
        ]

    def meta_get(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return r[0] if r else None

    def meta_set(self, k: str, v: str):
        self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))
        self.db.commit()

    def heating_rows(self) -> dict[tuple[str, str], dict]:
        return {
            (r["context"], r["email"]): dict(r)
            for r in self.db.execute("SELECT * FROM heating ORDER BY context, email")
        }

    def event_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM engagement_events ORDER BY id")]

    # ---- Transporte ----
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
            if "batch" in data:
                results = []
                try:
                    self.db.execute("BEGIN")
                    for item in data["batch"]:
                        assert "@" not in item["sql"], "email só por parâmetro"
                        params = item.get("params") or []
                        self.max_params = max(self.max_params, len(params))
                        assert len(params) <= 100, f"limite de 100 parâmetros excedido: {len(params)}"
                        cur = self.db.execute(item["sql"], params)
                        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                        results.append({"results": rows, "success": True})
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
                return 200, {"success": True, "result": results}
            assert "@" not in data["sql"], "email só por parâmetro"
            params = data.get("params") or []
            self.max_params = max(self.max_params, len(params))
            assert len(params) <= 100, f"limite de 100 parâmetros excedido: {len(params)}"
            cur = self.db.execute(data["sql"], params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            self.db.commit()
            return 200, {"success": True, "result": [{"results": rows, "success": True}]}
        assert headers["Authorization"] == "Bearer tw" and u.netloc == "127.0.0.1:3100"
        st, res = self._twenty(method, u, data)
        return st, json.loads(json.dumps(res))

    def _twenty(self, method, u, data):
        if u.path == "/metadata":
            q = data["query"]
            v = data.get("variables") or {}
            if "createOneObject" in q:
                o = v["input"]["object"]
                if o["nameSingular"] == "courseEnrollment":
                    assert self.enroll_obj is None
                    self.enroll_obj = {"id": "obj-enroll", **o}
                    self.enroll_fields = {"name": {"id": "ef-name", "name": "name", "type": "TEXT", "options": None}}
                    return 200, {"data": {"createOneObject": {"id": "obj-enroll"}}}
                assert self.conv_obj is None
                self.conv_obj = {"id": "obj-conv", **o}
                self.conv_fields = {"name": {"id": "cf-name", "name": "name", "type": "TEXT", "options": None}}
                return 200, {"data": {"createOneObject": {"id": "obj-conv"}}}
            if "createOneField" in q:
                f = v["input"]["field"]
                target = {"obj-person": self.person_fields, "obj-enroll": self.enroll_fields, "obj-conv": self.conv_fields}[f["objectMetadataId"]]
                assert f["name"] not in target, f"campo duplicado {f['name']}"
                opts = [{"id": f"opt-{f['name']}-{o['value']}", **o} for o in f.get("options") or []] or None
                node = {"id": f"fid-{f['name']}", "name": f["name"], "type": f["type"], "options": opts, "_spec": f}
                target[f["name"]] = node
                if f["type"] == "RELATION":
                    rp = f["relationCreationPayload"]
                    assert rp["targetObjectMetadataId"] == "obj-person" and rp["type"] == "MANY_TO_ONE"
                    inv = "inscricoesEmCursos" if f["objectMetadataId"] == "obj-enroll" else "conversoesDoSite"
                    self.person_fields[inv] = {"id": f"pf-{inv}", "name": inv, "type": "RELATION", "options": None}
                return 200, {"data": {"createOneField": {k: val for k, val in node.items() if k != "_spec"}}}
            if "updateOneField" in q:
                i = v["input"]
                for src in (self.person_fields, self.enroll_fields, self.conv_fields):
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
                                 "fields": {"edges": [{"node": {k: val for k, val in f.items() if k != "_spec"}} for f in fields.values()]}}}
            edges = [obj("obj-person", "person", "people", self.person_fields)]
            if self.enroll_obj:
                edges.append(obj("obj-enroll", self.enroll_obj["nameSingular"], self.enroll_obj["namePlural"], self.enroll_fields))
            if self.conv_obj:
                edges.append(obj("obj-conv", self.conv_obj["nameSingular"], self.conv_obj["namePlural"], self.conv_fields))
            return 200, {"data": {"objects": {"edges": edges}}}

        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path in ("/rest/people", "/rest/courseEnrollments", "/rest/siteConversions") and method == "GET":
            store, key = {
                "/rest/people": (self.people, "people"),
                "/rest/courseEnrollments": (self.enrolls, "courseEnrollments"),
                "/rest/siteConversions": (self.convs, "siteConversions"),
            }[u.path]
            items = sorted(store.values(), key=lambda x: x["id"])
            if q.get("filter") is None:
                items = [x for x in items if not x.get("deletedAt")]
            elif q["filter"] != CS.WITH_TRASH:
                return 400, {"messages": ["filtro inesperado"]}
            if "starting_after" in q:
                items = [x for x in items if x["id"] > q["starting_after"]]
            lim = int(q["limit"])
            page = items[:lim]
            return 200, {"data": {key: page}, "pageInfo": {"hasNextPage": len(items) > lim, "endCursor": page[-1]["id"] if page else None}}

        if u.path == "/rest/people" and method == "POST":
            if any((p.get("emails") or {}).get("primaryEmail") == data["emails"]["primaryEmail"] for p in self.people.values()):
                return 400, {"messages": ["A duplicate entry was detected"]}
            for k in data:
                assert k in ("emails", "name") or k in self.person_fields, f"campo inexistente {k}"
            self.n += 1
            pid = f"new-{self.n:04d}"
            self.people[pid] = {"id": pid, "createdAt": f"2026-11-27T12:00:{self.n:02d}.000Z", **data}
            return 201, {"data": {"createPerson": {"id": pid}}}

        if u.path.startswith("/rest/people/") and method == "PATCH":
            pid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            for k in data:
                assert k in self.person_fields, f"campo inexistente {k}"
            self.people[pid].update(data)
            return 200, {"data": {}}

        if u.path == "/rest/courseEnrollments" and method == "POST":
            assert self.enroll_obj
            for k in data:
                assert k in self.enroll_fields or (k == "pessoaId" and "pessoa" in self.enroll_fields), f"campo inexistente {k}"
            if any(e.get("inscricaoId") == data["inscricaoId"] for e in self.enrolls.values()):
                return 400, {"messages": ["A duplicate entry was detected"]}
            self.n += 1
            eid = f"enr-{self.n:04d}"
            self.enrolls[eid] = {"id": eid, **data}
            return 201, {"data": {"createCourseEnrollment": {"id": eid}}}

        if u.path.startswith("/rest/courseEnrollments/") and method == "PATCH":
            eid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            for k in data:
                assert k in self.enroll_fields or (k == "pessoaId" and "pessoa" in self.enroll_fields), f"campo inexistente {k}"
            if "pessoaId" in data and data["pessoaId"] == "":
                return 400, {"messages": ["invalid input syntax for type uuid: \"\""]}
            self.enrolls[eid].update(data)
            return 200, {"data": {}}

        if u.path.startswith("/rest/courseEnrollments/") and method == "DELETE":
            eid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            del self.enrolls[eid]
            return 200, {"data": {"deleteCourseEnrollment": {"id": eid}}}

        if u.path.startswith("/rest/siteConversions/") and method == "DELETE":
            cid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            del self.convs[cid]
            return 200, {"data": {"deleteSiteConversion": {"id": cid}}}

        return 500, {"errors": [{"message": f"rota inesperada {method} {u.path}"}]}

    def twenty_writes(self):
        return [
            (m, p) for m, p, d, _ in self.calls
            if not p.endswith("/query") and m != "GET"
            and not (p == "/metadata" and not any(x in d["query"] for x in ("createOne", "updateOne")))
        ]

    def d1_writes(self):
        out = []
        for _m, p, d, _ in self.calls:
            if not p.endswith("/query"):
                continue
            if "batch" in d:
                out.append("BATCH:" + ";".join(x["sql"].split()[0] for x in d["batch"]))
            elif not d["sql"].lstrip().upper().startswith("SELECT"):
                out.append(d["sql"])
        return out


def person(pid, email, extra=(), created="2026-01-01T00:00:00.000Z", **fields):
    return {
        "id": pid,
        "createdAt": created,
        "name": {"firstName": "Nome", "lastName": "Original"},
        "emails": {"primaryEmail": email, "additionalEmails": list(extra)},
        **fields,
    }


def run_job(
    fake,
    *,
    apply=False,
    now_dt=NOW,
    ler_curso=None,
    ler_ga4_dia=None,
    email_desde=None,
    ga4_desde=None,
    exclude_emails=None,
    extra_env=None,
    ga4_delete=None,
    bq_delete=None,
):
    out = io.StringIO()
    env = {**ENV, **(extra_env or {})}
    if email_desde is not None:
        env["HEATING_EMAIL_DESDE"] = email_desde
    if ga4_desde is not None:
        env["HEATING_GA4_DESDE"] = ga4_desde
    reader = (lambda slug: []) if ler_curso is None else ler_curso
    counts = H.run(
        TS.Clients(env, fake, sleep=lambda s: None),
        apply=apply,
        out=out,
        now_dt=now_dt,
        ler_curso=reader,
        ler_ga4_dia=ler_ga4_dia,
        forget_key=FORGET_KEY,
        exclude_emails=exclude_emails,
        ga4_delete=ga4_delete,
        bq_delete=bq_delete,
    )
    return counts, out.getvalue()


# ------------------------------------------------------------------ Testes unitários da régua

class ReguaTest(unittest.TestCase):
    def _ev(self, ev_id, signal, *, days=0.0, seconds=0.0, context="site", ref="ref-1"):
        return {
            "id": ev_id,
            "context": context,
            "email": "u@corp.com",
            "signal": signal,
            "source": "resend",
            "ref": ref,
            "occurred_at": ago(days=days, seconds=seconds),
        }

    def test_meia_vida_180_dias_e_futuro(self):
        # 12 * 0.5**(30/30) = 6.0; 12 * 0.5**(0/30) = 12.0
        r0 = H.calcular_heating_contexto("site", [self._ev("e1", "material_baixado", days=0)], NOW)
        self.assertEqual((r0["score"], r0["band"]), (12, "frio"))
        r30 = H.calcular_heating_contexto("site", [self._ev("e1", "material_baixado", days=30)], NOW)
        self.assertEqual((r30["score"], r30["band"], r30["signals_list"][0]["pontos"]), (6, "frio", 6.0))
        # Exatamente 180 dias: entra (50 * 0.5**6 = 0.78125 -> round 1).
        r180 = H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", days=180)], NOW)
        self.assertEqual((r180["score"], r180["signals_list"][0]["pontos"]), (1, 0.8))
        # > 180 dias: não entra.
        self.assertIsNone(H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", days=180, seconds=1)], NOW))
        # Evento futuro (> T): não entra.
        self.assertIsNone(H.calcular_heating_contexto("site", [self._ev("e1", "site_agende", seconds=-1)], NOW))

    def test_tetos_por_dia_e_mais_recente(self):
        # newsletter_confirmada: só a mais recente conta (10 hoje, ignora a de 10 dias atrás).
        evs = [
            self._ev("n1", "newsletter_confirmada", days=10, ref=None),
            self._ev("n2", "newsletter_confirmada", days=0, ref=None),
        ]
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual(r["score"], 10)
        # email_aberto: teto 1/dia UTC; email_clicado: teto 2/dia UTC.
        desde = ago(days=30)
        evs2 = [
            self._ev("o1", "email_aberto", days=0, seconds=600, ref="m1"),
            self._ev("o2", "email_aberto", days=0, seconds=300, ref="m2"),
            self._ev("o3", "email_aberto", days=0, seconds=100, ref="m3"),
            # 3 cliques no mesmo dia (refs diferentes para não disparar rajada): só 2 contam -> 8 pts + 1 aberto = 9.
            self._ev("c1", "email_clicado", days=0, seconds=500, ref="m1"),
            self._ev("c2", "email_clicado", days=0, seconds=400, ref="m2"),
            self._ev("c3", "email_clicado", days=0, seconds=300, ref="m3"),
        ]
        r2 = H.calcular_heating_contexto("site", evs2, NOW, email_desde=desde)
        self.assertEqual(r2["score"], 9)
        # site_visita (2, 1/dia), site_engajada (3, 1/dia), site_agende (50, 1/dia), site_contato (50, 1/dia): teto 100.
        evs3 = [
            self._ev("v1", "site_visita", days=0, seconds=10),
            self._ev("v2", "site_visita", days=0, seconds=20),
            self._ev("g1", "site_engajada", days=0, seconds=10),
            self._ev("g2", "site_engajada", days=0, seconds=20),
            self._ev("a1", "site_agende", days=0, seconds=10),
            self._ev("a2", "site_agende", days=0, seconds=20),
            self._ev("k1", "site_contato", days=0, seconds=10),
            self._ev("k2", "site_contato", days=0, seconds=20),
        ]
        r3 = H.calcular_heating_contexto("site", evs3, NOW)
        self.assertEqual((r3["score"], r3["band"]), (100, "quente"))

    def test_teto_passivos_proporcional_site_e_curso(self):
        # Site: 10 dias de site_visita (2) + 10 dias de site_engajada (3) -> sem teto daria ~45; com teto para em 25
        # mantendo a proporção 2:3 (10,0 e 15,0).
        evs = []
        for d in range(10):
            evs.append(self._ev(f"v{d}", "site_visita", days=d))
            evs.append(self._ev(f"g{d}", "site_engajada", days=d))
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual((r["score"], r["band"]), (25, "morno"))
        pts = {s["sinal"]: s["pontos"] for s in r["signals_list"]}
        self.assertEqual(pts, {"site_engajada": 15.0, "site_visita": 10.0})

        # Curso: 40 dias seguidos de email_aberto (1/dia) -> soma sem teto > 26; com teto para em 20 (morno).
        evs_c = [self._ev(f"oc{d}", "email_aberto", days=d, context=CTX_CURSO, ref=f"em-{d}") for d in range(40)]
        rc = H.calcular_heating_contexto(CTX_CURSO, evs_c, NOW, email_desde=ago(days=100))
        self.assertEqual((rc["score"], rc["band"]), (20, "morno"))

    def test_material_maior_ponto_e_materiais_distintos(self):
        # Mesmo material: pedido (2) + download (12) -> só 12 conta; dois downloads -> só o mais recente conta.
        evs = [
            self._ev("p1", "material_pedido", days=0, ref="planilha"),
            self._ev("d1", "material_baixado", days=30, ref="planilha"),
            self._ev("d2", "material_baixado", days=0, ref="planilha"),
            # Outro material: soma!
            self._ev("p2", "material_pedido", days=0, ref="mapa"),
        ]
        r = H.calcular_heating_contexto("site", evs, NOW)
        self.assertEqual((r["score"], r["band"]), (14, "frio"))
        self.assertEqual(
            [(s["sinal"], s["pontos"]) for s in r["signals_list"]],
            [("material_baixado", 12.0), ("material_pedido", 2.0)],
        )

    def test_robo_por_entrega_e_por_rajada(self):
        desde = ago(days=30)
        # Entrega há 100 s; clique 60 s depois da entrega -> descartado; clique 61 s depois -> conta (4 pts).
        evs_entrega = [
            self._ev("ent", "email_entregue", seconds=100, ref="em-a"),
            self._ev("bot", "email_clicado", seconds=40, ref="em-a"),   # 60 s após entrega: descartado
            self._ev("hum", "email_clicado", seconds=39, ref="em-a"),   # 61 s após entrega: mas está a 1 s de `bot`!
        ]
        # Como `bot` (40 s atrás) e `hum` (39 s atrás) têm 1 s entre si no mesmo ref, ambos caem na rajada!
        self.assertIsNone(H.calcular_heating_contexto("site", evs_entrega, NOW, email_desde=desde))

        # Separando o humano (sem rajada): entrega há 200 s, bot 140 s atrás (60 s depois), humano 100 s atrás (100 s depois, 40 s do bot).
        evs_sep = [
            self._ev("ent", "email_entregue", seconds=200, ref="em-a"),
            self._ev("bot", "email_clicado", seconds=140, ref="em-a"),
            self._ev("hum", "email_clicado", seconds=100, ref="em-a"),
        ]
        r = H.calcular_heating_contexto("site", evs_sep, NOW, email_desde=desde)
        self.assertEqual(r["score"], 4)

        # Rajada pura (sem email_entregue): dois cliques a 5 s de distância no mesmo ref -> ambos descartados.
        evs_burst = [
            self._ev("b1", "email_clicado", seconds=100, ref="em-b"),
            self._ev("b2", "email_clicado", seconds=95, ref="em-b"),
        ]
        self.assertIsNone(H.calcular_heating_contexto("site", evs_burst, NOW, email_desde=desde))

        # Dois cliques a 6 s de distância no mesmo ref -> ambos contam (8 pts).
        evs_ok = [
            self._ev("b1", "email_clicado", seconds=100, ref="em-b"),
            self._ev("b2", "email_clicado", seconds=94, ref="em-b"),
        ]
        self.assertEqual(H.calcular_heating_contexto("site", evs_ok, NOW, email_desde=desde)["score"], 8)

    def test_heating_email_desde_e_descadastro(self):
        evs = [
            self._ev("o1", "email_aberto", days=10, ref="m1"),
            self._ev("c1", "email_clicado", days=2, ref="m2"),
        ]
        # Sem HEATING_EMAIL_DESDE: abertura e clique não contam.
        self.assertIsNone(H.calcular_heating_contexto("site", evs, NOW, email_desde=None))
        # Com HEATING_EMAIL_DESDE há 5 dias: só o clique (há 2 dias) conta -> 4 * 0.5**(2/30) = 3.82 -> 4.
        r = H.calcular_heating_contexto("site", evs, NOW, email_desde=ago(days=5))
        self.assertEqual((r["score"], [s["sinal"] for s in r["signals_list"]]), (4, ["email_clicado"]))
        # Exatamente igual a HEATING_EMAIL_DESDE entra; 1 ms antes fica de fora.
        exato = ago(days=5)
        antes_1ms = CS.stamp(NOW - timedelta(days=5, milliseconds=1))
        ev_eq = {**self._ev("eq", "email_clicado", days=5, ref="m-eq"), "occurred_at": exato}
        ev_lt = {**self._ev("lt", "email_clicado", days=5, ref="m-lt"), "occurred_at": antes_1ms}
        r_eq = H.calcular_heating_contexto("site", [ev_eq], NOW, email_desde=exato)
        self.assertIsNotNone(r_eq)
        self.assertEqual([s["sinal"] for s in r_eq["signals_list"]], ["email_clicado"])
        self.assertIsNone(H.calcular_heating_contexto("site", [ev_lt], NOW, email_desde=exato))
        # Descadastrado: perde sinais de email, mas mantém outros sinais.
        evs2 = evs + [self._ev("d1", "material_baixado", days=0, ref="planilha")]
        r_unsub = H.calcular_heating_contexto("site", evs2, NOW, email_desde=ago(days=30), email_bloqueado=True)
        self.assertEqual((r_unsub["score"], [s["sinal"] for s in r_unsub["signals_list"]]), (12, ["material_baixado"]))

    def test_calcular_heating_contexto_usa_datetime_e_dia_utc(self):
        # 1) email_desde sem milissegundos ('...Z') vs occurred_at com milissegundos ('...000Z'):
        #    como texto, '.' < 'Z' falharia; por datetime, são iguais e o evento conta.
        ev_ms = {**self._ev("c1", "email_clicado", ref="m1"), "occurred_at": "2026-11-27T12:00:00.000Z"}
        r = H.calcular_heating_contexto("site", [ev_ms], NOW, email_desde="2026-11-27T12:00:00Z")
        self.assertIsNotNone(r)
        self.assertEqual(r["score"], 4)

        # 2) Dia UTC por _dt (não por slice [:10]): 2026-11-26T22:00:00-03:00 == 2026-11-27T01:00:00Z,
        #    mesmo dia UTC que 2026-11-27T02:00:00.000Z -> teto de 1 email_aberto/dia UTC mantém só 1 (1 pt, não 2).
        evs_tz = [
            {**self._ev("o1", "email_aberto", ref="m1"), "occurred_at": "2026-11-26T22:00:00-03:00"},
            {**self._ev("o2", "email_aberto", ref="m2"), "occurred_at": "2026-11-27T02:00:00.000Z"},
        ]
        r_tz = H.calcular_heating_contexto("site", evs_tz, NOW, email_desde="2026-11-01T00:00:00Z")
        self.assertEqual(r_tz["score"], 1)

        # 3) Ordenação por _dt (não por texto): 2026-11-26T23:00:00-03:00 (2026-11-27T02:00Z) é MAIS recente
        #    que 2026-11-27T01:00:00Z; em newsletter_confirmada (só a mais recente conta), vence a de 02:00Z.
        now_t = datetime(2026, 11, 27, 2, 0, 0, tzinfo=timezone.utc)
        evs_ord = [
            {**self._ev("n-old", "newsletter_confirmada", ref=None), "occurred_at": "2026-11-01T01:00:00Z"},
            {**self._ev("n-new", "newsletter_confirmada", ref=None), "occurred_at": "2026-11-26T23:00:00-03:00"},
        ]
        r_ord = H.calcular_heating_contexto("site", evs_ord, now_t)
        self.assertEqual(r_ord["score"], 10)

    def test_spam_365_dias_faixas_e_top_3(self):
        # Spam de 300 dias e exatamente 365 dias atrás (> 180d, <= 365d) zera mesmo com Agende hoje.
        for sp_days in (300, 365):
            evs = [
                self._ev("sp", "email_spam", days=sp_days, ref="m0"),
                self._ev("ag", "site_agende", days=0, ref=None),
            ]
            r = H.calcular_heating_contexto("site", evs, NOW)
            self.assertEqual(r["score"], 0)
            self.assertEqual(r["band"], "frio")
            self.assertEqual(r["signals"], '[{"sinal":"email_spam","rotulo":"Reclamou de spam: não abordar","pontos":0}]')

        # Spam com mais de 365 dias (> 365d) NÃO zera a nota.
        evs_old_spam = [
            self._ev("sp-old", "email_spam", days=365, seconds=1, ref="m0"),
            self._ev("dl", "material_baixado", days=0, ref="m1"),
        ]
        r_old = H.calcular_heating_contexto("site", evs_old_spam, NOW)
        self.assertEqual((r_old["score"], r_old["band"]), (12, "frio"))

        # Fronteiras exatas das faixas: 14 frio, 15 morno, 39 morno, 40 quente.
        r14 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("p1", "material_pedido", days=0, ref="b"),   # 2 -> 14
        ], NOW)
        self.assertEqual((r14["score"], r14["band"]), (14, "frio"))
        r15 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("g1", "site_engajada", days=0, ref=None),    # 3 -> 15
        ], NOW)
        self.assertEqual((r15["score"], r15["band"]), (15, "morno"))
        r39 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("m2", "material_baixado", days=0, ref="b"),  # 12
            self._ev("m3", "material_baixado", days=0, ref="c"),  # 12
            self._ev("g1", "site_engajada", days=0, ref=None),    # 3 -> 39
        ], NOW)
        self.assertEqual((r39["score"], r39["band"]), (39, "morno"))
        r40 = H.calcular_heating_contexto("site", [
            self._ev("m1", "material_baixado", days=0, ref="a"),  # 12
            self._ev("m2", "material_baixado", days=0, ref="b"),  # 12
            self._ev("m3", "material_baixado", days=0, ref="c"),  # 12
            self._ev("p1", "material_pedido", days=0, ref="d"),   # 2
            self._ev("p2", "material_pedido", days=0, ref="e"),   # 2 -> 40
        ], NOW)
        self.assertEqual((r40["score"], r40["band"]), (40, "quente"))

        # Top 3 sinais em JSON compacto.
        evs_top = [
            self._ev("a1", "site_agende", days=0),       # 50
            self._ev("m1", "material_baixado", days=0, ref="mat1"),  # 12
            self._ev("n1", "newsletter_confirmada", days=0),  # 10
            self._ev("v1", "site_visita", days=0),       # 2 (4º lugar: fica fora do top 3)
        ]
        rt = H.calcular_heating_contexto("site", evs_top, NOW)
        self.assertEqual((rt["score"], rt["band"]), (74, "quente"))
        self.assertEqual(
            [s["sinal"] for s in rt["signals_list"]],
            ["site_agende", "material_baixado", "newsletter_confirmada"],
        )
        self.assertNotIn(", ", rt["signals"])  # JSON compacto

        # Curso: cadastro (10) + 1 aula hoje (5) = 15 -> morno; mais 1 aula ontem + 1 clique (3) + 1 aberto (1) -> 24.
        evs_c = [
            self._ev("cad", "curso_cadastro", days=0, context=CTX_CURSO, ref=None),
            self._ev("au1", "curso_aula", days=0, context=CTX_CURSO, ref="d1"),
            self._ev("au2", "curso_aula", days=0, seconds=60, context=CTX_CURSO, ref="d2"),  # mesmo dia: teto 1/dia
        ]
        rc = H.calcular_heating_contexto(CTX_CURSO, evs_c, NOW)
        self.assertEqual((rc["score"], rc["band"]), (15, "morno"))


# ------------------------------------------------------------------ Testes integrados do job, Twenty e CLI

class HeatingSyncTest(unittest.TestCase):
    def test_separacao_site_e_curso_criacao_de_pessoa_e_inscricao_e_idempotencia(self):
        # Pessoa existente no CRM + nova pessoa vinda do curso (com nome Google) + bounced (não cria Pessoa)
        # + email só do site sem Pessoa (`sem_pessoa`).
        f = Fake(people=[person("p1", "ana@corp.com")])
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(2, "sempessoa@corp.com", action="newsletter_confirm", when=ago(days=0))
        f.add_event("ev-ag", "ana@corp.com", "site_agende", source="ga4", ref=None, when=ago(days=0))

        curso_docs = [
            {
                "email": "Bia@Corp.com",
                "status": "active",
                "source": "google",
                "googleUid": "uid_bia_1",
                "name": "Bia Maria Souza",
                "createdAt": ago(days=0),
                "aulas": [
                    {"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)},
                    {"dayId": "day-02", "completed": False, "updatedAt": ago(days=0)},  # não concluída
                ],
            },
            {
                "email": "bounce@corp.com",
                "status": "bounced",
                "source": "form",
                "googleUid": None,
                "name": "Bounce User",
                "createdAt": ago(days=0),
                "aulas": [],
            },
            {
                # Ana também faz o curso, mas isso NÃO pode mudar a nota dela no contexto `site` (62 = 50 + 12).
                "email": "ana@corp.com",
                "status": "completed",
                "source": "google",
                "googleUid": "uid_ana_1",
                "name": "Outro Nome",
                "createdAt": ago(days=0),
                "aulas": [
                    {"dayId": "day-01", "completed": True, "updatedAt": ago(days=2)},
                    {"dayId": "day-02", "completed": True, "updatedAt": ago(days=1)},
                    {"dayId": "day-03", "completed": True, "updatedAt": ago(days=0)},
                ],
            },
        ]

        # 1) Simulação não escreve nada.
        counts_dry, out_dry = run_job(f, apply=False, ler_curso=lambda slug: curso_docs)
        self.assertEqual((f.twenty_writes(), f.d1_writes()), ([], []))
        self.assertIn("simulação", out_dry)
        self.assertEqual(counts_dry["pessoas_criadas"], 1)  # só Bia (bounce não cria)
        self.assertEqual(counts_dry["sem_pessoa"], 1)       # sempessoa@corp.com
        self.assertEqual(counts_dry["inscricoes_criadas"], 2)  # Ana e Bia

        # 2) --apply grava tudo.
        counts, out = run_job(f, apply=True, ler_curso=lambda slug: curso_docs)
        self.assertIn("gravado", out)
        self.assertEqual(counts["eventos_conversao_novos"], 2)
        self.assertEqual(counts["eventos_curso_novos"], 7)  # 3 cadastros + 4 aulas concluídas
        self.assertEqual(counts["notas_site"], 2)           # ana, sempessoa
        self.assertEqual(counts["notas_curso"], 3)          # ana, bia, bounce
        self.assertEqual(counts["quentes_site"], 1)         # ana (62)
        self.assertEqual(counts["pessoas_atualizadas"], 1)  # ana
        self.assertEqual(counts["pessoas_criadas"], 1)      # bia
        self.assertEqual(counts["inscricoes_criadas"], 2)   # ana, bia
        self.assertEqual(counts["sem_pessoa"], 1)           # sempessoa
        self.assertLessEqual(f.max_params, 100)

        # Ana: nota site = 62 (não contaminada pelo curso!), nome original preservado; sinais no Twenty sem pontos.
        p_ana = f.people["p1"]
        self.assertEqual(p_ana["name"], {"firstName": "Nome", "lastName": "Original"})
        self.assertEqual((p_ana["heatingNota"], p_ana["heatingFaixa"]), (62, "QUENTE"))
        self.assertEqual(p_ana["heatingSinais"], "Clique em Agende; Download de material")
        self.assertIn('"pontos":12.0', f.heating_rows()[("site", "ana@corp.com")]["signals"])

        # Bia: criada com firstName/lastName divididos, sem nota site na Person.
        p_bia = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "bia@corp.com")
        self.assertEqual(p_bia["name"], {"firstName": "Bia", "lastName": "Maria Souza"})
        self.assertNotIn("heatingNota", p_bia)

        # Bounce: não criou Pessoa nem Inscrição no Twenty, mas tem nota de curso no D1 com twenty_person_id nulo.
        self.assertFalse(any(p["emails"]["primaryEmail"] == "bounce@corp.com" for p in f.people.values()))
        hrows = f.heating_rows()
        self.assertEqual(hrows[("site", "ana@corp.com")]["score"], 62)
        self.assertEqual(hrows[("site", "ana@corp.com")]["twenty_person_id"], "p1")
        self.assertEqual(hrows[("site", "sempessoa@corp.com")]["twenty_person_id"], None)
        self.assertEqual(hrows[(CTX_CURSO, "bia@corp.com")]["score"], 15)
        self.assertEqual(hrows[(CTX_CURSO, "bia@corp.com")]["twenty_person_id"], p_bia["id"])
        self.assertEqual(hrows[(CTX_CURSO, "bounce@corp.com")]["twenty_person_id"], None)

        # Inscrição da Bia no Twenty (sinais sem pontos).
        enr_bia = next(e for e in f.enrolls.values() if e["pessoaId"] == p_bia["id"])
        self.assertEqual(
            (enr_bia["curso"], enr_bia["status"], enr_bia["aulasConcluidas"], enr_bia["nota"], enr_bia["faixa"], enr_bia["sinais"]),
            ("CHATGPT_NO_DIA_A_DIA", "ATIVO", 1, 15, "MORNO", "Cadastro no curso; Aula concluída no curso"),
        )

        # 3) Idempotência: segunda rodada sem mudança (mesmo T) não grava nada nem no D1 nem no Twenty!
        n_tw, n_d1 = len(f.twenty_writes()), len(f.d1_writes())
        counts2, _ = run_job(f, apply=True, ler_curso=lambda slug: curso_docs)
        self.assertEqual(len(f.twenty_writes()), n_tw)
        self.assertEqual(len(f.d1_writes()), n_d1)
        self.assertEqual(
            (counts2["eventos_conversao_novos"], counts2["eventos_curso_novos"],
             counts2["pessoas_atualizadas"], counts2["pessoas_criadas"],
             counts2["inscricoes_criadas"], counts2["inscricoes_atualizadas"]),
            (0, 0, 0, 0, 0, 0),
        )

    def test_d1_falso_tem_os_limites_medidos_no_d1_de_producao(self):
        # Os dois limites que derrubaram as rodadas reais de 28/11; se o D1 falso aceitar mais que o D1, os testes mentem.
        f = Fake()
        cinco = " UNION ALL ".join(["SELECT 1"] * H.D1_COMPOUND_SELECT_MAX)
        self.assertEqual(len(f.db.execute(cinco).fetchall()), 5)
        with self.assertRaisesRegex(sqlite3.OperationalError, "too many terms in compound SELECT"):
            f.db.execute(cinco + " UNION ALL SELECT 1").fetchall()
        self.assertEqual(f.db.execute("SELECT 'a' GLOB ?", ("?" * D1_LIKE_PATTERN_MAX,)).fetchone()[0], 0)
        with self.assertRaisesRegex(sqlite3.OperationalError, "LIKE or GLOB pattern too complex"):
            f.db.execute("SELECT 'a' GLOB ?", ("?" * (D1_LIKE_PATTERN_MAX + 1),)).fetchone()

    def test_esquema_final_nao_tem_padrao_de_glob_ou_like_acima_do_limite_do_d1(self):
        # 28/11 (épico #197): a segunda rodada real falhou com "LIKE or GLOB pattern too complex": os CHECKs de data das
        # migrações 0011 a 0014 usavam um padrão de 92 caracteres. A 0015 recria as seis tabelas com padrões curtos.
        f = Fake()
        longos = []
        for nome, sql in f.db.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"):
            for m in re.finditer(r"\b(?:GLOB|LIKE)\s+'((?:[^']|'')*)'", sql, re.I):
                if len(m.group(1).replace("''", "'")) > D1_LIKE_PATTERN_MAX:
                    longos.append((nome, len(m.group(1))))
        self.assertEqual(longos, [])
        tabelas = {r[0] for r in f.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertFalse({t for t in tabelas if t.endswith("_new")})
        indices = {r[0] for r in f.db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
        self.assertLessEqual({
            "engagement_events_context_email_occurred", "engagement_events_email", "engagement_events_signal_ref",
            "heating_context_score_email", "heating_email", "site_identities_email", "ga4_purge_queue_proxima",
        }, indices)
        gatilhos = {r[0] for r in f.db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        self.assertIn("site_identities_enqueue_purge", gatilhos)

    def test_0015_recria_as_seis_tabelas_iguais_as_originais_salvo_o_check_de_data(self):
        # Esquema das seis tabelas com as migrações até a 0014 (sem o limite do D1, que recusaria só os INSERT) e até a
        # 0015. Trocando cada CHECK de data dividido pela forma antiga, o texto tem de ficar idêntico: as oito colunas
        # usam a forma canônica (o teste seguinte prova que ela equivale ao padrão de 92) e, nas seis tabelas, nenhum
        # outro CHECK, coluna, índice ou trigger mudou. Fora delas, o esquema fica idêntico.
        padrao = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z"
        dividido = re.compile(
            r"CHECK \(\s*length\((\w+)\) = 24 AND substr\(\1, 1, 10\) GLOB '\[0-9\]\[0-9\]\[0-9\]\[0-9\]-\[0-9\]\[0-9\]-\[0-9\]\[0-9\]'"
            r"\s+AND substr\(\1, 11, 9\) GLOB 'T\[0-9\]\[0-9\]:\[0-9\]\[0-9\]:\[0-9\]\[0-9\]'"
            r" AND substr\(\1, 20, 5\) GLOB '\.\[0-9\]\[0-9\]\[0-9\]Z'\s*\)")
        tabelas = ("engagement_events", "heating", "heating_optout", "curso_esquecido", "site_identities", "ga4_purge_queue")

        def esquema(ate: str, troca_0015=None, fora=False) -> dict:
            db = sqlite3.connect(":memory:")
            for nome in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n <= ate):
                with open(os.path.join(MIGRATIONS, nome), encoding="utf-8") as fh:
                    texto = fh.read()
                if troca_0015 and nome.startswith("0015"):
                    antes_troca = texto
                    texto = texto.replace(*troca_0015)
                    self.assertNotEqual(texto, antes_troca)
                db.executescript(texto)
            linhas = db.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name %s IN (%s)"
                % ("NOT" if fora else "", ",".join("?" * len(tabelas))), tabelas).fetchall()
            db.close()
            return {(t, n): sql for t, n, sql in linhas}

        normal = sql_normalizado

        antes, depois = esquema("0014_zzz"), esquema("0015_zzz")
        self.assertEqual(set(antes), set(depois))
        self.assertEqual(len([k for k in antes if k[0] == "table"]), 6)
        trocas = 0
        for chave, sql in depois.items():
            sql, n = dividido.subn(lambda m: f"CHECK ({m.group(1)} GLOB '{padrao}')", sql)
            trocas += n
            with self.subTest(objeto=chave):
                self.assertEqual(normal(sql), normal(antes[chave]))
        self.assertEqual(trocas, 8)

        # Além do texto, os atributos que o SQLite leu de cada coluna (tipo, NOT NULL, DEFAULT, chave), para que nenhuma
        # grafia equivalente no texto esconda uma mudança de significado (rodada 4 do #210, Kimi).
        def colunas(ate: str) -> dict:
            db = sqlite3.connect(":memory:")
            for nome in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n <= ate):
                with open(os.path.join(MIGRATIONS, nome), encoding="utf-8") as fh:
                    db.executescript(fh.read())
            r = {t: db.execute(f"PRAGMA table_xinfo({t})").fetchall() for t in tabelas}
            db.close()
            return r

        self.assertEqual(colunas("0015_zzz"), colunas("0014_zzz"))

        # Fora das seis tabelas a 0015 não mexe em nada (revisão do Claude fresh no head c2f8a29).
        fora_antes, fora_depois = esquema("0014_zzz", fora=True), esquema("0015_zzz", fora=True)
        self.assertGreater(len(fora_antes), 10)
        self.assertEqual(fora_depois, fora_antes)
        self.assertNotEqual(esquema("0015_zzz", ("DROP TABLE heating;", "DROP INDEX subscribers_email;\nDROP TABLE heating;"),
                                    fora=True), fora_antes)

        # Rodada 2 do #210 (Kimi): um trigger que grava em `ga4_purge_queue_new` (tabela que não existe depois do RENAME)
        # tem de aparecer como diferença, não ser normalizado.
        mutante = esquema("0015_zzz", ("INSERT INTO ga4_purge_queue (uid,", "INSERT INTO ga4_purge_queue_new (uid,"))
        chave = ("trigger", "site_identities_enqueue_purge")
        self.assertNotEqual(normal(mutante[chave]), normal(antes[chave]))

    def test_normalizacao_do_esquema_nunca_mexe_dentro_de_literal(self):
        # Rodada 1 do #210 (Kimi): `_new`, aspas e `IF NOT EXISTS` saíam também de dentro de literais, e um CHECK
        # `NOT GLOB '*[^0-9a-f_new]*'` (que aceita 'n' * 64) passava por igual ao original. Rodada 2: nenhum nome é
        # reescrito, então `_new` fora de literal também é diferença.
        base = "CREATE TABLE heating_optout (h TEXT CHECK (h NOT GLOB '*[^0-9a-f]*'))"
        self.assertNotEqual(sql_normalizado(base.replace("heating_optout", "heating_optout_new")), sql_normalizado(base))
        self.assertEqual(sql_normalizado('CREATE TABLE IF NOT EXISTS "heating_optout" (h TEXT) -- x\n'),
                         sql_normalizado("CREATE TABLE heating_optout ( h TEXT )"))
        # Rodada 3 do #210 (Kimi): tirar toda aspa dupla fazia `source TEXT "NOT NULL"` (coluna sem NOT NULL) igual ao
        # original. Só identificador simples perde as aspas.
        # Rodada 4 (Kimi): `"NOT" NULL` também deixa de ser restrição; aspas no corpo nunca são tiradas.
        self.assertEqual(sql_normalizado('CREATE TABLE "t" (a TEXT)'), sql_normalizado("CREATE TABLE t (a TEXT)"))
        self.assertEqual(sql_normalizado('CREATE TABLE IF NOT EXISTS t (a TEXT)'), sql_normalizado("CREATE TABLE t (a TEXT)"))
        self.assertNotEqual(sql_normalizado('CREATE TABLE "a""b" (a TEXT)'), sql_normalizado("CREATE TABLE ab (a TEXT)"))
        for original, mutante in (("a TEXT NOT NULL", 'a TEXT "NOT NULL"'), ("a TEXT NOT NULL", 'a TEXT "NOT" NULL'),
                                  ("a TEXT NOT NULL", '"a" TEXT NOT NULL'), ("OLD.uid", '"OLD.uid"')):
            with self.subTest(mutante=mutante):
                aspas = "CREATE TABLE t (a TEXT NOT NULL, b TEXT CHECK (b <> OLD.uid))"
                self.assertNotEqual(sql_normalizado(aspas.replace(original, mutante)), sql_normalizado(aspas))
        # Rodada 5 (Claude fresh): o ajuste de espaço junto de parêntese rodava também dentro de literal.
        for a, b in (("'( x'", "'(x'"), ("'x )'", "'x)'"), ("'*( *'", "'*(*'"), ("[a  b]", "[a b]"), ("`a -- b`", "`a`"),
                     ("[a /* b */]", "[a]")):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(sql_normalizado(f"CREATE TABLE t (c TEXT CHECK (c <> {a}))"),
                                    sql_normalizado(f"CREATE TABLE t (c TEXT CHECK (c <> {b}))"))
        # Revisão do Roger no head 919aa2b: `--` ou `/*` dentro de colchetes ou crases escondia a coluna seguinte.
        for nome in ("[a--b]", "`a/*b`"):
            with self.subTest(nome=nome):
                self.assertNotEqual(sql_normalizado(f"CREATE TABLE t ({nome} TEXT, c TEXT)"),
                                    sql_normalizado(f"CREATE TABLE t ({nome} TEXT, d TEXT)"))
        self.assertEqual(sql_normalizado("CREATE TABLE t ( c TEXT CHECK ( c <> 'x' ) )"),
                         sql_normalizado("CREATE TABLE t (c TEXT CHECK (c <> 'x'))"))
        # Revisão do Roger no head c2f8a29: `--` dentro de `/* */` cortava a linha e escondia a coluna seguinte.
        bloco = "CREATE TABLE t (a TEXT /* -- it's */ , b TEXT)"
        self.assertNotEqual(sql_normalizado(bloco), sql_normalizado(bloco.replace("b TEXT", "c TEXT")))
        self.assertEqual(sql_normalizado(bloco), sql_normalizado("CREATE TABLE t (a TEXT , b TEXT)"))
        self.assertNotEqual(sql_normalizado("CREATE TABLE t (a TEXT) /* aberto"), sql_normalizado("CREATE TABLE t (b TEXT)"))
        self.assertEqual(sql_normalizado("CREATE TABLE t (a TEXT CHECK (a <> '/* x */'))").count("/* x */"), 1)
        for mutante in ("'*[^0-9a-f_new]*'", "'*[^0-9a-f\"]*'", "'*[^0-9a-f IF NOT EXISTS]*'", "'*[^0-9a-f ]*'",
                        "'*[^0-9a-f--]*'", "'*[^0-9a-f'']*'"):
            with self.subTest(mutante=mutante):
                self.assertNotEqual(sql_normalizado(base.replace("'*[^0-9a-f]*'", mutante)), sql_normalizado(base))

    def test_0015_tem_so_os_comandos_esperados_na_ordem(self):
        # Rodadas 3 e 4 do #210 (Kimi e Claude fresh): mais linhas no teste de preservação só deslocam o LIMIT que
        # escapa, e fixar só os INSERT deixava passar um DELETE ou UPDATE avulso. Aqui o arquivo inteiro é fixado: para
        # cada tabela, na ordem, `CREATE TABLE <t>_new (...)`, a cópia exata (`INSERT INTO <t>_new (<colunas da 0014>)`
        # e `SELECT <as mesmas> FROM <t>`, nada depois do FROM), `DROP TABLE <t>`, `ALTER TABLE <t>_new RENAME TO <t>` e
        # os índices de <t>; no fim, o trigger e mais nada. O conteúdo de cada CREATE é conferido no teste estrutural.
        db = sqlite3.connect(":memory:")
        for nome in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n < "0015"):
            with open(os.path.join(MIGRATIONS, nome), encoding="utf-8") as fh:
                db.executescript(fh.read())
        with open(os.path.join(MIGRATIONS, "0015_d1_glob_curto.sql"), encoding="utf-8") as fh:
            texto = re.sub(r"(?m)^--[^\n]*\n", "", fh.read())
        self.assertNotIn("/*", texto)
        self.assertNotIn("--", texto)  # comentário só em linha própria; nenhum no meio de comando
        tabelas = ("engagement_events", "heating", "heating_optout", "curso_esquecido", "site_identities", "ga4_purge_queue")
        antes_trigger, trigger = texto.split("CREATE TRIGGER", 1)
        comandos = [" ".join(c.split()) for c in antes_trigger.split(";") if c.strip()]
        esperados = []
        for t in tabelas:
            cols = ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({t})"))
            n_indices = db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (t,)
            ).fetchone()[0]
            esperados += [rf"CREATE TABLE {t}_new \(.*\)", re.escape(f"INSERT INTO {t}_new ({cols}) SELECT {cols} FROM {t}"),
                          f"DROP TABLE {t}", f"ALTER TABLE {t}_new RENAME TO {t}"]
            esperados += [rf"CREATE INDEX IF NOT EXISTS \w+ ON {t} \([^;]*\)"] * n_indices
        self.assertEqual(len(comandos), len(esperados))
        for n, (comando, padrao) in enumerate(zip(comandos, esperados)):
            with self.subTest(n=n, comando=comando[:60]):
                self.assertRegex(comando, rf"\A{padrao}\Z")
        self.assertRegex(trigger, r"\A IF NOT EXISTS site_identities_enqueue_purge\b")
        self.assertEqual(trigger.count("END;"), 1)
        self.assertEqual(trigger.split("END;", 1)[1].strip(), "")
        db.close()

    def test_0015_preserva_as_linhas_de_cada_tabela_coluna_por_coluna(self):
        # Os seis INSERT ... SELECT da 0015 copiam cada coluna para ela mesma. Duas linhas por tabela: dentro de cada
        # linha, colunas do mesmo tipo têm valores distintos (uma troca de colunas aparece), e as duas linhas diferem em
        # todas as colunas (uma cópia parcial aparece). O teste confere isso antes de usar as linhas.
        db = sqlite3.connect(":memory:")  # sem o limite do D1: até a 0014 o CHECK de data ainda tem o padrão de 92
        db.row_factory = sqlite3.Row
        for nome in sorted(n for n in os.listdir(MIGRATIONS) if n.endswith(".sql") and n < "0015"):
            with open(os.path.join(MIGRATIONS, nome), encoding="utf-8") as fh:
                db.executescript(fh.read())
        d = lambda dia: f"2026-11-{dia:02d}T0{dia % 10}:1{dia % 10}:2{dia % 10}.{dia:03d}Z"
        # Duas linhas por tabela, comparadas pela chave (rodada 2 do #210); o teste anterior fixa o arquivo inteiro, o
        # que pega LIMIT, WHERE ou um DELETE avulso de qualquer tamanho.
        linhas = {
            "engagement_events": ("id", [
                {"id": "ev-1", "context": "site", "email": "a@b.co", "signal": "email_aberto", "source": "resend",
                 "ref": "ref-1", "occurred_at": d(1), "recorded_at": d(2)},
                {"id": "ev-2", "context": CTX_CURSO, "email": "e@f.co", "signal": "curso_aula", "source": "curso",
                 "ref": None, "occurred_at": d(11), "recorded_at": d(12)},
            ]),
            "heating": ("context, email", [
                {"context": "site", "email": "a@b.co", "score": 12, "band": "frio", "signals": '["x"]',
                 "computed_at": d(3), "twenty_person_id": "p-1"},
                {"context": CTX_CURSO, "email": "e@f.co", "score": 70, "band": "quente", "signals": '["y"]',
                 "computed_at": d(13), "twenty_person_id": None},
            ]),
            "heating_optout": ("email_hmac", [
                {"email_hmac": hexid(1), "opted_out_at": d(4)},
                {"email_hmac": hexid(3), "opted_out_at": d(14)},
            ]),
            "curso_esquecido": ("context, email_hmac", [
                {"context": CTX_CURSO, "email_hmac": hexid(2), "forgotten_at": d(5)},
                {"context": "curso:outro-curso", "email_hmac": hexid(4), "forgotten_at": d(15)},
            ]),
            "site_identities": ("uid", [
                {"uid": "u1", "email": "c@d.co", "linked_at": d(6)},
                {"uid": "u3", "email": "g@h.co", "linked_at": d(16)},
            ]),
            "ga4_purge_queue": ("uid", [
                {"uid": "u2", "ga4_pendente": 0, "sessoes": '[["ps", 1]]', "bq_tentativas": 2, "bq_concluido": 1,
                 "geracao": 3, "bq_erro": "falhou", "proxima_em": d(7), "criado_em": d(8)},
                {"uid": "u4", "ga4_pendente": 1, "sessoes": "[]", "bq_tentativas": 4, "bq_concluido": 0,
                 "geracao": 5, "bq_erro": None, "proxima_em": d(17), "criado_em": d(18)},
            ]),
        }
        for tabela, (_, regs) in linhas.items():
            with self.subTest(semente=tabela):
                a, b = regs
                self.assertEqual(set(a), set(b))
                self.assertTrue(all(a[c] != b[c] for c in a), [c for c in a if a[c] == b[c]])
                for linha in regs:
                    por_tipo: dict = {}
                    for c, v in linha.items():
                        if v is not None:
                            por_tipo.setdefault(type(v), []).append(v)
                    self.assertTrue(all(len(vs) == len(set(vs)) for vs in por_tipo.values()), linha)
        for tabela, (_, regs) in linhas.items():
            for linha in regs:
                cols = ", ".join(linha)
                db.execute(f"INSERT INTO {tabela} ({cols}) VALUES ({', '.join('?' * len(linha))})",
                           tuple(linha.values()))
        with open(os.path.join(MIGRATIONS, "0015_d1_glob_curto.sql"), encoding="utf-8") as fh:
            db.executescript(fh.read())
        for tabela, (chave, regs) in linhas.items():
            with self.subTest(tabela=tabela):
                esperado = sorted(regs, key=lambda r: tuple(r[c] for c in chave.split(", ")))
                self.assertEqual([dict(r) for r in db.execute(f"SELECT * FROM {tabela} ORDER BY {chave}")], esperado)
        db.close()

    def test_check_de_data_dividido_aceita_e_recusa_os_mesmos_valores_de_antes(self):
        # O CHECK da 0015 (comprimento 24 e três GLOB curtos) precisa aceitar exatamente o que o padrão de 92 aceitava.
        antigo = sqlite3.connect(":memory:")  # sem limite, para avaliar o padrão antigo
        padrao = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z"
        validos = [NOW_ISO, "2026-11-28T00:00:00.000Z", "9999-99-99T99:99:99.999Z"]
        invalidos = [
            "", "2026-11-28", "2026-11-28T13:53:07Z", "2026-11-28T13:53:07.123", "2026-11-28T13:53:07.123ZZ",
            "2026-11-28 13:53:07.123Z", "2026-11-28T13:53:07.12aZ", "x2026-11-28T13:53:07.123Z", "2026/11/28T13:53:07.123Z",
            "2026-11-28T13:53:07.123z", "2026-11-28T13-53-07.123Z", "2026-11-28T13:53:07.1234Z",
            "2026-11-28T13:53:07,123Z",  # no GLOB o ponto é literal
        ]
        f = Fake()
        for i, valor in enumerate(validos + invalidos):
            esperado = bool(antigo.execute("SELECT ? GLOB ?", (valor, padrao)).fetchone()[0])
            self.assertEqual(esperado, valor in validos, valor)
            for tabela, sql, args in (
                ("heating_optout", "INSERT INTO heating_optout (email_hmac, opted_out_at) VALUES (?, ?)", (hexid(i),)),
                ("site_identities", "INSERT INTO site_identities (uid, email, linked_at) VALUES (?, ?, ?)",
                 (f"u{i}", "a@b.co")),
                ("curso_esquecido", "INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, ?, ?)",
                 (CTX_CURSO, hexid(i))),
            ):
                with self.subTest(tabela=tabela, valor=valor):
                    if esperado:
                        f.db.execute(sql, (*args, valor))
                    else:
                        with self.assertRaisesRegex(sqlite3.IntegrityError, "CHECK constraint failed"):
                            f.db.execute(sql, (*args, valor))
        antigo.close()

    def test_insert_condicional_respeita_o_limite_de_select_composto_do_d1(self):
        # 28/11 (épico #197): a primeira rodada real falhou no D1 com "too many terms in compound SELECT" (11 linhas num
        # UNION ALL). O D1 falso tem o mesmo teto de 5; um contato com muitas conversões grava em lotes de 5.
        self.assertEqual(H.D1_COMPOUND_SELECT_MAX, 5)
        self.assertLessEqual(H.EVENT_CHUNK, H.D1_COMPOUND_SELECT_MAX)
        self.assertLessEqual(H.HEATING_CHUNK, H.D1_COMPOUND_SELECT_MAX)
        f = Fake(people=[person("p1", "muitos@corp.com")], schema=True)
        for i in range(1, 24):
            f.add_conv(i, "muitos@corp.com", action="material_download", when=ago(days=i % 20 + 1))
        counts, _ = run_job(f, apply=True)
        n = f.db.execute("SELECT count(*) FROM engagement_events WHERE email = 'muitos@corp.com'").fetchone()[0]
        self.assertEqual(n, 23)
        self.assertEqual(counts["eventos_conversao_novos"], 23)
        with self.assertRaises(ValueError):
            H._insert_events_sql([{"id": str(i)} for i in range(6)], FORGET_KEY)
        with self.assertRaises(ValueError):
            H._insert_heating_sql([("site", "a@b.co", 1, "FRIO", "[]", NOW_ISO, None)] * 6, FORGET_KEY)

    def test_login_no_site_nao_pontua_nem_conta_como_conversao_invalida(self):
        # site_login (épico #197): conversão válida para o CRM, sem sinal na régua; não gera evento nem alerta.
        f = Fake(people=[person("p1", "login@corp.com")], schema=True)
        f.add_conv(1, "login@corp.com", action="site_login", interests=(), when=ago(days=1))
        f.add_conv(2, "login@corp.com", action="material_download", when=ago(days=1))
        f.add_conv(3, "login@corp.com", action="material_request", when=ago(days=2))
        f.add_conv(4, "login@corp.com", action="newsletter_confirm", when=ago(days=3))
        counts, _ = run_job(f, apply=True)
        self.assertEqual(counts["conversao_invalida"], 0)
        evs = [r for r in f.db.execute("SELECT id, signal FROM engagement_events WHERE email = 'login@corp.com'")]
        # As três ações de antes continuam pontuando, cada uma no seu sinal; só o login fica de fora.
        self.assertEqual(sorted(r[1] for r in evs), ["material_baixado", "material_pedido", "newsletter_confirmada"])
        self.assertNotIn("cv:" + hexid(1), [r[0] for r in evs])

    def test_oposicao_exclusao_e_retencao(self):
        f = Fake(
            people=[
                person(
                    "p-opt", "opt@corp.com",
                    heatingNota=50, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO,
                ),
                person("p-ex", "gui@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-opt"] = {
            "id": "e-opt",
            "inscricaoId": H.inscricao_id(SLUG, "opt@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=10),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=5),
            "nota": 20,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso (10,0)",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p-opt",
        }
        # Oposição registrada no D1.
        f.add_optout("opt@corp.com")
        f.add_event("ev-opt-site", "opt@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))
        f.add_event("ev-opt-cur", "opt@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        # Excluídos: HEATING_EXCLUDE + domínios de teste (eventos ficam, mas não recebem nota).
        for i, em in enumerate(
            ("gui@corp.com", "a@example.com", "b@example.org", "c@example.net", "d@exemplo.com",
             "e@foo.test", "f@foo.invalid", "g@sub.example"),
            start=1,
        ):
            f.add_event(f"ev-ex-{i}", em, "site_agende", context="site", source="ga4", ref=None, when=ago(days=1))
        # Retenção: evento com 366 dias é apagado; evento com 364 dias fica.
        f.add_event("ev-velho", "ok@corp.com", "site_visita", context="site", source="ga4", ref=None, when=ago(days=366))
        f.add_event("ev-recente", "ok@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))

        curso_docs = [{
            "email": "opt@corp.com",
            "status": "active",
            "googleUid": "uid_opt",
            "name": "Opt User",
            "createdAt": ago(days=10),
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ago(days=5)}],
        }]
        counts, _ = run_job(f, apply=True, ler_curso=lambda s: curso_docs, exclude_emails={"gui@corp.com"})
        self.assertEqual(counts["oposicao_aplicada"], 1)
        self.assertEqual(counts["excluidos"], 8)
        self.assertEqual(counts["retencao_apagados"], 1)
        self.assertEqual(counts["pessoas_limpas"], 1)
        self.assertEqual(counts["notas_site"], 1)  # só ok@corp.com

        # Eventos de optout e de retenção sumiram; eventos de excluídos ficaram.
        ev_ids = set(f.event_ids())
        self.assertNotIn("ev-opt-site", ev_ids)
        self.assertNotIn("ev-opt-cur", ev_ids)
        self.assertNotIn("ev-velho", ev_ids)
        self.assertIn("ev-ex-1", ev_ids)
        self.assertIn("ev-recente", ev_ids)

        # Person do optout teve os 4 campos heating* limpos; Inscrição teve nota/faixa/sinais limpos mantendo o resto.
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(f.people["p-opt"][k])
        eopt = f.enrolls["e-opt"]
        self.assertEqual((eopt["nota"], eopt["faixa"], eopt["sinais"], eopt["calculadoEm"]), (None, None, None, None))
        self.assertEqual((eopt["status"], eopt["aulasConcluidas"]), ("ATIVO", 1))

    def test_curso_indisponivel_mantem_eventos_e_nao_mexe_nas_inscricoes(self):
        f = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        f.add_event("cu-prev", "ana@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=0))
        f.enrolls["e1"] = {
            "id": "e1",
            "inscricaoId": H.inscricao_id(SLUG, "ana@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=10),
            "aulasConcluidas": 3,
            "ultimaAula": ago(days=5),
            "nota": 99,
            "faixa": "QUENTE",
            "sinais": "antigo",
            "calculadoEm": ago(days=5),
            "pessoaId": "p1",
        }
        before_enroll = copy.deepcopy(f.enrolls["e1"])

        def fail_curso(_slug):
            raise H.CursoIndisponivel("sem acesso ao Firestore")

        counts, out = run_job(f, apply=True, ler_curso=fail_curso)
        self.assertEqual(counts["curso_indisponivel"], 1)
        self.assertIn("curso_indisponivel: 1", out)
        # Evento já gravado ainda gera nota de curso no D1, mas a Inscrição no Twenty não é tocada.
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ana@corp.com")]["score"], 10)
        self.assertEqual(f.enrolls["e1"], before_enroll)
        self.assertEqual([c for c in f.twenty_writes() if "courseEnrollments" in c[1]], [])

        # Sem ler_curso injetado e sem HEATING_GCP_SA_FILE: também conta curso_indisponivel sem quebrar.
        out2 = io.StringIO()
        counts2 = H.run(TS.Clients(ENV, f, sleep=lambda s: None), apply=False, out=out2, now_dt=NOW, forget_key=FORGET_KEY)
        self.assertEqual(counts2["curso_indisponivel"], 1)

        # Erros que não são de leitura do curso (ex.: RuntimeError de programação) não são engolidos.
        with self.assertRaises(RuntimeError):
            run_job(f, apply=False, ler_curso=lambda _s: (_ for _ in ()).throw(RuntimeError("bug")))

    def test_lixeira_nao_recria_pessoa_nem_inscricao(self):
        f = Fake(
            people=[
                person("p-lixo", "lixo@corp.com", deletedAt="2026-11-20T00:00:00.000Z"),
                person("p-ok", "ok@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-lixo"] = {
            "id": "e-lixo",
            "inscricaoId": H.inscricao_id(SLUG, "ok@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "deletedAt": "2026-11-20T00:00:00.000Z",
            "pessoaId": "p-ok",
        }
        f.add_event("ev-lixo", "lixo@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))
        docs = [
            {"email": "lixo@corp.com", "status": "active", "googleUid": "u1", "name": "Lixo", "createdAt": ago(days=0), "aulas": []},
            {"email": "ok@corp.com", "status": "active", "googleUid": "u2", "name": "Ok", "createdAt": ago(days=0), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(counts["pessoa_na_lixeira"], 1)
        self.assertEqual(counts["pessoas_criadas"], 0)
        self.assertEqual(counts["inscricoes_criadas"], 0)
        self.assertEqual(counts["inscricoes_atualizadas"], 0)
        self.assertEqual([c for c in f.twenty_writes() if c[1] == "/rest/courseEnrollments/e-lixo"], [])
        self.assertEqual(len(f.people), 2)
        self.assertEqual(len(f.enrolls), 1)
        self.assertIsNone(f.heating_rows()[("site", "lixo@corp.com")]["twenty_person_id"])

    def test_descadastro_site_e_curso_no_job(self):
        f = Fake(people=[person("p1", "u@corp.com")], schema=True)
        f.add_subscriber("s1", "u@corp.com", unsubscribed=1)
        f.add_event("e-site-clk", "u@corp.com", "email_clicado", context="site", ref="m1", when=ago(days=0))
        f.add_event("e-cur-clk", "u@corp.com", "email_clicado", context=CTX_CURSO, ref="m2", when=ago(days=0))
        docs = [{"email": "u@corp.com", "status": "unsubscribed", "googleUid": "u1", "name": "U", "createdAt": ago(days=0), "aulas": []}]
        run_job(f, apply=True, ler_curso=lambda _s: docs, email_desde=ago(days=30))
        hrows = f.heating_rows()
        # No site: só tinha clique, e está descadastrado dos Expert Insights -> sem nota no site.
        self.assertNotIn(("site", "u@corp.com"), hrows)
        # No curso: perdeu o clique (3 pts), ficou só com curso_cadastro (10 pts).
        self.assertEqual(hrows[(CTX_CURSO, "u@corp.com")]["score"], 10)

    def test_optout_cli_e_simulacao(self):
        f = Fake(
            people=[person(
                "p1", "segredo@corp.com",
                heatingNota=45, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO,
            )],
            schema=True,
        )
        f.enrolls["e1"] = {
            "id": "e1",
            "inscricaoId": H.inscricao_id(SLUG, "segredo@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=1),
            "nota": 20,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso (10,0)",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p1",
        }
        f.add_event("e-s", "segredo@corp.com", "site_agende", context="site", source="ga4", ref=None, when=ago(days=1))
        f.add_event("e-c", "segredo@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'segredo@corp.com', 45, 'quente', '[]', ?, 'p1'),"
            "(?, 'segredo@corp.com', 20, 'morno', '[]', ?, 'p1')",
            (NOW_ISO, CTX_CURSO, NOW_ISO),
        )
        f.db.commit()

        # Simulação não grava nada.
        out_dry = io.StringIO()
        c_dry = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "Segredo@Corp.com\n", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["eventos_apagados"], c_dry["notas_apagadas"], c_dry["pessoas_limpas"], c_dry["inscricoes_limpas"]), (2, 2, 1, 1))
        self.assertNotIn("segredo", out_dry.getvalue().lower())
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 0)

        # --apply grava marca, apaga todos os contextos no D1 e limpa Person + nota/faixa/sinais da Inscrição.
        out = io.StringIO()
        H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "Segredo@Corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertIn("oposição registrada", out.getvalue())
        self.assertNotIn("segredo", out.getvalue().lower())
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 1)
        self.assertEqual(f.event_ids(), [])
        self.assertEqual(f.heating_rows(), {})
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(f.people["p1"][k])
        self.assertEqual((f.enrolls["e1"]["nota"], f.enrolls["e1"]["faixa"], f.enrolls["e1"]["sinais"], f.enrolls["e1"]["calculadoEm"]),
                         (None, None, None, None))
        self.assertEqual(f.enrolls["e1"]["aulasConcluidas"], 2)  # resto da Inscrição mantido

    def test_optout_limpa_pessoa_com_email_alterado_via_heating_e_conversions(self):
        # P2 / Claude P4: --optout resolve Pessoas ativas também pelos twenty_person_id salvos em `heating`
        # (todos os contextos) e em `conversions` antes de apagar o D1, ignorando Pessoas na lixeira e ids nulos.
        f = Fake(
            people=[
                person("p-site", "novo-site@corp.com", heatingNota=45, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO),
                person("p-curso", "novo-curso@corp.com", heatingNota=30, heatingFaixa="MORNO", heatingSinais="y", heatingCalculadoEm=NOW_ISO),
                person("p-conv", "novo-conv@corp.com", heatingNota=12, heatingFaixa="FRIO", heatingSinais="z", heatingCalculadoEm=NOW_ISO),
                person(
                    "p-lixo", "novo-lixo@corp.com",
                    deletedAt="2026-11-20T00:00:00.000Z",
                    heatingNota=50, heatingFaixa="QUENTE", heatingSinais="w", heatingCalculadoEm=NOW_ISO,
                ),
            ],
            schema=True,
        )
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'antigo@corp.com', 45, 'quente', '[]', ?, 'p-site'),"
            "(?, 'antigo@corp.com', 20, 'morno', '[]', ?, 'p-curso')",
            (NOW_ISO, CTX_CURSO, NOW_ISO),
        )
        f.add_conv(1, "antigo@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=1))
        f.add_conv(2, "antigo@corp.com", action="newsletter_confirm", when=ago(days=1))
        f.add_conv(3, "antigo@corp.com", action="material_request", material="planilha-custos-ia", when=ago(days=1))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-conv' WHERE id = ?", (hexid(1),))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-lixo' WHERE id = ?", (hexid(2),))
        f.db.execute("UPDATE conversions SET twenty_person_id = NULL WHERE id = ?", (hexid(3),))
        f.db.commit()

        # Simulação conta as 3 Pessoas ativas (site, curso e conversions) e ignora a da lixeira, sem gravar nada.
        out_dry = io.StringIO()
        c_dry = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "antigo@corp.com", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["notas_apagadas"], c_dry["pessoas_limpas"]), (2, 3))
        self.assertEqual(f.twenty_writes(), [])
        self.assertNotIn("antigo", out_dry.getvalue().lower())

        # --apply limpa as 3 Pessoas ativas (mesmo com email alterado no Twenty), não toca na lixeira e apaga D1.
        out = io.StringIO()
        c_app = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "antigo@corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertEqual((c_app["notas_apagadas"], c_app["pessoas_limpas"]), (2, 3))
        self.assertNotIn("antigo", out.getvalue().lower())
        for pid in ("p-site", "p-curso", "p-conv"):
            for k in H.PERSON_HEATING_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        self.assertEqual(f.people["p-lixo"]["heatingNota"], 50)
        patched = {p for m, p, _, _ in f.calls if m == "PATCH"}
        self.assertEqual(patched, {"/rest/people/p-site", "/rest/people/p-curso", "/rest/people/p-conv"})
        self.assertEqual(f.heating_rows(), {})
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 1)

    def test_forget_curso_impede_rematerializacao_e_preserva_site(self):
        f = Fake(people=[person("p1", "aluna@corp.com")], schema=True)
        f.add_conv(1, "aluna@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        # Evento de site vindo do webhook (não regenerado a partir de conversions) e evento de outro curso: devem sobreviver.
        f.add_event("wh-site-aluna", "aluna@corp.com", "email_clicado", context="site", source="resend", ref="em-1", when=ago(days=0))
        f.add_event("cu-outro-aluna", "aluna@corp.com", "curso_cadastro", context="curso:outro-curso", source="curso", ref=None, when=ago(days=0))
        docs = [{
            "email": "aluna@corp.com",
            "status": "active",
            "googleUid": "uid_aluna",
            "name": "Aluna",
            "createdAt": ago(days=0),
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ago(days=0)}],
        }]
        run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(len(f.enrolls), 1)
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())
        self.assertIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())

        # Coloca a Inscrição na lixeira para garantir que --forget-curso também apaga da lixeira de vez.
        only_enr = next(iter(f.enrolls.values()))
        only_enr["deletedAt"] = NOW_ISO

        # Simulação não apaga.
        out_dry = io.StringIO()
        c_dry = H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=False, out=out_dry, forget_key=FORGET_KEY)
        self.assertEqual((c_dry["inscricoes_no_crm"], c_dry["notas_apagadas"]), (1, 1))
        self.assertIn("Firestore e no Identity Platform", out_dry.getvalue())
        self.assertNotIn("aluna", out_dry.getvalue().lower())

        # --apply grava curso_esquecido, apaga Inscrição de vez, apaga eventos/nota do curso, preserva site e Pessoa.
        out = io.StringIO()
        H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=True, out=out, forget_key=FORGET_KEY)
        self.assertIn("curso removido", out.getvalue())
        self.assertIn("Firestore e no Identity Platform", out.getvalue())
        self.assertNotIn("aluna", out.getvalue().lower())
        self.assertEqual(f.enrolls, {})
        self.assertIn("p1", f.people)  # Pessoa preservada
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())
        self.assertNotIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())
        self.assertEqual(f.db.execute("SELECT count(*) FROM curso_esquecido").fetchone()[0], 1)
        # Eventos de outros contextos (site do webhook e outro curso) continuam em engagement_events; os do curso saíram.
        rem_events = {r["id"]: r["context"] for r in f.db.execute("SELECT id, context FROM engagement_events")}
        self.assertIn("wh-site-aluna", rem_events)
        self.assertIn("cu-outro-aluna", rem_events)
        self.assertNotIn(CTX_CURSO, rem_events.values())

        # Próxima rodada do job com os mesmos dados ainda no Firestore NÃO rematerializa o curso esquecido
        # (a única nota de curso que permanece é a de `curso:outro-curso`).
        counts_next, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual((counts_next["eventos_curso_novos"], counts_next["inscricoes_criadas"], counts_next["notas_curso"]), (0, 0, 1))
        self.assertEqual(f.enrolls, {})
        self.assertNotIn((CTX_CURSO, "aluna@corp.com"), f.heating_rows())
        self.assertIn(("curso:outro-curso", "aluna@corp.com"), f.heating_rows())
        self.assertIn(("site", "aluna@corp.com"), f.heating_rows())

    def test_forget_curso_com_sobra_no_twenty_mantem_d1_intacto(self):
        f = Fake(people=[person("p1", "aluna@corp.com")], schema=True)
        f.enrolls["e1"] = {"id": "e1", "inscricaoId": H.inscricao_id(SLUG, "aluna@corp.com"), "pessoaId": "p1"}
        f.add_event("cu-1", "aluna@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        real = f._twenty
        f._twenty = lambda m, u, d: (200, {"data": {}}) if m == "DELETE" else real(m, u, d)
        with self.assertRaisesRegex(H.SyncError, "o D1 ficou intacto"):
            H.forget_curso(TS.Clients(ENV, f, sleep=lambda s: None), SLUG, "aluna@corp.com", apply=True, forget_key=FORGET_KEY)
        self.assertEqual(f.event_ids(), ["cu-1"])
        self.assertEqual(f.db.execute("SELECT count(*) FROM curso_esquecido").fetchone()[0], 1)

    def test_oposicao_concorrente_durante_a_rodada_condicional_e_releitura(self):
        # Codex P1: (A) INSERT condicional em engagement_events e heating não grava quem recebeu marca no meio;
        # (B) releitura de heating_optout no fim da rodada limpa D1 e Twenty (Pessoa e Inscrições, existentes e criadas).
        class ConcurrentOptoutFake(Fake):
            def __init__(self, *args, **kwargs):
                self.injected_ev = False
                self.injected_ht = False
                self.injected_late = False
                self.ev_after_insert = None
                self.ht_after_batch = None
                super().__init__(*args, **kwargs)

            def __call__(self, method, url, headers, body):
                data = json.loads(body) if body else None
                u = urllib.parse.urlparse(url)
                if u.netloc == "api.cloudflare.com" and isinstance(data, dict):
                    sql = data.get("sql", "")
                    if not self.injected_ev and sql.startswith("INSERT INTO engagement_events"):
                        self.injected_ev = True
                        # Simula POST /prioridade gravando heating_optout logo antes do INSERT de eventos.
                        self.add_optout("corr-ev@corp.com")
                        res = super().__call__(method, url, headers, body)
                        self.ev_after_insert = [
                            r[0] for r in self.db.execute("SELECT email FROM engagement_events WHERE email = 'corr-ev@corp.com'")
                        ]
                        return res
                    if not self.injected_ht and "batch" in data and data["batch"][0]["sql"] == "DELETE FROM heating":
                        self.injected_ht = True
                        # Simula POST /prioridade gravando heating_optout logo antes do batch de heating.
                        self.add_optout("corr-ht@corp.com")
                        res = super().__call__(method, url, headers, body)
                        self.ht_after_batch = [
                            r[0] for r in self.db.execute("SELECT email FROM heating WHERE email = 'corr-ht@corp.com'")
                        ]
                        # E simula outro email (com Pessoa + Inscrição criadas nesta rodada) fazendo oposição
                        # imediatamente após a troca de heating, antes da releitura final.
                        self.add_optout("late-new@corp.com")
                        self.injected_late = True
                        return res
                return super().__call__(method, url, headers, body)

        f = ConcurrentOptoutFake(
            people=[
                person("p-ev", "corr-ev@corp.com"),
                person("p-ht", "corr-ht@corp.com"),
                person("p-ok", "ok@corp.com"),
            ],
            schema=True,
        )
        f.enrolls["e-ev"] = {
            "id": "e-ev",
            "inscricaoId": H.inscricao_id(SLUG, "corr-ev@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=2),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": None,
            "sinais": None,
            "calculadoEm": None,
            "pessoaId": "p-ev",
        }
        f.add_conv(1, "corr-ev@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(2, "corr-ht@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(3, "late-new@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(4, "ok@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        # Vínculo uid -> email já existente: a oposição tardia também apaga `site_identities`, e o trigger da 0014
        # enfileira o uid para remoção no Google (Claude P3 da rodada 2 do #204).
        f.add_identity("UidLateNew", "late-new@corp.com")
        f.add_identity("UidOk", "ok@corp.com")
        docs = [
            {"email": "corr-ev@corp.com", "status": "active", "googleUid": "u1", "name": "Ev", "createdAt": ago(days=2), "aulas": []},
            {"email": "corr-ht@corp.com", "status": "active", "googleUid": "u2", "name": "Ht", "createdAt": ago(days=2), "aulas": []},
            {"email": "late-new@corp.com", "status": "active", "googleUid": "u3", "name": "Late New", "createdAt": ago(days=0), "aulas": []},
        ]

        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertTrue(f.injected_ev and f.injected_ht and f.injected_late)
        # (A) O próprio SQL condicional impediu a inserção em engagement_events e heating antes mesmo da releitura!
        self.assertEqual(f.ev_after_insert, [])
        self.assertEqual(f.ht_after_batch, [])
        # (B) Ao final da rodada, D1 e Twenty (Pessoa e Inscrições) dos 3 emails estão limpos; ok@corp.com intacto.
        self.assertEqual(counts["oposicao_aplicada"], 3)
        for em in ("corr-ev@corp.com", "corr-ht@corp.com", "late-new@corp.com"):
            self.assertEqual(f.db.execute("SELECT count(*) FROM engagement_events WHERE email = ?", (em,)).fetchone()[0], 0, em)
            self.assertEqual(f.db.execute("SELECT count(*) FROM heating WHERE email = ?", (em,)).fetchone()[0], 0, em)
        for pid in ("p-ev", "p-ht"):
            for k in H.PERSON_HEATING_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        p_late = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "late-new@corp.com")
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(p_late[k], k)
        for enr in f.enrolls.values():
            if enr["pessoaId"] in ("p-ev", "p-ht", p_late["id"]):
                for k in ("nota", "faixa", "sinais", "calculadoEm"):
                    self.assertIsNone(enr[k], (enr["id"], k))
        # Contato sem oposição preservado.
        self.assertEqual(f.people["p-ok"]["heatingNota"], 12)
        self.assertEqual(f.heating_rows()[("site", "ok@corp.com")]["score"], 12)
        # `site_identities` do email com oposição tardia apagada e uid enfileirado; o vínculo sem oposição fica.
        self.assertEqual([r[0] for r in f.db.execute("SELECT uid FROM site_identities ORDER BY uid")], ["UidOk"])
        fila = {r["uid"]: r for r in f.purge_queue_rows()}
        self.assertEqual(sorted(fila), ["UidLateNew"])
        self.assertEqual(fila["UidLateNew"]["ga4_pendente"], 1)
        self.assertEqual(fila["UidLateNew"]["bq_concluido"], 0)

    def test_oposicao_concorrente_5f_limpa_pessoa_com_email_alterado_via_heating_e_conversions(self):
        # Passo 5f: oposição que chega durante a rodada também resolve Pessoas ativas pelos twenty_person_id salvos em
        # `heating` (site e curso) e `conversions` (mesmo de conversão antiga) e por `created_pid_by_email`, ignorando lixeira.
        class LateRenamedOptoutFake(Fake):
            def __init__(self, *args, **kwargs):
                self.injected = False
                super().__init__(*args, **kwargs)

            def __call__(self, method, url, headers, body):
                data = json.loads(body) if body else None
                u = urllib.parse.urlparse(url)
                if (
                    u.netloc == "api.cloudflare.com"
                    and isinstance(data, dict)
                    and not self.injected
                    and "batch" in data
                    and data["batch"][0]["sql"] == "DELETE FROM heating"
                ):
                    self.injected = True
                    res = super().__call__(method, url, headers, body)
                    self.add_optout("antigo@corp.com")
                    self.add_optout("so-conv@corp.com")
                    self.add_optout("late-new@corp.com")
                    return res
                return super().__call__(method, url, headers, body)

        f = LateRenamedOptoutFake(
            people=[
                # Mantém nota existente na rodada (had_before=True, não entra em person_clears).
                person("p-site", "novo-site@corp.com", heatingNota=12, heatingFaixa="FRIO", heatingSinais="Download de material", heatingCalculadoEm=NOW_ISO),
                # Ganha nota nova na rodada (entra em person_updates).
                person("p-curso", "novo-curso@corp.com"),
                # Mantém nota existente e é achada só pelo twenty_person_id em conversions (sem linha em heating).
                person("p-conv", "novo-conv@corp.com", heatingNota=12, heatingFaixa="FRIO", heatingSinais="Download de material", heatingCalculadoEm=NOW_ISO),
                # Na lixeira: nunca deve ser tocada.
                person(
                    "p-lixo", "novo-lixo@corp.com",
                    deletedAt="2026-11-20T00:00:00.000Z",
                    heatingNota=50, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO,
                ),
            ],
            schema=True,
        )
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) VALUES "
            "('site', 'antigo@corp.com', 45, 'quente', '[]', ?, 'p-site'),"
            "(?, 'antigo@corp.com', 20, 'morno', '[]', ?, 'p-curso')",
            (NOW_ISO, CTX_CURSO, NOW_ISO),
        )
        # Conversões antigas fora da retenção: so-conv@corp.com só existe em conversions (sem linha em heating nem
        # em engagement_events), provando a inclusão de raw_convs em candidate_emails no passo 5f.
        f.add_conv(1, "so-conv@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=400))
        f.add_conv(2, "antigo@corp.com", action="newsletter_confirm", when=ago(days=400))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-conv' WHERE id = ?", (hexid(1),))
        f.db.execute("UPDATE conversions SET twenty_person_id = 'p-lixo' WHERE id = ?", (hexid(2),))
        # Eventos atuais nos novos emails para manter/gravar heating* no Twenty durante 5b antes da releitura 5f.
        f.add_conv(3, "novo-site@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(4, "novo-curso@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(5, "novo-conv@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(6, "late-new@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.db.commit()
        docs = [
            {"email": "late-new@corp.com", "status": "active", "googleUid": "u-late", "name": "Late", "createdAt": ago(days=0), "aulas": []},
        ]

        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertTrue(f.injected)
        self.assertEqual(counts["oposicao_aplicada"], 3)
        for pid in ("p-site", "p-curso", "p-conv"):
            for k in H.PERSON_HEATING_FIELDS:
                self.assertIsNone(f.people[pid][k], (pid, k))
        p_new = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "late-new@corp.com")
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(p_new[k], k)
        self.assertEqual(f.people["p-lixo"]["heatingNota"], 50)
        patched_lixo = [p for m, p, _, _ in f.calls if m == "PATCH" and p == "/rest/people/p-lixo"]
        self.assertEqual(patched_lixo, [])
        for em in ("antigo@corp.com", "so-conv@corp.com", "late-new@corp.com"):
            self.assertEqual(f.db.execute("SELECT count(*) FROM engagement_events WHERE email = ?", (em,)).fetchone()[0], 0, em)
            self.assertEqual(f.db.execute("SELECT count(*) FROM heating WHERE email = ?", (em,)).fetchone()[0], 0, em)

    def test_d1_batch_valida_resposta_e_erros_por_item(self):
        # agy P1: d1_batch rejeita data não-dict, success falso no envelope, result não-lista,
        # tamanho divergente das instruções, item não-dict, item com success=False e results inválido.
        stmts = [("SELECT 1", None), ("SELECT 2", None), ("SELECT 3", None)]
        casos = [
            (200, []),
            (200, None),
            (200, {"success": False, "result": [], "errors": [{"message": "segredo@corp.com"}]}),
            (200, {"success": True, "result": "nao-lista"}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": [], "success": False, "error": "segredo@corp.com"}, {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, "ruim", {"results": [], "success": True}]}),
            (200, {"success": True, "result": [{"results": [], "success": True}, {"results": "ruim", "success": True}, {"results": [], "success": True}]}),
        ]
        for st, payload in casos:
            c = TS.Clients(ENV, lambda _m, _u, _h, _b, _s=st, _p=payload: (_s, _p), sleep=lambda s: None)
            with self.assertRaises(H.SyncError) as ctx:
                H.d1_batch(c, stmts)
            self.assertNotIn("segredo", str(ctx.exception).lower())

        # Caso válido: 3 itens com success=True.
        ok_payload = {"success": True, "result": [{"results": [{"a": 1}], "success": True}, {"results": None, "success": True}, {"results": [], "success": True}]}
        c_ok = TS.Clients(ENV, lambda _m, _u, _h, _b: (200, ok_payload), sleep=lambda s: None)
        self.assertEqual(H.d1_batch(c_ok, stmts), [[{"a": 1}], [], []])

    def test_inscricao_orfa_e_email_trocado_religa_a_pessoa_criada(self):
        # agy P1: Inscrição existente sem Pessoa ativa (pessoaId=None ou de Pessoa cujo email mudou) quando a Pessoa
        # é criada nesta rodada liga a Inscrição ao novo id retornado e nunca envia pessoaId="".
        f = Fake(people=[person("p-trocada", "outro@corp.com")], schema=True)
        f.enrolls["e-orphan"] = {
            "id": "e-orphan",
            "inscricaoId": H.inscricao_id(SLUG, "orfa@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 9,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": None,
        }
        f.enrolls["e-moved"] = {
            "id": "e-moved",
            "inscricaoId": H.inscricao_id(SLUG, "mudou@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 9,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-trocada",
        }
        docs = [
            {"email": "orfa@corp.com", "status": "active", "googleUid": "u-o", "name": "Orfa", "createdAt": ago(days=5), "aulas": []},
            {"email": "mudou@corp.com", "status": "active", "googleUid": "u-m", "name": "Mudou", "createdAt": ago(days=5), "aulas": []},
        ]
        # Simulação conta 2 criações de Pessoa e 2 atualizações de Inscrição sem escrever nada.
        c_dry, _ = run_job(f, apply=False, ler_curso=lambda _s: docs)
        self.assertEqual((c_dry["pessoas_criadas"], c_dry["inscricoes_atualizadas"]), (2, 2))
        self.assertEqual(f.twenty_writes(), [])

        # --apply cria as Pessoas e religa as duas Inscrições aos novos ids (nunca pessoaId="").
        c_app, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual((c_app["pessoas_criadas"], c_app["inscricoes_atualizadas"]), (2, 2))
        p_orfa = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "orfa@corp.com")
        p_mudou = next(p for p in f.people.values() if p["emails"]["primaryEmail"] == "mudou@corp.com")
        self.assertEqual(f.enrolls["e-orphan"]["pessoaId"], p_orfa["id"])
        self.assertEqual(f.enrolls["e-moved"]["pessoaId"], p_mudou["id"])
        for m, path, data, _ in f.calls:
            if m == "PATCH" and path.startswith("/rest/courseEnrollments/"):
                self.assertNotEqual(data.get("pessoaId"), "")

    def test_patch_diario_sinais_sem_pontos_e_sem_patch_no_dia_seguinte(self):
        # Claude P2: mesmo evento em dois dias seguidos com mesma nota -> nenhum PATCH no Twenty no 2º dia,
        # heatingSinais/sinais têm só rótulos sem pontos (pontos ficam no D1), e calculadoEm só muda quando
        # nota/faixa/rótulos mudam.
        day1 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
        ev_when = "2026-11-01T12:00:00.000Z"
        f = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ev_when)
        f.add_conv(2, "ana@corp.com", action="newsletter_confirm", when=ev_when)
        docs = [{
            "email": "ana@corp.com",
            "status": "active",
            "googleUid": "uid_ana",
            "name": "Ana",
            "createdAt": ev_when,
            "aulas": [{"dayId": "d1", "completed": True, "updatedAt": ev_when}],
        }]

        # Dia 1: nota site = 12 (frio), nota curso = 8 (frio).
        c1, _ = run_job(f, apply=True, now_dt=day1, ler_curso=lambda _s: docs)
        self.assertEqual((c1["pessoas_atualizadas"], c1["inscricoes_criadas"]), (1, 1))
        self.assertEqual(f.people["p1"]["heatingNota"], 12)
        self.assertEqual(f.people["p1"]["heatingSinais"], "Download de material; Inscrição na newsletter")
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day1))
        enr = next(iter(f.enrolls.values()))
        self.assertEqual((enr["nota"], enr["sinais"], enr["calculadoEm"]), (8, "Cadastro no curso; Aula concluída no curso", CS.stamp(day1)))
        d1_sig_day1 = f.heating_rows()[("site", "ana@corp.com")]["signals"]

        # Dia 2: pontos decaídos mudam no D1, mas nota (12 e 8), faixa e rótulos são iguais -> ZERO PATCH no Twenty!
        n_tw_before = len(f.twenty_writes())
        c2, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs)
        self.assertEqual((c2["pessoas_atualizadas"], c2["inscricoes_atualizadas"]), (0, 0))
        self.assertEqual(len(f.twenty_writes()), n_tw_before)
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day1))
        self.assertEqual(enr["calculadoEm"], CS.stamp(day1))
        # No D1, heating.computed_at e os pontos decaídos em heating.signals foram atualizados.
        self.assertEqual(f.heating_rows()[("site", "ana@corp.com")]["computed_at"], CS.stamp(day2))
        self.assertNotEqual(f.heating_rows()[("site", "ana@corp.com")]["signals"], d1_sig_day1)

        # Dia 2 com mudança só de status no curso (active -> completed), mesma nota/faixa/sinais:
        # PATCH altera só status e NÃO mexe em calculadoEm ("Nota mudou em").
        docs_comp = [{**docs[0], "status": "completed"}]
        c3, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c3["inscricoes_atualizadas"], 1)
        last_patch = [d for m, p, d, _ in f.calls if m == "PATCH" and p.startswith("/rest/courseEnrollments/")][-1]
        self.assertEqual(last_patch, {"status": "CONCLUIDO"})
        self.assertEqual(enr["calculadoEm"], CS.stamp(day1))

        # Mutante P3: se heatingCalculadoEm estiver ausente (None ou "") com nota, faixa e sinais iguais,
        # _person_heating_patch e o job preenchem heatingCalculadoEm.
        for missing_calc in (None, ""):
            p_sem_calc = person(
                "p-calc", "ana@corp.com",
                heatingNota=12,
                heatingFaixa="FRIO",
                heatingSinais="Download de material; Inscrição na newsletter",
                heatingCalculadoEm=missing_calc,
            )
            want_ana = H.calcular_heating_contexto(
                "site",
                [
                    {"id": "1", "context": "site", "email": "ana@corp.com", "signal": "material_baixado", "ref": "planilha-custos-ia", "occurred_at": ev_when},
                    {"id": "2", "context": "site", "email": "ana@corp.com", "signal": "newsletter_confirmada", "ref": None, "occurred_at": ev_when},
                ],
                day1,
            )
            patch_calc = H._person_heating_patch(p_sem_calc, want_ana, CS.stamp(day1))
            self.assertEqual(patch_calc["heatingCalculadoEm"], CS.stamp(day1))

        f.people["p1"]["heatingCalculadoEm"] = None
        c4, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c4["pessoas_atualizadas"], 1)
        self.assertEqual(f.people["p1"]["heatingCalculadoEm"], CS.stamp(day2))
        c5, _ = run_job(f, apply=True, now_dt=day2, ler_curso=lambda _s: docs_comp)
        self.assertEqual(c5["pessoas_atualizadas"], 0)

    def test_curso_status_desconhecido_bloqueia_emails_conservadoramente(self):
        # Claude P3 + Codex P2: descadastrado com cadastro (10) + clique (3) tem nota 10 com Firestore disponível,
        # e continua com nota 10 (NÃO vira 13) quando o Firestore está indisponível ou quando o inscrito sumiu.
        f = Fake(people=[person("p1", "u@corp.com"), person("p2", "ativo@corp.com")], schema=True)
        f.add_event("e-clk-u", "u@corp.com", "email_clicado", context=CTX_CURSO, ref="m1", when=ago(days=0))
        f.add_event("e-clk-a", "ativo@corp.com", "email_clicado", context=CTX_CURSO, ref="m2", when=ago(days=0))
        docs = [
            {"email": "u@corp.com", "status": "unsubscribed", "googleUid": "u1", "name": "U", "createdAt": ago(days=0), "aulas": []},
            {"email": "ativo@corp.com", "status": "active", "googleUid": "u2", "name": "A", "createdAt": ago(days=0), "aulas": []},
        ]
        # 1) Firestore disponível: u@ fica em 10 (bloqueado); ativo@ fica em 13 (10 + 3).
        run_job(f, apply=True, ler_curso=lambda _s: docs, email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 13)

        # 2) Firestore indisponível: u@ continua 10 (não vira 13!), e ativo@ cai conservadoramente para 10.
        def fail_curso(_slug):
            raise H.CursoIndisponivel("falha Firestore")
        run_job(f, apply=True, ler_curso=fail_curso, email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 10)

        # 3) Inscrito sumiu do Firestore (lista vazia): u@ continua 10 (não vira 13!).
        run_job(f, apply=True, ler_curso=lambda _s: [], email_desde=ago(days=30))
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "u@corp.com")]["score"], 10)
        self.assertEqual(f.heating_rows()[(CTX_CURSO, "ativo@corp.com")]["score"], 10)

    def test_inscricao_fora_do_curso_limpa_nota_congelada_e_oposicao_limpa_com_curso_indisponivel(self):
        # agy P2: (1) Inscrição ativa cujo email sumiu do Firestore e não tem nota hoje tem nota/faixa/sinais/calculadoEm limpos;
        # (2) Oposição limpa a nota da Inscrição mesmo quando o Firestore do curso está indisponível.
        f = Fake(people=[person("p1", "saiu@corp.com")], schema=True)
        f.enrolls["e-saiu"] = {
            "id": "e-saiu",
            "inscricaoId": H.inscricao_id(SLUG, "saiu@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=190),
            "nota": 25,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=190),
            "pessoaId": "p1",
        }
        c1, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c1["inscricoes_atualizadas"], 1)
        self.assertEqual(
            (f.enrolls["e-saiu"]["nota"], f.enrolls["e-saiu"]["faixa"], f.enrolls["e-saiu"]["sinais"], f.enrolls["e-saiu"]["calculadoEm"]),
            (None, None, None, None),
        )
        self.assertEqual((f.enrolls["e-saiu"]["status"], f.enrolls["e-saiu"]["aulasConcluidas"]), ("ATIVO", 2))

        # Curso indisponível: oposição ainda limpa a nota da Inscrição; quem não fez oposição fica intocado.
        g = Fake(people=[person("p-opt", "opt@corp.com"), person("p-ok", "ok@corp.com")], schema=True)
        g.add_optout("opt@corp.com")
        g.enrolls["e-opt"] = {
            "id": "e-opt",
            "inscricaoId": H.inscricao_id(SLUG, "opt@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=5),
            "nota": 15,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-opt",
        }
        g.enrolls["e-ok"] = {
            "id": "e-ok",
            "inscricaoId": H.inscricao_id(SLUG, "ok@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=5),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=5),
            "nota": 30,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=5),
            "pessoaId": "p-ok",
        }
        c2, _ = run_job(g, apply=True, ler_curso=lambda _s: (_ for _ in ()).throw(H.CursoIndisponivel("down")))
        self.assertEqual((c2["curso_indisponivel"], c2["oposicao_aplicada"], c2["inscricoes_atualizadas"]), (1, 1, 1))
        self.assertEqual(
            (g.enrolls["e-opt"]["nota"], g.enrolls["e-opt"]["faixa"], g.enrolls["e-opt"]["sinais"], g.enrolls["e-opt"]["calculadoEm"]),
            (None, None, None, None),
        )
        self.assertEqual(g.enrolls["e-ok"]["nota"], 30)

    def test_inscricao_ausente_do_curso_com_nota_no_d1_atualiza_apenas_campos_de_nota(self):
        # P2: Inscrição ativa no Twenty cujo email não veio na leitura atual do curso (curso disponível), mas que ainda
        # tem nota no D1 para o contexto (email_spam ou eventos dentro da janela de 180d), atualiza só nota, faixa,
        # sinais e calculadoEm (e só quando mudam ou falta calculadoEm), sem mexer em status/aulasConcluidas/pessoaId.
        f = Fake(
            people=[
                person("p-spam", "spam@corp.com"),
                person("p-decai", "decai@corp.com"),
                person("p-semcalc", "semcalc@corp.com"),
                person("p-vazia", "vazia-spam@corp.com"),
            ],
            schema=True,
        )
        # Caso do log: tinha 25/MORNO no Twenty, sumiu do Firestore e recebeu email_spam (janela de 365d).
        f.enrolls["e-spam"] = {
            "id": "e-spam",
            "inscricaoId": H.inscricao_id(SLUG, "spam@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=40),
            "aulasConcluidas": 2,
            "ultimaAula": ago(days=20),
            "nota": 25,
            "faixa": "MORNO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=10),
            "pessoaId": "p-spam",
        }
        f.add_event("ev-spam-cur", "spam@corp.com", "email_spam", context=CTX_CURSO, source="resend", ref="em-1", when=ago(days=200))

        # Decaimento: evento de cadastro há 30d -> nota 5 / FRIO / "Cadastro no curso"; sinais já era igual.
        f.enrolls["e-decai"] = {
            "id": "e-decai",
            "inscricaoId": H.inscricao_id(SLUG, "decai@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=30),
            "aulasConcluidas": 1,
            "ultimaAula": ago(days=30),
            "nota": 10,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": ago(days=30),
            "pessoaId": "p-decai",
        }
        f.add_event("ev-decai-cur", "decai@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=30))

        # Mesma nota/faixa/sinais, mas sem calculadoEm -> PATCH preenche só calculadoEm.
        f.enrolls["e-semcalc"] = {
            "id": "e-semcalc",
            "inscricaoId": H.inscricao_id(SLUG, "semcalc@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=30),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 5,
            "faixa": "FRIO",
            "sinais": "Cadastro no curso",
            "calculadoEm": None,
            "pessoaId": "p-semcalc",
        }
        f.add_event("ev-semcalc-cur", "semcalc@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=30))

        # Inscrição que estava com os 4 campos vazios no Twenty e recebeu email_spam fora do Firestore.
        f.enrolls["e-vazia"] = {
            "id": "e-vazia",
            "inscricaoId": H.inscricao_id(SLUG, "vazia-spam@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "CONCLUIDO",
            "entrada": ago(days=200),
            "aulasConcluidas": 5,
            "ultimaAula": ago(days=190),
            "nota": None,
            "faixa": None,
            "sinais": None,
            "calculadoEm": None,
            "pessoaId": "p-vazia",
        }
        f.add_event("ev-vazia-spam", "vazia-spam@corp.com", "email_spam", context=CTX_CURSO, source="resend", ref="em-2", when=ago(days=1))

        c1, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c1["inscricoes_atualizadas"], 4)
        patches = {
            p.rsplit("/", 1)[1]: d
            for m, p, d, _ in f.calls
            if m == "PATCH" and p.startswith("/rest/courseEnrollments/")
        }
        self.assertEqual(patches["e-spam"], {
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
        })
        self.assertEqual((f.enrolls["e-spam"]["status"], f.enrolls["e-spam"]["aulasConcluidas"], f.enrolls["e-spam"]["pessoaId"]),
                         ("ATIVO", 2, "p-spam"))
        self.assertEqual(patches["e-decai"], {"nota": 5, "calculadoEm": NOW_ISO})
        self.assertEqual((f.enrolls["e-decai"]["status"], f.enrolls["e-decai"]["aulasConcluidas"]), ("ATIVO", 1))
        self.assertEqual(patches["e-semcalc"], {"calculadoEm": NOW_ISO})
        self.assertEqual(patches["e-vazia"], {
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
        })
        self.assertEqual((f.enrolls["e-vazia"]["status"], f.enrolls["e-vazia"]["aulasConcluidas"]), ("CONCLUIDO", 5))

        # D1 e Twenty alinhados (0/frio no spam); segunda rodada sem mudança não faz nenhum PATCH!
        self.assertEqual((f.heating_rows()[(CTX_CURSO, "spam@corp.com")]["score"], f.heating_rows()[(CTX_CURSO, "spam@corp.com")]["band"]),
                         (0, "frio"))
        n_tw = len(f.twenty_writes())
        c2, _ = run_job(f, apply=True, ler_curso=lambda _s: [])
        self.assertEqual(c2["inscricoes_atualizadas"], 0)
        self.assertEqual(len(f.twenty_writes()), n_tw)

    def test_string_vazia_nao_gera_patch_eterno_e_zero_e_preenchido(self):
        # agy P2: "" é tratado como vazio (sem PATCH eterno), e 0 (nota de spam) é tratado como preenchido.
        f = Fake(
            people=[
                person("p-vazio", "vazio@corp.com", heatingNota=None, heatingFaixa="", heatingSinais="", heatingCalculadoEm=None),
                person("p-zero", "zero@corp.com", heatingNota=0, heatingFaixa="FRIO", heatingSinais="Reclamou de spam: não abordar", heatingCalculadoEm=NOW_ISO),
            ],
            schema=True,
        )
        f.enrolls["e-vazio"] = {
            "id": "e-vazio",
            "inscricaoId": H.inscricao_id(SLUG, "vazio@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": "",
            "sinais": "",
            "calculadoEm": None,
            "pessoaId": "p-vazio",
        }
        f.enrolls["e-fora-vazio"] = {
            "id": "e-fora-vazio",
            "inscricaoId": H.inscricao_id(SLUG, "fora@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": None,
            "faixa": None,
            "sinais": "",
            "calculadoEm": None,
            "pessoaId": "p-vazio",
        }
        f.enrolls["e-zero"] = {
            "id": "e-zero",
            "inscricaoId": H.inscricao_id(SLUG, "zero@corp.com"),
            "curso": "CHATGPT_NO_DIA_A_DIA",
            "status": "ATIVO",
            "entrada": ago(days=200),
            "aulasConcluidas": 0,
            "ultimaAula": None,
            "nota": 0,
            "faixa": "FRIO",
            "sinais": "Reclamou de spam: não abordar",
            "calculadoEm": NOW_ISO,
            "pessoaId": "p-zero",
        }
        docs = [
            {"email": "vazio@corp.com", "status": "active", "googleUid": "uv", "name": "V", "createdAt": ago(days=200), "aulas": []},
            {"email": "zero@corp.com", "status": "active", "googleUid": "uz", "name": "Z", "createdAt": ago(days=200), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        # p-vazio, e-vazio e e-fora-vazio NÃO recebem PATCH; p-zero e e-zero (nota=0) SÃO limpos.
        self.assertEqual((counts["pessoas_limpas"], counts["inscricoes_atualizadas"]), (1, 1))
        patched_paths = [p for m, p, _, _ in f.calls if m == "PATCH"]
        self.assertEqual(patched_paths, ["/rest/people/p-zero", "/rest/courseEnrollments/e-zero"])
        self.assertIsNone(f.people["p-zero"]["heatingNota"])
        self.assertIsNone(f.enrolls["e-zero"]["nota"])

        # optout() também trata "" como vazio.
        out_opt = io.StringIO()
        c_opt = H.optout(TS.Clients(ENV, f, sleep=lambda s: None), "vazio@corp.com", apply=False, out=out_opt, forget_key=FORGET_KEY)
        self.assertEqual((c_opt["pessoas_limpas"], c_opt["inscricoes_limpas"]), (0, 0))

    def test_excluidos_e_dominios_de_teste_nao_criam_pessoa_nem_inscricao(self):
        # Claude P3 + mutante P3: HEATING_EXCLUDE (mesmo com Pessoa já existente no Twenty) e domínios de teste
        # não ganham Pessoa nova nem Inscrição no Twenty.
        f = Fake(people=[person("p-gui", "gui@corp.com")], schema=True)
        docs = [
            {"email": "gui@corp.com", "status": "active", "googleUid": "u1", "name": "Gui", "createdAt": ago(days=0), "aulas": []},
            {"email": "ex2@corp.com", "status": "active", "googleUid": "u4", "name": "Ex2", "createdAt": ago(days=0), "aulas": []},
            {"email": "a@example.com", "status": "active", "googleUid": "u2", "name": "Ex", "createdAt": ago(days=0), "aulas": []},
            {"email": "b@foo.test", "status": "active", "googleUid": "u3", "name": "T", "createdAt": ago(days=0), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs, exclude_emails={"gui@corp.com", "ex2@corp.com"})
        self.assertEqual((counts["pessoas_criadas"], counts["inscricoes_criadas"], counts["excluidos"]), (0, 0, 4))
        self.assertEqual(set(f.people), {"p-gui"})
        self.assertEqual(f.enrolls, {})

    def test_is_excluded_aliases_forma_canonica_item_com_mais_e_dominios(self):
        # Item sem `+` exclui ele mesmo e aliases com `+` (em minúsculas), sem tratar pontos do Gmail nem outro domínio.
        # Item que já contém `+` exclui apenas ele mesmo (exato).
        excluded = {"Fulano@Gmail.com", "ciclano+vip@gmail.com"}
        for em in (
            "fulano@gmail.com",
            "FULANO@GMAIL.COM",
            "fulano+teste@gmail.com",
            "Fulano+A+B@Gmail.com",
            "fulano+@gmail.com",
            "ciclano+vip@gmail.com",
            "CICLANO+VIP@GMAIL.COM",
        ):
            with self.subTest(excluded=em):
                self.assertTrue(H.is_excluded(em, excluded), em)
        for em in (
            "fu.lano@gmail.com",
            "fu.lano+teste@gmail.com",
            "fulano@outro.com",
            "fulano+teste@outro.com",
            "ciclano@gmail.com",
            "ciclano+outro@gmail.com",
            "ciclano+vip+extra@gmail.com",
            "ciclano+vip@outro.com",
        ):
            with self.subTest(allowed=em):
                self.assertFalse(H.is_excluded(em, excluded), em)

        # Integração no job: alias de item sem `+` e item com `+` exato não recebem nota nem criam Pessoa/Inscrição;
        # forma base de item com `+`, outro alias, ponto do Gmail e domínio diferente continuam pontuando.
        f = Fake(schema=True)
        for idx_c, em in enumerate(
            (
                "fulano+teste@gmail.com",
                "ciclano+vip@gmail.com",
                "ciclano@gmail.com",
                "ciclano+outro@gmail.com",
                "fu.lano@gmail.com",
                "fulano+teste@outro.com",
            ),
            start=1,
        ):
            f.add_conv(idx_c, em, action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs = [
            {"email": "fulano+curso@gmail.com", "status": "active", "googleUid": "u1", "name": "Fulano", "createdAt": ago(days=0), "aulas": []},
            {"email": "ciclano+vip@gmail.com", "status": "active", "googleUid": "u2", "name": "Ciclano", "createdAt": ago(days=0), "aulas": []},
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs, exclude_emails=excluded)
        self.assertEqual((counts["pessoas_criadas"], counts["inscricoes_criadas"], counts["excluidos"]), (0, 0, 3))
        hrows = f.heating_rows()
        for em in ("fulano+teste@gmail.com", "fulano+curso@gmail.com", "ciclano+vip@gmail.com"):
            self.assertNotIn(("site", em), hrows)
            self.assertNotIn((CTX_CURSO, em), hrows)
        for em in ("ciclano@gmail.com", "ciclano+outro@gmail.com", "fu.lano@gmail.com", "fulano+teste@outro.com"):
            self.assertIn(("site", em), hrows)

    def test_id_evento_aula_inclui_sha16_do_email_e_evita_colisao_mesmo_uid(self):
        # Claude P4: dois documentos com o mesmo googleUid e emails diferentes geram ids distintos
        # `cu:<slug>:aula:<googleUid>:<sha16>:<dayId>` e ambos contabilizam a aula no D1 e no Twenty.
        f = Fake(schema=True)
        docs = [
            {
                "email": "Um@Corp.com",
                "status": "active",
                "googleUid": "uid_shared_1",
                "name": "Um",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            },
            {
                "email": "dois@corp.com",
                "status": "active",
                "googleUid": "uid_shared_1",
                "name": "Dois",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            },
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: docs)
        self.assertEqual(counts["eventos_curso_novos"], 4)  # 2 cadastros + 2 aulas sem colisão
        id_um = f"cu:{SLUG}:aula:uid_shared_1:{H.email_sha256_32('um@corp.com')[:16]}:day-01"
        id_dois = f"cu:{SLUG}:aula:uid_shared_1:{H.email_sha256_32('dois@corp.com')[:16]}:day-01"
        self.assertNotEqual(id_um, id_dois)
        ev_ids = set(f.event_ids())
        self.assertIn(id_um, ev_ids)
        self.assertIn(id_dois, ev_ids)
        hrows = f.heating_rows()
        self.assertEqual((hrows[(CTX_CURSO, "um@corp.com")]["score"], hrows[(CTX_CURSO, "um@corp.com")]["band"]), (15, "morno"))
        self.assertEqual((hrows[(CTX_CURSO, "dois@corp.com")]["score"], hrows[(CTX_CURSO, "dois@corp.com")]["band"]), (15, "morno"))

    def test_ci_pages_yml_roda_test_heating_sync_apos_conversoes(self):
        # P1: .github/workflows/pages.yml precisa rodar test_heating_sync.py no passo "Testes dos scripts de montagem",
        # logo depois de test_conversoes_sync.py.
        pages_yml = os.path.join(HERE, "..", "..", ".github", "workflows", "pages.yml")
        with open(pages_yml, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        step_idx = lines.index("- name: Testes dos scripts de montagem")
        conv_idx = lines.index("python3 blog-src/scripts/test_conversoes_sync.py", step_idx)
        self.assertEqual(lines[conv_idx + 1], "python3 blog-src/scripts/test_heating_sync.py")

    def test_mutacoes_sobreviventes_esquecido_optout_pessoa_nova_desempate_e_firestore(self):
        # 1) Evento pré-existente no D1 de curso_esquecido é apagado no passo 3 (preservando site).
        f1 = Fake(people=[person("p1", "esc@corp.com")], schema=True)
        mac_esc = H.curso_esquecido_hmac(CTX_CURSO, "esc@corp.com", FORGET_KEY)
        f1.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, ?, ?)", (CTX_CURSO, mac_esc, NOW_ISO))
        f1.db.commit()
        f1.add_event("ev-esc-cur", "esc@corp.com", "email_clicado", context=CTX_CURSO, source="resend", ref="m1", when=ago(days=1))
        f1.add_event("ev-esc-site", "esc@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=1))
        run_job(f1, apply=True)
        self.assertEqual(f1.event_ids(), ["ev-esc-site"])

        # 2) Oposição barra novos eventos de conversão e de curso no D1 depois do run.
        f2 = Fake(schema=True)
        f2.add_optout("opt@corp.com")
        f2.add_conv(1, "opt@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs2 = [{"email": "opt@corp.com", "status": "active", "googleUid": "u1", "name": "Opt", "createdAt": ago(days=0), "aulas": []}]
        c2, _ = run_job(f2, apply=True, ler_curso=lambda _s: docs2)
        self.assertEqual((c2["eventos_conversao_novos"], c2["eventos_curso_novos"], f2.event_ids(), f2.heating_rows()), (0, 0, [], {}))

        # 3) Pessoa criada pelo curso (inclusive source="form" sem googleUid) já nasce com campos heating* do site.
        f3 = Fake(schema=True)
        f3.add_conv(1, "form@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs3 = [{"email": "form@corp.com", "status": "active", "source": "form", "googleUid": None, "name": "Form User", "createdAt": ago(days=0), "aulas": []}]
        c3, _ = run_job(f3, apply=True, ler_curso=lambda _s: docs3)
        self.assertEqual((c3["pessoas_criadas"], c3["inscricoes_criadas"]), (1, 1))
        post_people = [d for m, p, d, _ in f3.calls if m == "POST" and p == "/rest/people"]
        self.assertEqual(len(post_people), 1)
        self.assertEqual(
            (post_people[0]["heatingNota"], post_people[0]["heatingFaixa"], post_people[0]["heatingSinais"], post_people[0]["heatingCalculadoEm"]),
            (12, "FRIO", "Download de material", NOW_ISO),
        )

        # 4) Desempate de Pessoa com dois emails: vence a maior nota em qualquer ordem; em empate, desempata por email.
        for em_high, em_low in (("a@corp.com", "b@corp.com"), ("b@corp.com", "a@corp.com")):
            f4 = Fake(people=[person("p1", "a@corp.com", extra=["b@corp.com"])], schema=True)
            f4.add_event("ev-hi", em_high, "site_agende", context="site", source="ga4", ref=None, when=ago(days=0))
            f4.add_event("ev-lo", em_low, "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))
            run_job(f4, apply=True)
            self.assertEqual((f4.people["p1"]["heatingNota"], f4.people["p1"]["heatingFaixa"]), (50, "QUENTE"))
        f4_tie = Fake(people=[person("p1", "a@corp.com", extra=["b@corp.com"])], schema=True)
        f4_tie.add_event("ev-a", "a@corp.com", "material_baixado", context="site", source="conversoes", ref="m1", when=ago(days=0))  # 12
        f4_tie.add_event("ev-b1", "b@corp.com", "newsletter_confirmada", context="site", source="conversoes", ref=None, when=ago(days=0))  # 10
        f4_tie.add_event("ev-b2", "b@corp.com", "site_visita", context="site", source="ga4", ref=None, when=ago(days=0))  # 2 -> 12
        run_job(f4_tie, apply=True)
        self.assertEqual(f4_tie.people["p1"]["heatingSinais"], "Inscrição na newsletter; Visita ao site")

        # 5) Mapeamento de exceções em ler_curso_firestore.
        import types

        with self.assertRaisesRegex(H.SyncError, "curso desconhecido"):
            H.ler_curso_firestore("inexistente", sa_file="/tmp/fake.json")
        with self.assertRaises(H.CursoIndisponivel):
            H.ler_curso_firestore(SLUG, sa_file="")

        class GoogleApiErr(Exception):
            __module__ = "google.api_core.exceptions"

        class GrpcErr(Exception):
            __module__ = "grpc._channel"

        for exc in (OSError("sem arquivo"), ValueError("json ruim"), GoogleApiErr("quota"), GrpcErr("rede"), RuntimeError("bug")):
            fake_google = types.ModuleType("google")
            fake_cloud = types.ModuleType("google.cloud")
            fake_fs = types.ModuleType("google.cloud.firestore")
            fake_oauth = types.ModuleType("google.oauth2")
            fake_sa = types.ModuleType("google.oauth2.service_account")

            class _Creds:
                @staticmethod
                def from_service_account_file(_path, _e=exc):
                    raise _e

            fake_sa.Credentials = _Creds
            fake_fs.Client = object
            fake_cloud.firestore = fake_fs
            fake_oauth.service_account = fake_sa
            fake_google.cloud = fake_cloud
            fake_google.oauth2 = fake_oauth
            saved = {k: sys.modules.get(k) for k in ("google", "google.cloud", "google.cloud.firestore", "google.oauth2", "google.oauth2.service_account")}
            sys.modules.update({
                "google": fake_google,
                "google.cloud": fake_cloud,
                "google.cloud.firestore": fake_fs,
                "google.oauth2": fake_oauth,
                "google.oauth2.service_account": fake_sa,
            })
            try:
                if isinstance(exc, RuntimeError):
                    with self.assertRaises(RuntimeError):
                        H.ler_curso_firestore(SLUG, sa_file="/tmp/fake.json")
                else:
                    with self.assertRaises(H.CursoIndisponivel):
                        H.ler_curso_firestore(SLUG, sa_file="/tmp/fake.json")
            finally:
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v


class CliAndOutputTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="heating-lock-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def _cli(
        self,
        fake,
        *args,
        stdin=None,
        ler_curso=None,
        ler_ga4_dia=None,
        ga4_delete=None,
        bq_delete=None,
        extra_env=None,
        lock_wait_s=0.05,
        lock_poll_s=0.01,
    ):
        out, err = io.StringIO(), io.StringIO()
        keys = [*ENV, "CONVERSOES_SYNC_LOCK", "HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE", "HEATING_GA4_DESDE"]
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        for opt_k in ("HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE", "HEATING_GA4_DESDE"):
            os.environ.pop(opt_k, None)
        if extra_env:
            os.environ.update(extra_env)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = H.main(
                    list(args),
                    transport=fake,
                    sleep=lambda s: None,
                    stdin=io.StringIO(stdin) if stdin is not None else None,
                    now_dt=NOW,
                    ler_curso=(lambda s: []) if ler_curso is None else ler_curso,
                    ler_ga4_dia=ler_ga4_dia,
                    ga4_delete=ga4_delete,
                    bq_delete=bq_delete,
                    lock_wait_s=lock_wait_s,
                    lock_poll_s=lock_poll_s,
                )
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_saida_tem_todas_as_18_chaves_e_nunca_imprime_email(self):
        f = Fake(people=[person("p1", "segredo@corp.com")])
        f.add_conv(1, "segredo@corp.com", when=ago(days=0))
        rc, text = self._cli(f, "--apply")
        self.assertEqual(rc, 0, text)
        self.assertNotIn("segredo", text.lower())
        for k in H.RUN_COUNT_KEYS:
            self.assertRegex(text, rf"(?m)^{k}: \d+$", k)

        # Erro no Twenty ou exceção inesperada nunca ecoam email.
        g = Fake(fail=lambda m, p, d: (500, {"errors": [{"message": "erro com segredo@corp.com"}]}) if p == "/metadata" else None)
        rc2, text2 = self._cli(g, "--apply")
        self.assertEqual((rc2, "segredo" in text2.lower()), (1, False))

        def broken(*_a):
            raise KeyError("segredo@corp.com")
        rc3, text3 = self._cli(broken)
        self.assertEqual((rc3, "segredo" in text3.lower()), (1, False))

    def test_lock_ocupado_espera_e_falha_com_mensagem_fixa(self):
        f = Fake()
        clock = [0.0]
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            clock[0] += s

        with open(self.lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            out, err = io.StringIO(), io.StringIO()
            old = {k: os.environ.get(k) for k in [*ENV, "CONVERSOES_SYNC_LOCK"]}
            os.environ.update(ENV)
            os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = H.main(
                        ["--apply"],
                        transport=f,
                        sleep=fake_sleep,
                        now_dt=NOW,
                        ler_curso=lambda _s: [],
                        lock_wait_s=600.0,
                        lock_poll_s=100.0,
                        monotonic=lambda: clock[0],
                    )
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(rc, 1)
        self.assertIn("lock ocupado por mais de 10 minutos", err.getvalue())
        self.assertEqual(sum(sleeps), 600.0)
        self.assertEqual(f.calls, [])

    def test_cli_optout_e_forget_curso_exigem_exatamente_um_email(self):
        f = Fake(schema=True)
        for flag in (("--optout",), ("--forget-curso", SLUG)):
            rc0, txt0 = self._cli(f, *flag, stdin="")
            self.assertEqual(rc0, 1)
            self.assertIn("exatamente um email", txt0)
            rc2, txt2 = self._cli(f, *flag, stdin="a@corp.com\nb@corp.com\n")
            self.assertEqual(rc2, 1)
            self.assertIn("exatamente um email", txt2)
        rc_ok, txt_ok = self._cli(f, "--optout", "--apply", stdin="ok@corp.com\n")
        self.assertEqual(rc_ok, 0, txt_ok)
        self.assertNotIn("ok@corp.com", txt_ok)
        rc_fc, txt_fc = self._cli(f, "--forget-curso", SLUG, "--apply", stdin="ok@corp.com\n")
        self.assertEqual(rc_fc, 0, txt_fc)
        self.assertNotIn("ok@corp.com", txt_fc)

    def test_lotes_heating_d1_batch_conversao_e_curso_invalidos_e_check_0012(self):
        # 20 emails válidos no site e 1 no curso -> 21 notas em 5 lotes de INSERT de até 5 (teto de SELECT composto do D1)
        # numa única `batch`, com <= 100 params.
        f = Fake(schema=True)
        for i in range(1, 21):
            f.add_conv(i, f"u{i:02d}@corp.com", action="newsletter_confirm", when=ago(days=0))
        # Uma conversão malformada (`conversao_invalida`) e dois docs inválidos no curso (`curso_invalido`).
        f.add_conv("z" * 64, "bad@corp.com", action="newsletter_confirm", when=ago(days=0))
        bad_curso = [
            {"email": "invalido", "status": "active", "createdAt": ago(days=0), "aulas": []},
            {"email": "ok@corp.com", "status": "status_desconhecido", "createdAt": ago(days=0), "aulas": []},
            {
                "email": "ok2@corp.com",
                "status": "active",
                "googleUid": "uid_ok2",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day/invalido", "completed": True, "updatedAt": ago(days=0)}],
            },
        ]
        counts, _ = run_job(f, apply=True, ler_curso=lambda _s: bad_curso)
        self.assertEqual(counts["notas_site"], 20)
        self.assertEqual(counts["conversao_invalida"], 1)
        self.assertEqual(counts["curso_invalido"], 3)
        self.assertLessEqual(f.max_params, 100)
        batches = [w for w in f.d1_writes() if w.startswith("BATCH:")]
        # Uma troca só: DELETE e os INSERT em lotes de até D1_COMPOUND_SELECT_MAX linhas (limite do D1).
        self.assertEqual(len(batches), 1)
        partes = batches[0][len("BATCH:"):].split(";")
        n_heating = f.db.execute("SELECT count(*) FROM heating").fetchone()[0]
        # Volume fixo: 20 notas do site e 1 do curso (ok2). Um defeito que gravasse só o primeiro lote deixaria 5.
        self.assertEqual(n_heating, 21)
        self.assertEqual(f.db.execute("SELECT count(*) FROM heating WHERE context = 'site'").fetchone()[0], 20)
        self.assertEqual(partes, ["DELETE"] + ["INSERT"] * 5)

        # Migração 0012 recusa contexto `site` e `email_hmac` fora do padrão hex 64.
        with self.assertRaises(sqlite3.IntegrityError):
            f.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES ('site', ?, ?)", ("a" * 64, NOW_ISO))
        with self.assertRaises(sqlite3.IntegrityError):
            f.db.execute("INSERT INTO curso_esquecido (context, email_hmac, forgotten_at) VALUES (?, 'curto', ?)", (CTX_CURSO, NOW_ISO))

        # Vetor cruzado de heating_optout_hmac com o Worker (mesmo prefixo heating-optout-v1\n).
        import hashlib
        import hmac as _hmac
        esperado = _hmac.new(FORGET_KEY.encode("utf-8"), b"heating-optout-v1\nleitor@exemplo.com", hashlib.sha256).hexdigest()
        self.assertEqual(H.heating_optout_hmac("  Leitor@Exemplo.COM ", FORGET_KEY), esperado)

    def test_metadados_nota_mudou_em_e_nao_renomeia_campo_existente(self):
        # 1) Criação do esquema do zero: rótulos e descrições explicam que só muda quando a nota muda
        #    e que a data do último cálculo fica em heating.computed_at no D1 (painel).
        f = Fake(people=[person("p1", "ana@corp.com")], schema=False)
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        docs = [{
            "email": "ana@corp.com",
            "status": "active",
            "googleUid": "uid_ana",
            "name": "Ana",
            "createdAt": ago(days=0),
            "aulas": [],
        }]
        run_job(f, apply=True, ler_curso=lambda _s: docs)

        spec_person = f.person_fields["heatingCalculadoEm"]["_spec"]
        self.assertEqual(spec_person["label"], "Heating: nota mudou em")
        self.assertIn("heating.computed_at", spec_person["description"])
        self.assertIn("D1", spec_person["description"])

        spec_enroll = f.enroll_fields["calculadoEm"]["_spec"]
        self.assertEqual(spec_enroll["label"], "Nota mudou em")
        self.assertIn("heating.computed_at", spec_enroll["description"])
        self.assertIn("D1", spec_enroll["description"])

        # 2) Se os campos já existirem no Twenty com outro label, o script NÃO renomeia nem chama updateOneField.
        g = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        g.person_fields["heatingCalculadoEm"]["_spec"]["label"] = "Heating: calculado em"
        g.enroll_fields["calculadoEm"]["_spec"]["label"] = "Calculado em"
        g.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        run_job(g, apply=True, ler_curso=lambda _s: docs)

        meta_muts = [
            d["query"] for m, p, d, _ in g.calls
            if p == "/metadata" and any(x in d["query"] for x in ("createOne", "updateOne"))
        ]
        self.assertEqual(meta_muts, [])
        self.assertEqual(g.person_fields["heatingCalculadoEm"]["_spec"]["label"], "Heating: calculado em")
        self.assertEqual(g.enroll_fields["calculadoEm"]["_spec"]["label"], "Calculado em")

    def test_falha_no_meio_do_batch_sem_rollback_recupera_na_rodada_seguinte(self):
        # D1 falso que executa parte do `batch` de `heating` (DELETE + 1º INSERT de 14 linhas) fazendo commit
        # SEM rollback e falha antes do 2º INSERT (6 linhas). A rodada seguinte precisa refazer `heating` inteira.
        class PartialBatchFake(Fake):
            def __init__(self, *args, **kwargs):
                self.fail_mid_batch = True
                self.mid_batch_triggered = False
                super().__init__(*args, **kwargs)

            def __call__(self, method, url, headers, body):
                data = json.loads(body) if body else None
                u = urllib.parse.urlparse(url)
                if (
                    self.fail_mid_batch
                    and u.netloc == "api.cloudflare.com"
                    and isinstance(data, dict)
                    and "batch" in data
                    and len(data["batch"]) >= 3
                    and data["batch"][0]["sql"] == "DELETE FROM heating"
                ):
                    self.calls.append((method, u.path, data, u.query))
                    self.fail_mid_batch = False
                    self.mid_batch_triggered = True
                    # Executa e commita só o DELETE e o primeiro INSERT, sem rollback, e devolve 500 no meio.
                    for item in data["batch"][:2]:
                        self.db.execute(item["sql"], item.get("params") or [])
                    self.db.commit()
                    return 500, {"success": False, "errors": [{"message": "falha no meio do batch"}]}
                return super().__call__(method, url, headers, body)

        f = PartialBatchFake(schema=True)
        # Linha antiga em `heating` que deve sumir na troca.
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) "
            "VALUES ('site', 'antigo@corp.com', 99, 'quente', '[]', ?, NULL)",
            (ago(days=1),),
        )
        f.db.commit()
        for i in range(1, 21):
            f.add_conv(i, f"u{i:02d}@corp.com", action="newsletter_confirm", when=ago(days=0))

        # 1ª rodada: falha no meio do batch sem rollback -> SyncError e tabela `heating` parcial (14 de 20).
        with self.assertRaisesRegex(H.SyncError, "D1 respondeu 500"):
            run_job(f, apply=True)
        self.assertTrue(f.mid_batch_triggered)
        parcial = f.heating_rows()
        self.assertEqual(len(parcial), H.HEATING_CHUNK)  # 14 linhas gravadas sem rollback
        self.assertNotIn(("site", "antigo@corp.com"), parcial)

        # 2ª rodada: sem falha, refaz `heating` inteira com as 20 linhas certas.
        counts2, _ = run_job(f, apply=True)
        self.assertEqual(counts2["notas_site"], 20)
        final = f.heating_rows()
        self.assertEqual(len(final), 20)
        self.assertNotIn(("site", "antigo@corp.com"), final)
        for i in range(1, 21):
            row = final[("site", f"u{i:02d}@corp.com")]
            self.assertEqual((row["score"], row["band"], row["computed_at"]), (10, "frio", NOW_ISO))


# ------------------------------------------------------------------ Testes do GA4/BigQuery e remoção no Google (corte 2)

def _ts_us(dt: datetime) -> int:
    """Converte datetime UTC para event_timestamp do GA4 em microssegundos inteiros."""
    return int(dt.timestamp()) * 1_000_000 + dt.microsecond


class Ga4EBigQueryTest(unittest.TestCase):
    def setUp(self):
        fd, self.lock = tempfile.mkstemp(prefix="heating-ga4-lock-")
        os.close(fd)
        self.addCleanup(os.unlink, self.lock)

    def _cli(self, fake, *args, stdin=None, ler_ga4_dia=None, ga4_delete=None, bq_delete=None, extra_env=None):
        out, err = io.StringIO(), io.StringIO()
        keys = [*ENV, "CONVERSOES_SYNC_LOCK", "HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE", "HEATING_GA4_DESDE"]
        old = {k: os.environ.get(k) for k in keys}
        os.environ.update(ENV)
        os.environ["CONVERSOES_SYNC_LOCK"] = self.lock
        for opt_k in ("HEATING_EXCLUDE", "HEATING_GCP_SA_FILE", "HEATING_EMAIL_DESDE", "HEATING_GA4_DESDE"):
            os.environ.pop(opt_k, None)
        if extra_env:
            os.environ.update(extra_env)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = H.main(
                    list(args),
                    transport=fake,
                    sleep=lambda s: None,
                    stdin=io.StringIO(stdin) if stdin is not None else None,
                    now_dt=NOW,
                    ler_curso=lambda _s: [],
                    ler_ga4_dia=ler_ga4_dia,
                    ga4_delete=ga4_delete,
                    bq_delete=bq_delete,
                )
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return rc, out.getvalue() + err.getvalue()

    def test_ga4_desligado_sem_env_e_validacao_de_data(self):
        f = Fake(schema=True)
        called = []
        counts, _ = run_job(f, apply=True, ler_ga4_dia=lambda d: called.append(d) or [])
        self.assertEqual(called, [])
        self.assertEqual((counts["ga4_dias_lidos"], counts["eventos_ga4_novos"], counts["ga4_indisponivel"]), (0, 0, 0))
        self.assertIsNone(f.meta_get(H.GA4_META_KEY))
        for bad in ("2026-13-01", "2026-11-26T00:00:00Z", "ontem"):
            with self.assertRaisesRegex(H.SyncError, "HEATING_GA4_DESDE inválida"):
                run_job(f, apply=False, ga4_desde=bad, ler_ga4_dia=lambda d: [])

    def test_ga4_dias_processados_avanco_marcador_ids_timestamps_limiares_e_falha_no_meio(self):
        f = Fake(
            people=[
                person("p-ana", "ana@corp.com"),
                person("p-bia", "bia@corp.com"),
                person("p-cid", "cid@corp.com"),
            ],
            schema=True,
        )
        f.add_identity("UidAna123", "ana@corp.com")
        f.add_identity("UidBia456", "bia@corp.com")
        f.add_identity("UidCid789", "cid@corp.com")

        # Dia 25: Ana com 59.999 ms e 0 scrolls (NÃO engajada -> só site_visita),
        #         Bia com 60.000 ms e 0 scrolls (engajada!),
        #         Cid com 0 ms e 1 scroll (engajada!) + contato + agenda com timestamps distintos.
        t_vis = datetime(2026, 11, 25, 10, 0, 1, 123456, tzinfo=timezone.utc)
        t_eng = datetime(2026, 11, 25, 10, 1, 2, 234567, tzinfo=timezone.utc)
        t_con = datetime(2026, 11, 25, 10, 2, 3, 345678, tzinfo=timezone.utc)
        t_age = datetime(2026, 11, 25, 10, 3, 4, 456789, tzinfo=timezone.utc)
        t_d26 = datetime(2026, 11, 26, 15, 30, 0, 500000, tzinfo=timezone.utc)

        data_by_day = {
            date(2026, 11, 25): [
                {
                    "user_id": "UidAna123",
                    "sessoes": 1,
                    "page_views": 2,
                    "engagement_time_msec": 59999,
                    "scrolls": 0,
                    "cliques_contato": 0,
                    "cliques_agenda": 0,
                    "min_ts_visita": _ts_us(t_vis),
                    "min_ts_engajamento": _ts_us(t_eng),
                    "min_ts_contato": None,
                    "min_ts_agenda": None,
                },
                {
                    "user_id": "UidBia456",
                    "sessoes": 1,
                    "page_views": 3,
                    "engagement_time_msec": 60000,
                    "scrolls": 0,
                    "cliques_contato": 0,
                    "cliques_agenda": 0,
                    "min_ts_visita": _ts_us(t_vis),
                    "min_ts_engajamento": _ts_us(t_eng),
                    "min_ts_contato": None,
                    "min_ts_agenda": None,
                },
                {
                    "user_id": "UidCid789",
                    "sessoes": 1,
                    "page_views": 4,
                    "engagement_time_msec": 0,
                    "scrolls": 1,
                    "cliques_contato": 1,
                    "cliques_agenda": 2,
                    "min_ts_visita": _ts_us(t_vis),
                    "min_ts_engajamento": _ts_us(t_eng),
                    "min_ts_contato": _ts_us(t_con),
                    "min_ts_agenda": _ts_us(t_age),
                },
            ],
            date(2026, 11, 26): [
                {
                    "user_id": "UidAna123",
                    "sessoes": 1,
                    "page_views": 1,
                    "engagement_time_msec": 1000,
                    "scrolls": 0,
                    "cliques_contato": 0,
                    "cliques_agenda": 0,
                    "min_ts_visita": _ts_us(t_d26),
                    "min_ts_engajamento": _ts_us(t_d26),
                    "min_ts_contato": None,
                    "min_ts_agenda": None,
                },
            ],
        }
        seen_days: list[date] = []

        def reader(dia: date):
            seen_days.append(dia)
            return data_by_day.get(dia)

        # 1) Simulação não grava eventos nem avança heating_ga4_ultimo_dia.
        c_dry, out_dry = run_job(f, apply=False, ga4_desde="2026-11-25", ler_ga4_dia=reader)
        self.assertEqual(seen_days, [date(2026, 11, 25), date(2026, 11, 26)])
        self.assertEqual((c_dry["ga4_dias_lidos"], c_dry["eventos_ga4_novos"]), (2, 1 + 2 + 4 + 1))
        self.assertIsNone(f.meta_get(H.GA4_META_KEY))
        self.assertEqual(f.event_ids(), [])
        for secret in ("UidAna123", "UidBia456", "UidCid789", "ana@corp.com"):
            self.assertNotIn(secret.lower(), out_dry.lower())

        # 2) --apply grava eventos dia a dia e só avança o marcador depois de gravar os eventos do dia.
        seen_days.clear()
        n_before = len(f.calls)
        c_app, out_app = run_job(f, apply=True, ga4_desde="2026-11-25", ler_ga4_dia=reader)
        self.assertEqual((c_app["ga4_dias_lidos"], c_app["eventos_ga4_novos"]), (2, 8))
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-26")
        for secret in ("UidAna123", "UidBia456", "UidCid789", "ana@corp.com"):
            self.assertNotIn(secret.lower(), out_app.lower())

        # Verifica ordem exata das escritas no D1: eventos do dia 25 -> meta 2026-11-25 -> eventos do dia 26 -> meta 2026-11-26.
        ga_writes = []
        for _m, path, data, _q in f.calls[n_before:]:
            if not path.endswith("/query") or "sql" not in data:
                continue
            sql = data["sql"]
            if sql.startswith("INSERT INTO engagement_events"):
                ga_writes.append(("ev", data["params"][0][:11]))
            elif sql.startswith("INSERT INTO meta"):
                ga_writes.append(("meta", data["params"][1]))
        # Lotes de até 5 eventos: um dia pode ter mais de um INSERT, sempre antes do marcador daquele dia.
        colapsado = [w for i, w in enumerate(ga_writes) if i == 0 or w != ga_writes[i - 1]]
        self.assertEqual(
            colapsado,
            [("ev", "ga:20261125"), ("meta", "2026-11-25"), ("ev", "ga:20261126"), ("meta", "2026-11-26")],
        )

        # Verifica ids, source, context, ref e occurred_at exatos (microssegundos -> ISO com milissegundos).
        evs = {
            r["id"]: dict(r)
            for r in f.db.execute("SELECT id, context, email, signal, source, ref, occurred_at FROM engagement_events")
        }
        h_ana = hashlib.sha256(b"UidAna123").hexdigest()[:16]
        h_bia = hashlib.sha256(b"UidBia456").hexdigest()[:16]
        h_cid = hashlib.sha256(b"UidCid789").hexdigest()[:16]

        self.assertIn(f"ga:20261125:{h_ana}:site_visita", evs)
        self.assertNotIn(f"ga:20261125:{h_ana}:site_engajada", evs)  # 59999 ms e 0 scrolls
        self.assertIn(f"ga:20261125:{h_bia}:site_visita", evs)
        self.assertIn(f"ga:20261125:{h_bia}:site_engajada", evs)     # 60000 ms
        self.assertEqual(evs[f"ga:20261125:{h_cid}:site_visita"]["occurred_at"], "2026-11-25T10:00:01.123Z")
        self.assertEqual(evs[f"ga:20261125:{h_cid}:site_engajada"]["occurred_at"], "2026-11-25T10:01:02.234Z")
        self.assertEqual(evs[f"ga:20261125:{h_cid}:site_contato"]["occurred_at"], "2026-11-25T10:02:03.345Z")
        self.assertEqual(evs[f"ga:20261125:{h_cid}:site_agende"]["occurred_at"], "2026-11-25T10:03:04.456Z")
        for ev in evs.values():
            self.assertEqual((ev["context"], ev["source"], ev["ref"]), ("site", "ga4", None))

        # Pontuação no heating (pesos do corte 1 inalterados): Cid tem visita+engajada+contato+agende -> 100 (quente).
        hrows = f.heating_rows()
        self.assertEqual((hrows[("site", "cid@corp.com")]["score"], hrows[("site", "cid@corp.com")]["band"]), (100, "quente"))
        self.assertGreater(hrows[("site", "bia@corp.com")]["score"], hrows[("site", "ana@corp.com")]["score"])

        # 3) Rodada seguinte no mesmo dia: relê a janela retroativa (2026-11-25 e 2026-11-26), mas ids determinísticos
        #    deduplicam (eventos_ga4_novos == 0) e o marcador não regride (mata mutação M13).
        seen_days.clear()
        ev_count_before = len(f.event_ids())
        c2, _ = run_job(f, apply=True, ga4_desde="2026-11-25", ler_ga4_dia=reader)
        self.assertEqual(seen_days, [date(2026, 11, 25), date(2026, 11, 26)])
        self.assertEqual((c2["ga4_dias_lidos"], c2["eventos_ga4_novos"]), (2, 0))
        self.assertEqual(len(f.event_ids()), ev_count_before)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-26")

        # 4) Falha ao gravar eventos do dia 2 não avança marcador para o dia 2 (mantém dia 1).
        state = {"armed": True}

        def fail_day2(method, path, data):
            if (
                state["armed"]
                and path.endswith("/query")
                and isinstance(data, dict)
                and data.get("sql", "").startswith("INSERT INTO engagement_events")
                and any(isinstance(p, str) and p.startswith("ga:20261126:") for p in (data.get("params") or []))
            ):
                state["armed"] = False
                return 500, {"success": False}
            return None

        g = Fake(schema=True, fail=fail_day2)
        g.add_identity("UidAna123", "ana@corp.com")
        with self.assertRaises(H.SyncError):
            run_job(g, apply=True, ga4_desde="2026-11-25", ler_ga4_dia=lambda d: data_by_day.get(d))
        self.assertEqual(g.meta_get(H.GA4_META_KEY), "2026-11-25")

    def test_ga4_eventos_atrasados_rele_3_dias_anteriores_e_deduplica(self):
        f = Fake(schema=True)
        f.add_identity("UidAna123", "ana@corp.com")
        f.meta_set(H.GA4_META_KEY, "2026-11-25")
        h_ana = hashlib.sha256(b"UidAna123").hexdigest()[:16]
        # Evento de visita do dia 24 já existia antes.
        f.add_event(
            f"ga:20261124:{h_ana}:site_visita",
            "ana@corp.com",
            "site_visita",
            source="ga4",
            ref=None,
            when="2026-11-24T10:00:00.000Z",
        )
        seen: list[date] = []

        def reader(dia: date):
            seen.append(dia)
            if dia == date(2026, 11, 24):
                # Chegou evento atrasado (schedule_click) no dia 24 dentro da janela de 72 h do GA4!
                return [{
                    "user_id": "UidAna123", "sessoes": 1, "page_views": 1, "engagement_time_msec": 0,
                    "scrolls": 0, "cliques_contato": 0, "cliques_agenda": 1,
                    "min_ts_visita": _ts_us(datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)),
                    "min_ts_engajamento": None, "min_ts_contato": None,
                    "min_ts_agenda": _ts_us(datetime(2026, 11, 24, 10, 5, tzinfo=timezone.utc)),
                }]
            return []

        counts, _ = run_job(f, apply=True, ga4_desde="2026-11-20", ler_ga4_dia=reader)
        # Relê os 3 dias até o marcador (23, 24, 25) + dia novo (26).
        self.assertEqual(seen, [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26)])
        # Só o evento atrasado `site_agende` é novo; `site_visita` já existente foi deduplicado.
        self.assertEqual(counts["eventos_ga4_novos"], 1)
        self.assertIn(f"ga:20261124:{h_ana}:site_agende", f.event_ids())
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-26")

        # Rodada seguinte sem eventos novos: `eventos_ga4_novos == 0` e nenhuma duplicata.
        counts2, _ = run_job(f, apply=True, ga4_desde="2026-11-20", ler_ga4_dia=reader)
        self.assertEqual(counts2["eventos_ga4_novos"], 0)
        self.assertEqual(len(f.event_ids()), 2)

    def test_ga4_dia_sem_tabela_espera_ou_avanca_quando_posterior_existe(self):
        # 1) Nenhum dia posterior tem tabela ainda: espera (não conta ga4_dia_sem_tabela nem avança marcador).
        f = Fake(schema=True)
        f.add_identity("UidAna123", "ana@corp.com")
        seen_wait: list[date] = []

        def reader_wait(dia: date):
            seen_wait.append(dia)
            if dia == date(2026, 11, 24):
                return [{
                    "user_id": "UidAna123", "sessoes": 1, "page_views": 1, "engagement_time_msec": 0,
                    "scrolls": 0, "cliques_contato": 0, "cliques_agenda": 0,
                    "min_ts_visita": _ts_us(datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)),
                    "min_ts_engajamento": None, "min_ts_contato": None, "min_ts_agenda": None,
                }]
            return None  # dias 25 e 26 ainda sem tabela

        c_wait, _ = run_job(f, apply=True, ga4_desde="2026-11-24", ler_ga4_dia=reader_wait)
        self.assertEqual(seen_wait, [date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26)])
        self.assertEqual(
            (c_wait["ga4_dias_lidos"], c_wait["ga4_dia_sem_tabela"], c_wait["eventos_ga4_novos"], c_wait["ga4_indisponivel"]),
            (1, 0, 1, 0),
        )
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-24")

        # 2) Dia 25 sem tabela (dia sem eventos), mas dia 26 já tem tabela: conta ga4_dia_sem_tabela=1 e marcador segue!
        def reader_gap(dia: date):
            if dia == date(2026, 11, 25):
                return None
            if dia == date(2026, 11, 26):
                return [{
                    "user_id": "UidAna123", "sessoes": 1, "page_views": 1, "engagement_time_msec": 0,
                    "scrolls": 0, "cliques_contato": 0, "cliques_agenda": 0,
                    "min_ts_visita": _ts_us(datetime(2026, 11, 26, 11, 0, tzinfo=timezone.utc)),
                    "min_ts_engajamento": None, "min_ts_contato": None, "min_ts_agenda": None,
                }]
            return []

        c_gap, out_gap = run_job(f, apply=True, ga4_desde="2026-11-24", ler_ga4_dia=reader_gap)
        self.assertEqual((c_gap["ga4_dia_sem_tabela"], c_gap["ga4_dias_lidos"], c_gap["eventos_ga4_novos"]), (1, 2, 1))
        self.assertIn("ga4_dia_sem_tabela: 1", out_gap)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-26")

    def test_ga4_teto_de_14_dias_por_rodada(self):
        f = Fake(schema=True)
        seen1: list[date] = []
        c1, _ = run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=lambda d: seen1.append(d) or [])
        self.assertEqual(len(seen1), 14)
        self.assertEqual((seen1[0], seen1[-1]), (date(2026, 11, 1), date(2026, 11, 14)))
        self.assertEqual(c1["ga4_dias_lidos"], 14)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-14")

        # Segunda rodada relê 3 dias (12, 13, 14) + 11 dias novos (15..25) respeitando o teto de 14 dias.
        seen2: list[date] = []
        c2, _ = run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=lambda d: seen2.append(d) or [])
        self.assertEqual(len(seen2), 14)
        self.assertEqual((seen2[0], seen2[-1]), (date(2026, 11, 12), date(2026, 11, 25)))
        self.assertEqual(c2["ga4_dias_lidos"], 14)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-25")

        # Terceira rodada conclui até ontem (26): relê 23..25 + novo 26.
        seen3: list[date] = []
        c3, _ = run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=lambda d: seen3.append(d) or [])
        self.assertEqual(seen3, [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26)])
        self.assertEqual(c3["ga4_dias_lidos"], 4)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-26")

    def test_ga4_janela_inteira_sem_tabela_nao_trava_o_marcador(self):
        # 14 dias sem evento nenhum logo depois de ligar a fonte, com tabela no dia 15 em diante (achado do Kimi):
        # a sonda de ontem mostra que o export existe, e o marcador anda até o fim da janela.
        f = Fake(schema=True)
        primeira_tabela = date(2026, 11, 15)
        lidos: list[date] = []

        def reader(d):
            lidos.append(d)
            return [] if d >= primeira_tabela else None

        c1, _ = run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=reader)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-14")
        self.assertEqual(c1["ga4_dia_sem_tabela"], 14)
        self.assertIn(NOW.date() - timedelta(days=1), lidos, "sonda de ontem")

        # As rodadas seguintes releem o fim da janela anterior e seguem (teto de 14 dias) até ontem, sem voltar ao
        # mesmo bloco.
        run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=reader)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-25")
        run_job(f, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=reader)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), (NOW.date() - timedelta(days=1)).isoformat())

        # Sem tabela nenhuma ainda (nem ontem): espera, sem marcador.
        g = Fake(schema=True)
        cg, _ = run_job(g, apply=True, ga4_desde="2026-11-01", ler_ga4_dia=lambda d: None)
        self.assertIsNone(g.meta_get(H.GA4_META_KEY))
        self.assertEqual(cg["ga4_dia_sem_tabela"], 0)

    def test_fila_quinta_tentativa_adiada_fica_e_alerta_sessoes_gravadas_antes_e_excedidas(self):
        # 5ª tentativa ainda com tabela adiada (streaming buffer): o item NÃO sai da fila e conta em bq_remocao_travada.
        f = Fake(schema=True)
        f.add_purge_queue("UidTravado1", NOW_ISO, ga4_pendente=0, bq_tentativas=4)
        c5, out5 = run_job(f, apply=True, bq_delete=lambda _u, _s: {"tabelas_limpas": 0, "adiadas": 1, "sessoes": []})
        self.assertEqual(c5["bq_remocao_travada"], 1)
        self.assertIn("bq_remocao_travada: 1", out5)
        row = [r for r in f.purge_queue_rows() if r["uid"] == "UidTravado1"][0]
        self.assertEqual((row["bq_tentativas"], row["bq_concluido"], row["proxima_em"]), (5, 0, CS.stamp(NOW + timedelta(days=1))))
        # No dia seguinte, sem adiamento: conclui e sai.
        run_job(f, apply=True, now_dt=NOW + timedelta(days=1), bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []})
        self.assertEqual([r["uid"] for r in f.purge_queue_rows() if r["uid"] == "UidTravado1"], [])

        # Sessões gravadas antes do primeiro DELETE: falha depois da descoberta não perde as sessões.
        g = Fake(schema=True)
        g.add_purge_queue("UidMeio1", NOW_ISO, ga4_pendente=0, sessoes='[["psVelho",1]]')

        def bq_falha_no_meio(uid, saved, persistir):
            persistir([("psNovo", 2)])
            raise CS.GoogleIndisponivel("falha depois de um DELETE")

        cg, _ = run_job(g, apply=True, bq_delete=bq_falha_no_meio)
        self.assertEqual(cg["ga4_indisponivel"], 1)
        row_g = [r for r in g.purge_queue_rows() if r["uid"] == "UidMeio1"][0]
        self.assertEqual(json.loads(row_g["sessoes"]), [["psNovo", 2], ["psVelho", 1]])
        self.assertEqual((row_g["bq_tentativas"], row_g["bq_concluido"]), (0, 0))

        # Sessões acima do teto: a remoção não começa, nada é cortado e o item fica (bq_sessoes_excedidas).
        h = Fake(schema=True)
        h.add_purge_queue("UidMuitas1", NOW_ISO, ga4_pendente=0)
        deletes: list[str] = []

        def bq_muitas(uid, saved, persistir):
            persistir([(f"ps{i:07d}", i) for i in range(60000)])
            deletes.append(uid)  # nunca chega aqui
            return {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []}

        ch, outh = run_job(h, apply=True, bq_delete=bq_muitas)
        self.assertEqual((ch["bq_sessoes_excedidas"], deletes), (1, []))
        self.assertIn("bq_sessoes_excedidas: 1", outh)
        row_h = [r for r in h.purge_queue_rows() if r["uid"] == "UidMuitas1"][0]
        self.assertEqual((row_h["sessoes"], row_h["bq_concluido"]), ("[]", 0))

    def test_fila_reenfileirada_durante_o_google_nao_e_apagada_nem_sobrescrita(self):
        # Kimi P2 (rodada 3 do #204): o trigger reabre o item enquanto o BigQuery roda. O fechamento só vale na geração
        # lida, então a nova solicitação fica inteira na fila (GA4 pendente, tentativas zeradas, BigQuery a fazer).
        for tent, nome, falha in (
            (2, "fechamento (DELETE)", False),
            (0, "atualização (UPDATE)", False),
            (0, "falha do BigQuery (UPDATE do caminho de erro)", True),
        ):
            f = Fake(schema=True)
            f.add_purge_queue("UidCorrida1", NOW_ISO, ga4_pendente=0, bq_tentativas=tent)

            def bq_com_reabertura(uid, saved, persistir, _f=f, _falha=falha):
                _f.add_identity(uid, "corrida@corp.com")
                _f.db.execute("DELETE FROM site_identities WHERE uid = ?", (uid,))
                _f.db.commit()
                if _falha:
                    raise CS.GoogleIndisponivel("BigQuery fora depois da reabertura")
                return {"tabelas_limpas": 1, "adiadas": 0, "sessoes": [("psCorrida", 9)]}

            run_job(f, apply=True, bq_delete=bq_com_reabertura)
            rows = [r for r in f.purge_queue_rows() if r["uid"] == "UidCorrida1"]
            self.assertEqual(len(rows), 1, nome)
            r = rows[0]
            self.assertEqual(
                (r["ga4_pendente"], r["bq_tentativas"], r["bq_concluido"], r["geracao"]), (1, 0, 0, 1), nome
            )
            # As sessões descobertas no meio ficam (persistir/união não depende da geração).
            # Na rodada seguinte a nova geração é processada normalmente (proxima_em do trigger vem do relógio real).
            f.db.execute("UPDATE ga4_purge_queue SET proxima_em = ? WHERE uid = ?", (NOW_ISO, "UidCorrida1"))
            f.db.commit()
            ga4: list[str] = []
            run_job(f, apply=True, ga4_delete=lambda u: ga4.append(u) or True,
                    bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []})
            self.assertEqual(ga4, ["UidCorrida1"], nome)
            r2 = [x for x in f.purge_queue_rows() if x["uid"] == "UidCorrida1"][0]
            self.assertEqual((r2["ga4_pendente"], r2["bq_tentativas"], r2["geracao"]), (0, 1, 1), nome)

    def test_optout_com_sessoes_excedidas_avisa_e_sai_com_codigo_2(self):
        # Kimi P2 (rodada 3 do #204): GA4 feito e BigQuery sem começar por excesso de sessões não pode sair com 0.
        f = Fake(people=[person("p1", "muitas@corp.com")], schema=True)
        f.add_identity("UidMuitas9", "muitas@corp.com")

        def bq_muitas(uid, saved, persistir):
            persistir([(f"ps{i:07d}", i) for i in range(60000)])
            return {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []}

        rc, txt = self._cli(f, "--optout", "--apply", stdin="muitas@corp.com\n",
                            ga4_delete=lambda _u: True, bq_delete=bq_muitas)
        self.assertEqual(rc, 2, txt)
        self.assertIn("bq_sessoes_excedidas: 1", txt)
        self.assertIn(CS.BQ_PURGE_PENDENTE_MSG, txt)
        self.assertNotIn(CS.BQ_FILA_COM_ERRO_MSG, txt, "a falha desta rodada já tem a própria mensagem")
        self.assertNotIn("UidMuitas9", txt)
        row = [r for r in f.purge_queue_rows() if r["uid"] == "UidMuitas9"][0]
        self.assertEqual((row["ga4_pendente"], row["bq_concluido"]), (0, 0))
        self.assertEqual(f.db.execute("SELECT bq_erro FROM ga4_purge_queue WHERE uid = 'UidMuitas9'").fetchone()[0],
                         "sessoes_excedidas")
        # Repetir o comando (vínculo já apagado, nada a processar para o email) continua avisando e sai com 2.
        rc2, txt2 = self._cli(f, "--optout", "--apply", stdin="muitas@corp.com\n",
                              ga4_delete=lambda _u: True, bq_delete=bq_muitas)
        self.assertEqual(rc2, 2, txt2)
        self.assertIn("bq_fila_com_erro_total: 1", txt2)
        self.assertIn(CS.BQ_FILA_COM_ERRO_MSG, txt2)
        # O job diário que conclui a tentativa limpa o erro, e a repetição volta a sair com 0.
        f.db.execute("UPDATE ga4_purge_queue SET proxima_em = ? WHERE uid = 'UidMuitas9'", (NOW_ISO,))
        f.db.commit()
        run_job(f, apply=True, bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []})
        self.assertIsNone(f.db.execute("SELECT bq_erro FROM ga4_purge_queue WHERE uid = 'UidMuitas9'").fetchone()[0])
        rc3, txt3 = self._cli(f, "--optout", "--apply", stdin="muitas@corp.com\n")
        self.assertEqual(rc3, 0, txt3)
        self.assertIn("bq_fila_com_erro_total: 0", txt3)
        self.assertNotIn(CS.BQ_FILA_COM_ERRO_MSG, txt3)

    def test_ga4_sem_vinculo_oposicao_e_ligacao_so_na_sessao(self):
        f = Fake(schema=True)
        f.add_identity("UidLigado1", "ligado@corp.com")
        f.add_identity("UidOptout2", "opt@corp.com")
        f.add_optout("opt@corp.com")

        # Simula resultado da consulta com ligação retroativa apenas dentro da mesma sessão:
        # - UidLigado1 teve 1 sessão (page_view antes do login + auth_identified + scroll na mesma sessão)
        # - UidSemVinculo não está em site_identities -> ga4_sem_vinculo
        # - UidOptout2 está em heating_optout -> ignorado e contado em oposicao_aplicada
        ts = _ts_us(datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc))
        rows = [
            {
                "user_id": "UidLigado1", "sessoes": 1, "page_views": 2, "engagement_time_msec": 10000,
                "scrolls": 1, "cliques_contato": 0, "cliques_agenda": 0,
                "min_ts_visita": ts, "min_ts_engajamento": ts, "min_ts_contato": None, "min_ts_agenda": None,
            },
            {
                "user_id": "UidSemVinculo", "sessoes": 1, "page_views": 1, "engagement_time_msec": 70000,
                "scrolls": 1, "cliques_contato": 1, "cliques_agenda": 1,
                "min_ts_visita": ts, "min_ts_engajamento": ts, "min_ts_contato": ts, "min_ts_agenda": ts,
            },
            {
                "user_id": "UidOptout2", "sessoes": 1, "page_views": 1, "engagement_time_msec": 70000,
                "scrolls": 1, "cliques_contato": 0, "cliques_agenda": 1,
                "min_ts_visita": ts, "min_ts_engajamento": ts, "min_ts_contato": None, "min_ts_agenda": ts,
            },
        ]
        counts, out = run_job(f, apply=True, ga4_desde="2026-11-26", ler_ga4_dia=lambda _d: rows)
        self.assertEqual((counts["ga4_sem_vinculo"], counts["oposicao_aplicada"], counts["eventos_ga4_novos"]), (1, 1, 2))
        self.assertEqual(set(f.heating_rows()), {("site", "ligado@corp.com")})
        self.assertEqual(f.heating_rows()[("site", "ligado@corp.com")]["score"], 5)  # visita 2 + engajada 3 decaído 1d -> 5
        for secret in ("UidLigado1", "UidSemVinculo", "UidOptout2", "ligado@corp.com", "opt@corp.com"):
            self.assertNotIn(secret.lower(), out.lower())

    def test_ga4_indisponivel_segue_sem_fonte_e_nao_avanca_marcador(self):
        f = Fake(people=[person("p1", "ana@corp.com")], schema=True)
        f.add_conv(1, "ana@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_identity("UidAna123", "ana@corp.com")
        f.meta_set(H.GA4_META_KEY, "2026-11-24")

        # Falha no segundo dia (26) depois de ler o primeiro (25): a rodada segue sem GA4 e não avança o marcador.
        def fail_day2(dia: date):
            if dia == date(2026, 11, 25):
                ts = _ts_us(datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc))
                return [{
                    "user_id": "UidAna123", "sessoes": 1, "page_views": 1, "engagement_time_msec": 0,
                    "scrolls": 0, "cliques_contato": 0, "cliques_agenda": 0,
                    "min_ts_visita": ts, "min_ts_engajamento": None, "min_ts_contato": None, "min_ts_agenda": None,
                }]
            raise H.Ga4Indisponivel("BigQuery fora do ar")

        counts, out = run_job(f, apply=True, ga4_desde="2026-11-24", ler_ga4_dia=fail_day2)
        self.assertEqual((counts["ga4_indisponivel"], counts["ga4_dias_lidos"], counts["eventos_ga4_novos"]), (1, 0, 0))
        self.assertIn("ga4_indisponivel: 1", out)
        self.assertEqual(f.meta_get(H.GA4_META_KEY), "2026-11-24")  # marcador não avançou
        # Conversões seguiram normalmente.
        self.assertEqual(f.heating_rows()[("site", "ana@corp.com")]["score"], 12)

    def test_ler_ga4_dia_producao_usa_tabela_do_dia_sem_wildcard_e_filtro_consentimento(self):
        captured: dict = {"get_table": [], "queries": [], "projects": []}

        class NotFound(Exception):
            __module__ = "google.api_core.exceptions"
            code = 404

        class GoogleApiErr(Exception):
            __module__ = "google.api_core.exceptions"

        class _ScalarParam:
            def __init__(self, name, typ, value):
                self.name, self.type_, self.value = name, typ, value

        class _JobCfg:
            def __init__(self, query_parameters=()):
                self.query_parameters = list(query_parameters)

        class _QueryJob:
            def __init__(self, rows):
                self._rows = rows

            def result(self):
                return self._rows

        mode = {"behavior": "ok"}

        class _Client:
            def __init__(self, project=None, credentials=None):
                captured["projects"].append(project)

            def get_table(self, table_id):
                captured["get_table"].append(table_id)
                if mode["behavior"] == "not_found":
                    raise NotFound("tabela ainda não existe")
                if mode["behavior"] == "api_err":
                    raise GoogleApiErr("quota")
                if mode["behavior"] == "runtime_err":
                    raise RuntimeError("bug interno")
                return types.SimpleNamespace(location="southamerica-east1")

            def query(self, sql, job_config=None, location=None):
                captured["queries"].append((sql, job_config, location))
                return _QueryJob([{
                    "user_id": "UidProd1",
                    "sessoes": 1,
                    "page_views": 3,
                    "engagement_time_msec": 65000,
                    "scrolls": 1,
                    "cliques_contato": 0,
                    "cliques_agenda": 1,
                    "min_ts_visita": 1764151200000000,
                    "min_ts_engajamento": 1764151210000000,
                    "min_ts_contato": None,
                    "min_ts_agenda": 1764151220000000,
                }])

        fake_google = types.ModuleType("google")
        fake_cloud = types.ModuleType("google.cloud")
        fake_bq = types.ModuleType("google.cloud.bigquery")
        fake_oauth = types.ModuleType("google.oauth2")
        fake_sa = types.ModuleType("google.oauth2.service_account")

        class _Creds:
            @staticmethod
            def from_service_account_file(path):
                captured["sa_path"] = path
                return object()

        fake_bq.Client = _Client
        fake_bq.QueryJobConfig = _JobCfg
        fake_bq.ScalarQueryParameter = _ScalarParam
        fake_sa.Credentials = _Creds
        fake_cloud.bigquery = fake_bq
        fake_oauth.service_account = fake_sa
        fake_google.cloud = fake_cloud
        fake_google.oauth2 = fake_oauth

        mod_keys = ("google", "google.cloud", "google.cloud.bigquery", "google.oauth2", "google.oauth2.service_account")
        saved = {k: sys.modules.get(k) for k in mod_keys}
        sys.modules.update({
            "google": fake_google,
            "google.cloud": fake_cloud,
            "google.cloud.bigquery": fake_bq,
            "google.oauth2": fake_oauth,
            "google.oauth2.service_account": fake_sa,
        })
        try:
            # 1) Tabela existe: consulta exata na tabela do dia, sem wildcard, com filtro de consentimento e stream_id.
            rows = H.ler_ga4_dia(date(2026, 11, 26), sa_file="/tmp/sa.json")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["user_id"], "UidProd1")
            self.assertEqual(captured["projects"][-1], "mindapps")
            self.assertEqual(captured["get_table"][-1], "mindapps.analytics_341040810.events_20261126")
            sql, cfg, loc = captured["queries"][-1]
            self.assertIn("`mindapps.analytics_341040810.events_20261126`", sql)
            self.assertNotIn("events_*", sql)
            self.assertNotIn("intraday", sql.lower())
            # Asserções estruturais sobre a SQL normalizada (matam mutações M1, M2, M3, M4 e M6/M17).
            nsql = re.sub(r"\s+", " ", sql).strip()
            self.assertIn("WHERE stream_id = @stream_id AND privacy_info.analytics_storage = 'Yes'", nsql)
            self.assertIn(
                "sessoes_uid AS ( SELECT user_pseudo_id, ga_session_id, ANY_VALUE(user_id) AS session_user_id "
                "FROM base WHERE user_id IS NOT NULL AND user_id != '' AND user_pseudo_id IS NOT NULL "
                "AND ga_session_id IS NOT NULL GROUP BY user_pseudo_id, ga_session_id HAVING COUNT(DISTINCT user_id) = 1 )",
                nsql,
            )
            self.assertIn("COALESCE(NULLIF(b.user_id, ''), s.session_user_id) AS uid", nsql)
            self.assertIn(
                "FROM base AS b LEFT JOIN sessoes_uid AS s "
                "ON b.user_pseudo_id = s.user_pseudo_id AND b.ga_session_id = s.ga_session_id",
                nsql,
            )
            self.assertIn("FROM ligados WHERE uid IS NOT NULL AND uid != '' GROUP BY uid", nsql)
            self.assertEqual([(p.name, p.value) for p in cfg.query_parameters], [("stream_id", "13120304692")])
            self.assertEqual(loc, "southamerica-east1")

            # 2) Tabela inexistente (404 NotFound): devolve None sem rodar consulta.
            n_q = len(captured["queries"])
            mode["behavior"] = "not_found"
            self.assertIsNone(H.ler_ga4_dia(date(2026, 11, 26), sa_file="/tmp/sa.json"))
            self.assertEqual(len(captured["queries"]), n_q)

            # 3) Erro de API GCP -> Ga4Indisponivel; erro de programação -> propaga.
            mode["behavior"] = "api_err"
            with self.assertRaises(H.Ga4Indisponivel):
                H.ler_ga4_dia(date(2026, 11, 26), sa_file="/tmp/sa.json")
            mode["behavior"] = "runtime_err"
            with self.assertRaises(RuntimeError):
                H.ler_ga4_dia(date(2026, 11, 26), sa_file="/tmp/sa.json")
            # Sem SA file -> Ga4Indisponivel.
            with self.assertRaises(H.Ga4Indisponivel):
                H.ler_ga4_dia(date(2026, 11, 26), sa_file="")
        finally:
            for k, v in saved.items():
                sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)

    def test_ga4_day_query_sql_execucao_real_duckdb(self):
        try:
            import duckdb  # type: ignore[import-not-found]
        except ImportError:
            # No CI o duckdb é instalado no workflow: lá a ausência é falha, nunca pulo silencioso.
            if os.environ.get("CI"):
                raise
            self.skipTest("duckdb não disponível no Python local")

        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE events_20261126 (
              stream_id VARCHAR,
              privacy_info STRUCT(analytics_storage VARCHAR),
              user_id VARCHAR,
              user_pseudo_id VARCHAR,
              event_name VARCHAR,
              event_timestamp BIGINT,
              event_params STRUCT(key VARCHAR, value STRUCT(int_value BIGINT))[]
            )
            """
        )

        def ep(sid, eng=None):
            arr = []
            if sid is not None:
                arr.append({"key": "ga_session_id", "value": {"int_value": sid}})
            if eng is not None:
                arr.append({"key": "engagement_time_msec", "value": {"int_value": eng}})
            return arr

        # - Sessão (ps1, 100): page_view anônimo antes do login (eng 65s) + auth_identified com UidA -> liga retroativo!
        # - Outra sessão (ps1, 200): scroll anônimo -> NÃO liga a UidA (sessão diferente).
        # - Sessão mista (ps2, 300): scroll anônimo + UidB + UidC -> HAVING = 1 bloqueia ligação do anônimo.
        # - Sem consentimento / outro stream -> filtrados fora.
        # - Sem ga_session_id -> anônimo não liga.
        con.executemany(
            "INSERT INTO events_20261126 VALUES (?, {'analytics_storage': ?}, ?, ?, ?, ?, ?)",
            [
                (H.GA4_STREAM_ID, "Yes", None, "ps1", "page_view", 1000, ep(100, 65000)),
                (H.GA4_STREAM_ID, "Yes", "UidA", "ps1", "auth_identified", 2000, ep(100, 0)),
                (H.GA4_STREAM_ID, "Yes", None, "ps1", "scroll", 3000, ep(200, 5000)),
                (H.GA4_STREAM_ID, "Yes", None, "ps2", "scroll", 4000, ep(300, 0)),
                (H.GA4_STREAM_ID, "Yes", "UidB", "ps2", "page_view", 5000, ep(300, 1000)),
                (H.GA4_STREAM_ID, "Yes", "UidC", "ps2", "contact_click", 6000, ep(300, 0)),
                (H.GA4_STREAM_ID, "No", "UidSemCons", "ps3", "page_view", 7000, ep(400, 70000)),
                ("9999999999", "Yes", "UidOutroStream", "ps4", "page_view", 8000, ep(500, 70000)),
                (H.GA4_STREAM_ID, "Yes", None, "ps1", "schedule_click", 9000, ep(None, 0)),
            ],
        )

        raw_sql = H.ga4_day_query_sql(date(2026, 11, 26))
        duck_sql = (
            raw_sql.replace("`mindapps.analytics_341040810.events_20261126`", "events_20261126")
            .replace("@stream_id", f"'{H.GA4_STREAM_ID}'")
            .replace("UNNEST(event_params)", "UNNEST(event_params) AS _p(ep)")
            .replace("SELECT value.int_value FROM UNNEST(event_params) AS _p(ep) WHERE key =", "SELECT ep.value.int_value FROM UNNEST(event_params) AS _p(ep) WHERE ep.key =")
            .replace("CAST(ga_session_id AS STRING)", "CAST(ga_session_id AS VARCHAR)")
            .replace("COUNTIF(", "COUNT(*) FILTER (WHERE ")
            .replace("IF(engagement_time_msec > 0 OR event_name = 'scroll', event_timestamp, NULL)", "CASE WHEN engagement_time_msec > 0 OR event_name = 'scroll' THEN event_timestamp ELSE NULL END")
            .replace("IF(event_name = 'contact_click', event_timestamp, NULL)", "CASE WHEN event_name = 'contact_click' THEN event_timestamp ELSE NULL END")
            .replace("IF(event_name = 'schedule_click', event_timestamp, NULL)", "CASE WHEN event_name = 'schedule_click' THEN event_timestamp ELSE NULL END")
        )
        cols = [d[0] for d in con.execute(duck_sql).description]
        by_uid = {r[0]: dict(zip(cols, r)) for r in con.execute(duck_sql).fetchall()}
        self.assertEqual(set(by_uid), {"UidA", "UidB", "UidC"})
        # UidA recebeu o page_view + 65000ms anônimo da sessão 100, mas NÃO recebeu scroll da sessão 200 nem schedule_click sem sessão.
        self.assertEqual((by_uid["UidA"]["page_views"], by_uid["UidA"]["engagement_time_msec"], by_uid["UidA"]["scrolls"], by_uid["UidA"]["cliques_agenda"]), (1, 65000, 0, 0))
        self.assertEqual(by_uid["UidA"]["min_ts_visita"], 1000)
        # Sessão mista: scroll anônimo não foi para UidB nem UidC.
        self.assertEqual((by_uid["UidB"]["scrolls"], by_uid["UidC"]["scrolls"], by_uid["UidC"]["cliques_contato"]), (0, 0, 1))

    def test_fila_ga4_purge_queue_trigger_ciclo_completo_e_checks_0014(self):
        f = Fake(schema=True)
        # 1) CHECKs da migração 0014: uid, ga4_pendente, sessoes (json_valid e até 1.000.000 de caracteres), bq_concluido,
        #    bq_tentativas (0..5), geracao (>= 0) e datas.
        for kw in (
            {"uid": "uid_com_underscore"},
            {"uid": ""},
            {"uid": "UidOk1", "ga4_pendente": 2},
            {"uid": "UidOk1", "sessoes": "nao-json"},
            {"uid": "UidOk1", "sessoes": '"' + ("a" * 1000001) + '"'},
            {"uid": "UidOk1", "bq_concluido": 2},
            {"uid": "UidOk1", "bq_tentativas": -1},
            {"uid": "UidOk1", "bq_tentativas": 6},
            {"uid": "UidOk1", "geracao": -1},
            {"uid": "UidOk1", "bq_erro": "outro"},
            {"uid": "UidOk1", "proxima_em": "2026-11-27"},
            {"uid": "UidOk1", "criado_em": "2026-11-27"},
        ):
            with self.assertRaises(sqlite3.IntegrityError, msg=str(kw)):
                f.add_purge_queue(**kw)

        # 2) Trigger AFTER DELETE ON site_identities (como o Worker faz no POST /prioridade): enfileira automaticamente.
        f.add_identity("UidViaWorker1", "web@corp.com")
        f.db.execute("DELETE FROM site_identities WHERE email = ?", ("web@corp.com",))
        f.db.commit()
        q_trig = f.purge_queue_rows()
        self.assertEqual(len(q_trig), 1)
        self.assertEqual(
            (q_trig[0]["uid"], q_trig[0]["ga4_pendente"], q_trig[0]["sessoes"], q_trig[0]["bq_tentativas"]),
            ("UidViaWorker1", 1, "[]", 0),
        )
        # Atualiza proxima_em para NOW_ISO (para independência do relógio real nos testes) e testa ON CONFLICT preservando sessoes.
        f.db.execute(
            "UPDATE ga4_purge_queue SET ga4_pendente = 0, sessoes = ?, bq_tentativas = 2, bq_concluido = 1, proxima_em = ? "
            "WHERE uid = ?",
            ('[["ps0",77]]', CS.stamp(NOW + timedelta(days=2)), "UidViaWorker1"),
        )
        f.db.commit()
        f.add_identity("UidViaWorker1", "web@corp.com")
        f.db.execute("DELETE FROM site_identities WHERE email = ?", ("web@corp.com",))
        f.db.commit()
        q_retrig = f.purge_queue_rows()[0]
        # O trigger reabre tudo (inclusive bq_concluido) e preserva as sessões salvas.
        self.assertEqual(
            (q_retrig["ga4_pendente"], q_retrig["sessoes"], q_retrig["bq_tentativas"], q_retrig["bq_concluido"]),
            (1, '[["ps0",77]]', 0, 0),
        )
        f.db.execute("UPDATE ga4_purge_queue SET proxima_em = ? WHERE uid = ?", (NOW_ISO, "UidViaWorker1"))
        f.db.commit()

        # Também adiciona um item futuro (não vence agora).
        f.add_purge_queue("UidFuturo2", CS.stamp(NOW + timedelta(days=10)), ga4_pendente=0, bq_tentativas=2)

        # Simulação não chama Google nem altera fila.
        ga4_calls: list[str] = []
        bq_calls: list[tuple[str, list]] = []
        c_dry, _ = run_job(
            f, apply=False,
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertEqual((ga4_calls, bq_calls, c_dry["ga4_fila_processados"]), ([], [], 1))

        # 3) Ciclo completo no job diário:
        #    D0: GA4 dá 403 -> conta ga4_remocao_pendente=1; BQ recebe ps0 e descobre ps1 -> salva ambas, bq_tentativas=1, D+1.
        c_d0, out_d0 = run_job(
            f, apply=True, now_dt=NOW,
            ga4_delete=lambda u: ga4_calls.append(u) or False,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {
                "tabelas_limpas": 2, "adiadas": 1, "sessoes": [("ps1", 101)],
            },
        )
        self.assertEqual(
            (c_d0["ga4_remocao_pendente"], c_d0["bq_remocao_adiada"], c_d0["ga4_fila_processados"], c_d0["ga4_indisponivel"]),
            (1, 1, 1, 0),
        )
        self.assertIn("ga4_remocao_pendente: 1", out_d0)
        self.assertNotIn("uidviaworker1", out_d0.lower())
        self.assertEqual(bq_calls[-1], ("UidViaWorker1", [("ps0", 77)]))
        row_d0 = [r for r in f.purge_queue_rows() if r["uid"] == "UidViaWorker1"][0]
        self.assertEqual(
            (row_d0["ga4_pendente"], row_d0["sessoes"], row_d0["bq_tentativas"], row_d0["proxima_em"]),
            (1, '[["ps0",77],["ps1",101]]', 1, CS.stamp(NOW + timedelta(days=1))),
        )

        #    D+1: GA4 agora dá 200 -> zera ga4_pendente; BQ recebe as duas sessões salvas, bq_tentativas=2, D+3.
        ga4_calls.clear()
        bq_calls.clear()
        c_d1, _ = run_job(
            f, apply=True, now_dt=NOW + timedelta(days=1),
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertEqual((ga4_calls, bq_calls), (["UidViaWorker1"], [("UidViaWorker1", [("ps0", 77), ("ps1", 101)])]))
        self.assertEqual((c_d1["ga4_remocao_pendente"], c_d1["ga4_fila_processados"]), (0, 1))
        row_d1 = [r for r in f.purge_queue_rows() if r["uid"] == "UidViaWorker1"][0]
        self.assertEqual((row_d1["ga4_pendente"], row_d1["bq_tentativas"], row_d1["proxima_em"]), (0, 2, CS.stamp(NOW + timedelta(days=3))))

        #    D+3: GA4 não é mais chamado (ga4_pendente=0); BQ tem tabela adiada (streaming buffer) -> NÃO sai da fila!
        ga4_calls.clear()
        bq_calls.clear()
        c_d3, _ = run_job(
            f, apply=True, now_dt=NOW + timedelta(days=3),
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 0, "adiadas": 1, "sessoes": []},
        )
        self.assertEqual((ga4_calls, c_d3["bq_remocao_adiada"]), ([], 1))
        row_d3 = [r for r in f.purge_queue_rows() if r["uid"] == "UidViaWorker1"][0]
        self.assertEqual((row_d3["bq_tentativas"], row_d3["proxima_em"]), (3, CS.stamp(NOW + timedelta(days=4))))

        #    D+4: ainda adiada -> bq_tentativas=4, D+5.
        run_job(
            f, apply=True, now_dt=NOW + timedelta(days=4),
            ga4_delete=lambda _u: True,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 0, "adiadas": 1, "sessoes": []},
        )
        row_d4 = [r for r in f.purge_queue_rows() if r["uid"] == "UidViaWorker1"][0]
        self.assertEqual((row_d4["bq_tentativas"], row_d4["proxima_em"]), (4, CS.stamp(NOW + timedelta(days=5))))

        #    D+5: 5ª tentativa (teto) -> sai da fila; só UidFuturo2 permanece!
        run_job(
            f, apply=True, now_dt=NOW + timedelta(days=5),
            ga4_delete=lambda _u: True,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertEqual([r["uid"] for r in f.purge_queue_rows()], ["UidFuturo2"])

        # 4) Item NÃO sai da fila quando BQ termina (D+3 sem adiamento) mas GA4 continua em 403; sai quando GA4 volta 200
        #    (e não chama BQ de novo).
        f.add_purge_queue("UidSoGa4", NOW_ISO, ga4_pendente=1, bq_tentativas=2)
        c_keep, _ = run_job(
            f, apply=True, now_dt=NOW,
            ga4_delete=lambda _u: False,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertEqual(c_keep["ga4_remocao_pendente"], 1)
        row_keep = [r for r in f.purge_queue_rows() if r["uid"] == "UidSoGa4"][0]
        self.assertEqual((row_keep["ga4_pendente"], row_keep["bq_tentativas"], row_keep["bq_concluido"]), (1, 3, 1))

        bq_extra: list[str] = []
        c_done, _ = run_job(
            f, apply=True, now_dt=NOW + timedelta(days=1),
            ga4_delete=lambda _u: True,
            bq_delete=lambda u, _s: bq_extra.append(u) or {},
        )
        self.assertEqual((bq_extra, c_done["ga4_remocao_pendente"]), ([], 0))
        self.assertEqual([r["uid"] for r in f.purge_queue_rows()], ["UidFuturo2"])

        # 5) Se BigQuery falhar ao processar a fila, ga4_indisponivel=1, ga4_fila_processados=0 e linha fica na fila.
        f.add_purge_queue("UidFalha3", NOW_ISO, ga4_pendente=0, bq_tentativas=1)

        def fail_bq(_uid, _sess):
            raise CS.GoogleIndisponivel("BQ indisponível")

        c_fail, _ = run_job(f, apply=True, bq_delete=fail_bq)
        self.assertEqual((c_fail["ga4_indisponivel"], c_fail["ga4_fila_processados"]), (1, 0))
        self.assertEqual([r["uid"] for r in f.purge_queue_rows()], ["UidFalha3", "UidFuturo2"])

    def test_site_identities_lida_com_oposicao_ou_fila_mesmo_sem_ga4_desde(self):
        # 1) HEATING_GA4_DESDE ausente (0 dias do GA4), mas há oposição em `heating_optout` com `site_identities`
        #    remanescente: o job lê `site_identities`, apaga (disparando o trigger) e processa GA4 + BigQuery!
        f = Fake(schema=True)
        f.add_optout("opt@corp.com")
        f.add_identity("UidOptPendente1", "opt@corp.com")
        f.add_identity("UidAtivo2", "ativo@corp.com")
        ga4_calls: list[str] = []
        bq_calls: list[tuple[str, list]] = []

        c, _ = run_job(
            f, apply=True, ga4_desde=None,
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": [("psOpt", 9)]},
        )
        self.assertEqual((c["oposicao_aplicada"], c["ga4_dias_lidos"], c["ga4_fila_processados"]), (1, 0, 1))
        self.assertEqual((ga4_calls, bq_calls), (["UidOptPendente1"], [("UidOptPendente1", [])]))
        self.assertEqual([r[0] for r in f.db.execute("SELECT uid FROM site_identities")], ["UidAtivo2"])
        q = f.purge_queue_rows()
        self.assertEqual([(r["uid"], r["ga4_pendente"], r["bq_tentativas"]) for r in q], [("UidOptPendente1", 0, 1)])

        # 2) HEATING_GA4_DESDE ausente e sem oposição, mas há item vencido na fila: `site_identities` também é lida.
        g = Fake(schema=True)
        g.add_purge_queue("UidNaFila1", NOW_ISO, ga4_pendente=1, bq_tentativas=2)
        run_job(
            g, apply=True, ga4_desde=None,
            ga4_delete=lambda _u: True,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertTrue(
            any(isinstance(d, dict) and "FROM site_identities" in d.get("sql", "") for _m, _p, d, _q in g.calls)
        )
        self.assertEqual(g.purge_queue_rows(), [])

    def test_optout_remove_no_google_agenda_fila_trata_403_sem_sa_e_forget_curso_nao_mexe_no_ga4(self):
        # 1) --optout com sucesso: apaga site_identities (trigger enfileira), processa GA4 e BigQuery na hora, agenda D+1.
        f = Fake(
            people=[person("p1", "segredo@corp.com", heatingNota=50, heatingFaixa="QUENTE", heatingSinais="x", heatingCalculadoEm=NOW_ISO)],
            schema=True,
        )
        f.add_identity("UidSecretoA1", "segredo@corp.com")
        f.add_identity("UidSecretoB2", "segredo@corp.com")
        f.add_identity("UidOutroC3", "outro@corp.com")
        f.add_event("ev-1", "segredo@corp.com", "site_visita", source="ga4", ref=None, when=ago(days=1))

        ga4_calls: list[str] = []
        bq_calls: list[tuple[str, list]] = []

        # Simulação não chama Google nem apaga site_identities.
        rc_dry, txt_dry = self._cli(
            f, "--optout", stdin="segredo@corp.com\n",
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
        )
        self.assertEqual((rc_dry, ga4_calls, bq_calls), (0, [], []))
        self.assertIn("identidades_site: 2", txt_dry)
        self.assertEqual(f.db.execute("SELECT count(*) FROM site_identities").fetchone()[0], 3)

        # --apply executa tudo e não vaza uid nem email.
        rc_ok, txt_ok = self._cli(
            f, "--optout", "--apply", stdin="segredo@corp.com\n",
            ga4_delete=lambda u: ga4_calls.append(u) or True,
            bq_delete=lambda u, s: bq_calls.append((u, list(s))) or {
                "tabelas_limpas": 2, "adiadas": 1, "sessoes": [("ps-" + u, 42)],
            },
        )
        self.assertEqual(rc_ok, 0, txt_ok)
        self.assertEqual(ga4_calls, ["UidSecretoA1", "UidSecretoB2"])
        self.assertEqual(bq_calls, [("UidSecretoA1", []), ("UidSecretoB2", [])])
        self.assertIn("ga4_removidos: 2", txt_ok)
        self.assertIn("bq_remocao_adiada: 2", txt_ok)
        self.assertNotIn(H.GA4_PURGE_PENDENTE_MSG, txt_ok)
        for secret in ("segredo@corp.com", "UidSecretoA1", "UidSecretoB2"):
            self.assertNotIn(secret.lower(), txt_ok.lower())
        # Só a identidade de outro@corp.com sobrou.
        rem_ids = [r[0] for r in f.db.execute("SELECT uid FROM site_identities")]
        self.assertEqual(rem_ids, ["UidOutroC3"])
        # Fila com sessões salvas, ga4_pendente=0, bq_tentativas=1, proxima_em=D+1.
        qrows = [
            (r["uid"], r["ga4_pendente"], r["sessoes"], r["bq_tentativas"], r["proxima_em"])
            for r in f.purge_queue_rows()
        ]
        self.assertEqual(
            qrows,
            [
                ("UidSecretoA1", 0, '[["ps-UidSecretoA1",42]]', 1, CS.stamp(NOW + timedelta(days=1))),
                ("UidSecretoB2", 0, '[["ps-UidSecretoB2",42]]', 1, CS.stamp(NOW + timedelta(days=1))),
            ],
        )

        # 2) 403 no GA4: código 2 + mensagem fixa; BigQuery, fila (com ga4_pendente=1), site_identities, D1 e Twenty concluídos!
        g = Fake(
            people=[person("p1", "falha403@corp.com", heatingNota=30, heatingFaixa="MORNO", heatingSinais="x", heatingCalculadoEm=NOW_ISO)],
            schema=True,
        )
        g.add_identity("UidQuatroZeroTres", "falha403@corp.com")
        g.add_event("ev-403", "falha403@corp.com", "site_visita", source="ga4", ref=None, when=ago(days=1))
        bq_403: list[str] = []
        rc_403, txt_403 = self._cli(
            g, "--optout", "--apply", stdin="falha403@corp.com\n",
            ga4_delete=lambda _u: False,  # simula 403 no GA4
            bq_delete=lambda u, _s: bq_403.append(u) or {"tabelas_limpas": 1, "adiadas": 0, "sessoes": [("ps403", 7)]},
        )
        self.assertEqual(rc_403, 2, txt_403)
        self.assertIn("ga4_remocao_pendente: 1", txt_403)
        self.assertIn(H.GA4_PURGE_PENDENTE_MSG, txt_403)
        self.assertEqual(bq_403, ["UidQuatroZeroTres"])
        self.assertEqual(g.db.execute("SELECT count(*) FROM site_identities").fetchone()[0], 0)
        self.assertEqual(g.event_ids(), [])
        self.assertIsNone(g.people["p1"]["heatingNota"])
        self.assertEqual(
            [(r["uid"], r["ga4_pendente"], r["bq_tentativas"], r["sessoes"]) for r in g.purge_queue_rows()],
            [("UidQuatroZeroTres", 1, 1, '[["ps403",7]]')],
        )
        for secret in ("falha403@corp.com", "UidQuatroZeroTres"):
            self.assertNotIn(secret.lower(), txt_403.lower())

        # 3) GA4 200 mas BigQuery falha: código 2 + BQ_PURGE_PENDENTE_MSG + bq_tentativas=0 com proxima_em=agora (mata M15/M17).
        m = Fake(
            people=[person("p1", "bqfail@corp.com", heatingNota=25, heatingFaixa="MORNO", heatingSinais="x", heatingCalculadoEm=NOW_ISO)],
            schema=True,
        )
        m.add_identity("UidBqFailOpt", "bqfail@corp.com")

        def fail_bq(_u, _s):
            raise CS.GoogleIndisponivel("BQ fora")

        rc_bq, txt_bq = self._cli(
            m, "--optout", "--apply", stdin="bqfail@corp.com\n",
            ga4_delete=lambda _u: True,
            bq_delete=fail_bq,
        )
        self.assertEqual(rc_bq, 2, txt_bq)
        self.assertIn("bq_remocao_falhou: 1", txt_bq)
        self.assertIn(H.BQ_PURGE_PENDENTE_MSG, txt_bq)
        self.assertNotIn(H.GA4_PURGE_PENDENTE_MSG, txt_bq)
        self.assertEqual(
            [(r["uid"], r["ga4_pendente"], r["bq_tentativas"], r["proxima_em"]) for r in m.purge_queue_rows()],
            [("UidBqFailOpt", 0, 0, NOW_ISO)],
        )

        # 4) Sem HEATING_GCP_SA_FILE (ex.: no Mac): apaga site_identities (trigger enfileira), informa e sai com código 2
        #    (a remoção no GA4 continua pendente na fila até o job diário concluir).
        h = Fake(
            people=[person("p1", "mac@corp.com", heatingNota=20, heatingFaixa="MORNO", heatingSinais="x", heatingCalculadoEm=NOW_ISO)],
            schema=True,
        )
        h.add_identity("UidSemSaMac1", "mac@corp.com")
        rc_mac, txt_mac = self._cli(h, "--optout", "--apply", stdin="mac@corp.com\n")
        self.assertEqual(rc_mac, 2, txt_mac)
        self.assertIn("ga4_fila_pendente_total: 1", txt_mac)
        self.assertIn("ga4_fila_enfileirados: 1", txt_mac)
        self.assertIn(H.GA4_FILA_SEM_CRED_MSG, txt_mac)
        self.assertNotIn(H.GA4_PURGE_PENDENTE_MSG, txt_mac)
        self.assertEqual(h.db.execute("SELECT count(*) FROM site_identities").fetchone()[0], 0)
        self.assertIsNone(h.people["p1"]["heatingNota"])
        self.assertEqual(
            [(r["uid"], r["ga4_pendente"], r["bq_tentativas"], r["sessoes"]) for r in h.purge_queue_rows()],
            [("UidSemSaMac1", 1, 0, "[]")],
        )
        for secret in ("mac@corp.com", "UidSemSaMac1"):
            self.assertNotIn(secret.lower(), txt_mac.lower())

        # 5) --forget-curso NÃO mexe no GA4, BigQuery, site_identities, ga4_purge_queue nem heating_optout.
        k = Fake(people=[person("p1", "curso@corp.com")], schema=True)
        k.add_identity("UidCursoIntacto", "curso@corp.com")
        k.add_event("ev-c", "curso@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        rc_fc, txt_fc = self._cli(
            k, "--forget-curso", SLUG, "--apply", stdin="curso@corp.com\n",
            ga4_delete=lambda _u: (_ for _ in ()).throw(AssertionError("não deve chamar GA4")),
            bq_delete=lambda _u, _s: (_ for _ in ()).throw(AssertionError("não deve chamar BigQuery")),
        )
        self.assertEqual(rc_fc, 0, txt_fc)
        self.assertEqual([r[0] for r in k.db.execute("SELECT uid FROM site_identities")], ["UidCursoIntacto"])
        self.assertEqual(k.purge_queue_rows(), [])
        self.assertEqual(k.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 0)

    def test_forget_site_grava_heating_optout_e_job_depois_nao_recria_nota_enquanto_forget_curso_nao_grava(self):
        # Decisão do Gui (2026-11-27):
        # 1) conversoes_sync.py --forget --apply grava heating_optout (mesmo HMAC do Worker) antes de apagar o resto,
        #    e uma rodada seguinte de heating_sync.py --apply não recria nota (nem no D1 nem no Twenty) para o email.
        f = Fake(
            people=[
                person("p1", "alvo@corp.com", heatingNota=62, heatingFaixa="QUENTE", heatingSinais="Download de material", heatingCalculadoEm=NOW_ISO),
                person("p2", "outro@corp.com"),
            ],
            schema=True,
        )
        f.add_conv(1, "alvo@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=1))
        f.add_event("ev-alvo-site", "alvo@corp.com", "site_agende", source="ga4", ref=None, when=ago(days=1))
        f.db.execute(
            "INSERT INTO heating (context, email, score, band, signals, computed_at, twenty_person_id) "
            "VALUES ('site', 'alvo@corp.com', 62, 'quente', '[]', ?, 'p1')",
            (NOW_ISO,),
        )
        f.add_identity("UidAlvo1", "alvo@corp.com")
        f.add_identity("UidOutro2", "outro@corp.com")
        f.db.commit()

        out_forget = io.StringIO()
        CS.forget(
            TS.Clients(ENV, f, sleep=lambda s: None),
            "  Alvo@Corp.com ",
            apply=True,
            out=out_forget,
            forget_key=FORGET_KEY,
            ga4_delete=lambda _u: True,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 1, "adiadas": 0, "sessoes": []},
            now_dt=NOW,
        )
        expected_mac = H.heating_optout_hmac("alvo@corp.com", FORGET_KEY)
        opt_rows = [dict(r) for r in f.db.execute("SELECT email_hmac, opted_out_at FROM heating_optout")]
        self.assertEqual(len(opt_rows), 1)
        self.assertEqual(opt_rows[0]["email_hmac"], expected_mac)
        self.assertRegex(opt_rows[0]["opted_out_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertIsNone(f.people["p1"]["heatingNota"])

        # Chegam novos sinais depois da remoção (nova conversão, atividade no curso, evento no GA4 e outro email ativo).
        f.add_conv(2, "alvo@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        f.add_conv(3, "outro@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        curso_docs = [
            {
                "email": "alvo@corp.com",
                "status": "active",
                "source": "google",
                "googleUid": "uid_alvo_c",
                "name": "Alvo",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            }
        ]
        ts = _ts_us(datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc))
        ga4_rows = [
            {
                "user_id": "UidOutro2", "sessoes": 1, "page_views": 2, "engagement_time_msec": 70000,
                "scrolls": 1, "cliques_contato": 0, "cliques_agenda": 0,
                "min_ts_visita": ts, "min_ts_engajamento": ts, "min_ts_contato": None, "min_ts_agenda": None,
            }
        ]
        counts, _ = run_job(
            f,
            apply=True,
            ler_curso=lambda _s: curso_docs,
            ga4_desde="2026-11-26",
            ler_ga4_dia=lambda _d: ga4_rows,
            ga4_delete=lambda _u: True,
            bq_delete=lambda _u, _s: {"tabelas_limpas": 0, "adiadas": 0, "sessoes": []},
        )
        self.assertGreaterEqual(counts["oposicao_aplicada"], 1)
        hrows = f.heating_rows()
        self.assertNotIn(("site", "alvo@corp.com"), hrows)
        self.assertNotIn((CTX_CURSO, "alvo@corp.com"), hrows)
        self.assertEqual(
            f.db.execute("SELECT count(*) FROM engagement_events WHERE email = 'alvo@corp.com'").fetchone()[0],
            0,
        )
        for k in H.PERSON_HEATING_FIELDS:
            self.assertIsNone(f.people["p1"].get(k), k)
        # Outro email continua pontuando normalmente.
        self.assertIn(("site", "outro@corp.com"), hrows)
        self.assertGreater(f.people["p2"]["heatingNota"], 0)

        # 2) --forget-curso NÃO grava heating_optout: o curso fica esquecido, mas o contexto `site` segue pontuando.
        g = Fake(people=[person("p1", "aluno@corp.com")], schema=True)
        g.add_event("ev-c", "aluno@corp.com", "curso_cadastro", context=CTX_CURSO, source="curso", ref=None, when=ago(days=1))
        rc_fc, txt_fc = self._cli(g, "--forget-curso", SLUG, "--apply", stdin="aluno@corp.com\n")
        self.assertEqual(rc_fc, 0, txt_fc)
        self.assertEqual(g.db.execute("SELECT count(*) FROM curso_esquecido").fetchone()[0], 1)
        self.assertEqual(g.db.execute("SELECT count(*) FROM heating_optout").fetchone()[0], 0)

        # Na rodada seguinte do job, o curso segue bloqueado, enquanto uma conversão no site gera nota normalmente.
        g.add_conv(1, "aluno@corp.com", action="material_download", material="planilha-custos-ia", when=ago(days=0))
        aluno_docs = [
            {
                "email": "aluno@corp.com",
                "status": "active",
                "source": "google",
                "googleUid": "uid_aluno",
                "name": "Aluno",
                "createdAt": ago(days=0),
                "aulas": [{"dayId": "day-01", "completed": True, "updatedAt": ago(days=0)}],
            }
        ]
        run_job(g, apply=True, ler_curso=lambda _s: aluno_docs)
        hrows_g = g.heating_rows()
        self.assertNotIn((CTX_CURSO, "aluno@corp.com"), hrows_g)
        self.assertIn(("site", "aluno@corp.com"), hrows_g)
        self.assertEqual(hrows_g[("site", "aluno@corp.com")]["score"], 12)
        self.assertEqual(g.people["p1"]["heatingNota"], 12)


if __name__ == "__main__":
    unittest.main()
