/* ============================================================
   users/static/users/js/instructor_common.js
   Helpers shared by the instructor Dashboard and Results pages.
   Load Chart.js first, then this file.

   - tabs()          tab buttons that show one section at a time
   - categoryChart() horizontal bar chart (sort + show last period)
   - targetLine()    dashed gold "Excellent 4.5" line for any chart
   - paginate()      shows a few table rows per page
   - showInsights()  "strongest area" / "needs attention" cards
   ============================================================ */
var Instructor = (function () {
    'use strict';

    /* One palette: navy + gold. Same meaning on every chart.
       high = Excellent (deep navy), mid = Good (blue), low = Needs improvement (gold) */
    var COLOR = {
        high: '#1b2f6b', mid: '#5b78c4', low: '#c9a227',
        previous: '#c9d4ee', navy: '#13224f', line: '#223a85',
        gold: '#c9a227', goldInk: '#7a5c14', muted: '#5b6585',
        grid: 'rgba(19, 34, 79, 0.08)'
    };

    if (typeof Chart !== 'undefined') {
        Chart.defaults.font.family = '"Source Sans 3", system-ui, sans-serif';
        Chart.defaults.font.size = 13;
        Chart.defaults.color = COLOR.muted;
        Chart.defaults.plugins.tooltip.backgroundColor = COLOR.navy;
        Chart.defaults.plugins.tooltip.titleColor = '#e0bb4a';
        Chart.defaults.plugins.tooltip.bodyColor = '#ffffff';
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 8;
    }


    /* ---------- small tools ---------- */
    function getJSON(id) {
        var el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }

    function levelOf(score) {
        return score >= 4.5 ? 'high' : score >= 3.5 ? 'mid' : 'low';
    }

    /* "Teaching Effectiveness" -> ["Teaching", "Effectiveness"] so long names fit on phones */
    function wrapLabel(text, max) {
        var lines = [], line = '';
        String(text).split(' ').forEach(function (word) {
            if (line && (line + ' ' + word).length > max) { lines.push(line); line = word; }
            else { line = (line + ' ' + word).trim(); }
        });
        if (line) lines.push(line);
        return lines;
    }


    /* ---------- tabs: one section at a time, arrow keys work ---------- */
    function tabs(root, onShow) {
        if (!root) return;
        var buttons = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
        if (!buttons.length) return;

        function select(tab, focus, writeHash) {
            buttons.forEach(function (b) {
                var on = b === tab;
                b.setAttribute('aria-selected', on ? 'true' : 'false');
                b.tabIndex = on ? 0 : -1;
                document.getElementById(b.getAttribute('aria-controls')).hidden = !on;
            });
            if (focus) tab.focus();
            if (writeHash && window.history.replaceState) {
                window.history.replaceState(null, '', '#' + tab.dataset.tab);
            }
            if (onShow) onShow(tab.dataset.tab);
        }

        buttons.forEach(function (tab, i) {
            tab.addEventListener('click', function () { select(tab, false, true); });
            tab.addEventListener('keydown', function (e) {
                var next;
                if (e.key === 'ArrowRight')     next = buttons[(i + 1) % buttons.length];
                else if (e.key === 'ArrowLeft') next = buttons[(i - 1 + buttons.length) % buttons.length];
                else if (e.key === 'Home')      next = buttons[0];
                else if (e.key === 'End')       next = buttons[buttons.length - 1];
                else return;
                e.preventDefault();
                select(next, true, true);
            });
        });

        var fromHash = buttons.filter(function (b) { return '#' + b.dataset.tab === window.location.hash; })[0];
        var marked   = buttons.filter(function (b) { return b.getAttribute('aria-selected') === 'true'; })[0];
        select(fromHash || marked || buttons[0], false, false);
    }


    /* ---------- chart plugins ---------- */

    /* Dashed gold line, e.g. "Excellent 4.5". axis: 'x' (bar chart) or 'y' (line chart) */
    function targetLine(axis, value, text) {
        return {
            id: 'targetLine',
            afterDatasetsDraw: function (chart) {
                var scale = chart.scales[axis];
                if (!scale) return;
                var area = chart.chartArea, ctx = chart.ctx;
                var pos = scale.getPixelForValue(value);

                ctx.save();
                ctx.setLineDash([5, 4]);
                ctx.strokeStyle = COLOR.gold;
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                if (axis === 'x') { ctx.moveTo(pos, area.top); ctx.lineTo(pos, area.bottom); }
                else              { ctx.moveTo(area.left, pos); ctx.lineTo(area.right, pos); }
                ctx.stroke();

                ctx.setLineDash([]);
                ctx.font = '600 12px "Source Sans 3", sans-serif';
                ctx.fillStyle = COLOR.goldInk;
                if (axis === 'x') { ctx.textAlign = 'center'; ctx.fillText(text, pos, area.top - 6); }
                else              { ctx.textAlign = 'right';  ctx.fillText(text, area.right, pos - 6); }
                ctx.restore();
            }
        };
    }

    /* Writes the score at the end of each bar */
    var valueLabels = {
        id: 'valueLabels',
        afterDatasetsDraw: function (chart) {
            var ctx = chart.ctx;
            ctx.save();
            ctx.font = '700 13px "Source Sans 3", sans-serif';
            ctx.textBaseline = 'middle';
            chart.data.datasets.forEach(function (ds, di) {
                if (!chart.isDatasetVisible(di)) return;
                ctx.fillStyle = ds.labelColor || COLOR.navy;
                chart.getDatasetMeta(di).data.forEach(function (bar, i) {
                    var v = ds.data[i];
                    if (v === null || v === undefined) return;
                    ctx.fillText(v.toFixed(2), bar.x + 8, bar.y);
                });
            });
            ctx.restore();
        }
    };


    /* ---------- read the category table (data-* attributes on each row) ---------- */
    function readCategoryRows(table) {
        var rows = [];
        if (!table) return rows;
        table.querySelectorAll('tbody tr[data-label]').forEach(function (tr) {
            var score = parseFloat(tr.dataset.score);
            var prev  = parseFloat(tr.dataset.previous);
            if (isNaN(score)) return;
            rows.push({ label: tr.dataset.label, score: score, previous: isNaN(prev) ? null : prev });
        });
        return rows;
    }


    /* ---------- horizontal bar chart: one bar per category ---------- */
    function categoryChart(canvas, rows) {
        if (!canvas || !rows.length || typeof Chart === 'undefined') return null;

        var hasPrev = rows.some(function (r) { return r.previous !== null; });
        var state   = { sort: 'default', previous: hasPrev };
        var chart   = null;

        function ordered() {
            var list = rows.slice();
            if (state.sort === 'high') list.sort(function (a, b) { return b.score - a.score; });
            if (state.sort === 'low')  list.sort(function (a, b) { return a.score - b.score; });
            return list;
        }

        function build() {
            var list     = ordered();
            var showPrev = hasPrev && state.previous;
            if (chart) chart.destroy();

            canvas.parentNode.style.height = (list.length * (showPrev ? 72 : 50) + 60) + 'px';

            var datasets = [{
                label: 'This period',
                data: list.map(function (r) { return r.score; }),
                backgroundColor: list.map(function (r) { return COLOR[levelOf(r.score)]; }),
                borderRadius: 6,
                maxBarThickness: 26
            }];

            if (showPrev) {
                datasets.push({
                    label: 'Last period',
                    data: list.map(function (r) { return r.previous; }),
                    backgroundColor: COLOR.previous,
                    labelColor: COLOR.muted,
                    borderRadius: 6,
                    maxBarThickness: 26
                });
            }

            chart = new Chart(canvas, {
                type: 'bar',
                data: { labels: list.map(function (r) { return r.label; }), datasets: datasets },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    layout: { padding: { top: 20, right: 44 } },
                    scales: {
                        x: {
                            min: 0, max: 5,
                            ticks: { stepSize: 1 },
                            grid: { color: COLOR.grid },
                            border: { display: false },
                            title: { display: true, text: 'Score (out of 5)', color: COLOR.muted }
                        },
                        y: {
                            grid: { display: false },
                            border: { display: false },
                            ticks: {
                                color: COLOR.navy,
                                font: { size: 13, weight: '600' },
                                callback: function (value) { return wrapLabel(this.getLabelForValue(value), 16); }
                            }
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function (c) { return ' ' + c.dataset.label + ': ' + c.parsed.x.toFixed(2) + ' / 5'; }
                            }
                        }
                    }
                },
                plugins: [targetLine('x', 4.5, 'Excellent 4.5'), valueLabels]
            });
        }

        build();

        return {
            hasPrevious: hasPrev,
            setSort:     function (mode) { state.sort = mode; build(); },
            setPrevious: function (on)   { state.previous = on; build(); },
            resize:      function ()     { if (chart) chart.resize(); }
        };
    }


    /* ---------- "Strongest area" and "Needs the most attention" cards ---------- */
    function fillInsight(id, row) {
        var box = document.getElementById(id);
        if (!box) return;
        box.querySelector('[data-name]').textContent  = row.label;
        box.querySelector('[data-score]').textContent = row.score.toFixed(2) + ' out of 5';
        box.hidden = false;
    }

    function showInsights(rows) {
        if (rows.length < 2) return;
        var best = rows[0], low = rows[0];
        rows.forEach(function (r) {
            if (r.score > best.score) best = r;
            if (r.score < low.score)  low  = r;
        });
        if (best.score === low.score) return;       // all the same: nothing to point out
        fillInsight('insightBest', best);
        fillInsight('insightLow', low);
    }


    /* ---------- pager: shows a few table rows at a time ---------- */
    function paginate(tbody, pager, pageSize, noun) {
        var rows  = Array.prototype.slice.call(tbody.children);
        var pages = Math.max(1, Math.ceil(rows.length / pageSize));
        var page  = 1;
        var info  = pager.querySelector('[data-pager-info]');
        var prev  = pager.querySelector('[data-pager-prev]');
        var next  = pager.querySelector('[data-pager-next]');

        function render() {
            rows.forEach(function (tr, i) {
                tr.hidden = !(i >= (page - 1) * pageSize && i < page * pageSize);
            });
            info.textContent = 'Page ' + page + ' of ' + pages + '  (' + rows.length + ' ' + (noun || 'rows') + ')';
            prev.disabled = page === 1;
            next.disabled = page === pages;
        }

        prev.addEventListener('click', function () { if (page > 1)     { page--; render(); } });
        next.addEventListener('click', function () { if (page < pages) { page++; render(); } });

        pager.hidden = pages === 1;
        render();
    }

    return {
        COLOR: COLOR,
        getJSON: getJSON,
        levelOf: levelOf,
        tabs: tabs,
        targetLine: targetLine,
        readCategoryRows: readCategoryRows,
        categoryChart: categoryChart,
        showInsights: showInsights,
        paginate: paginate
    };
})();