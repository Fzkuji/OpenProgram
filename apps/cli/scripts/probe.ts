import { BackendClient } from '../src/ws/client.js';

const url = process.argv[2] ?? 'ws://127.0.0.1:8765/ws';
const client = new BackendClient(url);

const seen: string[] = [];
client.on((ev) => {
  seen.push(ev.type);
  console.log('<<', JSON.stringify(ev).slice(0, 240));
});

client.connect();
client.send({ action: 'sync' });
client.send({ action: 'list_agents' });

setTimeout(() => {
  console.log('--- summary ---');
  console.log('events:', seen.join(', '));
  client.close();
  process.exit(0);
}, 2500);
