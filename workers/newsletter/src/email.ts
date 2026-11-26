// Email de confirmação (issue #21, garantia A9): HTML e texto, um único link, o de confirmação.

/** Email normalizado (minúsculas, sem espaços nas pontas) ou null. Sem espaço, controle ou caractere de cabeçalho. */
export function normalizeEmail(raw: string): string | null {
  const e = raw.trim().toLowerCase();
  if (e.length > 254) return null;
  const label = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
  const re = new RegExp(`^[a-z0-9.!#$%&*+/=?^_\`{|}~-]{1,64}@${label}(?:\\.${label})+$`);
  return re.test(e) ? e : null;
}

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/**
 * Email de entrega de um material: um botão por arquivo (link assinado, 7 dias) e, só se a pessoa marcou a caixa
 * no formulário, o link de confirmação dos Expert Insights. Sem a caixa, nenhum link de inscrição.
 */
export function materialEmail(
  email: string,
  titulo: string,
  links: Array<{ rotulo: string; url: string }>,
  confirmLink: string | null,
): { subject: string; html: string; text: string } {
  const plural = links.length > 1;
  const subject = `${plural ? 'Seus links' : 'Seu link'}: ${titulo}`;
  const text = [
    'Olá,',
    '',
    `Aqui ${plural ? 'estão os links' : 'está o link'} para baixar o material que você pediu no guifav.dev: ${titulo}.`,
    `${plural ? 'Os links valem' : 'O link vale'} por 7 dias.`,
    '',
    ...links.flatMap((l) => [`${l.rotulo}: ${l.url}`]),
    '',
    ...(confirmLink
      ? ['Você também pediu para receber os Expert Insights, os artigos de Guilherme Favaron por email. Para confirmar, abra o link abaixo. Ele vale por 48 horas.', '', confirmLink, '']
      : []),
    `Este email foi enviado para ${email} porque alguém pediu o material com este endereço, e o pedido fica registrado. Se não foi você, escreva para contact@guifav.dev que eu apago o registro.`,
  ].join('\n');
  const botoes = links
    .map((l) => `<p style="margin:0 0 12px"><a href="${esc(l.url)}" style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:8px;font-size:16px">${esc(l.rotulo)}</a></p>`)
    .join('\n');
  const insights = confirmLink
    ? `<p style="margin:24px 0 12px;font-size:16px;line-height:1.5">Você também pediu para receber os Expert Insights, os artigos de Guilherme Favaron por email. Para confirmar, use o link abaixo. Ele vale por 48 horas.</p>
<p style="margin:0 0 12px"><a href="${esc(confirmLink)}" style="color:#1a1a1a;font-size:16px">Confirmar inscrição nos Expert Insights</a></p>`
    : '';
  const html = `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${esc(subject)}</title></head>
<body style="margin:0;padding:24px;background:#f6f6f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a">
<div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px">
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Olá,</p>
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Aqui ${plural ? 'estão os links' : 'está o link'} para baixar o material que você pediu no guifav.dev: <strong>${esc(titulo)}</strong>. ${plural ? 'Os links valem' : 'O link vale'} por 7 dias.</p>
${botoes}
${insights}
<p style="margin:24px 0 0;font-size:14px;line-height:1.5;color:#555555">Este email foi enviado para <strong>${esc(email)}</strong> porque alguém pediu o material com este endereço, e o pedido fica registrado. Se não foi você, escreva para <a href="mailto:contact@guifav.dev" style="color:inherit">contact@guifav.dev</a> que eu apago o registro.</p>
</div>
</body></html>`;
  return { subject, html, text };
}

export function confirmationEmail(email: string, link: string): { subject: string; html: string; text: string } {
  const subject = 'Confirme sua inscrição nos Expert Insights';
  const text = [
    'Olá,',
    '',
    `Recebemos um pedido para inscrever ${email} nos Expert Insights, os artigos de Guilherme Favaron por email.`,
    '',
    'Para confirmar, abra o link abaixo. Ele vale por 48 horas.',
    '',
    link,
    '',
    'Se não foi você, ignore este email: sem a confirmação, nada é cadastrado.',
  ].join('\n');
  const html = `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${esc(subject)}</title></head>
<body style="margin:0;padding:24px;background:#f6f6f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a">
<div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px">
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Olá,</p>
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Recebemos um pedido para inscrever <strong>${esc(email)}</strong> nos Expert Insights, os artigos de Guilherme Favaron por email.</p>
<p style="margin:0 0 24px;font-size:16px;line-height:1.5">Para confirmar, use o botão abaixo. O link vale por 48 horas.</p>
<p style="margin:0 0 24px"><a href="${esc(link)}" style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:8px;font-size:16px">Confirmar inscrição</a></p>
<p style="margin:0;font-size:14px;line-height:1.5;color:#555555">Se não foi você, ignore este email: sem a confirmação, nada é cadastrado.</p>
</div>
</body></html>`;
  return { subject, html, text };
}
