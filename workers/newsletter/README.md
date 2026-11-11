# Worker `blog-newsletter`

Inscrição da newsletter do blog guifav.dev (issue #21, parte A). Recebe o formulário do blog, manda o email de confirmação e, quando o link é aberto, cadastra o contato no segmento do Resend.

| Rota | O que faz |
| --- | --- |
| `POST /subscribe` | Confere a origem, aplica o limite por IP, confere o Turnstile, valida o email, aplica o limite por endereço e manda o email com o link assinado. Responde sempre com 303 para `/blog/inscricao-enviada/`, exceto com Turnstile reprovado (`/blog/assinar/#erro-verificacao`). |
| `GET /confirm` | Valida o link (HMAC-SHA256, 48 h), cria ou reativa o contato no segmento e redireciona para `/blog/inscricao-confirmada/`. Link inválido ou expirado: `/blog/inscricao-invalida/`. |

Os limites (cerca de 5 pedidos por IP e 1 email por endereço a cada 60 s, no `wrangler.toml`) usam o rate limit nativo do Workers, que é aproximado e conta separado em cada localidade do Cloudflare. Junto com o Turnstile, eles contêm abuso, mas não são um teto global garantido.

Não há banco: o link carrega o email e a validade, e a assinatura prova a origem.

## Testes

```
npm test
```

Node 22.18 ou mais novo (TypeScript direto pelo `node --test`). Os testes rodam no workflow do Pages, junto com os do blog.

## Deploy

O deploy é manual, depois do merge, e não está no CI. O DNS de `guifav.dev` só muda com ok do Gui.

1. **Segmento** "IA Aplicada" no Resend. Copie o id para `SEGMENT_ID` no `wrangler.toml`. Com `SEGMENT_ID` vazio, o Worker responde 500 e não cadastra ninguém.
2. **Turnstile:** crie um widget para `guifav.dev`. A chave pública vai para `TURNSTILE_SITE_KEY` em `blog-src/src/consts.ts`; a secreta, para o segredo abaixo.
3. **Publicação:** `npx wrangler@4 deploy`. A rota `insights.guifav.dev` é um domínio próprio do Worker, e o Cloudflare cria o registro DNS do subdomínio.
4. **Segredos**, os três de uma vez (`npx wrangler@4 secret bulk`, lendo um JSON pela entrada padrão, sem o valor passar pela tela nem pelo histórico): `RESEND_API_KEY`, `TURNSTILE_SECRET` e `CONFIRM_SECRET` (`openssl rand -base64 48`). Confira com `npx wrangler@4 secret list`.
5. **Teste real** com um endereço que se consiga ler: inscrição no site publicado, email recebido de `insights@guifav.dev`, clique no link, contato no segmento. Confirmação repetida não duplica. Depois, o contato de teste sai.

O formulário (`NEWSLETTER_FORM_ENABLED` e a chave pública do Turnstile em `blog-src/src/consts.ts`) foi ligado no PR #34, junto com o primeiro deploy, em 2026-11-11.

## Remetente

A confirmação sai de `insights@guifav.dev`. Esse domínio está verificado no Resend e com rastreamento de abertura e clique desligado, então o link chega sem redirecionamento. A newsletter (parte B) usa `expert.guifav.dev`, com rastreamento ligado.
