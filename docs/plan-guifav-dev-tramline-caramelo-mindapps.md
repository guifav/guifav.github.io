# Plano: guifav.dev, Tramline, Caramelo e MindApps

Status: aprovado para planejamento, nao executar implementacao ainda.
Data: 2026-07-09.

## Papel deste projeto

`guifav.github.io` sera a pagina pessoal / developer profile de Guilherme Favaron. O dominio publico desejado e `guifav.dev`, hospedado via GitHub Pages neste repositorio.

A pagina deve apresentar o Guilherme como CTO/builder de IA aplicada, com projetos reais em producao, codigo aberto e cases de modelos/produtos.

## Arquitetura de links

- `guifav.dev` aponta para este site.
- Este site aponta para `https://tramline.ai` como empresa/projeto guarda-chuva de modelos especializados.
- Este site aponta para `https://ia-caramelo.com` como case de modelo/produto.
- Este site aponta para `https://mindapps.ai` como portfolio/lab open-source de projetos de IA aplicada.
- Tramline deve apontar para Caramelo e MindApps como cases.

## Mudancas planejadas neste repo

1. Criar `CNAME` com `guifav.dev`.
2. Atualizar canonical/OG/JSON-LD de `https://guifav.github.io` para `https://guifav.dev`, mantendo GitHub Pages como host.
3. Atualizar `index.html`:
   - Hero mais orientado a builder/developer profile.
   - Seção de projetos principais com Tramline, Caramelo e MindApps.
   - Tramline descrita como empresa/projeto de modelos de IA especializados, nao mais como governanca de citizen development.
   - Caramelo descrito como case de modelo especializado da Tramline, com link para `ia-caramelo.com`.
   - MindApps descrito como portfolio/lab open-source de projetos de IA aplicada, tambem como case da Tramline.
   - Manter ferramentas existentes como LLM Ranking e Framework SEO/AEO/GEO.
4. Atualizar i18n PT-BR/EN em conjunto.
5. Atualizar footer e links sociais.

## DNS planejado

Provider: Cloudflare.
Gui confirmou que ha account token valido localmente.

Registros desejados para GitHub Pages:

- Apex `guifav.dev`:
  - A -> 185.199.108.153
  - A -> 185.199.109.153
  - A -> 185.199.110.153
  - A -> 185.199.111.153
- Opcional IPv6:
  - AAAA -> 2606:50c0:8000::153
  - AAAA -> 2606:50c0:8001::153
  - AAAA -> 2606:50c0:8002::153
  - AAAA -> 2606:50c0:8003::153
- `www.guifav.dev`:
  - CNAME -> guifav.github.io

Antes de aplicar DNS, checar zona Cloudflare e registros existentes.

## Validacao esperada

- `git diff` revisado antes de commit.
- `python3 -m http.server` ou equivalente para smoke local.
- Browser/manual check: PT-BR, EN, tema, links, mobile aproximado.
- `dig` confirmando DNS apos Cloudflare.
- `curl -I https://guifav.dev` apos propagacao e GitHub Pages HTTPS.

## Fora do escopo desta rodada

- Redesign completo.
- Mexer em `llm-ranking/` ou `seo-geo-aeo-framework/` alem de links/copy da home.
- Alterar conteudo profundo de MindApps ou Tramline fora da tessitura publica aprovada.
