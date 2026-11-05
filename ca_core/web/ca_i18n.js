/* CA Tools i18n – DE/EN/ES/FR. CA_I18n.set(payload); CA_I18n.t(key); CA_I18n.apply(root?) */
(function (w) {
  'use strict';
  var S = { lang: 'de', strings: {}, languages: [] };
  function t(key) {
    var s = S.strings[key];
    return (s !== undefined && s !== null && s !== '') ? s : key;
  }
  function apply(root) {
    root = root || document;
    var nodes = root.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i], key = el.getAttribute('data-i18n');
      if (!key) continue;
      var attr = el.getAttribute('data-i18n-attr');
      if (attr) el.setAttribute(attr, t(key));
      else el.textContent = t(key);
    }
    var ph = root.querySelectorAll('[data-i18n-placeholder]');
    for (var j = 0; j < ph.length; j++) ph[j].setAttribute('placeholder', t(ph[j].getAttribute('data-i18n-placeholder')));
    var ti = root.querySelectorAll('[data-i18n-title]');
    for (var k = 0; k < ti.length; k++) ti[k].setAttribute('title', t(ti[k].getAttribute('data-i18n-title')));
    try { document.documentElement.lang = S.lang; } catch (e) {}
  }
  function set(payload) {
    if (!payload) return;
    S.lang = payload.language || 'de';
    S.strings = payload.strings || {};
    S.languages = payload.languages || [];
    apply(document);
  }
  function fillSelect(sel, current) {
    if (!sel) return;
    sel.innerHTML = '';
    (S.languages || []).forEach(function (x) {
      var o = document.createElement('option');
      o.value = x.id; o.textContent = x.label;
      sel.appendChild(o);
    });
    sel.value = current || S.lang;
  }
  w.CA_I18n = { t: t, set: set, apply: apply, fillSelect: fillSelect, getLang: function () { return S.lang; } };
})(window);
