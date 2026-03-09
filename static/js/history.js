document.addEventListener('DOMContentLoaded', () => {
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';

    const BAR_COLOR = 'rgba(99,179,237,0.75)';
    const LINE_COLOR = '#63b3ed';

    // ── Summary stats ──
    fetch('/api/history/summary').then(r => r.json()).then(d => {
        setText('h-unique-clients', fmt(d.unique_clients));
        setText('h-total-sessions', fmt(d.total_sessions));
        setText('h-countries', fmt(d.countries));
    }).catch(() => {});

    // ── Peak hours bar chart ──
    fetch('/api/history/hourly').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-hourly'), {
            type: 'bar',
            data: {
                labels: data.map(d => `${String((d.hour + 8) % 24).padStart(2, '0')}:00`),
                datasets: [{ label: 'Sessions', data: data.map(d => d.count),
                    backgroundColor: BAR_COLOR, borderRadius: 4 }]
            },
            options: { aspectRatio: 3.5, plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } }
        });
    }).catch(() => {});

    // ── Daily trend line chart ──
    fetch('/api/history/daily?days=30').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-daily'), {
            type: 'line',
            data: {
                labels: data.map(d => d.date),
                datasets: [{ label: 'Sessions', data: data.map(d => d.count),
                    borderColor: LINE_COLOR, backgroundColor: 'rgba(99,179,237,0.1)',
                    fill: true, tension: 0.3, pointRadius: 3 }]
            },
            options: { aspectRatio: 3.5, plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } }
        });
    }).catch(() => {});

    // ── Countries horizontal bar chart ──
    fetch('/api/history/countries').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-countries'), {
            type: 'bar',
            data: {
                labels: data.map(d => d.country),
                datasets: [{ label: 'Clients', data: data.map(d => d.count),
                    backgroundColor: BAR_COLOR, borderRadius: 4 }]
            },
            options: { indexAxis: 'y', plugins: { legend: { display: false } },
                scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
        });
    }).catch(() => {});

    // ── Client table ──
    let searchTerm = '';
    let pageOffset = 0;
    const PAGE_SIZE = 50;

    function loadClients() {
        const params = new URLSearchParams({ search: searchTerm, limit: PAGE_SIZE, offset: pageOffset });
        fetch(`/api/history/clients?${params}`)
            .then(r => r.json())
            .then(data => { renderTable(data.clients || []); renderPager(data.total || 0); })
            .catch(() => {
                document.getElementById('h-client-tbody').innerHTML =
                    '<tr><td colspan="8" class="empty-cell">Failed to load.</td></tr>';
            });
    }

    function renderTable(clients) {
        const tbody = document.getElementById('h-client-tbody');
        if (!clients.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-cell">No records found.</td></tr>';
            return;
        }
        tbody.innerHTML = clients.map(c => {
            const loc = [c.city, c.region, c.country].filter(Boolean).join(', ') || '—';
            const typeLabel = (c.client_type || 'unknown').toUpperCase();
            const glyph = typeLabel.slice(0, 2);
            return `<tr>
                <td>${esc(c.device_name || c.client_id)}</td>
                <td><span class="type-chip"><span class="type-glyph" aria-hidden="true">${esc(glyph)}</span>${esc(typeLabel)}</span></td>
                <td>${esc(loc)}</td>
                <td class="text-meta">${esc(c.ip_address || '—')}</td>
                <td>${esc(c.room_id || '—')}</td>
                <td class="text-meta">${esc(fmtDate(c.first_seen))}</td>
                <td class="text-meta">${esc(fmtDate(c.last_seen))}</td>
                <td>${c.total_sessions ?? '—'}</td>
            </tr>`;
        }).join('');
    }

    function renderPager(total) {
        const pages = Math.ceil(total / PAGE_SIZE);
        const current = Math.floor(pageOffset / PAGE_SIZE);
        const el = document.getElementById('h-pagination');
        if (pages <= 1) { el.innerHTML = ''; return; }
        el.innerHTML = Array.from({ length: pages }, (_, i) =>
            `<button class="btn btn-outline btn-mini${i === current ? ' active' : ''}" data-page="${i}">${i + 1}</button>`
        ).join('');
        el.querySelectorAll('button[data-page]').forEach(btn => {
            btn.addEventListener('click', () => {
                pageOffset = parseInt(btn.dataset.page) * PAGE_SIZE;
                loadClients();
            });
        });
    }

    document.getElementById('h-search')?.addEventListener('input', e => {
        searchTerm = e.target.value.trim();
        pageOffset = 0;
        loadClients();
    });

    loadClients();

    // ── Helpers ──
    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }
    function fmt(n) { return n != null ? Number(n).toLocaleString('en-US') : '-'; }
    function fmtDate(s) {
        if (!s) return '—';
        const d = new Date(s.replace(' ', 'T') + 'Z'); // stored as UTC
        const bj = new Date(d.getTime() + 8 * 3600 * 1000); // UTC+8
        return bj.toISOString().replace('T', ' ').slice(0, 16);
    }
    function esc(s) {
        return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
});
