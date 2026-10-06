// WeChat QR uploads: the image must be a PNG, JPEG or WebP of at most 1 MB that really decodes as a QR code.
import jsQRModule from "npm:jsqr@1.4.0";

// jsqr's CommonJS default export is the function itself (its typings describe a module object).
type JsQr = (
  data: Uint8ClampedArray,
  width: number,
  height: number,
  options?: { inversionAttempts?: string },
) => { data: string } | null;
const jsQR = jsQRModule as unknown as JsQr;

export const MAX_BYTES = 1024 * 1024;
const MAX_SIDE = 4096;
export type ImageKind = "png" | "jpg" | "webp";
export const CONTENT_TYPES: Record<ImageKind, string> = { png: "image/png", jpg: "image/jpeg", webp: "image/webp" };

/** The image type from its first bytes (never from the file name or the declared type). */
export function sniff(bytes: Uint8Array): ImageKind | null {
  if (bytes.length > 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) {
    return "png";
  }
  if (bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "jpg";
  if (
    bytes.length > 12 && String.fromCharCode(...bytes.subarray(0, 4)) === "RIFF" &&
    String.fromCharCode(...bytes.subarray(8, 12)) === "WEBP"
  ) return "webp";
  return null;
}

interface Pixels {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

let webpReady: Promise<(buf: ArrayBuffer) => Promise<ImageData>> | null = null;
function webpDecoder() {
  webpReady ??= (async () => {
    const mod = await import("npm:@jsquash/webp@1.5.0/decode.js");
    const wasm = await fetch("https://cdn.jsdelivr.net/npm/@jsquash/webp@1.5.0/codec/dec/webp_dec.wasm");
    if (!wasm.ok) throw new Error("webp_decoder_unavailable");
    await mod.init(await WebAssembly.compile(await wasm.arrayBuffer()));
    return mod.default as (buf: ArrayBuffer) => Promise<ImageData>;
  })();
  return webpReady;
}

async function pixels(bytes: Uint8Array, kind: ImageKind): Promise<Pixels> {
  if (kind === "webp") {
    const img = await (await webpDecoder())(bytes.slice().buffer);
    return { data: img.data as Uint8ClampedArray, width: img.width, height: img.height };
  }
  // Loaded on first use: the Deno build of ImageScript (its codecs are bundled WebAssembly).
  const { decode } = await import("https://deno.land/x/imagescript@1.3.0/mod.ts");
  // deno-lint-ignore no-explicit-any
  const img: any = await decode(bytes);
  const b = img.bitmap as Uint8Array;
  return { data: new Uint8ClampedArray(b.buffer, b.byteOffset, b.byteLength), width: img.width, height: img.height };
}

export type Check = { ok: true; kind: ImageKind } | { ok: false; error: "too_large" | "not_an_image" | "not_a_qr" };

/** Checks an upload; the QR's content itself is not stored or returned. */
export async function checkQrImage(bytes: Uint8Array): Promise<Check> {
  if (bytes.length > MAX_BYTES) return { ok: false, error: "too_large" };
  const kind = sniff(bytes);
  if (!kind) return { ok: false, error: "not_an_image" };
  let px: Pixels;
  try {
    px = await pixels(bytes, kind);
  } catch {
    return { ok: false, error: "not_an_image" };
  }
  if (!px.width || !px.height || px.width > MAX_SIDE || px.height > MAX_SIDE) {
    return { ok: false, error: "not_an_image" };
  }
  const found = jsQR(px.data, px.width, px.height, { inversionAttempts: "attemptBoth" });
  return found && found.data ? { ok: true, kind } : { ok: false, error: "not_a_qr" };
}
