import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {once} from 'node:events';

async function start(t, empty = false) {
  const folder = await mkdtemp(path.join(tmpdir(), 'jop-test-'));
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1'); await once(probe, 'listening');
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const process = spawn(globalThis.process.execPath, ['server.js'], {
    cwd: path.resolve(import.meta.dirname, '..'),
    env: {...globalThis.process.env, PORT: String(port), JOP_DATA_DIR: folder, JOP_SEED: empty ? 'empty' : 'demo'},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  process.stdout.on('data', d => {logs += d;});
  process.stderr.on('data', d => {logs += d;});
  t.after(async () => {
    if (process.exitCode === null) { process.kill(); await once(process, 'exit'); }
    await rm(folder, {recursive: true, force: true});
  });
  const base = 'http://127.0.0.1:' + port;
  for (let i=0; i<100; i++) {
    if (process.exitCode !== null) throw new Error(logs);
    try { if ((await fetch(base + '/api/items')).ok) return base; } catch {}
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  throw new Error('Server failed to start: ' + logs);
}
const json = body => ({headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

test('fresh demo is fictional and no purchase history is bundled', async t => {
  const base = await start(t);
  assert.equal((await (await fetch(base + '/api/items')).json()).length, 49);
  assert.deepEqual(await (await fetch(base + '/api/purchases')).json(), []);
  assert.deepEqual(await (await fetch(base + '/api/preferences')).json(), {colours:'',location:'',notes:''});
  const html = await (await fetch(base)).text();
  assert.match(html, /Pause before/);
  assert.doesNotMatch(html, /Autopilot checkout|Let Jop buy it/);
});

test('private empty startup, unknown values, and wardrobe feedback', async t => {
  const base = await start(t, true);
  assert.deepEqual(await (await fetch(base + '/api/items')).json(), []);
  const created = await fetch(base + '/api/items', {method:'POST', ...json({name:'Demo pants',cat:'Bottoms',price:null,date:null})});
  assert.equal(created.status, 201);
  const item = await created.json();
  assert.equal(item.price, null); assert.equal(item.date, null);
  const state = async patch => (await fetch(base + '/api/items/' + item.id + '/wardrobe', {method:'PUT',...json(patch)})).json();
  await state({favourite:true});
  const disliked = await state({disliked:true});
  assert.equal(disliked.favourite,false); assert.equal(disliked.disliked,true);
  const removed = await state({inWardrobe:false});
  assert.equal(removed.inWardrobe,false);
});

test('cross-site mutations and data-directory downloads are blocked', async t => {
  const base = await start(t,true);
  const write = await fetch(base + '/api/preferences', {method:'PUT',headers:{'Content-Type':'application/json',Origin:'https://example.test'},body:JSON.stringify({notes:'should not save'})});
  assert.equal(write.status,403);
  assert.equal((await fetch(base + '/data/jop.db')).status,404);
  assert.equal((await fetch(base + '/server.js')).status,404);
});
