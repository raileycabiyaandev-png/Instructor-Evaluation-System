function toggleSidebar() {
    var sb = document.getElementById('sidebar');
    var ov = document.getElementById('sbOverlay');
    var open = sb.classList.toggle('sidebar-open');
    ov.style.display = open ? 'block' : 'none';
}
function closeSidebar() {
    document.getElementById('sidebar').classList.remove('sidebar-open');
    document.getElementById('sbOverlay').style.display = 'none';
}
 
(function () {
    var dataEl = document.getElementById('results-chart-data');
    var canvas = document.getElementById('categoryChart');
    if (!dataEl || !canvas || typeof Chart === 'undefined') return;
 
    var d;
    try { d = JSON.parse(dataEl.textContent); } catch (e) { return; }
 
    var datasets = [];
 
    // Previous period first (gray), so it sits to the left of the current bar
    if (d.previous) {
        datasets.push({
            label: d.previous_label,
            data: d.previous,
            backgroundColor: 'rgba(148,163,184,0.65)',
            borderRadius: 6,
            borderSkipped: false
        });
    }
 
    datasets.push({
        label: d.current_label,
        data: d.current,
        backgroundColor: 'rgba(15,30,74,0.85)',
        borderRadius: 6,
        borderSkipped: false
    });
 
    new Chart(canvas, {
        type: 'bar',
        data: { labels: d.labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: !!d.previous, position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: function (c) {
                            return ' ' + c.dataset.label + ': ' + c.parsed.y.toFixed(2) + ' / 5';
                        }
                    }
                }
            },
            scales: {
                y: {
                    min: 0, max: 5,
                    ticks: { stepSize: 1, font: { size: 11 }, callback: function (v) { return v + '/5'; } },
                    grid: { color: 'rgba(0,0,0,0.05)' }
                },
                x: { ticks: { font: { size: 11 } }, grid: { display: false } }
            }
        }
    });
})();
 