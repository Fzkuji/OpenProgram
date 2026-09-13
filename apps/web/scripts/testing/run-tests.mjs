/** Run every Web unit test, including feature subdirectories, on Node 20+. */
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../../', import.meta.url));
function collect(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? collect(path) : entry.isFile() && entry.name.endsWith('.test.mjs') ? [path] : [];
  });
}
const files = collect(join(root, 'tests')).sort();
if (!files.length) throw new Error('No Web tests discovered');
if (process.argv.includes('--list')) {
  console.log(files.join('\n'));
} else {
  const result = spawnSync(process.execPath, ['--no-warnings', '--experimental-strip-types', '--test', ...files], { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}
