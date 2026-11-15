/**
 * Run every Web source check (`scripts/<area>/check-*.mjs`), each in its own
 * Node process, and fail at the end with the list of every failing check.
 *
 * A check is discovered by its file name, so adding or deleting one needs no
 * package.json edit, and one red check no longer hides the ones after it.
 * `check-static-resource-budget.mjs` is left out: it measures a production
 * build and runs on its own after `npm run build`.
 *
 *   node scripts/testing/run-checks.mjs            run all checks
 *   node scripts/testing/run-checks.mjs --list     print the discovered checks
 *   node scripts/testing/run-checks.mjs chat tabs  run checks whose path contains a filter
 */
import { readdirSync } from 'node:fs';
import { availableParallelism } from 'node:os';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const root = fileURLToPath(new URL('../../', import.meta.url));
const scriptsDir = join(root, 'scripts');
const EXCLUDED = new Set(['check-static-resource-budget.mjs']);
const STDERR_LINES = 15;

function discover() {
  return readdirSync(scriptsDir, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .flatMap(area => readdirSync(join(scriptsDir, area.name), { withFileTypes: true })
      .filter(entry => entry.isFile() && /^check-.+\.mjs$/.test(entry.name) && !EXCLUDED.has(entry.name))
      .map(entry => relative(root, join(scriptsDir, area.name, entry.name))))
    .sort();
}

function runOne(script) {
  return new Promise(resolve => {
    const started = Date.now();
    const child = spawn(process.execPath, ['--no-warnings', '--experimental-strip-types', script], {
      cwd: root,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', error => resolve({ script, code: 1, stdout, stderr: `${stderr}${error.stack ?? error}`, ms: Date.now() - started }));
    child.on('close', (code, signal) => resolve({
      script,
      code: code ?? 1,
      signal,
      stdout,
      stderr,
      ms: Date.now() - started,
    }));
  });
}

/**
 * The first STDERR_LINES lines of a failure, minus Node's uncaught-exception
 * preamble and the quoted source an assert.match prints as its "Input:" (often
 * hundreds of lines), plus the stack frame inside the check itself.
 */
function excerpt({ script, stderr, stdout }) {
  const all = (stderr.trim() || stdout.trim()).split('\n');
  const start = all.findIndex(line => /^\w*Error\b|^Error \[|^AssertionError/.test(line));
  const body = start >= 0 ? all.slice(start) : all;
  const kept = [];
  let elided = 0;
  for (const line of body) {
    if (/^\s*'.*'( \+)?$/.test(line)) { elided += 1; continue; }
    if (elided) { kept.push(`  ... (${elided} lines of input elided)`); elided = 0; }
    kept.push(line);
  }
  const shown = kept.slice(0, STDERR_LINES);
  const frame = kept.find(line => line.trim().startsWith('at ') && line.includes(script.replace(/^scripts\//, '')));
  if (frame && !shown.includes(frame)) shown.push(frame);
  if (kept.length > shown.length) shown.push(`  ... (${kept.length - shown.length} more lines)`);
  return shown;
}

const args = process.argv.slice(2);
const filters = args.filter(arg => !arg.startsWith('--'));
const scripts = discover().filter(script => !filters.length || filters.some(filter => script.includes(filter)));
if (!scripts.length) throw new Error('No Web checks discovered');

if (args.includes('--list')) {
  console.log(scripts.join('\n'));
} else {
  const results = new Array(scripts.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(scripts.length, Math.max(1, availableParallelism() - 1)) }, async () => {
    while (next < scripts.length) {
      const index = next++;
      const result = await runOne(scripts[index]);
      results[index] = result;
      const mark = result.code === 0 ? 'ok  ' : 'FAIL';
      console.log(`${mark} ${result.script} (${result.ms} ms)`);
    }
  });
  await Promise.all(workers);

  const failures = results.filter(result => result.code !== 0);
  if (failures.length) {
    console.log('');
    for (const failure of failures) {
      const why = failure.signal ? `killed by ${failure.signal}` : `exit ${failure.code}`;
      console.log(`--- ${failure.script} (${why})`);
      for (const line of excerpt(failure)) console.log(line);
      console.log('');
    }
    console.log(`${failures.length} of ${results.length} checks failed:`);
    for (const failure of failures) console.log(`  ${failure.script}`);
    process.exitCode = 1;
  } else {
    console.log(`\nAll ${results.length} checks passed.`);
  }
}
