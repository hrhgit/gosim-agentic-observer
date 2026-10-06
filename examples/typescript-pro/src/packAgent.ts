/**
 * Zip this project into a ZIP the platform can prepare and run as a complete project.
 *
 *     npm run pack [-- --out ../typescript-pro-agent.zip]
 *
 * Mirrors the platform's project rules (see observer.project.json and the hackathon's docs):
 * observer.project.json stays at the zip root with "protocol": "jsonl-v4"; .env is never packed (the
 * platform rejects ZIPs that contain one, and a permanent key must never be uploaded -- set it on the
 * Participate page instead); node_modules, dist (the platform builds it), run_output, .git and
 * editor/OS clutter are skipped. Port of python-pro/pack_agent.py; Node built-ins only (zlib).
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import * as path from "node:path";
import { crc32, deflateRawSync } from "node:zlib";

const ROOT = path.resolve(__dirname, "..");
const MANIFEST_NAME = "observer.project.json";
const EXCLUDED_DIRS = new Set(["node_modules", "dist", ".npm-cache", ".git", "run_output", ".idea", ".vscode"]);
const EXCLUDED_SUFFIXES = [".zip", ".log"];
const EXCLUDED_NAMES = new Set([".DS_Store", "Thumbs.db"]);
const ENV_TEMPLATES = new Set([".env.example", ".env.sample", ".env.template"]);

function collect(dir: string, rel = ""): string[] {
  const files: string[] = [];
  for (const name of readdirSync(dir).sort()) {
    const relPath = rel ? `${rel}/${name}` : name;
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) {
      if (!EXCLUDED_DIRS.has(name)) files.push(...collect(full, relPath));
      continue;
    }
    if (EXCLUDED_NAMES.has(name) || EXCLUDED_SUFFIXES.some((suffix) => name.endsWith(suffix))) continue;
    if (name === ".env" || (name.startsWith(".env.") && !ENV_TEMPLATES.has(name))) continue;
    files.push(relPath);
  }
  return files;
}

/** A plain ZIP (deflate, no extras): local headers, central directory, end record. */
function zip(entries: [string, Buffer][]): Buffer {
  const parts: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  const dosTime = 0; // 1980-01-01 00:00: reproducible archives
  const dosDate = (0 << 9) | (1 << 5) | 1;
  for (const [name, data] of entries) {
    const nameBytes = Buffer.from(name, "utf8");
    const packed = deflateRawSync(data, { level: 9 });
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6); // UTF-8 names
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(8, 10);
    header.writeUInt16LE(dosTime, 12);
    header.writeUInt16LE(dosDate, 14);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(packed.length, 20);
    header.writeUInt32LE(data.length, 24);
    header.writeUInt16LE(nameBytes.length, 28);
    header.writeUInt32LE(offset, 42);
    parts.push(local, nameBytes, packed);
    central.push(header, nameBytes);
    offset += local.length + nameBytes.length + packed.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

function main(argv: string[]): number {
  const at = argv.indexOf("--out");
  const out = path.resolve(at >= 0 && argv[at + 1] ? (argv[at + 1] as string) : path.join(ROOT, "..", "typescript-pro-agent.zip"));
  const manifestPath = path.join(ROOT, MANIFEST_NAME);
  if (!existsSync(manifestPath)) throw new Error(`missing ${MANIFEST_NAME} at ${ROOT}`);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (manifest.protocol !== "jsonl-v4") throw new Error(`${MANIFEST_NAME} must declare "protocol": "jsonl-v4"`);
  if (out === ROOT || out.startsWith(ROOT + path.sep)) throw new Error("write the zip outside this project folder, or it would package itself");
  mkdirSync(path.dirname(out), { recursive: true });

  const files = collect(ROOT);
  writeFileSync(out, zip(files.map((rel) => [rel, readFileSync(path.join(ROOT, rel))])));
  console.log(`packed ${files.length} files from ${ROOT} -> ${out} (${statSync(out).size} bytes)`);
  console.log(`  manifest: image ${manifest.image}, run ${manifest.run.join(" ")}`);
  if (existsSync(path.join(ROOT, ".env"))) {
    console.log("  note: .env exists but was left out of the ZIP (never upload it; set your key on the Participate page)");
  }
  console.log("  next: upload this ZIP as a complete project, or push this folder to a GitHub repository");
  return 0;
}

process.exitCode = main(process.argv.slice(2));
