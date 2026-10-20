// Run via: node run.mjs --playwright /absolute/path/to/playwright --out NEW_DIR [--rounds 6]
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash, randomBytes } from 'node:crypto';
import { installProbe } from './marker.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(Array.from({ length: (process.argv.length - 2) / 2 }, (_, i) =>
  [process.argv[2 + i * 2].replace(/^--/, ''), process.argv[3 + i * 2]]));
if (!args.playwright || !args.out) throw new Error('--playwright and --out required');
const rounds = Number(args.rounds ?? 6);
if (!Number.isInteger(rounds) || rounds < 1 || rounds > 6) throw new Error('rounds must be 1..6');
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve(args.playwright));
const out = path.resolve(args.out);
await fs.mkdir(out); // Refuse to overwrite an earlier experiment.
const fixture = await fs.readFile(path.join(here, 'fixture.html'), 'utf8');
const sourceHashes = {};
for (const name of ['fixture.html', 'marker.mjs', 'run.mjs', 'analyze.py'])
  sourceHashes[name] = createHash('sha256').update(await fs.readFile(path.join(here, name))).digest('hex');
const manifest = {
  schema: 1, provenance: 'Keel-authored synthetic diagnostic; not Pi output',
  startedAt: new Date().toISOString(), sourceHashes, rounds, viewport: { width: 800, height: 600 },
  node: process.version, playwright: require(path.join(path.resolve(args.playwright), 'package.json')).version,
  chromiumPath: chromium.executablePath(), runs: [], status: 'running',
};
const save = () => fs.writeFile(path.join(out, 'captures.json'), JSON.stringify(manifest, null, 2) + '\n');
await save();
let browser;
try {
  browser = await chromium.launch({ headless: true });
  manifest.chromium = browser.version();
  // All six permutations balance order across six rounds.
  const orders = [
    ['baseline', 'sample', 'marker'], ['sample', 'marker', 'baseline'],
    ['marker', 'baseline', 'sample'], ['marker', 'sample', 'baseline'],
    ['sample', 'baseline', 'marker'], ['baseline', 'marker', 'sample'],
  ];
  for (const workload of ['normal', 'busy']) {
    for (let round = 0; round < rounds; round++) {
      for (const mode of orders[round]) {
        const id = `${workload}-${round + 1}-${mode}`;
        const dir = path.join(out, id);
        await fs.mkdir(dir);
        const token = randomBytes(4).readUInt32BE();
        const info = { id, workload, round: round + 1, mode, token, status: 'running' };
        manifest.runs.push(info);
        await save();
        const context = await browser.newContext({
          viewport: manifest.viewport, deviceScaleFactor: 1,
          recordVideo: { dir, size: manifest.viewport },
        });
        try {
          const page = await context.newPage();
          page.setDefaultTimeout(15000);
          const errors = [];
          page.on('pageerror', error => errors.push(String(error)));
          await page.setContent(fixture);
          const session = await context.newCDPSession(page);
          await session.send('Performance.enable');
          await page.evaluate(installProbe, { mode, token });
          await page.evaluate(name => app.startWorkload(name), workload);
          await page.waitForTimeout(500);
          const metricsBefore = await session.send('Performance.getMetrics');
          const specs = [
            { id: 1, kind: 'short', durationMs: 10 },
            { id: 2, kind: 'medium', durationMs: 50 },
            { id: 3, kind: 'long', durationMs: 200 },
            { id: 4, kind: 'healthy', durationMs: 0 },
            ...[5, 6, 7, 8].map(id => ({ id, kind: 'race', durationMs: 12 })),
          ];
          for (const spec of specs) {
            await page.evaluate(spec => app.prepare(spec), spec);
            // Same deterministic phase variation for the three matched arms.
            await page.waitForTimeout(100 + ((round * 13 + spec.id * 7) % 23));
            await page.locator('#go').click();
            await page.waitForFunction(id => app.done === id, spec.id, { polling: 20 });
            await page.waitForTimeout(210);
          }
          const metricsAfter = await session.send('Performance.getMetrics');
          const data = await page.evaluate(() => {
            app.stop();
            if (window.probe) probe.running = false;
            return { events: app.events, samples: window.probe?.samples ?? [], stoppedMs: performance.now() };
          });
          const videoPath = await page.video().path();
          await fs.writeFile(path.join(dir, 'events.json'), JSON.stringify({
            ...info, specs, ...data, metricsBefore, metricsAfter, errors,
          }, null, 2) + '\n');
          info.video = path.relative(out, videoPath);
          info.events = `${id}/events.json`;
          info.status = errors.length ? 'error' : 'completed';
          if (errors.length) throw new Error(errors.join('\n'));
        } finally {
          await context.close(); // Video is finalized before hashes/analysis.
        }
        info.videoSha256 = createHash('sha256').update(await fs.readFile(path.join(out, info.video))).digest('hex');
        info.eventsSha256 = createHash('sha256').update(await fs.readFile(path.join(out, info.events))).digest('hex');
        await save();
        process.stdout.write(`${id}: ${info.status}\n`);
      }
    }
  }
  manifest.status = 'completed';
} catch (error) {
  manifest.status = 'error';
  manifest.error = String(error.stack ?? error);
  process.stderr.write(manifest.error + '\n');
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  manifest.finishedAt = new Date().toISOString();
  await save();
}
