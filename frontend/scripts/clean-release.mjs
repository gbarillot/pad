import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const releaseDirectory = resolve(scriptDirectory, "../release");

await rm(releaseDirectory, { force: true, recursive: true });
console.log(`Removed ${releaseDirectory}`);
