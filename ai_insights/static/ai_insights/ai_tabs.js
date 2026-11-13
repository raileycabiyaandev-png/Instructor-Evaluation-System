/* ai_insights/static/ai_insights/ai_tabs.js
   Tiny helper for the AI pages (no library needed):
   1. Tabs:   [data-ai-tabs] holds buttons [data-ai-tab="name"] and panels [data-ai-panel="name"].
   2. Filter: [data-ai-filter-group] holds chips [data-ai-filter="value"]; cards have data-filter-value.
   Everything still shows (all tabs open one after the other) if JavaScript is off. */
(function () {
    function initTabs(root) {
        var tabs = [].slice.call(root.querySelectorAll('[data-ai-tab]'));
        var panels = [].slice.call(root.querySelectorAll('[data-ai-panel]'));
        if (!tabs.length) return;
        function show(name, focus) {
            tabs.forEach(function (t) {
                var on = t.getAttribute('data-ai-tab') === name;
                t.setAttribute('aria-pressed', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;
                if (on && focus) t.focus();
            });
            panels.forEach(function (p) { p.hidden = p.getAttribute('data-ai-panel') !== name; });
        }
        tabs.forEach(function (t, i) {
            t.addEventListener('click', function () { show(t.getAttribute('data-ai-tab')); });
            t.addEventListener('keydown', function (e) {
                var next = null;
                if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
                if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
                if (next) { e.preventDefault(); show(next.getAttribute('data-ai-tab'), true); }
            });
        });
        show(root.getAttribute('data-ai-start') || tabs[0].getAttribute('data-ai-tab'));
    }

    function initFilter(group) {
        var scope = group.closest('[data-ai-filter-scope]') || document;
        var chips = [].slice.call(group.querySelectorAll('[data-ai-filter]'));
        var cards = [].slice.call(scope.querySelectorAll('[data-filter-value]'));
        var empty = scope.querySelector('[data-ai-empty]');
        function apply(value) {
            chips.forEach(function (c) { c.setAttribute('aria-pressed', c.getAttribute('data-ai-filter') === value ? 'true' : 'false'); });
            var shown = 0;
            cards.forEach(function (card) {
                var ok = value === 'all' || card.getAttribute('data-filter-value') === value;
                card.hidden = !ok;
                if (ok) shown += 1;
            });
            if (empty) empty.hidden = shown !== 0;
        }
        chips.forEach(function (c) { c.addEventListener('click', function () { apply(c.getAttribute('data-ai-filter')); }); });
        apply('all');
    }

    document.querySelectorAll('[data-ai-tabs]').forEach(initTabs);
    document.querySelectorAll('[data-ai-filter-group]').forEach(initFilter);
})();