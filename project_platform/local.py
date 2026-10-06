"""Local complete-project runner using the same public session as cloud jobs."""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess

from challenge.contracts import DECISION_COLUMNS
from .docker_runtime import DockerWorkspace
from .egress import anthropic_base
from .executor import execute
from .manifest import MANIFEST_NAME, ProjectError, ProjectManifest
from .preparation import resolve_image
from .session import SessionClient
from .team_egress import valid_variable_name
from .transport import ExecutionError, JsonlTransport


class NativeWorkspace:
    """Explicit local-only mode for a participant's own trusted project.

    This class is deliberately absent from the hosted execution path. It executes
    the project's argv directly on the user's computer, never on a cloud worker.
    """
    def __init__(self, root: Path, manifest: ProjectManifest):
        self.root = root.resolve(strict=True)
        self.cwd = (self.root / manifest.working_directory).resolve(strict=True)
        if not self.cwd.is_relative_to(self.root):
            raise ProjectError('Project working directory is outside the project.')
        self.manifest = manifest
        self.transport = None
        self.environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
        self.environment.update(dict(manifest.environment))

    def build(self):
        for command in self.manifest.build:
            try:
                result = subprocess.run(command, cwd=self.cwd, env=self.environment,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            except (OSError, subprocess.TimeoutExpired):
                raise ExecutionError('Local build failed. Check the build command in your project manifest.') from None
            if result.returncode:
                raise ExecutionError('Local build failed. Check the build command in your project manifest.')

    def start(self, environment, *, team=None):
        own = dict(team['environment']) if team else {}
        secrets = tuple(value for key, value in environment.items() if key.endswith(('TOKEN', 'KEY')))
        secrets += tuple(own[name] for name in (team or {}).get('secrets', ()) if len(own[name]) >= 4)
        self.transport = JsonlTransport(list(self.manifest.run), cwd=self.cwd,
            environment={**self.environment, **own, **environment}, redactions=secrets)
        return self.transport

    def close(self):
        if self.transport:
            self.transport.close(force=True)


# Direct model access (the default): the project calls its own provider with the
# participant's own variables, exactly as in a cloud evaluation with team variables.
# These names are taken from the shell; --env-file adds any NAME=value lines.
MODEL_VARIABLES = ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL',
                   'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL')
_SECRET_SUFFIXES = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')


def own_environment(env_file: Path | None = None, environ=None) -> dict:
    """{"environment", "secrets"}: the participant's own model variables for a local run."""
    environ = os.environ if environ is None else environ
    variables = {name: environ[name] for name in MODEL_VARIABLES if environ.get(name)}
    if env_file is not None:
        for number, line in enumerate(env_file.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name, separator, value = line.removeprefix('export ').partition('=')
            name, value = name.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            if not separator or not valid_variable_name(name) or name.startswith('OBSERVER_') or '\x00' in value:
                raise ProjectError(f'Line {number} of the environment file is not a valid NAME=value variable.')
            variables[name] = value
    return {'environment': variables, 'secrets': sorted(n for n in variables if n.endswith(_SECRET_SUFFIXES))}


class _OwnEnvironment:
    """Gives the runtime the participant's own variables next to the run identity."""
    def __init__(self, runtime, team):
        self.runtime, self.team = runtime, team

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def start(self, environment):
        return self.runtime.start(environment, team=self.team)


def export_decisions(client: SessionClient, destination: Path) -> str:
    result = client.call('decisions')
    rows = result.get('rows')
    if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != set(DECISION_COLUMNS) or
            any(not isinstance(value, str) for value in row.values()) for row in rows):
        raise ProjectError('The server returned an invalid committed trace.')
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=DECISION_COLUMNS, lineterminator='\n', extrasaction='raise')
    writer.writeheader(); writer.writerows(rows)
    raw = buffer.getvalue().encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != result.get('decisions_digest'):
        raise ProjectError('Export differs from the official trace. Download the private result from the website.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace a previous attempt's CSV, even on a successful retry.
    with destination.open('xb') as handle:
        handle.write(raw)
    return digest


def run_local(project: Path, session_url: str, credential: str, model_base: str | None, output: Path, *,
              native: bool = False, env_file: Path | None = None):
    match = re.fullmatch(r'obs_([0-9a-f-]{36})\.[A-Za-z0-9_-]{40,100}', credential)
    if not match:
        raise ProjectError('Paste the temporary run credential from the project page.')
    if output.exists():
        raise ProjectError('The output file already exists. Choose a new output path.')
    manifest = ProjectManifest.parse((project / MANIFEST_NAME).read_bytes())
    client = SessionClient(session_url, credential)
    if native:
        runtime = NativeWorkspace(project, manifest)
    else:
        from dataclasses import replace
        manifest = replace(manifest, image=resolve_image(manifest.image))
        runtime = DockerWorkspace(project, manifest, manifest.image)
    identity = {'OBSERVER_API_URL': session_url, 'OBSERVER_RUN_ID': match[1], 'OBSERVER_RUN_TOKEN': credential}
    if model_base:
        # Legacy: the platform model proxy (refused for teams on team egress once it is retired).
        environment = {**identity, 'OPENAI_BASE_URL': model_base, 'OPENAI_API_KEY': credential,
                       'ANTHROPIC_BASE_URL': anthropic_base(model_base), 'ANTHROPIC_API_KEY': credential}
    else:
        environment = identity
        runtime = _OwnEnvironment(runtime, own_environment(env_file))
    try:
        execute(runtime, client, environment)
    except (Exception, KeyboardInterrupt):
        try: client.call('abort')
        except Exception: pass
        raise
    return export_decisions(client, output)


def main():
    parser = argparse.ArgumentParser(description='Run your complete project with the official step-by-step session.')
    parser.add_argument('--session-url', required=True)
    parser.add_argument('--model-base-url', help='Legacy: the platform model proxy. Leave it out to use your own '
                        'OPENAI_* / ANTHROPIC_* variables (as in cloud evaluations).')
    parser.add_argument('--env-file', type=Path, help='NAME=value lines given to your project as its environment.')
    parser.add_argument('--project', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, default=Path('decisions.csv'))
    parser.add_argument('--native', action='store_true', help='Run your own trusted project directly on this computer, without Docker.')
    parser.add_argument('--export-only', action='store_true', help='Export a finished run without executing the project again.')
    args = parser.parse_args()
    credential = os.environ.get('OBSERVER_RUN_TOKEN') or getpass.getpass('Temporary run credential (hidden): ')
    try:
        if args.export_only:
            digest = export_decisions(SessionClient(args.session_url, credential), args.output)
        else:
            digest = run_local(args.project, args.session_url, credential, args.model_base_url, args.output,
                                   native=args.native, env_file=args.env_file)
        print(f'CSV saved to {args.output}; verified SHA256 {digest}')
        print('Upload this CSV to the matching run on the project page.')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # A participant command, HTTP error or signed URL can contain credentials.
        print(f'Run/export did not finish ({type(exc).__name__}). Check the run status, project manifest and output path; credentials were not logged.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
