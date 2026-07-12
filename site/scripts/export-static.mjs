import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = fileURLToPath(new URL("../out-static/", import.meta.url));
const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("export", Date.now().toString());
const { default: worker } = await import(workerUrl.href);

const response = await worker.fetch(
  new Request("https://example.invalid/", { headers: { accept: "text/html" } }),
  { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} },
);
if (!response.ok) throw new Error(`static render failed: HTTP ${response.status}`);

let html = await response.text();
html = html.replaceAll("/assets/", "/xg-edge/assets/");

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(`${root}dist/client`, output, { recursive: true });
await writeFile(`${output}index.html`, html, "utf8");
await writeFile(`${output}.nojekyll`, "", "utf8");
console.log(`Static site exported to ${output}`);
