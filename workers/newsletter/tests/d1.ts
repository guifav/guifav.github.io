// D1 falso em SQLite real (node:sqlite) com as migrações do painel (workers/admin/migrations), donas do esquema.
// Mesmo desenho do D1 falso dos testes do painel. `falha` derruba as instruções cujo SQL casar com a expressão.
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import type { D1Like, D1Stmt } from '../src/conversoes.ts';

export function d1(falha?: RegExp): D1Like & { raw: DatabaseSync } {
  const db = new DatabaseSync(':memory:');
  const dir = new URL('../../admin/migrations/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) db.exec(readFileSync(new URL(f, dir), 'utf-8'));
  const stmt = (sql: string, args: unknown[] = []): D1Stmt => ({
    bind: (...v: unknown[]) => stmt(sql, v),
    run: async () => {
      if (falha?.test(sql)) throw new Error('D1_ERROR: indisponível');
      return db.prepare(sql).run(...(args as never[]));
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
