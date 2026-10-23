// academics.js - small helpers shared by the Academic Management pages.
(function () {
    // Open a modal:  <button data-open-modal="myModalId">
    document.querySelectorAll('[data-open-modal]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var modal = document.getElementById(btn.getAttribute('data-open-modal'));
            if (modal) modal.classList.add('show');
        });
    });

    // Close a modal: the X / Cancel button, or a click on the dark background
    document.querySelectorAll('.modal').forEach(function (modal) {
        modal.querySelectorAll('[data-close-modal]').forEach(function (btn) {
            btn.addEventListener('click', function () { modal.classList.remove('show'); });
        });
        modal.addEventListener('click', function (e) {
            if (e.target === modal) modal.classList.remove('show');
        });
        // If the form had errors, show the modal again so the admin can fix them
        if (modal.dataset.hasErrors === '1') modal.classList.add('show');
    });

    // Live search: <input data-table-search="tableId">
    document.querySelectorAll('[data-table-search]').forEach(function (input) {
        input.addEventListener('input', function () {
            var term = input.value.toLowerCase();
            document.querySelectorAll('#' + input.getAttribute('data-table-search') + ' tbody tr')
                .forEach(function (row) {
                    row.style.display = row.innerText.toLowerCase().indexOf(term) > -1 ? '' : 'none';
                });
        });
    });

    // Ask before a risky action: <form data-confirm="Are you sure?">
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            if (!window.confirm(form.getAttribute('data-confirm'))) e.preventDefault();
        });
    });

    // Stop double clicks on Save buttons
    document.querySelectorAll('form[data-single-submit]').forEach(function (form) {
        form.addEventListener('submit', function () {
            form.querySelectorAll('button[type="submit"]').forEach(function (b) { b.disabled = true; });
        });
    });

    // Filter dropdown that reloads the page
    document.querySelectorAll('select[data-auto-submit]').forEach(function (sel) {
        sel.addEventListener('change', function () { sel.form.submit(); });
    });
})();
