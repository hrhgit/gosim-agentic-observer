import { assertEquals, assertRejects } from "@std/assert";
import {
  assertPublicHost,
  isPublicAddress,
  publicBase,
  publicBaseSyntax,
  type Resolver,
} from "./observer-public-base.ts";

const answers = (a: string[], aaaa: string[] = []): Resolver => (_host, type) =>
  Promise.resolve(type === "A" ? a : aaaa);
const missing: Resolver = () => Promise.reject(Object.assign(new Error("no record"), { name: "NotFound" }));
const broken: Resolver = () => Promise.reject(Object.assign(new Error("dns unavailable"), { name: "NotSupported" }));

Deno.test("any public HTTPS base is accepted and normalized", () => {
  assertEquals(publicBaseSyntax("https://api.moonshot.cn/v1/"), "https://api.moonshot.cn/v1");
  assertEquals(publicBaseSyntax(" https://API.Kimi.com/coding/v1 "), "https://api.kimi.com/coding/v1");
  assertEquals(publicBaseSyntax("https://open.bigmodel.cn/api/paas/v4"), "https://open.bigmodel.cn/api/paas/v4");
  assertEquals(publicBaseSyntax("https://api.example.com:443/v1"), "https://api.example.com/v1");
});

Deno.test("HTTP, credentials, queries, other ports, IPs and local or reserved names are refused", () => {
  for (
    const bad of [
      "http://api.example.com/v1",
      "https://user:secret@api.example.com/v1",
      "https://api.example.com/v1?key=x",
      "https://api.example.com/v1#x",
      "https://api.example.com:8443/v1",
      "https://127.0.0.1/v1",
      "https://2130706433/v1",
      "https://0x7f.1/v1",
      "https://169.254.169.254/latest",
      "https://[::1]/v1",
      "https://[fd00::1]/v1",
      "https://localhost/v1",
      "https://api.localhost/v1",
      "https://metadata/v1",
      "https://printer.local/v1",
      "https://svc.internal/v1",
      "https://router.home.arpa/v1",
      "https://unapproved.test/v1",
      "https://site.example/v1",
      "https://x.invalid/v1",
      "file:///etc/passwd",
      "not a url",
      null,
      42,
      "https://" + "a".repeat(1000) + ".com/v1",
    ]
  ) assertEquals(publicBaseSyntax(bad), null, String(bad));
});

Deno.test("exact organizer-configured bases are trusted as they are, but never over HTTP", () => {
  const trusted = new Set(["https://127.0.0.1:8443/v1", "http://organizer-test.test/v1"]);
  assertEquals(publicBaseSyntax("https://127.0.0.1:8443/v1/", trusted), "https://127.0.0.1:8443/v1");
  assertEquals(publicBaseSyntax("https://127.0.0.1:8443/v2", trusted), null);
  assertEquals(publicBaseSyntax("http://organizer-test.test/v1", trusted), null);
});

Deno.test("only public unicast addresses count as public", () => {
  for (const ok of ["1.1.1.1", "104.18.2.5", "8.8.8.8", "2606:4700::6810:84e5", "2400:cb00:2049:1::a29f:1804"]) {
    assertEquals(isPublicAddress(ok), true, ok);
  }
  for (
    const bad of [
      "0.0.0.0",
      "10.1.2.3",
      "100.64.0.1",
      "100.127.255.254",
      "127.0.0.1",
      "169.254.169.254",
      "172.16.0.1",
      "172.31.255.255",
      "192.0.0.8",
      "192.0.2.1",
      "192.168.1.1",
      "198.18.0.1",
      "198.51.100.7",
      "203.0.113.9",
      "224.0.0.1",
      "240.0.0.1",
      "255.255.255.255",
      "::",
      "::1",
      "::ffff:127.0.0.1",
      "::ffff:10.0.0.1",
      "64:ff9b::a00:1",
      "fc00::1",
      "fd12:3456::1",
      "fe80::1",
      "ff02::1",
      "2001:db8::1",
      "2001:0:4136:e378::1",
      "2002:c0a8:101::1",
      "not-an-address",
      "1.2.3",
      "1::2::3",
    ]
  ) assertEquals(isPublicAddress(bad), false, bad);
});

Deno.test("a name must resolve, and only to public addresses", async () => {
  await assertPublicHost("https://api.team.com/v1", answers(["104.18.2.5"], ["2606:4700::6810:84e5"]));
  await assertRejects(() => assertPublicHost("https://api.team.com/v1", answers(["104.18.2.5", "10.0.0.8"])));
  await assertRejects(() => assertPublicHost("https://api.team.com/v1", answers([], ["fd00::5"])));
  await assertRejects(() => assertPublicHost("https://nowhere.team.com/v1", missing));
  // Without a working DNS API the naming rules and the TLS name check still apply.
  await assertPublicHost("https://api.team.com/v1", broken);
  await assertPublicHost("https://api.team.com/v1", null);
});

Deno.test("publicBase combines the rules and never resolves a refused or trusted base", async () => {
  let lookups = 0;
  const counting: Resolver = (host, type) => {
    lookups++;
    return answers(host === "rebind.team.com" ? ["127.0.0.1"] : ["104.18.2.5"])(host, type);
  };
  assertEquals(await publicBase("https://api.team.com/v1", new Set(), counting), "https://api.team.com/v1");
  assertEquals(await publicBase("https://rebind.team.com/v1", new Set(), counting), null);
  const before = lookups;
  assertEquals(await publicBase("https://localhost/v1", new Set(), counting), null);
  assertEquals(
    await publicBase("https://127.0.0.1:9/v1", new Set(["https://127.0.0.1:9/v1"]), counting),
    "https://127.0.0.1:9/v1",
  );
  assertEquals(lookups, before);
});
