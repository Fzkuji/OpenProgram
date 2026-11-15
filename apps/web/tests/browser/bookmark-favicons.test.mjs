import assert from 'node:assert/strict';
import test from 'node:test';
import { bookmarkFaviconSources, updateBookmarkFavicon, readBookmarkTree, toggleBookmark, BOOKMARKS_STORAGE_KEY } from '../../lib/tabs/browser/bookmarks.ts';

test('bookmark icons prefer saved images and fall back only to the website origin', () => {
  const bookmark = {title:'Page',url:'https://site.test/path?q=secret#section'};
  assert.deepEqual(bookmarkFaviconSources(bookmark), {url:'https://site.test/favicon.ico',fallbackUrl:undefined});
  assert.deepEqual(bookmarkFaviconSources({...bookmark,faviconUrl:'https://cdn.test/logo.png'}), {url:'https://cdn.test/logo.png',fallbackUrl:'https://site.test/favicon.ico'});
  for (const faviconUrl of ['file:///private/icon.png','javascript:alert(1)','https://user:pass@site.test/icon.png']) {
    assert.equal(bookmarkFaviconSources({...bookmark,faviconUrl}).url,'https://site.test/favicon.ico');
  }
  for (const url of ['file:///private/page.html','javascript:alert(1)','https://user:pass@site.test/']) {
    assert.equal(bookmarkFaviconSources({title:'Page',url}).url,undefined);
  }
});

test('saved bookmark icons update by exact URL, survive empty state, and preserve the tree', () => {
  const previousStorage = globalThis.localStorage;
  const previousWindow = globalThis.window;
  const data = new Map();
  let events = 0;
  globalThis.localStorage = {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};
  globalThis.window = {dispatchEvent:()=>{events++;}};
  const icon='data:image/png;base64,AQID';
  try {
    toggleBookmark({title:'Saved page',url:'https://site.test/',faviconUrl:icon});
    assert.equal(readBookmarkTree().children[0].faviconUrl,icon);
    const original=localStorage.getItem(BOOKMARKS_STORAGE_KEY);
    updateBookmarkFavicon('https://other.test/', 'https://other.test/logo.png');
    updateBookmarkFavicon('https://site.test/', '');
    updateBookmarkFavicon('https://site.test/', icon);
    assert.equal(events,1);
    assert.equal(localStorage.getItem(BOOKMARKS_STORAGE_KEY),original);
    updateBookmarkFavicon('https://site.test/','https://site.test/new.png');
    const next=readBookmarkTree().children[0];
    assert.equal(next.title,'Saved page');
    assert.equal(next.url,'https://site.test/');
    assert.equal(next.faviconUrl,'https://site.test/new.png');
    assert.equal(events,2);
  } finally {
    globalThis.localStorage=previousStorage;
    globalThis.window=previousWindow;
  }
});
