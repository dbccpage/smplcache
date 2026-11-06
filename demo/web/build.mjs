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

function stripModuleSyntax(code) {
  return code
    .replace(/\r\n/g, "\n")
    .replace(/^import\s+[\s\S]*?from\s+["'][^"']+["'];?\s*$/gm, "")
    .replace(/^import\s+["'][^"']+["'];?\s*$/gm, "")
    .replace(/^export\s+(?=(?:async\s+)?function\b|const\b|let\b|class\b)/gm, "")
    .replace(/^export\s*\{[^}]*\};?\s*$/gm, "");
}

// 2. Bundle shared moves.js + main.js for /lab/ and moves.js + onepane.js for /
const movesJs = stripModuleSyntax(
  fs.readFileSync(path.join(tmpOutDir, "moves.js"), "utf-8")
);
const mainEntryJs = stripModuleSyntax(
  fs.readFileSync(path.join(tmpOutDir, "main.js"), "utf-8")
);
const onePaneEntryJs = stripModuleSyntax(
  fs.readFileSync(path.join(tmpOutDir, "onepane.js"), "utf-8")
);

const mainJs = `${movesJs}\n${mainEntryJs}\n`;
const onePaneJs = `(() => {\n${movesJs}\n${onePaneEntryJs}\n})();`;

const cssPath = path.join(__dirname, "src", "styles.css");
// The shared Anomalon design system (tokens, header, footer, chips) ships first, then local styles.
const sharedCssPath = path.join(__dirname, "src", "anomalon.css");
const cssContent = (
  fs.readFileSync(sharedCssPath, "utf-8") +
  "\n" +
  fs.readFileSync(cssPath, "utf-8")
).replace(/\r\n/g, "\n");

const jsHash = crypto
  .createHash("sha256")
  .update(mainJs)
  .digest("hex")
  .slice(0, 10);
const cssHash = crypto
  .createHash("sha256")
  .update(cssContent)
  .digest("hex")
  .slice(0, 10);

const jsFilename = `app-${jsHash}.js`;
const cssFilename = `styles-${cssHash}.css`;

fs.writeFileSync(path.join(assetsDir, jsFilename), mainJs, "utf-8");
fs.writeFileSync(path.join(assetsDir, cssFilename), cssContent, "utf-8");

// 3. Generate production dist/lab/index.html
const rawHtml = fs
  .readFileSync(path.join(__dirname, "index.html"), "utf-8")
  .replace(/\r\n/g, "\n");
const prodHtml = rawHtml
  .replace(
    "</head>",
    `    <link rel="stylesheet" href="/assets/${cssFilename}" />\n  </head>`
  )
  .replace(
    `<script type="module" src="/src/main.ts"></script>`,
    `<script type="module" src="/assets/${jsFilename}"></script>`
  );

// The full walkthrough lives at /lab/; the front page is the one-pane overview.
fs.mkdirSync(path.join(distDir, "lab"), { recursive: true });
fs.writeFileSync(path.join(distDir, "lab", "index.html"), prodHtml, "utf-8");

// 4. Front page: the one-pane overview, styled by the shared Anomalon stylesheet only.
const sharedCss = fs
  .readFileSync(sharedCssPath, "utf-8")
  .replace(/\r\n/g, "\n");
const sharedHash = crypto
  .createHash("sha256")
  .update(sharedCss)
  .digest("hex")
  .slice(0, 10);
const sharedFilename = `anomalon-${sharedHash}.css`;
fs.writeFileSync(path.join(assetsDir, sharedFilename), sharedCss, "utf-8");
const onePane = fs
  .readFileSync(path.join(__dirname, "src", "onepane.html"), "utf-8")
  .replace(/\r\n/g, "\n")
  .replace(
    "<!-- ANOMALON_CSS -->",
    () => `<link rel="stylesheet" href="/assets/${sharedFilename}" />`
  )
  .replace("<!-- ONEPANE_JS -->", () => `<script>\n${onePaneJs}\n</script>`);
fs.writeFileSync(path.join(distDir, "index.html"), onePane, "utf-8");
fs.rmSync(tmpOutDir, { recursive: true, force: true });

console.log(
  `Built demo/web/dist: /assets/${jsFilename}, /assets/${cssFilename}, /assets/${sharedFilename}`
);
