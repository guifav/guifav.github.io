# Blog do guifav.dev

Fonte do blog publicado em `https://guifav.dev/blog/` (épico #16). Astro com saída estática; o workflow `.github/workflows/pages.yml` gera o blog, junta com o site atual e publica no GitHub Pages.

## Rodar localmente

Requer Node 22.23.4 ou superior (`.nvmrc`).

```bash
cd blog-src
npm ci
npm test          # funções puras (node --test)
npm run dev       # http://localhost:4321/blog/
npm run build     # gera dist/
```

Para conferir o site inteiro como ele vai ao ar, a partir da raiz do repo:

```bash
python3 scripts/assemble_site.py   # monta _site/ a partir de site-manifest.txt + blog-src/dist
python3 scripts/check_site.py      # links internos, noindex antes do cutover, RSS e sitemap (requer node no PATH)
python3 -m http.server -d _site 8099
python3 scripts/verify_live.py http://127.0.0.1:8099 --local
```

## O que vai ao ar

- Da raiz do repo, só o que está em `site-manifest.txt`. Pasta nova (por exemplo `workers/`) não é publicada sem entrar na lista. `docs/`, `_next/` e `tools/` ficam fora por decisão do Gui.
- O blog vai para `/blog/`. O GitHub Pages só serve o `404.html` da raiz, que atende o blog também.

## Posts

Um markdown por post em `src/content/posts/<slug>.md`, direto na pasta e com o nome do arquivo igual ao campo `slug`. O `npm run build` roda antes `scripts/check-posts.ts`, que falha com slug duplicado, nome diferente do slug ou post em subpasta (sem isso, o Astro juntaria dois posts com o mesmo slug em uma página só, sem erro). O esquema está em `src/lib/schema.ts`:

| Campo | Obrigatório | Observação |
| --- | --- | --- |
| `title`, `description` | sim | |
| `subtitle` | não | |
| `date` | sim | data de publicação (ISO 8601); nulo, booleano e número são rejeitados |
| `updated` | não | data real de alteração; nunca a data da importação |
| `slug` | sim | idêntico ao do Substack; vira a URL `/blog/<slug>/` |
| `cover` | não | caminho dentro do blog, por exemplo `img/<slug>/capa-1456.webp` |
| `type` | não | `newsletter` (padrão) ou `podcast` |
| `spotify_url` | só em podcast | link https de episódio do Spotify (open.spotify.com ou Spotify for Creators); vira o player embutido |
| `index` | não | padrão `true`; com `false`, a página sai com `noindex, follow` e fica fora do sitemap |
| `newsletter` | não | padrão `false`; marca o post para o rascunho de envio (#21) |

Imagens ficam em `public/img/<slug>/`, publicadas em `/blog/img/<slug>/`.

## HTML no corpo dos posts

O que cada barreira garante, e como classificar achados de revisão, está em [`CONTRATO-DE-CONTEUDO.md`](CONTRATO-DE-CONTEUDO.md).

O markdown pode trazer HTML cru (o importador do Substack gera `figure`, `picture`, `video`, tabelas e iframes). Ele passa por uma lista do que é permitido no render (`src/lib/sanitize.ts`, ligada em `astro.config.mjs`):

- elemento fora da lista (`script`, `style`, `svg`, `form`, `article`, `center`, qualquer tag desconhecida), iframe que não seja https de Spotify, YouTube ou Vimeo e imagem ou vídeo com URL ativa em `src`, `srcset` ou `poster` fazem o build falhar, com o arquivo e as tags. Nada some em silêncio: o importador tem de gerar só HTML da lista;
- atributos fora da lista (`on*`, `srcdoc`, `data-*`, estilo que não seja o do realce de código) e URLs `javascript:` e `data:` em links são removidos, sem perda de conteúdo.

`public/` só aceita mídia (webp, png, jpg, jpeg, gif, avif, mp4, webm), porque o Astro copia essa pasta sem passar pela limpeza. HTML, SVG, JS, CSS e o resto fazem o build falhar (checagem em `scripts/check-posts.ts`). Sob `/blog/`, o site publicado só tem `.html`, `.xml` e essa mídia.

Os scripts do layout levam o atributo `data-site-script`. Script novo no layout precisa dele, senão o `check_site.py` trava o deploy.

O Astro guarda em cache (`.astro/` e `node_modules/.astro/`) o HTML já renderizado dos posts e não refaz esse HTML quando só o config muda. Depois de mexer no `astro.config.mjs` ou em `src/lib/sanitize.ts`, apague essas duas pastas antes do build. No CI o build parte sempre sem cache.

## Antes do cutover

`PRE_CUTOVER_NOINDEX` em `src/consts.ts` deixa o blog inteiro com `noindex`, e o `check_site.py` falha se alguma página fora do blog apontar para ele. O PR do cutover (#24) troca a constante para `false`.

Os três posts `teste-*` existem só para conferir o leiaute e saem com a importação (#19).

## Newsletter: rascunho do envio (#21, parte B)

`scripts/newsletter_draft.py` monta o email de um post publicado e cria o broadcast como **rascunho** no Resend. Ele nunca envia: o envio é o clique do Gui no painel do Resend. Roda na KVM, com a chave do Resend só no ambiente de lá.

```
# só gera email.html e email.txt para conferir
python3 scripts/newsletter_draft.py <slug> --dry-run --out /tmp/email
# cria o rascunho "blog/<slug>" (com --update, reescreve o rascunho que já existe)
RESEND_API_KEY=... NEWSLETTER_SEGMENT_ID=... python3 scripts/newsletter_draft.py <slug>
```

- A fonte é a página publicada em `https://guifav.dev/blog/<slug>/` (`--source dist` usa o build local). O post precisa estar no ar antes do rascunho.
- A conversão é por lista fechada de tags, classes, atributos e esquemas de link. O que estiver fora dela interrompe o script.
- Um broadcast por post: se já existe um rascunho, nada muda; se já existe um enviado, agendado ou em envio, o script para.
- HTML acima de 102 KB, o limite em que o Gmail corta a mensagem, leva só o começo do post e o link "Ler no site".
- Remetente padrão `IA Aplicada <insights@expert.guifav.dev>` (`NEWSLETTER_FROM` só aceita `expert.guifav.dev`), reply-to `contact@guifav.dev`.
- Testes: `python3 scripts/test_newsletter_draft.py` (sem rede; rodam no CI).
