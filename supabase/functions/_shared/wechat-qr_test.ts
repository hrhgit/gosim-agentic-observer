import { assertEquals } from "@std/assert";
import { decode, Image } from "https://deno.land/x/imagescript@1.3.0/mod.ts";
import QRCode from "npm:qrcode@1.5.4";
import { checkQrImage, MAX_BYTES, sniff } from "./wechat-qr.ts";

const qrPng = async () =>
  new Uint8Array(await QRCode.toBuffer("https://u.wechat.com/survey26-test", { type: "png", width: 360, margin: 2 }));

Deno.test("PNG, JPEG and WebP QR codes are accepted", async () => {
  const png = await qrPng();
  assertEquals(await checkQrImage(png), { ok: true, kind: "png" });
  // deno-lint-ignore no-explicit-any
  const jpg = await (await decode(png) as any).encodeJPEG(85);
  assertEquals(await checkQrImage(jpg), { ok: true, kind: "jpg" });
  const webp = await Deno.readFile(new URL("./testdata/wechat-qr.webp", import.meta.url));
  assertEquals(await checkQrImage(webp), { ok: true, kind: "webp" });
});

Deno.test("a picture without a QR code, a non-image and an oversized file are refused", async () => {
  const plain = await new Image(300, 300).fill(0xc86432ff).encode();
  assertEquals(await checkQrImage(plain), { ok: false, error: "not_a_qr" });
  assertEquals(await checkQrImage(new TextEncoder().encode("<svg></svg>")), { ok: false, error: "not_an_image" });
  const fakePng = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
  assertEquals(await checkQrImage(fakePng), { ok: false, error: "not_an_image" });
  assertEquals(await checkQrImage(new Uint8Array(MAX_BYTES + 1)), { ok: false, error: "too_large" });
});

Deno.test("the type comes from the bytes, not the name", () => {
  assertEquals(sniff(new Uint8Array([0xff, 0xd8, 0xff, 0xe0])), "jpg");
  assertEquals(sniff(new TextEncoder().encode("GIF89a........")), null);
});
