// Minimal ZIP handling for private result downloads: write a one-file archive,
// read one entry back, and append one file to an existing archive without
// re-encoding its other entries. ZIP64 and multi-disk archives are refused.

export class ZipError extends Error {}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function transform(data: Uint8Array, stream: CompressionStream | DecompressionStream, limit: number) {
  const reader = new Blob([data as Uint8Array<ArrayBuffer>]).stream().pipeThrough(stream).getReader();
  const parts: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > limit) throw new ZipError("zip_entry_too_large");
      parts.push(value);
    }
  } catch (error) {
    throw error instanceof ZipError ? error : new ZipError("invalid_zip_entry");
  } finally {
    await reader.cancel().catch(() => {});
  }
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.byteLength;
  }
  return out;
}

type Entry = { name: Uint8Array; method: number; crc: number; data: Uint8Array; size: number };

async function entry(name: string, content: Uint8Array): Promise<Entry> {
  const compressed = await transform(content, new CompressionStream("deflate-raw"), content.length + 65536);
  const deflate = compressed.length < content.length;
  return {
    name: new TextEncoder().encode(name),
    method: deflate ? 8 : 0,
    crc: crc32(content),
    data: deflate ? compressed : content,
    size: content.length,
  };
}

// Fixed 2026-01-01 00:00 DOS timestamp, like the trusted job archives.
const DOS_TIME = 0, DOS_DATE = ((2026 - 1980) << 9) | (1 << 5) | 1;

function localHeader(e: Entry) {
  const h = new Uint8Array(30 + e.name.length), v = new DataView(h.buffer);
  v.setUint32(0, 0x04034b50, true);
  v.setUint16(4, 20, true);
  v.setUint16(6, 0x0800, true); // UTF-8 names
  v.setUint16(8, e.method, true);
  v.setUint16(10, DOS_TIME, true);
  v.setUint16(12, DOS_DATE, true);
  v.setUint32(14, e.crc, true);
  v.setUint32(18, e.data.length, true);
  v.setUint32(22, e.size, true);
  v.setUint16(26, e.name.length, true);
  h.set(e.name, 30);
  return h;
}

function centralHeader(e: Entry, offset: number, mode = 0o100644) {
  const h = new Uint8Array(46 + e.name.length), v = new DataView(h.buffer);
  v.setUint32(0, 0x02014b50, true);
  v.setUint16(4, (3 << 8) | 20, true); // Unix, so the mode below is honoured
  v.setUint16(6, 20, true);
  v.setUint16(8, 0x0800, true);
  v.setUint16(10, e.method, true);
  v.setUint16(12, DOS_TIME, true);
  v.setUint16(14, DOS_DATE, true);
  v.setUint32(16, e.crc, true);
  v.setUint32(20, e.data.length, true);
  v.setUint32(24, e.size, true);
  v.setUint16(28, e.name.length, true);
  v.setUint32(38, (mode << 16) >>> 0, true);
  v.setUint32(42, offset, true);
  h.set(e.name, 46);
  return h;
}

function end(count: number, size: number, offset: number, comment: Uint8Array = new Uint8Array()) {
  const h = new Uint8Array(22 + comment.length), v = new DataView(h.buffer);
  v.setUint32(0, 0x06054b50, true);
  v.setUint16(8, count, true);
  v.setUint16(10, count, true);
  v.setUint32(12, size, true);
  v.setUint32(16, offset, true);
  v.setUint16(20, comment.length, true);
  h.set(comment, 22);
  return h;
}

function concat(parts: Uint8Array[]) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

type Directory = { count: number; size: number; offset: number; eocd: number; comment: Uint8Array };

function directory(zip: Uint8Array): Directory {
  const v = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 22 - 65535); i--) {
    if (v.getUint32(i, true) !== 0x06054b50) continue;
    const commentLength = v.getUint16(i + 20, true);
    if (i + 22 + commentLength !== zip.length) continue;
    const disk = v.getUint16(i + 4, true), start = v.getUint16(i + 6, true);
    const count = v.getUint16(i + 10, true), size = v.getUint32(i + 12, true), offset = v.getUint32(i + 16, true);
    if (
      disk !== 0 || start !== 0 || v.getUint16(i + 8, true) !== count || count === 0xffff ||
      size === 0xffffffff || offset === 0xffffffff || offset + size !== i
    ) throw new ZipError("unsupported_zip");
    return { count, size, offset, eocd: i, comment: zip.slice(i + 22, zip.length) };
  }
  throw new ZipError("invalid_zip");
}

function names(zip: Uint8Array, dir: Directory) {
  const v = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  const result: {
    name: string;
    method: number;
    compressed: number;
    size: number;
    crc: number;
    local: number;
    flags: number;
    mode: number;
  }[] = [];
  let p = dir.offset;
  for (let i = 0; i < dir.count; i++) {
    if (p + 46 > dir.eocd || v.getUint32(p, true) !== 0x02014b50) throw new ZipError("invalid_zip");
    const length = v.getUint16(p + 28, true), extra = v.getUint16(p + 30, true), note = v.getUint16(p + 32, true);
    if (p + 46 + length > dir.eocd) throw new ZipError("invalid_zip");
    result.push({
      name: new TextDecoder().decode(zip.subarray(p + 46, p + 46 + length)),
      method: v.getUint16(p + 10, true),
      crc: v.getUint32(p + 16, true),
      compressed: v.getUint32(p + 20, true),
      size: v.getUint32(p + 24, true),
      local: v.getUint32(p + 42, true),
      flags: v.getUint16(p + 8, true),
      mode: v.getUint32(p + 38, true) >>> 16,
    });
    p += 46 + length + extra + note;
  }
  if (p !== dir.eocd) throw new ZipError("invalid_zip");
  return result;
}

export async function singleFileZip(name: string, content: Uint8Array): Promise<Uint8Array> {
  const e = await entry(name, content);
  const local = localHeader(e), central = centralHeader(e, 0);
  return concat([local, e.data, central, end(1, central.length, local.length + e.data.length)]);
}

/** Several files in one archive (stored in this order, executable bits kept). */
export async function filesZip(files: { path: string; data: Uint8Array; executable: boolean }[]): Promise<Uint8Array> {
  if (!files.length || files.length >= 0xffff) throw new ZipError("unsupported_zip");
  const locals: Uint8Array[] = [], centrals: Uint8Array[] = [];
  let offset = 0;
  for (const file of files) {
    const e = await entry(file.path, file.data);
    const local = localHeader(e);
    centrals.push(centralHeader(e, offset, file.executable ? 0o100755 : 0o100644));
    locals.push(local, e.data);
    offset += local.length + e.data.length;
  }
  const size = centrals.reduce((n, c) => n + c.length, 0);
  if (offset + size >= 0xffffffff) throw new ZipError("unsupported_zip");
  return concat([...locals, ...centrals, end(files.length, size, offset)]);
}

/** Every entry name of an archive (directories end with "/"). */
export function zipNames(zip: Uint8Array): string[] {
  return names(zip, directory(zip)).map((e) => e.name);
}

/** Read one named entry; bounded, CRC-checked, stored or deflated only. */
export async function readZipEntry(
  zip: Uint8Array,
  name: string | ((name: string) => boolean),
  limit: number,
): Promise<Uint8Array | null> {
  const match = typeof name === "string" ? (n: string) => n === name : name;
  const found = names(zip, directory(zip)).find((e) => match(e.name));
  if (!found) return null;
  return await entryData(zip, found, limit);
}

/**
 * Every regular file of a trusted job archive (project_platform.artifacts.pack_files):
 * plain relative paths, no links, special files, encryption or duplicates.
 */
export async function readZipFiles(
  zip: Uint8Array,
  limit: number,
  maxFiles = 10000,
  include: (name: string) => boolean = () => true,
) {
  const entries = names(zip, directory(zip)).filter((e) => include(e.name));
  if (!entries.length || entries.length > maxFiles) throw new ZipError("invalid_zip");
  const files: { path: string; data: Uint8Array; executable: boolean }[] = [];
  const seen = new Set<string>();
  let total = 0;
  for (const e of entries) {
    if (e.name.endsWith("/")) continue;
    const type = e.mode & 0o170000;
    if (
      (e.flags & 1) || (type !== 0 && type !== 0o100000) || !e.name || e.name.startsWith("/") ||
      e.name.includes("\\") || e.name.includes("\0") ||
      e.name.split("/").some((part) => !part || part === "." || part === ".." || part.toLowerCase() === ".git") ||
      seen.has(e.name.toLowerCase())
    ) throw new ZipError("invalid_zip");
    seen.add(e.name.toLowerCase());
    total += e.size;
    if (total > limit) throw new ZipError("zip_entry_too_large");
    files.push({ path: e.name, data: await entryData(zip, e, limit), executable: (e.mode & 0o111) !== 0 });
  }
  if (!files.length) throw new ZipError("invalid_zip");
  return files.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

async function entryData(zip: Uint8Array, found: ReturnType<typeof names>[number], limit: number) {
  const v = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  const p = found.local;
  if (p + 30 > zip.length || v.getUint32(p, true) !== 0x04034b50) throw new ZipError("invalid_zip");
  const start = p + 30 + v.getUint16(p + 26, true) + v.getUint16(p + 28, true);
  if (start + found.compressed > zip.length || found.size > limit) throw new ZipError("zip_entry_too_large");
  const raw = zip.subarray(start, start + found.compressed);
  let data: Uint8Array;
  if (found.method === 0) data = raw.slice();
  else if (found.method === 8) data = await transform(raw, new DecompressionStream("deflate-raw"), limit);
  else throw new ZipError("unsupported_zip");
  if (data.length !== found.size || crc32(data) !== found.crc) throw new ZipError("invalid_zip_entry");
  return data;
}

/**
 * Append one file to an archive. Existing local records keep their offsets; only
 * the central directory moves. A top-level folder shared by every existing
 * entry (as in a GitHub zipball) is reused, so the new file sits beside them.
 */
export async function appendZipEntry(zip: Uint8Array, name: string, content: Uint8Array): Promise<Uint8Array> {
  const dir = directory(zip);
  const existing = names(zip, dir);
  const first = existing[0]?.name ?? "";
  const folder = first.includes("/") ? first.slice(0, first.indexOf("/") + 1) : "";
  const prefix = folder && existing.every((e) => e.name.startsWith(folder)) ? folder : "";
  const path = prefix + name;
  if (existing.some((e) => e.name === path)) throw new ZipError("zip_entry_exists");
  if (dir.count + 1 >= 0xffff) throw new ZipError("unsupported_zip");
  const e = await entry(path, content);
  const local = localHeader(e);
  const offset = dir.offset;
  const central = centralHeader(e, offset);
  const directoryOffset = offset + local.length + e.data.length;
  const directorySize = dir.size + central.length;
  if (directoryOffset + directorySize >= 0xffffffff) throw new ZipError("unsupported_zip");
  return concat([
    zip.subarray(0, offset),
    local,
    e.data,
    zip.subarray(dir.offset, dir.offset + dir.size),
    central,
    end(dir.count + 1, directorySize, directoryOffset, dir.comment),
  ]);
}
