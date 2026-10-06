/**
 * "observer-seal-v1" public-key sealing for the public-repository runner pool;
 * the runner side is project_platform/sealing.py (same format, tested both ways).
 *
 * b"OSB1" || ephemeral X25519 public key (32) || AES-256-GCM ciphertext and tag.
 * Key (32) and nonce (12) come from HKDF-SHA256 over the X25519 shared secret,
 * salted with ephemeral || recipient public key; the context (for example
 * "result:<job id>") is the HKDF info suffix and the AEAD associated data.
 */
import { x25519 } from "npm:@noble/curves@1.9.7/ed25519";

const MAGIC = new TextEncoder().encode("OSB1");
const INFO = "observer-seal-v1:";
export const SEAL_OVERHEAD = MAGIC.length + 32 + 16;

export class SealError extends Error {}

export function encodeKey(raw: Uint8Array): string {
  return btoa(String.fromCharCode(...raw)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export function decodeKey(value: unknown): Uint8Array {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value)) throw new SealError("invalid_seal_key");
  const raw = Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/") + "="), (c) => c.charCodeAt(0));
  if (raw.length !== 32) throw new SealError("invalid_seal_key");
  return raw;
}

export function publicKeyFor(privateKey: Uint8Array): Uint8Array {
  if (privateKey.length !== 32) throw new SealError("invalid_seal_key");
  return x25519.getPublicKey(privateKey);
}

async function derive(shared: Uint8Array, ephemeral: Uint8Array, recipient: Uint8Array, context: string) {
  if (shared.every((b) => b === 0)) throw new SealError("invalid_seal_key");
  const salt = new Uint8Array(64);
  salt.set(ephemeral);
  salt.set(recipient, 32);
  const ikm = await crypto.subtle.importKey("raw", shared as Uint8Array<ArrayBuffer>, "HKDF", false, ["deriveBits"]);
  const bits = new Uint8Array(
    await crypto.subtle.deriveBits(
      { name: "HKDF", hash: "SHA-256", salt, info: new TextEncoder().encode(INFO + context) },
      ikm,
      44 * 8,
    ),
  );
  const key = await crypto.subtle.importKey("raw", bits.slice(0, 32), "AES-GCM", false, ["encrypt", "decrypt"]);
  return { key, nonce: bits.slice(32) };
}

function sharedSecret(privateKey: Uint8Array, publicKey: Uint8Array) {
  try {
    return x25519.getSharedSecret(privateKey, publicKey);
  } catch {
    throw new SealError("invalid_seal_key");
  }
}

export async function seal(recipient: Uint8Array, data: Uint8Array, context: string): Promise<Uint8Array> {
  if (recipient.length !== 32) throw new SealError("invalid_seal_key");
  const ephemeralPrivate = crypto.getRandomValues(new Uint8Array(32));
  const ephemeral = x25519.getPublicKey(ephemeralPrivate);
  const { key, nonce } = await derive(sharedSecret(ephemeralPrivate, recipient), ephemeral, recipient, context);
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce, additionalData: new TextEncoder().encode(context) },
      key,
      data as Uint8Array<ArrayBuffer>,
    ),
  );
  const out = new Uint8Array(MAGIC.length + 32 + ciphertext.length);
  out.set(MAGIC);
  out.set(ephemeral, MAGIC.length);
  out.set(ciphertext, MAGIC.length + 32);
  return out;
}

export async function openSealed(privateKey: Uint8Array, sealed: Uint8Array, context: string): Promise<Uint8Array> {
  if (sealed.length < SEAL_OVERHEAD || MAGIC.some((b, i) => sealed[i] !== b)) {
    throw new SealError("invalid_sealed_data");
  }
  const ephemeral = sealed.slice(MAGIC.length, MAGIC.length + 32);
  const { key, nonce } = await derive(
    sharedSecret(privateKey, ephemeral),
    ephemeral,
    publicKeyFor(privateKey),
    context,
  );
  try {
    return new Uint8Array(
      await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: nonce, additionalData: new TextEncoder().encode(context) },
        key,
        sealed.subarray(MAGIC.length + 32) as Uint8Array<ArrayBuffer>,
      ),
    );
  } catch {
    throw new SealError("invalid_sealed_data");
  }
}

export function toBase64(data: Uint8Array): string {
  let text = "";
  for (let i = 0; i < data.length; i += 0x8000) text += String.fromCharCode(...data.subarray(i, i + 0x8000));
  return btoa(text);
}
