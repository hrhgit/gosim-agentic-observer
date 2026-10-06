"""Run a complete project using only public observations from the session API."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from challenge.challenge_workflow import GlobalDeadlineExpired

from .docker_runtime import DockerWorkspace
from .session import SessionClient, SessionError, long_poll_seconds, wait_until
from .transport import NORMAL_TERMINATION_REASONS


def _graceful_finish(runtime, status: dict, last_sequence: int) -> dict:
    """One last finish message after a normally ended session, then a bounded
    grace period before the usual close. The score is already fixed on the
    server; a project that crashes on or ignores the message changes nothing.
    Servers predating termination_reason in the status payload skip this."""
    transport = getattr(runtime, "transport", None)
    reason = status.get("termination_reason")
    if (transport is not None and status.get("status") in ("scored", "awaiting_csv")
            and reason in NORMAL_TERMINATION_REASONS):
        try:
            transport.finish(reason, last_sequence)
        except Exception:
            pass
    return status


def execute(runtime: DockerWorkspace, client: SessionClient, environment: dict[str,str], *, startup_seconds: float = 1500):
    try:
        return _execute(runtime,client,environment,startup_seconds=startup_seconds)
    except GlobalDeadlineExpired:
        # The simulator still needs to persist and score the committed prefix.
        # Expiration ends participant decisions, not trusted result publication.
        runtime.close()
        finish_deadline=time.monotonic()+120
        while time.monotonic()<finish_deadline:
            status=client.call('status',deadline=finish_deadline)
            if status['status'] in ('scored','awaiting_csv'):
                return status
            if status['status'] in ('failed','cancelled'):
                raise SessionError('evaluation_failed')
            time.sleep(0.5)
        raise SessionError('result_publication_timeout')
    finally:
        runtime.close()


def _execute(runtime: DockerWorkspace, client: SessionClient, environment: dict[str,str], *, startup_seconds: float):
    runtime.build()
    transport=runtime.start(environment)
    startup=time.monotonic()+startup_seconds
    publication=wait_until(lambda:client.call("poll",deadline=startup)["publication"],deadline=startup)
    transport.publish_initial(publication)
    client.call("ready",deadline=startup)
    last_sequence=0
    last_response=None
    # The server holds each poll until the next observation exists, and a
    # response request returns the following step, so a step costs one request.
    wait={"wait":long_poll_seconds(None),"wait_for":"observation"}
    def session_ended(call):
        try:
            return call(), None
        except SessionError as exc:
            if exc.code=="invalid_or_expired_capability":
                status=client.call("status")
                if status["status"] in ("scored","awaiting_csv"):
                    return None, status
            raise
    message=None
    try:
        while True:
            if message is None:
                message,finished=session_ended(lambda:client.call("poll",initialized=True,**wait))
                if finished is not None:
                    return _graceful_finish(runtime,finished,last_sequence)
            snapshot=message["observation"]
            if snapshot is None:
                message=None
                time.sleep(0.02)
                continue
            sequence=message["sequence"]
            if sequence==last_sequence:
                # A lost POST response or a slow commit must not invoke a
                # nondeterministic participant model a second time.
                if not message["action_received"]:
                    client.call("respond",sequence=sequence,response=last_response)
                message=None
                time.sleep(0.02)
                continue
            if sequence!=last_sequence+1:
                raise SessionError("unexpected_sequence")
            deadline_at=datetime.fromisoformat(message["deadline_at"].replace("Z","+00:00"))
            deadline=time.monotonic()+max(0,(deadline_at-datetime.now(timezone.utc)).total_seconds())
            response=transport(snapshot,deadline)
            message,finished=session_ended(lambda:client.call("respond",sequence=sequence,response=response,
                deadline=deadline,wait=long_poll_seconds(deadline),wait_for="observation"))
            if finished is not None:
                return _graceful_finish(runtime,finished,last_sequence)
            last_sequence,last_response=sequence,response
    finally:
        runtime.close()
