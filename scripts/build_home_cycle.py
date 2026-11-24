"""Gera o ciclo de dados e de IA da seção #temas da home (index.html): o SVG do desktop, o do celular, o CSS do
diagrama e as chaves cy_* do dicionário EN. É a fonte do diagrama; não edite o SVG à mão no index.html.

    python3 scripts/build_home_cycle.py           # reescreve o index.html
    python3 scripts/build_home_cycle.py --check   # falha se o index.html estiver diferente do que o gerador produz (CI)
"""
import math

PERIOD = 15.0
TOKENS = 3

# (chave, título, linhas) em PT e EN, no sentido horário a partir das fontes
STAGES = [
    ('fontes', ('Fontes', 'Sources'), [('CRM, ERP e planilhas', 'CRM, ERP and spreadsheets'), ('documentos e site', 'documents and website'), ('sistemas legados', 'legacy systems')]),
    ('int', ('Integração', 'Integration'), [('ETL e ELT', 'ETL and ELT'), ('APIs e CDC', 'APIs and CDC'), ('ingestão de documentos', 'document ingestion')]),
    ('dados', ('Dados', 'Data'), [('lakehouse em camadas', 'layered lakehouse'), ('bronze, prata e ouro', 'bronze, silver and gold'), ('índice vetorial', 'vector index')]),
    ('acc', ('Acesso com permissão', 'Permissioned access'), [('Text-to-SQL', 'Text-to-SQL'), ('RAG sobre documentos', 'RAG over documents'), ('MCP para os sistemas', 'MCP for the systems')]),
    ('ia', ('IA', 'AI'), [('modelos comerciais,', 'commercial, open or'), ('abertos ou ajustados', 'fine-tuned models'), ('agentes com revisão', 'agents with review')]),
    ('apps', ('Aplicações', 'Applications'), [('dashboards', 'dashboards'), ('analista de dados com IA', 'AI data analyst'), ('chatbot e automações', 'chatbot and automations')]),
]
TITLE = ('Ciclo de dados e de IA de uma empresa, que se retroalimenta', 'A company data and AI cycle that feeds back into itself')
DESC = ('Fontes, integração, dados, acesso com permissão, IA e aplicações, em sequência. O uso das aplicações gera dados que voltam às fontes e aprimora as ferramentas, e a governança cobre todo o ciclo.',
        'Sources, integration, data, permissioned access, AI and applications, in sequence. Using the applications creates data that flows back into the sources and improves the tools, and governance covers the whole cycle.')
GOV = (('GOVERNANÇA', 'GOVERNANCE'), [('permissões e auditoria', 'permissions and audit'), ('avaliação e LGPD', 'evaluation and data privacy'), ('custo sob controle', 'cost under control')])
GOV_M = [('permissões, auditoria e avaliação', 'permissions, audit and evaluation'), ('LGPD e custo sob controle', 'data privacy and cost under control')]
FB_T = ('RETROALIMENTAÇÃO', 'FEEDBACK LOOP')
FB = [('o uso gera dados novos e aprimora as ferramentas:', 'use creates new data and improves the tools:'),
      ('avaliação e feedback ajustam modelos, prompts e agentes', 'evaluation and feedback tune models, prompts and agents')]
FB_M = [('o uso gera dados novos e aprimora', 'use creates new data and improves'),
        ('as ferramentas: avaliação e feedback', 'the tools: evaluation and feedback'),
        ('ajustam modelos, prompts e agentes', 'tune models, prompts and agents')]

EN = {}
def en(key, value):
    """Registra a tradução; a mesma chave não pode ter dois textos em inglês."""
    if EN.get(key, value) != value:
        raise SystemExit(f'build_home_cycle: a chave {key} tem dois textos em inglês')
    EN[key] = value

def T(cls, x, y, key, pair, anchor='middle', extra=''):
    en(key, pair[1])
    a = '' if anchor == 'start' else f' text-anchor="{anchor}"'
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a}{extra} data-i18n="{key}">{pair[0]}</text>'

# ---------- ícones animados ----------
def vcube(cx, cy, s):
    h = s * 0.5
    hexa = f'M{cx} {cy - s} L{cx + s} {cy - h} L{cx + s} {cy + h} L{cx} {cy + s} L{cx - s} {cy + h} L{cx - s} {cy - h} Z'
    top = f'M{cx} {cy - s} L{cx + s} {cy - h} L{cx} {cy} L{cx - s} {cy - h} Z'
    o = [f'<path class="i-vc-f" d="{hexa}"/><path class="i-vc-t" d="{top}"/>',
         f'<path class="i-vc-y" d="M{cx} {cy} V{cy + s} M{cx} {cy} L{cx - s} {cy - h} M{cx} {cy} L{cx + s} {cy - h}"/>']
    ox, oy = cx - 1, cy + 3
    for dx, dy, dl, du in ((-6, -8, 0.0, 3.2), (7, -5, -0.9, 4.1), (5, 6, -1.7, 3.6), (-7, 3, -2.4, 4.6)):
        o.append(f'<g class="i-vc-v" style="transform-origin:{ox}px {oy}px;animation-delay:{dl}s;animation-duration:{du}s">'
                 f'<line x1="{ox}" y1="{oy}" x2="{ox + dx}" y2="{oy + dy}"/><circle cx="{ox + dx}" cy="{oy + dy}" r="2"/></g>')
    o.append(f'<circle class="i-vc-o" cx="{ox}" cy="{oy}" r="1.6"/><path class="i-vc-e" d="{hexa}"/>')
    return ''.join(o)

def icon(key, cx, cy):
    if key == 'fontes':
        o = []
        for k in range(3):
            y = cy - 10 + k * 9
            o.append(f'<path class="i-db-r" style="animation-delay:{k * .5:.1f}s" d="M{cx - 13} {y} a13 4.5 0 0 0 26 0 v6 a13 4.5 0 0 1 -26 0 z"/>')
        o.append(f'<ellipse class="i-db-top" cx="{cx}" cy="{cy - 10}" rx="13" ry="4.5"/>')
        return ''.join(o)
    if key == 'int':
        o = []
        for k, dy in enumerate((-10, 0, 10)):
            d = f'M{cx - 15} {cy + dy} C{cx - 4} {cy + dy} {cx - 4} {cy} {cx + 4} {cy} H{cx + 15}'
            o.append(f'<path class="i-pipe-b" d="{d}"/><path class="i-pipe-r" style="animation-delay:-{k * .4:.1f}s" d="{d}"/>')
        o.append(f'<circle class="i-pipe-end" cx="{cx + 15}" cy="{cy}" r="2.6"/>')
        return ''.join(o)
    if key == 'dados':
        return vcube(cx, cy, 15)
    if key == 'acc':
        return (f'<path class="i-lock-s" d="M{cx - 6} {cy - 2} v-5 a6 6 0 0 1 12 0 v5"/>'
                f'<rect class="i-lock-b" x="{cx - 10}" y="{cy - 2}" width="20" height="15" rx="3.5"/>'
                f'<circle class="i-lock-k" cx="{cx}" cy="{cy + 5}" r="2.2"/>')
    if key == 'ia':
        L = [(cx - 13, cy - 10), (cx - 13, cy), (cx - 13, cy + 10)]
        M = [(cx, cy - 6), (cx, cy + 6)]
        Rn = (cx + 13, cy)
        o = [f'<line class="i-net-e" x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}"/>' for a in L for b in M]
        o += [f'<line class="i-net-e" x1="{b[0]}" y1="{b[1]}" x2="{Rn[0]}" y2="{Rn[1]}"/>' for b in M]
        for k, p in enumerate(L + M + [Rn]):
            layer = 0 if k < 3 else (1 if k < 5 else 2)
            o.append(f'<circle class="i-net-n" style="animation-delay:{layer * .35:.2f}s" cx="{p[0]}" cy="{p[1]}" r="3.2"/>')
        return ''.join(o)
    if key == 'apps':
        o = []
        for k, h in enumerate((10, 17, 13)):
            o.append(f'<rect class="i-bar" style="animation-delay:-{k * .5:.1f}s" x="{cx - 15 + k * 7}" y="{cy + 11 - h}" width="5" height="{h}" rx="1.2"/>')
        o.append(f'<path class="i-chat" d="M{cx + 6} {cy - 12} h10 a3 3 0 0 1 3 3 v6 a3 3 0 0 1 -3 3 h-6 l-4 3 v-3 a3 3 0 0 1 -3 -3 v-6 a3 3 0 0 1 3 -3 z"/>')
        for k in range(3):
            o.append(f'<circle class="i-chat-d" style="animation-delay:{k * .2:.1f}s" cx="{cx + 7.5 + k * 4}" cy="{cy - 6}" r="1.2"/>')
        return ''.join(o)
    raise ValueError(f'ícone desconhecido: {key!r}')

def card(stage, cx, cy, delay, w, h, ty, ly, lstep):
    key, name, lines = stage
    x, y = cx - w / 2, cy - h / 2
    o = ['<g>', f'<rect class="d-card" x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="16"/>',
         f'<rect class="d-glow" style="animation-delay:{delay:.2f}s" x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="16"/>',
         f'<rect class="d-ico-bg" x="{x + 14:.1f}" y="{cy - 22:.1f}" width="44" height="44" rx="12"/>',
         f'<g aria-hidden="true">{icon(key, x + 36, cy)}</g>',
         T('d-t', x + 72, y + ty, f'cy_{key}_t', name, 'start')]
    for j, t in enumerate(lines):
        o.append(T('d-c', x + 72, y + ly + j * lstep, f'cy_{key}_{j + 1}', t, 'start'))
    o.append('</g>')
    return ''.join(o)

# ---------- trilho ----------
class Track:
    def __init__(self, start):
        self.p = start; self.segs = []; self.d = f'M{start[0]:.1f} {start[1]:.1f}'
    def line(self, q):
        self.segs.append(('L', self.p, q, math.dist(self.p, q))); self.d += f' L{q[0]:.1f} {q[1]:.1f}'; self.p = q
    def arc(self, c, r, a0, a1):
        q = (c[0] + r * math.cos(math.radians(a1)), c[1] + r * math.sin(math.radians(a1)))
        self.d += f' A{r} {r} 0 0 1 {q[0]:.1f} {q[1]:.1f}'
        self.segs.append(('A', c, r, a0, a1, math.radians(abs(a1 - a0)) * r)); self.p = q
    @property
    def length(self): return sum(s[-1] for s in self.segs)
    def at(self, s):
        s %= self.length
        for sg in self.segs:
            if s <= sg[-1]:
                if sg[0] == 'L':
                    t = s / sg[-1] if sg[-1] else 0; p0, p1 = sg[1], sg[2]
                    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t), math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
                _, c, r, a0, a1, L = sg
                a = a0 + (a1 - a0) * s / L
                return (c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a))), a + 90
            s -= sg[-1]
        return self.at(0)  # sobra de arredondamento no fim da volta: é o ponto de partida
    def find(self, pt):
        best = (1e9, 0); n = 2000
        for k in range(n):
            s = self.length * k / n
            d = math.dist(self.at(s)[0], pt)
            if d < best[0]: best = (d, s)
        return best[1]

def rrect(x0, y0, x1, y1, r, start):
    """Retângulo de cantos arredondados no sentido horário, começando em start (num lado reto de baixo)."""
    t = Track(start)
    t.line((x0 + r, y1)); t.arc((x0 + r, y1 - r), r, 90, 180)
    t.line((x0, y0 + r)); t.arc((x0 + r, y0 + r), r, 180, 270)
    t.line((x1 - r, y0)); t.arc((x1 - r, y0 + r), r, 270, 360)
    t.line((x1, y1 - r)); t.arc((x1 - r, y1 - r), r, 0, 90)
    t.line(start)
    return t

def common(t, centers, tid, fb_seg):
    """Trilho, setas e pontos; devolve as posições dos cartões ao longo do trilho."""
    L = t.length
    pos = [t.find(c) for c in centers]
    o = [f'<defs><path id="{tid}" d="{t.d} Z"/></defs>',
         f'<path class="d-trk" d="{t.d} Z"/>', f'<path class="d-trk-run" d="{t.d} Z"/>']
    for i in range(len(pos)):
        s0, s1 = pos[i], pos[(i + 1) % len(pos)]
        if s1 <= s0: s1 += L
        for frac in fb_seg if i == len(pos) - 1 else (0.5,):
            (x, y), ang = t.at(s0 + (s1 - s0) * frac)
            o.append(f'<path class="d-chev" transform="translate({x:.1f} {y:.1f}) rotate({ang:.1f})" d="M-5 -7 L4 0 L-5 7"/>')
    for k in range(TOKENS):
        o.append(f'<circle class="d-token" r="5.5"><animateMotion dur="{PERIOD:g}s" begin="-{k * PERIOD / TOKENS:.2f}s" repeatCount="indefinite"><mpath href="#{tid}"/></animateMotion></circle>')
    return o, pos

def delays(pos, L):
    step = PERIOD / TOKENS
    return [((s / L * PERIOD) % step) - step for s in pos]

def lock(cx, cy):
    return (f'<g class="d-lock" aria-hidden="true" transform="translate({cx} {cy})"><rect x="-7" y="-4" width="14" height="11" rx="2.5"/>'
            f'<path d="M-4.5 -4 v-3 a4.5 4.5 0 0 1 9 0 v3"/></g>')

def desktop():
    W, H = 1000, 750
    x0, x1, y0, y1, r = 150, 850, 104, 584, 150
    t = rrect(x0, y0, x1, y1, r, (500, y1))
    my = (y0 + y1) / 2
    centers = [(330, y1), (x0, my), (330, y0), (670, y0), (x1, my), (670, y1)]
    o = [f'<svg class="cy-d" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="cy-t-d" aria-describedby="cy-x-d" focusable="false">',
         f'<title id="cy-t-d" data-i18n="cy_title">{TITLE[0]}</title><desc id="cy-x-d" data-i18n="cy_desc">{DESC[0]}</desc>']
    trk, pos = common(t, centers, 'cy-trk-d', (0.5,))
    o += trk[:3]
    gx0, gx1, gy0, gy1 = 330, 670, my - 112, my + 112
    o.append(f'<rect class="d-gov-o" x="{gx0 - 18}" y="{gy0 - 18}" width="{gx1 - gx0 + 36}" height="{gy1 - gy0 + 36}" rx="64"/>'
             f'<rect class="d-orbit" x="{gx0 - 18}" y="{gy0 - 18}" width="{gx1 - gx0 + 36}" height="{gy1 - gy0 + 36}" rx="64"/>'
             f'<rect class="d-gov" x="{gx0}" y="{gy0}" width="{gx1 - gx0}" height="{gy1 - gy0}" rx="48"/>')
    o.append(lock(500, my - 50))
    o.append(T('d-gov-t', 500, my - 14, 'cy_gov_t', GOV[0]))
    for j, p in enumerate(GOV[1]):
        o.append(T('d-c', 500, my + 12 + j * 19, f'cy_gov_{j + 1}', p))
    o += trk[3:]
    for st, c, dl in zip(STAGES, centers, delays(pos, t.length)):
        o.append(card(st, c[0], c[1], dl, 262, 112, 32, 54, 18))
    (fx, fy), _ = t.at((pos[-1] + pos[0] + t.length) / 2)
    o.append(f'<path class="d-leader" d="M{fx:.1f} {fy + 10:.1f} V {y1 + 70:.1f}"/>')
    o.append(T('d-fb-t', fx, y1 + 92, 'cy_fb_t', FB_T))
    o.append(T('d-c', fx, y1 + 113, 'cy_fb_1', FB[0]))
    o.append(T('d-c', fx, y1 + 131, 'cy_fb_2', FB[1]))
    o.append('</svg>')
    return ''.join(o)

def mobile():
    W = 360
    CW, CH, G = 300, 118, 34
    cxm = 198
    y0 = 132
    cys = [y0 + i * (CH + G) + CH / 2 for i in range(6)]
    yt, yb, xl, r = y0 - 20, cys[-1] + CH / 2 + 20, 24, 18
    # trilho: desce pelo centro dos cartões, volta pela esquerda (a retroalimentação)
    t = Track((cxm, yt))
    t.line((cxm, yb - r)); t.arc((cxm - r, yb - r), r, 0, 90)
    t.line((xl + r, yb)); t.arc((xl + r, yb - r), r, 90, 180)
    t.line((xl, yt + r)); t.arc((xl + r, yt + r), r, 180, 270)
    t.line((cxm, yt))
    H = int(yb + 140)
    centers = [(cxm, c) for c in cys]
    o = [f'<svg class="cy-m" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="cy-t-m" aria-describedby="cy-x-m" focusable="false">',
         f'<title id="cy-t-m" data-i18n="cy_title">{TITLE[0]}</title><desc id="cy-x-m" data-i18n="cy_desc">{DESC[0]}</desc>']
    o.append(f'<rect class="d-gov-o" x="6" y="8" width="{W - 12}" height="{H - 16}" rx="28"/>')
    o.append(lock(28, 37))
    o.append(T('d-gov-t', 44, 42, 'cy_gov_t', GOV[0], 'start'))
    for j, p in enumerate(GOV_M):
        o.append(T('d-c', 22, 68 + j * 21, f'cy_gov_m{j + 1}', p, 'start'))
    trk, pos = common(t, centers, 'cy-trk-m', (0.18, 0.5, 0.82))
    o += trk
    for st, c, dl in zip(STAGES, centers, delays(pos, t.length)):
        o.append(card(st, c[0], c[1], dl, CW, CH, 36, 61, 21))
    o.append(T('d-fb-t', W / 2, yb + 40, 'cy_fb_t', FB_T))
    for j, p in enumerate(FB_M):
        o.append(T('d-c', W / 2, yb + 64 + j * 21, f'cy_fbm_{j + 1}', p))
    o.append('</svg>')
    return ''.join(o)

CSS = f'''    .dflow{{margin:12px 0 32px}}
    @media(min-width:1000px){{.dflow{{width:min(1120px,calc(100vw - 48px));margin-left:calc(50% - min(560px,50vw - 24px))}}}}
    .dflow-wrap{{border:1px solid var(--border);border-radius:18px;background:var(--bg-card);padding:20px 16px}}
    .dflow svg{{display:block;width:100%;height:auto;font-family:inherit}}
    .dflow .cy-m{{display:none}}
    @media(max-width:999px){{.dflow .cy-d{{display:none}}.dflow .cy-m{{display:block;max-width:440px;margin:0 auto}}.dflow-wrap{{padding:12px 6px}}}}
    .dflow .d-t{{font-size:15px;font-weight:650;fill:var(--text)}}
    .dflow .d-c{{font-size:12.5px;fill:var(--text-secondary)}}
    .dflow .cy-m .d-t{{font-size:18px}}
    .dflow .cy-m .d-c{{font-size:15px}}
    .dflow .cy-m .d-gov-t,.dflow .cy-m .d-fb-t{{font-size:15px}}
    .dflow .d-card{{fill:var(--bg);stroke:var(--border);stroke-width:1.2}}
    .dflow .d-glow{{fill:none;stroke:var(--hl);stroke-width:3;opacity:0;animation:d-glow {PERIOD / TOKENS:g}s ease-out infinite}}
    .dflow .d-ico-bg{{fill:var(--hl);fill-opacity:.1}}
    .dflow .d-trk{{fill:none;stroke:var(--border);stroke-width:2.4}}
    .dflow .d-trk-run{{fill:none;stroke:var(--hl);stroke-width:2.4;stroke-linecap:round;stroke-dasharray:3 18;opacity:.9;animation:d-run 4s linear infinite}}
    .dflow .d-chev{{fill:none;stroke:var(--hl);stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}}
    .dflow .d-token{{fill:var(--hl);stroke:var(--bg-card);stroke-width:2.5}}
    .dflow .d-gov{{fill:var(--bg);stroke:var(--border);stroke-width:1.2}}
    .dflow .d-gov-o{{fill:none;stroke:var(--border);stroke-width:1;stroke-dasharray:1 7}}
    .dflow .d-orbit{{fill:none;stroke:var(--hl);stroke-width:2.2;stroke-dasharray:2 19;stroke-linecap:round;opacity:.8;animation:d-run 6s linear infinite}}
    .dflow .d-gov-t{{font-size:13px;font-weight:800;letter-spacing:2.2px;fill:var(--text)}}
    .dflow .d-fb-t{{font-size:12.5px;font-weight:800;letter-spacing:2.2px;fill:var(--hl-text)}}
    .dflow .d-leader{{stroke:var(--hl);stroke-opacity:.6;stroke-width:1.2;stroke-dasharray:2 3}}
    .dflow .d-lock{{fill:none;stroke:var(--text-secondary);stroke-width:1.6;opacity:.7}}
    .dflow .i-db-r{{fill:var(--bg);stroke:var(--text-secondary);stroke-width:1.3;animation:i-fill 1.5s ease-in-out infinite}}
    .dflow .i-db-top{{fill:var(--bg);stroke:var(--text-secondary);stroke-width:1.3}}
    .dflow .i-pipe-b{{fill:none;stroke:var(--text-secondary);stroke-width:1.4;opacity:.55}}
    .dflow .i-pipe-r{{fill:none;stroke:var(--hl);stroke-width:2;stroke-linecap:round;stroke-dasharray:3 11;animation:d-run 1.6s linear infinite}}
    .dflow .i-pipe-end{{fill:var(--hl)}}
    .dflow .i-vc-f{{fill:var(--bg-card);fill-opacity:.6}}
    .dflow .i-vc-t{{fill:var(--hl);fill-opacity:.14}}
    .dflow .i-vc-y{{fill:none;stroke:var(--text-secondary);stroke-width:1;stroke-opacity:.35;stroke-dasharray:2 2}}
    .dflow .i-vc-e{{fill:none;stroke:var(--text-secondary);stroke-width:1.3;stroke-linejoin:round}}
    .dflow .i-vc-v{{transform-box:view-box;animation:i-swing 4s ease-in-out infinite alternate}}
    .dflow .i-vc-v line{{stroke:var(--hl);stroke-width:1.6;stroke-linecap:round}}
    .dflow .i-vc-v circle{{fill:var(--hl)}}
    .dflow .i-vc-o{{fill:var(--text-secondary)}}
    .dflow .i-lock-b{{fill:var(--bg);stroke:var(--text-secondary);stroke-width:1.5}}
    .dflow .i-lock-s{{fill:none;stroke:var(--text-secondary);stroke-width:1.5;animation:i-shackle 3s ease-in-out infinite}}
    .dflow .i-lock-k{{fill:var(--hl);animation:d-live 3s ease-in-out infinite}}
    .dflow .i-net-e{{stroke:var(--text-secondary);stroke-width:1;opacity:.45}}
    .dflow .i-net-n{{fill:var(--hl);animation:d-live 1.4s ease-in-out infinite}}
    .dflow .i-bar{{fill:var(--hl);transform-box:fill-box;transform-origin:50% 100%;animation:i-grow 3.4s ease-in-out infinite}}
    .dflow .i-chat{{fill:var(--bg);stroke:var(--text-secondary);stroke-width:1.3}}
    .dflow .i-chat-d{{fill:var(--text-secondary);animation:d-live 1.2s ease-in-out infinite}}
    @keyframes d-run{{to{{stroke-dashoffset:-42}}}}
    @keyframes d-live{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}
    @keyframes d-glow{{0%{{opacity:0}}6%{{opacity:.9}}30%,100%{{opacity:0}}}}
    @keyframes i-fill{{0%,100%{{fill:var(--bg)}}40%{{fill:var(--hl)}}}}
    @keyframes i-shackle{{0%,55%,100%{{transform:translateY(0)}}70%,85%{{transform:translateY(-3px)}}}}
    @keyframes i-grow{{0%,100%{{transform:scaleY(.55)}}50%{{transform:scaleY(1)}}}}
    @keyframes i-swing{{from{{transform:rotate(-24deg)}}to{{transform:rotate(24deg)}}}}
    .dflow-legend{{list-style:none;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px 32px}}
    .dflow-legend li{{border-top:2px solid var(--hl);padding-top:12px}}
    .dflow-legend a{{font-size:15px;font-weight:650;line-height:1.35;color:var(--text);text-decoration:none}}
    .dflow-legend a:hover{{color:var(--hl-text);text-decoration:underline;text-underline-offset:2px}}
    .dflow-legend p{{font-size:13.5px;line-height:1.55;color:var(--text-secondary);margin-top:4px}}
    @media(max-width:999px){{.dflow-legend{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(max-width:560px){{.dflow-legend{{grid-template-columns:1fr}}}}
    @media(prefers-reduced-motion:reduce){{.dflow *{{animation:none!important}}.dflow .d-token{{display:none}}}}
'''


# ---------- aplicação no index.html ----------
import json, re, sys
from pathlib import Path

FIG_RE = re.compile(r'(      <figure class="dflow">\n).*?(      </figure>\n)', re.S)
CSS_RE = re.compile(r'    \.dflow\{margin:12px 0 32px\}\n.*?    @media\(prefers-reduced-motion:reduce\)\{\.dflow \*\{animation:none!important\}[^\n]*\n', re.S)
EN_RE = re.compile(r'(<script type="application/json" id="i18n-en">)(\{.*?\n  \})(</script>)', re.S)

def dump(dd):
    return '{\n' + ',\n'.join(f'      {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}' for k, v in dd.items()) + '\n  }'

def render(html):
    EN.clear()
    d, m = desktop(), mobile()
    en('cy_title', TITLE[1])
    en('cy_desc', DESC[1])
    fig = '      <figure class="dflow">\n        <div class="dflow-wrap">\n          ' + d + '\n          ' + m + '\n        </div>\n      </figure>\n'
    for rx in (FIG_RE, CSS_RE, EN_RE):
        n = len(rx.findall(html))
        if n != 1:
            raise SystemExit(f'build_home_cycle: esperava 1 trecho para {rx.pattern[:40]!r}, achei {n}')
    html = FIG_RE.sub(lambda _: fig, html)
    html = CSS_RE.sub(lambda _: CSS, html)
    mo = EN_RE.search(html)
    data = json.loads(mo.group(2))
    if dump(data) != mo.group(2):
        raise SystemExit('build_home_cycle: o dicionário EN saiu do formato esperado (6 espaços por chave)')
    data = {k: v for k, v in data.items() if not k.startswith('cy_')}
    data.update(EN)
    html = html[:mo.start(2)] + dump(data) + html[mo.end(2):]
    used = set(re.findall(r'data-i18n(?:-alt|-aria)?="(cy_[^"]+)"', html))
    if used != set(EN):
        raise SystemExit(f'build_home_cycle: chaves cy_ sem par: {sorted(used ^ set(EN))}')
    return html

HOME = Path(__file__).resolve().parent.parent / 'index.html'

def main(argv, path=HOME):
    import argparse
    ap = argparse.ArgumentParser(description='Gera o ciclo de dados e de IA da home.')
    ap.add_argument('--check', action='store_true', help='só confere; falha se o index.html estiver diferente do gerador')
    args = ap.parse_args(argv)
    cur = path.read_text(encoding='utf-8')
    new = render(cur)
    if args.check:
        if new != cur:
            print('build_home_cycle: index.html difere do gerador; rode python3 scripts/build_home_cycle.py', file=sys.stderr)
            return 1
        print('build_home_cycle: index.html em dia com o gerador')
        return 0
    path.write_text(new, encoding='utf-8')
    print('build_home_cycle: index.html atualizado')
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
