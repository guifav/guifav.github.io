# Worker `blog-admin`

Painel de assinantes da newsletter em `guifav.dev/admin` (issue #40). Só o Gui tem acesso.

| Rota | O que faz |
| --- | --- |
| `GET /admin` | Lista os assinantes do segmento "IA Aplicada", com busca por email, filtros (status, origem, pago) e contadores, em páginas de 100. |
| `GET /admin/export.csv` | Os mesmos filtros, em CSV, sem paginação. Células que começam com `=`, `+`, `-` ou `@` ganham `'` na frente, para não virarem fórmula. |
| `POST /admin/unsubscribe` | Descadastra no Resend (`unsubscribed=true`) e grava a auditoria `admin_unsubscribed_at` no contato. Só mostra sucesso se a releitura no Resend confirmar, e só então atualiza a cópia. Exige a caixa "confirmo" e `Origin` igual ao do site. |
| cron `17 * * * *` | Copia o segmento do Resend para o D1 `blog-admin`, com um GET por contato (a listagem do Resend não traz propriedades), a 4 req/s. As linhas antigas só saem depois que todas as novas foram gravadas: uma falha no meio não apaga nada e fica registrada no painel, sem email. |

Qualquer outro caminho que a rota `guifav.dev/admin*` pegue (ex.: `/administrar`) segue para o GitHub Pages.

## Acesso

1. **Cloudflare Access:** aplicação "Painel de assinantes (guifav.dev/admin)", com allow só para o email do Gui, login por PIN no email e sessão de 24 h.
2. **No Worker:** confere de novo o JWT do Access (`Cf-Access-Jwt-Assertion`):
   - assinatura RS256 com as chaves de `guifav.cloudflareaccess.com/cdn-cgi/access/certs`;
   - emissor, `aud` da aplicação (`ACCESS_AUD`) e validade;
   - email em `ALLOWED_EMAILS`.

   Sem isso, toda rota do painel responde 403, mesmo se a aplicação do Access for apagada ou mal configurada.

## Testes

```
npm test
```

Node 22.18 ou mais novo. O D1 dos testes é SQLite de verdade (`node:sqlite`), com a migração do repo. O Resend falso devolve as propriedades no formato documentado (`{value, type}`). Os JWTs são assinados com RSA.

## Deploy

O deploy é manual, depois do merge. O proxy da Cloudflare em `guifav.dev` já está ligado (2026-11-11), e a aplicação do Access e o D1 já existem.

1. **Chave do Resend dedicada:** `POST /api-keys` com `full_access`, nome `blog-admin`, gravada direto num arquivo 0600 em `/root/.hermes/credentials/`.
2. **Tabelas:** `npx wrangler@4 d1 migrations apply blog-admin --remote`.
3. **Publicação:** `npx wrangler@4 deploy`.
4. **Segredos:** `RESEND_API_KEY` e `ALLOWED_EMAILS`, de uma vez (`npx wrangler@4 secret bulk`, lendo JSON pela entrada padrão).
5. **Primeira cópia:** rodar o cron uma vez pelo painel da Cloudflare, ou esperar o minuto 17, e conferir `last_sync_count` no D1.
6. **Conferência:**
   - sem login, `/admin` vai para o Access;
   - com JWT inválido direto no Worker, responde 403;
   - o resto do site continua idêntico (comparação byte a byte);
   - com login do Gui, o painel mostra os contadores do Resend.
