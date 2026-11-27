/* academics/js/section_students.js
   Section roster page: tabs, search + filter + sort + pagination (enrolled list),
   searchable multi-select (add list), and the remove-confirmation modal. */
(function () {
    'use strict';

    var root = document.querySelector('[data-section]');
    if (!root) return;

    root.classList.add('js');

    /* ============================================================
       HELPERS
       ============================================================ */

    /* lower-case + strip accents, 1 char in = 1 char out */
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

    function tokensOf(q) {
        var f = fold(q.trim());
        return f ? f.split(/\s+/) : [];
    }

    function index(el, source) {
        el._q = fold((source || '').replace(/\s+/g, ' ').trim());
        el._qc = el._q.replace(/\s+/g, '');
    }

    /* every word must appear; "delacruz" still finds "Dela Cruz" */
    function matchTokens(el, tokens, compact) {
        if (!tokens.length) return true;
        for (var i = 0; i < tokens.length; i++) {
            if (el._q.indexOf(tokens[i]) === -1) return el._qc.indexOf(compact) !== -1;
        }
        return true;
    }

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
            var last = merged[merged.length - 1];
            if (marks[m][0] <= last[1]) last[1] = Math.max(last[1], marks[m][1]);
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

    /* ============================================================
       TABS
       ============================================================ */

    var tabs = Array.prototype.slice.call(root.querySelectorAll('[data-tab]'));
    var panels = Array.prototype.slice.call(root.querySelectorAll('[data-panel]'));

    function activate(name, focusTab) {

        tabs.forEach(function (t) {
            var on = t.dataset.tab === name;
            t.classList.toggle('is-active', on);
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            t.tabIndex = on ? 0 : -1;
            if (on && focusTab) t.focus();
        });

        panels.forEach(function (p) { p.hidden = p.dataset.panel !== name; });

        try {
            history.replaceState(null, '', name === 'add' ? '#add' : location.pathname + location.search);
        } catch (e) { /* ignore */ }
    }

    tabs.forEach(function (t, i) {

        t.addEventListener('click', function () { activate(t.dataset.tab); });

        t.addEventListener('keydown', function (e) {
            var next = null;
            if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
            else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
            else if (e.key === 'Home') next = tabs[0];
            else if (e.key === 'End') next = tabs[tabs.length - 1];
            if (next) {
                e.preventDefault();
                activate(next.dataset.tab, true);
            }
        });
    });

    root.querySelectorAll('[data-goto-tab]').forEach(function (btn) {
        btn.addEventListener('click', function () { activate(btn.dataset.gotoTab); });
    });

    var memberRows = Array.prototype.slice.call(root.querySelectorAll('#membersTable tbody [data-row]'));
    var addItems = Array.prototype.slice.call(root.querySelectorAll('[data-item]'));

    activate(
        location.hash === '#add' || (memberRows.length === 0 && addItems.length > 0) ? 'add' : 'enrolled'
    );

    /* ============================================================
       ENROLLED LIST: search, status filter, sort, pagination
       ============================================================ */

    var mPanel = root.querySelector('[data-panel="enrolled"]');

    if (memberRows.length && mPanel) {

        var tbody = mPanel.querySelector('tbody');
        var searchInput = mPanel.querySelector('[data-search-input]');
        var searchClear = mPanel.querySelector('[data-search-clear]');
        var statusBtns = Array.prototype.slice.call(mPanel.querySelectorAll('[data-status-filter]'));
        var sortSelect = mPanel.querySelector('[data-sort-select]');
        var sortToggle = mPanel.querySelector('[data-sort-toggle]');
        var sortTh = sortToggle ? sortToggle.closest('th') : null;
        var clearBtns = Array.prototype.slice.call(mPanel.querySelectorAll('[data-clear]'));
        var sizeSelect = mPanel.querySelector('[data-page-size]');
        var tableWrap = mPanel.querySelector('[data-table]');
        var emptyMatch = mPanel.querySelector('[data-empty-match]');
        var footer = mPanel.querySelector('[data-footer]');
        var summary = mPanel.querySelector('[data-summary]');
        var headingCount = mPanel.querySelector('[data-heading-count]');
        var pagesBox = mPanel.querySelector('[data-pages]');
        var prevBtn = mPanel.querySelector('[data-prev]');
        var nextBtn = mPanel.querySelector('[data-next]');
        var pager = mPanel.querySelector('[data-pager]');

        var state = {
            q: '',
            tokens: [],
            compact: '',
            status: 'all',
            sortDir: 0,
            page: 1,
            size: sizeSelect ? (parseInt(sizeSelect.value, 10) || 10) : 10
        };

        var ordered = memberRows.slice();

        memberRows.forEach(function (row, i) {
            row._i = i;
            index(row, row.dataset.search || row.textContent);
            row._n = fold(row.dataset.name || '');
            row._active = row.dataset.active === 'true';
            row._hl = Array.prototype.slice.call(row.querySelectorAll('.hl'));
        });

        var activeTotal = memberRows.filter(function (r) { return r._active; }).length;
        var activeStat = root.querySelector('[data-stat="active"]');
        if (activeStat) activeStat.textContent = activeTotal;

        var plural = function (n) { return 'student' + (n === 1 ? '' : 's'); };

        var applySort = function () {

            if (state.sortDir) {
                ordered = memberRows.slice().sort(function (a, b) {
                    var r = a._n.localeCompare(b._n, undefined, { numeric: true });
                    return r !== 0 ? r * state.sortDir : a._i - b._i;
                });
            } else {
                ordered = memberRows.slice();
            }

            var frag = document.createDocumentFragment();
            ordered.forEach(function (row) { frag.appendChild(row); });
            tbody.appendChild(frag);

            if (sortTh) sortTh.setAttribute('aria-sort', state.sortDir === 1 ? 'ascending' : state.sortDir === -1 ? 'descending' : 'none');
            if (sortToggle) {
                var icon = sortToggle.querySelector('i');
                if (icon) icon.className = 'bi ' + (state.sortDir === 1 ? 'bi-sort-up' : state.sortDir === -1 ? 'bi-sort-down' : 'bi-arrow-down-up');
                sortToggle.classList.toggle('is-sorted', state.sortDir !== 0);
            }
            if (sortSelect) sortSelect.value = state.sortDir ? String(state.sortDir) : '';
        };

        var renderPager = function (pages) {

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
        };

        var render = function (scrollToTop) {

            /* search first, so the status counts always reflect what you typed */
            var searched = ordered.filter(function (r) { return matchTokens(r, state.tokens, state.compact); });

            var counts = { all: searched.length, active: 0, inactive: 0 };
            searched.forEach(function (r) { if (r._active) counts.active++; else counts.inactive++; });

            ['all', 'active', 'inactive'].forEach(function (k) {
                var el = mPanel.querySelector('[data-count="' + k + '"]');
                if (el) el.textContent = counts[k];
            });

            var list = searched.filter(function (r) {
                return state.status === 'all' || (state.status === 'active') === r._active;
            });

            var total = list.length;
            var pages = Math.max(1, Math.ceil(total / state.size));

            if (state.page > pages) state.page = pages;

            var start = (state.page - 1) * state.size;
            var end = Math.min(start + state.size, total);

            memberRows.forEach(function (row) { row.hidden = true; });

            list.slice(start, end).forEach(function (row) {
                row.hidden = false;
                row._hl.forEach(function (el) { highlight(el, state.tokens); });
            });

            var filtered = !!(state.q || state.status !== 'all');

            if (tableWrap) tableWrap.hidden = total === 0;
            if (emptyMatch) emptyMatch.hidden = total !== 0;
            if (footer) footer.hidden = total === 0 || (pages === 1 && memberRows.length <= 10);

            if (summary) {
                summary.textContent = total
                    ? 'Showing ' + (start + 1) + '–' + end + ' of ' + total + ' ' + plural(total)
                    : '';
            }

            if (headingCount) {
                headingCount.innerHTML = '';
                var strong = document.createElement('strong');
                if (filtered) {
                    strong.textContent = total;
                    headingCount.appendChild(strong);
                    headingCount.appendChild(document.createTextNode(' of ' + memberRows.length + ' ' + plural(memberRows.length) + ' match'));
                } else {
                    strong.textContent = memberRows.length;
                    headingCount.appendChild(strong);
                    headingCount.appendChild(document.createTextNode(' ' + plural(memberRows.length) + ' enrolled'));
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

            clearBtns.forEach(function (btn) {
                if (btn.classList.contains('clear-filters')) btn.hidden = !(filtered || state.sortDir);
            });

            if (scrollToTop === true) {
                var card = root.querySelector('.records-card');
                if (card && card.getBoundingClientRect().top < 0) {
                    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }
        };

        var setQuery = function (raw) {
            state.q = raw.trim();
            state.tokens = tokensOf(raw);
            state.compact = state.tokens.join('');
        };

        var onSearchNow = function () {
            setQuery(searchInput ? searchInput.value : '');
            state.page = 1;
            render();
        };

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

        if (sortSelect) {
            sortSelect.addEventListener('change', function () {
                state.sortDir = parseInt(sortSelect.value, 10) || 0;
                state.page = 1;
                applySort();
                render();
            });
        }

        if (sortToggle) {
            sortToggle.addEventListener('click', function () {
                state.sortDir = state.sortDir === 0 ? 1 : state.sortDir === 1 ? -1 : 0;
                state.page = 1;
                applySort();
                render();
            });
        }

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
                state.sortDir = 0;
                state.page = 1;
                if (searchInput) searchInput.value = '';
                applySort();
                render();
            });
        });

        render();
    }

    /* ============================================================
       ADD LIST: search, multi-select, sticky action bar
       ============================================================ */

    var addForm = root.querySelector('[data-add-form]');

    var syncSelection = function () {};

    if (addForm && addItems.length) {

        var addSearch = addForm.querySelector('[data-add-search]');
        var addSearchClear = addForm.querySelector('[data-add-search-clear]');
        var addMeta = addForm.querySelector('[data-add-meta]');
        var addEmpty = addForm.querySelector('[data-add-empty]');
        var addList = addForm.querySelector('[data-add-list]');
        var selText = addForm.querySelector('[data-selected-text]');
        var addSubmit = addForm.querySelector('[data-add-submit]');
        var selectShown = addForm.querySelector('[data-select-shown]');
        var clearSel = addForm.querySelector('[data-clear-selection]');

        var aTokens = [];
        var aCompact = '';

        addItems.forEach(function (item) {
            index(item, item.dataset.search || item.textContent);
            item._box = item.querySelector('input[type="checkbox"]');
            item._hl = Array.prototype.slice.call(item.querySelectorAll('.hl'));
        });

        syncSelection = function () {

            var n = 0;

            addItems.forEach(function (item) {
                var on = item._box.checked;
                item.classList.toggle('is-checked', on);
                if (on) n++;
            });

            if (selText) {
                selText.textContent = n === 0
                    ? 'No students selected'
                    : n + ' student' + (n === 1 ? '' : 's') + ' selected';
                selText.classList.toggle('has-selection', n > 0);
            }

            if (addSubmit && !addSubmit._busy) addSubmit.disabled = n === 0;
        };

        var filterAdd = function () {

            var shown = 0;

            addItems.forEach(function (item) {
                var ok = matchTokens(item, aTokens, aCompact);
                item.hidden = !ok;
                if (ok) {
                    shown++;
                    item._hl.forEach(function (el) { highlight(el, aTokens); });
                }
            });

            if (addList) addList.hidden = shown === 0;
            if (addEmpty) addEmpty.hidden = shown !== 0;

            if (addMeta) {
                addMeta.textContent = aTokens.length
                    ? shown + ' of ' + addItems.length + ' shown'
                    : addItems.length + ' available';
            }

            if (addSearchClear) addSearchClear.hidden = !(addSearch && addSearch.value);
        };

        var addRaf = 0;

        if (addSearch) {

            addSearch.addEventListener('input', function () {
                if (addRaf) cancelAnimationFrame(addRaf);
                addRaf = requestAnimationFrame(function () {
                    aTokens = tokensOf(addSearch.value);
                    aCompact = aTokens.join('');
                    filterAdd();
                });
            });

            addSearch.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') e.preventDefault();       /* Enter must not submit the form */
                if (e.key === 'Escape' && addSearch.value) {
                    addSearch.value = '';
                    aTokens = [];
                    aCompact = '';
                    filterAdd();
                }
            });
        }

        if (addSearchClear) {
            addSearchClear.addEventListener('click', function () {
                addSearch.value = '';
                aTokens = [];
                aCompact = '';
                filterAdd();
                addSearch.focus();
            });
        }

        addForm.addEventListener('change', function (e) {
            if (e.target && e.target.type === 'checkbox') syncSelection();
        });

        if (selectShown) {
            selectShown.addEventListener('click', function () {
                addItems.forEach(function (item) { if (!item.hidden) item._box.checked = true; });
                syncSelection();
            });
        }

        if (clearSel) {
            clearSel.addEventListener('click', function () {
                addItems.forEach(function (item) { item._box.checked = false; });
                syncSelection();
            });
        }

        addForm.addEventListener('submit', function (e) {

            if (addItems.every(function (item) { return !item._box.checked; })) {
                e.preventDefault();
                return;
            }

            if (addSubmit) {
                addSubmit._busy = true;
                addSubmit.disabled = true;
                addSubmit.textContent = 'Adding…';
            }
        });

        filterAdd();
        syncSelection();
    }

    /* ============================================================
       REMOVE CONFIRMATION MODAL (blurred backdrop)
       ============================================================ */

    var modal = document.querySelector('[data-remove-modal]');
    var pendingForm = null;
    var lastTrigger = null;

    var FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), ' +
                    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function openRemove(btn) {

        pendingForm = btn.closest('form');
        lastTrigger = btn;

        modal.querySelector('[data-remove-name]').textContent = btn.dataset.studentName || 'This student';

        modal.classList.add('show');
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');

        var f = modal.querySelector('[data-autofocus]');
        if (f) setTimeout(function () { f.focus(); }, 60);
    }

    function closeRemove() {

        modal.classList.remove('show');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('modal-open');
        pendingForm = null;

        if (lastTrigger && lastTrigger.focus && document.contains(lastTrigger)) lastTrigger.focus();
    }

    if (modal) {

        document.addEventListener('click', function (e) {

            var btn = e.target.closest && e.target.closest('[data-remove]');

            if (btn) {
                e.preventDefault();
                openRemove(btn);
                return;
            }

            if (e.target.closest && e.target.closest('[data-close]') && modal.contains(e.target)) {
                closeRemove();
            }
        });

        /* pressing the blurred backdrop closes the dialog */
        modal.addEventListener('mousedown', function (e) {
            if (e.target === modal) closeRemove();
        });

        var confirmBtn = modal.querySelector('[data-confirm-remove]');

        confirmBtn.addEventListener('click', function () {

            if (!pendingForm) return;

            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Removing…';

            pendingForm.submit();
        });
    }

    /* ============================================================
       KEYBOARD
       ============================================================ */

    document.addEventListener('keydown', function (e) {

        var open = modal && modal.classList.contains('show');

        if (open) {

            if (e.key === 'Escape') { closeRemove(); return; }

            if (e.key === 'Tab') {
                var items = Array.prototype.filter.call(modal.querySelectorAll(FOCUSABLE), function (el) {
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

        /* "/" jumps to the search box of the visible tab */
        if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
            var tag = (document.activeElement && document.activeElement.tagName) || '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

            var visible = root.querySelector('[data-panel]:not([hidden])');
            var search = visible && visible.querySelector('[data-search-input], [data-add-search]');

            if (search) {
                e.preventDefault();
                search.focus();
                search.select();
            }
        }
    });

    /* back button / bfcache: restore a clean state */
    window.addEventListener('pageshow', function (e) {

        if (!e.persisted) return;

        var confirmBtn = modal && modal.querySelector('[data-confirm-remove]');
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="fas fa-user-minus" aria-hidden="true"></i> Remove student';
        }

        var submit = root.querySelector('[data-add-submit]');
        if (submit) {
            submit._busy = false;
            submit.innerHTML = '<i class="bi bi-person-plus" aria-hidden="true"></i> Add to ' + (root.dataset.sectionName || 'section');
        }

        syncSelection();
    });

})();