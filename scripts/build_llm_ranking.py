"""Gera no build o HTML do /llm-ranking/ a partir do snapshot da Arena (issue #113).

Fonte: o dataset oficial `lmarena-ai/leaderboard-dataset` no Hugging Face (CC BY 4.0), arena de texto com controle
de estilo (`text_style_control`). O snapshot vem do repositório privado guifav/llm-ranking-data, gravado na KVM por
scripts/llm_ranking_snapshot.py; o build não chama a fonte nem guarda chave nenhuma.

O template é llm-ranking/index.html, copiado para o _site pelo assemble_site.py. Cada bloco gerado entra no lugar de
um marcador <!--LLM:nome--> que aparece uma vez no template. O resultado:

- a tabela com os TOP primeiros modelos, legível sem JavaScript (o llm-ranking/ordenar.js só reordena);
- a faixa de posições de cada modelo, calculada pelos intervalos de 95% sobre a lista inteira;
- os destaques do topo, o "o que mudou" na janela de 7 dias, os recortes e a atribuição exigida pela CC BY 4.0;
- a data da publicação na meta DATA_META, que o build_sitemaps.py usa como lastmod da página (e o IndexNow, por
  consequência, só avisa quando os dados mudam), e o JSON-LD WebPage com isBasedOn e dateModified.

Uso (depois do assemble_site.py, antes do build_sitemaps.py):
    python3 scripts/build_llm_ranking.py --snapshot _dados/llm-ranking/arena-texto.json [--site _site]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, timedelta
from html import escape
from pathlib import Path

PAGE = "llm-ranking/index.html"
URL = "https://guifav.dev/llm-ranking/"
PERSON_ID = "https://guifav.dev/#person"
DATASET = "lmarena-ai/leaderboard-dataset"
DATASET_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
CONFIG = "text_style_control"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.pt-br"
ARENA_URL = "https://arena.ai/leaderboard/text"
DATA_META = "dados-publicados-em"
SCHEMA = 1
TOP = 100
JANELA_DIAS = 7
MAX_MUDANCAS = 8
TOP_DESTAQUE = 10
TOP_ABERTOS = 5
TOP_CATEGORIA = 3
TOP_ORGANIZACOES = 10
MARKER = re.compile(r"<!--LLM:([a-z-]+)-->")
DATA_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Licenças que a fonte usa (lista fechada: valor novo falha no snapshot e no build, para alguém decidir se é de pesos
# abertos). FECHADAS e SEM_INFORMACAO não contam como pesos abertos; todas as outras contam.
FECHADAS = {"Proprietary"}
SEM_INFORMACAO = {"", "Other"}
ABERTAS = {
    "AI2 ImpACT Low-risk", "Apache 2.0", "Apache-2.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "DBRX LICENSE", "DeepSeek",
    "DeepSeek License", "Falcon-180B TII License", "Gemma", "Gemma license", "Jamba Open", "Kimi K3 license",
    "Kimi K3.1 Pro License", "Llama", "Llama 2 Community", "Llama 3 Community", "Llama 3.1", "Llama 3.1 Community",
    "Llama 3.2", "Llama 4", "Llama-3.3", "MIT", "MRL", "MiniMax Community License", "Mistral Research", "Modified MIT",
    "NVIDIA Open Model", "NexusFlow", "Non-commercial", "Nvidia", "Nvidia Open", "Nvidia Open Model", "OpenMDW-1.1",
    "Qianwen LICENSE", "Qwen", "Yi License", "tencent-hunyuan-community",
}
LICENCAS = FECHADAS | SEM_INFORMACAO | ABERTAS

# Recortes (parecer do Fable 5.2 na #113): categoria da fonte -> rótulo na página.
TAREFAS = {
    "coding": "Programação",
    "math": "Matemática",
    "hard_prompts": "Pedidos difíceis",
    "instruction_following": "Seguir instruções",
    "multi_turn": "Conversa com várias trocas",
    "longer_query": "Pedidos longos",
    "creative_writing": "Escrita criativa",
    "expert": "Perguntas de especialista",
    "non_english": "Fora do inglês (a fonte não tem recorte de português)",
}
SETORES = {
    "industry_software_and_it_services": "Software e TI",
    "industry_business_and_management_and_financial_operations": "Negócios, gestão e finanças",
    "industry_legal_and_government": "Jurídico e governo",
    "industry_medicine_and_healthcare": "Medicina e saúde",
    "industry_mathematical": "Matemática aplicada",
    "industry_life_and_physical_and_social_science": "Ciências da vida, físicas e sociais",
    "industry_writing_and_literature_and_language": "Escrita, literatura e idiomas",
    "industry_entertainment_and_sports_and_media": "Entretenimento, esportes e mídia",
}
CATEGORIAS = (*TAREFAS, *SETORES)
CAMPOS = ("posicao", "modelo", "organizacao", "licenca", "pontuacao", "inferior", "superior", "votos")
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


class RankingError(Exception):
    """Snapshot inválido ou template sem os marcadores esperados."""


# ---------------------------------------------------------------- validação


def _dia(s, campo: str) -> date:
    if not isinstance(s, str) or not DATA_RE.fullmatch(s):
        raise RankingError(f"{campo}: data fora do formato AAAA-MM-DD: {s!r}")
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise RankingError(f"{campo}: data inválida: {s!r}") from e


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validar_linhas(linhas, onde: str, minimo: int = 1) -> None:
    """Uma lista de modelos da fonte: posições 1..N em ordem, intervalo que contém a pontuação, licença conhecida."""
    if not isinstance(linhas, list) or len(linhas) < minimo:
        raise RankingError(f"{onde}: esperava ao menos {minimo} modelos")
    nomes = set()
    for i, m in enumerate(linhas, 1):
        if not isinstance(m, dict) or set(m) != set(CAMPOS):
            raise RankingError(f"{onde}[{i}]: campos {sorted(m) if isinstance(m, dict) else m!r}, esperado {list(CAMPOS)}")
        if m["posicao"] != i or isinstance(m["posicao"], bool):
            raise RankingError(f"{onde}[{i}]: posição {m['posicao']!r} fora da ordem 1..N")
        if not isinstance(m["modelo"], str) or not m["modelo"].strip():
            raise RankingError(f"{onde}[{i}]: modelo sem nome")
        if m["modelo"] in nomes:
            raise RankingError(f"{onde}[{i}]: modelo repetido {m['modelo']!r}")
        nomes.add(m["modelo"])
        if not isinstance(m["organizacao"], str):
            raise RankingError(f"{onde}[{i}]: organização inválida")
        if m["licenca"] not in LICENCAS:
            raise RankingError(f"{onde}[{i}]: licença desconhecida {m['licenca']!r} (classifique em build_llm_ranking.py)")
        if not all(_num(m[k]) for k in ("pontuacao", "inferior", "superior")):
            raise RankingError(f"{onde}[{i}]: pontuação ou intervalo não numérico")
        if not m["inferior"] <= m["pontuacao"] <= m["superior"]:
            raise RankingError(f"{onde}[{i}]: pontuação fora do intervalo")
        if i > 1 and m["pontuacao"] > linhas[i - 2]["pontuacao"]:
            raise RankingError(f"{onde}[{i}]: pontuação maior que a da posição anterior")
        if not isinstance(m["votos"], int) or isinstance(m["votos"], bool) or m["votos"] < 0:
            raise RankingError(f"{onde}[{i}]: votos inválidos")


def validar(snap, hoje: date | None = None) -> dict:
    """Confere o snapshot inteiro e devolve-o. Qualquer desvio é erro: a página não sai com dado duvidoso."""
    hoje = hoje or date.today()
    if not isinstance(snap, dict):
        raise RankingError("snapshot não é um objeto JSON")
    esperado = {"schema", "fonte", "publicacao", "modelos", "categorias", "anterior"}
    if set(snap) != esperado:
        raise RankingError(f"snapshot com campos {sorted(snap)}, esperado {sorted(esperado)}")
    if snap["schema"] != SCHEMA:
        raise RankingError(f"schema {snap['schema']!r}, esperado {SCHEMA}")
    if snap["fonte"] != {"dataset": DATASET, "config": CONFIG}:
        raise RankingError(f"fonte inesperada: {snap['fonte']!r}")
    pub = _dia(snap["publicacao"], "publicacao")
    if pub > hoje + timedelta(days=1):
        raise RankingError(f"publicacao {pub} no futuro")
    validar_linhas(snap["modelos"], "modelos", TOP)
    cats = snap["categorias"]
    if not isinstance(cats, dict) or set(cats) != set(CATEGORIAS):
        raise RankingError(f"categorias {sorted(cats) if isinstance(cats, dict) else cats!r}, esperado {sorted(CATEGORIAS)}")
    for c in CATEGORIAS:
        validar_linhas(cats[c], f"categorias.{c}")
        if len(cats[c]) > TOP_CATEGORIA:
            raise RankingError(f"categorias.{c}: mais de {TOP_CATEGORIA} modelos")
    ant = snap["anterior"]
    if ant is not None:
        if not isinstance(ant, dict) or set(ant) != {"publicacao", "modelos"}:
            raise RankingError("anterior: esperado {publicacao, modelos} ou null")
        d = _dia(ant["publicacao"], "anterior.publicacao")
        if d > pub - timedelta(days=JANELA_DIAS):
            raise RankingError(f"anterior.publicacao {d} a menos de {JANELA_DIAS} dias de {pub}")
        validar_linhas(ant["modelos"], "anterior.modelos")
    return snap


# ---------------------------------------------------------------- cálculos


def aberto(m: dict) -> bool:
    return m["licenca"] in ABERTAS


def sobrepoe(a: dict, b: dict) -> bool:
    return a["inferior"] <= b["superior"] and b["inferior"] <= a["superior"]


def faixas(modelos: list[dict]) -> list[tuple[int, int]]:
    """Faixa de posições possíveis de cada modelo pelos intervalos de 95%, sobre a lista inteira (parecer do Fable):
    melhor = 1 + modelos cujo limite inferior supera o superior deste (certamente acima); pior = 1 + modelos cujo
    limite superior alcança o inferior deste (talvez acima). Intervalos fechados, como em sobrepoe(): dois modelos que
    só encostam estão na margem um do outro, e a faixa dos dois inclui a posição do outro."""
    out = []
    for m in modelos:
        melhor = 1 + sum(1 for o in modelos if o is not m and o["inferior"] > m["superior"])
        pior = 1 + sum(1 for o in modelos if o is not m and o["superior"] >= m["inferior"])
        out.append((melhor, pior))
    return out


def na_margem_do_lider(modelos: list[dict]) -> int:
    """Quantos outros modelos têm intervalo que se sobrepõe ao do líder."""
    lider = modelos[0]
    return sum(1 for o in modelos[1:] if sobrepoe(o, lider))


def mudancas(atual: list[dict], antes: list[dict]) -> list[str]:
    """O que mudou desde a publicação anterior (parecer do Fable): novo líder; entradas e saídas do top 10; modelos
    novos e removidos dentro do top 100; subidas e quedas no top 100 só quando o intervalo de agora e o de antes não se
    sobrepõem. No máximo MAX_MUDANCAS itens, nessa prioridade. O pareamento é pelo nome da fonte."""
    por_nome_antes = {m["modelo"]: m for m in antes}
    por_nome_agora = {m["modelo"]: m for m in atual}
    itens: list[str] = []
    lider, lider_antes = atual[0], antes[0]
    if lider["modelo"] != lider_antes["modelo"]:
        ex = por_nome_agora.get(lider_antes["modelo"])
        margem = " (dentro da margem de erro)" if ex is not None and sobrepoe(lider, ex) else ""
        itens.append(f"Novo líder: {_m(lider)}, no lugar de {_m(lider_antes)}{margem}.")
    top10 = [m["modelo"] for m in atual[:TOP_DESTAQUE]]
    top10_antes = [m["modelo"] for m in antes[:TOP_DESTAQUE]]
    for n in top10:
        if n not in top10_antes:
            p = por_nome_antes.get(n)
            onde = f"vindo da {p['posicao']}ª posição" if p else "estreia na lista"
            itens.append(f"Entrou no top 10: {_m(por_nome_agora[n])}, {por_nome_agora[n]['posicao']}ª posição ({onde}).")
    for n in top10_antes:
        if n not in top10:
            agora = por_nome_agora.get(n)
            onde = f"agora na {agora['posicao']}ª posição" if agora else "saiu da lista"
            itens.append(f"Saiu do top 10: {_m(por_nome_antes[n])} ({onde}).")
    for m in atual[:TOP]:
        if m["modelo"] not in por_nome_antes and m["modelo"] not in top10:
            itens.append(f"Modelo novo: {_m(m)}, {m['posicao']}ª posição.")
    for m in antes[:TOP]:
        if m["modelo"] not in por_nome_agora:
            itens.append(f"Saiu da lista: {_m(m)}, que estava na {m['posicao']}ª posição.")
    for m in atual[:TOP]:
        p = por_nome_antes.get(m["modelo"])
        if p is None or sobrepoe(m, p):
            continue
        verbo = "Subiu" if m["inferior"] > p["superior"] else "Caiu"
        itens.append(f"{verbo} além da margem de erro: {_m(m)}, de {_n(p['pontuacao'])} para {_n(m['pontuacao'])} "
                     f"pontos ({p['posicao']}ª para {m['posicao']}ª posição).")
    return itens[:MAX_MUDANCAS]


def organizacoes(modelos: list[dict]) -> list[tuple[str, dict, int]]:
    """As TOP_ORGANIZACOES primeiras pela melhor posição: (organização, melhor modelo, modelos no top 100)."""
    melhor: dict[str, dict] = {}
    for m in modelos:
        melhor.setdefault(m["organizacao"], m)
    contagem: dict[str, int] = {}
    for m in modelos[:TOP]:
        contagem[m["organizacao"]] = contagem.get(m["organizacao"], 0) + 1
    ordem = sorted(melhor.items(), key=lambda kv: kv[1]["posicao"])[:TOP_ORGANIZACOES]
    return [(org, m, contagem.get(org, 0)) for org, m in ordem]


# ---------------------------------------------------------------- formatação


def _n(x: float) -> str:
    """Inteiro arredondado (meio para cima), com ponto de milhar."""
    return f"{math.floor(x + 0.5):,}".replace(",", ".")


def _m(m: dict) -> str:
    return escape(m["modelo"])


def _org(m: dict) -> str:
    return escape(m["organizacao"]) if m["organizacao"] else "não informada"


def _lic(m: dict) -> str:
    if m["licenca"] in FECHADAS:
        return "Proprietária"
    if m["licenca"] in SEM_INFORMACAO:
        return "Não informada" if not m["licenca"] else "Outra"
    return escape(m["licenca"])


def _faixa(f: tuple[int, int]) -> str:
    return str(f[0]) if f[0] == f[1] else f"{f[0]} a {f[1]}"


def _pont(m: dict) -> str:
    return f"{_n(m['pontuacao'])} <span class=\"ic\">({_n(m['inferior'])} a {_n(m['superior'])})</span>"


def data_br(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def data_extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


# ---------------------------------------------------------------- blocos


def bloco_meta(pub: date) -> str:
    return f'<meta name="{DATA_META}" content="{pub.isoformat()}">'


def bloco_jsonld(pub: date) -> str:
    doc = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebPage",
                "@id": URL + "#webpage",
                "url": URL,
                "name": "Ranking de LLMs (LMArena): os 100 melhores modelos de IA",
                "inLanguage": "pt-BR",
                "dateModified": pub.isoformat(),
                "author": {"@id": PERSON_ID},
                "isBasedOn": {
                    "@type": "Dataset",
                    "name": "Arena Leaderboard Dataset",
                    "url": DATASET_URL,
                    "license": "https://creativecommons.org/licenses/by/4.0/",
                    "creator": {"@type": "Organization", "name": "Arena", "url": "https://arena.ai/"},
                },
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Guilherme Favaron", "item": "https://guifav.dev/"},
                    {"@type": "ListItem", "position": 2, "name": "Ranking de LLMs", "item": URL},
                ],
            },
        ],
    }
    txt = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{txt}</script>'


def bloco_data(pub: date) -> str:
    return escape(data_br(pub))


def bloco_total(modelos: list[dict]) -> str:
    return str(len(modelos))


def bloco_destaques(modelos: list[dict], pub: date) -> str:
    lider = modelos[0]
    n = na_margem_do_lider(modelos)
    margem = ("nenhum outro modelo está dentro da margem de erro dele" if n == 0 else
              f"{n} {'modelo está' if n == 1 else 'modelos estão'} dentro da margem de erro dele")
    ab = next((m for m in modelos if aberto(m)), None)
    itens = [f"<li><strong>Líder:</strong> {_m(lider)}, da organização {_org(lider)}, com {_n(lider['pontuacao'])} "
             f"pontos; {margem}.</li>"]
    if ab is not None:
        itens.append(f"<li><strong>Melhor de pesos abertos:</strong> {_m(ab)}, da organização {_org(ab)}, na "
                     f"{ab['posicao']}ª posição (licença: {_lic(ab)}).</li>")
    itens.append(f"<li><strong>Dados:</strong> publicação da Arena de {escape(data_extenso(pub))}.</li>")
    return '<ul class="destaques">\n' + "\n".join(itens) + "\n</ul>"


def bloco_mudancas(snap: dict) -> str:
    ant = snap["anterior"]
    if ant is None:
        return ""
    itens = mudancas(snap["modelos"], ant["modelos"])
    if not itens:
        return ""
    de, ate = date.fromisoformat(ant["publicacao"]), date.fromisoformat(snap["publicacao"])
    lis = "\n".join(f"<li>{t}</li>" for t in itens)
    return (f'<section id="o-que-mudou" class="mudancas">\n<h2>O que mudou</h2>\n'
            f'<p class="section-intro">Publicação de {data_br(ate)} comparada com a de {data_br(de)}.</p>\n'
            f'<ul>\n{lis}\n</ul>\n</section>')


def bloco_tabela(modelos: list[dict]) -> str:
    fx = faixas(modelos)
    linhas = []
    for m, f in list(zip(modelos, fx))[:TOP]:
        linhas.append(
            f'<tr><td data-valor="{m["posicao"]}">{m["posicao"]}</td>'
            f'<td data-valor="{f[0]}">{_faixa(f)}</td>'
            f"<td>{_m(m)}</td><td>{_org(m)}</td>"
            f'<td data-valor="{m["pontuacao"]!r}">{_pont(m)}</td>'
            f'<td data-valor="{m["votos"]}">{_n(m["votos"])}</td>'
            f"<td>{_lic(m)}</td></tr>")
    corpo = "\n".join(linhas)
    return (f'<table id="ranking" class="ranking">\n'
            f'<caption>Os {min(TOP, len(modelos))} primeiros de {len(modelos)} modelos da arena de texto</caption>\n'
            f'<thead><tr><th scope="col" data-ordem="num">Posição</th><th scope="col" data-ordem="num">Faixa</th>'
            f'<th scope="col" data-ordem="texto">Modelo</th><th scope="col" data-ordem="texto">Organização</th>'
            f'<th scope="col" data-ordem="num">Pontuação (intervalo de 95%)</th>'
            f'<th scope="col" data-ordem="num">Votos</th><th scope="col" data-ordem="texto">Licença</th></tr></thead>\n'
            f"<tbody>\n{corpo}\n</tbody>\n</table>")


def bloco_abertos(modelos: list[dict]) -> str:
    ab = [m for m in modelos if aberto(m)][:TOP_ABERTOS]
    if not ab:
        return '<p class="section-intro">Nenhum modelo de pesos abertos na lista desta publicação.</p>'
    linhas = "\n".join(f"<tr><td>{m['posicao']}</td><td>{_m(m)}</td><td>{_org(m)}</td><td>{_lic(m)}</td>"
                       f"<td>{_pont(m)}</td></tr>" for m in ab)
    return ('<table class="recorte">\n<thead><tr><th scope="col">Posição geral</th><th scope="col">Modelo</th>'
            '<th scope="col">Organização</th><th scope="col">Licença</th><th scope="col">Pontuação</th></tr></thead>\n'
            f"<tbody>\n{linhas}\n</tbody>\n</table>")


def bloco_categorias(cats: dict, rotulos: dict[str, str], titulo: str) -> str:
    linhas = []
    for chave, rotulo in rotulos.items():
        nomes = [f"{_m(m)} <span class=\"ic\">({_n(m['pontuacao'])})</span>" for m in cats[chave]]
        nomes += ["&nbsp;"] * (TOP_CATEGORIA - len(nomes))
        linhas.append(f"<tr><th scope=\"row\">{escape(rotulo)}</th>" + "".join(f"<td>{n}</td>" for n in nomes) + "</tr>")
    corpo = "\n".join(linhas)
    return (f'<table class="recorte">\n<thead><tr><th scope="col">{escape(titulo)}</th><th scope="col">1º</th>'
            f'<th scope="col">2º</th><th scope="col">3º</th></tr></thead>\n<tbody>\n{corpo}\n</tbody>\n</table>')


def bloco_organizacoes(modelos: list[dict]) -> str:
    linhas = "\n".join(f"<tr><td>{_org(m)}</td><td>{_m(m)}</td><td>{m['posicao']}</td><td>{n}</td></tr>"
                       for _, m, n in organizacoes(modelos))
    return ('<table class="recorte">\n<thead><tr><th scope="col">Organização</th><th scope="col">Melhor modelo</th>'
            '<th scope="col">Posição</th><th scope="col">Modelos no top 100</th></tr></thead>\n'
            f"<tbody>\n{linhas}\n</tbody>\n</table>")


def bloco_atribuicao(pub: date) -> str:
    return (f'<p class="atribuicao">Fonte: Arena (<a href="{ARENA_URL}">arena.ai</a>), dataset '
            f'<a href="{DATASET_URL}">{DATASET}</a>, subconjunto <code>{CONFIG}</code>, licença '
            f'<a href="{LICENSE_URL}" rel="license">CC BY 4.0</a>, publicação de {data_br(pub)}. Adaptação do '
            f"guifav.dev: recorte dos {TOP} primeiros, valores arredondados, faixa de posições calculada a partir dos "
            f"intervalos, destaques, comparação com a publicação anterior e recortes por categoria e por organização. "
            f"A Arena não revisou nem endossa esta página.</p>")


def blocos(snap: dict) -> dict[str, str]:
    pub = date.fromisoformat(snap["publicacao"])
    modelos = snap["modelos"]
    return {
        "meta": bloco_meta(pub),
        "jsonld": bloco_jsonld(pub),
        "data": bloco_data(pub),
        "total": bloco_total(modelos),
        "destaques": bloco_destaques(modelos, pub),
        "mudancas": bloco_mudancas(snap),
        "tabela": bloco_tabela(modelos),
        "atribuicao": bloco_atribuicao(pub),
        "abertos": bloco_abertos(modelos),
        "tarefas": bloco_categorias(snap["categorias"], TAREFAS, "Tipo de tarefa"),
        "setores": bloco_categorias(snap["categorias"], SETORES, "Setor"),
        "organizacoes": bloco_organizacoes(modelos),
    }


def render(template: str, snap: dict) -> str:
    """Troca cada marcador pelo bloco. Marcador faltando, repetido ou desconhecido é erro."""
    bl = blocos(snap)
    achados = MARKER.findall(template)
    faltam = [k for k in bl if achados.count(k) != 1]
    sobram = sorted(set(achados) - set(bl))
    if faltam or sobram:
        raise RankingError(f"template: marcadores que não aparecem uma vez: {faltam}; desconhecidos: {sobram}")
    return MARKER.sub(lambda mo: bl[mo.group(1)], template)


def build(snapshot: Path, site: Path, hoje: date | None = None) -> tuple[int, str]:
    try:
        snap = json.loads(snapshot.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RankingError(f"snapshot não encontrado: {snapshot} (o checkout do guifav/llm-ranking-data falhou?)") from e
    except json.JSONDecodeError as e:
        raise RankingError(f"snapshot não é JSON válido: {e}") from e
    validar(snap, hoje)
    page = site / PAGE
    if not page.is_file():
        raise RankingError(f"{page} não existe: rode o assemble_site.py antes")
    page.write_text(render(page.read_text(encoding="utf-8"), snap), encoding="utf-8")
    return min(TOP, len(snap["modelos"])), snap["publicacao"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gera o HTML do /llm-ranking/ no _site a partir do snapshot da Arena.")
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--site", default="_site", type=Path)
    args = ap.parse_args(argv)
    try:
        n, pub = build(args.snapshot, args.site)
    except RankingError as e:
        print(f"build_llm_ranking: ERRO: {e}", file=sys.stderr)
        return 1
    print(f"build_llm_ranking: {n} modelos na tabela, dados de {pub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
