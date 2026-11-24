"""Testes do gerador do ciclo da home (scripts/build_home_cycle.py)."""
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_home_cycle as bhc

HOME = Path(__file__).resolve().parent.parent / 'index.html'


class BuildHomeCycleTest(unittest.TestCase):
    def setUp(self):
        self.html = HOME.read_text(encoding='utf-8')

    def test_home_em_dia_com_o_gerador(self):
        self.assertEqual(bhc.render(self.html), self.html)

    def test_idempotente(self):
        once = bhc.render(self.html)
        self.assertEqual(bhc.render(once), once)

    def test_detecta_svg_editado_a_mao(self):
        edited = self.html.replace('data-i18n="cy_dados_t">Dados<', 'data-i18n="cy_dados_t">Dado<', 1)
        self.assertNotEqual(edited, self.html)
        self.assertEqual(bhc.render(edited), self.html)

    def test_chaves_cy_tem_par_em_ingles(self):
        used = set(re.findall(r'data-i18n="(cy_[^"]+)"', self.html))
        en = set(re.findall(r'"(cy_[^"]+)":', self.html))
        self.assertIn('cy_desc', used)
        self.assertEqual(used, en)

    def test_svg_descrito_para_leitor_de_tela(self):
        for v in ('d', 'm'):
            self.assertIn(f'aria-labelledby="cy-t-{v}" aria-describedby="cy-x-{v}"', self.html)
            self.assertIn(f'<desc id="cy-x-{v}" data-i18n="cy_desc">', self.html)

    def test_falha_sem_cada_trecho(self):
        quebras = (('<figure class="dflow">', '<figure class="outra">'),
                   ('    .dflow{margin:12px 0 32px}', '    .outro{margin:12px 0 32px}'),
                   ('id="i18n-en"', 'id="i18n-xx"'))
        for antes, depois in quebras:
            with self.subTest(trecho=antes), self.assertRaises(SystemExit):
                bhc.render(self.html.replace(antes, depois, 1))

    def test_falha_com_chave_en_repetida_e_diferente(self):
        bhc.EN.clear()
        bhc.en('cy_x', 'one')
        bhc.en('cy_x', 'one')
        with self.assertRaises(SystemExit):
            bhc.en('cy_x', 'two')
        bhc.EN.clear()

    def test_icone_desconhecido_falha(self):
        with self.assertRaises(ValueError):
            bhc.icon('outro', 0, 0)

    def test_svg_sem_script_nem_href_externo(self):
        figure = re.search(r'<figure class="dflow">.*?</figure>', self.html, re.S).group(0)
        self.assertNotIn('<script', figure)
        svgs = re.findall(r'<svg class="cy-[dm]".*?</svg>', figure, re.S)
        self.assertEqual(len(svgs), 2)
        for svg in svgs:
            self.assertEqual(re.findall(r'href="(?!#)', svg), [])
        self.assertEqual(re.findall(r'href="(?!#)([^"]*)"', figure), [bhc.GIF])

    def test_animacoes_tem_keyframes_no_trecho_gerado(self):
        usados = set(re.findall(r'animation:([a-z-]+) ', bhc.CSS))
        definidos = set(re.findall(r'@keyframes ([a-z-]+)', bhc.CSS))
        self.assertTrue(usados)
        self.assertLessEqual(usados, definidos)

    def test_cli_check_e_escrita(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'index.html'
            p.write_text(self.html, encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(bhc.main(['--check'], p), 0)
                dirty = self.html.replace('>Dados<', '>Dado<', 1)
                p.write_text(dirty, encoding='utf-8')
                self.assertEqual(bhc.main(['--check'], p), 1)
                self.assertEqual(p.read_text(encoding='utf-8'), dirty)  # --check não escreve
                self.assertEqual(bhc.main([], p), 0)                     # sem flag, reescreve
            self.assertIn('difere do gerador', err.getvalue())
            self.assertEqual(p.read_text(encoding='utf-8'), self.html)

    def test_cli_recusa_argumento_desconhecido(self):
        # HTML válido e desatualizado: só o argparse impede a escrita (o render sozinho daria certo)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'index.html'
            dirty = self.html.replace('>Dados<', '>Dado<', 1)
            p.write_text(dirty, encoding='utf-8')
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                bhc.main(['--chek'], p)
            self.assertEqual(cm.exception.code, 2)
            self.assertIn('--chek', err.getvalue())
            self.assertEqual(p.read_text(encoding='utf-8'), dirty)

    def test_gif_em_dia_com_o_desenho(self):
        meta = json.loads(bhc.GIF_META.read_text(encoding='utf-8'))
        self.assertEqual(meta['source_sha256'], bhc.gif_hash(),
                         'o desenho, o CSS do ciclo ou o tema claro mudaram: rode scripts/render_home_cycle_gif.mjs')
        gif = (bhc.ROOT / bhc.GIF.lstrip('/')).read_bytes()
        self.assertEqual(gif[:6], b'GIF89a')
        self.assertEqual(hashlib.sha256(gif).hexdigest(), meta['gif_sha256'], 'o GIF não é o registrado no json')
        self.assertEqual((int.from_bytes(gif[6:8], 'little'), int.from_bytes(gif[8:10], 'little')), (meta['width'], meta['height']))
        self.assertLess(len(gif), 1_000_000)
        self.assertEqual((meta['loop_seconds'], meta['fps'], meta['frames']), (bhc.LOOP, bhc.GIF_FPS, round(bhc.LOOP * bhc.GIF_FPS)))

    def test_hash_cobre_o_tema_claro(self):
        fonte = bhc.gif_source()
        self.assertIn(':root{--bg:', fonte)
        self.assertIn('body{font-family:', fonte)
        self.assertEqual(bhc.gif_source(), fonte)

    def test_hash_e_pagina_nao_mexem_no_dicionario(self):
        bhc.EN.clear()
        bhc.en('cy_x', 'one')
        bhc.gif_hash()
        bhc.gif_page()
        self.assertEqual(bhc.EN, {'cy_x': 'one'})
        bhc.EN.clear()

    def test_regra_de_largura_nao_pega_o_icone_do_botao(self):
        # o ícone do "Baixar GIF" é um <svg> dentro do .dflow; a regra de 100% vale só para os dois diagramas
        self.assertNotRegex(bhc.CSS, r'\.dflow svg\{')
        self.assertIn('.dflow-wrap > svg{display:block;width:100%', bhc.CSS)

    def test_cli_gif_page(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ciclo.html'
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(bhc.main(['--gif-page', str(p)], Path(d) / 'nao-usado.html'), 0)
            info = json.loads(out.getvalue())
            self.assertEqual(info['source_sha256'], bhc.gif_hash())
            self.assertEqual(info['loop_seconds'], bhc.LOOP)
            self.assertEqual(info['fps'], bhc.GIF_FPS)
            pagina = p.read_text(encoding='utf-8')
            self.assertIn('<svg class="cy-d"', pagina)
            self.assertIn('data-theme="light"', pagina)

    def test_botao_de_baixar_e_fonte(self):
        self.assertIn(f'<a class="dflow-dl" href="{bhc.GIF}" download="{bhc.GIF_NAME}" data-ga-download="{bhc.GIF_GA_DOWNLOAD}">', self.html)
        self.assertEqual(self.html.count('data-i18n="cy_src">Fonte: guifav.dev<'), 2)
        # no layout do celular o botão sai do canto e entra no fluxo, para não cobrir o desenho
        self.assertIn('@media(max-width:999px){.dflow-dl{position:static;', bhc.CSS)
        self.assertIn('.dflow-wrap:focus-within .dflow-dl', bhc.CSS)

    def test_animacoes_fecham_no_loop(self):
        def fecha(ciclo, onde):
            q = bhc.LOOP / ciclo
            self.assertGreaterEqual(round(q), 1, onde)
            self.assertAlmostEqual(q, round(q), msg=onde)
        # a classe é a última antes da chave (".dflow .i-vc-v{...}" -> i-vc-v)
        decls = re.findall(r'\.([a-z-]+)\{[^}]*?animation:([a-z-]+) ([0-9.]+)s([^;}]*)', bhc.CSS)
        self.assertEqual(len(decls), len(re.findall(r'animation:(?!none\b)', bhc.CSS)))
        alternadas = set()
        for cls, nome, dur, resto in decls:
            vai_e_volta = 'alternate' in resto
            if vai_e_volta:
                alternadas.add(cls)
            fecha(float(dur) * (2 if vai_e_volta else 1), f'{cls} {nome} {dur}s')
        svgs = bhc.desktop() + bhc.mobile()
        self.assertIn('i-vc-v', alternadas)
        inline = re.findall(r'class="([a-z-]+)" style="[^"]*animation-duration:([0-9.]+)s', svgs)
        self.assertTrue(inline)
        self.assertLessEqual({cls for cls, _ in inline}, {cls for cls, *_ in decls})
        for cls, dur in inline:
            fecha(float(dur) * (2 if cls in alternadas else 1), f'{cls} inline {dur}s')
        # atraso positivo deixa a animação parada no começo e cria emenda no GIF
        atrasos = [float(x) for x in re.findall(r'animation-delay:(-?[0-9.]+)s', svgs + bhc.CSS)]
        self.assertTrue(atrasos)
        self.assertTrue(all(x <= 0 for x in atrasos), [x for x in atrasos if x > 0])
        # SMIL: a duração fecha no LOOP e os pontos estão espaçados de LOOP
        for svg in (bhc.desktop(), bhc.mobile()):
            durs = {float(x) for x in re.findall(r'<animateMotion dur="([0-9.]+)s"', svg)}
            begins = sorted(float(x) for x in re.findall(r'<animateMotion dur="[0-9.]+s" begin="(-?[0-9.]+)s"', svg))
            # pontos iguais, um a cada LOOP: a volta inteira (dur) dura TOKENS * LOOP, e o desenho repete a cada LOOP
            self.assertEqual(len(durs), 1)
            self.assertAlmostEqual(durs.pop(), bhc.TOKENS * bhc.LOOP)
            self.assertEqual(len(begins), bhc.TOKENS)
            self.assertEqual(svg.count('<animateMotion'), bhc.TOKENS)
            self.assertTrue(all(b <= 0 for b in begins), begins)
            for a, b in zip(begins, begins[1:]):
                self.assertAlmostEqual(b - a, bhc.LOOP, places=2)

    def test_rotulos_do_celular_em_escala(self):
        self.assertIn('.dflow .cy-m .d-gov-t,.dflow .cy-m .d-fb-t{font-size:15px}', bhc.CSS)

    def test_trilho_na_sobra_de_arredondamento(self):
        t = bhc.rrect(150, 104, 850, 584, 150, (500, 584))
        (x, y), _ = t.at(t.length - 1e-9)
        self.assertAlmostEqual(x, 500, places=3)
        self.assertAlmostEqual(y, 584, places=3)

        class Inflado(bhc.Track):  # comprimento um pouco maior que a soma dos trechos: força a sobra
            @property
            def length(self):
                return sum(sg[-1] for sg in self.segs) + 1e-6
        u = Inflado((500, 584))
        u.segs, u.d = t.segs, t.d
        (x, y), _ = u.at(u.length - 1e-9)
        self.assertAlmostEqual(x, 500, places=3)
        self.assertAlmostEqual(y, 584, places=3)

if __name__ == '__main__':
    unittest.main()
