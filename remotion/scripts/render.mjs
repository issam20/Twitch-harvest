/**
 * render.mjs — Script de rendu Remotion appelé par Python.
 *
 * Usage : node scripts/render.mjs /path/to/render_config.json
 *
 * render_config.json :
 * {
 *   "publicDir": "/tmp/harvest_render_xxx",   // contient step1.mp4
 *   "outputPath": "/data/edited/clip_edited.mp4",
 *   "inputProps": {
 *     "videoSrc": "step1.mp4",
 *     "title": "...", "colorGrade": "viral", "addZoom": false,
 *     "words": [...], "highlightColor": "#E8003C",
 *     "durationInFrames": 900, "fps": 60, "width": 405, "height": 720
 *   }
 * }
 */

import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

async function main() {
  const configPath = process.argv[2];
  if (!configPath) {
    process.stderr.write("Usage: node render.mjs <render_config.json>\n");
    process.exit(1);
  }

  const config = JSON.parse(await readFile(configPath, "utf-8"));
  const { publicDir, outputPath, inputProps } = config;

  if (!publicDir || !outputPath || !inputProps) {
    process.stderr.write(
      "[remotion] render_config.json incomplet (publicDir, outputPath, inputProps requis)\n"
    );
    process.exit(1);
  }

  // Bundling du projet Remotion
  process.stdout.write("[remotion] bundling...\n");
  const bundleLocation = await bundle({
    entryPoint: resolve(__dirname, "../src/index.ts"),
    publicDir: resolve(publicDir),
    onProgress: (p) => {
      if (p % 25 === 0 || p === 100) {
        process.stdout.write(`[remotion] bundle ${p}%\n`);
      }
    },
  });

  // Sélection de la composition avec les props d'entrée
  // (calculateMetadata dans Root.tsx lit durationInFrames/fps/width/height)
  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: "TikTokClip",
    inputProps,
  });

  const totalFrames = composition.durationInFrames;
  process.stdout.write(
    `[remotion] rendering ${totalFrames} frames @ ${composition.fps}fps ` +
    `(${composition.width}x${composition.height})...\n`
  );

  let lastPct = -1;
  await renderMedia({
    composition,
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: resolve(outputPath),
    inputProps,
    timeoutInMilliseconds: 300_000,
    onProgress: ({ progress }) => {
      const pct = Math.round(progress * 100);
      if (pct !== lastPct && (pct % 10 === 0 || pct === 100)) {
        lastPct = pct;
        process.stdout.write(`[remotion] ${pct}%\n`);
      }
    },
  });
}

main().catch((err) => {
  process.stderr.write(`[remotion] ERREUR: ${err?.message ?? String(err)}\n`);
  if (err?.stack) process.stderr.write(`${err.stack}\n`);
  process.exit(1);
});
