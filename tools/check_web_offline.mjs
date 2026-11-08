// Fails when the built web app asks for anything outside the host that serves it.
// Usage: node check_web_offline.mjs <build/web directory>
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { chromium } from 'playwright-core';

const root = process.argv[2];
const types = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.wasm': 'application/wasm',
  '.png': 'image/png',
};

const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://x').pathname;
  if (path.startsWith('/api/')) {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(path === '/api/status' ? '{"serial":"X","online":false,"broker_connected":true,"alerts":[],"robot":null}' : '[]');
    return;
  }
  try {
    const file = join(root, normalize(path === '/' ? '/index.html' : path));
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage();
const external = new Set();
page.on('request', (request) => {
  if (!request.url().startsWith(origin) && !request.url().startsWith('data:') && !request.url().startsWith('blob:')) {
    external.add(request.url());
  }
});
await page.addInitScript((url) => {
  localStorage.setItem('flutter.server_url', JSON.stringify(url));
  localStorage.setItem('flutter.access_key', '"key"');
}, origin);
await page.goto(origin);
await page.waitForTimeout(8000);
await browser.close();
server.close();

if (external.size > 0) {
  console.error(`The web app requested outside hosts:\n${[...external].join('\n')}`);
  process.exit(1);
}
console.log('No external requests.');
