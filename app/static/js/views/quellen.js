import { api } from '../api.js';
import { h } from '../dom.js';

export const title = 'Quellen';

export async function render(root) {
  const data = await api.quellen();
  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Quellen'), h('span', { class: 'page-sub' }, data.file)));
  if (!data.available) {
    root.append(h('p', { class: 'empty' }, `${data.file} fehlt in der Sammlung.`));
    return;
  }
  const article = h('article', { class: 'card prose' });
  // The server renderer escapes every character of the source before adding its own tags.
  article.innerHTML = data.html;
  root.append(article, h('p', { class: 'footnote' }, 'Links öffnen extern in einem neuen Tab.'));
}
