/** Reproducible submission ZIP; Node built-ins only. Explicit source allowlist. */
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import * as path from "node:path";
import { crc32, deflateRawSync } from "node:zlib";
const ROOT = path.resolve(__dirname, "..");
function sourceFiles(dir: string, relative: string, accept = (name: string): boolean => name.endsWith(".ts")): string[] {
  const files: string[] = [];
  for (const name of readdirSync(dir).sort()) {
    const rel = `${relative}/${name}`,
      full = path.join(dir, name),
      stat = lstatSync(full);
    if (stat.isSymbolicLink())
      throw new Error(`source symlink is not supported: ${rel}`);
    if (stat.isDirectory()) files.push(...sourceFiles(full, rel, accept));
    else if (accept(name)) files.push(rel);
  }
  return files;
}
export function zip(entries: [string, Buffer][]): Buffer {
  const parts: Buffer[] = [],
    central: Buffer[] = [];
  let offset = 0;
  for (const [name, data] of entries) {
    const names = Buffer.from(name, "utf8"),
      packed = deflateRawSync(data, { level: 9 }),
      checksum = crc32(data),
      local = Buffer.alloc(30),
      header = Buffer.alloc(46);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(33, 12);
    local.writeUInt32LE(checksum, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(names.length, 26);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(8, 10);
    header.writeUInt16LE(33, 14);
    header.writeUInt32LE(checksum, 16);
    header.writeUInt32LE(packed.length, 20);
    header.writeUInt32LE(data.length, 24);
    header.writeUInt16LE(names.length, 28);
    header.writeUInt32LE(offset, 42);
    parts.push(local, names, packed);
    central.push(header, names);
    offset += local.length + names.length + packed.length;
  }
  const directory = Buffer.concat(central),
    end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}
function main(argv: string[]): void {
  const at = argv.indexOf("--out"),
    out = path.resolve(
      at >= 0 && argv[at + 1]
        ? argv[at + 1]
        : path.join(ROOT, "artifacts", "llm-observer-v1.zip"),
    );
  const manifest = JSON.parse(
    readFileSync(path.join(ROOT, "observer.project.json"), "utf8"),
  );
  if (manifest.protocol !== "jsonl-v4")
    throw new Error("manifest protocol must be jsonl-v4");
  const files = [
    "observer.project.json",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    ".nvmrc",
    ...sourceFiles(path.join(ROOT, "src"), "src"),
    "models/example-reliability-request.json",
    ...sourceFiles(path.join(ROOT, "models/conditions-reliability"), "models/conditions-reliability", name => /\.(json|npz|pt)$/.test(name)),
    ...sourceFiles(path.join(ROOT, "models/instrument-fault-report"), "models/instrument-fault-report", name => name.endsWith(".json")),
    ...sourceFiles(path.join(ROOT, "runtime"), "runtime", name => /\.(py|json|cjs)$/.test(name)),
  ];
  for (const name of [
    "README.md",
    "RESULTS.md",
    "NOTICE.md",
    "SOURCE_MANIFEST.json",
    "MODELS.md",
  ])
    if (existsSync(path.join(ROOT, name))) files.push(name);
  const entries: [string, Buffer][] = files.map((rel) => [
    rel,
    readFileSync(path.join(ROOT, rel)),
  ]);
  const unpacked = entries.reduce((s, [, data]) => s + data.length, 0),
    archive = zip(entries);
  if (
    files.length > 10000 ||
    unpacked > 100 * 1024 * 1024 ||
    archive.length > 50 * 1024 * 1024
  )
    throw new Error("archive exceeds participant project limits");
  mkdirSync(path.dirname(out), { recursive: true });
  writeFileSync(out, archive);
  process.stdout.write(
    JSON.stringify({
      out,
      files: files.length,
      zip_bytes: archive.length,
      extracted_bytes: unpacked,
      image: manifest.image,
    }) + "\n",
  );
}
if (require.main === module) main(process.argv.slice(2));
