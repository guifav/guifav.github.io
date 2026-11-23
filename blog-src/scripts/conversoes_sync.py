"""Leva as conversões do site (tabela `conversions` do D1 `blog-admin`) para o Twenty (crm.guifav.dev). Issue #97, fase 2.

Fonte: as linhas de `conversions` com `synced_at` nulo, gravadas pelo Worker `blog-newsletter` (fase 1, PRs #98 e #100).
Simulação por padrão: lê o D1 e o Twenty e mostra só contagens. Grava apenas com --apply.

No Twenty, criados se faltarem (metadata API):
- Objeto `siteConversion` ("Conversão do site"): acao (SELECT), comprovado (BOOLEAN), material e arquivo (TEXT), interesses
  (MULTI_SELECT), ocorreuEm (DATE_TIME), eventoId (TEXT único, o `id` do D1), pedidoId e gaClientId (TEXT) e a relação N:1
  `pessoa` com Person.
- Na Person: siteInteresses (MULTI_SELECT, união dos interesses das conversões), siteUltimaConversao (DATE_TIME),
  siteEmailComprovado (BOOLEAN: alguma conversão com o email comprovado por link) e siteGaClientId (TEXT, o client_id do
  GA4 da conversão mais recente que trouxe um).

Regras:
- Correspondência por email, no principal e nos adicionais, sem diferença de caixa (a mesma do twenty_sync.py). Mais de
  uma Pessoa com o email: a conversão vai para a mais antiga (createdAt, depois id), e a contagem mostra o caso.
- Pessoa nova só nasce de uma ação com o email comprovado (download ou confirmação da newsletter), só com o email.
- Pedido sem clique (`material_request`, `verified = 0`) só vira Conversão se o email já estiver numa Pessoa (decisão do
  Gui na #97, 2026-11-23). Sem Pessoa, a linha fica pendente no D1: se o email chegar ao CRM depois (pelo download, pela
  newsletter ou à mão), o pedido vai junto na rodada seguinte.
- Idempotente: a Conversão é identificada pelo eventoId (campo único no Twenty); a que já existe não é criada de novo, e
  a linha é marcada com `synced_at` depois que a Pessoa e as Conversões do email foram gravadas.
- siteInteresses só ganha valores, nunca perde: uma tag tirada à mão volta se a pessoa tiver uma conversão nova com o tema.
  Um interesse novo no catálogo vira opção nova das duas multi-seleções, com o rótulo do catálogo.
- Linha malformada (ação, email, datas, interesses ou ids fora do formato) não é gravada nem marcada, e é contada em
  `linha_invalida` (o wrapper alerta).
- Lê e valida tudo (D1, metadados, Pessoas e Conversões) antes de qualquer escrita. A saída tem só contagens, nunca emails.

Remoção (direito do titular), --forget: lê UM email da entrada padrão (fora do histórico do shell e da lista de
processos), apaga as linhas dele no D1 primeiro (para o sync não recriar nada) e depois as Conversões dele no Twenty. A
Pessoa e os campos site* dela ficam como estão (decisão do Gui, 2026-11-23). Simulação sem --apply.

Rodar na KVM (todas as credenciais vêm do ambiente):
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... D1_DATABASE_ID=... TWENTY_API_KEY=... \\
    TWENTY_LOCAL_URL=http://127.0.0.1:3100 python3 scripts/conversoes_sync.py [--apply]
    printf '%s\\n' "$EMAIL" | ... python3 scripts/conversoes_sync.py --forget [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, OrderedDict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twenty_sync as TS  # noqa: E402
from twenty_sync import SyncError  # noqa: E402

OBJ = {"nameSingular": "siteConversion", "namePlural": "siteConversions", "labelSingular": "Conversão do site",
       "labelPlural": "Conversões do site", "icon": "IconTargetArrow", "isLabelSyncedWithName": False,
       "description": "Ações do site guifav.dev com email (pedido e download de material, confirmação da newsletter). "
                      "Gravadas pelo sync da issue #97 a partir do D1 blog-admin; não edite à mão."}
PLURAL = OBJ["namePlural"]
ACOES = {
    "material_request": ("MATERIAL_REQUEST", "Pedido de material"),
    "material_download": ("MATERIAL_DOWNLOAD", "Download de material"),
    "newsletter_confirm": ("NEWSLETTER_CONFIRM", "Inscrição confirmada"),
}
COLORS = ("blue", "purple", "green", "orange", "turquoise", "pink", "yellow", "red", "sky", "gray")
CONV_FIELDS = OrderedDict([
    ("acao", {"type": "SELECT", "label": "Ação", "icon": "IconClick", "options": [
        {"value": v, "label": lbl, "color": COLORS[i], "position": i} for i, (v, lbl) in enumerate(ACOES.values())]}),
    ("comprovado", {"type": "BOOLEAN", "label": "Email comprovado", "icon": "IconMailCheck"}),
    ("material", {"type": "TEXT", "label": "Material", "icon": "IconFileDownload"}),
    ("arquivo", {"type": "TEXT", "label": "Arquivo baixado", "icon": "IconFile"}),
    ("interesses", {"type": "MULTI_SELECT", "label": "Interesses", "icon": "IconTags"}),
    ("ocorreuEm", {"type": "DATE_TIME", "label": "Ocorreu em", "icon": "IconCalendarEvent"}),
    ("eventoId", {"type": "TEXT", "label": "Id do evento (D1)", "icon": "IconHash", "isUnique": True}),
    ("pedidoId", {"type": "TEXT", "label": "Id do pedido (D1)", "icon": "IconLink"}),
    ("gaClientId", {"type": "TEXT", "label": "client_id do GA4", "icon": "IconChartLine"}),
])
RELATION = {"name": "pessoa", "type": "RELATION", "label": "Pessoa", "icon": "IconUser",
            "inverse_label": "Conversões do site", "inverse_icon": "IconTargetArrow"}
PERSON_FIELDS = OrderedDict([
    ("siteInteresses", {"type": "MULTI_SELECT", "label": "Interesses (site)", "icon": "IconTags"}),
    ("siteUltimaConversao", {"type": "DATE_TIME", "label": "Última conversão no site", "icon": "IconCalendarEvent"}),
    ("siteEmailComprovado", {"type": "BOOLEAN", "label": "Email comprovado no site", "icon": "IconMailCheck"}),
    ("siteGaClientId", {"type": "TEXT", "label": "client_id do GA4 (site)", "icon": "IconChartLine"}),
])
MULTI = {"interesses", "siteInteresses"}

HEX64 = re.compile(r"[0-9a-f]{64}")
EMAIL = re.compile(r"[^\s@A-Z]+@[^\s@A-Z]+\.[^\s@A-Z]+")  # o Worker grava em minúsculas
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
INTEREST = re.compile(r"[A-Z][A-Z0-9_]{0,62}")
GA_CID = re.compile(r"\d{1,20}\.\d{1,20}")
MAX_LABEL = 120
MARK_CHUNK = 50  # ids por UPDATE no D1 (limite de 100 parâmetros por consulta)
FORGET_MARK = "conversões apagadas"


def _full(rx: re.Pattern, v) -> bool:
    return isinstance(v, str) and rx.fullmatch(v) is not None


def parse_row(r: dict) -> dict | None:
    """Linha do D1 validada e normalizada, ou None se algo estiver fora do formato que o Worker grava."""
    try:
        action, verified = r.get("action"), r.get("verified")
        if action not in ACOES or verified not in (0, 1) or isinstance(verified, bool):
            return None
        if (action == "material_request") != (verified == 0):
            return None  # só o pedido é não comprovado
        if not _full(HEX64, r.get("id")) or not _full(EMAIL, r.get("email")):
            return None
        material = r.get("material")
        if action == "newsletter_confirm":
            if material is not None:
                return None
        elif not _full(SLUG, material):
            return None
        for k, rx in (("file", SLUG), ("request_id", HEX64), ("ga_client_id", GA_CID)):
            if r.get(k) is not None and not _full(rx, r.get(k)):
                return None
        if r.get("file") is not None and action != "material_download":
            return None
        if r.get("request_id") is not None and action != "material_download":
            return None
        when = TS.iso(r.get("occurred_at") or "")
        if not when:
            return None
        raw = json.loads(r.get("interests") or "")
        if not isinstance(raw, list):
            return None
        interests: OrderedDict[str, str] = OrderedDict()
        for it in raw:
            if not isinstance(it, dict) or not _full(INTEREST, it.get("id")):
                return None
            label = it.get("rotulo")
            if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
                return None
            interests.setdefault(it["id"], label.strip())
        return {"id": r["id"], "email": r["email"], "action": action, "verified": verified == 1, "material": material,
                "file": r.get("file"), "request_id": r.get("request_id"), "ga": r.get("ga_client_id"), "when": when,
                "interests": interests}
    except (ValueError, TypeError, AttributeError):
        return None


def read_metadata(c: TS.Clients) -> dict:
    q = ("{ objects(paging:{first:200}){ edges{ node{ id nameSingular namePlural "
         "fields(paging:{first:500}){ edges{ node{ id name type options } } } } } } }")
    data = c.twenty("POST", "/metadata", {"query": q}, op="ler metadados")
    try:
        nodes = {e["node"]["nameSingular"]: e["node"] for e in data["data"]["objects"]["edges"]}
        person = nodes["person"]
        conv = nodes.get(OBJ["nameSingular"])

        def fields(o):
            return {f["node"]["name"]: f["node"] for f in o["fields"]["edges"]} if o else {}
        meta = {"person_id": person["id"], "person": fields(person), "conv_id": conv["id"] if conv else None, "conv": fields(conv)}
    except (KeyError, TypeError) as e:
        raise SyncError("metadados do Twenty em formato inesperado") from e
    if conv and conv.get("namePlural") != PLURAL:
        raise SyncError("objeto siteConversion com plural inesperado")
    # Campo com o nome certo e tipo errado: recusa em vez de gravar no lugar errado.
    for spec, have in ((PERSON_FIELDS, meta["person"]), (CONV_FIELDS, meta["conv"])):
        for k, f in spec.items():
            if k in have and have[k]["type"] != f["type"]:
                raise SyncError(f"campo {k} existe com outro tipo")
    if RELATION["name"] in meta["conv"] and meta["conv"][RELATION["name"]]["type"] != "RELATION":
        raise SyncError("campo pessoa existe com outro tipo")
    return meta


def list_records(c: TS.Clients, plural: str) -> list[dict]:
    """Listagem REST completa (mesmo contrato de paginação do twenty_sync.list_people)."""
    out, cursor = [], ""
    for _ in range(TS.MAX_PAGES):
        q = f"?limit={TS.PAGE}" + (f"&starting_after={urllib.parse.quote(cursor)}" if cursor else "")
        data = c.twenty("GET", f"/rest/{plural}{q}", op=f"listar {plural}")
        page = (data.get("data") or {}).get(plural)
        info = data.get("pageInfo")
        if not isinstance(page, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
            raise SyncError(f"listagem de {plural} inválida")
        out += page
        if not info["hasNextPage"]:
            return out
        nxt = info.get("endCursor") or ""
        if not nxt or nxt == cursor:
            raise SyncError(f"paginação de {plural} sem progresso")
        cursor = nxt
    raise SyncError(f"paginação sem fim em {plural}")


def people_by_email(people: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for p in sorted(people, key=lambda p: (TS.iso(p.get("createdAt") or "") or "9999", p["id"])):
        for e in TS._emails(p):
            idx.setdefault(e, []).append(p)
    return idx


def _option_values(field: dict | None) -> list[str]:
    return [o["value"] for o in (field or {}).get("options") or []]


def person_patch(p: dict | None, rows: list[dict]) -> dict:
    """Campos site* desejados para a Pessoa (p None: Pessoa nova), só os que mudam."""
    cur_int = list((p or {}).get("siteInteresses") or [])
    merged = cur_int + [i for r in rows for i in r["interests"] if i not in cur_int]
    merged = list(OrderedDict.fromkeys(merged))
    cur_last = TS.iso((p or {}).get("siteUltimaConversao") or "")
    last = max([cur_last] + [r["when"] for r in rows])
    proven = bool((p or {}).get("siteEmailComprovado")) or any(r["verified"] for r in rows)
    want = {"siteInteresses": merged, "siteUltimaConversao": last, "siteEmailComprovado": proven}
    with_ga = [r for r in rows if r["ga"]]
    cur_ga = (p or {}).get("siteGaClientId") or ""
    if with_ga:
        newest = max(with_ga, key=lambda r: (r["when"], r["id"]))
        # Só troca um client_id já gravado por outro de uma conversão tão ou mais recente que a última conhecida.
        if not cur_ga or newest["when"] >= cur_last:
            want["siteGaClientId"] = newest["ga"]
    if p is None:
        return want
    out = {}
    for k, v in want.items():
        have = p.get(k)
        if k == "siteUltimaConversao":
            have = TS.iso(have or "") or None
        if k == "siteEmailComprovado":
            have = bool(have)
        if have != v:
            out[k] = v
    return out


def conversion_body(r: dict, person_id: str) -> dict:
    code, label = ACOES[r["action"]]
    return {"name": f"{label}: {r['material']}" if r["material"] else label, "acao": code, "comprovado": r["verified"],
            "material": r["material"], "arquivo": r["file"], "interesses": list(r["interests"]), "ocorreuEm": r["when"],
            "eventoId": r["id"], "pedidoId": r["request_id"], "gaClientId": r["ga"], "pessoaId": person_id}


def ensure_schema(c: TS.Clients, meta: dict, labels: dict[str, str]) -> None:
    """Cria o que falta: objeto, campos, relação e opções novas de interesse. Só roda com --apply, depois das leituras."""
    fm = "mutation($input: CreateOneFieldMetadataInput!){ createOneField(input:$input){ id name type options } }"

    def create(object_id: str, name: str, spec: dict, opts: list | None = None) -> dict:
        f = {"objectMetadataId": object_id, "name": name, "isNullable": True,
             **{k: v for k, v in spec.items() if k != "options"}}
        if spec["type"] == "SELECT":
            f["options"] = spec["options"]
        if spec["type"] == "MULTI_SELECT":
            f["options"] = opts or []
        data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar campo")
        return data["data"]["createOneField"]

    if not meta["conv_id"]:
        m = "mutation($input: CreateOneObjectInput!){ createOneObject(input:$input){ id } }"
        data = c.twenty("POST", "/metadata", {"query": m, "variables": {"input": {"object": OBJ}}}, op="criar objeto")
        meta["conv_id"] = data["data"]["createOneObject"]["id"]
    new_opts = lambda field: [  # noqa: E731
        {"value": v, "label": labels[v], "color": COLORS[(len(_option_values(field)) + i) % len(COLORS)],
         "position": len(_option_values(field)) + i}
        for i, v in enumerate(sorted(v for v in labels if v not in _option_values(field)))]
    for obj_key, obj_id, spec in (("person", meta["person_id"], PERSON_FIELDS), ("conv", meta["conv_id"], CONV_FIELDS)):
        for name, f in spec.items():
            if name not in meta[obj_key]:
                meta[obj_key][name] = create(obj_id, name, f, new_opts(None) if name in MULTI else None)
    if RELATION["name"] not in meta["conv"]:
        f = {"objectMetadataId": meta["conv_id"], "name": RELATION["name"], "type": "RELATION", "label": RELATION["label"],
             "icon": RELATION["icon"], "isNullable": True,
             "relationCreationPayload": {"targetObjectMetadataId": meta["person_id"], "targetFieldLabel": RELATION["inverse_label"],
                                         "targetFieldIcon": RELATION["inverse_icon"], "type": "MANY_TO_ONE"}}
        data = c.twenty("POST", "/metadata", {"query": fm, "variables": {"input": {"field": f}}}, op="criar relação")
        meta["conv"][RELATION["name"]] = data["data"]["createOneField"]
    um = "mutation($input: UpdateOneFieldMetadataInput!){ updateOneField(input:$input){ id options } }"
    for obj_key, name in (("person", "siteInteresses"), ("conv", "interesses")):
        field = meta[obj_key][name]
        add = new_opts(field)
        if add:  # as opções antigas vão como estão (com id), para o Twenty não recriar nem apagar nenhuma
            options = list(field.get("options") or []) + add
            data = c.twenty("POST", "/metadata", {"query": um, "variables": {"input": {"id": field["id"], "update": {"options": options}}}},
                            op="criar opção")
            field["options"] = data["data"]["updateOneField"]["options"]


def run(c: TS.Clients, *, apply: bool = False, out=None) -> Counter:
    out = out or sys.stdout
    counts: Counter = Counter()
    # 1) Leituras e validações. Nenhuma escrita antes de a fonte e o CRM estarem lidos por inteiro.
    raw = c.d1("SELECT id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id, file "
               "FROM conversions WHERE synced_at IS NULL ORDER BY occurred_at, id")
    rows = []
    for r in raw:
        p = parse_row(r)
        if p is None:
            counts["linha_invalida"] += 1
        else:
            rows.append(p)
    counts["pendentes_lidas"] = len(raw)
    for k in ("linha_invalida", "pedido_sem_pessoa", "email_em_mais_de_uma_pessoa", "pessoas_a_criar", "pessoas_a_atualizar",
              "conversoes_a_criar", "conversoes_ja_no_crm", "linhas_a_marcar"):
        counts[k] += 0  # sempre visíveis na saída
    if not rows:
        _report(counts, out, "nada pendente" if not raw else "só linhas inválidas; nada foi gravado")
        return counts
    meta = read_metadata(c)
    people = TS.list_people(c)
    existing = list_records(c, PLURAL) if meta["conv_id"] else []
    known = {x.get("eventoId") for x in existing if x.get("eventoId")}
    idx = people_by_email(people)

    # 2) Plano. Agrupa por Pessoa (dois emails da mesma Pessoa viram uma gravação só, calculada sobre todas as linhas
    # dela) e, para quem ainda não está no CRM, por email.
    by_email: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        by_email.setdefault(r["email"], []).append(r)
    groups: OrderedDict[tuple, dict] = OrderedDict()
    for email, rs in by_email.items():
        persons = idx.get(email) or []
        if not persons and not any(r["verified"] for r in rs):
            counts["pedido_sem_pessoa"] += len(rs)  # fica pendente: só vira Conversão quando o email estiver no CRM
            continue
        if len(persons) > 1:
            counts["email_em_mais_de_uma_pessoa"] += 1
        key = ("p", persons[0]["id"]) if persons else ("e", email)
        g = groups.setdefault(key, {"email": email, "target": persons[0] if persons else None, "rows": []})
        g["rows"] += rs
    plan = []
    labels: dict[str, str] = {}
    for g in groups.values():
        rs = sorted(g["rows"], key=lambda r: (r["when"], r["id"]))
        target = g["target"]
        patch = person_patch(target, rs)
        new = [r for r in rs if r["id"] not in known]
        counts["pessoas_a_criar" if target is None else "pessoas_a_atualizar"] += 1 if (target is None or patch) else 0
        counts["conversoes_a_criar"] += len(new)
        counts["conversoes_ja_no_crm"] += len(rs) - len(new)
        counts["linhas_a_marcar"] += len(rs)
        for r in rs:
            for i, lbl in r["interests"].items():
                labels[i] = lbl  # rótulo do catálogo mais recente (linhas em ordem de data)
        plan.append((g["email"], target, patch, new, rs))
    missing_obj = 0 if meta["conv_id"] else 1
    counts["objeto_a_criar"] = missing_obj
    counts["campos_a_criar"] = (sum(k not in meta["person"] for k in PERSON_FIELDS) + sum(k not in meta["conv"] for k in CONV_FIELDS)
                                + (RELATION["name"] not in meta["conv"]))
    counts["opcoes_a_criar"] = sum(len([v for v in labels if v not in _option_values(meta[o].get(n))])
                                   for o, n in (("person", "siteInteresses"), ("conv", "interesses")))

    # 3) Escritas, só com --apply: esquema, depois cada email (Pessoa, Conversões, marca no D1).
    if apply and plan:
        ensure_schema(c, meta, labels)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for email, target, patch, new, rs in plan:
            if target is None:
                data = c.twenty("POST", "/rest/people", {"emails": {"primaryEmail": email, "additionalEmails": []}, **patch},
                                op="criar pessoa")
                pid = data["data"]["createPerson"]["id"]
            else:
                pid = target["id"]
                if patch:
                    c.twenty("PATCH", f"/rest/people/{urllib.parse.quote(pid)}", patch, op="atualizar pessoa")
            for r in new:
                c.twenty("POST", f"/rest/{PLURAL}", conversion_body(r, pid), op="criar conversão")
            ids = [r["id"] for r in rs]
            for i in range(0, len(ids), MARK_CHUNK):
                part = ids[i:i + MARK_CHUNK]
                c.d1(f"UPDATE conversions SET synced_at = ? WHERE synced_at IS NULL AND id IN ({','.join('?' * len(part))})",
                     [now, *part])
            counts["linhas_marcadas"] += len(rs)
    _report(counts, out, "gravado" if apply else "simulação: nada foi gravado (use --apply)")
    return counts


def forget(c: TS.Clients, email: str, *, apply: bool = False, out=None) -> Counter:
    """Apaga as conversões de um email: primeiro no D1 (o sync não recria), depois no Twenty. A Pessoa fica."""
    out = out or sys.stdout
    email = email.strip().lower()
    if not _full(EMAIL, email):
        raise SyncError("email inválido na entrada")
    counts: Counter = Counter()
    ids = {r["id"] for r in c.d1("SELECT id FROM conversions WHERE email = ?", [email])}
    meta = read_metadata(c)
    persons = people_by_email(TS.list_people(c)).get(email) or []
    pids = {p["id"] for p in persons}
    convs = []
    if meta["conv_id"]:
        records = list_records(c, PLURAL)
        # Da Pessoa, só as Conversões que não têm mais linha no D1: sobras de uma remoção interrompida depois do DELETE
        # no D1. As de outro email da mesma Pessoa continuam no D1 e ficam.
        linked = [x["eventoId"] for x in records if x.get("pessoaId") in pids and x.get("eventoId") and x["eventoId"] not in ids]
        alive: set[str] = set()
        for i in range(0, len(linked), MARK_CHUNK):
            part = linked[i:i + MARK_CHUNK]
            alive |= {r["id"] for r in c.d1(f"SELECT id FROM conversions WHERE id IN ({','.join('?' * len(part))})", part)}
        convs = [x for x in records if x.get("eventoId") and (x["eventoId"] in ids or (x.get("pessoaId") in pids and x["eventoId"] not in alive))]
    counts["d1_linhas"], counts["conversoes_no_crm"], counts["pessoas_mantidas"] = len(ids), len(convs), len(pids)
    if apply:
        c.d1("DELETE FROM conversions WHERE email = ?", [email])
        for x in convs:
            c.twenty("DELETE", f"/rest/{PLURAL}/{urllib.parse.quote(x['id'])}", op="apagar conversão")
        if c.d1("SELECT count(*) AS n FROM conversions WHERE email = ?", [email])[0]["n"] != 0:
            raise SyncError("o D1 ainda tem linhas do email depois da remoção")
    _report(counts, out, FORGET_MARK if apply else "simulação: nada foi apagado (use --apply)")
    return counts


def _report(counts: Counter, out, tail: str) -> None:
    for k in sorted(counts):
        print(f"{k}: {counts[k]}", file=out)
    print(tail, file=out)


def main(argv=None, transport=TS._transport, sleep=time.sleep, stdin=None) -> int:
    ap = TS._Parser(description="Leva as conversões do site para o Twenty (simulação por padrão).")
    ap.add_argument("--apply", action="store_true", help="grava no Twenty e no D1")
    ap.add_argument("--forget", action="store_true", help="apaga as conversões do email lido da entrada padrão")
    a = ap.parse_args(argv)
    env = {k: os.environ.get(k, "") for k in TS.REQUIRED_ENV}
    if not all(env.values()):
        print("ERRO: faltam variáveis de ambiente: " + ", ".join(k for k, v in env.items() if not v), file=sys.stderr)
        return 2
    try:
        c = TS.Clients(env, transport, sleep)
        if a.forget:
            lines = [x for x in (stdin or sys.stdin).read().splitlines() if x.strip()]
            if len(lines) != 1:
                raise SyncError("--forget espera exatamente um email na entrada padrão")
            forget(c, lines[0], apply=a.apply)
        else:
            run(c, apply=a.apply)
    except SyncError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: falha inesperada ({type(e).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
