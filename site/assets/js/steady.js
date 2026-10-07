// Landing page behaviour: the page is fully readable without this script.
// It adds the mobile menu, the "scrolled" nav style, scroll reveals and number count-ups.
(function () {
  "use strict";
  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.classList.add("js");

  // Navigation
  var nav = document.getElementById("nav");
  var toggle = document.getElementById("menu-toggle");
  if (nav && toggle) {
    var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 24); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    var setOpen = function (open) {
      nav.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    toggle.addEventListener("click", function () { setOpen(!nav.classList.contains("open")); });
    nav.querySelectorAll(".nav-links a").forEach(function (link) {
      link.addEventListener("click", function () { setOpen(false); });
    });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") setOpen(false); });
  }

  // Bars grow one after another
  document.querySelectorAll(".bars li").forEach(function (bar, i) { bar.style.setProperty("--i", i); });

  // Count-up: each number runs from data-from to data-to, ending on the value already in the HTML
  function countUp(el) {
    var from = parseFloat(el.dataset.from), to = parseFloat(el.dataset.to);
    var dec = parseInt(el.dataset.dec || "0", 10), unit = el.dataset.unit || "";
    var start = null, duration = 1600;
    function frame(now) {
      if (start === null) start = now;
      var t = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 4);
      el.textContent = (from + (to - from) * eased).toFixed(dec) + unit;
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // Scroll reveal
  var groups = [
    ".head", ".path-panel", ".history", ".compare", ".note", ".honest",
    ".grid-3 > *", ".stat", ".trust li", ".faq details", ".cta-grid > *"
  ];
  var targets = [];
  groups.forEach(function (selector) {
    var siblings = new Map();
    document.querySelectorAll(selector).forEach(function (el) {
      if (el.closest(".hero")) return;
      var index = siblings.has(el.parentNode) ? siblings.get(el.parentNode) + 1 : 0;
      siblings.set(el.parentNode, index);
      el.classList.add("reveal");
      el.style.setProperty("--d", Math.min(index, 6) * 0.08 + "s");
      targets.push(el);
    });
  });
  // The history and compare panels animate their inner bars too
  document.querySelectorAll(".history, .compare").forEach(function (el) {
    if (targets.indexOf(el) < 0) { el.classList.add("reveal"); targets.push(el); }
  });

  function show(el) {
    el.classList.add("in");
    el.querySelectorAll("[data-to]").forEach(function (num) { if (!reduced) countUp(num); });
  }

  if (reduced || !("IntersectionObserver" in window)) {
    targets.forEach(show);
    return;
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        show(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -10% 0px", threshold: 0.15 });
  targets.forEach(function (el) { observer.observe(el); });
})();
