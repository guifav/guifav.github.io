"""Testes do gerador do ciclo da home (scripts/build_home_cycle.py)."""
import contextlib
import io
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
        self.assertEqual(re.findall(r'href="(?!#)', figure), [])

    def test_animacoes_tem_keyframes_no_trecho_gerado(self):
        usados = set(re.findall(r'animation:([a-z-]+) ', bhc.CSS))
        definidos = set(re.findall(r'@keyframes ([a-z-]+)', bhc.CSS))
        self.assertTrue(usados)
        self.assertLessEqual(usados, definidos)

    def test_cli_check(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'index.html'
            p.write_text(self.html, encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(bhc.main(['--check'], p), 0)
                p.write_text(self.html.replace('>Dados<', '>Dado<', 1), encoding='utf-8')
                self.assertEqual(bhc.main(['--check'], p), 1)
            self.assertIn('difere do gerador', err.getvalue())
            self.assertIn('>Dado<', p.read_text(encoding='utf-8'))  # --check não escreve

    def test_cli_recusa_argumento_desconhecido(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'index.html'
            p.write_text('x', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bhc.main(['--chek'], p)
            self.assertEqual(p.read_text(encoding='utf-8'), 'x')


if __name__ == '__main__':
    unittest.main()
