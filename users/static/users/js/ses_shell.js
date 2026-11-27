/* ============================================================
   users/static/users/js/ses_shell.js
   Sidebar behaviour shared by Admin, Instructor and Student:
   - mobile drawer (menu button, overlay, Esc, swipe left, focus return)
   - dropdown groups (.nav-item with .nav-toggle), one open at a time
   Exposes toggleSidebar() and closeSidebar() for page scripts.
   Load it BEFORE any page script that calls them.
   ============================================================ */
(function () {
    'use strict';

    var root    = document.documentElement;
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    var toggle  = document.getElementById('menuToggle');
    var overlay = document.getElementById('sbOverlay');
    var desktop = window.matchMedia('(min-width: 992px)');

    function isOpen() { return sidebar.classList.contains('sidebar-open'); }

    function setOpen(open, returnFocus) {
        sidebar.classList.toggle('sidebar-open', open);
        root.classList.toggle('shell-locked', open);
        if (toggle) {
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
        }
        if (open) {
            var first = sidebar.querySelector('.nav-link');
            if (first) first.focus({ preventScroll: true });
        } else if (returnFocus && toggle) {
            toggle.focus();
        }
    }

    window.toggleSidebar = function () { setOpen(!isOpen(), true); };
    window.closeSidebar  = function () { setOpen(false, false); };

    if (toggle)  toggle.addEventListener('click', window.toggleSidebar);
    if (overlay) overlay.addEventListener('click', function () { setOpen(false, true); });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isOpen()) setOpen(false, true);
    });

    desktop.addEventListener('change', function (e) { if (e.matches) setOpen(false, false); });

    /* Swipe left on the drawer to close it */
    var startX = null, startY = 0;
    sidebar.addEventListener('touchstart', function (e) {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
    }, { passive: true });
    sidebar.addEventListener('touchend', function (e) {
        if (startX === null) return;
        var dx = e.changedTouches[0].clientX - startX;
        var dy = e.changedTouches[0].clientY - startY;
        startX = null;
        if (isOpen() && dx < -60 && Math.abs(dx) > Math.abs(dy) * 1.5) setOpen(false, true);
    }, { passive: true });

    /* Dropdown groups: open one, close the others */
    var groups = Array.prototype.slice.call(document.querySelectorAll('.nav-item'));
    function syncAria() {
        groups.forEach(function (group) {
            var btn = group.querySelector('.nav-toggle');
            if (btn) btn.setAttribute('aria-expanded', group.classList.contains('open') ? 'true' : 'false');
        });
    }
    groups.forEach(function (group) {
        var btn = group.querySelector('.nav-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            groups.forEach(function (other) { if (other !== group) other.classList.remove('open'); });
            group.classList.toggle('open');
            syncAria();
        });
    });
    syncAria();
})();