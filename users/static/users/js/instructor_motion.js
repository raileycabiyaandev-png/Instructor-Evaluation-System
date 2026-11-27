/* Instructor dashboard motion (frontend only). */
(function () {
  "use strict";
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!("IntersectionObserver" in window)) return;

  var PATTERN = /^(\D*?)(\d+(?:\.\d+)?)(\D*)$/;

  function ease(t) { return 1 - Math.pow(1 - t, 3); }

  function countUp(el) {
    var original = el.textContent.trim();
    var m = original.match(PATTERN);
    if (!m) return;
    var target = parseFloat(m[2]);
    if (!isFinite(target) || target === 0) return;
    var decimals = (m[2].split(".")[1] || "").length;
    var start = null, duration = 800;

    el.style.minWidth = el.getBoundingClientRect().width + "px"; /* no layout jump */

    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      el.textContent = m[1] + (target * ease(p)).toFixed(decimals) + m[3];
      if (p < 1) requestAnimationFrame(frame);
      else { el.textContent = original; el.style.minWidth = ""; }
    }
    requestAnimationFrame(frame);
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { io.unobserve(e.target); countUp(e.target); }
    });
  }, { threshold: 0.4 });

  document.querySelectorAll(".dash .stat-value, .dash .hero-number").forEach(function (el) {
    io.observe(el);
  });
})();