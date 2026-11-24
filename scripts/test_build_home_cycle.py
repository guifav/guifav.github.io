"""Testes do gerador do ciclo da home (scripts/build_home_cycle.py)."""
import contextlib
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
                         'o SVG ou o CSS do ciclo mudaram: rode scripts/render_home_cycle_gif.mjs')
        gif = bhc.ROOT / bhc.GIF.lstrip('/')
        self.assertEqual(gif.read_bytes()[:6], b'GIF89a')
        self.assertLess(gif.stat().st_size, 1_000_000)
        self.assertEqual(meta['loop_seconds'], bhc.LOOP)

    def test_botao_de_baixar_e_fonte(self):
        self.assertIn(f'<a class="dflow-dl" href="{bhc.GIF}" download="{bhc.GIF_NAME}">', self.html)
        self.assertEqual(self.html.count('data-i18n="cy_src">Fonte: guifav.dev<'), 2)

    def test_animacoes_fecham_no_loop(self):
        # cada duração (o dobro, nas alternadas) divide o LOOP: o GIF repete sem emenda
        for nome, dur in re.findall(r'animation:([a-z-]+) ([0-9.]+)s', bhc.CSS):
            ciclo = float(dur) * (2 if nome == 'i-swing' else 1)
            self.assertAlmostEqual(bhc.LOOP / ciclo, round(bhc.LOOP / ciclo), msg=f'{nome} {dur}s')
        for dur in re.findall(r'animation-duration:([0-9.]+)s', bhc.desktop()):
            self.assertAlmostEqual(bhc.LOOP / (2 * float(dur)), round(bhc.LOOP / (2 * float(dur))), msg=dur)

    def test_regra_de_largura_nao_pega_o_icone_do_botao(self):
        # o ícone do "Baixar GIF" é um <svg> dentro do .dflow; a regra de 100% vale só para os dois diagramas
        self.assertNotRegex(bhc.CSS, r'\.dflow svg\{')
        self.assertIn('.dflow-wrap > svg{display:block;width:100%', bhc.CSS)

    def test_rotulos_do_celular_em_escala(self):
        self.assertIn('.dflow .cy-m .d-gov-t,.dflow .cy-m .d-fb-t{font-size:15px}', bhc.CSS)

    def test_trilho_na_sobra_de_arredondamento(self):
        t = bhc.rrect(150, 104, 850, 584, 150, (500, 584))
        (x, y), _ = t.at(t.length - 1e-9)
        self.assertAlmostEqual(x, 500, places=3)
        self.assertAlmostEqual(y, 584, places=3)
        self.assertIsNotNone(t.at(sum(sg[-1] for sg in t.segs) + 1e-12))


if __name__ == '__main__':
    unittest.main()
