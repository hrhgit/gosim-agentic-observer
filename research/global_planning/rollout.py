from __future__ import annotations

import json
import time
from pathlib import Path

from .bridge import PlanningBridge
from .common import save, digest
from .environment import OfficialEnvironment, rewards_from_episode, returns_to_go


def episode(bundle, output, policy=None, *, node=None, models=True, rule_only=False, reuse_key=None):
    output = Path(output)
    completed = output / "episode.json"
    if completed.exists():
        value = json.loads(completed.read_text())
        if reuse_key is None or value.get("reuse_key") != reuse_key:
            raise ValueError("Existing episode has no matching resume identity")
        return value
    environment = OfficialEnvironment(bundle, output)
    bridge = None
    snapshots, choices, actions = [], [], []
    started = time.perf_counter()
    feature_seconds = model_seconds = 0.
    try:
        initialize, event = environment.reset()
        bridge = PlanningBridge(initialize, output / "agent.log", node=node, models=models, rule_only=rule_only)
        while event["type"] != "done":
            snapshot = event["payload"]
            snapshots.append(snapshot)
            bridge.send(dict(operation="decide", payload=snapshot))
            response = bridge.read()
            selected_action = None
            if response["type"] == "proposal":
                frame = response["frame"]
                before = time.perf_counter()
                selected, details = (frame["teacher"], {}) if policy is None else policy(frame)
                model_seconds += time.perf_counter() - before
                if not isinstance(selected, int) or not 0 <= selected < len(frame["candidates"]):
                    raise ValueError("Policy returned invalid action index")
                selected_action = frame["candidates"][selected]["action"]
                choices.append(dict(step=len(snapshots)-1, frame=frame, selected=selected, **details))
                bridge.send(dict(operation="choose", index=selected))
                response = bridge.read()
            if response["type"] != "action":
                raise RuntimeError("No action after selection")
            action = response["action"]
            if selected_action is not None and action != selected_action:
                raise AssertionError("Selected and executed actions differ")
            actions.append(action)
            event = environment.step(action)
        rewards = rewards_from_episode(snapshots, event["report"])
        returns = returns_to_go(rewards)
        for choice in choices:
            choice["return"] = returns[choice["step"]]
        value = dict(schema="global-planning-episode-v1", reuse_key=reuse_key, total=event["report"]["total"],
            components=event["report"]["components"], termination=event["report"]["termination"],
            rewards=rewards, reward_sum=sum(rewards), actions=actions, snapshots=snapshots, choices=choices,
            engine=event["timing"], models_enabled=models, rule_only=rule_only,
            node_cpu=response.get("cpu"), node_rss=response.get("rss"),
            model_seconds=model_seconds, wall_seconds=time.perf_counter()-started,
            input_hash=digest(initialize), fair_clock=False)
        save(completed, value)
        return value
    except Exception as error:
        save(output / "error.json", dict(status="failed", error=repr(error), completed_steps=len(actions)))
        raise
    finally:
        if bridge is not None:
            bridge.close()
        environment.close()
