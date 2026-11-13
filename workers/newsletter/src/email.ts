// Email de confirmação (issue #21, garantia A9): HTML e texto, um único link, o de confirmação.
const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

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
