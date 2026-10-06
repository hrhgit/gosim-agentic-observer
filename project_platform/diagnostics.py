"""Bounded private project diagnostics; never copy a trusted exception verbatim."""
from __future__ import annotations

import re

from .job_client import JobError
from .repository import RepositoryError
from .session import SessionError


def safe_code(error: Exception) -> str:
    value=str(error)
    if isinstance(error,(JobError,SessionError)) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}',value):
        return value
    if isinstance(error,RepositoryError):
        # Platform storage (GitHub), not the project: distinguishable in job status.
        return 'snapshot_repository_unavailable'
    return 'project_operation_failed'


def private_log(value: str, secrets=(), limit: int = 32768) -> str:
    for secret in secrets:
        if secret:value=value.replace(secret,'[REDACTED]')
    value=re.sub(r'https?://[^\s<>\x22\x27]+','[URL REDACTED]',value)
    value=re.sub(r'obs_[0-9a-f-]{36}\.[A-Za-z0-9_-]+','[REDACTED]',value)
    value=re.sub(r'(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+)','[REDACTED]',value)
    value=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[REDACTED]',value)
    value=re.sub(r'(?i)(authorization\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?|bearer\s+)[^\s]+',r'\1[REDACTED]',value)
    return value.encode()[-limit:].decode(errors='ignore')


AGENT_LOG_LIMIT = 2 * 1024 * 1024


def agent_log(build_log: str, stderr: str, secrets=(), *, truncated: bool = False) -> str:
    """The team's own build output and project stderr, for its private result ZIP.

    Uses the same scrubbing as diagnostics. Only participant-produced text is
    included: no job payload, scenario, platform exception or credential.
    """
    if truncated:
        # The retained tail can begin inside a credential that the redaction
        # patterns no longer recognize; never keep that partial first line.
        stderr = '[platform] earlier output was truncated\n' + stderr.partition('\n')[2]
    text = ('[platform] project build output\n' + build_log.rstrip('\n') + '\n' if build_log.strip() else '')
    text += '[platform] project stderr\n' + stderr
    value = private_log(text, secrets, AGENT_LOG_LIMIT)
    if len(value.encode()) >= AGENT_LOG_LIMIT:
        value = '[platform] earlier output was truncated\n' + value.partition('\n')[2]
    return value


class ProjectJobFailure(JobError):
    def __init__(self, diagnostics: dict):
        super().__init__('project_operation_failed')
        self.diagnostics=diagnostics
