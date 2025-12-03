// Global variables
let allModels = [];
let filteredModels = [];
let charts = {};
let selectedComparison = [];
let arenasData = {};
let analyticsControls = { versatilityTopN: 3, crTopN: 3, orgFilter: [] };
let categoriesCount = 0;

// Category mapping
const categories = {
    overall: 'Overall',
    expert: 'Expert',
    hardPrompts: 'Hard Prompts',
    coding: 'Coding',
    math: 'Math',
    creativeWriting: 'Creative Writing',
    instructionFollowing: 'Instruction Following',
    longerQuery: 'Longer Query'
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    setupEventListeners();
    loadData();
});

// Theme Management
function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
    
    if (Object.keys(charts).length > 0) {
        updateAllCharts();
    }
}

function updateThemeIcon(theme) {
    const icon = document.querySelector('.theme-icon');
    icon.textContent = theme === 'light' ? '🌙' : '☀️';
}

// Event Listeners
function setupEventListeners() {
    document.getElementById('themeToggle').addEventListener('click', toggleTheme);
    
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });
    
    document.getElementById('searchInput').addEventListener('input', filterTable);
    document.getElementById('categoryFilter').addEventListener('change', filterTable);
    const tf = document.getElementById('typeFilter');
    if (tf) tf.addEventListener('change', filterTable);
    document.getElementById('exportBtn').addEventListener('click', exportToCSV);
    
    document.getElementById('compareSearch').addEventListener('input', handleComparisonSearch);
    
    document.querySelectorAll('.sortable').forEach(th => {
        th.addEventListener('click', () => sortTable(th.dataset.sort));
    });

    document.addEventListener('change', (e) => {
        if (e.target && e.target.id === 'versatilityTopN') {
            analyticsControls.versatilityTopN = parseInt(e.target.value);
            renderDecisionMap();
        }
        if (e.target && e.target.id === 'crTopN') {
            analyticsControls.crTopN = parseInt(e.target.value);
            renderArenaDifficulty();
        }
        if (e.target && e.target.id === 'orgFilterSelect') {
            const opts = Array.from(e.target.selectedOptions).map(o => o.value);
            analyticsControls.orgFilter = opts;
            renderOrgDomain();
        }
    });
}

// Load and Parse Data
async function loadData() {
    showLoading(true);
    try {
        const response = await fetch('arena_overview.csv');
        const text = await response.text();
        allModels = parseCSV(text);
        filteredModels = [...allModels];
        await loadArenaData();
        renderAll();
        showLoading(false);
    } catch (error) {
        console.error('Error loading data:', error);
        showLoading(false);
        alert('Erro ao carregar os dados. Verifique se o arquivo arena_overview.csv existe.');
    }
}

function parseCSV(text) {
    const lines = text.split('\n').map(line => line.trim()).filter(line => line);
    if (lines.length < 2) return [];
    const header = lines[0].split(',').map(h => h.trim());
    const categoryColumns = ['overall','expert','hard_prompts','coding','math','creative_writing','instruction_following','longer_query'];
    categoriesCount = categoryColumns.filter(col => header.includes(col)).length;
    const idx = (name) => header.indexOf(name);
    const iModel = idx('model');
    const iOverall = idx('overall');
    const iExpert = idx('expert');
    const iHard = idx('hard_prompts');
    const iCoding = idx('coding');
    const iMath = idx('math');
    const iCreative = idx('creative_writing');
    const iInstruction = idx('instruction_following');
    const iLonger = idx('longer_query');

    const models = [];
    for (let i = 1; i < lines.length; i++) {
        const cols = lines[i].split(',').map(c => c.trim());
        const name = cols[iModel];
        const overall = parseRank(cols[iOverall]);
        if (!name || !overall) continue;
        const expert = parseRank(cols[iExpert]);
        const hardPrompts = parseRank(cols[iHard]);
        const coding = parseRank(cols[iCoding]);
        const math = parseRank(cols[iMath]);
        const creativeWriting = parseRank(cols[iCreative]);
        const instructionFollowing = parseRank(cols[iInstruction]);
        const longerQuery = parseRank(cols[iLonger]);

        models.push({
            name,
            overall,
            expert,
            hardPrompts,
            coding,
            math,
            creativeWriting,
            instructionFollowing,
            longerQuery
        });
    }
    return models;
}

function parseRank(value) {
    if (!value || value === '-') return null;
    const parsed = parseInt(value);
    return isNaN(parsed) ? null : parsed;
}

async function loadArenaData() {
    const files = {
        text: 'text_arena.csv',
        vision: 'vision_arena.csv',
        t2i: 'text-to-image_arena.csv',
        t2v: 'text-to-video_arena.csv',
        image_edit: 'image-edit_arena.csv',
        webdev: 'webdev_arena.csv',
        search: 'search_arena.csv'
    };
    const entries = Object.entries(files);
    const res = await Promise.all(entries.map(([k, f]) => fetch(f).then(r => r.text()).catch(() => null)));
    arenasData = {};
    for (let i = 0; i < entries.length; i++) {
        const key = entries[i][0];
        const txt = res[i];
        arenasData[key] = txt ? parseArenaCSV(txt) : [];
    }
}

function parseArenaCSV(text) {
    const lines = text.split('\n').map(l => l.trim()).filter(l => l);
    if (lines.length < 2) return [];
    const h = lines[0].split(',').map(s => s.trim());
    const idx = (n) => h.indexOf(n);
    const iRank = idx('Rank');
    const iUp = idx('Rank_Spread_Upper');
    const iLo = idx('Rank_Spread_Lower');
    const iModel = idx('Model');
    const iScore = idx('Score');
    const iCI = idx('CI_95');
    const iVotes = idx('Votes');
    const iOrg = idx('Organization');
    const iLic = idx('License');
    const out = [];
    for (let i = 1; i < lines.length; i++) {
        const c = lines[i].split(',').map(s => s.trim());
        const rank = parseInt(c[iRank]);
        const model = c[iModel];
        if (!model || isNaN(rank)) continue;
        out.push({
            rank,
            rankSpreadUpper: parseInt(c[iUp]),
            rankSpreadLower: parseInt(c[iLo]),
            model,
            score: parseFloat(c[iScore]),
            ci95: parseFloat(c[iCI]),
            votes: parseInt(c[iVotes]),
            organization: c[iOrg],
            license: c[iLic]
        });
    }
    return out;
}

// Rendering
function renderAll() {
    updateStatsCards();
    renderOverview();
    renderRankingsTable();
    renderCategoriesView();
    renderTypesView();
    renderCompaniesView();
    updateLastUpdate();
}

function updateStatsCards() {
    document.getElementById('totalModels').textContent = allModels.length;
    
    if (allModels.length > 0) {
        const topModel = allModels.reduce((best, model) => 
            model.overall < best.overall ? model : best
        );
        document.getElementById('topModel').textContent = topModel.name;
    }

    const catEl = document.getElementById('categoriesCount');
    if (catEl) catEl.textContent = categoriesCount;
}

function updateLastUpdate() {
    const now = new Date();
    const dateStr = now.toLocaleDateString('pt-BR');
    document.getElementById('lastUpdate').textContent = dateStr;
    const footerEl = document.getElementById('footerDate');
    if (footerEl) footerEl.textContent = dateStr;
}

// Overview Tab
function renderOverview() {
    const top10 = [...allModels].sort((a, b) => (a.overall || Infinity) - (b.overall || Infinity)).slice(0, 10);
    renderTop10OverviewTable(top10);
    renderWorldMap();
    updateLeaders();
}

function renderTop10Chart(models) {
    const ctx = document.getElementById('top10Chart');
    if (!ctx) return;
    
    if (charts.top10) charts.top10.destroy();
    
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    
    charts.top10 = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: models.map(m => m.name),
            datasets: [{
                label: 'Ranking Overall',
                data: models.map(m => m.overall),
                backgroundColor: '#ffbe00',
                borderColor: '#ffbe00',
                borderWidth: 2
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (context) => `Ranking: ${context.parsed.x}`
                    }
                }
            },
            scales: {
                x: {
                    reverse: true,
                    ticks: { color: textColor },
                    grid: { color: gridColor }
                },
                y: {
                    ticks: { color: textColor },
                    grid: { color: gridColor }
                }
            }
        }
    });
}

function renderDistributionChart() {
    const ctx = document.getElementById('distributionChart');
    if (!ctx) return;
    
    if (charts.distribution) charts.distribution.destroy();
    
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    
    const ranges = {
        'Top 10': 0,
        '11-50': 0,
        '51-100': 0,
        '101-200': 0,
        '201+': 0
    };
    
    allModels.forEach(model => {
        const rank = model.overall;
        if (rank <= 10) ranges['Top 10']++;
        else if (rank <= 50) ranges['11-50']++;
        else if (rank <= 100) ranges['51-100']++;
        else if (rank <= 200) ranges['101-200']++;
        else ranges['201+']++;
    });
    
    charts.distribution = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: Object.keys(ranges),
            datasets: [{
                data: Object.values(ranges),
                backgroundColor: [
                    '#ffbe00',
                    '#ffd966',
                    '#ffe699',
                    '#fff2cc',
                    '#fffbf0'
                ],
                borderWidth: 2,
                borderColor: theme === 'dark' ? '#000000' : '#ffffff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: textColor }
                }
            }
        }
    });
}

function renderWorldMap() {
    const container = document.getElementById('worldMap');
    if (!container || typeof L === 'undefined') return;
    if (charts.worldMap) { charts.worldMap.remove(); charts.worldMap = null; }

    const map = L.map('worldMap', { worldCopyJump: true }).setView([20, 0], 2);
    charts.worldMap = map;
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    setTimeout(() => map.invalidateSize(), 0);

    const countryCoords = {
        'USA': [37.0902, -95.7129],
        'China': [35.8617, 104.1954],
        'France': [46.2276, 2.2137],
        'Israel': [31.0461, 34.8516],
        'UK': [55.3781, -3.4360],
        'Germany': [51.1657, 10.4515],
        'Canada': [56.1304, -106.3468],
        'Japan': [36.2048, 138.2529]
    };

    const originMap = {
        'llama': 'USA',
        'gemini': 'USA',
        'gpt': 'USA',
        'claude': 'USA',
        'mistral': 'France',
        'mixtral': 'France',
        'qwen': 'China',
        'qwen2': 'China',
        'qwen3': 'China',
        'glm': 'China',
        'hunyuan': 'China',
        'deepseek': 'China',
        'yi': 'China',
        'amazon-nova': 'USA',
        'olmo': 'USA',
        'gemma': 'USA',
        'phi': 'USA',
        'grok': 'USA',
        'granite': 'USA',
        'jamba': 'Israel',
        'openchat': 'USA',
        'reka': 'USA',
        'wizardlm': 'China',
        'flux': 'Germany',
        'command': 'Canada',
        'c4ai': 'Canada'
    };
    const orgMap = {
        'llama': 'Meta',
        'gemini': 'Google',
        'gpt': 'OpenAI',
        'claude': 'Anthropic',
        'mistral': 'Mistral',
        'mixtral': 'Mistral',
        'qwen': 'Alibaba',
        'qwen2': 'Alibaba',
        'qwen3': 'Alibaba',
        'glm': 'Zhipu',
        'hunyuan': 'Tencent',
        'deepseek': 'DeepSeek',
        'yi': '01 AI',
        'amazon-nova': 'Amazon',
        'olmo': 'Allen AI',
        'gemma': 'Google',
        'phi': 'Microsoft',
        'grok': 'xAI',
        'granite': 'IBM',
        'jamba': 'AI21 Labs',
        'openchat': 'OpenChat',
        'reka': 'Reka AI',
        'wizardlm': 'Microsoft',
        'flux': 'Black Forest Labs',
        'command': 'Cohere',
        'c4ai': 'Cohere'
    };
    const hqCoords = {
        'OpenAI': [37.7749, -122.4194],
        'Google': [37.3861, -122.0839],
        'Anthropic': [37.7749, -122.4194],
        'Meta': [37.4529, -122.1817],
        'Microsoft': [47.6730, -122.1215],
        'xAI': [37.7749, -122.4194],
        'IBM': [41.1266, -73.7147],
        'Amazon': [47.6062, -122.3321],
        'AI21 Labs': [32.0853, 34.7818],
        'Allen AI': [47.6062, -122.3321],
        'Mistral': [48.8566, 2.3522],
        'Alibaba': [30.2741, 120.1551],
        'Tencent': [22.5431, 114.0579],
        'Zhipu': [39.9042, 116.4074],
        'DeepSeek': [39.9042, 116.4074],
        '01 AI': [39.9042, 116.4074],
        'OpenChat': [37.7749, -122.4194],
        'Reka AI': [37.4852, -122.2364],
        'Black Forest Labs': [52.5200, 13.4050],
        'Cohere': [43.6532, -79.3832]
    };

    const counts = {};
    const companies = {};
    const counted = new Set();
    const addCount = (country, org, nameKey) => {
        if (!country) return;
        if (nameKey && counted.has(nameKey)) return;
        if (nameKey) counted.add(nameKey);
        counts[country] = (counts[country] || 0) + 1;
        const orgName = org || 'N/A';
        if (!companies[country]) companies[country] = new Set();
        companies[country].add(orgName);
    };

    allModels.forEach(m => {
        const key = (m.name || '').toLowerCase();
        const match = Object.keys(originMap).find(prefix => key.startsWith(prefix));
        const country = match ? originMap[match] : null;
        const org = match ? orgMap[match] : null;
        addCount(country, org, key);
    });

    Object.values(arenasData || {}).forEach(arr => (arr || []).forEach(m => {
        const name = (m.model || '').toLowerCase();
        const match = Object.keys(originMap).find(prefix => name.startsWith(prefix));
        if (match) {
            addCount(originMap[match], orgMap[match], name);
        } else {
            const country = orgCountry(m.organization || '');
            addCount(country, m.organization || null, name);
        }
    }));

    const markers = [];
    const orgLayer = L.layerGroup();
    Object.entries(counts).forEach(([country, count]) => {
        const coord = countryCoords[country];
        if (!coord) return;
        const radius = Math.min(40, 8 + Math.sqrt(count) * 4);
        const mk = L.circleMarker(coord, {
            radius,
            color: '#ffbe00',
            fillColor: '#ffbe00',
            fillOpacity: 0.6,
            weight: 2
        }).addTo(map);
        const orgs = Array.from((companies[country] || new Set())).sort();
        mk.bindPopup(`${country}: ${count} modelos${orgs.length ? '<br>' + orgs.join(', ') : ''}`);
        mk.bindTooltip(`${country}`, { permanent: true, direction: 'top', className: 'country-label', offset: [0, -radius] }).openTooltip();
        markers.push({ marker: mk, country, radius });
        mk.on('click', () => {
            const pts = orgs.map((org, idx) => {
                const c = hqCoords[org];
                if (c) return c;
                const angle = (idx / Math.max(1, orgs.length)) * Math.PI * 2;
                return [coord[0] + 1.0 * Math.sin(angle), coord[1] + 1.5 * Math.cos(angle)];
            });
            if (pts.length) {
                const bounds = L.latLngBounds(pts);
                map.fitBounds(bounds, { padding: [30, 30], maxZoom: 6 });
                if (!map.hasLayer(orgLayer)) map.addLayer(orgLayer);
            }
        });
        orgs.forEach((org, idx) => {
            const c = hqCoords[org];
            let pt = c || coord;
            if (!c) {
                const angle = (idx / Math.max(1, orgs.length)) * Math.PI * 2;
                pt = [coord[0] + 1.0 * Math.sin(angle), coord[1] + 1.5 * Math.cos(angle)];
            }
            const cmk = L.circleMarker(pt, {
                radius: 6,
                color: '#1f77b4',
                fillColor: '#1f77b4',
                fillOpacity: 0.85,
                weight: 1
            }).bindTooltip(org, { permanent: true, direction: 'right', className: 'company-label' });
            orgLayer.addLayer(cmk);
        });
    });

    map.on('zoomend', () => {
        const z = map.getZoom();
        markers.forEach(({ marker, country, radius }) => {
            const orgs = Array.from((companies[country] || new Set())).sort();
            marker.unbindTooltip();
            const text = z >= 3 && orgs.length ? `${country}: ${orgs.join(', ')}` : `${country}`;
            marker.bindTooltip(text, { permanent: true, direction: 'top', className: 'country-label', offset: [0, -radius] }).openTooltip();
        });
        if (z >= 3) { if (!map.hasLayer(orgLayer)) map.addLayer(orgLayer); } else { if (map.hasLayer(orgLayer)) map.removeLayer(orgLayer); }
    });

    if (map.getZoom() >= 3) map.addLayer(orgLayer);
}

function updateLeaders() {
    const leaders = {
        overall: findBest('overall'),
        coding: findBest('coding'),
        creativeWriting: findBest('creativeWriting'),
        math: findBest('math')
    };
    
    document.getElementById('leaderInfo').innerHTML = 
        `<strong>${leaders.overall.name}</strong><br>Ranking: #${leaders.overall.overall}`;
    document.getElementById('codingLeader').innerHTML = 
        `<strong>${leaders.coding.name}</strong><br>Ranking: #${leaders.coding.coding}`;
    document.getElementById('creativeLeader').innerHTML = 
        `<strong>${leaders.creativeWriting.name}</strong><br>Ranking: #${leaders.creativeWriting.creativeWriting}`;
    document.getElementById('mathLeader').innerHTML = 
        `<strong>${leaders.math.name}</strong><br>Ranking: #${leaders.math.math}`;
}

function findBest(category) {
    return allModels
        .filter(m => m[category])
        .reduce((best, model) => 
            !best || model[category] < best[category] ? model : best
        );
}

// Rankings Table
function renderRankingsTable() {
    const table = document.getElementById('rankingsTable');
    const type = (document.getElementById('typeFilter') && document.getElementById('typeFilter').value) || 'all';
    if (type !== 'all') {
        const searchTerm = document.getElementById('searchInput').value.toLowerCase();
        const theadHTML = '<thead><tr><th>Rank</th><th>Modelo</th><th>Score</th><th>CI_95</th><th>Votes</th><th>Organização</th><th>Licença</th></tr></thead>';
        const arena = (arenasData[type] || []).filter(m => (m.model || '').toLowerCase().includes(searchTerm)).sort((a, b) => a.rank - b.rank).slice(0, 300);
        let bodyHTML = '<tbody id="rankingsTableBody">';
        bodyHTML += arena.map(m => `
            <tr>
                <td><span class="rank-badge ${getRankClass(m.rank)}">${m.rank}</span></td>
                <td><strong>${m.model}</strong></td>
                <td>${isNaN(m.score) ? '-' : m.score}</td>
                <td>${isNaN(m.ci95) ? '-' : m.ci95}</td>
                <td>${isNaN(m.votes) ? '-' : m.votes}</td>
                <td>${m.organization || '-'}</td>
                <td>${m.license || '-'}</td>
            </tr>
        `).join('');
        bodyHTML += '</tbody>';
        table.innerHTML = theadHTML + bodyHTML;
        return;
    }
    const theadHTML = '<thead><tr><th class="sortable" data-sort="overall">Overall</th><th>Modelo</th><th class="sortable" data-sort="expert">Expert</th><th class="sortable" data-sort="hardPrompts">Hard Prompts</th><th class="sortable" data-sort="coding">Coding</th><th class="sortable" data-sort="math">Math</th><th class="sortable" data-sort="creativeWriting">Creative</th><th class="sortable" data-sort="instructionFollowing">Instructions</th><th class="sortable" data-sort="longerQuery">Longer Query</th></tr></thead>';
    let bodyHTML = '<tbody id="rankingsTableBody">';
    bodyHTML += filteredModels.map(model => `
        <tr>
            <td><span class="rank-badge ${getRankClass(model.overall)}">${model.overall}</span></td>
            <td><strong>${model.name}</strong></td>
            <td>${formatRank(model.expert)}</td>
            <td>${formatRank(model.hardPrompts)}</td>
            <td>${formatRank(model.coding)}</td>
            <td>${formatRank(model.math)}</td>
            <td>${formatRank(model.creativeWriting)}</td>
            <td>${formatRank(model.instructionFollowing)}</td>
            <td>${formatRank(model.longerQuery)}</td>
        </tr>
    `).join('');
    bodyHTML += '</tbody>';
    table.innerHTML = theadHTML + bodyHTML;
    table.querySelectorAll('.sortable').forEach(th => {
        th.addEventListener('click', () => sortTable(th.dataset.sort));
    });
}

function getRankClass(rank) {
    if (rank <= 3) return 'rank-1';
    if (rank <= 10) return 'rank-top10';
    if (rank <= 50) return 'rank-top50';
    return 'rank-other';
}

function formatRank(rank) {
    return rank ? `<span class="rank-badge ${getRankClass(rank)}">${rank}</span>` : '-';
}

// Filters and Search
function filterTable() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    const category = document.getElementById('categoryFilter').value;
    const type = (document.getElementById('typeFilter') && document.getElementById('typeFilter').value) || 'all';
    const typeSet = type !== 'all' ? new Set((arenasData[type] || []).map(m => (m.model || '').toLowerCase())) : null;
    
    filteredModels = [...allModels].filter(model => {
        const matchesSearch = model.name.toLowerCase().includes(searchTerm);
        const matchesType = !typeSet || typeSet.has(model.name.toLowerCase());
        return matchesSearch && matchesType;
    });
    
    filteredModels.sort((a, b) => {
        const aVal = a[category] || Infinity;
        const bVal = b[category] || Infinity;
        return aVal - bVal;
    });
    
    renderRankingsTable();
}

function sortTable(column) {
    const isAscending = filteredModels[0] && filteredModels[0][column] <= (filteredModels[1]?.[column] || Infinity);
    
    filteredModels.sort((a, b) => {
        const aVal = a[column] || Infinity;
        const bVal = b[column] || Infinity;
        return isAscending ? bVal - aVal : aVal - bVal;
    });
    
    renderRankingsTable();
}

// Export
function exportToCSV() {
    const headers = ['Overall', 'Model', 'Expert', 'Hard Prompts', 'Coding', 'Math', 'Creative Writing', 'Instruction Following', 'Longer Query'];
    const rows = filteredModels.map(m => [
        m.overall,
        m.name,
        m.expert || '-',
        m.hardPrompts || '-',
        m.coding || '-',
        m.math || '-',
        m.creativeWriting || '-',
        m.instructionFollowing || '-',
        m.longerQuery || '-'
    ]);
    
    const csv = [headers, ...rows].map(row => row.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'llm-rankings.csv';
    a.click();
}

// Comparison
function handleComparisonSearch(e) {
    const term = e.target.value.toLowerCase();
    const results = document.getElementById('compareResults');
    
    if (term.length < 2) {
        results.classList.remove('show');
        return;
    }
    
    const matches = allModels
        .filter(m => m.name.toLowerCase().includes(term))
        .slice(0, 10);
    
    results.innerHTML = matches.map(m => 
        `<div class="autocomplete-item" data-model="${m.name}">${m.name}</div>`
    ).join('');
    
    results.classList.add('show');
    
    results.querySelectorAll('.autocomplete-item').forEach(item => {
        item.addEventListener('click', () => addToComparison(item.dataset.model));
    });
}

function addToComparison(modelName) {
    if (selectedComparison.length >= 5) {
        alert('Máximo de 5 modelos para comparação');
        return;
    }
    
    if (selectedComparison.includes(modelName)) {
        return;
    }
    
    selectedComparison.push(modelName);
    updateComparisonView();
    
    document.getElementById('compareSearch').value = '';
    document.getElementById('compareResults').classList.remove('show');
}

function removeFromComparison(modelName) {
    selectedComparison = selectedComparison.filter(m => m !== modelName);
    updateComparisonView();
}

function updateComparisonView() {
    const container = document.getElementById('selectedModels');
    container.innerHTML = selectedComparison.map(name => `
        <div class="selected-model">
            <span>${name}</span>
            <button onclick="removeFromComparison('${name}')">×</button>
        </div>
    `).join('');
    
    if (selectedComparison.length > 0) {
        renderComparisonRadar();
        renderComparisonTable();
    }
}

function renderComparisonRadar() {
    const ctx = document.getElementById('comparisonRadar');
    if (!ctx) return;
    
    if (charts.comparison) charts.comparison.destroy();
    
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    
    const models = selectedComparison.map(name => 
        allModels.find(m => m.name === name)
    );
    
    const categoryKeys = ['expert', 'hardPrompts', 'coding', 'math', 'creativeWriting', 'instructionFollowing', 'longerQuery'];
    
    const datasets = models.map((model, idx) => ({
        label: model.name,
        data: categoryKeys.map(key => model[key] ? 278 - model[key] : 0),
        borderColor: getChartColor(idx),
        backgroundColor: getChartColor(idx, 0.2),
        borderWidth: 2
    }));
    
    charts.comparison = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: categoryKeys.map(key => categories[key]),
            datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: textColor }
                }
            },
            scales: {
                r: {
                    ticks: { color: textColor },
                    grid: { color: gridColor },
                    pointLabels: { color: textColor }
                }
            }
        }
    });
}

function renderComparisonTable() {
    const container = document.getElementById('comparisonTable');
    const models = selectedComparison.map(name => 
        allModels.find(m => m.name === name)
    );
    
    const categoryKeys = Object.keys(categories);
    
    let html = '<table><thead><tr><th>Categoria</th>';
    models.forEach(m => html += `<th>${m.name}</th>`);
    html += '</tr></thead><tbody>';
    
    categoryKeys.forEach(key => {
        html += `<tr><td><strong>${categories[key]}</strong></td>`;
        models.forEach(m => {
            html += `<td>${formatRank(m[key])}</td>`;
        });
        html += '</tr>';
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

// Categories View
function renderCategoriesView() {
    const grid = document.getElementById('categoryGrid');
    const categoryKeys = Object.keys(categories);
    
    grid.innerHTML = categoryKeys.map(key => {
        const top10 = allModels
            .filter(m => m[key])
            .sort((a, b) => a[key] - b[key])
            .slice(0, 10);
        
        return `
            <div class="category-card">
                <h3>${categories[key]}</h3>
                <ul class="category-list">
                    ${top10.map((m, idx) => `
                        <li class="category-item">
                            <span class="category-rank">#${idx + 1}</span>
                            <span class="category-model">${m.name}</span>
                            <span class="rank-badge ${getRankClass(m[key])}">${m[key]}</span>
                        </li>
                    `).join('')}
                </ul>
            </div>
        `;
    }).join('');
}

function renderTypesView() {
    const grid = document.getElementById('typesGrid');
    if (!grid) return;
    const order = ['image_edit','search','text','t2i','t2v','vision','webdev'];
    grid.innerHTML = order.map(key => {
        const arena = arenasData[key] || [];
        const top10 = topN(arena, 10);
        return `
            <div class="category-card">
                <h3>${getArenaLabel(key)}</h3>
                <ul class="category-list">
                    ${top10.map((m, idx) => `
                        <li class="category-item">
                            <span class="category-rank">#${idx + 1}</span>
                            <span class="category-model">${m.model}</span>
                            <span class="rank-badge ${getRankClass(m.rank)}">${m.rank}</span>
                        </li>
                    `).join('')}
                </ul>
            </div>
        `;
    }).join('');
}

function renderCompaniesView() {
    const grid = document.getElementById('companiesGrid');
    if (!grid) return;
    const orgMap = {};
    const typeOrder = ['text','webdev','vision','t2i','t2v','image_edit','search'];
    const hasModelInType = (name, type) => (arenasData[type] || []).some(m => (m.model || '') === name);
    const modelType = (name) => {
        for (const t of typeOrder) if (hasModelInType(name, t)) return t; return null;
    };
    Object.entries(arenasData).forEach(([type, arr]) => {
        (arr || []).forEach(m => {
            const org = m.organization || 'N/A';
            if (!orgMap[org]) orgMap[org] = { models: new Map(), country: orgCountry(org) };
            if (!orgMap[org].models.has(m.model)) orgMap[org].models.set(m.model, { type, score: m.score });
        });
    });
    const orgs = Object.keys(orgMap).sort((a,b)=> a.localeCompare(b));
    if (!orgs.length) { grid.innerHTML = '<div class="category-card"><h3>Sem dados de empresas</h3><p>Carregue as arenas ou aguarde o carregamento.</p></div>'; return; }
    const cardHTML = orgs.map(org => {
        const entries = Array.from(orgMap[org].models.entries());
        const models = entries.map(([name, meta]) => {
            const overview = allModels.find(mm => mm.name === name);
            return { name, type: meta.type || modelType(name), overall: overview ? overview.overall : null };
        }).sort((a,b)=> (a.overall||Infinity) - (b.overall||Infinity));
        const types = Array.from(new Set(models.map(m => m.type).filter(Boolean)));
        const tagsHTML = types.map(t => `<span class="tag">${getArenaLabel(t)}</span>`).join('');
        const countHTML = `<span class="tag count-tag">Modelos: ${models.length}</span>`;
        const rowsHTML = models.map(m => `
            <tr>
                <td><strong>${m.name}</strong></td>
                <td>${m.type ? `<span class="tag">${getArenaLabel(m.type)}</span>` : '—'}</td>
                <td>${formatRank(m.overall)}</td>
            </tr>
        `).join('');
        return `
            <div class="category-card">
                <div class="card-header">
                    <h3>${org}</h3>
                    ${countHTML}
                </div>
                <p style="color: var(--color-text-secondary); margin-bottom: 10px;">País: ${orgMap[org].country || 'N/A'}</p>
                <div class="tags" style="margin-bottom: 12px;">${tagsHTML}</div>
                <div class="table-responsive">
                    <table class="table-simple">
                        <thead>
                            <tr><th>Modelo</th><th>Tipo</th><th>Overall</th></tr>
                        </thead>
                        <tbody>
                            ${rowsHTML}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }).join('');
    grid.innerHTML = cardHTML;
}

function orgCountry(org) {
    const map = {
        'Google': 'USA',
        'OpenAI': 'USA',
        'Anthropic': 'USA',
        'Meta': 'USA',
        'Microsoft': 'USA',
        'Nvidia': 'USA',
        'IBM': 'USA',
        'Amazon': 'USA',
        'Alibaba': 'China',
        'Tencent': 'China',
        'Baidu': 'China',
        'Zhipu AI': 'China',
        'Z.ai': 'China',
        'Moonshot': 'China',
        'Bytedance': 'China',
        'DeepSeek': 'China',
        'xAI': 'USA',
        'Mistral': 'France',
        'Black Forest Labs': 'Germany',
        'AI21 Labs': 'Israel',
        'Cohere': 'Canada'
    };
    return map[org] || 'N/A';
}

// Analytics
function renderAnalytics() {
    renderCorrelationChart();
    renderPerformanceChart();
    renderDecisionMap();
    renderOrgDomain();
    renderLicenseAnalysis();
    renderConfidenceAnalysis();
    renderMultiModalChampions();
    renderEmergingPlayers();
    renderArenaDifficulty();
}

function renderCorrelationChart() {
    const ctx = document.getElementById('correlationChart');
    if (!ctx) return;
    if (charts.correlation) charts.correlation.destroy();
    const keys = ['expert','hardPrompts','coding','math','creativeWriting','instructionFollowing','longerQuery'];
    const values = keys.map(k => spearman(allModels, 'overall', k));
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    charts.correlation = new Chart(ctx, {
        type: 'bar',
        data: { labels: keys.map(k => categories[k]), datasets: [{ label: 'Correlação com Overall', data: values, backgroundColor: '#ffbe00' }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { min: -1, max: 1, grid: { color: gridColor }, ticks: { color: textColor } }, x: { ticks: { color: textColor }, grid: { color: gridColor } } },
            plugins: {
                legend: { display: false },
                subtitle: { display: true, text: 'Coeficiente de Spearman entre categorias e Overall (−1 a +1).' },
                tooltip: { callbacks: { label: (ctx) => `Correlação: ${ctx.parsed.y}` } }
            }
        }
    });
}

function renderPerformanceChart() {
    const ctx = document.getElementById('performanceChart');
    if (!ctx) return;
    if (charts.performance) charts.performance.destroy();
    const brackets = [ { name: 'Top 10', min: 1, max: 10 }, { name: '11-50', min: 11, max: 50 }, { name: '51-100', min: 51, max: 100 }, { name: '101-200', min: 101, max: 200 }, { name: '201+', min: 201, max: 10000 } ];
    const categoriesToShow = ['coding','math','creativeWriting'];
    const labels = brackets.map(b => b.name);
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    const palette = {
        coding: 'rgba(255, 190, 0, 0.8)',
        math: 'rgba(59, 130, 246, 0.8)',
        creativeWriting: 'rgba(239, 68, 68, 0.8)'
    };
    const datasets = categoriesToShow.map((cat) => ({
        label: categories[cat],
        data: brackets.map(b => averageRank(allModels, cat, b.min, b.max)),
        backgroundColor: palette[cat],
        borderColor: palette[cat],
        borderWidth: 2
    }));
    charts.performance = new Chart(ctx, { type: 'bar', data: { labels, datasets }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } }, plugins: { tooltip: { callbacks: { label: (ctx) => `Média (0–100): ${ctx.parsed.y}` } } } } });
}

function renderDetailedStats() {
    const container = document.getElementById('detailedStats');
    const categoryKeys = Object.keys(categories);
    
    let html = '';
    categoryKeys.forEach(key => {
        const validModels = allModels.filter(m => typeof m[key] === 'number');
        const count = validModels.length;
        if (count === 0) {
            html += `
            <div class="stat-row">
                <div class="stat-row-label">${categories[key]}</div>
                <div class="stat-row-value">Média: - | Melhor: - | Pior: - | Avaliados: 0</div>
            </div>
            `;
            return;
        }
        const avg = validModels.reduce((sum, m) => sum + m[key], 0) / count;
        const best = Math.min(...validModels.map(m => m[key]));
        const worst = Math.max(...validModels.map(m => m[key]));
        
        html += `
            <div class="stat-row">
                <div class="stat-row-label">${categories[key]}</div>
                <div class="stat-row-value">Média: ${avg.toFixed(1)} | Melhor: ${best} | Pior: ${worst} | Avaliados: ${count}</div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// Helpers
function getChartColor(index, alpha = 1) {
    const colors = [
        `rgba(255, 190, 0, ${alpha})`,
        `rgba(0, 0, 0, ${alpha})`,
        `rgba(255, 255, 255, ${alpha})`,
        `rgba(128, 95, 0, ${alpha})`,
        `rgba(255, 223, 102, ${alpha})`
    ];
    return colors[index % colors.length];
}
function getCategoryMax(category) {
    const ranks = allModels.filter(m => m[category]).map(m => m[category]);
    if (!ranks.length) return null;
    return Math.max(...ranks);
}
function pearson(models, keyA, keyB) {
    const pairs = models.filter(m => m[keyA] && m[keyB]).map(m => [m[keyA], m[keyB]]);
    if (pairs.length < 3) return 0;
    const ax = pairs.map(p => p[0]);
    const bx = pairs.map(p => p[1]);
    const mean = arr => arr.reduce((s,v)=>s+v,0)/arr.length;
    const ma = mean(ax), mb = mean(bx);
    const num = pairs.reduce((s,[a,b]) => s + (a - ma)*(b - mb), 0);
    const denA = Math.sqrt(ax.reduce((s,a)=> s + Math.pow(a - ma,2),0));
    const denB = Math.sqrt(bx.reduce((s,b)=> s + Math.pow(b - mb,2),0));
    const den = denA * denB;
    if (!den) return 0;
    return +(num / den).toFixed(3);
}
function spearman(models, keyA, keyB) {
    const pairs = models.filter(m => m[keyA] && m[keyB]).map(m => [m[keyA], m[keyB]]);
    if (pairs.length < 3) return 0;
    const ax = pairs.map(p => p[0]);
    const bx = pairs.map(p => p[1]);
    const rank = (arr) => {
        const idxs = arr.map((v,i)=>({v,i})).sort((a,b)=>a.v-b.v);
        const ranks = new Array(arr.length);
        for (let i=0;i<idxs.length;i++) {
            let j=i;
            while (j+1<idxs.length && idxs[j+1].v===idxs[i].v) j++;
            const avgRank = (i + j + 2) / 2;
            for (let k=i;k<=j;k++) ranks[idxs[k].i] = avgRank;
            i=j;
        }
        return ranks;
    };
    const ra = rank(ax);
    const rb = rank(bx);
    const mean = arr => arr.reduce((s,v)=>s+v,0)/arr.length;
    const ma = mean(ra), mb = mean(rb);
    const num = ra.reduce((s,a,i)=> s + (a - ma)*(rb[i] - mb), 0);
    const denA = Math.sqrt(ra.reduce((s,a)=> s + Math.pow(a - ma,2),0));
    const denB = Math.sqrt(rb.reduce((s,b)=> s + Math.pow(b - mb,2),0));
    const den = denA * denB;
    if (!den) return 0;
    return +(num / den).toFixed(2);
}
function averageRank(models, category, minOverall, maxOverall) {
    const maxCat = getCategoryMax(category);
    if (!maxCat || maxCat <= 0) return 0;
    const toScore = (rank) => ((maxCat + 1 - rank) / maxCat) * 100;
    const arr = models
        .filter(m => m.overall >= minOverall && m.overall <= maxOverall && m[category])
        .map(m => toScore(m[category]));
    if (!arr.length) return 0;
    return +(arr.reduce((s,v)=>s+v,0) / arr.length).toFixed(1);
}

function getArenaLabel(key) {
    const map = {
        text: 'Texto',
        vision: 'Visão',
        t2i: 'Texto→Imagem',
        t2v: 'Texto→Vídeo',
        image_edit: 'Edição de Imagem',
        webdev: 'WebDev',
        search: 'Busca'
    };
    return map[key] || key;
}

function topN(arr, n) {
    return [...arr].sort((a, b) => a.rank - b.rank).slice(0, n);
}

function licenseType(s) {
    if (!s) return 'Open';
    const t = s.toLowerCase();
    return t.includes('proprietary') ? 'Proprietary' : 'Open';
}

function renderDecisionMap() {
    const el = document.getElementById('decisionMatrixTable');
    if (!el) return;
    const rows = Object.keys(arenasData).map(k => {
        const a = arenasData[k];
        if (!a || !a.length) return null;
        const t = topN(a, 1)[0];
        return { arena: getArenaLabel(k), model: t.model, org: t.organization, score: t.score };
    }).filter(Boolean);
    let html = '<thead><tr><th>Arena</th><th>Modelo</th><th>Organização</th><th>Score</th></tr></thead><tbody>';
    html += rows.map(r => `<tr><td>${r.arena}</td><td>${r.model}</td><td>${r.org}</td><td>${r.score}</td></tr>`).join('');
    html += '</tbody>';
    el.innerHTML = html;
    const ctx = document.getElementById('multiTop3Chart');
    if (!ctx) return;
    const counts = {};
    Object.values(arenasData).forEach(a => {
        topN(a, analyticsControls.versatilityTopN).forEach(m => {
            const key = m.organization || 'N/A';
            counts[key] = (counts[key] || 0) + 1;
        });
    });
    const labels = Object.keys(counts);
    const data = labels.map(k => counts[k]);
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.multiTop3) charts.multiTop3.destroy();
    charts.multiTop3 = new Chart(ctx, { type: 'bar', data: { labels, datasets: [{ label: 'Top3 em múltiplas arenas (por organização)', data, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } }, plugins: { legend: { display: false } } } });
}

function renderOrgDomain() {
    const ctx = document.getElementById('orgShareChart');
    if (!ctx) return;
    const focus = ['Google', 'OpenAI', 'Anthropic'];
    const arenas = Object.keys(arenasData);
    const labels = arenas.map(getArenaLabel);
    const datasets = focus.map(org => ({ label: org, data: arenas.map(k => {
        const a = arenasData[k];
        if (!a || !a.length) return 0;
        const top = topN(a, 10);
        const c = top.filter(m => (m.organization || '').toLowerCase() === org.toLowerCase()).length;
        return +(c / top.length * 100).toFixed(1);
    }), backgroundColor: org === 'Google' ? '#3cba54' : org === 'OpenAI' ? '#1f77b4' : '#9467bd' }));
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.orgShare) charts.orgShare.destroy();
    charts.orgShare = new Chart(ctx, { type: 'bar', data: { labels, datasets }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } } } });
    const ctx2 = document.getElementById('orgVolumePerfChart');
    if (!ctx2) return;
    const orgMap = {};
    arenas.forEach(k => {
        (arenasData[k] || []).forEach(m => {
            const o = m.organization || 'N/A';
            if (!orgMap[o]) orgMap[o] = { count: 0, sum: 0, n: 0 };
            orgMap[o].count += 1;
            if (!isNaN(m.score)) { orgMap[o].sum += m.score; orgMap[o].n += 1; }
        });
    });
    const pointsAll = Object.entries(orgMap).map(([o, v]) => ({ o, x: v.count, y: v.n ? v.sum / v.n : 0 }));
    populateOrgFilter(Object.keys(orgMap).sort());
    const active = analyticsControls.orgFilter && analyticsControls.orgFilter.length ? analyticsControls.orgFilter : null;
    const points = active ? pointsAll.filter(p => active.includes(p.o)) : pointsAll;
    const theme2 = document.documentElement.getAttribute('data-theme');
    const textColor2 = theme2 === 'dark' ? '#ffffff' : '#000000';
    const gridColor2 = theme2 === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.orgScatter) charts.orgScatter.destroy();
    charts.orgScatter = new Chart(ctx2, { type: 'scatter', data: { datasets: [{ label: 'Org', data: points.map(p => ({ x: p.x, y: p.y })), backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { color: gridColor2 }, ticks: { color: textColor2 }, title: { display: true, text: 'Volume de modelos' } }, y: { grid: { color: gridColor2 }, ticks: { color: textColor2 }, title: { display: true, text: 'Score médio' } } }, plugins: { tooltip: { callbacks: { label: (c) => { const p = points[c.dataIndex]; return `${p.o}: (${p.x}, ${p.y.toFixed(1)})`; } } } } } });
}

function populateOrgFilter(orgs) {
    const sel = document.getElementById('orgFilterSelect');
    if (!sel) return;
    const prev = new Set(analyticsControls.orgFilter || []);
    sel.innerHTML = orgs.map(o => `<option value="${o}" ${prev.has(o) ? 'selected' : ''}>${o}</option>`).join('');
}

function renderLicenseAnalysis() {
    const ctx = document.getElementById('licensePerfChart');
    if (!ctx) return;
    const labels = Object.keys(arenasData).map(getArenaLabel);
    const open = [];
    const prop = [];
    Object.keys(arenasData).forEach(k => {
        const a = arenasData[k];
        if (!a || !a.length) { open.push(0); prop.push(0); return; }
        const o = a.filter(m => licenseType(m.license) === 'Open');
        const p = a.filter(m => licenseType(m.license) === 'Proprietary');
        const mean = arr => arr.length ? arr.reduce((s, v) => s + (v.score || 0), 0) / arr.length : 0;
        open.push(+mean(o).toFixed(1));
        prop.push(+mean(p).toFixed(1));
    });
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.licensePerf) charts.licensePerf.destroy();
    charts.licensePerf = new Chart(ctx, { type: 'bar', data: { labels, datasets: [{ label: 'Open', data: open, backgroundColor: '#1f77b4' }, { label: 'Proprietário', data: prop, backgroundColor: '#d62728' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } } } });
    const note = document.getElementById('tcoNote');
    if (note) note.textContent = 'TCO não calculado por falta de dados de custo de licença.';
}

function renderConfidenceAnalysis() {
    const ctx = document.getElementById('votesCiScatter');
    if (!ctx) return;
    const all = [];
    Object.values(arenasData).forEach(a => a.forEach(m => { if (!isNaN(m.votes) && !isNaN(m.ci95)) all.push({ x: m.votes, y: m.ci95 }); }));
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.votesCi) charts.votesCi.destroy();
    charts.votesCi = new Chart(ctx, { type: 'scatter', data: { datasets: [{ label: 'Votes vs CI_95', data: all, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { color: gridColor }, ticks: { color: textColor }, title: { display: true, text: 'Votes' } }, y: { grid: { color: gridColor }, ticks: { color: textColor }, title: { display: true, text: 'CI_95' } } } } });
    const eqEl = document.getElementById('ciEquivalence');
    if (!eqEl) return;
    const items = [];
    Object.entries(arenasData).forEach(([k, a]) => {
        const top = topN(a, 10);
        for (let i = 0; i < top.length; i++) {
            for (let j = i + 1; j < top.length; j++) {
                const ai = { lo: top[i].score - top[i].ci95, hi: top[i].score + top[i].ci95 };
                const aj = { lo: top[j].score - top[j].ci95, hi: top[j].score + top[j].ci95 };
                const overlap = !(ai.hi < aj.lo || aj.hi < ai.lo);
                if (overlap) items.push(`${getArenaLabel(k)}: ${top[i].model} ~ ${top[j].model}`);
            }
        }
    });
    eqEl.innerHTML = items.length ? `<ul>${items.slice(0, 30).map(s => `<li>${s}</li>`).join('')}</ul>` : 'Sem equivalências nas top 10.';
    const ctx2 = document.getElementById('rankSpreadChart');
    if (!ctx2) return;
    const labels = Object.keys(arenasData).map(getArenaLabel);
    const vals = Object.values(arenasData).map(a => {
        if (!a || !a.length) return 0;
        const mean = arr => arr.reduce((s, v) => s + v, 0) / arr.length;
        const spreads = topN(a, 20).map(m => (m.rankSpreadUpper - m.rankSpreadLower));
        return +mean(spreads).toFixed(1);
    });
    const theme2 = document.documentElement.getAttribute('data-theme');
    const textColor2 = theme2 === 'dark' ? '#ffffff' : '#000000';
    const gridColor2 = theme2 === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.spread) charts.spread.destroy();
    charts.spread = new Chart(ctx2, { type: 'bar', data: { labels, datasets: [{ label: 'Spread médio (Top20)', data: vals, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: gridColor2 }, ticks: { color: textColor2 } }, x: { grid: { color: gridColor2 }, ticks: { color: textColor2 } } } } });
}

function renderMultiModalChampions() {
    const table = document.getElementById('multiModalChampionsTable');
    if (!table) return;
    const orgTop = {};
    Object.entries(arenasData).forEach(([k, a]) => {
        const t = topN(a, 1)[0];
        if (!t) return;
        const o = t.organization || 'N/A';
        if (!orgTop[o]) orgTop[o] = [];
        orgTop[o].push(getArenaLabel(k));
    });
    const rows = Object.entries(orgTop).map(([o, arenas]) => ({ org: o, arenas: arenas.join(', '), count: arenas.length })).sort((a, b) => b.count - a.count);
    let html = '<thead><tr><th>Organização</th><th>Arenas Lideradas</th><th>#</th></tr></thead><tbody>';
    html += rows.map(r => `<tr><td>${r.org}</td><td>${r.arenas}</td><td>${r.count}</td></tr>`).join('');
    html += '</tbody>';
    table.innerHTML = html;
    const ctx = document.getElementById('generalistIndexChart');
    if (!ctx) return;
    const labels = rows.map(r => r.org);
    const data = rows.map(r => r.count);
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.generalist) charts.generalist.destroy();
    charts.generalist = new Chart(ctx, { type: 'bar', data: { labels, datasets: [{ label: 'Arenas lideradas', data, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } }, plugins: { legend: { display: false } } } });
}

function renderEmergingPlayers() {
    const table = document.getElementById('emergingPlayersTable');
    if (!table) return;
    const items = [];
    Object.entries(arenasData).forEach(([k, a]) => {
        if (!a || !a.length) return;
        const scores = a.map(m => m.score).filter(v => !isNaN(v)).sort((x, y) => x - y);
        const votes = a.map(m => m.votes).filter(v => !isNaN(v)).sort((x, y) => x - y);
        const q = (arr, p) => arr.length ? arr[Math.floor((arr.length - 1) * p)] : 0;
        const s80 = q(scores, 0.8);
        const v30 = q(votes, 0.3);
        a.forEach(m => { if (m.score >= s80 && m.votes <= v30) items.push({ arena: getArenaLabel(k), model: m.model, org: m.organization, score: m.score, votes: m.votes }); });
    });
    const rows = items.slice(0, 20);
    let html = '<thead><tr><th>Arena</th><th>Modelo</th><th>Org</th><th>Score</th><th>Votes</th></tr></thead><tbody>';
    html += rows.map(r => `<tr><td>${r.arena}</td><td>${r.model}</td><td>${r.org}</td><td>${r.score}</td><td>${r.votes}</td></tr>`).join('');
    html += '</tbody>';
    table.innerHTML = html;
}

function renderArenaDifficulty() {
    const ctx = document.getElementById('arenaStdDevChart');
    if (!ctx) return;
    const labels = Object.keys(arenasData).map(getArenaLabel);
    const stds = Object.values(arenasData).map(a => {
        if (!a || !a.length) return 0;
        const xs = a.map(m => m.score).filter(v => !isNaN(v));
        const mean = xs.reduce((s, v) => s + v, 0) / xs.length;
        const varr = xs.reduce((s, v) => s + Math.pow(v - mean, 2), 0) / xs.length;
        return +Math.sqrt(varr).toFixed(1);
    });
    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';
    const gridColor = theme === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.stddev) charts.stddev.destroy();
    charts.stddev = new Chart(ctx, { type: 'bar', data: { labels, datasets: [{ label: 'Std Dev do Score', data: stds, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: gridColor }, ticks: { color: textColor } }, x: { grid: { color: gridColor }, ticks: { color: textColor } } }, plugins: { legend: { display: false } } } });
    const ctx2 = document.getElementById('arenaCR3Chart');
    if (!ctx2) return;
    const cr3 = Object.values(arenasData).map(a => {
        if (!a || !a.length) return 0;
        const t10 = topN(a, 10);
        const t3 = topN(a, analyticsControls.crTopN);
        const s10 = t10.reduce((s, m) => s + (m.score || 0), 0);
        const s3 = t3.reduce((s, m) => s + (m.score || 0), 0);
        return s10 ? +(s3 / s10 * 100).toFixed(1) : 0;
    });
    const labels2 = Object.keys(arenasData).map(getArenaLabel);
    const theme2 = document.documentElement.getAttribute('data-theme');
    const textColor2 = theme2 === 'dark' ? '#ffffff' : '#000000';
    const gridColor2 = theme2 === 'dark' ? '#333333' : '#e0e0e0';
    if (charts.cr3) charts.cr3.destroy();
    charts.cr3 = new Chart(ctx2, { type: 'bar', data: { labels: labels2, datasets: [{ label: `CR${analyticsControls.crTopN}% (Top${analyticsControls.crTopN}/Top10)`, data: cr3, backgroundColor: '#ffbe00' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100, grid: { color: gridColor2 }, ticks: { color: textColor2 } }, x: { grid: { color: gridColor2 }, ticks: { color: textColor2 } } } } });
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    
    document.querySelectorAll('.tab-content').forEach(section => {
        section.classList.toggle('active', section.id === `${tabName}-section`);
    });
    
    if (tabName === 'analytics') {
        renderAnalytics();
    } else if (tabName === 'rankings') {
        document.getElementById('categoryFilter').value = 'overall';
        const tf = document.getElementById('typeFilter');
        if (tf) tf.value = 'all';
        filterTable();
    } else if (tabName === 'types') {
        renderTypesView();
    } else if (tabName === 'companies') {
        renderCompaniesView();
    }
}

function showLoading(show) {
    document.getElementById('loading').classList.toggle('show', show);
}

function updateAllCharts() {
    if (charts.worldMap || charts.top10) renderOverview();
    if (charts.comparison) renderComparisonRadar();
}

window.removeFromComparison = removeFromComparison;
function renderTop10OverviewTable(models) {
    const el = document.getElementById('top10OverviewTable');
    if (!el) return;
    let html = '<thead><tr>' +
        '<th>Overall</th><th>Modelo</th><th>Expert</th><th>Hard Prompts</th><th>Coding</th><th>Math</th><th>Creative Writing</th><th>Instruction Following</th><th>Longer Query</th>' +
        '</tr></thead><tbody>';
    html += models.map(m => (
        `<tr>` +
        `<td>${formatRank(m.overall)}</td>` +
        `<td><strong>${m.name}</strong></td>` +
        `<td>${formatRank(m.expert)}</td>` +
        `<td>${formatRank(m.hardPrompts)}</td>` +
        `<td>${formatRank(m.coding)}</td>` +
        `<td>${formatRank(m.math)}</td>` +
        `<td>${formatRank(m.creativeWriting)}</td>` +
        `<td>${formatRank(m.instructionFollowing)}</td>` +
        `<td>${formatRank(m.longerQuery)}</td>` +
        `</tr>`
    )).join('');
    html += '</tbody>';
    el.innerHTML = html;
}
