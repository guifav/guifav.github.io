"""Redirects de URLs antigas para posts, ferramentas movidas e mudanças de endereço do guifav.dev, por Bulk Redirects da conta Cloudflare (#102, #106, #146, #151).

Fonte única do par de listas de Bulk Redirects `guifav_redirects_a` e `guifav_redirects_b` e da regra da conta que
liga uma delas. Ninguém edita essas listas ou a fase de redirects da conta pelo painel: o `apply` substitui a lista
inativa inteira e a fase inteira.

    python3 scripts/site_redirects.py rules
        Imprime os itens da lista e a regra, como vão para a Cloudflare.

    python3 scripts/site_redirects.py apply --yes
        1. Lê a fase http_request_redirect da conta e recusa se houver regra que não seja deste script
           (o PUT da fase a apagaria). A regra deste script diz qual lista do par está ativa.
        2. Escreve só na outra lista do par (criada se não existir): substitui todos os itens e espera a operação
           assíncrona terminar. A lista ativa nunca é tocada.
        3. Lê os itens de volta e compara com os do código. Só então grava a regra apontando para a lista nova.
        Qualquer falha antes do passo 3 deixa a regra como estava, sobre a lista ativa e intacta: a configuração no
        ar continua a última conferida.
        Lê CLOUDFLARE_API_TOKEN e CLOUDFLARE_ACCOUNT_ID do ambiente (permissões: Mass URL Redirects Write e
        Account Rule Lists Write).

    python3 scripts/site_redirects.py remove --yes
        Rollback: grava a fase de redirects da conta sem regras. As listas ficam, sem efeito.

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
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover_redirects as cutover  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
POSTS = REPO / "blog-src/src/content/posts"

HOST = "guifav.dev"
BLOG = f"https://{HOST}/blog/"
TOOLS = f"https://{HOST}/tools/"
# Par azul/verde: o apply escreve e confere a lista inativa e só então troca a regra para ela.
LISTS = ("guifav_redirects_a", "guifav_redirects_b")
# Marca das listas do par: o apply só adota (e sobrescreve) lista com exatamente esta descrição.
LIST_DESCRIPTION = "guifav.dev: URLs antigas para posts; escrita só por scripts/site_redirects.py"
PHASE = "http_request_redirect"
RULE_DESCRIPTION = "site_redirects: lista guifav_redirects"
MAX_ITEMS = 10_000  # URLs somadas em todas as listas da conta, plano Free (o par guarda a lista duas vezes)

# Grupo A da #120: caminhos /post/<antigo> do domínio antigo que têm post equivalente com outro slug. A tabela final,
# com as divergências da proposta, está registrada na #120. Evidência de cada par:
# - texto da página arquivada no Internet Archive contra os posts (0,90 a 0,98; os demais ficaram abaixo de 0,07);
# - modernizacao-sistemas-legados-vantagem-competitiva, sem cópia arquivada: o post microservices-vs-monolith-decisoes
#   o cita como o artigo anterior da série, publicado dois dias antes;
# - microservices-vs-monolith-decisoes-arquiteturais-competitividade (21/11/2025, depois da última captura): o slug é
#   o título do post, palavra por palavra;
# - ia-brasileira-sabia-2-amazonia-...: o post ia-brasileira-sabia-3-... tem o mesmo slug com 3 no lugar de 2, e o alt
#   da imagem de capa guarda o título antigo ("IA Brasileira - Sabia 2, Amazonia IA, Maritaca AI - ...").
# Fica fora, por decisão do Gui (grupo C da #120): o caminho de liderança e negócios no futuro, que segue em 404.
OLD_POSTS: tuple[tuple[str, str], ...] = (
    ("analise-preditiva-tomada-decisao-modelos-empresariais", "analise-preditiva-tomada-decisao-producao"),
    ("arquitetura-ia-empresarial-definitiva", "a-arquitetura-definitiva-de-ia-para"),
    ("construindo-times-ia-lideranca-tecnica-transformacao-empresarial", "construindo-times-de-ia-lideranca"),
    ("criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1",
     "criancas-conectadas-pesquisa-sobre-como-as-familias-equilibram-tecnologia-e-bem-estar-parte-1-0f6d9e9adf6a"),
    # O site antigo servia o texto de A/B testing neste endereço (similaridade 0,98 com o post de destino).
    ("customer-analytics-metodologias-segmentacao-comportamental", "ab-testing-e-experimentacao-cientifica"),
    ("customer-analytics-segmentacao-personalizacao-dados", "customer-analytics-e-segmentacao"),
    ("data-pipelines-empresariais-silos-dados-business-intelligence", "data-pipelines-empresariais-como"),
    ("desmistificando-gpus-em-ia", "desmistificando-gpus-em-ia-79e168b03ccd"),
    ("fine-tuning-vs-prompt-engineering-decisoes-roi", "fine-tuning-vs-prompt-engineering"),
    ("ia-brasileira-sabia-2-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional",
     "ia-brasileira-sabia-3-amazonia-ia-maritaca-ai-inteligencia-artificial-nacional"),
    ("ia-generativa-producao-custos-performance-escalabilidade", "ia-generativa-em-producao-custos"),
    ("langgraph-workflows-complexos-ia-orquestracao", "langgraph-orquestrando-workflows"),
    ("microservices-vs-monolith-decisoes-arquiteturais-competitividade", "microservices-vs-monolith-decisoes"),
    ("modernizacao-sistemas-legados-vantagem-competitiva", "modernizacao-de-sistemas-legados"),
    ("o-futuro-das-organizacoes-alem-da-tecnologia-e-processos", "o-futuro-das-organizacoes-alem-da"),
    ("o-surgimento-da-ia-geral-super-humana",
     "o-surgimento-da-ia-geral-super-humana-nao-sera-um-evento-o-avanco-sera-progressivo-dbf420240ecc"),
    ("streaming-data-em-escala-arquiteturas-para-analytics-sem-latencia-analitica",
     "streaming-data-em-escala-arquiteturas"),
)

# #106: posts quase idênticos publicados duas vezes. A cópia absorvida fica no repo com index: false (sai do sitemap e
# ganha noindex) e o endereço dela vai em 301 para a versão original. Vencedora escolhida e registrada na #106: a que
# tem impressões no Search Console e link interno, e é a publicação original (a absorvida é a repostagem seguinte).
DUPLICATES: tuple[tuple[str, str], ...] = (
    ("a-era-dos-prototipos-acabou-como",
     "a-era-dos-prototipos-acabou-como-a-ia-esta-revolucionando-o-desenvolvimento-de-software"),
    ("sabia-2-o-rival-brasileiro-do-chatgpt40",
     "sabia-2-o-novo-modelo-de-linguagem-em-portugues-que-rivaliza-com-gpt-4-maritaca-ai"),
)

# Exceção única de nome legado (#151): o PDF antigo tem maiúsculas, "_", espaço e parênteses no arquivo. Fica fora de
# _TOOL_PATH para não afrouxar a validação dos demais pares; na lista vai codificado como a Cloudflare compara
# http.request.full_uri após a normalização RFC 3986 (espaço -> %20; parênteses são sub-delims reservados e ficam literais).
_FRAMEWORK_PDF = "seo-geo-aeo-framework/Framework-SEO-AEO_GEO_ GuilhermeFavaron(20251201).pdf"
_TOOL_LEGACY_OLD = f"/{_FRAMEWORK_PDF}"
_TOOL_LEGACY_NEW = f"/tools/{_FRAMEWORK_PDF}"

# #151: as quatro ferramentas passaram a morar em /tools/. Diretórios (com "/" no fim) geram origem com e sem barra
# final; arquivos (.html e o PDF legado do framework) geram o caminho exato.
MOVED_TOOLS: tuple[tuple[str, str], ...] = (
    ("/mapa-decisao-ia/", "/tools/mapa-decisao-ia/"),
    ("/mapa-decisao-ia/enviado/", "/tools/mapa-decisao-ia/enviado/"),
    ("/planilha-custos-ia/", "/tools/planilha-custos-ia/"),
    ("/planilha-custos-ia/enviado/", "/tools/planilha-custos-ia/enviado/"),
    ("/llm-ranking/", "/tools/llm-ranking/"),
    ("/seo-geo-aeo-framework/", "/tools/seo-geo-aeo-framework/framework-seo-aeo.html"),
    ("/seo-geo-aeo-framework/framework-seo-aeo.html", "/tools/seo-geo-aeo-framework/framework-seo-aeo.html"),
    (_TOOL_LEGACY_OLD, _TOOL_LEGACY_NEW),
)

# #146: mudanças de endereço do próprio site (fora dos posts). Cada par leva (com e sem barra) em 301 para o endereço
# novo, sem query nem fragmento no target_url (o navegador preserva a âncora, como #cookies).
SITE_MOVES: tuple[tuple[str, str], ...] = (
    ("/blog/privacidade/", "/privacidade/"),
)

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_OLD = re.compile(r"[a-z0-9%]+(?:-[a-z0-9%]+)*")
_TOOL_PATH = re.compile(r"/[a-z0-9]+(?:[/-][a-z0-9]+)*(?:/|\.html)")
_SITE_PATH = re.compile(r"/(?:[a-z0-9]+(?:-[a-z0-9]+)*/)+")


@dataclass(frozen=True)
class Redirect:
    source: str  # host + caminho, sem esquema (casa http e https)
    target: str  # URL absoluta do destino em guifav.dev (sem query nem #)


def post_url(slug: str) -> str:
    return f"{BLOG}{slug}/"


def _repo_page(repo: Path, path: str) -> Path:
    unquoted = unquote(path)
    rel = unquoted.lstrip("/")
    return repo / rel / "index.html" if unquoted.endswith("/") else repo / rel


def _encode_url_path(path: str) -> str:
    """Codifica o caminho como a Cloudflare compara http.request.full_uri (RFC 3986: espaço -> %20; () literais)."""
    return cutover.normalize(path)


def site_url(path: str) -> str:
    return f"https://{HOST}{_encode_url_path(path)}"


def first_hop(old: str) -> str:
    """Onde a regra do cutover leva /post/<old> hoje (a mesma Location nos dois hosts, com e sem barra)."""
    loc = cutover.simulate("www." + cutover.ZONE, f"/post/{old}")
    if loc is None or not loc.startswith(BLOG) or not loc.endswith("/"):
        raise SystemExit(f"/post/{old}: o cutover não leva a {BLOG}<slug>/ ({loc})")
    return loc


def redirects() -> list[Redirect]:
    out = []
    pairs = ([(first_hop(old), post_url(new)) for old, new in OLD_POSTS]
             + [(post_url(dup), post_url(new)) for dup, new in DUPLICATES]
             + [(site_url(old), site_url(new)) for old, new in SITE_MOVES])
    for loc, target in pairs:
        src = loc.removeprefix("https://")
        for s in (src, src.rstrip("/")):  # com e sem barra: a lista casa o caminho exato
            out.append(Redirect(s, target))
    for old_path, new_path in MOVED_TOOLS:
        src = HOST + _encode_url_path(old_path)
        target = site_url(new_path)
        forms = (src, src.rstrip("/")) if old_path.endswith("/") else (src,)
        for s in forms:
            out.append(Redirect(s, target))
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


def problems(reds: list[Redirect] | None = None, posts_dir: Path = POSTS, repo: Path = REPO) -> list[str]:
    """O que impede gravar a lista. Vazio = pode gravar."""
    reds = redirects() if reds is None else reds
    live, every = indexable_posts(posts_dir), all_posts(posts_dir)
    out = []
    olds = [o for o, _ in OLD_POSTS]
    if len(set(olds)) != len(olds):
        out.append("caminho antigo repetido em OLD_POSTS")
    dups = [d for d, _ in DUPLICATES]
    if len(set(dups)) != len(dups) or set(dups) & set(olds):
        out.append("duplicata repetida ou também em OLD_POSTS")
    dup_set = set(dups)
    for old, new in OLD_POSTS:
        if not _OLD.fullmatch(old) or old != old.lower():
            out.append(f"caminho antigo fora do formato: {old!r}")
        if old in every:
            out.append(f"{old}: já é um post; o cutover leva direto a ele")
        # Destino precisa existir como post publicado e não ser duplicata absorvida (pode ter index: false pela #107).
        if not _SLUG.fullmatch(new) or new not in every or new in dup_set:
            out.append(f"{old}: destino {new!r} não é post publicado")
    for dup, new in DUPLICATES:
        if dup not in every or dup in live:
            out.append(f"{dup}: a duplicata absorvida precisa ficar no repo com index: false")
        if not _SLUG.fullmatch(new) or new not in live or new == dup:
            out.append(f"{dup}: destino {new!r} não é post indexável")
    tool_olds = [o for o, _ in MOVED_TOOLS]
    if len(set(tool_olds)) != len(tool_olds):
        out.append("caminho antigo repetido em MOVED_TOOLS")
    for old_path, new_path in MOVED_TOOLS:
        old_ok = bool(_TOOL_PATH.fullmatch(old_path)) or old_path == _TOOL_LEGACY_OLD
        new_ok = bool(_TOOL_PATH.fullmatch(new_path)) or new_path == _TOOL_LEGACY_NEW
        if not old_ok or old_path.startswith(("/blog/", "/tools/")):
            out.append(f"caminho antigo fora do formato em MOVED_TOOLS: {old_path!r}")
        if not new_path.startswith("/tools/") or not new_ok:
            out.append(f"{old_path}: destino fora de /tools/: {new_path!r}")
        if _repo_page(repo, old_path).exists():
            out.append(f"{old_path}: origem ainda existe no repo")
        if not _repo_page(repo, new_path).is_file():
            out.append(f"{old_path}: destino {new_path!r} não existe no repo")
    moves = [m for m, _ in SITE_MOVES]
    if len(set(moves)) != len(moves):
        out.append("caminho antigo repetido em SITE_MOVES")
    post_paths = {f"/blog/{s}/" for s in (*olds, *dups)}
    tool_paths = set(tool_olds)
    for old, new in SITE_MOVES:
        if not _SITE_PATH.fullmatch(old) or not _SITE_PATH.fullmatch(new):
            out.append(f"mudança de endereço fora do formato /caminho/: {old!r} -> {new!r}")
            continue
        if old == new:
            out.append(f"{old}: origem e destino iguais em SITE_MOVES")
        if old in post_paths:
            out.append(f"{old}: já está em OLD_POSTS ou DUPLICATES")
        if old in tool_paths:
            out.append(f"{old}: já está em MOVED_TOOLS")
        if not (repo / new.lstrip("/") / "index.html").is_file():
            out.append(f"{old}: destino {new!r} não existe no repo ({new.lstrip('/')}index.html)")
        if (repo / old.lstrip("/") / "index.html").exists():
            out.append(f"{old}: ainda existe na raiz do repo; o redirect esconderia a página")
        if old.startswith("/blog/"):
            slug = old.removeprefix("/blog/").rstrip("/")
            if slug in live:
                out.append(f"{old}: é um post indexável; o redirect esconderia a página")
            if (repo / "blog-src/src/pages" / f"{slug}.astro").exists():
                out.append(f"{old}: ainda existe em blog-src/src/pages/{slug}.astro")
    sources = [r.source for r in reds]
    if len(set(sources)) != len(sources):
        out.append("origem repetida na lista")
    targets = {r.target for r in reds}
    pdf_target = site_url(_TOOL_LEGACY_NEW)
    for r in reds:
        if "/" not in r.source:
            out.append(f"origem fora de {HOST}/: {r.source}")
            continue
        path = "/" + r.source.split("/", 1)[1]
        if not r.source.startswith(HOST + "/") or path == "/":
            out.append(f"origem fora de {HOST}/: {r.source}")
        elif path.startswith("/blog/"):
            slug = path.removeprefix("/blog/").rstrip("/")
            if slug in live:
                out.append(f"origem {r.source} é um post indexável: o redirect esconderia a página")
        elif _repo_page(repo, path).is_file() or (
                not path.endswith("/") and _repo_page(repo, path + "/").is_file()):
            out.append(f"origem {r.source} existe no repo: o redirect esconderia a página")
        if any(c.isspace() for c in r.source) or "?" in r.source or "#" in r.source:
            out.append(f"origem com caractere não codificado ou inválido: {r.source}")
        if "https://" + r.source in targets or "https://" + r.source + "/" in targets:
            out.append(f"origem {r.source} também é destino: cadeia de redirects")
        valid_end = (r.target.endswith("/")
                     or (r.target.startswith(TOOLS) and r.target.endswith(".html"))
                     or r.target == pdf_target)
        if (not r.target.startswith(f"https://{HOST}/") or not valid_end
                or r.target == f"https://{HOST}/" or "?" in r.target or "#" in r.target
                or any(c.isspace() for c in r.target)):
            out.append(f"destino fora de https://{HOST}/.../: {r.target}")
    if len(reds) * len(LISTS) > MAX_ITEMS:
        out.append(f"{len(reds)} itens em {len(LISTS)} listas: o plano Free aceita {MAX_ITEMS} somando as listas")
    return out


def list_items(reds: list[Redirect] | None = None) -> list[dict]:
    reds = redirects() if reds is None else reds
    return [{"redirect": {"source_url": r.source, "target_url": r.target, "status_code": 301,
                          "preserve_query_string": False, "include_subdomains": False,
                          "subpath_matching": False}} for r in reds]


def rule(list_name: str) -> dict:
    if list_name not in LISTS:
        raise ValueError(list_name)
    return {"description": RULE_DESCRIPTION, "expression": f"http.request.full_uri in ${list_name}",
            "action": "redirect",
            "action_parameters": {"from_list": {"name": list_name, "key": "http.request.full_uri"}},
            "enabled": True}


def active_list(rules: list[dict]) -> str | None:
    """Lista do par ligada pela regra deste script (None sem regra). Recusa estado que o script não produz."""
    if not rules:
        return None
    if len(rules) > 1:
        raise SystemExit(f"a fase {PHASE} da conta tem {len(rules)} regras deste script; o script grava uma só")
    name = ((rules[0].get("action_parameters") or {}).get("from_list") or {}).get("name")
    if name not in LISTS:
        raise SystemExit(f"a regra deste script liga a lista {name!r}, fora do par {LISTS}: conferir no painel")
    return name


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
        with e:  # fecha o corpo da resposta de erro
            try:
                data = json.load(e)
            except ValueError:
                raise SystemExit(f"Cloudflare {method} {path}: HTTP {e.code} sem corpo JSON") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SystemExit(f"Cloudflare {method} {path}: falha de conexão ou resposta ilegível "
                         f"({type(e).__name__})") from None
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
        q = "?per_page=500" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
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
    out = {k: r.get(k, defaults.get(k)) for k in _ITEM_KEYS}
    # A Cloudflare aceita %20 no PUT, mas devolve source_url com %20 decodificado para espaço literal no GET
    # (mantendo target_url com %20). Normalizamos só %20 em source_url para conferir sem afrouxar os outros campos.
    if isinstance(out["source_url"], str):
        out["source_url"] = out["source_url"].replace("%20", " ")
    return out


def apply(token: str, account: str, opener=urllib.request.urlopen, sleep=time.sleep) -> int:
    bad = problems()
    if bad:
        raise SystemExit("lista recusada:\n  " + "\n  ".join(bad))
    active = active_list(_phase_rules(account, token, opener))  # recusa antes de qualquer escrita
    target = LISTS[1] if active == LISTS[0] else LISTS[0]
    lists = _cf("GET", f"/accounts/{account}/rules/lists", token, opener=opener)["result"] or []
    for x in lists:
        if x.get("name") in LISTS and x.get("kind") != "redirect":
            raise SystemExit(f"já existe uma lista {x.get('name')} de tipo {x.get('kind')!r}")
        if x.get("name") in LISTS and x.get("description") != LIST_DESCRIPTION:
            raise SystemExit(f"a lista {x.get('name')} existe com outra descrição ({x.get('description')!r}): "
                             "não foi criada por este script, e o apply não a adota")
    same = [x for x in lists if x.get("name") == target]
    if same:
        list_id = same[0]["id"]
    else:
        list_id = _cf("POST", f"/accounts/{account}/rules/lists", token,
                      {"name": target, "description": LIST_DESCRIPTION, "kind": "redirect"},
                      opener=opener)["result"]["id"]
    kept = (f"a regra segue na lista {active}, intacta" if active else "nenhuma regra foi gravada")
    sent = list_items()
    op = _cf("PUT", f"/accounts/{account}/rules/lists/{list_id}/items", token, sent, opener=opener)["result"]
    try:
        _wait(account, op["operation_id"], token, opener, sleep)
    except SystemExit as e:
        raise SystemExit(f"{e}; {kept}") from None
    got = sorted((_norm_item(i) for i in _items(account, list_id, token, opener)),
                 key=lambda d: str(d["source_url"] or ""))
    want = sorted((_norm_item(i) for i in sent), key=lambda d: str(d["source_url"] or ""))
    if got != want:
        raise SystemExit(f"a lista {target} lida de volta não confere com a enviada ({len(got)} de {len(want)} "
                         f"itens); {kept}: rodar apply de novo")
    entry = f"/accounts/{account}/rulesets/phases/{PHASE}/entrypoint"
    res = _cf("PUT", entry, token, {"rules": [rule(target)]}, opener=opener)["result"]
    back = [{k: r.get(k) for k in _RULE_KEYS} for r in res.get("rules") or []]
    if back != [rule(target)]:
        raise SystemExit(f"a regra gravada não confere com a enviada (lista {target}): conferir no painel")
    print(f"lista {target}: {len(got)} itens; regra ligada a ela na fase {PHASE} da conta (ruleset {res.get('id')})"
          + (f"; a lista {active} ficou sem efeito" if active else ""))
    return 0


def remove(token: str, account: str, opener=urllib.request.urlopen) -> int:
    rules = _phase_rules(account, token, opener)
    active_list(rules)
    if not rules:
        print(f"a fase {PHASE} da conta já está sem regras deste script: nada a remover")
        return 0
    entry = f"/accounts/{account}/rulesets/phases/{PHASE}/entrypoint"
    res = _cf("PUT", entry, token, {"rules": []}, opener=opener)["result"]
    if res.get("rules"):
        raise SystemExit("a fase ainda tem regras depois do PUT: conferir no painel")
    print(f"regra removida da fase {PHASE} da conta; as listas {LISTS} ficaram, sem efeito")
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
    for dup, new in DUPLICATES:
        checks.append((post_url(new), 200, None))
    for target in dict.fromkeys(site_url(new) for _, new in (*MOVED_TOOLS, *SITE_MOVES)):
        checks.append((target, 200, None))
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
        print(json.dumps({"lists": list(LISTS), "items": list_items(), "rules": [rule(n) for n in LISTS]},
                         ensure_ascii=False, indent=2))
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
