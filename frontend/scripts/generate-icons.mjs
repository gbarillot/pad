import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const sourceDirectory = resolve(
  scriptDirectory,
  "../../../docs/resources/AppIcons/Assets.xcassets/AppIcon.appiconset",
);
const iconsetDirectory = resolve(frontendDirectory, "resources/PAD.iconset");
const outputIcon = resolve(frontendDirectory, "resources/icon.icns");

const iconFiles = [
  ["16.png", "icon_16x16.png"],
  ["32.png", "icon_16x16@2x.png"],
  ["32.png", "icon_32x32.png"],
  ["64.png", "icon_32x32@2x.png"],
  ["128.png", "icon_128x128.png"],
  ["256.png", "icon_128x128@2x.png"],
  ["256.png", "icon_256x256.png"],
  ["512.png", "icon_256x256@2x.png"],
  ["512.png", "icon_512x512.png"],
  ["1024.png", "icon_512x512@2x.png"],
];

await rm(iconsetDirectory, { force: true, recursive: true });
await mkdir(iconsetDirectory, { recursive: true });

for (const [sourceName, destinationName] of iconFiles) {
  await copyFile(resolve(sourceDirectory, sourceName), resolve(iconsetDirectory, destinationName));
}

const result = spawnSync("iconutil", ["-c", "icns", iconsetDirectory, "-o", outputIcon], {
  stdio: "inherit",
});
await rm(iconsetDirectory, { force: true, recursive: true });

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

console.log(`Generated ${outputIcon}`);
