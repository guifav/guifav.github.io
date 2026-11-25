# Contrato de conteúdo do blog

O que cada barreira garante no HTML publicado em guifav.dev/blog/ e qual formato editorial
os posts novos seguem. Vale para escrita de posts, revisão de código e classificação de
achados de gate. Adjudicado no PR #30 (épico #16, issue #17) e estendido com o formato de
resposta na issue #108.

## Quem escreve o que vai ao ar

O site é estático. Não há entrada de terceiro em tempo de execução. Só três origens
produzem HTML do blog:

1. Markdown dos posts em src/content/posts/, gerado pelo importador do Substack (#19)
   a partir de conteúdo do próprio autor, ou vindo da fonte aprovada de um rascunho
   (ver "Rascunhos e fonte no R2", abaixo).
2. Templates Astro em src/layouts/ e src/pages/, que são código revisado em PR.
3. Arquivos de public/, copiados pelo Astro sem transformação.

Quem tem escrita no repositório pode publicar qualquer coisa editando um template. Isso
é o que a revisão de PR cobre, e nenhuma barreira abaixo tenta impedir.

## Rascunhos e fonte no R2

O rascunho de um post novo fica fora do repositório até a aprovação do Gui no painel
guifav.dev/admin/rascunhos (#62), no bucket R2 privado blog-rascunhos. Cada versão que o
blog-src/scripts/draft_preview.py envia guarda duas coisas em <slug>/v/<versão>/ (#82):

- a prévia (o HTML montado e os arquivos dele), listada no campo files do manifesto. É o
  único conteúdo que o painel serve;
- a fonte, em fonte/: o Markdown (<slug>.md) e as imagens (img/...), listada no campo fonte
  do manifesto com o sha256 de cada arquivo. O painel nunca serve a fonte.

A fonte é exatamente o que gerou o source_sha256 da versão, e é esse hash que a aprovação
grava no D1. Um post que chega a src/content/posts/ vindo de um rascunho tem de ser o da
fonte aprovada, com o hash conferido. Quem faz isso é a workflow .github/workflows/publicar.yml
(#82), disparada com o slug e a versão: pelo draft_preview.py publicar, ela confere no D1
que a versão foi aprovada e é de um artigo, baixa a fonte da versão, confere o sha256 de
cada arquivo e o source_sha256, grava o Markdown em src/content/posts/<slug>.md e as
imagens em public/img/<slug>/ e abre o PR do branch publica/<slug>-<versão8>, com a
evidência no corpo. Ela recusa antes de gravar se qualquer conferência falhar, se o post
já existe na main ou se a fonte tem um arquivo que a barreira 2 não aceita em public/
(SVG, por exemplo). O PR passa pelo mesmo gate de qualquer outro; a workflow não mergeia.
Manifestos de antes do #82, sem o campo fonte, continuam válidos para o painel, mas não
publicam: o rascunho precisa ser reenviado.

Depois da publicação, com o PR da versão mergeado e o post no ar (o endereço responde 200
com HTML cujo elemento canonical aponta para ele), o painel mostra o botão "Remover
rascunho", que tira do R2 o rascunho do artigo (o manifesto, todas as versões e a fonte). Os
derivados ficam e continuam abaixo da linha do artigo, que passa a dizer "publicado: ver o post". A remoção é manual
porque o R2 não tem remoção condicional: um envio do mesmo slug durante a remoção poderia se
perder.

## Formato de resposta dos posts

Todo post novo publicado em `src/content/posts/<slug>.md` segue o formato de resposta
definido na issue #108 (com os limites de busca da #103 e o recorte de dados estruturados
da #105). Este contrato é a fonte canônica das regras abaixo; o esquema completo do
frontmatter fica em `blog-src/src/lib/schema.ts` e as verificações do site montado em
`scripts/check_site.py`. O fluxo editorial na skill `artigo-iaaplicada` (repositório
`guifav/guifav-skills`) aponta para esta seção.

1. **Título (`title` e `<title>`).** O campo `title` no frontmatter é obrigatório e não
   pode ficar vazio (`postSchema` em `blog-src/src/lib/schema.ts`, erro no build). O
   `<title>` publicado do post vai sem o nome do site: nem o sufixo ` | Guilherme Favaron`
   nem o prefixo `Guilherme Favaron | ` (o nome do site vem do JSON-LD `WebSite` da home,
   #103), e precisa bater com `og:title` e `twitter:title` (`check_titles_and_groups` em
   `scripts/check_site.py`, erro na CI). O teto de referência é de 60 caracteres
   (`TITLE_MAX = 60` em `check_seo` de `scripts/check_site.py`): acima de 60 caracteres, a
   CI emite aviso (não falha o build), em posts novos (`date` a partir de `2026-11-12` UTC,
   `NEW_POST_SINCE`) e em posts importados indexáveis.
2. **Descrição (`description` e `<meta name="description">`).** O campo `description` no
   frontmatter é obrigatório e não vazio (`postSchema`, erro no build). Ele é independente
   de `summary` e nunca substituído por ele (#103, #108): continua alimentando
   `<meta name="description">`, Open Graph, Twitter Card e o JSON-LD `BlogPosting` (o
   template `blog-src/src/pages/[slug].astro` separa os dois campos; `scripts/check_site.py`
   não compara os textos de `description` e `summary`). Em post novo (`date` a partir de
   `2026-11-12` UTC, `NEW_POST_SINCE`), `description` abaixo de 70 caracteres falha a CI
   (`DESCRIPTION_MIN_NEW = 70` em `check_seo` de `scripts/check_site.py`, erro). A faixa
   recomendada é de 120 a 160 caracteres (`DESCRIPTION_RANGE = (120, 160)` em `check_seo`):
   fora dessa faixa (quando não cai no erro de mínimo em post novo), a CI emite aviso em
   posts novos e em posts importados indexáveis.
3. **Resumo no topo (`summary` e bloco "Em resumo").** Por decisão de 25/11 na #108, o
   campo `summary` no frontmatter vale para todo post indexável. Ele traz 2 ou 3 frases
   diretas respondendo à pergunta principal do post, com até 500 caracteres, de forma
   independente da `description`. No HTML publicado, `blog-src/src/pages/[slug].astro`
   exibe o valor dentro de `<section class="post-summary">` com o rótulo `"Em resumo"`,
   logo acima de `<div class="post-body">` (e portanto acima do corpo e de qualquer
   `<figure>` dele). O que a CI verifica e o que é só editorial:
   - quando `summary` é declarado, valor vazio ou acima de 500 caracteres falha o build
     (`SUMMARY_MAX = 500` em `postSchema`, `blog-src/src/lib/schema.ts`); `scripts/check_site.py`
     não verifica esse teto de 500 caracteres;
   - chave `summary` repetida ou fora do formato YAML simples no frontmatter falha antes do
     build (`blog-src/scripts/check-posts.ts` via `simpleScalarProblems` em
     `blog-src/src/lib/postfiles.ts`);
   - consistência entre frontmatter e HTML publicado falha a CI (`check_summary` e
     `_has_frontmatter_summary` em `scripts/check_site.py`): se há `summary` não vazio no
     frontmatter, a página precisa de exatamente um bloco visível `<section class="post-summary">`
     (`SUMMARY_CLASS`) antes de `.post-body`, começando com `"Em resumo"` (`SUMMARY_LABEL`)
     e seguido de texto não vazio; se não há `summary`, o bloco não pode aparecer;
   - exigir `summary` em todo post indexável (`summary` é opcional no `postSchema` e no
     `check_summary`) e manter a extensão em 2 ou 3 frases são regras editoriais, não
     verificadas pela CI.
4. **Subtítulos (`H2`/`H3`) em forma de pergunta.** Escrever subtítulos `H2` (e `H3`,
   quando fizer sentido) em forma de pergunta onde couber, sem forçar. Quando o texto
   depender de outra estrutura (por exemplo, seções numeradas citadas ao longo do corpo),
   manter a forma natural. Regra editorial, não verificada pela CI.
5. **Tabelas em Markdown e texto alternativo em imagens.** Tabelas comparativas, de
   critérios ou de dados vão sempre em Markdown puro no corpo do post, nunca exportadas
   como imagem (as tags de tabela são permitidas pela barreira 1 em `src/lib/sanitize.ts`).
   Evitar tabela como imagem é regra editorial, não verificada pela CI. Para imagens reais
   do post (`<img>` dentro de `<article>`), preencher o atributo `alt`: `check_seo`
   (`_ArticleImages` em `scripts/check_site.py`) emite aviso para cada imagem sem texto
   alternativo nos posts novos e nos importados indexáveis.
6. **Perguntas frequentes no fim, sem JSON-LD `FAQPage`.** Fechar o post com 3 a 5
   perguntas frequentes em texto visível: um `H2` (por exemplo, `## Perguntas frequentes`
   ou `## Perguntas frequentes sobre <tema>`) e cada pergunta como `H3`, seguida da resposta
   em parágrafo. As perguntas e respostas só podem tratar do que o próprio post já responde
   (lição do Lote B da #108: a seção nunca traz fato novo que o corpo não sustenta).
   Presença, quantidade (3 a 5), estrutura (`H2`/`H3`) e fidelidade ao corpo são regras
   editoriais, não verificadas pela CI. Nunca emitir JSON-LD `FAQPage` (decisões da #105 e
   da #108):
   - no markdown do post, `<script type="application/ld+json">` falha o build pela barreira
     1 (`src/lib/sanitize.ts`, `<script>` fora da lista permitida) e dispara erro de conteúdo
     ativo em `scripts/check_site.py` (`_Page` / `check`, `<script>` fora do template);
   - nos templates de post, o JSON-LD é apenas o `@graph` com `BlogPosting`,
     `BreadcrumbList` e `Person` emitido por `postLdGraph` (`blog-src/src/lib/blog.ts`) e
     conferido por `check_post_ld` em `scripts/check_site.py`. A checagem explícita que
     recusa `@type: "FAQPage"` em `scripts/check_site.py` hoje roda apenas em
     `check_consulting` (página `/consultoria/`); em templates de post, não adicionar
     `FAQPage` é regra editorial e de revisão de template, não verificada por
     `check_post_ld`.

Resumo do que a CI verifica e do que fica na revisão editorial:

| Regra | Limite ou forma | Verificado pela CI | Onde (erro ou aviso) |
| --- | --- | --- | --- |
| `title` preenchido | mínimo 1 caractere | sim | erro no build (`postSchema` em `blog-src/src/lib/schema.ts`) |
| `<title>` sem nome do site e igual a `og:title`/`twitter:title` | sem prefixo/sufixo `Guilherme Favaron` | sim | erro na CI (`check_titles_and_groups` em `scripts/check_site.py`) |
| Comprimento do `<title>` | até 60 caracteres | sim | aviso na CI (`TITLE_MAX = 60` em `check_seo`, `scripts/check_site.py`) |
| `description` preenchida | mínimo 1 caractere (independente de `summary` no template e na revisão) | sim | erro no build (`postSchema` em `blog-src/src/lib/schema.ts`; `check_site.py` não compara os dois textos) |
| Mínimo da `description` em post novo | mínimo 70 caracteres (`date >= 2026-11-12` UTC) | sim | erro na CI (`DESCRIPTION_MIN_NEW = 70`, `NEW_POST_SINCE` em `check_seo`) |
| Faixa da `description` | 120 a 160 caracteres | sim | aviso na CI (`DESCRIPTION_RANGE = (120, 160)` em `check_seo`) |
| `summary` válido quando declarado | não vazio, até 500 caracteres, chave única | sim | erro no build (`SUMMARY_MAX = 500` em `postSchema`; `simpleScalarProblems` em `postfiles.ts`; não checado pelo `check_site.py`) |
| Bloco "Em resumo" alinhado ao `summary` | 1 `<section class="post-summary">` visível antes de `.post-body` com rótulo `"Em resumo"` | sim | erro na CI (`check_summary`, `_has_frontmatter_summary`, `SUMMARY_CLASS`, `SUMMARY_LABEL` em `scripts/check_site.py`) |
| `summary` em todo post indexável, com 2 ou 3 frases | 2 ou 3 frases respondendo à pergunta principal | não | regra editorial, não verificada pela CI |
| Subtítulos `H2`/`H3` em forma de pergunta | onde couber, sem forçar | não | regra editorial, não verificada pela CI |
| Tabelas em Markdown (nunca como imagem) | tabela Markdown pura no corpo | não | regra editorial, não verificada pela CI |
| Imagens do artigo com `alt` | atributo `alt` não vazio em `<img>` dentro de `<article>` | sim | aviso na CI (`_ArticleImages` em `check_seo`, `scripts/check_site.py`) |
| Perguntas frequentes no fim (3 a 5, só fatos do corpo) | `H2` + `H3` + parágrafo em texto visível | não | regra editorial, não verificada pela CI |
| Sem `<script>` / `FAQPage` no markdown do post | proibido no conteúdo do post | sim | erro no build (`sanitize.ts`) e na CI (`_Page`/`check` em `scripts/check_site.py`) |
| Sem JSON-LD `FAQPage` em template | proibido por decisão (#105, #108) | parcial | erro na CI só em `/consultoria/` (`check_consulting` em `scripts/check_site.py`); em templates de post, regra editorial e de revisão (não checada por `check_post_ld`) |

## Barreiras

1. Limpeza no render (src/lib/sanitize.ts). É a barreira de segurança do conteúdo do
   markdown. O Satteri lê o HTML cru como o navegador faria; o plugin falha o build com
   elemento fora da lista, iframe fora dos hosts permitidos e imagem ou vídeo com URL
   ativa em src, srcset ou poster. Atributos fora da lista são removidos. Um bypass
   desta barreira no markdown é bloqueio de gate.
   O atributo download é aceito só em <a> (#75), para o botão de baixar a animação
   sobre a figura (a.baixar): o valor é apenas o nome sugerido do arquivo, o href
   continua passando por safeUrl e download segue removido em qualquer outra tag. O
   botão é para figuras animadas: numa imagem exibida com menos de 32px de altura, ele
   passa da borda de baixo da imagem, sem cobrir a legenda.
2. Lista de extensões de public/ (scripts/check-posts.ts, antes do build, e
   scripts/check_site.py na raiz do repo, depois). Só mídia: webp, png, jpg, jpeg, gif,
   avif, mp4, webm. HTML, SVG, JS, CSS, qualquer outra extensão e link simbólico (o Vite
   segue o link ao copiar) falham o build. Sob
   /blog/, o site publicado só tem .html, .xml e essa mídia. Um arquivo fora disso em
   _site/blog/ é bloqueio de gate.
3. Templates. São código revisado. O atributo data-site-script marca os scripts do
   layout para o verificador reconhecer; ele não é prova de origem contra quem edita o
   template, e não pretende ser. Do mesmo jeito, data-site-form marca o único formulário do
   blog, o de inscrição da newsletter (#21): POST, com destino no Worker
   (SITE_FORM_ACTIONS em scripts/check_site.py) e fora do conteúdo do post.
4. Verificador do site montado (scripts/check_site.py, na raiz do repo). É verificação
   de integridade: links internos existem, nenhuma página fora do blog aponta para ele
   antes do cutover, toda página do blog tem noindex antes do cutover, RSS e sitemap são
   válidos e o sitemap só lista páginas que existem e são indexáveis. Ele também verifica
   os itens de SEO, bloco "Em resumo" e JSON-LD listados na seção "Formato de resposta dos
   posts". A resolução de URL é feita pelo URL do Node, o parser WHATWG real
   (scripts/resolve_urls.mjs). A leitura de HTML usa o html.parser do Python, que não é um
   parser HTML5: ele lê o HTML que os templates e a limpeza do render produzem, não emula o
   navegador. O alarme de conteúdo ativo que ele carrega é secundário e pega regressão de
   template; ele não é a barreira de segurança.

## Como classificar achados

- Bypass da barreira 1 no markdown, ou da barreira 2: bloqueio.
- Link quebrado, noindex ausente, sitemap ou RSS errado, em HTML que o blog gera: bloqueio.
- Erro de build ou de CI nos campos do formato de resposta (título de post com sufixo ou
  divergente de `og:title`/`twitter:title`, `description` abaixo de 70 caracteres em post
  novo, `summary` vazio ou acima de 500 caracteres no schema, bloco "Em resumo" ausente ou
  fora de posição, `FAQPage` em `/consultoria/`): bloqueio.
- Aviso de CI (`title` acima de 60 caracteres, `description` fora de 120 a 160 caracteres,
  imagem sem `alt`) ou desvio nas regras editoriais do formato de resposta (`summary`
  ausente em post indexável ou fora de 2 ou 3 frases, subtítulos sem pergunta onde caberia,
  tabela como imagem, perguntas frequentes fora de 3 a 5 ou com fato que o corpo não
  sustenta): apontar na revisão antes do ok do Gui, sem classificar como erro de build.
- Divergência do check_site.py em relação ao navegador que só aparece com HTML ou URL
  que nenhuma das três origens produz dentro das barreiras 1 a 3: P3, registrada no
  PR, sem bloqueio e sem remendo no parser.
- Conteúdo que exige novo elemento, atributo ou extensão: muda este contrato e a lista
  no mesmo PR, com teste.
