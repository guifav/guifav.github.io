"""Testes do gerador do ciclo da home (scripts/build_home_cycle.py)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_home_cycle as bhc

HOME = Path(__file__).resolve().parent.parent / 'index.html'


class BuildHomeCycleTest(unittest.TestCase):
    def setUp(self):
        self.html = HOME.read_text()

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
        self.assertTrue(used)
        self.assertEqual(used, en)

    def test_falha_sem_a_figura(self):
        with self.assertRaises(SystemExit):
            bhc.render(self.html.replace('<figure class="dflow">', '<figure class="outra">'))

    def test_svg_sem_script_nem_href_externo(self):
        figure = re.search(r'<figure class="dflow">.*?</figure>', self.html, re.S).group(0)
        self.assertNotIn('<script', figure)
        self.assertEqual(re.findall(r'href="(?!#)', figure), [])


if __name__ == '__main__':
    unittest.main()
