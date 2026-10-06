/**
 * A quick look at an uploaded project ZIP before a revision is created: is there any
 * program at all? A ZIP with neither observer.project.json nor a recognizable source
 * file (outside hidden folders) can never be prepared, so it is refused at once,
 * without spending a preparation or a model call. Mirrored in web/src/lib/sourceCheck.ts
 * and project_platform/model_adapter.py (source_summary).
 */
export const SOURCE_EXTENSIONS = new Set([
  "py",
  "pyw",
  "ipynb",
  "ts",
  "tsx",
  "js",
  "jsx",
  "mjs",
  "cjs",
  "rs",
  "go",
  "java",
  "kt",
  "kts",
  "scala",
  "rb",
  "php",
  "c",
  "cc",
  "cpp",
  "cxx",
  "h",
  "hh",
  "hpp",
  "cs",
  "fs",
  "swift",
  "m",
  "mm",
  "lua",
  "r",
  "jl",
  "ex",
  "exs",
  "erl",
  "clj",
  "dart",
  "zig",
  "nim",
  "pl",
  "pm",
  "ml",
  "hs",
  "sh",
  "bash",
  "ps1",
  "groovy",
  "v",
  "sol",
  "wasm",
]);
export const PROJECT_FILES = new Set([
  "dockerfile",
  "makefile",
  "cargo.toml",
  "package.json",
  "go.mod",
  "pyproject.toml",
  "requirements.txt",
  "setup.py",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "gemfile",
  "composer.json",
  "deno.json",
]);

export type SourceCheck = { manifest: boolean; code: boolean; files: string[] };

export function inspectSourceNames(names: string[]): SourceCheck {
  const files = names.map((n) => n.replace(/\\/g, "/").replace(/^\.\//, ""))
    .filter((n) => n && !n.endsWith("/") && !n.startsWith("__MACOSX/"));
  let manifest = false, code = false;
  for (const name of files) {
    const parts = name.split("/");
    if (parts.some((p) => p.startsWith("."))) continue;
    const base = parts[parts.length - 1].toLowerCase();
    if (base === "observer.project.json") manifest = true;
    const dot = base.lastIndexOf(".");
    if (PROJECT_FILES.has(base) || (dot > 0 && SOURCE_EXTENSIONS.has(base.slice(dot + 1)))) code = true;
  }
  return { manifest, code, files: files.slice(0, 20) };
}
