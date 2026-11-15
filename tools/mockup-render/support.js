// Minimal stand-in for the design canvas runtime, enough to render the
// hondalog mockup artboards headlessly for README screenshots.
class DCLogic {
  constructor(props) { this.props = props || {}; this.state = {}; }
  setState(s) { Object.assign(this.state, s || {}); window.__dcSchedule && window.__dcSchedule(); }
}
window.DCLogic = DCLogic;

(function () {
  const EXPR = /\{\{\s*([^}]+?)\s*\}\}/g;
  function lookup(expr, scopes) {
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
    const parts = expr.split('.');
    for (let i = scopes.length - 1; i >= 0; i--) {
      if (scopes[i] && parts[0] in Object(scopes[i])) {
        let v = scopes[i];
        for (const p of parts) { v = v == null ? undefined : v[p]; }
        return v;
      }
    }
    return undefined;
  }
  function interp(str, scopes) {
    return str.replace(EXPR, (_, e) => { const v = lookup(e, scopes); return v == null ? '' : String(v); });
  }
  const refEls = new Map();
  let pendingRefs = [];

  function processNode(node, scopes, out) {
    if (node.nodeType === 3) { out.appendChild(document.createTextNode(interp(node.nodeValue, scopes))); return; }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toLowerCase();
    if (tag === 'sc-for') {
      const list = lookup((node.getAttribute('list') || '').replace(/^\{\{|\}\}$/g, '').trim(), scopes) || [];
      const as = node.getAttribute('as') || 'item';
      list.forEach((item, idx) => {
        const s = {}; s[as] = item; s.index = idx;
        for (const ch of node.childNodes) processNode(ch, scopes.concat([s]), out);
      });
      return;
    }
    if (tag === 'sc-if') {
      const v = lookup((node.getAttribute('value') || '').replace(/^\{\{|\}\}$/g, '').trim(), scopes);
      if (v) for (const ch of node.childNodes) processNode(ch, scopes, out);
      return;
    }
    const isSvg = node.namespaceURI === 'http://www.w3.org/2000/svg';
    const el = isSvg ? document.createElementNS(node.namespaceURI, node.tagName) : document.createElement(node.tagName);
    let refFn = null;
    for (const a of Array.from(node.attributes)) {
      const m = /^\{\{\s*([^}]+?)\s*\}\}$/.exec(a.value);
      if (m) {
        const v = lookup(m[1], scopes);
        if (a.name === 'ref' && typeof v === 'function') { refFn = v; continue; }
        if (/^on[a-z]+$/i.test(a.name) && typeof v === 'function') {
          el.addEventListener(a.name.slice(2).toLowerCase(), v); continue;
        }
        if (v === false || v == null) continue;
        el.setAttribute(a.name, v === true ? '' : String(v));
      } else {
        el.setAttribute(a.name, interp(a.value, scopes));
      }
    }
    if (refFn) {
      const key = refFn.toString() + '|' + tag;
      if (refEls.has(key)) { out.appendChild(refEls.get(key)); return; }
      refEls.set(key, el);
      pendingRefs.push([refFn, el]);
    }
    for (const ch of node.childNodes) processNode(ch, scopes, el);
    out.appendChild(el);
  }

  window.addEventListener('DOMContentLoaded', () => {
    const host = document.querySelector('x-dc');
    const helmet = host.querySelector('helmet');
    if (helmet) { for (const ch of Array.from(helmet.childNodes)) document.head.appendChild(ch); helmet.remove(); }
    const tpl = document.createElement('div');
    tpl.innerHTML = host.innerHTML;
    host.innerHTML = '';
    const script = document.querySelector('script[data-dc-script]');
    const propsDef = JSON.parse(script.getAttribute('data-props') || '{}');
    const props = {};
    for (const k in propsDef) if (!k.startsWith('$')) props[k] = propsDef[k].default;
    const Component = new Function('DCLogic', script.textContent + '\nreturn Component;')(DCLogic);
    const comp = new Component(props);
    let queued = false;
    function render() {
      queued = false;
      const vals = comp.renderVals ? comp.renderVals() : {};
      const frag = document.createDocumentFragment();
      pendingRefs = [];
      for (const ch of tpl.childNodes) processNode(ch, [comp, vals], frag);
      host.replaceChildren(frag);
      for (const [fn, el] of pendingRefs) fn(el);
    }
    window.__dcSchedule = () => { if (!queued) { queued = true; requestAnimationFrame(render); } };
    render();
    if (comp.componentDidMount) comp.componentDidMount();
    window.__dcSchedule();
  });
})();
