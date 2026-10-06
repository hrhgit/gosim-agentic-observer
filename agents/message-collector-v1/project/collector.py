#!/usr/bin/env python3
"""Receive and capture formal survey messages; issue only legal waits."""
from datetime import datetime, timedelta
import json
import sys
from capture import Capture


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def wait_action(payload, minimum, maximum, survey_end):
    now = parse_time(payload['now_utc'])
    end = parse_time(payload.get('survey_end_utc') or survey_end)
    remaining = (end - now).total_seconds()
    # Receive the last batch just before survey end. A wait directly to the end
    # could leave the final slots' carried messages undelivered.
    if 1 < remaining <= maximum:
        until = (end - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')
        return {'action': 'wait', 'until_utc': until}
    return {'action': 'wait', 'duration_seconds': maximum if remaining > maximum else minimum}


def main():
    capture = Capture(sys.stderr)
    minimum = maximum = survey_end = None
    try:
        for raw in sys.stdin:
            capture.record(raw)
            message = json.loads(raw)
            kind = message.get('message_type')
            if kind == 'initialize':
                payload = message['payload']
                exposure = payload['instrument']['exposure']
                minimum = int(exposure['min_duration_seconds'])
                maximum = int(exposure['max_duration_seconds'])
                survey_end = payload['survey']['end_utc']
            elif kind == 'decision_request':
                if survey_end is None:
                    raise ValueError('decision request before initialize')
                response = {'protocol_version': message['protocol_version'],
                            'message_type': 'decision_response',
                            'decision_sequence': message['decision_sequence'],
                            **wait_action(message['payload'], minimum, maximum, survey_end)}
                sys.stdout.write(json.dumps(response, separators=(',', ':')) + '\n')
                sys.stdout.flush()
            elif kind == 'finish':
                break
    finally:
        capture.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
