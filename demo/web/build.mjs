import { execSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, "dist");
const assetsDir = path.join(distDir, "assets");
const tmpOutDir = path.join(__dirname, ".tsbuild");

fs.rmSync(distDir, { recursive: true, force: true });
fs.rmSync(tmpOutDir, { recursive: true, force: true });
fs.mkdirSync(assetsDir, { recursive: true });

// 1. Run strict TypeScript compiler to typecheck and emit ES2022 modules
execSync("npx tsc --project tsconfig.json --noEmit false --outDir .tsbuild", {
  cwd: __dirname,
  stdio: "inherit",
});

// 2. Read compiled main.js and styles.css, compute content hashes
const mainJsPath = path.join(tmpOutDir, "main.js");
let mainJs = fs.readFileSync(mainJsPath, "utf-8");
// Strip any type-only or CSS side-effect imports
mainJs = mainJs.replace(/^import\s+["']\.\/styles\.css["'];?\s*$/gm, "");

const cssPath = path.join(__dirname, "src", "styles.css");
// The shared Anomalon design system (tokens, header, footer, chips) ships first, then local styles.
const sharedCssPath = path.join(__dirname, "src", "anomalon.css");
const cssContent =
  fs.readFileSync(sharedCssPath, "utf-8") + "\n" + fs.readFileSync(cssPath, "utf-8");

const jsHash = crypto.createHash("sha256").update(mainJs).digest("hex").slice(0, 10);
const cssHash = crypto.createHash("sha256").update(cssContent).digest("hex").slice(0, 10);

const jsFilename = `app-${jsHash}.js`;
const cssFilename = `styles-${cssHash}.css`;

fs.writeFileSync(path.join(assetsDir, jsFilename), mainJs, "utf-8");
fs.writeFileSync(path.join(assetsDir, cssFilename), cssContent, "utf-8");

// 3. Generate production dist/index.html
const rawHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf-8");
const prodHtml = rawHtml
  .replace(
    "</head>",
    `    <link rel="stylesheet" href="/assets/${cssFilename}" />\n  </head>`
  )
  .replace(
    `<script type="module" src="/src/main.ts"></script>`,
    `<script type="module" src="/assets/${jsFilename}"></script>`
  );

fs.writeFileSync(path.join(distDir, "index.html"), prodHtml, "utf-8");
fs.rmSync(tmpOutDir, { recursive: true, force: true });

console.log(`Built demo/web/dist: /assets/${jsFilename}, /assets/${cssFilename}`);
