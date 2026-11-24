"""Redirects de URLs antigas para posts do guifav.dev, por Bulk Redirects da conta Cloudflare (#102, #106).

Fonte única da lista de Bulk Redirects `guifav_redirects` e da regra da conta que a liga. Ninguém edita essa
lista ou a fase de redirects da conta pelo painel: o `apply` substitui a lista inteira e a fase inteira.

    python3 scripts/site_redirects.py rules
        Imprime os itens da lista e a regra, como vão para a Cloudflare.

    python3 scripts/site_redirects.py apply --yes
        1. Lê a fase http_request_redirect da conta e recusa se houver regra que não seja deste script
           (o PUT da fase a apagaria).
        2. Cria a lista se ela não existir, substitui todos os itens e espera a operação assíncrona terminar.
        3. Lê os itens de volta e compara com os do código. Só então grava a regra que liga a lista.
        Lê CLOUDFLARE_API_TOKEN e CLOUDFLARE_ACCOUNT_ID do ambiente (permissões: Mass URL Redirects Write e
        Account Rule Lists Write).

    python3 scripts/site_redirects.py remove --yes
        Rollback: grava a fase de redirects da conta sem regras. A lista fica, sem efeito.

    python3 scripts/site_redirects.py verify
        Confere no ar cada caminho antigo do domínio antigo (www e apex, com e sem barra): 301 para o endereço que a
        regra do cutover produz em guifav.dev, 301 para o post e o post com 200. Confere também cada origem da lista
        acessada direto.

Por que Bulk Redirects e não uma regra da zona antiga: a zona guilhermefavaron.com.br (plano Free) tem 10 regras de
Single Redirect, 9 já usadas pelo cutover (scripts/cutover_redirects.py), e cada regra tem um destino só. A regra do
cutover leva /post/<antigo> para guifav.dev/blog/<antigo>/; a lista leva esse endereço ao post equivalente. São dois
saltos de 301, e a fase de redirects da zona antiga fica intacta.

Semântica da lista reproduzida em simulate(): a origem casa com host e caminho exatos (sem caixa no host, com caixa
no caminho), em http e https (a origem não tem esquema), sem subdomínios e sem subcaminhos; a query string não entra
na comparação e não é preservada.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as cutover  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
POSTS = REPO / "blog-src/src/content/posts"

HOST = "guifav.dev"
BLOG = f"https://{HOST}/blog/"
LIST_NAME = "guifav_redirects"
LIST_DESCRIPTION = "guifav.dev: URLs antigas para posts (scripts/site_redirects.py, #102 e #106)"
PHASE = "http_request_redirect"
RULE_DESCRIPTION = "site_redirects: lista guifav_redirects"
MAX_ITEMS = 10_000  # URLs em todas as listas da conta, plano Free

# Caminhos /post/<antigo> do site anterior ao Substack que têm post equivalente (#102). A equivalência foi medida
# pelo texto da página arquivada contra os posts (0,90 a 0,98; os demais ficaram abaixo de 0,07), exceto
# modernizacao-sistemas-legados-vantagem-competitiva, sem cópia arquivada: o post microservices-vs-monolith-decisoes
# o cita como o artigo anterior da série, publicado dois dias antes. Caminho com %XX em caixa baixa, como no link.
OLD_POSTS: tuple[tuple[str, str], ...] = (
    ("analise-preditiva-tomada-decisao-modelos-empresariais", "analise-preditiva-tomada-decisao-producao"),
    ("arquitetura-ia-empresarial-definitiva", "a-arquitetura-definitiva-de-ia-para"),
    ("como-a-intelig%c3%aancia-artificial-vai-transformar-a-lideran%c3%a7a-e-os-neg%c3%b3cios-no-futuro",
     "como-a-inteligencia-artificial-vai-transformar-a-lideranca-e-os-negocios-no-futuro"),
    ("construindo-times-ia-lideranca-tecnica-transformacao-empresarial", "construindo-times-de-ia-lideranca"),
    ("criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1",
     "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1-0f6d9e9adf6a"),
    # O site antigo servia o texto de A/B testing neste endereço (similaridade 0,98 com o post de destino).
    ("customer-analytics-metodologias-segmentacao-comportamental", "ab-testing-e-experimentacao-cientifica"),
    ("customer-analytics-segmentacao-personalizacao-dados", "customer-analytics-e-segmentacao"),
    ("data-pipelines-empresariais-silos-dados-business-intelligence", "data-pipelines-empresariais-como"),
    ("desmistificando-gpus-em-ia", "desmistificando-gpus-em-ia-79e168b03ccd"),
    ("fine-tuning-vs-prompt-engineering-decisoes-roi", "fine-tuning-vs-prompt-engineering"),
    ("ia-generativa-producao-custos-performance-escalabilidade", "ia-generativa-em-producao-custos"),
    ("langgraph-workflows-complexos-ia-orquestracao", "langgraph-orquestrando-workflows"),
    ("modernizacao-sistemas-legados-vantagem-competitiva", "modernizacao-de-sistemas-legados"),
    ("o-futuro-das-organizacoes-alem-da-tecnologia-e-processos", "o-futuro-das-organizacoes-alem-da"),
    ("o-surgimento-da-ia-geral-super-humana",
     "o-surgimento-da-ia-geral-super-humana-nao-sera-um-evento-o-avanco-sera-progressivo-dbf420240ecc"),
    ("streaming-data-em-escala-arquiteturas-para-analytics-sem-latencia-analitica",
     "streaming-data-em-escala-arquiteturas"),
)

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_OLD = re.compile(r"[a-z0-9%]+(?:-[a-z0-9%]+)*")


@dataclass(frozen=True)
class Redirect:
    source: str  # host + caminho, sem esquema (casa http e https)
    target: str  # URL absoluta do post


def post_url(slug: str) -> str:
    return f"{BLOG}{slug}/"


def first_hop(old: str) -> str:
    """Onde a regra do cutover leva /post/<old> hoje (a mesma Location nos dois hosts, com e sem barra)."""
    loc = cutover.simulate("www." + cutover.ZONE, f"/post/{old}")
    if loc is None or not loc.startswith(BLOG) or not loc.endswith("/"):
        raise SystemExit(f"/post/{old}: o cutover não leva a {BLOG}<slug>/ ({loc})")
    return loc


def redirects() -> list[Redirect]:
    out = []
    for old, new in OLD_POSTS:
        src = first_hop(old).removeprefix("https://")
        for s in (src, src.rstrip("/")):  # com e sem barra: a lista casa o caminho exato
            out.append(Redirect(s, post_url(new)))
    return out


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    return m.group(1) if m else ""


def indexable_posts(posts_dir: Path = POSTS) -> set[str]:
    """Slugs com página indexável: o arquivo existe e o frontmatter não diz index: false."""
    out = set()
    for f in posts_dir.glob("*.md"):
        if not re.search(r"^index:\s*false\s*$", _frontmatter(f), re.M):
            out.add(f.stem)
    return out


def all_posts(posts_dir: Path = POSTS) -> set[str]:
    return {f.stem for f in posts_dir.glob("*.md")}


def problems(reds: list[Redirect] | None = None, posts_dir: Path = POSTS) -> list[str]:
    """O que impede gravar a lista. Vazio = pode gravar."""
    reds = redirects() if reds is None else reds
    live, every = indexable_posts(posts_dir), all_posts(posts_dir)
    out = []
    olds = [o for o, _ in OLD_POSTS]
    if len(set(olds)) != len(olds):
        out.append("caminho antigo repetido em OLD_POSTS")
    for old, new in OLD_POSTS:
        if not _OLD.fullmatch(old) or old != old.lower():
            out.append(f"caminho antigo fora do formato: {old!r}")
        if old in every:
            out.append(f"{old}: já é um post; o cutover leva direto a ele")
        if not _SLUG.fullmatch(new) or new not in live:
            out.append(f"{old}: destino {new!r} não é post indexável")
    sources = [r.source for r in reds]
    if len(set(sources)) != len(sources):
        out.append("origem repetida na lista")
    targets = {r.target for r in reds}
    for r in reds:
        path = "/" + r.source.split("/", 1)[1]
        if not r.source.startswith(HOST + "/blog/"):
            out.append(f"origem fora de {HOST}/blog/: {r.source}")
        slug = path.removeprefix("/blog/").rstrip("/")
        if slug in live:
            out.append(f"origem {r.source} é um post indexável: o redirect esconderia a página")
        if "https://" + r.source in targets or "https://" + r.source + "/" in targets:
            out.append(f"origem {r.source} também é destino: cadeia de redirects")
        if not r.target.startswith(BLOG) or not r.target.endswith("/"):
            out.append(f"destino fora de {BLOG}<slug>/: {r.target}")
    if len(reds) > MAX_ITEMS:
        out.append(f"{len(reds)} itens: o plano Free aceita {MAX_ITEMS}")
    return out


def list_items(reds: list[Redirect] | None = None) -> list[dict]:
    reds = redirects() if reds is None else reds
    return [{"redirect": {"source_url": r.source, "target_url": r.target, "status_code": 301,
                          "preserve_query_string": False, "include_subdomains": False,
                          "subpath_matching": False}} for r in reds]


def rule() -> dict:
    return {"description": RULE_DESCRIPTION, "expression": f"http.request.full_uri in ${LIST_NAME}",
            "action": "redirect",
            "action_parameters": {"from_list": {"name": LIST_NAME, "key": "http.request.full_uri"}},
            "enabled": True}


def simulate(url: str, reds: list[Redirect] | None = None) -> str | None:
    """Destino que a lista dá para a URL (http ou https), ou None."""
    reds = redirects() if reds is None else reds
    p = urlsplit(url)
    key = (p.hostname or "").lower() + (p.path or "/")
    for r in reds:
        if r.source == key:
            return r.target
    return None


# ---------------------------------------------------------------- Cloudflare

API = "https://api.cloudflare.com/client/v4"
_ITEM_KEYS = ("source_url", "target_url", "status_code", "preserve_query_string", "include_subdomains",
              "subpath_matching")
_RULE_KEYS = ("description", "expression", "action", "action_parameters", "enabled")


def _cf(method: str, path: str, token: str, body=None, opener=urllib.request.urlopen,
        missing_ok: bool = False) -> dict:
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
    if missing_ok and any(e.get("code") == 10003 for e in data.get("errors") or []):
        return {"success": True, "result": None}  # a fase ainda não tem ruleset
    if not data.get("success"):
        raise SystemExit(f"Cloudflare {method} {path}: {data.get('errors')}")
    return data


def _phase_rules(account: str, token: str, opener) -> list[dict]:
    entry = f"/accounts/{account}/rulesets/phases/{PHASE}/entrypoint"
    res = _cf("GET", entry, token, opener=opener, missing_ok=True)["result"] or {}
    rules = res.get("rules") or []
    foreign = [r.get("description") or r.get("id") for r in rules if r.get("description") != RULE_DESCRIPTION]
    if foreign:
        raise SystemExit(f"a fase {PHASE} da conta tem regras que não são deste script: {foreign}. "
                         "O PUT as apagaria.")
    return rules


def _items(account: str, list_id: str, token: str, opener) -> list[dict]:
    out, cursor = [], None
    while True:
        q = f"?per_page=500" + (f"&cursor={cursor}" if cursor else "")
        data = _cf("GET", f"/accounts/{account}/rules/lists/{list_id}/items{q}", token, opener=opener)
        out += data.get("result") or []
        cursor = ((data.get("result_info") or {}).get("cursors") or {}).get("after")
        if not cursor:
            return out


def _wait(account: str, op: str, token: str, opener, sleep, timeout: float = 120, every: float = 2) -> None:
    waited = 0.0
    while True:
        st = _cf("GET", f"/accounts/{account}/rules/lists/bulk_operations/{op}", token, opener=opener)["result"]
        if st.get("status") == "completed":
            return
        if st.get("status") == "failed":
            raise SystemExit(f"operação {op} da lista falhou: {st.get('error')}")
        if waited >= timeout:
            raise SystemExit(f"operação {op} da lista não terminou em {timeout:.0f}s")
        sleep(every)
        waited += every


def _norm_item(it: dict) -> dict:
    r = it.get("redirect") or {}
    defaults = {"status_code": 301, "preserve_query_string": False, "include_subdomains": False,
                "subpath_matching": False}
    return {k: r.get(k, defaults.get(k)) for k in _ITEM_KEYS}


def apply(token: str, account: str, opener=urllib.request.urlopen, sleep=time.sleep) -> int:
    bad = problems()
    if bad:
        raise SystemExit("lista recusada:\n  " + "\n  ".join(bad))
    _phase_rules(account, token, opener)  # recusa antes de qualquer escrita
    lists = _cf("GET", f"/accounts/{account}/rules/lists", token, opener=opener)["result"] or []
    same = [x for x in lists if x.get("name") == LIST_NAME]
    if same and same[0].get("kind") != "redirect":
        raise SystemExit(f"já existe uma lista {LIST_NAME} de tipo {same[0].get('kind')!r}")
    if same:
        list_id = same[0]["id"]
    else:
        list_id = _cf("POST", f"/accounts/{account}/rules/lists", token,
                      {"name": LIST_NAME, "description": LIST_DESCRIPTION, "kind": "redirect"},
                      opener=opener)["result"]["id"]
    sent = list_items()
    op = _cf("PUT", f"/accounts/{account}/rules/lists/{list_id}/items", token, sent, opener=opener)["result"]
    _wait(account, op["operation_id"], token, opener, sleep)
    got = sorted((_norm_item(i) for i in _items(account, list_id, token, opener)), key=lambda d: d["source_url"])
    want = sorted((_norm_item(i) for i in sent), key=lambda d: d["source_url"])
    if got != want:
        raise SystemExit(f"a lista lida de volta não confere com a enviada ({len(got)} de {len(want)} itens); "
                         "a regra não foi gravada: rodar apply de novo")
    entry = f"/accounts/{account}/rulesets/phases/{PHASE}/entrypoint"
    res = _cf("PUT", entry, token, {"rules": [rule()]}, opener=opener)["result"]
    back = [{k: r.get(k) for k in _RULE_KEYS} for r in res.get("rules") or []]
    if back != [rule()]:
        raise SystemExit("a regra gravada não confere com a enviada: rodar apply de novo")
    print(f"lista {LIST_NAME}: {len(got)} itens; regra ligada na fase {PHASE} da conta (ruleset {res.get('id')})")
    return 0


def remove(token: str, account: str, opener=urllib.request.urlopen) -> int:
    rules = _phase_rules(account, token, opener)
    if not rules:
        print(f"a fase {PHASE} da conta já está sem regras deste script: nada a remover")
        return 0
    entry = f"/accounts/{account}/rulesets/phases/{PHASE}/entrypoint"
    res = _cf("PUT", entry, token, {"rules": []}, opener=opener)["result"]
    if res.get("rules"):
        raise SystemExit("a fase ainda tem regras depois do PUT: conferir no painel")
    print(f"regra removida da fase {PHASE} da conta; a lista {LIST_NAME} ficou, sem efeito")
    return 0


# ---------------------------------------------------------------- verificação no ar

def verify(fetcher=cutover.fetch, pause: float = 0.12, out=sys.stdout) -> int:
    fails: list[str] = []
    ok = 0
    reds = redirects()

    def hop(url: str, want_status: int, want_loc: str | None) -> bool:
        status, loc = fetcher(url, "GET")
        time.sleep(pause)
        if status != want_status or loc != want_loc:
            fails.append(f"{url}: esperado {want_status} -> {want_loc}, veio {status} -> {loc}")
            return False
        return True

    checks: list[tuple[str, int, str | None]] = []
    for old, new in OLD_POSTS:
        mid, final = first_hop(old), post_url(new)
        checks += [(f"https://{host}/post/{old}{suffix}", 301, mid) for host in cutover.HOSTS for suffix in ("", "/")]
        checks.append((final, 200, None))
    checks += [("https://" + r.source, 301, r.target) for r in reds]  # inclui o segundo salto de cada caminho
    for url, status, loc in checks:
        ok += hop(url, status, loc)
    print(f"verificadas: {ok + len(fails)}; ok: {ok}; falhas: {len(fails)}", file=out)
    for f in fails[:50]:
        print("FALHA " + f, file=out)
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    for name in ("apply", "remove"):
        p = sub.add_parser(name)
        p.add_argument("--yes", action="store_true", help="confirma a gravação na conta")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    if args.cmd == "rules":
        bad = problems()
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        print(json.dumps({"list": LIST_NAME, "items": list_items(), "rule": rule()}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd in ("apply", "remove"):
        if not args.yes:
            raise SystemExit(f"{args.cmd} grava na conta: repita com --yes")
        token, account = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        if not token or not account:
            raise SystemExit("defina CLOUDFLARE_API_TOKEN e CLOUDFLARE_ACCOUNT_ID")
        return apply(token, account) if args.cmd == "apply" else remove(token, account)
    return verify()


if __name__ == "__main__":
    sys.exit(main())
