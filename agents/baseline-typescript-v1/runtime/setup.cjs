/** Build-time only: provision a pinned Python CPU runtime inside the writable project. */
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '..'), runtime = path.join(root, '.runtime');
function run(command, args, env) {
  const result = spawnSync(command, args, { cwd: root, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (${result.status})`);
}
async function main() {
  if (process.platform !== 'linux' || process.arch !== 'x64') {
    throw new Error('Automatic setup targets competition Linux x86_64. Locally set BASE_MODEL_PYTHON to a Python 3.12 environment with torch, numpy, scipy.');
  }
  fs.mkdirSync(runtime, { recursive: true });
  const env = { ...process.env, UV_CACHE_DIR: path.join(runtime, 'cache'), UV_PYTHON_INSTALL_DIR: path.join(runtime, 'python'), UV_LINK_MODE: 'copy', UV_PYTHON_PREFERENCE: 'only-managed', UV_NO_PROGRESS: '1' };
  const uv = path.join(runtime, 'uv');
  if (!fs.existsSync(uv)) {
    const response = await fetch('https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-unknown-linux-gnu.tar.gz');
    if (!response.ok) throw new Error(`uv download failed: ${response.status}`);
    const archive = path.join(runtime, 'uv.tar.gz');
    fs.writeFileSync(archive, Buffer.from(await response.arrayBuffer()));
    run('tar', ['xzf', archive, '--strip-components=1', '-C', runtime], env);
    fs.unlinkSync(archive);
  }
  run(uv, ['python', 'install', '3.12.11'], env);
  if (!fs.existsSync(path.join(runtime, 'venv/bin/python'))) run(uv, ['venv', '--python', '3.12.11', path.join(runtime, 'venv')], env);
  const python = path.join(runtime, 'venv/bin/python');
  run(uv, ['pip', 'install', '--python', python, '--index', 'https://download.pytorch.org/whl/cpu', 'torch==2.8.0+cpu'], env);
  run(uv, ['pip', 'install', '--python', python, 'numpy==2.2.6', 'scipy==1.15.3'], env);
  run(python, ['-B', '-c', 'import torch,numpy,scipy; print("model runtime",torch.__version__,numpy.__version__,scipy.__version__)'], env);
  fs.rmSync(path.join(runtime, 'cache'), { recursive: true, force: true });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
