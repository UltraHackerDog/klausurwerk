// The ground (paper or ink), following the Wasser kit: ink is `data-theme="ink"` on <html>,
// paper is the absence of the attribute. Classic script, loaded blocking in <head>, so the
// chosen ground is applied before the first paint. A stored choice wins; without one the
// system preference decides and is followed live.
(function () {
  var KEY = 'wasser.ground';
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  var override = null;

  function stored() {
    if (override) return override;
    try {
      var g = localStorage.getItem(KEY);
      return g === 'ink' || g === 'paper' ? g : null;
    } catch (e) {
      return null;
    }
  }

  function current() {
    return stored() || (media && media.matches ? 'ink' : 'paper');
  }

  function apply() {
    var g = current();
    if (g === 'ink') root.setAttribute('data-theme', 'ink');
    else root.removeAttribute('data-theme');
    window.dispatchEvent(new CustomEvent('groundchange', { detail: g }));
    return g;
  }

  function set(g) {
    override = g === 'ink' ? 'ink' : 'paper';
    try {
      localStorage.setItem(KEY, override);
    } catch (e) {
      // Private mode: the choice still holds for this page view.
    }
    return apply();
  }

  if (media && media.addEventListener) media.addEventListener('change', apply);
  window.klausurwerkGround = {
    current: function () { return root.getAttribute('data-theme') === 'ink' ? 'ink' : 'paper'; },
    set: set,
  };
  apply();
})();
