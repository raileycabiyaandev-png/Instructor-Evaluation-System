/* ============================================================
   users/static/users/js/admin_common.js
   Helpers shared by the admin pages.

   - Admin.tabs()       tab buttons that show one section at a time
   - Admin.listTable()  search + filter + sort + pages for a table
   ============================================================ */
var Admin = (function () {
    'use strict';

    /* ---------- tabs ---------- */
    function tabs(root, onShow) {
        if (!root) return;
        var buttons = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
        if (!buttons.length) return;

        function select(tab, focus) {
            buttons.forEach(function (b) {
                var on = b === tab;
                b.setAttribute('aria-selected', on ? 'true' : 'false');
                b.tabIndex = on ? 0 : -1;
                document.getElementById(b.getAttribute('aria-controls')).hidden = !on;
            });
            if (focus) tab.focus();
            if (onShow) onShow(tab.dataset.tab);
        }

        buttons.forEach(function (tab, i) {
            tab.addEventListener('click', function () { select(tab, false); });
            tab.addEventListener('keydown', function (e) {
                var next;
                if (e.key === 'ArrowRight')     next = buttons[(i + 1) % buttons.length];
                else if (e.key === 'ArrowLeft') next = buttons[(i - 1 + buttons.length) % buttons.length];
                else if (e.key === 'Home')      next = buttons[0];
                else if (e.key === 'End')       next = buttons[buttons.length - 1];
                else return;
                e.preventDefault();
                select(next, true);
            });
        });

        var marked = buttons.filter(function (b) { return b.getAttribute('aria-selected') === 'true'; })[0];
        select(marked || buttons[0], false);
    }


    /* ---------- table with search, filter, sort and pages ----------
       opts.tbody     the <tbody> (each row is a <tr>)
       opts.pageSize  rows per page
       opts.search    (optional) text input
       opts.sort      (optional) <select>; each option value is a key of opts.sorters
       opts.sorters   { key: function (rowA, rowB) { ... } }
       opts.pager     the pager element (see the HTML in the templates)
       opts.count     (optional) element that shows "Showing 1-5 of 12"
       opts.empty     (optional) element shown when nothing matches
       opts.filterBy  (optional) function (row, filterValue) -> true/false
       opts.onRender  (optional) function (visibleRows)
    */
    function listTable(opts) {
        var rows  = Array.prototype.slice.call(opts.tbody.children);
        var state = { query: '', filter: 'all', page: 1 };
        var info  = opts.pager.querySelector('[data-pager-info]');
        var prev  = opts.pager.querySelector('[data-pager-prev]');
        var next  = opts.pager.querySelector('[data-pager-next]');
        var matches = rows;

        function apply() {
            var q = state.query.trim().toLowerCase();

            matches = rows.filter(function (r) {
                var okText   = !q || r.textContent.toLowerCase().indexOf(q) !== -1;
                var okFilter = state.filter === 'all' || !opts.filterBy || opts.filterBy(r, state.filter);
                return okText && okFilter;
            });

            var key = opts.sort ? opts.sort.value : null;
            if (key && opts.sorters && opts.sorters[key]) matches.sort(opts.sorters[key]);

            var pages = Math.max(1, Math.ceil(matches.length / opts.pageSize));
            if (state.page > pages) state.page = pages;
            var start = (state.page - 1) * opts.pageSize;
            var shown = matches.slice(start, start + opts.pageSize);

            rows.forEach(function (r) { r.hidden = true; });
            matches.forEach(function (r) { opts.tbody.appendChild(r); });
            shown.forEach(function (r) { r.hidden = false; });

            info.textContent = 'Page ' + state.page + ' of ' + pages;
            prev.disabled = state.page === 1;
            next.disabled = state.page === pages;
            opts.pager.hidden = matches.length <= opts.pageSize;

            if (opts.count) {
                opts.count.textContent = matches.length
                    ? 'Showing ' + (start + 1) + ' to ' + (start + shown.length) + ' of ' + matches.length
                    : 'Showing 0 of ' + rows.length;
            }
            if (opts.empty) opts.empty.hidden = matches.length > 0;
            if (opts.onRender) opts.onRender(shown);
        }

        if (opts.search) opts.search.addEventListener('input', function () { state.query = opts.search.value; state.page = 1; apply(); });
        if (opts.sort)   opts.sort.addEventListener('change', function () { state.page = 1; apply(); });
        prev.addEventListener('click', function () { if (state.page > 1) { state.page--; apply(); } });
        next.addEventListener('click', function () { state.page++; apply(); });

        apply();

        return {
            setFilter: function (value) { state.filter = value; state.page = 1; apply(); },
            refresh: apply
        };
    }

    return { tabs: tabs, listTable: listTable };
})();