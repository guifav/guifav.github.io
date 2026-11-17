"""Testes de comportamento do .github/workflows/publicar.yml (#82, fatia 3). Rodar: python3 blog-src/scripts/test_publicar_workflow.py

Os passos run do YAML rodam de verdade, com o mesmo bash --noprofile --norc -eo pipefail do runner, contra um remoto
git bare num repositório mínimo, um gh falso e o D1 e o R2 falsos (a classe Cloudflare do draft_preview é trocada).
Cobre o que o teste de texto não pega: idempotência, recuperação de branch sem PR, recusas e o resumo da execução.
Precisa de bash 4+, git e jq (o runner do GitHub tem os três).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import draft_preview as D  # noqa: E402

WORKFLOW = D.REPO / ".github" / "workflows" / "publicar.yml"
SLUG = "teste-publicacao-automatica"
MD = '---\ntitle: "Teste de publicação"\ndescription: "x"\n---\ntexto\n'.encode()
IMGS = {"01-capa.webp": b"capa", "fig/02-g.png": b"g"}
ESPERADOS = sorted([f"blog-src/src/content/posts/{SLUG}.md", *[f"blog-src/public/img/{SLUG}/{k}" for k in IMGS]])


def passos(yml: str) -> list[dict]:
    """Passos do job publicar, lidos do texto do YAML: name, id, if, uses, env (mapa) e run (texto). O workflow segue a
    indentação de 2 espaços; os testes conferem que todos os passos esperados foram achados."""
    linhas = yml.split("\n")
    inicio = linhas.index("    steps:") + 1
    steps: list[dict] = []
    cur, bloco, mapa = None, None, None
    for ln in linhas[inicio:]:
        if ln and not ln.startswith("      "):
            break
        if ln.startswith("      - "):
            cur, bloco, mapa = {}, None, None
            steps.append(cur)
            ln = "        " + ln[8:]
        if cur is None:
            continue
        if bloco is not None:
            if ln.startswith("          ") or not ln.strip():
                cur[bloco].append(ln[10:])
                continue
            bloco = None
        if ln.startswith("        ") and not ln.startswith("         "):
            k, _, v = ln.strip().partition(":")
            v = v.strip()
            mapa = None
            if v == "|":
                cur[k], bloco = [], k
            elif v == "":
                cur[k], mapa = {}, k
            else:
                cur[k] = v
        elif mapa is not None and ln.startswith("          ") and ln.strip():
            k, _, v = ln.strip().partition(":")
            cur[mapa][k] = v.strip().strip('"')
    for st in steps:
        if isinstance(st.get("run"), list):
            st["run"] = "\n".join(st["run"]).strip("\n") + "\n"
    return steps


GH = r'''#!/usr/bin/env python3
"""gh falso: api .../pulls, pr create, run list e workflow run, sobre o estado em $SIM_STATE. Cada PR guardado tem
head, state (OPEN, MERGED ou CLOSED), base e owner (o dono do head; um fork tem outro dono)."""
import json, os, subprocess, sys
p = os.environ["SIM_STATE"]
st = json.load(open(p))
a = sys.argv[1:]
st["calls"].append(a)
if " ".join(a[:2]) in st.get("falha", []):
    json.dump(st, open(p, "w"))
    sys.exit("gh falso: falha simulada")
def opt(n):
    return a[a.index(n) + 1] if n in a else None
def jq(data):
    return subprocess.run(["jq", "-r", opt("--jq")], input=json.dumps(data), capture_output=True, text=True, check=True).stdout
out = ""
campos = dict(v.split("=", 1) for k, v in zip(a, a[1:]) if k == "-f")
if a[:1] == ["api"] and a[a.index("--method") + 1] == "GET" and a[3].endswith("/pulls") and campos.get("state") == "all":
    dono, _, branch = campos["head"].partition(":")
    out = jq([{"number": x["number"], "html_url": x["url"], "state": "open" if x["state"] == "OPEN" else "closed",
               "merged_at": "2026-11-16T21:00:00Z" if x["state"] == "MERGED" else None, "base": {"ref": x.get("base", "main")}}
              for x in reversed(st["prs"]) if x["head"] == branch and x.get("owner", "guifav") == dono])  # mais novo primeiro, como a API
elif a[:2] == ["run", "list"]:
    # Cada execução guardada é [branch, sha]; sha None vale para qualquer commit (o gh real sem --commit).
    out = jq([{"databaseId": 1} for b, c in st["runs"] if b == opt("--branch") and (opt("--commit") is None or c in (None, opt("--commit")))])
elif a[:2] == ["pr", "create"]:
    n = 100 + len(st["prs"])
    st["prs"].append({"number": n, "head": opt("--head"), "state": "OPEN", "url": f"https://github.com/guifav/guifav.github.io/pull/{n}"})
    st["bodies"].append(open(opt("--body-file")).read())
    out = st["prs"][-1]["url"] + "\n"
elif a[:2] != ["workflow", "run"]:
    sys.exit(f"gh falso: comando desconhecido {a}")
json.dump(st, open(p, "w"))
sys.stdout.write(out)
'''

PY = r'''#!/bin/bash
# python3 falso: o draft_preview.py roda com o Cloudflare trocado pelo D1 e o R2 de $SIM_NUVEM.
if [[ "$1" == *draft_preview.py ]]; then
  exec "$SIM_REAL_PY" - "$@" <<'DRIVER'
import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True  # o runner roda o script direto, sem __pycache__ no checkout
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
import draft_preview as D
n = json.load(open(os.environ["SIM_NUVEM"]))
Real = D.Cloudflare
class Falsa(Real):
    def aprovacoes(self, slug):
        return n["rows"]
    def baixar(self, key, work):
        if key not in n["objs"]:
            raise D.DraftError(f"não consegui baixar {key} do R2: o R2 respondeu NoSuchKey")
        return n["objs"][key].encode("latin-1")
D.Cloudflare = Falsa
sys.exit(D.main(sys.argv[2:]))
DRIVER
fi
exec "$SIM_REAL_PY" "$@"
'''


class PublicarWorkflowBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = passos(WORKFLOW.read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for nome, src in (("gh", GH), ("python3", PY)):
            (self.bin / nome).write_text(src)
            (self.bin / nome).chmod(0o755)
        # Repositório mínimo: o script de verdade, um post antigo e a pasta de imagens.
        base = self.root / "base"
        (base / "blog-src" / "scripts").mkdir(parents=True)
        shutil.copy(HERE / "draft_preview.py", base / "blog-src" / "scripts" / "draft_preview.py")
        (base / "blog-src" / "src" / "content" / "posts").mkdir(parents=True)
        (base / "blog-src" / "src" / "content" / "posts" / "outro-post.md").write_text("outro\n")
        (base / "blog-src" / "public" / "img" / "outro-post").mkdir(parents=True)
        (base / "blog-src" / "public" / "img" / "outro-post" / "a.webp").write_bytes(b"a")
        self.git("init", "-q", "-b", "main", cwd=base)
        self.git("add", "-A", cwd=base)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", cwd=base)
        self.remote = self.root / "remote.git"
        self.git("clone", "-q", "--bare", str(base), str(self.remote))
        self.estado({"prs": [], "runs": [], "calls": [], "bodies": []})
        self.versao = self.nuvem()
        self.branch = f"publica/{SLUG}-{self.versao[:8]}"
        self.execucoes = 0

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a, cwd=None, check=True):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True, check=check)

    def estado(self, st=None):
        p = self.root / "gh.json"
        if st is not None:
            p.write_text(json.dumps(st))
        return json.loads(p.read_text())

    def nuvem(self, md=MD, aprovar=True):
        fonte = {f"{SLUG}.md": md, **{f"img/{k}": v for k, v in IMGS.items()}}
        pasta = self.root / f"fonte-{len(list(self.root.glob('fonte-*')))}"
        for rel, b in fonte.items():
            (pasta / rel).parent.mkdir(parents=True, exist_ok=True)
            (pasta / rel).write_bytes(b)
        source = D.fonte_sha256(pasta, SLUG)
        man = D.manifest(D.Preview(SLUG, "t", b"<html>", {}), source, fonte)
        objs = {f"{SLUG}/manifest.json": json.dumps(man)}
        objs.update({f"{SLUG}/v/{man['version']}/fonte/{k}": b.decode("latin-1") for k, b in fonte.items()})
        rows = [{"id": 9, "html_sha256": man["html_sha256"], "source_sha256": source, "approved_at": "2026-11-16T20:00:00.000Z"}] if aprovar else []
        (self.root / "nuvem.json").write_text(json.dumps({"rows": rows, "objs": objs}))
        return man["version"]

    def rodar(self, slug=SLUG, versao=None, ref="refs/heads/main"):
        """Uma execução da workflow: checkout da main do remoto e os passos run, com os if do YAML."""
        self.execucoes += 1
        work = self.root / f"work-{self.execucoes}"
        self.git("clone", "-q", "--branch", "main", str(self.remote), str(work))
        runner = self.root / f"runner-{self.execucoes}"
        runner.mkdir()
        summary, genv = runner / "summary.md", runner / "env"
        summary.touch()
        genv.touch()
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.root), "SLUG": slug,
               "VERSAO": self.versao if versao is None else versao, "GH_TOKEN": "x", "GITHUB_REF": ref, "RUNNER_TEMP": str(runner),
               "GITHUB_ENV": str(genv), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_REPOSITORY": "guifav/guifav.github.io", "GITHUB_REPOSITORY_OWNER": "guifav", "GITHUB_RUN_ID": "123", "SIM_STATE": str(self.root / "gh.json"),
               "SIM_NUVEM": str(self.root / "nuvem.json"), "SIM_REAL_PY": sys.executable, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1",
               # Datas fixas: duas execuções sobre a mesma main fazem o mesmo commit, o caso mais difícil da recuperação
               # de branch sem PR (o commit conferido agora é o próprio commit do branch).
               "GIT_AUTHOR_DATE": "2026-11-16T20:00:00Z", "GIT_COMMITTER_DATE": "2026-11-16T20:00:00Z"}
        outputs, falhou = {}, False
        self.log = []
        for st in self.steps:
            cond = st.get("if")
            if cond == "failure()":
                if not falhou:
                    continue
            elif falhou:
                continue
            elif cond:
                # Só a forma usada no YAML: steps.<id>.outputs.estado (==|!=) '<valor>', unidas por ||.
                vale = False
                for parte in cond.split("||"):
                    k, op, v = parte.split()
                    val = outputs.get(k.split(".")[1], {}).get("estado", "")
                    vale = vale or ((op == "==") == (val == v.strip("'")))
                if not vale:
                    continue
            if "run" not in st:
                continue
            out = runner / "out"
            out.write_text("")
            def valor(k, v):
                if "steps.existe.outputs.url" in v:
                    return outputs.get("existe", {}).get("url", "")
                return {"CLOUDFLARE_ACCOUNT_ID": "a" * 32}.get(k, "falso") if "${{" in v else v
            passo = {k: valor(k, v) for k, v in st.get("env", {}).items()}
            e = {**env, **passo, "GITHUB_OUTPUT": str(out)}
            for line in genv.read_text().splitlines():
                k, _, v = line.partition("=")
                e[k] = v
            r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", st["run"]], cwd=work, env=e,
                               capture_output=True, text=True, timeout=60)
            self.log.append((st.get("name"), r.returncode, r.stderr[-600:]))
            if st.get("id"):
                outputs[st["id"]] = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
            falhou = falhou or r.returncode != 0
        # O log dos passos fica à parte, só para as mensagens de falha: o stderr do script não pode passar por resumo.
        return not falhou, summary.read_text(encoding="utf-8")

    def head_do_branch(self):
        return self.git("rev-parse", self.branch, cwd=self.remote).stdout.strip()

    def arquivos_do_branch(self):
        r = self.git("diff", "--name-only", "main", self.branch, cwd=self.remote, check=False)
        return sorted(r.stdout.split()) if r.returncode == 0 else None

    def chamadas(self, *prefixo):
        return [c for c in self.estado()["calls"] if c[:len(prefixo)] == list(prefixo)]

    def limpar_chamadas(self, **mudar):
        st = self.estado()
        st.update(calls=[], **mudar)
        self.estado(st)

    def empurrar_no_branch(self, mudar):
        """Muda o branch da versão no remoto, como alguém com escrita faria."""
        c = self.root / f"adult-{self.execucoes}"
        self.git("clone", "-q", "--branch", self.branch, str(self.remote), str(c))
        mudar(c)
        self.git("add", "-A", cwd=c)
        self.git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "x", cwd=c)
        self.git("push", "-q", "origin", self.branch, cwd=c)

    def test_passos_lidos_do_yaml(self):
        nomes = [st.get("name", st.get("uses")) for st in self.steps]
        self.assertEqual(nomes, ["Conferir as entradas", "actions/checkout@v7", "PR ou branch da versão já existe?",
                                 "Conferir a versão aprovada e gravar o post", "Enviar o branch", "Conferir o branch que já existe",
                                 "Abrir o PR e disparar o build", "PR aberto conferido", "Motivo da recusa"])
        self.assertEqual(self.steps[5]["if"], "steps.existe.outputs.estado == 'branch' || steps.existe.outputs.estado == 'pr-aberto'")
        self.assertIn("CF_PUBLICAR_API_TOKEN", self.steps[3]["env"])
        self.assertEqual(self.steps[2]["id"], "existe")
        self.assertEqual(self.steps[4]["run"], 'git push origin "HEAD:refs/heads/$BRANCH"')

    def test_caso_novo_abre_o_pr_do_branch_da_versao_e_dispara_o_build(self):
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(self.arquivos_do_branch(), ESPERADOS)
        mostra = self.git("show", f"{self.branch}:blog-src/src/content/posts/{SLUG}.md", cwd=self.remote).stdout
        self.assertEqual(mostra.encode(), MD)
        autor = self.git("log", "-1", "--format=%an <%ae>|%s", self.branch, cwd=self.remote).stdout.strip()
        self.assertEqual(autor, "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>|Publica artigo: Teste de publicação")
        [cria] = self.chamadas("pr", "create")
        self.assertEqual([cria[cria.index(k) + 1] for k in ("--base", "--head", "--title")],
                         ["main", self.branch, "Publica artigo: Teste de publicação"])
        corpo = self.estado()["bodies"][0]
        self.assertIn(self.versao, corpo)
        self.assertIn("https://github.com/guifav/guifav.github.io/actions/runs/123", corpo)
        self.assertEqual(self.chamadas("workflow", "run"), [["workflow", "run", "pages.yml", "--ref", self.branch]])
        self.assertIn("## PR de publicação aberto", resumo)

    def test_repetir_com_o_pr_existente_nao_faz_nada(self):
        self.assertTrue(self.rodar()[0])
        self.limpar_chamadas(runs=[[self.branch, self.head_do_branch()]])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])
        self.assertIn("## PR da versão já está aberto", resumo)
        self.assertIn("https://github.com/guifav/guifav.github.io/pull/100, com o head conferido de novo", resumo)
        # Mergeado ou fechado: nada, nem build, e nem a conferência (o D1 e o R2 nem são lidos).
        self.nuvem(aprovar=False)
        for estado in ("MERGED", "CLOSED"):
            st = self.estado()
            st["prs"][0]["state"], st["runs"], st["calls"] = estado, [], []
            self.estado(st)
            with self.subTest(estado=estado):
                ok, resumo = self.rodar()
                self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
                self.assertIn(f"({estado}). Nada a publicar de novo.", resumo)
                self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_pr_aberto_sem_build_ganha_o_build(self):
        self.assertTrue(self.rodar()[0])
        self.limpar_chamadas(runs=[])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(self.chamadas("pr", "create"), [])
        self.assertEqual(self.chamadas("workflow", "run"), [["workflow", "run", "pages.yml", "--ref", self.branch]])

    def test_pr_aberto_com_head_novo_da_mesma_arvore_ganha_o_build_do_head(self):
        # O build que conta é o do head conferido agora: um commit novo (aqui, vazio) com a mesma árvore aprovada não
        # herda o build do commit anterior (r3, Astra P2).
        self.assertTrue(self.rodar()[0])
        antigo = self.head_do_branch()
        c = self.root / "vazio"
        self.git("clone", "-q", "--branch", self.branch, str(self.remote), str(c))
        self.git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "vazio", cwd=c)
        self.git("push", "-q", "origin", self.branch, cwd=c)
        self.assertNotEqual(self.head_do_branch(), antigo)
        self.limpar_chamadas(runs=[[self.branch, antigo]])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(self.chamadas("workflow", "run"), [["workflow", "run", "pages.yml", "--ref", self.branch]])
        [lista] = self.chamadas("run", "list")
        self.assertEqual(lista[lista.index("--commit") + 1], self.head_do_branch())
        # Com o build do head atual, nada a disparar.
        self.limpar_chamadas(runs=[[self.branch, self.head_do_branch()]])
        self.assertTrue(self.rodar()[0])
        self.assertEqual(self.chamadas("workflow", "run"), [])

    def test_branch_sem_pr_com_o_conteudo_conferido_ganha_o_pr(self):
        self.assertTrue(self.rodar()[0])
        antes = self.git("rev-parse", self.branch, cwd=self.remote).stdout
        self.limpar_chamadas(prs=[], bodies=[])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(len(self.chamadas("pr", "create")), 1)
        self.assertEqual(self.git("rev-parse", self.branch, cwd=self.remote).stdout, antes, "o branch não é reescrito")
        local = self.git("rev-parse", "HEAD", cwd=self.root / f"work-{self.execucoes}").stdout
        self.assertEqual(local, antes, "com as datas fixas, o commit conferido agora é o mesmo do branch")
        self.assertIn("já existia, com exatamente os arquivos conferidos agora", resumo)

    def test_branch_sem_pr_adulterado_e_recusado(self):
        self.assertTrue(self.rodar()[0])
        md = f"blog-src/src/content/posts/{SLUG}.md"
        casos = {
            "conteúdo": (lambda c: (c / md).write_bytes(MD + b"injetado\n"), "o conteúdo dele não é o da versão conferida"),
            "arquivo a mais": (lambda c: (c / "blog-src" / "scripts" / "x.py").write_text("x"), "muda arquivos diferentes"),
            "link no lugar do post": (lambda c: ((c / md).unlink(), (c / md).symlink_to("../../../scripts/draft_preview.py")),
                                      "o conteúdo dele não é o da versão conferida"),
            "imagem a menos": (lambda c: (c / f"blog-src/public/img/{SLUG}/01-capa.webp").unlink(), "muda arquivos diferentes"),
        }
        base = self.git("rev-parse", self.branch, cwd=self.remote).stdout.strip()
        for nome, (mudar, padrao) in casos.items():
            with self.subTest(nome=nome):
                self.git("update-ref", f"refs/heads/{self.branch}", base, cwd=self.remote)
                self.empurrar_no_branch(mudar)
                self.limpar_chamadas(prs=[])
                ok, resumo = self.rodar()
                self.assertFalse(ok)
                self.assertEqual(self.chamadas("pr", "create"), [])
                self.assertIn("## Publicação recusada", resumo)
                self.assertIn(padrao, resumo)

    def test_sem_aprovacao_recusa_com_o_motivo_do_script_e_sem_branch(self):
        self.nuvem(aprovar=False)
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("nenhuma aprovação no painel", resumo)
        self.assertIsNone(self.arquivos_do_branch())
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_entradas_maliciosas_e_ref_errado_param_no_primeiro_passo(self):
        marca = self.root / "pwn"
        v = self.versao
        for slug, versao, ref, padrao in ((f"x$(touch {marca})", v, "refs/heads/main", "Slug inválido"),
                                          (f"{SLUG}\nx", v, "refs/heads/main", "Slug inválido"),
                                          (f"{SLUG}`touch {marca}`", v, "refs/heads/main", "Slug inválido"),
                                          (SLUG, v + "\n", "refs/heads/main", "Versão inválida"),
                                          (SLUG, f"$(touch {marca})", "refs/heads/main", "Versão inválida"),
                                          (SLUG, v.upper(), "refs/heads/main", "Versão inválida"),
                                          (SLUG, v, "refs/heads/outro", "só roda na main")):
            self.limpar_chamadas()
            with self.subTest(slug=slug, versao=versao, ref=ref):
                ok, resumo = self.rodar(slug, versao, ref)
                self.assertFalse(ok)
                self.assertIn(padrao, resumo)
                self.assertEqual(self.estado()["calls"], [], "nada vai ao GitHub")
                self.assertFalse(marca.exists())

    def test_pr_aberto_adulterado_ou_com_base_errada_e_recusado(self):
        # O PR aberto da versão não vale só por existir: o head é conferido de novo, e a base tem de ser a main (r2, Kimi P2).
        self.assertTrue(self.rodar()[0])
        md = f"blog-src/src/content/posts/{SLUG}.md"
        self.empurrar_no_branch(lambda c: (c / md).write_bytes(MD + b"injetado depois do PR\n"))
        for runs in ([[self.branch, self.head_do_branch()]], []):
            self.limpar_chamadas(runs=runs)
            with self.subTest(build_existente=bool(runs)):
                ok, resumo = self.rodar()
                self.assertFalse(ok)
                self.assertIn("o conteúdo dele não é o da versão conferida", resumo)
                self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [], "nem PR nem build")
        st = self.estado()
        st["prs"][0]["base"], st["calls"] = "outra", []
        self.estado(st)
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("tem base outra, não main", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_pr_aberto_mais_antigo_vale_antes_de_um_fechado_mais_novo(self):
        # A API devolve o mais novo primeiro; um PR fechado mais novo (com outra base) não esconde o PR aberto da versão,
        # que é conferido de novo (r4, Kimi P2). Dois abertos: ambíguo, recusa.
        self.assertTrue(self.rodar()[0])
        md = f"blog-src/src/content/posts/{SLUG}.md"
        self.empurrar_no_branch(lambda c: (c / md).write_bytes(MD + b"injetado depois do PR\n"))
        st = self.estado()
        st["prs"].append({"number": 200, "head": self.branch, "state": "CLOSED", "base": "outra", "url": "https://github.com/guifav/guifav.github.io/pull/200"})
        self.estado(st)
        self.limpar_chamadas(runs=[])
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("o conteúdo dele não é o da versão conferida", resumo)
        self.assertEqual(self.chamadas("workflow", "run"), [])
        st = self.estado()
        st["prs"][-1]["state"], st["calls"] = "OPEN", []
        self.estado(st)
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("tem mais de um PR aberto", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_pagina_cheia_de_prs_e_ambigua(self):
        # Com 100 PRs ou mais do branch, a página pode esconder o aberto: recusa. Com 99 fechados, nada a fazer (r5, Astra P4).
        fechados = [{"number": 300 + i, "head": self.branch, "state": "CLOSED", "url": f"https://github.com/guifav/guifav.github.io/pull/{300 + i}"}
                    for i in range(100)]
        self.limpar_chamadas(prs=fechados)
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("PRs demais para conferir", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])
        self.limpar_chamadas(prs=fechados[:99])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertIn("(CLOSED). Nada a publicar de novo.", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_pr_aberto_sem_aprovacao_valida_e_recusado(self):
        # A conferência do PR aberto passa pelo D1: sem a aprovação, o disparo de novo falha em vez de dar verde.
        self.assertTrue(self.rodar()[0])
        self.nuvem(aprovar=False)
        self.limpar_chamadas(runs=[])
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("nenhuma aprovação no painel", resumo)
        self.assertEqual(self.chamadas("workflow", "run"), [])

    def test_falha_do_gh_nas_consultas_recusa_sem_disparar(self):
        # Falha ao consultar os PRs ou os builds não vira "nenhum PR" nem "nenhum build" (r3, Kimi P3).
        self.limpar_chamadas(falha=["api --method"])
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("Não consegui consultar no GitHub os PRs do branch", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])
        self.limpar_chamadas(falha=[])
        self.assertTrue(self.rodar()[0])
        self.limpar_chamadas(falha=["run list"], runs=[[self.branch, self.head_do_branch()]])
        ok, resumo = self.rodar()
        self.assertFalse(ok)
        self.assertIn("Não consegui consultar no GitHub os builds do PR; nada foi disparado.", resumo)
        self.assertEqual(self.chamadas("pr", "create") + self.chamadas("workflow", "run"), [])

    def test_pr_de_fork_com_o_mesmo_branch_nao_conta(self):
        self.limpar_chamadas(prs=[{"number": 99, "head": self.branch, "state": "OPEN", "url": "https://github.com/x/pr/99", "owner": "x"}])
        ok, resumo = self.rodar()
        self.assertTrue(ok, f"{resumo}\nlog dos passos: {self.log}")
        self.assertEqual(len(self.chamadas("pr", "create")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
