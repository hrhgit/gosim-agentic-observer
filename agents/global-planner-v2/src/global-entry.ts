/** Explicitly selected learned planner, with no fallback or extra runtime limits. */
import { spawn } from "node:child_process";
import path from "node:path";
const root = path.resolve(__dirname, "..");
const python = process.env.BASE_MODEL_PYTHON ?? path.join(root, ".runtime/venv/bin/python");
const config = process.env.GLOBAL_PLANNER_CONFIG ?? path.join(root, "models/global-planning/config.json");
const child = spawn(python, ["-B", path.join(root, "runtime/planning_entry.py"), "--config", config],
  {cwd: root, env: {...process.env, GLOBAL_PLANNER_NODE: process.execPath, BASE_MODEL_PYTHON: python}, stdio: ["pipe", "pipe", "inherit"]});
process.stdin.pipe(child.stdin); child.stdout.pipe(process.stdout);
child.on("error", error => { console.error(error); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
