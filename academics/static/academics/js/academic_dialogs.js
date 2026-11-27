/* ============================================================
   academics/js/academic_dialogs.js

   Two things for the academics list pages (Departments, Subjects,
   Sections, Teaching Assignments):

   1. EDIT window   - the Edit link opens the edit form in the same
                      styled window as "Add". The form is loaded from
                      the same edit URL and posted to the same URL, so
                      the backend is not touched. If anything fails,
                      the browser simply goes to the normal edit page.
   2. CONFIRM window - Delete / Deactivate / Activate show a clean
                      confirmation window instead of the browser popup.

   Load it AFTER academics.js and directory.js.
   ============================================================ */
(function () {
  "use strict";

  var current = null; // the window that is open right now
  var lastFocus = null;
  var dirty = false;

  /* ---------- small helpers ---------- */
  function make(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  function mount(node) {
    lastFocus = document.activeElement;
    document.body.appendChild(node);
    document.body.classList.add("ad-lock");
    current = node;
    dirty = false;
  }
  function unmount() {
    if (!current) return;
    current.remove();
    current = null;
    document.body.classList.remove("ad-lock");
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) {
      lastFocus.focus();
    }
  }

  /* ============================================================
     CONFIRM WINDOW (delete / deactivate / activate)
     ============================================================ */
  var KINDS = {
    delete: {
      icon: "bi-trash3",
      title: function (n) { return "Delete " + n + "?"; },
      text: function (n) {
        return "This will permanently remove the " + n + ". This action cannot be undone.";
      },
      ok: "Delete",
      busy: "Deleting…"
    },
    deactivate: {
      icon: "bi-slash-circle",
      title: function (n) { return "Deactivate " + n + "?"; },
      text: function (n) {
        return "The " + n + " will be marked as inactive. You can activate it again at any time.";
      },
      ok: "Deactivate",
      busy: "Deactivating…"
    },
    activate: {
      icon: "bi-check-circle",
      title: function (n) { return "Activate " + n + "?"; },
      text: function (n) {
        return "The " + n + " will be marked as active again.";
      },
      ok: "Activate",
      busy: "Activating…"
    }
  };

  function confirmDialog(form) {
    var kind = form.classList.contains("delete-form")
      ? "delete"
      : form.querySelector(".btn-deactivate")
        ? "deactivate"
        : "activate";
    var cfg = KINDS[kind];

    var m = /this\s+(.+?)\s*\?*$/i.exec(form.getAttribute("data-confirm") || "");
    var noun = m ? m[1] : "record";

    var row = form.closest("tr");
    var name = "";
    if (row) {
      var code = row.getAttribute("data-code") || "";
      var title = row.getAttribute("data-name") || "";
      if (!title) {
        var t = row.querySelector(".cell-title");
        title = t ? t.textContent.trim() : "";
      }
      name = code && title ? code + " — " + title : code || title;
    }

    var overlay = make("div", "ad-overlay");
    overlay.innerHTML =
      '<div class="ad-dialog ad-dialog--' + kind + '" role="alertdialog" aria-modal="true"' +
      ' aria-labelledby="adTitle" aria-describedby="adText">' +
      '<div class="ad-body">' +
      '<span class="ad-icon" aria-hidden="true"><i class="bi ' + cfg.icon + '"></i></span>' +
      '<div class="ad-text">' +
      '<h3 id="adTitle">' + esc(cfg.title(noun)) + "</h3>" +
      '<p id="adText">' + esc(cfg.text(noun)) + "</p>" +
      (name ? '<span class="ad-name">' + esc(name) + "</span>" : "") +
      "</div></div>" +
      '<div class="ad-foot">' +
      '<button type="button" class="ad-btn ad-btn--ghost" data-ad-close>Cancel</button>' +
      '<button type="button" class="ad-btn ad-btn--' + kind + '" data-ad-ok>' + esc(cfg.ok) + "</button>" +
      "</div></div>";

    mount(overlay);

    overlay.querySelector("[data-ad-ok]").addEventListener("click", function (e) {
      var ok = e.currentTarget;
      ok.disabled = true;
      ok.textContent = cfg.busy;
      overlay.querySelector("[data-ad-close]").disabled = true;
      form.dataset.adOk = "1";
      form.submit(); // normal submit: the backend receives exactly what it did before
    });

    overlay.querySelector("[data-ad-close]").focus();
  }

  /* ============================================================
     EDIT WINDOW
     ============================================================ */
  function findForm(doc) {
    var card = doc.querySelector(".update-card, .modern-modal");
    var form = card ? card.querySelector("form") : null;
    if (!form) {
      var forms = Array.prototype.filter.call(doc.querySelectorAll("form"), function (f) {
        return (
          f.querySelector('input[name="csrfmiddlewaretoken"]') &&
          f.querySelector("input:not([type=hidden]), select, textarea")
        );
      });
      form = forms[forms.length - 1] || null;
      card = null;
    }
    return form ? { card: card, form: form } : null;
  }

  /* Puts the loaded form into the styled window, using the same
     classes as the Add window so both look identical. */
  function fill(box, parts, baseUrl, doc) {
    box.innerHTML = "";
    var form = document.importNode(parts.form, true);

    // the form must post to the edit URL, not to the list page
    form.setAttribute("action", new URL(form.getAttribute("action") || "", baseUrl).href);
    form.setAttribute("method", "POST");
    form.removeAttribute("data-single-submit");

    if (parts.card) {
      Array.prototype.forEach.call(parts.card.children, function (child) {
        if (child.contains(parts.form)) return;
        box.appendChild(document.importNode(child, true));
      });
    }

    var titleText = "Edit";
    var h = doc.querySelector("main h1, main h2, main h3, h1, h2, h3");
    if (h && h.textContent.trim()) titleText = h.textContent.trim();

    // the header may be outside the form or inside it
    var header = box.querySelector(".modal-header-modern") || form.querySelector(".modal-header-modern");

    if (!header) {
      header = make(
        "div",
        "modal-header-modern",
        '<div class="modal-heading"><h3>' + esc(titleText) + "</h3></div>"
      );
      box.insertBefore(header, box.firstChild);
    }
    var heading = header.querySelector("h1, h2, h3");
    if (heading) heading.id = "adEditTitle";
    if (!header.querySelector(".close-btn")) {
      header.appendChild(
        make("button", "close-btn", '<i class="bi bi-x-lg" aria-hidden="true"></i>')
      ).setAttribute("type", "button");
      header.querySelector(".close-btn").setAttribute("aria-label", "Close");
    }

    // make sure the form has the body + footer structure
    if (!form.querySelector(".modal-body-modern")) {
      var body = make("div", "modal-body-modern");
      while (form.firstChild) body.appendChild(form.firstChild);
      Array.prototype.forEach.call(
        body.querySelectorAll("button[type=submit], input[type=submit]"),
        function (b) { b.remove(); }
      );
      form.appendChild(body);
    }
    if (!form.querySelector(".modal-footer-modern")) {
      form.appendChild(
        make(
          "div",
          "modal-footer-modern",
          '<button type="button" class="btn-secondary" data-ad-close>Cancel</button>' +
            '<button type="submit" class="btn-primary"><i class="bi bi-check-lg" aria-hidden="true"></i> Save changes</button>'
        )
      );
    }

    box.appendChild(form);
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("change", function () { dirty = true; });
  }

  function showEdit(parts, baseUrl, doc) {
    var overlay = make("div", "modal show ad-edit");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "adEditTitle");
    var box = make("div", "modal-content modern-modal");
    overlay.appendChild(box);

    mount(overlay);
    fill(box, parts, baseUrl, doc);

    var first = box.querySelector(
      'input:not([type=hidden]):not([type=checkbox]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
    );
    if (first) first.focus();
  }

  function openEdit(link) {
    link.classList.add("is-loading");
    link.setAttribute("aria-busy", "true");

    fetch(link.href, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("load failed");
        return res.text().then(function (html) { return { html: html, url: res.url }; });
      })
      .then(function (r) {
        var doc = new DOMParser().parseFromString(r.html, "text/html");
        var parts = findForm(doc);
        if (!parts) throw new Error("no form");
        showEdit(parts, r.url, doc);
      })
      .catch(function () {
        window.location.href = link.href; // fall back to the normal edit page
      })
      .then(function () {
        link.classList.remove("is-loading");
        link.removeAttribute("aria-busy");
      });
  }

  function submitEdit(form) {
    var btn = form.querySelector('button[type="submit"]');
    var label = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Saving…";
    }

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      redirect: "manual" // do not follow it: the success message must still be waiting for the page reload
    })
      .then(function (res) {
        // success: the backend answers with a redirect -> reload the list to show its message
        if (res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400)) {
          window.location.reload();
          return null;
        }
        return res.text().then(function (html) { return { html: html, url: res.url || form.action }; });
      })
      .then(function (r) {
        if (!r) return;
        var doc = new DOMParser().parseFromString(r.html, "text/html");
        var parts = findForm(doc);
        if (!parts) { window.location.reload(); return; }
        // validation errors: show the form again with its messages
        var box = form.closest(".modern-modal");
        fill(box, parts, form.action, doc);
        dirty = true;
      })
      .catch(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = label; }
        form.submit(); // last resort: normal submit
      });
  }

  /* ============================================================
     EVENTS (capture phase, so they run before the old popup code)
     ============================================================ */
  document.addEventListener(
    "click",
    function (e) {
      var t = e.target;
      if (!t || !t.closest) return;

      // 1. Edit link -> styled window
      var edit = t.closest("a.btn-edit");
      if (
        edit && !edit.closest(".ad-edit") && !current &&
        e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
      ) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!edit.classList.contains("is-loading")) openEdit(edit);
        return;
      }

      // 2. Delete / Deactivate / Activate -> confirm window
      var act = t.closest(".status-form button, .delete-form button");
      if (act) {
        var f = act.closest("form");
        if (f && f.hasAttribute("data-confirm") && !f.dataset.adOk) {
          e.preventDefault();
          e.stopImmediatePropagation();
          if (!current) confirmDialog(f);
          return;
        }
      }

      // 3. closing the open window
      if (!current) return;
      if (
        t.closest(
          "[data-ad-close], .ad-edit .close-btn, .ad-edit [data-close-modal], " +
            ".ad-edit .modal-footer-modern a:not(.btn-primary), .ad-edit .modal-footer-modern .btn-secondary"
        )
      ) {
        e.preventDefault();
        e.stopImmediatePropagation();
        unmount();
        return;
      }
      if (t === current && !dirty) unmount(); // click on the dark area
    },
    true
  );

  // Enter key inside a confirm form, or the edit form being submitted
  document.addEventListener(
    "submit",
    function (e) {
      var f = e.target;
      if (!f || !f.closest) return;

      if (f.closest(".ad-edit")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        submitEdit(f);
        return;
      }
      if (f.hasAttribute("data-confirm") && !f.dataset.adOk) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!current) confirmDialog(f);
      }
    },
    true
  );

  document.addEventListener("keydown", function (e) {
    if (!current) return;

    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      unmount();
      return;
    }

    if (e.key === "Tab") {
      var items = Array.prototype.filter.call(
        current.querySelectorAll(
          'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
        ),
        function (el) { return el.offsetParent !== null; }
      );
      if (!items.length) return;
      var a = items[0];
      var z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        z.focus();
      } else if (!e.shiftKey && document.activeElement === z) {
        e.preventDefault();
        a.focus();
      }
    }
  }, true);

  // coming back with the browser Back button: never leave a window stuck open
  window.addEventListener("pageshow", function () { unmount(); });
})();