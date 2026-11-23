# Worker `blog-newsletter`

Inscrição da newsletter do blog guifav.dev (issue #21, parte A). Recebe o formulário do blog, manda o email de confirmação e, quando o link é aberto, cadastra o contato no segmento do Resend.

| Rota | O que faz |
| --- | --- |
| `POST /subscribe` | Confere a origem, aplica o limite por IP, confere o Turnstile, valida o email, aplica o limite por endereço e manda o email com o link assinado. Responde sempre com 303 para `/blog/inscricao-enviada/`, exceto com Turnstile reprovado (`/blog/assinar/#erro-verificacao`). |
| `GET /confirm` | Valida o link (HMAC-SHA256, 48 h), cria ou reativa o contato no segmento, grava a propriedade `site_confirmed_at` e a conversão `newsletter_confirm` (issue #97) e redireciona para `/blog/inscricao-confirmada/`. Link inválido ou expirado: `/blog/inscricao-invalida/`. |
| `POST /material` | Formulário das páginas de material do canal @guifav (`/planilha-custos-ia/`, `/mapa-decisao-ia/`). Confere a origem, o limite por IP, o Turnstile, o email e o limite por endereço (chave `material:<email>`), grava a conversão `material_request` (email ainda não comprovado, `verified = 0`, com o `ga_client_id` do campo `ga_cid` quando válido; issue #99) e manda pelo Resend, de `MATERIAL_FROM`, um email com um link assinado por arquivo (7 dias) e, só se a caixa `insights=sim` veio marcada, o link de confirmação da lista. Não cria contato. Responde 303 para `/<material>/enviado/`, exceto com Turnstile reprovado (`/<material>/#erro-verificacao`). |
| `GET /material/download` | Confere o link (HMAC-SHA256 com prefixo `m1` e o id do arquivo: nunca vale como confirmação, e o link de um arquivo não abre outro) grava a conversão `material_download` (issue #97) com `request_id` apontando para o pedido e `file` com o arquivo baixado, manda ao GA4 o evento `material_download` pelo Measurement Protocol (issue #99) e devolve o arquivo como anexo, sem cache. Link inválido ou expirado: `/<material>/#link-expirado`. |

Os materiais ficam em `materiais/` e entram no Worker como módulos de dados (`[[rules]]` no `wrangler.toml`, ligados em `src/worker.ts`); o site não tem cópia pública deles. O catálogo (id, título, arquivos, interesses) está em `src/materiais.ts`. Para trocar um arquivo, substitua-o em `materiais/` e faça o deploy. O registro de quem pediu cada material é o log de envios do Resend, com as tags `material` e `insights`.

`site_confirmed_at` registra o opt-in pelo site. A sincronização com o export do Substack (`blog-src/scripts/import_subscribers.py --sync`) nunca descadastra quem tem esse campo.

Os limites (cerca de 5 pedidos por IP e 1 email por endereço a cada 60 s, no `wrangler.toml`) usam o rate limit nativo do Workers, que é aproximado e conta separado em cada localidade do Cloudflare. Junto com o Turnstile, eles contêm abuso, mas não são um teto global garantido.

O link carrega o email e a validade, e a assinatura prova a origem. O único estado é o D1 `blog-admin` (binding `DB`, esquema nas migrações do painel), com as conversões do site (issue #97):

- `conversions`: uma linha por ação com email comprovado pelo link assinado (`material_download`, `newsletter_confirm`), com o material (nulo na confirmação), os interesses do catálogo naquele momento e a data. O `id` é o SHA-256 da ação com a assinatura do link (só a forma canônica vale): abrir de novo o mesmo link não duplica, e um pedido novo gera outra linha. Dois pedidos do mesmo endereço no mesmo segundo produzem o mesmo link e contam como uma conversão. `synced_at` fica nulo até a fase 2 da #97 levar a linha ao Twenty.
- Pedido e download por pessoa (issue #99): `material_request` guarda o email do pedido com `verified = 0` (o email só é comprovado quando o link é aberto) e o `ga_client_id` que o formulário mandou (`ga_cid`, preenchido pelo `assets/track.js` só com os cookies aceitos). O id do pedido é o SHA-256 do material, do email e da validade dos links; o download recalcula o mesmo id a partir do link verificado e grava em `request_id`. A tabela `material_requests` (só contagem) saiu na migração 0005.
- GA4 (issue #99): no download, o Worker manda `material_download` (`material_id`, `file_id`) ao Measurement Protocol, com o `client_id` do pedido, que liga o download à visita no GA4; sem ele, com um `client_id` aleatório (contagem anônima). Nunca leva email. Precisa de `GA4_MEASUREMENT_ID` (no `wrangler.toml`) e do segredo `GA4_API_SECRET`; sem o segredo, o evento não sai, e o log diz isso. No GA4, registre `material_id` e `file_id` como dimensões personalizadas de evento.
- A gravação é de melhor esforço e roda depois da resposta (`ctx.waitUntil`): se o D1 falhar ou demorar, o arquivo é entregue e a confirmação conclui do mesmo jeito, e o log registra a falha sem o email.
- Um verificador de links do provedor de email que abra o link de download conta como clique. O email só vai ao endereço digitado, então a conversão ainda é dessa caixa de entrada; o risco aceito é alguém digitar o email de outra pessoa e o verificador dela abrir o link.

## Testes

```
npm test
```

Node 22.18 ou mais novo (TypeScript direto pelo `node --test`). Os testes rodam no workflow do Pages, junto com os do blog.

## Deploy

O deploy é manual, depois do merge, e não está no CI. O DNS de `guifav.dev` só muda com ok do Gui.

**Conversões (issues #97 e #99):** antes do `wrangler deploy` deste Worker, aplique as migrações `0004_conversions.sql` e `0005_conversions_pedido.sql` no D1, a partir de `workers/admin/`: `npx wrangler@4 d1 migrations apply blog-admin --remote`. Sem as tabelas, o Worker funciona, mas cada download e confirmação gera um erro no log e nenhuma conversão é gravada. Grave o segredo do GA4 com `npx wrangler@4 secret put GA4_API_SECRET` (o valor vem do GA4, em Admin > Fluxos de dados > guifav.dev > Segredos da API do Measurement Protocol). Depois do deploy, baixe um material com um endereço legível e confira a linha com `npx wrangler@4 d1 execute blog-admin --remote --command "SELECT action, material, occurred_at FROM conversions ORDER BY occurred_at DESC LIMIT 1"`; apague a linha de teste em seguida.

1. **Segmento** "IA Aplicada" no Resend. Copie o id para `SEGMENT_ID` no `wrangler.toml`. Com `SEGMENT_ID` vazio, o Worker responde 500 e não cadastra ninguém.
   **Tópico** "Expert Insights, de Guilherme Favaron" no Resend (antes "IA Aplicada", renomeado em #55) (privado, `default_subscription` opt_in), com o id em `TOPIC_ID`. O descadastro da newsletter vale só para o tópico, e não para todos os broadcasts da conta. Quem confirma de novo pelo site volta ao tópico: o `POST /contacts` leva `topics` com `opt_in`. Para um email que já existe, esse POST é upsert (201, mesmo id) e aplica status global, segmento, propriedades e tópico, conforme sondado na API em 2026-11-12. O caminho de reserva (reativar, tópico, segmento) só roda se a API recusar o POST com 409 ou 422. Com `TOPIC_ID` vazio, o Worker responde 500.
2. **Turnstile:** crie um widget para `guifav.dev`. A chave pública vai para `TURNSTILE_SITE_KEY` em `blog-src/src/consts.ts`; a secreta, para o segredo abaixo.
3. **Publicação:** `npx wrangler@4 deploy`. A rota `insights.guifav.dev` é um domínio próprio do Worker, e o Cloudflare cria o registro DNS do subdomínio.
4. **Segredos**, os três de uma vez (`npx wrangler@4 secret bulk`, lendo um JSON pela entrada padrão, sem o valor passar pela tela nem pelo histórico): `RESEND_API_KEY`, `TURNSTILE_SECRET` e `CONFIRM_SECRET` (`openssl rand -base64 48`). Confira com `npx wrangler@4 secret list`.
5. **Propriedade `site_confirmed_at`** no Resend, antes de publicar uma versão que a grava: o Resend recusa a gravação de propriedade inexistente, e a confirmação viraria 502. Crie com `blog-src/scripts/import_subscribers.py --schema`. A importação dos assinantes do Substack só roda depois deste deploy e do teste real com o marcador gravado: um Worker sem o marcador no ar durante a importação deixaria confirmações sem proteção contra o `--sync`.
6. **Teste real** com um endereço que se consiga ler: inscrição no site publicado, email recebido de `insights@guifav.dev`, clique no link, contato no segmento. Confirmação repetida não duplica. Depois, o contato de teste sai.

O formulário (`NEWSLETTER_FORM_ENABLED` e a chave pública do Turnstile em `blog-src/src/consts.ts`) foi ligado no PR #34, junto com o primeiro deploy, em 2026-11-11.

## Remetente

A confirmação sai de `insights@guifav.dev`. Esse domínio está verificado no Resend e com rastreamento de abertura e clique desligado, então o link chega sem redirecionamento. A newsletter (parte B) usa `expert.guifav.dev`, com rastreamento ligado.
