// Landing page behaviour: the page is fully readable without this script.
// It only adds the mobile menu and the "scrolled" nav style.
(function () {
  "use strict";
  var nav = document.getElementById("nav");
  var toggle = document.getElementById("menu-toggle");
  if (!nav || !toggle) return;

  function onScroll() {
    nav.classList.toggle("scrolled", window.scrollY > 24);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  function setOpen(open) {
    nav.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  toggle.addEventListener("click", function () {
    setOpen(!nav.classList.contains("open"));
  });
  nav.querySelectorAll(".nav-links a").forEach(function (link) {
    link.addEventListener("click", function () { setOpen(false); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
})();
