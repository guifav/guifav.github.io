# Contrato de conteúdo do blog

O que cada barreira garante no HTML publicado em guifav.dev/blog/. Vale para revisão de
código e para classificar achados de gate. Adjudicado no PR #30 (épico #16, issue #17).

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
   válidos e o sitemap só lista páginas que existem e são indexáveis. A resolução de URL
   é feita pelo URL do Node, o parser WHATWG real (scripts/resolve_urls.mjs). A leitura
   de HTML usa o html.parser do Python, que não é um parser HTML5: ele lê o HTML que os
   templates e a limpeza do render produzem, não emula o navegador. O alarme de conteúdo
   ativo que ele carrega é secundário e pega regressão de template; ele não é a barreira
   de segurança.

## Como classificar achados

- Bypass da barreira 1 no markdown, ou da barreira 2: bloqueio.
- Link quebrado, noindex ausente, sitemap ou RSS errado, em HTML que o blog gera: bloqueio.
- Divergência do check_site.py em relação ao navegador que só aparece com HTML ou URL
  que nenhuma das três origens produz dentro das barreiras 1 a 3: P3, registrada no
  PR, sem bloqueio e sem remendo no parser.
- Conteúdo que exige novo elemento, atributo ou extensão: muda este contrato e a lista
  no mesmo PR, com teste.
