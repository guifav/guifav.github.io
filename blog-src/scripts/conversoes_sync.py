"""Leva as conversões do site (tabela `conversions` do D1 `blog-admin`) para o Twenty (crm.guifav.dev). Issue #97, fase 2.

Fonte: as linhas de `conversions` com `synced_at` nulo, gravadas pelo Worker `blog-newsletter` (fase 1, PRs #98 e #100).
Simulação por padrão: lê o D1 e o Twenty e mostra só contagens. Grava apenas com --apply.

No Twenty, criados se faltarem (metadata API):
- Objeto `siteConversion` ("Conversão do site"): acao (SELECT), comprovado (BOOLEAN), material e arquivo (TEXT), interesses
  (MULTI_SELECT), ocorreuEm (DATE_TIME), eventoId (TEXT único, o `id` do D1), pedidoId e gaClientId (TEXT), a origem da
  visita (issue #114: origemCanal e origemFonte, SELECT; utmSource, utmMedium, utmCampaign e paginaEntrada, TEXT) e a
  relação N:1 `pessoa` com Person.
- Na Person: siteInteresses (MULTI_SELECT, união dos interesses das conversões), siteUltimaConversao (DATE_TIME),
  siteEmailComprovado (BOOLEAN: alguma conversão com o email comprovado por link) e siteGaClientId (TEXT, o client_id do
  GA4 da conversão mais recente que trouxe um).

Regras:
- Correspondência por email, no principal e nos adicionais, sem diferença de caixa (a mesma do twenty_sync.py). Mais de
  uma Pessoa com o email: a conversão vai para a mais antiga (createdAt, depois id), e a contagem mostra o caso.
- Datas com milissegundos do começo ao fim (o Worker grava `toISOString()`): ordenar, comparar e gravar no segundo cheio
  trocaria a Pessoa mais antiga e o client_id mais recente quando dois eventos caem no mesmo segundo.
- Pessoa nova só nasce de uma ação com o email comprovado (download ou confirmação da newsletter), só com o email.
- Pedido sem clique (`material_request`, `verified = 0`) só vira Conversão se o email já estiver numa Pessoa (decisão do
  Gui na #97, 2026-11-23). Sem Pessoa, a linha fica pendente no D1: se o email chegar ao CRM depois (pelo download, pela
  newsletter ou à mão), o pedido vai junto na rodada seguinte.
- Idempotente: a Conversão é identificada pelo eventoId (campo único no Twenty); a que já existe não é criada de novo, e
  a linha é marcada com `synced_at` depois que a Pessoa e as Conversões do email foram gravadas.
- siteInteresses só ganha valores, nunca perde: uma tag tirada à mão volta se a pessoa tiver uma conversão nova com o tema.
  Um interesse novo no catálogo vira opção nova das duas multi-seleções, com o rótulo do catálogo.
- Linha malformada (ação, email, datas, interesses ou ids fora do formato que o Worker grava; o email com a mesma regra
  do `normalizeEmail` do Worker) não é gravada nem marcada, e é contada em `linha_invalida` (o wrapper alerta).
- Lê e valida tudo (D1, metadados, Pessoas e Conversões) antes de qualquer escrita. A saída tem só contagens, nunca emails.
  Um campo SELECT que já existe no Twenty sem todas as opções que o sync grava é recusado antes de qualquer escrita.
- Origem (issue #114): vai na Conversão, nunca na Pessoa, com as listas e os padrões do Worker
  (workers/newsletter/src/origem.ts) e do CHECK da migração 0006. Linha anterior à #114 tem a origem toda nula e vira
  Conversão com os campos de origem vazios. Origem fora do formato (canal ou fonte fora da lista, UTM ou página de
  entrada que não casam, fonte sem canal) é linha inválida. O --forget apaga a Conversão inteira, e a origem sai junto.
- Lixeira do Twenty: a listagem padrão da REST esconde registros apagados pela interface, mas os campos únicos (eventoId
  e o email principal da Pessoa) continuam valendo para eles. Por isso Pessoas e Conversões são lidas com a lixeira.
  Conversão na lixeira conta como já existente (não é recriada; a linha é marcada). Email cuja única Pessoa está na
  lixeira não ganha Pessoa nova nem Conversão: a linha fica pendente e conta em `pessoa_na_lixeira` (o wrapper alerta),
  porque restaurar ou apagar de vez essa Pessoa é decisão humana.

Remoção (direito do titular), --forget: lê UM email da entrada padrão (fora do histórico do shell e da lista de
processos). As linhas do email no D1 são o diário da remoção: apaga no Twenty (de vez, inclusive as que estão na
lixeira) as Conversões cujos eventoId são os ids dessas linhas, relê o Twenty com a lixeira para provar que nenhuma
sobrou e só então apaga as linhas no D1. Uma queda no meio deixa o
D1 intacto, e repetir o comando conclui, mesmo que o email da Pessoa tenha mudado no CRM. A Pessoa e os campos site*
dela ficam como estão (decisão do Gui, 2026-11-23). Simulação sem --apply.

Sync e --forget nunca rodam juntos: os dois pegam o mesmo lock (arquivo em CONVERSOES_SYNC_LOCK, padrão
/tmp/guifav-conversoes-sync.lock). Sem ele, o sync poderia criar no Twenty uma Conversão de uma linha pendente entre a
limpeza do Twenty e a do D1, e essa Conversão ficaria sem linha no D1 que a ligasse ao email.

Rodar na KVM (todas as credenciais vêm do ambiente):
    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... D1_DATABASE_ID=... TWENTY_API_KEY=... \\
    TWENTY_LOCAL_URL=http://127.0.0.1:3100 python3 scripts/conversoes_sync.py [--apply]
    printf '%s\\n' "$EMAIL" | ... python3 scripts/conversoes_sync.py --forget [--apply]
"""
from __future__ import annotations

import argparse
import fcntl
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
# Origem da visita (issue #114): valor no D1 -> (opção no Twenty, rótulo). As mesmas listas do Worker e da migração 0006.
CANAIS = OrderedDict([
    ("busca", ("BUSCA", "Busca")), ("assistente", ("ASSISTENTE", "Assistente de IA")), ("social", ("SOCIAL", "Rede social")),
    ("email", ("EMAIL", "Email")), ("pago", ("PAGO", "Pago")), ("outro_site", ("OUTRO_SITE", "Outro site")),
    ("direto", ("DIRETO", "Direto")), ("desconhecida", ("DESCONHECIDA", "Desconhecida")),
])
FONTES = OrderedDict([
    ("google", ("GOOGLE", "Google")), ("bing", ("BING", "Bing")), ("chatgpt", ("CHATGPT", "ChatGPT")),
    ("perplexity", ("PERPLEXITY", "Perplexity")), ("claude", ("CLAUDE", "Claude")), ("copilot", ("COPILOT", "Copilot")),
    ("gemini", ("GEMINI", "Gemini")), ("linkedin", ("LINKEDIN", "LinkedIn")), ("youtube", ("YOUTUBE", "YouTube")),
    ("github", ("GITHUB", "GitHub")), ("outro", ("OUTRO", "Outro")),
])
SEM_FONTE = {"direto", "desconhecida"}
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
    ("origemCanal", {"type": "SELECT", "label": "Origem: canal", "icon": "IconRoute", "options": [
        {"value": v, "label": lbl, "color": COLORS[i % 10], "position": i} for i, (v, lbl) in enumerate(CANAIS.values())]}),
    ("origemFonte", {"type": "SELECT", "label": "Origem: fonte", "icon": "IconWorld", "options": [
        {"value": v, "label": lbl, "color": COLORS[i % 10], "position": i} for i, (v, lbl) in enumerate(FONTES.values())]}),
    ("utmSource", {"type": "TEXT", "label": "utm_source", "icon": "IconTag"}),
    ("utmMedium", {"type": "TEXT", "label": "utm_medium", "icon": "IconTag"}),
    ("utmCampaign", {"type": "TEXT", "label": "utm_campaign", "icon": "IconTag"}),
    ("paginaEntrada", {"type": "TEXT", "label": "Página de entrada", "icon": "IconDoorEnter"}),
])
ORIGEM_COLS = ("origin_channel", "origin_source", "utm_source", "utm_medium", "utm_campaign", "landing_path")
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
# A mesma regra do normalizeEmail de workers/newsletter/src/index.ts (minúsculas, local até 64, rótulos de domínio sem
# hífen nas pontas, total até 254). Classes só ASCII: o \d e o \w do Python aceitam dígitos e letras Unicode.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
EMAIL = re.compile(r"[a-z0-9.!#$%&*+/=?^_`{|}~-]{1,64}@" + _LABEL + r"(?:\." + _LABEL + r")+")
MAX_EMAIL = 254
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
INTEREST = re.compile(r"[A-Z][A-Z0-9_]{0,62}")
GA_CID = re.compile(r"[0-9]{1,20}\.[0-9]{1,20}")  # o parseGaClientId de workers/newsletter/src/ga4.ts
UTM = re.compile(r"[a-z0-9._-]{1,64}")  # o UTM de workers/newsletter/src/origem.ts
ENTRADA = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]*")  # o ENTRADA de origem.ts: segmentos não vazios
MAX_ENTRADA = 200
MAX_LABEL = 120
MARK_CHUNK = 50  # ids por UPDATE no D1 (limite de 100 parâmetros por consulta)
FORGET_MARK = "conversões apagadas"
# Filtro da REST do Twenty que devolve ativos e lixeira juntos (conferido ao vivo na 2.32 com uma sonda descartável).
WITH_TRASH = "or(deletedAt[is]:NULL,deletedAt[is]:NOT_NULL)"
LOCK_DEFAULT = "/tmp/guifav-conversoes-sync.lock"


def _full(rx: re.Pattern, v) -> bool:
    return isinstance(v, str) and rx.fullmatch(v) is not None


def valid_email(v) -> bool:
    return _full(EMAIL, v) and len(v) <= MAX_EMAIL


def iso_ms(value: str) -> str:
    """Data em ISO UTC com milissegundos ('2026-11-24T10:00:00.900Z'); '' se inválida. Mesmas entradas do TS.iso."""
    v = (value or "").strip().replace(" ", "T", 1)
    if not v:
        return ""
    v = re.sub(r"([+-]\d\d)$", r"\1:00", v.replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(timezone.utc)  # pode estourar o ano 1 ou 9999 ao trocar de fuso (OverflowError)
    except (ValueError, OverflowError):
        return ""
    return stamp(d)


def stamp(d: datetime) -> str:
    # Campos explícitos: o %Y do strftime no Linux não completa anos abaixo de 1000 com zeros ("999-..."), o que quebra
    # o formato ISO e a comparação como texto.
    return (f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}."
            f"{d.microsecond // 1000:03d}Z")


def _now() -> datetime:  # relógio do synced_at (substituível nos testes)
    return datetime.now(timezone.utc)


def parse_origem(r: dict) -> dict | None:
    """Origem da linha (issue #114) validada: dict com os seis campos (todos None numa linha anterior à #114) ou None se
    algo estiver fora do formato que o Worker grava e o CHECK do D1 aceita."""
    o = {k: r.get(k) for k in ORIGEM_COLS}
    if o["origin_channel"] is None:
        return o if all(v is None for v in o.values()) else None
    if o["origin_channel"] not in CANAIS:
        return None
    src = o["origin_source"]
    if src is not None and (src not in FONTES or o["origin_channel"] in SEM_FONTE):
        return None
    for k in ("utm_source", "utm_medium", "utm_campaign"):
        if o[k] is not None and not _full(UTM, o[k]):
            return None
    path = o["landing_path"]
    if path is not None and (not _full(ENTRADA, path) or len(path) > MAX_ENTRADA):
        return None
    return o


def parse_row(r: dict) -> dict | None:
    """Linha do D1 validada e normalizada, ou None se algo estiver fora do formato que o Worker grava."""
    try:
        action, verified = r.get("action"), r.get("verified")
        if action not in ACOES or verified not in (0, 1) or isinstance(verified, bool):
            return None
        if (action == "material_request") != (verified == 0):
            return None  # só o pedido é não comprovado
        if not _full(HEX64, r.get("id")) or not valid_email(r.get("email")):
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
        when = iso_ms(r.get("occurred_at") or "")
        if not when:
            return None
        origem = parse_origem(r)
        if origem is None:
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
                "interests": interests, "origem": origem}
    except (ValueError, TypeError, AttributeError, OverflowError):
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
            # SELECT que já existe precisa ter todas as opções que o sync grava: senão o Twenty recusaria a Conversão no
            # meio da rodada, depois de outras escritas.
            if k in have and f["type"] == "SELECT":
                missing = {o["value"] for o in f["options"]} - set(_option_values(have[k]))
                if missing:
                    raise SyncError(f"campo {k} existe sem as opções {', '.join(sorted(missing))}")
    if RELATION["name"] in meta["conv"] and meta["conv"][RELATION["name"]]["type"] != "RELATION":
        raise SyncError("campo pessoa existe com outro tipo")
    return meta


def list_records(c: TS.Clients, plural: str) -> list[dict]:
    """Listagem REST completa, com a lixeira (mesmo contrato de paginação do twenty_sync.list_people)."""
    out, cursor = [], ""
    for _ in range(TS.MAX_PAGES):
        q = (f"?limit={TS.PAGE}&filter={urllib.parse.quote(WITH_TRASH)}"
             + (f"&starting_after={urllib.parse.quote(cursor)}" if cursor else ""))
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
    """Pessoas ativas por email, da mais antiga para a mais nova. As da lixeira ficam de fora (veja trashed_emails)."""
    idx: dict[str, list[dict]] = {}
    for p in sorted(people, key=lambda p: (iso_ms(p.get("createdAt") or "") or "9999", p["id"])):
        if p.get("deletedAt"):
            continue
        for e in TS._emails(p):
            idx.setdefault(e, []).append(p)
    return idx


def trashed_emails(people: list[dict]) -> set[str]:
    return {e for p in people if p.get("deletedAt") for e in TS._emails(p)}


def _option_values(field: dict | None) -> list[str]:
    return [o["value"] for o in (field or {}).get("options") or []]


def person_patch(p: dict | None, rows: list[dict]) -> dict:
    """Campos site* desejados para a Pessoa (p None: Pessoa nova), só os que mudam."""
    cur_int = list((p or {}).get("siteInteresses") or [])
    merged = cur_int + [i for r in rows for i in r["interests"] if i not in cur_int]
    merged = list(OrderedDict.fromkeys(merged))
    cur_last = iso_ms((p or {}).get("siteUltimaConversao") or "")
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
            have = iso_ms(have or "") or None
        if k == "siteEmailComprovado":
            have = bool(have)
        if have != v:
            out[k] = v
    return out


def conversion_body(r: dict, person_id: str) -> dict:
    code, label = ACOES[r["action"]]
    o = r["origem"]
    return {"name": f"{label}: {r['material']}" if r["material"] else label, "acao": code, "comprovado": r["verified"],
            "material": r["material"], "arquivo": r["file"], "interesses": list(r["interests"]), "ocorreuEm": r["when"],
            "eventoId": r["id"], "pedidoId": r["request_id"], "gaClientId": r["ga"],
            "origemCanal": CANAIS[o["origin_channel"]][0] if o["origin_channel"] else None,
            "origemFonte": FONTES[o["origin_source"]][0] if o["origin_source"] else None,
            "utmSource": o["utm_source"], "utmMedium": o["utm_medium"], "utmCampaign": o["utm_campaign"],
            "paginaEntrada": o["landing_path"], "pessoaId": person_id}


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
    raw = c.d1("SELECT id, email, action, material, interests, occurred_at, verified, ga_client_id, request_id, file, "
               + ", ".join(ORIGEM_COLS) + " FROM conversions WHERE synced_at IS NULL ORDER BY occurred_at, id")
    rows = []
    for r in raw:
        p = parse_row(r)
        if p is None:
            counts["linha_invalida"] += 1
        else:
            rows.append(p)
    counts["pendentes_lidas"] = len(raw)
    for k in ("linha_invalida", "pedido_sem_pessoa", "pessoa_na_lixeira", "email_em_mais_de_uma_pessoa", "pessoas_a_criar",
              "pessoas_a_atualizar", "conversoes_a_criar", "conversoes_ja_no_crm", "conversoes_na_lixeira", "linhas_a_marcar"):
        counts[k] += 0  # sempre visíveis na saída
    if not rows:
        _report(counts, out, "nada pendente" if not raw else "só linhas inválidas; nada foi gravado")
        return counts
    meta = read_metadata(c)
    people = list_records(c, "people")
    existing = list_records(c, PLURAL) if meta["conv_id"] else []
    known = {x.get("eventoId") for x in existing if x.get("eventoId")}
    trashed_conv = {x.get("eventoId") for x in existing if x.get("eventoId") and x.get("deletedAt")}
    idx = people_by_email(people)
    trashed = trashed_emails(people)

    # 2) Plano. Agrupa por Pessoa (dois emails da mesma Pessoa viram uma gravação só, calculada sobre todas as linhas
    # dela) e, para quem ainda não está no CRM, por email.
    by_email: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        by_email.setdefault(r["email"], []).append(r)
    groups: OrderedDict[tuple, dict] = OrderedDict()
    for email, rs in by_email.items():
        persons = idx.get(email) or []
        if not persons and email in trashed:
            counts["pessoa_na_lixeira"] += len(rs)  # fica pendente: restaurar ou apagar de vez é decisão humana
            continue
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
        counts["conversoes_na_lixeira"] += sum(r["id"] in trashed_conv for r in rs)
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
        now = stamp(_now())
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
    """Apaga as conversões de um email: as do Twenty pelos ids do D1, depois as linhas do D1. A Pessoa fica."""
    out = out or sys.stdout
    email = email.strip().lower()
    if not valid_email(email):
        raise SyncError("email inválido na entrada")
    counts: Counter = Counter()
    # As linhas do D1 são o diário: enquanto existirem, os ids delas acham as Conversões no Twenty, qualquer que seja o
    # email atual da Pessoa no CRM.
    ids = {r["id"] for r in c.d1("SELECT id FROM conversions WHERE email = ?", [email])}
    meta = read_metadata(c)
    persons = people_by_email(list_records(c, "people")).get(email) or []
    convs = [x for x in list_records(c, PLURAL) if x.get("eventoId") in ids] if meta["conv_id"] else []
    counts["d1_linhas"], counts["conversoes_no_crm"], counts["pessoas_mantidas"] = len(ids), len(convs), len(persons)
    if apply:
        for x in convs:
            c.twenty("DELETE", f"/rest/{PLURAL}/{urllib.parse.quote(x['id'])}", op="apagar conversão")
        if meta["conv_id"] and any(x.get("eventoId") in ids for x in list_records(c, PLURAL)):
            raise SyncError("ainda há Conversões do email no Twenty; o D1 ficou intacto, rode de novo")
        c.d1("DELETE FROM conversions WHERE email = ?", [email])
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
    lock = None
    try:
        lock = open(os.environ.get("CONVERSOES_SYNC_LOCK") or LOCK_DEFAULT, "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("outra execução do sync ou do --forget em andamento") from None
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
    finally:
        if lock is not None:
            lock.close()  # fechar o arquivo solta o lock
    return 0


if __name__ == "__main__":
    sys.exit(main())
