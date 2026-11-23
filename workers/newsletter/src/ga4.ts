// Evento de download de material no GA4 pelo Measurement Protocol (issue #99). O download sai direto do Worker, sem
// página do site, então o evento parte daqui. Nunca leva email: só o material, o arquivo e o client_id do navegador
// do pedido, que existe só para quem aceitou os cookies. Sem ele, o client_id é aleatório e o evento vale como
// contagem anônima. Melhor esforço: roda depois da resposta (waitUntil) e nunca rejeita.

const MP = 'https://www.google-analytics.com/mp/collect';

/** client_id do GA4 vindo do formulário (os dois últimos números do cookie _ga), ou null. */
export function parseGaClientId(raw: string | null): string | null {
  const v = (raw ?? '').trim();
  return /^\d{1,20}\.\d{1,20}$/.test(v) ? v : null;
}

/** client_id descartável para quem recusou os cookies: não liga este evento a nenhum outro. */
export function anonClientId(nowMs: number): string {
  const r = crypto.getRandomValues(new Uint32Array(1))[0];
  return `${r}.${Math.floor(nowMs / 1000)}`;
}

export async function sendDownloadEvent(
  cfg: { measurementId: string; apiSecret?: string },
  fetchFn: typeof fetch,
  e: { clientId: string | null; material: string; file: string; nowMs: number },
): Promise<void> {
  if (!cfg.measurementId || !cfg.apiSecret) {
    console.error('evento do GA4 não enviado: GA4_MEASUREMENT_ID ou GA4_API_SECRET ausente');
    return;
  }
  // O segredo vai na URL (exigência do Measurement Protocol): a URL nunca entra em log.
  const url = `${MP}?${new URLSearchParams({ measurement_id: cfg.measurementId, api_secret: cfg.apiSecret })}`;
  const body = {
    client_id: e.clientId ?? anonClientId(e.nowMs),
    // Só medição: nada para anúncios, mesmo de quem aceitou os cookies.
    consent: { ad_user_data: 'DENIED', ad_personalization: 'DENIED' },
    events: [{ name: 'material_download', params: { material_id: e.material, file_id: e.file, engagement_time_msec: 1 } }],
  };
  try {
    const r = await fetchFn(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) console.error(`evento do GA4 não enviado: HTTP ${r.status}`);
  } catch (err) {
    console.error(`evento do GA4 não enviado: ${(err as Error)?.name ?? 'erro'}`);
  }
}
