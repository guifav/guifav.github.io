# Contrato de conteúdo do blog

O que cada barreira garante no HTML publicado em guifav.dev/blog/. Vale para revisão de
código e para classificar achados de gate. Adjudicado no PR #30 (épico #16, issue #17).

## Quem escreve o que vai ao ar

O site é estático. Não há entrada de terceiro em tempo de execução. Só três origens
produzem HTML do blog:

1. Markdown dos posts em src/content/posts/, gerado pelo importador do Substack (#19)
   a partir de conteúdo do próprio autor.
2. Templates Astro em src/layouts/ e src/pages/, que são código revisado em PR.
3. Arquivos de public/, copiados pelo Astro sem transformação.

Quem tem escrita no repositório pode publicar qualquer coisa editando um template. Isso
é o que a revisão de PR cobre, e nenhuma barreira abaixo tenta impedir.

## Barreiras

1. Limpeza no render (src/lib/sanitize.ts). É a barreira de segurança do conteúdo do
   markdown. O Satteri lê o HTML cru como o navegador faria; o plugin falha o build com
   elemento fora da lista, iframe fora dos hosts permitidos e imagem ou vídeo com URL
   ativa em src, srcset ou poster. Atributos fora da lista são removidos. Um bypass
   desta barreira no markdown é bloqueio de gate.
2. Lista de extensões de public/ (scripts/check-posts.ts, antes do build, e
   scripts/check_site.py na raiz do repo, depois). Só mídia: webp, png, jpg, jpeg, gif,
   avif, mp4, webm. HTML, SVG, JS, CSS e qualquer outra extensão falham o build. Sob
   /blog/, o site publicado só tem .html, .xml e essa mídia. Um arquivo fora disso em
   _site/blog/ é bloqueio de gate.
3. Templates. São código revisado. O atributo data-site-script marca os scripts do
   layout para o verificador reconhecer; ele não é prova de origem contra quem edita o
   template, e não pretende ser.
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
