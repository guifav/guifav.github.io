// Resolve URLs pelo parser WHATWG do Node (o mesmo comportamento do navegador).
//
// Usado por scripts/check_site.py: entrada padrão com um array JSON de pares [base, ref];
// saída padrão com um array JSON na mesma ordem, cada item {protocol, hostname, pathname}
// ou null quando a URL não é válida. Um único processo por execução do verificador.
import { readFileSync } from 'node:fs';

const pairs = JSON.parse(readFileSync(0, 'utf-8'));
if (!Array.isArray(pairs)) throw new Error('entrada precisa ser um array de [base, ref]');
const out = pairs.map(([base, ref]) => {
  try {
    const u = new URL(ref, base);
    return { protocol: u.protocol, hostname: u.hostname, pathname: u.pathname };
  } catch {
    return null;
  }
});
process.stdout.write(JSON.stringify(out));
