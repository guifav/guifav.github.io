// D1 falso em SQLite real (node:sqlite) com as migrações do painel (workers/admin/migrations), donas do esquema.
// Mesmo desenho do D1 falso dos testes do painel. `falha` derruba as instruções cujo SQL casar com a expressão, com a
// mensagem `mensagem` (por padrão, uma que cita um email, para provar que o log o remove).
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import type { D1Like, D1Stmt } from '../src/conversoes.ts';

export function d1(falha?: RegExp, mensagem = 'D1_ERROR: UNIQUE constraint failed for leitor@exemplo.com', pendura?: RegExp,
  porta?: Promise<void>): D1Like & { raw: DatabaseSync } {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../../admin/migrations/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => stmt(sql, v),
    run: async () => {
      if (pendura?.test(sql)) return new Promise(() => {}); // D1 que nunca responde
      if (porta) await porta; // D1 que só grava quando o teste libera
      if (falha?.test(sql)) throw new Error(mensagem);
      return db.prepare(sql).run(...(args as never[]));
    },
    first: async <T>() => {
      if (pendura?.test(sql)) return new Promise<T | null>(() => {});
      if (porta) await porta;
      if (falha?.test(sql)) throw new Error(mensagem);
      const r = db.prepare(sql).get(...(args as never[]));
      return (r ? JSON.parse(JSON.stringify(r)) : null) as T;
    },
  });
  return { raw: db, prepare: (sql: string) => stmt(sql) };
}

/** Linhas de uma tabela, como objetos simples. */
export const rows = (db: DatabaseSync, table: string) =>
  JSON.parse(JSON.stringify(db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all())) as Record<string, any>[];

/** Captura o console.error durante fn. */
export async function capturaErros(fn: () => Promise<unknown>): Promise<string[]> {
  const out: string[] = [];
  const orig = console.error;
  console.error = (...a: unknown[]) => void out.push(a.map(String).join(' '));
  try {
    await fn();
  } finally {
    console.error = orig;
  }
  return out;
}

/** A mesma assinatura base64url com outros bits de sobra no último caractere: outra string, os mesmos bytes. */
export function variantes(s: string): string[] {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const i = A.indexOf(s.at(-1)!);
  return [1, 2, 3].map((d) => s.slice(0, -1) + A[(i & ~3) | ((i + d) & 3)]);
}

/** As colunas de origem (issue #114, migração 0006) de uma linha sem origem: anterior à #114 ou sem de onde herdar. */
export const SEM_ORIGEM = { origin_channel: null, origin_source: null, utm_source: null, utm_medium: null, utm_campaign: null,
  landing_path: null } as const;
