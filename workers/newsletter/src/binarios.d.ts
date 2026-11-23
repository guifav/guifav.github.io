// Arquivos dos materiais importados como módulos de dados ([[rules]] type = "Data" no wrangler.toml).
declare module '*.xlsx' { const bytes: ArrayBuffer; export default bytes; }
declare module '*.pdf' { const bytes: ArrayBuffer; export default bytes; }
