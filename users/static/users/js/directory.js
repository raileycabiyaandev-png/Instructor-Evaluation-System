/* users/js/directory.js
   Shared by the admin Students / Instructors list pages AND their update pages.
   - Modals: Add modal, Edit modal (loaded on demand), standalone update page
   - Directory: live search with highlighting, faceted filters with counts,
     filter chips, sorting, pagination, and remembered filters. */
(function () {
    'use strict';

    /* ============================================================
       MODALS
       ============================================================ */

    var addModal = document.querySelector('[data-modal]');
    var editModal = document.querySelector('[data-edit-modal]');
    var editContent = document.querySelector('[data-edit-content]');
    var standalone = document.querySelector('[data-standalone-modal]');

    var lastTrigger = null;
    var editUrl = '';

    var FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), ' +
                    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function showModal(m) {
        m.classList.add('show');
        m.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
    }

    function hideModal(m) {
        m.classList.remove('show');
        m.setAttribute('aria-hidden', 'true');
        if (!document.querySelector('.modal.show')) document.body.classList.remove('modal-open');
    }

    function focusFirst(m) {
        var f = m.querySelector('input:not([type="hidden"]):not([type="checkbox"]), select, textarea');
        if (f) setTimeout(function () { f.focus(); }, 60);
    }

    function restoreFocus() {
        if (lastTrigger && lastTrigger.focus && document.contains(lastTrigger)) lastTrigger.focus();
    }

    /* ---------- Add modal ---------- */

    function openModal(trigger) {
        if (!addModal) return;
        lastTrigger = trigger || document.activeElement;
        showModal(addModal);
        focusFirst(addModal);
    }

    function closeModal() {
        if (!addModal) return;
        hideModal(addModal);
        restoreFocus();
    }

    window.openModal = openModal;
    window.closeModal = closeModal;

    document.querySelectorAll('[data-open-modal]').forEach(function (btn) {
        btn.addEventListener('click', function () { openModal(btn); });
    });

    document.querySelectorAll('[data-close-modal]').forEach(function (btn) {
        btn.addEventListener('click', closeModal);
    });

    if (addModal) {

        var addForm = addModal.querySelector('[data-modal-form]');
        var addSubmit = addModal.querySelector('[data-submit]');

        if (addForm && addSubmit) {

            var addLabel = addSubmit.textContent;

            addForm.addEventListener('submit', function () {
                addSubmit.disabled = true;
                addSubmit.textContent = 'Saving…';
            });

            window.addEventListener('pageshow', function (e) {
                if (e.persisted) {
                    addSubmit.disabled = false;
                    addSubmit.textContent = addLabel;
                }
            });
        }

        if (addModal.dataset.hasErrors === '1') openModal();
    }

    /* ---------- Edit modal (form is loaded from the existing update page) ---------- */

    function parseDialog(html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        return doc.querySelector('[data-update-dialog]');
    }

    function fillEdit(dialog) {

        editContent.innerHTML = dialog.innerHTML;

        var form = editContent.querySelector('form');
        if (form) form.setAttribute('action', editUrl);

        var firstError = editContent.querySelector('.error');
        if (firstError) firstError.scrollIntoView({ block: 'center' });
        else focusFirst(editModal);
    }

    function closeEdit() {
        if (!editModal) return;
        hideModal(editModal);
        editContent.innerHTML = '';
        restoreFocus();
    }

    function openEdit(url, trigger) {

        editUrl = url;
        lastTrigger = trigger || document.activeElement;

        editContent.innerHTML =
            '<div class="edit-loading" role="status">' +
            '<span class="edit-spinner" aria-hidden="true"></span>Loading…</div>';

        showModal(editModal);

        fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (res) {
                if (!res.ok || res.redirected) throw new Error('unavailable');
                return res.text();
            })
            .then(function (html) {
                var dialog = parseDialog(html);
                if (!dialog) throw new Error('no dialog');
                fillEdit(dialog);
            })
            .catch(function () {
                /* anything unexpected: just open the normal page */
                window.location.href = url;
            });
    }

    function ajaxSubmit(form, btn) {

        var label = btn ? btn.innerHTML : '';

        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Saving…';
        }

        fetch(editUrl, {
            method: 'POST',
            body: new FormData(form),
            credentials: 'same-origin',
            redirect: 'manual',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (res) {

                /* success: the view redirected. Reload the list (filters are remembered). */
                if (res.type === 'opaqueredirect' || res.status === 0 || (res.status >= 300 && res.status < 400)) {
                    window.location.reload();
                    return null;
                }

                if (!res.ok) throw new Error('bad status');

                return res.text().then(function (html) {
                    var dialog = parseDialog(html);
                    if (!dialog) { window.location.href = editUrl; return; }
                    fillEdit(dialog);     /* validation errors: show the form again */
                });
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = label; }
                form.submit();            /* last resort: normal form post */
            });
    }

    /* ---------- standalone update page ---------- */

    function leaveStandalone() {
        var url = standalone.dataset.cancelUrl;
        if (url) window.location.href = url;
        else if (window.history.length > 1) window.history.back();
        else window.location.href = document.referrer || '/';
    }

    if (standalone) {
        document.body.classList.add('modal-open');
        focusFirst(standalone);

        window.addEventListener('pageshow', function (e) {
            if (e.persisted) {
                standalone.querySelectorAll('[data-submit]').forEach(function (b) {
                    b.disabled = false;
                    b.innerHTML = b.dataset.label || b.innerHTML;
                });
            }
        });
    }

    /* ---------- shared behaviour ---------- */

    function closeTop() {
        if (standalone) { leaveStandalone(); return; }
        if (editModal && editModal.classList.contains('show')) { closeEdit(); return; }
        if (addModal && addModal.classList.contains('show')) closeModal();
    }

    document.addEventListener('click', function (e) {

        var link = e.target.closest && e.target.closest('a[data-edit-link]');

        if (link && editModal && editContent) {
            if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            openEdit(link.getAttribute('href'), link);
            return;
        }

        var closer = e.target.closest && e.target.closest('[data-close-edit]');
        if (closer) {
            e.preventDefault();
            closeTop();
        }
    });

    /* pressing the blurred backdrop closes whichever modal is open */
    document.addEventListener('mousedown', function (e) {
        var t = e.target;
        if (t.classList && t.classList.contains('modal') && t.classList.contains('show')) closeTop();
    });

    document.addEventListener('submit', function (e) {

        var form = e.target;
        if (!form.matches || !form.matches('[data-update-form]')) return;

        var btn = form.querySelector('[data-submit]');

        if (form.closest('[data-edit-modal]')) {
            e.preventDefault();
            ajaxSubmit(form, btn);
            return;
        }

        /* standalone page: normal post, just block double submits */
        if (btn) {
            btn.dataset.label = btn.innerHTML;
            btn.disabled = true;
            btn.textContent = 'Saving…';
        }
    });

    document.addEventListener('keydown', function (e) {

        var open = document.querySelector('.modal.show');

        if (open) {

            if (e.key === 'Escape') { closeTop(); return; }

            if (e.key === 'Tab') {
                var items = Array.prototype.filter.call(open.querySelectorAll(FOCUSABLE), function (el) {
                    return el.offsetParent !== null;
                });
                if (!items.length) return;

                var first = items[0];
                var last = items[items.length - 1];

                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
            return;
        }

        /* "/" jumps to the search box */
        if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
            var tag = (document.activeElement && document.activeElement.tagName) || '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
            var search = document.querySelector('[data-search-input]');
            if (search) {
                e.preventDefault();
                search.focus();
                search.select();
            }
        }
    });

    document.querySelectorAll('[data-alert]').forEach(function (alert) {
        setTimeout(function () {
            alert.classList.add('alert-hide');
            setTimeout(function () { alert.remove(); }, 400);
        }, 6000);
    });

    /* ============================================================
       DIRECTORY
       ============================================================ */

    var root = document.querySelector('[data-directory]');
    if (!root) return;

    var noun = root.dataset.noun || 'record';
    var NONE = '__none__';

    var tbody = root.querySelector('tbody');
    var rows = Array.prototype.slice.call(root.querySelectorAll('tbody [data-row]'));

    var searchInput = root.querySelector('[data-search-input]');
    var searchClear = root.querySelector('[data-search-clear]');
    var statusBtns = Array.prototype.slice.call(root.querySelectorAll('[data-status-filter]'));
    var selects = Array.prototype.slice.call(root.querySelectorAll('[data-filter-field]'));
    var sortBtns = Array.prototype.slice.call(root.querySelectorAll('[data-sort]'));
    var sortSelect = root.querySelector('[data-sort-select]');
    var clearBtns = Array.prototype.slice.call(root.querySelectorAll('[data-clear]'));
    var sizeSelect = root.querySelector('[data-page-size]');

    var tableWrap = root.querySelector('[data-table]');
    var emptyMatch = root.querySelector('[data-empty-match]');
    var footer = root.querySelector('[data-footer]');
    var summary = root.querySelector('[data-summary]');
    var headingCount = root.querySelector('[data-heading-count]');
    var pagesBox = root.querySelector('[data-pages]');
    var prevBtn = root.querySelector('[data-prev]');
    var nextBtn = root.querySelector('[data-next]');
    var pager = root.querySelector('[data-pager]');
    var chipsBox = root.querySelector('[data-chips]');
    var chipList = root.querySelector('[data-chip-list]');

    var state = {
        q: '',
        tokens: [],
        compact: '',
        status: 'all',
        filters: {},
        sortKey: null,
        sortDir: 1,
        page: 1,
        size: sizeSelect ? (parseInt(sizeSelect.value, 10) || 10) : 10
    };

    /* ---------- text folding: lower-case + strip accents, 1 char in = 1 char out ---------- */

    function fold(s) {
        var out = '';
        for (var i = 0; i < s.length; i++) {
            var ch = s.charAt(i);
            var b = ch.normalize ? ch.normalize('NFD').charAt(0) : ch;
            var low = b.toLowerCase();
            out += low.length === 1 ? low : b;
        }
        return out;
    }

    /* ---------- build the search index once ---------- */

    var ordered = rows.slice();

    rows.forEach(function (row, i) {
        row._i = i;
        row._q = fold((row.dataset.search || row.textContent).replace(/\s+/g, ' ').trim());
        row._qc = row._q.replace(/\s+/g, '');
        row._active = row.dataset.active === 'true';
        row._s = {};
        row._hl = null;
    });

    function sortValue(row, key) {
        if (!(key in row._s)) row._s[key] = fold(row.dataset[key] || '');
        return row._s[key];
    }

    /* ---------- dropdown filters ---------- */

    selects.forEach(function (select) {

        var field = select.dataset.filterField;
        var seen = {};
        var hasEmpty = false;

        select._field = field;
        select._label = (select.closest('.filter-field') && select.closest('.filter-field').querySelector('span'))
            ? select.closest('.filter-field').querySelector('span').textContent.trim()
            : field;
        select._allText = select.dataset.allLabel || select.options[0].textContent.replace(/\s*\(\d+\)\s*$/, '');
        select._opts = {};

        rows.forEach(function (row) {
            var v = row.dataset[field] || '';
            if (v === '') hasEmpty = true;
            else seen[v] = true;
        });

        var values = Object.keys(seen).sort(function (a, b) { return a.localeCompare(b); });

        values.forEach(function (v) {
            var opt = document.createElement('option');
            opt.value = v;
            opt._label = v;
            opt.textContent = v;
            select.appendChild(opt);
            select._opts[v] = opt;
        });

        if (hasEmpty && select.dataset.emptyLabel) {
            var none = document.createElement('option');
            none.value = NONE;
            none._label = select.dataset.emptyLabel;
            none.textContent = none._label;
            select.appendChild(none);
            select._opts[NONE] = none;
        }

        if (values.length === 0 && !hasEmpty) {
            var wrap = select.closest('.filter-field');
            if (wrap) wrap.hidden = true;
        }

        select.addEventListener('change', function () {
            state.filters[field] = select.value;
            state.page = 1;
            render();
        });
    });

    function selectFor(field) {
        for (var i = 0; i < selects.length; i++) if (selects[i]._field === field) return selects[i];
        return null;
    }

    /* ---------- sort dropdown (works on phones where headers are hidden) ---------- */

    if (sortSelect) {

        sortBtns.forEach(function (btn) {
            var label = btn.textContent.replace(/\s+/g, ' ').trim();
            [[1, ' (A → Z)'], [-1, ' (Z → A)']].forEach(function (pair) {
                var opt = document.createElement('option');
                opt.value = btn.dataset.sort + '|' + pair[0];
                opt.textContent = label + pair[1];
                sortSelect.appendChild(opt);
            });
        });

        sortSelect.addEventListener('change', function () {
            if (!sortSelect.value) {
                state.sortKey = null;
                state.sortDir = 1;
            } else {
                var parts = sortSelect.value.split('|');
                state.sortKey = parts[0];
                state.sortDir = parseInt(parts[1], 10) || 1;
            }
            state.page = 1;
            applySort();
            render();
        });
    }

    /* ---------- filtering logic ---------- */

    function searchOK(row) {
        if (!state.tokens.length) return true;

        var ok = true;
        for (var i = 0; i < state.tokens.length; i++) {
            if (row._q.indexOf(state.tokens[i]) === -1) { ok = false; break; }
        }
        if (ok) return true;

        /* "delacruz" should still find "Dela Cruz" */
        return state.tokens.length > 1 && row._qc.indexOf(state.compact) !== -1
            || (state.tokens.length === 1 && row._qc.indexOf(state.tokens[0]) !== -1);
    }

    /* which filters does this row fail? ([] = shown) */
    function failing(row) {

        var f = [];

        if (!searchOK(row)) f.push('search');

        if ((state.status === 'active' && !row._active) || (state.status === 'inactive' && row._active)) {
            f.push('status');
        }

        for (var i = 0; i < selects.length; i++) {
            var field = selects[i]._field;
            var want = state.filters[field];
            if (!want) continue;
            var have = row.dataset[field] || '';
            var ok = want === NONE ? have === '' : have === want;
            if (!ok) f.push(field);
        }

        return f;
    }

    function hasActiveFilters() {
        if (state.q || state.status !== 'all') return true;
        for (var f in state.filters) { if (state.filters[f]) return true; }
        return false;
    }

    /* ---------- sorting ---------- */

    function applySort() {

        if (state.sortKey) {
            var key = state.sortKey, dir = state.sortDir;
            ordered = rows.slice().sort(function (a, b) {
                var r = sortValue(a, key).localeCompare(sortValue(b, key), undefined, { numeric: true });
                return r !== 0 ? r * dir : a._i - b._i;
            });
        } else {
            ordered = rows.slice();
        }

        var frag = document.createDocumentFragment();
        ordered.forEach(function (row) { frag.appendChild(row); });
        tbody.appendChild(frag);

        sortBtns.forEach(function (btn) {
            var th = btn.closest('th');
            var icon = btn.querySelector('i');
            var on = btn.dataset.sort === state.sortKey;

            if (th) th.setAttribute('aria-sort', on ? (state.sortDir === 1 ? 'ascending' : 'descending') : 'none');
            btn.classList.toggle('is-sorted', on);
            if (icon) icon.className = 'bi ' + (on ? (state.sortDir === 1 ? 'bi-sort-up' : 'bi-sort-down') : 'bi-arrow-down-up');
        });

        if (sortSelect) sortSelect.value = state.sortKey ? state.sortKey + '|' + state.sortDir : '';
    }

    sortBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var key = btn.dataset.sort;

            if (state.sortKey !== key) { state.sortKey = key; state.sortDir = 1; }
            else if (state.sortDir === 1) { state.sortDir = -1; }
            else { state.sortKey = null; state.sortDir = 1; }

            state.page = 1;
            applySort();
            render();
        });
    });

    /* ---------- highlighting ---------- */

    function highlight(el, tokens) {

        if (el._t === undefined) el._t = el.textContent.replace(/\s+/g, ' ').trim();

        function undo() {
            if (el._m) { el.textContent = el._t; el._m = false; }
        }

        if (!tokens.length) { undo(); return; }

        var f = fold(el._t);
        var marks = [];

        tokens.forEach(function (t) {
            var i = f.indexOf(t);
            while (i !== -1) {
                marks.push([i, i + t.length]);
                i = f.indexOf(t, i + t.length);
            }
        });

        if (!marks.length) { undo(); return; }

        marks.sort(function (a, b) { return a[0] - b[0]; });

        var merged = [marks[0]];
        for (var m = 1; m < marks.length; m++) {
            var lastM = merged[merged.length - 1];
            if (marks[m][0] <= lastM[1]) lastM[1] = Math.max(lastM[1], marks[m][1]);
            else merged.push(marks[m]);
        }

        var frag = document.createDocumentFragment();
        var pos = 0;

        merged.forEach(function (r) {
            if (r[0] > pos) frag.appendChild(document.createTextNode(el._t.slice(pos, r[0])));
            var mk = document.createElement('mark');
            mk.className = 'hl-mark';
            mk.textContent = el._t.slice(r[0], r[1]);
            frag.appendChild(mk);
            pos = r[1];
        });

        if (pos < el._t.length) frag.appendChild(document.createTextNode(el._t.slice(pos)));

        el.textContent = '';
        el.appendChild(frag);
        el._m = true;
    }

    function highlightRow(row) {
        if (!row._hl) row._hl = Array.prototype.slice.call(row.querySelectorAll('.hl'));
        row._hl.forEach(function (el) { highlight(el, state.tokens); });
    }

    /* ---------- chips ---------- */

    function addChip(label, value, onRemove) {

        var chip = document.createElement('span');
        chip.className = 'chip';

        var text = document.createElement('span');
        var em = document.createElement('em');
        em.textContent = label + ': ';
        text.appendChild(em);
        text.appendChild(document.createTextNode(value));

        var x = document.createElement('button');
        x.type = 'button';
        x.setAttribute('aria-label', 'Remove filter ' + label + ' ' + value);
        x.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
        x.addEventListener('click', onRemove);

        chip.appendChild(text);
        chip.appendChild(x);
        chipList.appendChild(chip);
    }

    function renderChips() {

        if (!chipsBox || !chipList) return;

        chipList.innerHTML = '';

        if (state.q) {
            addChip('Search', '“' + state.q + '”', function () {
                searchInput.value = '';
                onSearchNow();
                searchInput.focus();
            });
        }

        if (state.status !== 'all') {
            addChip('Status', state.status === 'active' ? 'Active' : 'Inactive', function () {
                state.status = 'all';
                state.page = 1;
                render();
            });
        }

        selects.forEach(function (select) {
            var v = state.filters[select._field];
            if (!v) return;
            var opt = select._opts[v];
            addChip(select._label, opt ? opt._label : v, function () {
                state.filters[select._field] = '';
                select.value = '';
                state.page = 1;
                render();
            });
        });

        chipsBox.hidden = !hasActiveFilters();
    }

    /* ---------- pagination ---------- */

    function pageList(cur, n) {
        var out = [], i;
        if (n <= 7) {
            for (i = 1; i <= n; i++) out.push(i);
            return out;
        }
        out.push(1);
        var s = Math.max(2, cur - 1), e = Math.min(n - 1, cur + 1);
        if (s > 2) out.push('…');
        for (i = s; i <= e; i++) out.push(i);
        if (e < n - 1) out.push('…');
        out.push(n);
        return out;
    }

    function renderPager(pages) {

        if (!pagesBox) return;
        pagesBox.innerHTML = '';

        pageList(state.page, pages).forEach(function (p) {

            if (p === '…') {
                var gap = document.createElement('span');
                gap.className = 'page-gap';
                gap.textContent = '…';
                pagesBox.appendChild(gap);
                return;
            }

            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'page-btn' + (p === state.page ? ' is-current' : '');
            b.textContent = p;
            b.setAttribute('aria-label', 'Page ' + p);
            if (p === state.page) b.setAttribute('aria-current', 'page');

            b.addEventListener('click', function () {
                state.page = p;
                render(true);
            });

            pagesBox.appendChild(b);
        });
    }

    /* ---------- remember filters (survive Edit / Activate / Deactivate / Delete reloads) ---------- */

    var storeKey = 'directory:' + location.pathname;

    function save() {
        try {
            sessionStorage.setItem(storeKey, JSON.stringify({
                q: state.q,
                status: state.status,
                filters: state.filters,
                sortKey: state.sortKey,
                sortDir: state.sortDir,
                size: state.size
            }));
        } catch (e) { /* storage unavailable: ignore */ }
    }

    function restoreState() {
        var saved = null;
        try { saved = JSON.parse(sessionStorage.getItem(storeKey) || 'null'); } catch (e) { saved = null; }
        if (!saved) return;

        if (saved.q && searchInput) {
            searchInput.value = saved.q;
            setQuery(saved.q);
        }

        if (saved.status && ['all', 'active', 'inactive'].indexOf(saved.status) !== -1) {
            state.status = saved.status;
        }

        if (saved.filters) {
            Object.keys(saved.filters).forEach(function (field) {
                var select = selectFor(field);
                var v = saved.filters[field];
                if (select && v && select._opts[v]) {
                    state.filters[field] = v;
                    select.value = v;
                }
            });
        }

        if (saved.sortKey && sortBtns.some(function (b) { return b.dataset.sort === saved.sortKey; })) {
            state.sortKey = saved.sortKey;
            state.sortDir = saved.sortDir === -1 ? -1 : 1;
        }

        if (saved.size && sizeSelect && sizeSelect.querySelector('option[value="' + saved.size + '"]')) {
            state.size = saved.size;
            sizeSelect.value = String(saved.size);
        }
    }

    /* ---------- render ---------- */

    function plural(n) {
        return noun + (n === 1 ? '' : 's');
    }

    function render(scrollToTop) {

        var list = [];
        var fc = { status: { all: 0, active: 0, inactive: 0 } };

        selects.forEach(function (s) { fc[s._field] = { all: 0 }; });

        /* one pass: build the result list AND the facet counts */
        ordered.forEach(function (row) {

            var f = failing(row);

            if (f.length === 0) list.push(row);

            if (f.length > 1) return;

            var skip = f[0];

            if (!skip || skip === 'status') {
                fc.status.all++;
                if (row._active) fc.status.active++; else fc.status.inactive++;
            }

            selects.forEach(function (s) {
                if (skip && skip !== s._field) return;
                var v = row.dataset[s._field] || '';
                var key = v === '' ? NONE : v;
                fc[s._field].all++;
                fc[s._field][key] = (fc[s._field][key] || 0) + 1;
            });
        });

        ['all', 'active', 'inactive'].forEach(function (key) {
            var el = root.querySelector('[data-count="' + key + '"]');
            if (el) el.textContent = fc.status[key];
        });

        selects.forEach(function (select) {

            var counts = fc[select._field];
            select.options[0].textContent = select._allText + ' (' + counts.all + ')';

            Object.keys(select._opts).forEach(function (value) {
                var opt = select._opts[value];
                var n = counts[value] || 0;
                opt.textContent = opt._label + ' (' + n + ')';
                opt.disabled = n === 0 && select.value !== value;
            });
        });

        var total = list.length;
        var pages = Math.max(1, Math.ceil(total / state.size));

        if (state.page > pages) state.page = pages;

        var start = (state.page - 1) * state.size;
        var end = Math.min(start + state.size, total);

        rows.forEach(function (row) { row.hidden = true; });

        list.slice(start, end).forEach(function (row) {
            row.hidden = false;
            highlightRow(row);
        });

        if (tableWrap) tableWrap.hidden = total === 0;
        if (emptyMatch) emptyMatch.hidden = !(total === 0 && rows.length > 0);

        if (footer) footer.hidden = total === 0 || (pages === 1 && rows.length <= 10);

        if (summary) {
            summary.textContent = total
                ? 'Showing ' + (start + 1) + '–' + end + ' of ' + total + ' ' + plural(total)
                : '';
        }

        if (headingCount) {
            headingCount.innerHTML = '';
            if (hasActiveFilters()) {
                var strong = document.createElement('strong');
                strong.textContent = total;
                headingCount.appendChild(strong);
                headingCount.appendChild(document.createTextNode(' of ' + rows.length + ' ' + plural(rows.length) + ' match'));
            } else {
                var all = document.createElement('strong');
                all.textContent = rows.length;
                headingCount.appendChild(all);
                headingCount.appendChild(document.createTextNode(' ' + plural(rows.length) + ' in total'));
            }
        }

        if (pager) pager.hidden = pages <= 1;
        if (prevBtn) prevBtn.disabled = state.page <= 1;
        if (nextBtn) nextBtn.disabled = state.page >= pages;
        if (pages > 1) renderPager(pages);

        statusBtns.forEach(function (btn) {
            var on = btn.dataset.statusFilter === state.status;
            btn.classList.toggle('is-active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });

        if (searchClear) searchClear.hidden = !(searchInput && searchInput.value);

        renderChips();
        save();

        if (scrollToTop === true) {
            var card = root.querySelector('.records-card');
            if (card && card.getBoundingClientRect().top < 0) {
                card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }

    /* ---------- events ---------- */

    function setQuery(raw) {
        state.q = raw.trim();
        state.tokens = state.q ? fold(state.q).split(/\s+/) : [];
        state.compact = state.tokens.join('');
    }

    function onSearchNow() {
        setQuery(searchInput ? searchInput.value : '');
        state.page = 1;
        render();
    }

    var raf = 0;

    if (searchInput) {

        searchInput.addEventListener('input', function () {
            if (raf) cancelAnimationFrame(raf);
            raf = requestAnimationFrame(onSearchNow);
        });

        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                if (searchInput.value) {
                    searchInput.value = '';
                    onSearchNow();
                } else {
                    searchInput.blur();
                }
            }
        });
    }

    if (searchClear) {
        searchClear.addEventListener('click', function () {
            searchInput.value = '';
            onSearchNow();
            searchInput.focus();
        });
    }

    statusBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            state.status = btn.dataset.statusFilter;
            state.page = 1;
            render();
        });
    });

    if (sizeSelect) {
        sizeSelect.addEventListener('change', function () {
            state.size = parseInt(sizeSelect.value, 10) || 10;
            state.page = 1;
            render();
        });
    }

    if (prevBtn) prevBtn.addEventListener('click', function () { state.page -= 1; render(true); });
    if (nextBtn) nextBtn.addEventListener('click', function () { state.page += 1; render(true); });

    clearBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            setQuery('');
            state.status = 'all';
            state.filters = {};
            state.page = 1;
            if (searchInput) searchInput.value = '';
            selects.forEach(function (s) { s.value = ''; });
            render();
        });
    });

    /* ---------- start ---------- */

    restoreState();
    applySort();
    render();

})();