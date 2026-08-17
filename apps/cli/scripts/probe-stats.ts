import { BackendClient } from '../src/ws/client.js';

const c = new BackendClient(process.argv[2] ?? 'ws://127.0.0.1:8765/ws');
const seen: any[] = [];
c.on((ev: any) => {
  if (ev.type === 'stats') seen.push(ev);
});
c.connect();
setTimeout(() => c.send({ action: 'stats' as any }), 400);
setTimeout(() => {
  console.log(JSON.stringify(seen[0] ?? null, null, 2));
  c.close();
  process.exit(0);
}, 2200);
