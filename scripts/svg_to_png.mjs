// Render every SVG in a directory to a PNG beside it (2x), with the Playwright Chromium the
// frontend already installs (no extra dependency). Used for docs/final/figures (T9.4).
//
//   source ~/.nvm/nvm.sh && nvm use 22 && node scripts/svg_to_png.mjs docs/final/figures
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";

const require = createRequire(resolve("frontend/package.json"));
const { chromium } = require("@playwright/test");

const dir = resolve(process.argv[2] ?? "docs/final/figures");
const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 2 });
for (const name of readdirSync(dir).filter((f) => f.endsWith(".svg"))) {
  const svg = readFileSync(join(dir, name), "utf8");
  const [, w, h] = svg.match(/width='(\d+)' height='(\d+)'/) ?? [];
  await page.setViewportSize({ width: Number(w), height: Number(h) });
  await page.setContent(`<html><body style="margin:0">${svg}</body></html>`);
  await page
    .locator("svg")
    .screenshot({ path: join(dir, name.replace(/\.svg$/, ".png")) });
  console.log("png", name);
}
await browser.close();
