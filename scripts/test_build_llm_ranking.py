"""Testes do gerador do /llm-ranking/ (scripts/build_llm_ranking.py, issue #113).

Rodar: python3 scripts/test_build_llm_ranking.py
"""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_llm_ranking as B  # noqa: E402
import check_site as C  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOJE = date(2026, 11, 24)
ORGS = ("google", "anthropic", "openai", "meta", "deepseek", "alibaba", "mistral", "xai", "moonshot", "zai", "nvidia", "")
LICS = ("Proprietary", "Proprietary", "MIT", "Apache 2.0", "Proprietary", "Llama 4", "Other", "")


def modelo(i: int, rating: float | None = None, meia: float = 5.0, nome: str | None = None, lic: str | None = None,
           org: str | None = None, votos: int = 1000) -> dict:
    r = 1600.0 - 2 * i if rating is None else rating
    return {"posicao": i, "modelo": nome or f"modelo-{i:03d}", "organizacao": ORGS[i % len(ORGS)] if org is None else org,
            "licenca": LICS[i % len(LICS)] if lic is None else lic, "pontuacao": r, "inferior": r - meia,
            "superior": r + meia, "votos": votos}


def lista(n: int) -> list[dict]:
    return [modelo(i) for i in range(1, n + 1)]


def snapshot(n: int = 120, anterior: bool = True) -> dict:
    cats = {c: [modelo(i, nome=f"{c}-{i}") for i in range(1, 4)] for c in B.CATEGORIAS}
    snap = {"schema": 1, "fonte": {"dataset": B.DATASET, "config": B.CONFIG}, "publicacao": "2026-11-22",
            "modelos": lista(n), "categorias": cats,
            "anterior": {"publicacao": "2026-11-11", "modelos": lista(n)} if anterior else None}
    return snap


def renum(ms: list[dict]) -> list[dict]:
    """Reordena pela pontuação e refaz as posições 1..N."""
    ms = sorted(ms, key=lambda m: -m["pontuacao"])
    for i, m in enumerate(ms, 1):
        m["posicao"] = i
    return ms


class ValidarTest(unittest.TestCase):
    def ok(self, snap):
        return B.validar(snap, HOJE)

    def bad(self, snap, trecho: str):
        with self.assertRaises(B.RankingError) as cm:
            B.validar(snap, HOJE)
        self.assertIn(trecho, str(cm.exception))

    def test_accepts_the_fixture(self):
        s = snapshot()
        self.assertIs(self.ok(s), s)

    def test_accepts_without_previous(self):
        self.ok(snapshot(anterior=False))

    def test_rejects_wrong_top_level(self):
        s = snapshot(); s["extra"] = 1
        self.bad(s, "campos")
        for v in (2, True, 1.0, "1"):
            s = snapshot(); s["schema"] = v
            with self.subTest(schema=v):
                self.bad(s, "schema")
        s = snapshot(); s["fonte"] = {"dataset": B.DATASET, "config": "text"}
        self.bad(s, "fonte")
        self.bad([], "não é um objeto")

    def test_rejects_bad_dates(self):
        for v in ("22/11/2026", "2026-11-31", 20261122, "2026-11-22T00:00"):
            s = snapshot(); s["publicacao"] = v
            with self.subTest(v=v):
                self.bad(s, "publicacao")
        s = snapshot(); s["publicacao"] = "2026-11-26"
        self.bad(s, "no futuro")
        s = snapshot(); s["publicacao"] = "2026-11-25"
        self.ok(s)

    def test_rejects_too_few_models(self):
        self.bad(snapshot(n=B.TOP - 1), f"ao menos {B.TOP}")
        self.ok(snapshot(n=B.TOP))

    def test_rejects_bad_rows(self):
        casos = [
            (lambda m: m.update(posicao=7), "fora da ordem"),
            (lambda m: m.update(posicao=True), "fora da ordem"),
            (lambda m: m.update(posicao=2.0), "fora da ordem"),
            (lambda m: m.update(modelo=" "), "sem nome"),
            (lambda m: m.update(modelo="modelo-001"), "repetido"),
            (lambda m: m.update(organizacao=None), "organização"),
            (lambda m: m.update(licenca="WTFPL"), "licença desconhecida"),
            (lambda m: m.update(licenca=["MIT"]), "licença desconhecida"),
            (lambda m: m.update(licenca={"MIT": 1}), "licença desconhecida"),
            (lambda m: m.update(pontuacao="1500"), "não numérico"),
            (lambda m: m.update(superior=float("nan")), "não numérico"),
            (lambda m: m.update(inferior=float("inf")), "não numérico"),
            (lambda m: m.update(pontuacao=True), "não numérico"),
            (lambda m: m.update(inferior=m["pontuacao"] + 1), "fora do intervalo"),
            (lambda m: m.update(superior=m["pontuacao"] - 1), "fora do intervalo"),
            (lambda m: m.update(pontuacao=1700.0, superior=1705.0), "maior que a da posição anterior"),
            (lambda m: m.update(votos=10.0), "votos"),
            (lambda m: m.update(votos=-1), "votos"),
            (lambda m: m.update(votos=True), "votos"),
            (lambda m: m.pop("votos"), "campos"),
            (lambda m: m.update(extra=1), "campos"),
        ]
        for muda, trecho in casos:
            s = snapshot()
            muda(s["modelos"][1])
            with self.subTest(trecho=trecho):
                self.bad(s, trecho)

    def test_rejects_bad_categories(self):
        s = snapshot(); del s["categorias"]["coding"]
        self.bad(s, "categorias")
        s = snapshot(); s["categorias"]["outra"] = []
        self.bad(s, "categorias")
        s = snapshot(); s["categorias"]["math"] = []
        self.bad(s, "categorias.math")
        s = snapshot(); s["categorias"]["math"].append(modelo(4, nome="m4"))
        self.bad(s, "mais de 3")
        s = snapshot(); s["categorias"]["math"][0]["licenca"] = "WTFPL"
        self.bad(s, "licença desconhecida")

    def test_rejects_bad_previous(self):
        s = snapshot(); s["anterior"]["publicacao"] = "2026-11-16"
        self.bad(s, "a menos de 7 dias")
        s = snapshot(); s["anterior"]["publicacao"] = "2026-11-15"
        self.ok(s)
        s = snapshot(); s["anterior"] = {"publicacao": "2026-11-11"}
        self.bad(s, "anterior")
        s = snapshot(); s["anterior"]["modelos"] = []
        self.bad(s, "anterior.modelos")
        s = snapshot(); s["anterior"]["modelos"][0]["licenca"] = "x"
        self.bad(s, "licença desconhecida")

    def test_every_license_is_classified_once(self):
        self.assertEqual(B.FECHADAS & B.ABERTAS, set())
        self.assertEqual(B.SEM_INFORMACAO & B.ABERTAS, set())
        self.assertEqual(B.FECHADAS & B.SEM_INFORMACAO, set())
        self.assertFalse(B.aberto({"licenca": "Other"}))
        self.assertFalse(B.aberto({"licenca": ""}))
        self.assertFalse(B.aberto({"licenca": "Proprietary"}))
        self.assertTrue(B.aberto({"licenca": "MIT"}))


class CalculosTest(unittest.TestCase):
    def test_faixas(self):
        ms = [modelo(1, 1530, 10), modelo(2, 1520, 5), modelo(3, 1500, 3), modelo(4, 1499, 3)]
        # 1: [1520,1540]; 2: [1515,1525]; 3: [1497,1503]; 4: [1496,1502]
        self.assertEqual(B.faixas(ms), [(1, 2), (1, 2), (3, 4), (3, 4)])

    def test_faixa_touching_bounds_is_overlap_for_both(self):
        ms = [modelo(1, 1510, 5), modelo(2, 1500, 5)]  # [1505,1515] e [1495,1505]: superior de 2 == inferior de 1
        self.assertEqual(B.faixas(ms), [(1, 2), (1, 2)])
        ms = [modelo(1, 1510, 4), modelo(2, 1500, 5)]  # [1506,1514] e [1495,1505]: não encostam
        self.assertEqual(B.faixas(ms), [(1, 1), (2, 2)])

    def test_margem_do_lider(self):
        ms = [modelo(1, 1530, 10), modelo(2, 1520, 5), modelo(3, 1500, 3)]
        self.assertEqual(B.na_margem_do_lider(ms), 1)
        self.assertEqual(B.na_margem_do_lider([modelo(1, 1600, 1), modelo(2, 1500, 1)]), 0)

    def test_sobrepoe_is_closed(self):
        a, b = modelo(1, 1510, 5), modelo(2, 1500, 5)
        self.assertTrue(B.sobrepoe(a, b) and B.sobrepoe(b, a))
        self.assertFalse(B.sobrepoe(modelo(1, 1510, 4), b))

    def test_organizacoes(self):
        ms = [modelo(1, org="a"), modelo(2, org="b"), modelo(3, org="a"), modelo(4, org="c")]
        self.assertEqual([(o, m["posicao"], n) for o, m, n in B.organizacoes(ms)], [("a", 1, 2), ("b", 2, 1), ("c", 4, 1)])
        many = [modelo(i, org=f"o{i}") for i in range(1, 15)]
        self.assertEqual(len(B.organizacoes(many)), B.TOP_ORGANIZACOES)

    def test_organizacoes_count_only_top_100(self):
        ms = [modelo(i, org="a" if i in (1, 150) else "b") for i in range(1, 160)]
        self.assertEqual({o: n for o, _, n in B.organizacoes(ms)}, {"a": 1, "b": 99})

    def test_rounding_and_thousands(self):
        self.assertEqual(B._n(1526.5), "1.527")
        self.assertEqual(B._n(1526.49), "1.526")
        self.assertEqual(B._n(2.5), "3")
        self.assertEqual(B._n(28856), "28.856")
        self.assertEqual(B._n(84109.0), "84.109")


class MudancasTest(unittest.TestCase):
    def test_nothing_changed(self):
        self.assertEqual(B.mudancas(lista(120), lista(120)), [])

    def test_new_leader_with_and_without_margin(self):
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[1]["pontuacao"], agora[1]["inferior"], agora[1]["superior"] = 1601.0, 1596.0, 1606.0
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertEqual(itens[0], "Novo líder: modelo-002, no lugar de modelo-001 (dentro da margem de erro).")
        agora = copy.deepcopy(antes)
        agora[1]["pontuacao"], agora[1]["inferior"], agora[1]["superior"] = 1650.0, 1645.0, 1655.0
        agora = renum(agora)
        self.assertEqual(B.mudancas(agora, antes)[0], "Novo líder: modelo-002, no lugar de modelo-001.")

    def test_leader_gone_from_the_list(self):
        antes = lista(120)
        agora = renum(copy.deepcopy(antes[1:]))
        self.assertEqual(B.mudancas(agora, antes)[0], "Novo líder: modelo-002, no lugar de modelo-001, que saiu da lista.")

    def test_top10_in_and_out(self):
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[29].update(pontuacao=1585.0, inferior=1584.0, superior=1586.0)  # modelo-030 sobe para o 8º lugar
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertIn("Entrou no top 10: modelo-030, 8ª posição (vindo da 30ª posição).", itens)
        self.assertIn("Saiu do top 10: modelo-010 (agora na 11ª posição).", itens)

    def test_new_and_removed_models_only_inside_top_100(self):
        antes = lista(150)
        agora = copy.deepcopy(antes)
        agora = [m for m in agora if m["modelo"] not in ("modelo-050", "modelo-140")]
        agora.append(modelo(0, rating=1499.0, nome="novo-dentro"))
        agora.append(modelo(0, rating=1300.5, nome="novo-fora"))
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertTrue(any(t.startswith("Modelo novo: novo-dentro,") for t in itens))
        self.assertFalse(any("novo-fora" in t for t in itens))
        self.assertIn("Saiu da lista: modelo-050, que estava na 50ª posição.", itens)
        self.assertFalse(any("modelo-140" in t for t in itens))

    def test_new_leader_from_outside_top10_is_said_once(self):
        # r1 (Claude P3): o novo líder vindo de fora do top 10 não entra de novo como "Entrou no top 10".
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[29].update(pontuacao=1650.0, inferior=1645.0, superior=1655.0)
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertEqual(itens[0], "Novo líder: modelo-030, no lugar de modelo-001.")
        self.assertFalse(any(t.startswith("Entrou no top 10: modelo-030") for t in itens), itens)
        # A subida além da margem é outro evento (o tamanho do salto) e continua no bloco.
        self.assertIn("Subiu além da margem de erro: modelo-030, de 1.540 para 1.650 pontos (30ª para 1ª posição).", itens)

    def test_old_leader_gone_from_the_list_is_said_once(self):
        # r2 (Claude P4): o ex-líder que sai da lista entra uma vez, no item do novo líder.
        antes = lista(120)
        agora = renum([m for m in copy.deepcopy(antes) if m["modelo"] != "modelo-001"])
        itens = [t for t in B.mudancas(agora, antes) if "modelo-001" in t]
        self.assertEqual(itens, ["Novo líder: modelo-002, no lugar de modelo-001, que saiu da lista."])

    def test_old_leader_out_of_top10_but_still_listed(self):
        # r1 do #144 (agy P3): o ex-líder que cai para a 15ª posição continua na lista; a frase não diz que saiu.
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[0].update(pontuacao=1569.0, inferior=1564.0, superior=1574.0)
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertEqual(itens[0], "Novo líder: modelo-002, no lugar de modelo-001.")
        self.assertIn("Saiu do top 10: modelo-001 (agora na 15ª posição).", itens)
        self.assertFalse(any("que saiu da lista" in t for t in itens), itens)

    def test_top10_model_gone_from_the_list_is_said_once(self):
        # r1 (agy P1, Claude P3): quem sai do top 10 direto para fora da lista aparece uma vez só.
        antes = lista(120)
        agora = renum([m for m in copy.deepcopy(antes) if m["modelo"] != "modelo-004"])
        itens = [t for t in B.mudancas(agora, antes) if "modelo-004" in t]
        self.assertEqual(itens, ["Saiu do top 10: modelo-004 (saiu da lista)."])

    def test_new_model_straight_into_top10_is_said_once(self):
        antes = lista(120)
        agora = renum(copy.deepcopy(antes) + [modelo(0, rating=1597.0, meia=0.5, nome="estreante")])
        itens = [t for t in B.mudancas(agora, antes) if "estreante" in t]
        self.assertEqual(itens, ["Entrou no top 10: estreante, 2ª posição (estreia na lista)."])

    def test_moves_only_beyond_the_margin(self):
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[59].update(pontuacao=1483.0, inferior=1478.0, superior=1488.0)  # [1478,1488] vs [1475,1485]: sobrepõe
        agora[79].update(pontuacao=1430.0, inferior=1425.0, superior=1435.0)  # [1425,1435] vs [1435,1445]: encosta
        agora[89].update(pontuacao=1404.0, inferior=1400.0, superior=1408.0)  # [1400,1408] vs [1415,1425]: caiu
        agora[39].update(pontuacao=1540.0, inferior=1535.0, superior=1545.0)  # [1535,1545] vs [1515,1525]: subiu
        agora = renum(agora)
        itens = B.mudancas(agora, antes)
        self.assertFalse(any("modelo-060" in t for t in itens))
        self.assertFalse(any("modelo-080" in t for t in itens))
        self.assertTrue(any(t.startswith("Caiu além da margem de erro: modelo-090, de 1.420 para 1.404 pontos (90ª para")
                            for t in itens), itens)
        self.assertTrue(any(t.startswith("Subiu além da margem de erro: modelo-040, de 1.520 para 1.540 pontos (40ª para")
                            for t in itens), itens)

    def test_moves_outside_top_100_are_ignored(self):
        antes = lista(150)
        agora = copy.deepcopy(antes)
        agora[129].update(pontuacao=1345.0, inferior=1344.0, superior=1346.0)  # 130º, sobe muito, mas fora do top 100
        agora = renum(agora)
        self.assertFalse(any("modelo-130" in t for t in B.mudancas(agora, antes)))

    def test_at_most_eight_in_priority(self):
        antes = lista(150)
        agora = renum([m for m in copy.deepcopy(antes) if m["posicao"] not in range(20, 40)]
                      + [modelo(0, rating=1555.5 - k, meia=0.4, nome=f"n{k}") for k in range(20)])
        itens = B.mudancas(agora, antes)
        self.assertEqual(len(itens), B.MAX_MUDANCAS)
        self.assertTrue(all(t.startswith("Modelo novo") for t in itens))

    def test_names_are_escaped(self):
        antes = lista(120)
        agora = copy.deepcopy(antes)
        agora[0]["modelo"] = "<b>x</b>"
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", B.mudancas(agora, antes)[0])


class BlocosTest(unittest.TestCase):
    def test_table_has_top_rows_and_seven_cells(self):
        t = B.bloco_tabela(lista(130))
        rows = re.findall(r"<tr><td.*?</tr>", t)
        self.assertEqual(len(rows), B.TOP)
        self.assertTrue(all(r.count("<td") == 7 for r in rows))
        self.assertIn("Os 100 primeiros de 130 modelos", t)
        self.assertIn('<td data-valor="1598.0">1.598 <span class="ic">(1.593 a 1.603)</span></td>', t)

    def test_table_cells(self):
        ms = lista(100)
        ms[0].update(modelo="A&B <i>", organizacao="", licenca="Other", votos=28856)
        t = B.bloco_tabela(ms)
        first = re.search(r"<tr><td.*?</tr>", t).group(0)
        self.assertEqual(first, '<tr><td data-valor="1">1</td><td data-valor="1">1 a 6</td><td>A&amp;B &lt;i&gt;</td>'
                                '<td>não informada</td><td data-valor="1598.0">1.598 <span class="ic">(1.593 a 1.603)</span>'
                                '</td><td data-valor="28856">28.856</td><td>Outra</td></tr>')

    def test_organization_is_escaped_everywhere(self):
        # r1 (Claude P2): a organização é texto livre da fonte e aparece em quatro blocos.
        ms = lista(100)
        ms[0].update(organizacao="A&B <i>", licenca="MIT")
        esperado = "A&amp;B &lt;i&gt;"
        self.assertIn(f"<td>{esperado}</td>", B.bloco_tabela(ms))
        self.assertIn(f"da organização {esperado},", B.bloco_destaques(ms, date(2026, 11, 22)))
        self.assertIn(f"<td>{esperado}</td>", B.bloco_abertos(ms))
        self.assertIn(f"<tr><td>{esperado}</td>", B.bloco_organizacoes(ms))
        for b in (B.bloco_tabela(ms), B.bloco_destaques(ms, date(2026, 11, 22)), B.bloco_abertos(ms),
                  B.bloco_organizacoes(ms)):
            self.assertNotIn("<i>", b)

    def test_blank_organization(self):
        # r1 (agy P2): organização vazia ou em branco não vira célula vazia nem linha "não informada" no recorte.
        self.assertEqual(B._org({"organizacao": "  "}), "não informada")
        self.assertEqual(B._org({"organizacao": " google "}), "google")
        ms = [modelo(1, org=""), modelo(2, org="  "), modelo(3, org="a"), modelo(4, org=" a ")]
        self.assertEqual([(o, m["posicao"], n) for o, m, n in B.organizacoes(ms)], [("a", 3, 2)])

    def test_faixa_cell(self):
        ms = [modelo(1, 1530, 10), modelo(2, 1520, 5)] + [modelo(i, 1400 - i) for i in range(3, 101)]
        t = B.bloco_tabela(ms)
        self.assertIn('<td data-valor="1">1 a 2</td>', t)

    def test_licenses(self):
        self.assertEqual(B._lic({"licenca": "Proprietary"}), "Proprietária")
        self.assertEqual(B._lic({"licenca": "Other"}), "Outra")
        self.assertEqual(B._lic({"licenca": ""}), "Não informada")
        self.assertEqual(B._lic({"licenca": "Llama 4"}), "Llama 4")

    def test_destaques(self):
        ms = [modelo(1, 1530, 10, lic="Proprietary", org="google"), modelo(2, 1520, 5, lic="Proprietary"),
              modelo(3, 1500, 3, lic="MIT", org="deepseek")]
        d = B.bloco_destaques(ms, date(2026, 11, 22))
        self.assertIn("<strong>Líder:</strong> modelo-001, da organização google, com 1.530 pontos; 1 modelo está dentro da "
                      "margem de erro dele.", d)
        self.assertIn("<strong>Melhor de pesos abertos:</strong> modelo-003, da organização deepseek, na 3ª posição "
                      "(licença: MIT).", d)
        self.assertIn("<strong>Dados:</strong> publicação da Arena de 22 de novembro de 2026.", d)
        d = B.bloco_destaques([modelo(1, 1600, 1, lic="Proprietary"), modelo(2, 1500, 1, lic="Other")], date(2026, 11, 22))
        self.assertIn("nenhum outro modelo está dentro da margem de erro dele", d)
        self.assertNotIn("pesos abertos", d)
        d = B.bloco_destaques([modelo(1, 1600, 50)] + [modelo(i, 1600 - i) for i in range(2, 5)], date(2026, 11, 22))
        self.assertIn("3 modelos estão dentro da margem de erro dele", d)

    def test_mudancas_block(self):
        s = snapshot()
        self.assertEqual(B.bloco_mudancas(s), "")
        s["anterior"] = None
        self.assertEqual(B.bloco_mudancas(s), "")
        s = snapshot()
        s["modelos"] = renum(s["modelos"] + [modelo(0, rating=1597.0, meia=0.2, nome="novo")])
        b = B.bloco_mudancas(s)
        self.assertIn('<section id="o-que-mudou"', b)
        self.assertIn("Publicação de 22/11/2026 comparada com a de 11/11/2026.", b)
        self.assertIn("<li>Entrou no top 10: novo, 2ª posição (estreia na lista).</li>", b)

    def test_abertos(self):
        ms = [modelo(i, lic="Proprietary" if i % 3 else "MIT") for i in range(1, 40)]
        b = B.bloco_abertos(ms)
        self.assertEqual(len(re.findall(r"<tr><td>", b)), B.TOP_ABERTOS)
        self.assertIn("<tr><td>3</td><td>modelo-003</td>", b)
        self.assertIn("Nenhum modelo de pesos abertos", B.bloco_abertos([modelo(1, lic="Proprietary")]))

    def test_categorias(self):
        cats = snapshot()["categorias"]
        cats["math"] = cats["math"][:2]
        b = B.bloco_categorias(cats, B.TAREFAS, "Tipo de tarefa")
        self.assertEqual(b.count("<tr><th scope=\"row\">"), len(B.TAREFAS))
        self.assertIn('<tr><th scope="row">Matemática</th><td>math-1 <span class="ic">(1.598)</span></td>'
                      '<td>math-2 <span class="ic">(1.596)</span></td><td>&nbsp;</td></tr>', b)
        self.assertIn("a fonte não tem recorte de português", b)
        self.assertEqual(B.bloco_categorias(cats, B.SETORES, "Setor").count("<tr><th scope=\"row\">"), len(B.SETORES))

    def test_categories_cover_the_two_blocks_exactly(self):
        self.assertEqual(set(B.CATEGORIAS), set(B.TAREFAS) | set(B.SETORES))
        self.assertEqual(len(B.TAREFAS), 9)
        self.assertEqual(len(B.SETORES), 8)

    def test_jsonld(self):
        j = B.bloco_jsonld(date(2026, 11, 22))
        doc = json.loads(re.fullmatch(r'<script type="application/ld\+json">(.*)</script>', j).group(1))
        web = doc["@graph"][0]
        self.assertEqual((web["@type"], web["dateModified"], web["url"]), ("WebPage", "2026-11-22", B.URL))
        self.assertEqual(web["isBasedOn"]["url"], B.DATASET_URL)
        self.assertEqual(doc["@graph"][1]["@type"], "BreadcrumbList")
        self.assertNotIn("Dataset", [n["@type"] for n in doc["@graph"]])

    def test_jsonld_escapes_lt(self):
        orig = B.URL
        try:
            B.URL = "https://guifav.dev/</script>"
            j = B.bloco_jsonld(date(2026, 11, 22))
            inner = j[len('<script type="application/ld+json">'):-len("</script>")]
            self.assertNotIn("<", inner)
            self.assertIn("https://guifav.dev/\\u003c/script>", inner)
            self.assertEqual(json.loads(inner)["@graph"][0]["url"], "https://guifav.dev/</script>")
        finally:
            B.URL = orig

    def test_atribuicao(self):
        a = B.bloco_atribuicao(date(2026, 11, 22))
        self.assertIn(f'<a href="{B.DATASET_URL}">lmarena-ai/leaderboard-dataset</a>', a)
        self.assertIn('rel="license">CC BY 4.0</a>', a)
        self.assertIn("publicação de 22/11/2026", a)
        self.assertIn("A Arena não revisou nem endossa esta página.", a)

    def test_meta(self):
        self.assertEqual(B.bloco_meta(date(2026, 11, 2)), '<meta name="dados-publicados-em" content="2026-11-02">')


class RenderTest(unittest.TestCase):
    def template(self) -> str:
        return "".join(f"<!--LLM:{k}-->" for k in B.blocos(snapshot()))

    def test_every_marker_is_replaced(self):
        out = B.render(self.template(), snapshot())
        self.assertNotIn("<!--LLM:", out)

    def test_missing_duplicate_or_unknown_marker(self):
        t = self.template()
        for bad, trecho in ((t.replace("<!--LLM:tabela-->", ""), "tabela"), (t + "<!--LLM:tabela-->", "tabela"),
                            (t + "<!--LLM:outro-->", "outro")):
            with self.subTest(trecho=trecho):
                with self.assertRaises(B.RankingError) as cm:
                    B.render(bad, snapshot())
                self.assertIn(trecho, str(cm.exception))

    def test_real_template_has_each_marker_once(self):
        t = (REPO / B.PAGE).read_text(encoding="utf-8")
        self.assertEqual(sorted(B.MARKER.findall(t)), sorted(B.blocos(snapshot())))


class BuildTest(unittest.TestCase):
    """O template real do repo, montado num _site temporário, gerado e conferido pela regra do check_site."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.site = self.dir / "_site"
        (self.site / "llm-ranking").mkdir(parents=True)
        shutil.copy(REPO / B.PAGE, self.site / B.PAGE)
        self.snap = self.dir / "arena-texto.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, snap) -> None:
        self.snap.write_text(json.dumps(snap), encoding="utf-8")

    def test_build_and_check(self):
        self.write(snapshot())
        self.assertEqual(B.build(self.snap, self.site, HOJE), (100, "2026-11-22"))
        html = (self.site / B.PAGE).read_text(encoding="utf-8")
        self.assertEqual(C.check_llm_ranking(self.site), [])
        self.assertIn('<meta name="dados-publicados-em" content="2026-11-22">', html)
        self.assertIn(f'<meta name="dados-versao" content="{B.versao(snapshot())}">', html)
        self.assertIn("Dados de 22/11/2026;", html)
        self.assertIn("com os 120 modelos", html)
        self.assertEqual(C.check_titles_and_groups(B.PAGE, html), [])
        self.assertEqual(C.check_consent(B.PAGE, html), [])
        self.assertEqual(C.check_ga_host_guard(B.PAGE, html), [])
        self.assertEqual(C.check_tracking(B.PAGE, html), [])

    def test_version_is_the_hash_of_the_canonical_snapshot(self):
        # O snapshot da KVM grava B.serializar(snap); a versão é o sha256 desses bytes, então é a mesma dos dois lados
        # mesmo que o arquivo lido pelo build tenha outra formatação.
        s = snapshot()
        texto = B.serializar(s)
        self.assertEqual(B.versao(s), hashlib.sha256(texto.encode("utf-8")).hexdigest())
        self.assertEqual(B.versao(json.loads(json.dumps(s))), B.versao(s))
        t = copy.deepcopy(s); t["modelos"][5]["votos"] += 1
        self.assertNotEqual(B.versao(t), B.versao(s))
        self.assertEqual(t["publicacao"], s["publicacao"])
        self.assertTrue(texto.endswith("}\n"))

    def test_template_without_build_fails_the_check(self):
        self.assertTrue(any("marcador do template" in p for p in C.check_llm_ranking(self.site)))

    def test_invalid_snapshot_leaves_the_page_untouched(self):
        before = (self.site / B.PAGE).read_bytes()
        s = snapshot(); s["modelos"][0]["licenca"] = "nova"
        self.write(s)
        with self.assertRaises(B.RankingError):
            B.build(self.snap, self.site, HOJE)
        self.assertEqual((self.site / B.PAGE).read_bytes(), before)

    def test_main_errors(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(B.main(["--snapshot", str(self.dir / "nada.json"), "--site", str(self.site)]), 1)
        self.assertIn("checkout do guifav/llm-ranking-data falhou", err.getvalue())
        self.snap.write_text("{", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(B.main(["--snapshot", str(self.snap), "--site", str(self.site)]), 1)
        self.write(snapshot())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(B.main(["--snapshot", str(self.snap), "--site", str(self.dir / "sem-site")]), 1)

    def test_main_ok(self):
        self.write(snapshot())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(B.main(["--snapshot", str(self.snap), "--site", str(self.site)]), 0)
        self.assertIn("100 modelos na tabela, dados de 2026-11-22", out.getvalue())


SORTER_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
function el(text, attrs) {
  attrs = Object.assign({}, attrs || {});
  return {textContent: text, children: [], listeners: {},
    getAttribute: k => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); },
    removeAttribute: k => { delete attrs[k]; }, appendChild(c) { this.children.push(c); },
    addEventListener(t, f) { this.listeners[t] = f; }, attrs};
}
const linhas = [[1, 1, 'beta', 10], [2, 1, 'Alfa', 30], [3, 3, 'gama', 30], [4, 2, 'ágata', 5]].map(([p, f, n, v]) => {
  const r = {cells: [el(String(p), {'data-valor': String(p)}), el(f + ' a 9', {'data-valor': String(f)}), el(n),
    el(String(v), {'data-valor': String(v)})]};
  r.id = p; return r;
});
const tbody = {rows: linhas.slice(), appendChild(r) { this.rows.splice(this.rows.indexOf(r), 1); this.rows.push(r); }};
const ths = [el('Posição', {'data-ordem': 'num'}), el('Faixa', {'data-ordem': 'num'}), el('Modelo', {'data-ordem': 'texto'}),
  el('Votos', {'data-ordem': 'num'})];
const tabela = {tBodies: [tbody], tHead: {rows: [{cells: ths}]}};
const document = {getElementById: id => (id === 'ranking' ? tabela : null), createElement: () => el('')};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document});
const out = [{ordem: tbody.rows.map(r => r.id), aria: ths.map(t => t.getAttribute('aria-sort'))}];
for (const i of JSON.parse(process.argv[2])) {
  ths[i].children[0].listeners.click();
  out.push({ordem: tbody.rows.map(r => r.id), aria: ths.map(t => t.getAttribute('aria-sort'))});
}
out.push({botoes: ths.map(t => t.children.length && t.children[0].textContent + '|' + t.children[0].type)});
console.log(JSON.stringify(out));
"""


class SorterTest(unittest.TestCase):
    """O llm-ranking/ordenar.js num DOM mínimo, no node (r1, agy P2: o único JavaScript da página não tinha teste)."""

    def run_clicks(self, clicks: list[int]):
        node = shutil.which("node")
        if not node:
            self.skipTest("node ausente")
        r = subprocess.run([node, "-e", SORTER_HARNESS, str(REPO / "llm-ranking/ordenar.js"), json.dumps(clicks)],
                           capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def test_sorting(self):
        out = self.run_clicks([3, 3, 2, 2, 0, 0])
        A, D, N = "ascending", "descending", None
        self.assertEqual(out[0], {"ordem": [1, 2, 3, 4], "aria": [A, N, N, N]})
        # Votos: primeiro clique do maior para o menor; empate (30 e 30) desfeito pela posição da fonte.
        self.assertEqual(out[1], {"ordem": [2, 3, 1, 4], "aria": [N, N, N, D]})
        self.assertEqual(out[2], {"ordem": [4, 1, 2, 3], "aria": [N, N, N, A]})
        # Modelo: texto em português, de A a Z, sem diferença de caixa nem acento.
        self.assertEqual(out[3], {"ordem": [4, 2, 1, 3], "aria": [N, N, A, N]})
        self.assertEqual(out[4], {"ordem": [3, 1, 2, 4], "aria": [N, N, D, N]})
        # Posição: primeiro clique em ordem crescente.
        self.assertEqual(out[5], {"ordem": [1, 2, 3, 4], "aria": [A, N, N, N]})
        self.assertEqual(out[6], {"ordem": [4, 3, 2, 1], "aria": [D, N, N, N]})
        self.assertEqual(out[7], {"botoes": ["Posição|button", "Faixa|button", "Modelo|button", "Votos|button"]})

    def test_range_sorts_ascending_first(self):
        # r2 (agy P3): a Faixa é numérica e, como a posição, começa da melhor (menor) para a pior.
        out = self.run_clicks([1, 1])
        self.assertEqual(out[1], {"ordem": [1, 2, 4, 3], "aria": [None, "ascending", None, None]})
        self.assertEqual(out[2], {"ordem": [3, 4, 1, 2], "aria": [None, "descending", None, None]})

    def test_tie_is_broken_by_source_position_not_by_current_order(self):
        # Modelo de Z a A deixa gama (3) antes de Alfa (2); empatados em votos, voltam à ordem da fonte (2, 3).
        out = self.run_clicks([2, 2, 3])
        self.assertEqual(out[2]["ordem"], [3, 1, 2, 4])
        self.assertEqual(out[3], {"ordem": [2, 3, 1, 4], "aria": [None, None, None, "descending"]})


class TemplateTest(unittest.TestCase):
    """O template: navegação do site sem o botão PT/EN, sem os arquivos da versão antiga, com o ordenar.js."""

    def setUp(self):
        self.t = (REPO / B.PAGE).read_text(encoding="utf-8")

    def test_no_lang_toggle_and_site_theme(self):
        self.assertNotIn('id="lang-toggle"', self.t)
        self.assertIn('id="theme-toggle"', self.t)
        self.assertIn('/assets/site.js?v=', self.t)

    def test_no_old_files(self):
        for old in ("script.js", "styles.css", ".csv", "chart.js", "Chart.js"):
            self.assertNotIn(old, self.t)
        left = sorted(p.name for p in (REPO / "llm-ranking").iterdir())
        self.assertEqual(left, ["index.html", "ordenar.js"])

    def test_explanation_of_the_range_is_true(self):
        # r1 (agy e Claude P1): faixas que se cruzam não querem dizer intervalos que se sobrepõem.
        self.assertNotIn("faixas que se cruzam estão dentro da margem", self.t)
        self.assertIn("A faixa vai da melhor à pior posição compatível com os intervalos de 95%", self.t)
        self.assertIn("quando os intervalos da coluna Pontuação se cruzam", self.t)

    def test_links_to_the_two_tools(self):
        self.assertIn('href="/mapa-decisao-ia/"', self.t)
        self.assertIn('href="/planilha-custos-ia/"', self.t)

    def test_sorter_is_loaded_once(self):
        self.assertEqual(self.t.count('src="/llm-ranking/ordenar.js'), 1)

    def test_site_js_tolerates_a_page_without_the_lang_toggle(self):
        js = (REPO / "assets/site.js").read_text(encoding="utf-8")
        self.assertIn("if (lt) {", js)
        guarded = js.split("if (lt) {", 1)[1]
        self.assertIn("lt.addEventListener", guarded)
        self.assertNotIn("lt.addEventListener", js.split("if (lt) {", 1)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
