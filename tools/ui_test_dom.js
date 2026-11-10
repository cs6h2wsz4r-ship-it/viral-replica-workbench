const decode = s => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, k) => ({ amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'" }[k]));
let document;
class Node {
  constructor(tag, text = '') {
    this.nodeType = tag ? 1 : 3; this.tagName = tag?.toUpperCase(); this.nodeValue = text;
    this.childNodes = []; this.attrs = {}; this.style = { setProperty() {} };
    this.dataset = new Proxy({}, { has: (_, k) => this.hasAttribute('data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())), get: (_, k) => this.getAttribute('data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())) ?? undefined,
      set: (_, k, v) => { this.setAttribute('data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), v); return true; } });
    this.classList = { add: c => this.setAttribute('class', (this.getAttribute('class') || '') + ' ' + c), remove() {} };
    this.selectionStart = 0; this.selectionEnd = 0; this.selectionDirection = 'none'; this.scrollTop = 0;
    this.currentTime = 0; this.paused = true; this.reloads = 0;
  }
  get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({ name, value })); }
  get id() { return this.getAttribute('id') || ''; }
  get parentElement() { return this.parentNode; }
  get children() { return this.childNodes.filter(n => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null; }
  get nextElementSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  get isConnected() { return this === document?.body || !!this.parentNode?.isConnected; }
  get open() { return this.hasAttribute('open'); }
  set open(v) { v ? this.setAttribute('open', '') : this.removeAttribute('open'); }
  get src() { return this.getAttribute('src'); }
  get value() { return this._value ?? (this.tagName === 'TEXTAREA' ? this.textContent : this.tagName === 'SELECT' ? (this.querySelector('option[selected]') || this.querySelector('option'))?.value || '' : this.getAttribute('value') || ''); }
  set value(v) { this._value = v; }
  get type() { return this.getAttribute('type') || ''; }
  get checked() { return this._checked ?? this.hasAttribute('checked'); }
  set checked(v) { this._checked = v; }
  getAttribute(k) { return Object.hasOwn(this.attrs, k) ? this.attrs[k] : null; }
  hasAttribute(k) { return Object.hasOwn(this.attrs, k); }
  setAttribute(k, v) { if (k === 'src' && this.attrs[k] !== String(v)) this.reloads++; this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  contains(n) { return n === this || this.childNodes.some(c => c.contains(n)); }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) { n.remove(); const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length; this.childNodes.splice(i, 0, n); n.parentNode = this; return n; }
  replaceChild(n, old) { this.insertBefore(n, old); old.remove(); return old; }
  replaceWith(n) { this.parentNode.replaceChild(n, this); }
  removeChild(n) { n.remove(); return n; }
  remove() { if (this.parentNode) { this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; } if (this.contains(document?.activeElement)) document.activeElement = document.body; }
  cloneNode(deep) { const n = new Node(this.tagName, this.nodeValue); n.attrs = { ...this.attrs }; if (deep) this.childNodes.forEach(c => n.appendChild(c.cloneNode(true))); return n; }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(c => c.textContent).join(''); }
  set textContent(v) { this.childNodes.slice().forEach(c => c.remove()); if (v) this.appendChild(new Node(null, String(v))); }
  get innerHTML() { return this.childNodes.map(n => n.nodeType === 3 ? n.nodeValue : `<${n.tagName.toLowerCase()}${n.attributes.map(a => ` ${a.name}="${a.value}"`).join('')}>${n.innerHTML}</${n.tagName.toLowerCase()}>`).join(''); }
  set innerHTML(html) {
    this.childNodes.slice().forEach(c => c.remove()); this.writes = (this.writes || 0) + 1;
    const stack = [this];
    for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith('<!--')) continue;
      if (token.startsWith('</')) { stack.pop(); continue; }
      if (token[0] !== '<') { stack.at(-1).appendChild(new Node(null, decode(token))); continue; }
      const [, tag, attrs] = token.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/) || [];
      if (!tag) continue;
      const n = new Node(tag);
      for (const [, k, a, b, c] of attrs.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) n.setAttribute(k, decode(a ?? b ?? c ?? ''));
      stack.at(-1).appendChild(n);
      if (!/^(input|img|br|hr|meta|link|col|source)$/i.test(tag)) stack.push(n);
    }
    if (this.tagName === 'TEMPLATE') this.content = this;
  }
  matches(selector) {
    return selector.split(',').some(part => {
      const pieces = part.trim().split(/\s+(?![^\[]*\])/); const last = pieces.pop();
      const simple = (n, s) => {
        const tag = s.match(/^[\w-]+/); if (tag && n.tagName !== tag[0].toUpperCase()) return false;
        for (const m of s.matchAll(/#([\w-]+)|\.([\w-]+)|\[([^\]=]+)(?:="([^"]*)")?\]/g)) {
          if (m[1] && n.id !== m[1]) return false;
          if (m[2] && !(n.getAttribute('class') || '').split(/\s+/).includes(m[2])) return false;
          if (m[3] && (!n.hasAttribute(m[3]) || (m[4] !== undefined && n.getAttribute(m[3]) !== m[4]))) return false;
        }
        return n.nodeType === 1;
      };
      if (!simple(this, last)) return false;
      let parent = this.parentNode;
      while (pieces.length) { const s = pieces.pop(); while (parent && !simple(parent, s)) parent = parent.parentNode; if (!parent) return false; parent = parent.parentNode; }
      return true;
    });
  }
  closest(s) { return this.matches(s) ? this : this.parentNode?.closest(s) || null; }
  querySelectorAll(s) { return this.childNodes.flatMap(n => [...(n.nodeType === 1 && n.matches(s) ? [n] : []), ...n.querySelectorAll(s)]); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  focus() { document.activeElement = this; }
  setSelectionRange(a, b, d) { this.selectionStart = a; this.selectionEnd = b; this.selectionDirection = d; }
  getBoundingClientRect() { return { top: this._top ?? 100, bottom: (this._top ?? 100) + 100, height: 100 }; }
  addEventListener() {}
}

function createDocument() { const body = new Node("body"); document = { body, documentElement: body, activeElement: body, createElement: tag => new Node(tag), querySelector: s => body.querySelector(s), querySelectorAll: s => body.querySelectorAll(s), elementFromPoint: () => document.anchor || null }; return document; }
// Keep legacy confirmation choices while exercising V4's real asynchronous panel.
function installPageConfirmationHarness({run, context, document, dispatch, autoConfirm = true}) {
  if (!autoConfirm) return;
  context.respondToPageConfirmation = message => {
    const accepted = typeof context.confirm === 'function' ? context.confirm(message) : true;
    const button = document.querySelector(`[data-page-confirm="${accepted ? 'accept' : 'cancel'}"]`);
    if (!button || document.querySelector('#pageConfirmation').hidden) throw Error('Expected a visible page confirmation');
    return dispatch(button);
  };
  run(`const realPageConfirmForTest = pageConfirm;
    pageConfirm = (message, title) => {
      const pending = realPageConfirmForTest(message, title);
      respondToPageConfirmation(message);
      return pending;
    };`);
}
module.exports = { Node, createDocument, installPageConfirmationHarness };
