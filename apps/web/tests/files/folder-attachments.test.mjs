import assert from 'node:assert/strict';
import test from 'node:test';
import { captureDropEntries, firstDirectoryLevel } from '../../components/chat/composer/attach/folder-drop.ts';
import { buildAttachmentEnvelope } from '../../lib/chat/attachment-marker.ts';

function directory(batches) {
  return { name: 'project', isDirectory: true, createReader: () => ({readEntries(resolve) { resolve(batches.shift() || []); }}) };
}
test('folder attachment lists only immediate children and bounds long listings', async () => {
  const listing = await firstDirectoryLevel(directory([[{name:'README.md',isDirectory:false},{name:'nested',isDirectory:true,createReader() { throw new Error('must not recurse'); }}]]));
  assert.equal(listing, '"README.md"\n"nested/"');
  const many = await firstDirectoryLevel(directory([Array.from({length:201},(_,i)=>({name:`file-${i}`,isDirectory:false}))]));
  assert.equal(many.split('\n').length, 201);
  assert.match(many, /truncated/);
  assert.ok(new TextEncoder().encode(many).length < 8300);
});
test('native documents and folders send paths without any byte payload or body', () => {
  const envelope = buildAttachmentEnvelope([], [
    {filename:'report.pdf',ext:'pdf',sizeBytes:300000000,sourcePath:'/home/report.pdf',dataB64:'legacy-cached-bytes'},
    {filename:'project',ext:'folder',sizeBytes:0,sourcePath:'/home/project',directoryListing:'"README.md"\n"nested/"'},
  ]);
  assert.deepEqual(envelope.docsPayload, []);
  assert.match(envelope.mentions.join('\n'), /\/home\/report.pdf/);
  assert.match(envelope.mentions.join('\n'), /First-level directory listing/);
  assert.doesNotMatch(envelope.mentions.join('\n'), /legacy-cached-bytes/);
});
test('empty browser file is an upload, not a silently missing attachment', () => {
  const envelope = buildAttachmentEnvelope([], [{filename:'empty.txt',ext:'txt',sizeBytes:0,dataB64:''}]);
  assert.equal(envelope.docsPayload.length, 1);
  assert.equal(envelope.docsPayload[0].data, '');
});
test('message attachment markers preserve mixed insertion order', () => {
  const envelope = buildAttachmentEnvelope([{order:1,sizeBytes:12,attachment:{type:'image',filename:'image.png',media_type:'image/png',data:'bytes'}}], [{order:0,filename:'first.txt',ext:'txt',sizeBytes:10,sourcePath:'/home/first.txt'}]);
  assert.match(envelope.mentions[0], /first.txt/);
  assert.match(envelope.mentions[1], /image.png/);
});

test('directory drop pairs file items even with preceding text drag data', () => {
  const folderFile = {name:'project'};
  const entry = directory([]);
  const transfer = {files:[folderFile],items:[{kind:'string'},{kind:'file',webkitGetAsEntry:()=>entry,getAsFile:()=>folderFile}]};
  assert.equal(captureDropEntries(transfer).get(folderFile), entry);
});
