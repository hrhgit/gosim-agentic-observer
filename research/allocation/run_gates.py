"""Run allocation screening, then omniscient-guidance gate; stop before training if failed."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.blockage.common import ROOT, digest, freeze, write_json

TOOLS = ROOT / "agents/claude-baseline-v1/tools"
BASE = ROOT / "agents/claude-baseline-v1"
SEEDS = (260031, 260032)


def run(command, log, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, env=env)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")
    if any(Path(str(arg)).name == "quick_sim.py" for arg in command):
        output = log.read_text(encoding="utf-8")
        if "Traceback (most recent call last)" in output:
            raise RuntimeError(f"agent exception in successful simulator process; see {log}")
        summary = json.loads(output.strip().splitlines()[-1])
        dest = Path(command[command.index("--out") + 1])
        write_json(dest / "summary.json", summary)


def make_copy(out):
    target = out / "temporary-baseline"
    if target.exists():
        return target
    shutil.copytree(BASE / "agent", target / "agent", ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    shutil.copytree(TOOLS, target / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(Path(__file__).parent / "guide.py", target / "agent/core/allocation_guide.py")
    path = target / "agent/core/planner.py"
    text = path.read_text(encoding="utf-8")
    anchors = [("from .astro import DEG, OMEGA, RAD", "from .astro import DEG, OMEGA, RAD\nfrom .allocation_guide import Guide"),
               ("        self.sv = survey", "        self.sv = survey\n        self.allocation_guide = Guide(survey.cat)"),
               ("            s = pot * (q if q < 1.25 else 1.25) * sci_urgency[zcoarse[z]]",
                "            allocation_factor = self.allocation_guide.factor(z, t, bool(flag))\n"
                "            if allocation_factor == 0:\n"
                "                continue\n"
                "            s = pot * (q if q < 1.25 else 1.25) * sci_urgency[zcoarse[z]] * allocation_factor")]
    for old, new in anchors:
        if text.count(old) != 1:
            raise ValueError(f"planner patch anchor ambiguous: {old}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return target


def source_hashes():
    paths = list((BASE / "agent").rglob("*.py")) + list(TOOLS.glob("*.py")) + list(Path(__file__).parent.glob("*.py"))
    paths += [ROOT / "scripts/build-formal-self-generated-evaluations.py"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def input_hashes(cards):
    return {str(p.relative_to(cards)): digest(p) for c in ("a", "c") for p in sorted((cards / c).rglob("*")) if p.is_file()}


def parallel(tasks, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(task) for task in tasks]
        for future in futures:
            future.result()


def reuse_screening(source, out, cards):
    """Reuse completed outputs only, after verifying all scientific sources and input hashes."""
    old = json.loads((source / "protocol.json").read_text())
    current = source_hashes()
    runner_key = str(Path(__file__).resolve().relative_to(ROOT))
    if {k: v for k, v in old["code"].items() if k != runner_key} != {k: v for k, v in current.items() if k != runner_key} or old["inputs"] != input_hashes(cards) or old["smoke"]:
        raise ValueError("reuse requires identical scientific code and data and a full-season source run")
    copied = {}
    for c in ("a", "c"):
        oracle = source / f"first-weather/oracle/{c}/shared"
        if (oracle / "summary.json").exists() and (oracle / "allocation.npz").exists():
            if json.loads((oracle / "summary.json").read_text())["truncated"]:
                raise ValueError("cannot reuse truncated oracle")
            shutil.copytree(oracle, out / f"first-weather/oracle/{c}/shared", dirs_exist_ok=True)
            for name in ("summary.json", "allocation.npz"):
                copied[str(oracle / name)] = digest(oracle / name)
        baseline = source / f"first-weather/{c}/baseline"
        if (baseline / "summary.json").exists():
            summary = json.loads((baseline / "summary.json").read_text())
            if summary["nights"] == 1:
                raise ValueError("cannot reuse truncated baseline")
            shutil.copytree(baseline, out / f"first-weather/{c}/baseline", dirs_exist_ok=True)
            for path in baseline.iterdir():
                if path.is_file():
                    copied[str(path)] = digest(path)
    return dict(protocol_sha256=digest(source / "protocol.json"), artifacts=copied)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "artifacts/allocation-v1")
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--jobs", type=int, default=2, choices=(1, 2, 4), help="independent simulator processes; at most two card LPs, each needs >=1 GB")
    p.add_argument("--reuse-screening", type=Path, help="completed screening artifacts from a previous interrupted orchestration")
    args = p.parse_args()
    out = args.out.resolve()
    cards = ROOT / "evaluation_assets/formal-v4-self-generated-v2/cards"
    cached = reuse_screening(args.reuse_screening.resolve(), out, cards) if args.reuse_screening else None
    freeze(out, dict(stage="allocation_gates", code=source_hashes(), inputs=input_hashes(cards), cards=["a", "c"],
                     weather="formal-v4-self-generated-v2 plus two newly self-generated reference-rate seeds; no official hidden weather",
                     seeds=list(SEEDS), smoke=args.smoke, model="shared", grid="planner Grid", bin_slots=4, jobs=args.jobs, reused_screening=cached,
                     screening=dict(metric="half L1 of seasonal 5-degree Grid time shares", threshold=0.1, rule="proceed if either card >= threshold"),
                     guidance=["baseline", "soft1", "soft3", "hard"],
                     guide_scope="science-potential ranking term only; required/request bonuses unchanged; hard exclusion before ranking except flagged required/request zones",
                     selection="maximum A improvement among soft1/soft3/hard satisfying C>=-1%, selected only on first weather",
                     validation_gate=dict(A_mean_gain=0.03, C_mean_gain=-0.01, validation_seeds_only=True),
                     no_training_before_gate=True, local_scores_not_online_predictions=True))
    copy = make_copy(out)
    env = os.environ.copy()
    env["OBSERVER_PROJECT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT)
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["OBSERVER_EVAL_SET"] = str(cards.parent)
    for key in ("BASE_GUIDE", "BASE_GUIDE_STRENGTH"):
        env.pop(key, None)
    try:
        def screen_card(c):
            dest = out / "first-weather" / c
            cmd = [sys.executable, str(TOOLS / "upper_bound.py"), "--eval-set", str(cards.parent), "--cards", c, "--models", "shared", "--export", str(out / "first-weather/oracle")]
            if args.smoke:
                cmd += ["--max-bins", "8"]
            if not (out / f"first-weather/oracle/{c}/shared/allocation.npz").exists():
                run(cmd, dest / "oracle.log", env)
            print(f"{c}: oracle exported", flush=True)
            cmd = [sys.executable, str(copy / "tools/quick_sim.py"), "--card", c, "--out", str(dest / "baseline"), "--quiet"]
            if args.smoke:
                cmd += ["--nights", "1"]
            if not (dest / "baseline/summary.json").exists():
                run(cmd, dest / "baseline.log", env)
            print(f"{c}: baseline complete", flush=True)
            run([sys.executable, str(TOOLS / "compare_allocation.py"), "--allocation", str(out / f"first-weather/oracle/{c}/shared/allocation.npz"),
                 "--baseline", str(dest / "baseline"), "--card-dir", str(cards / c), "--out", str(dest / "comparison.json")], dest / "comparison.log", env)
        parallel([lambda c=c: screen_card(c) for c in ("a", "c")], args.jobs)
        screening = {c: json.loads((out / f"first-weather/{c}/comparison.json").read_text()) for c in ("a", "c")}
        if args.smoke:
            # One guided night also verifies matching zone IDs and temporary patch execution.
            e = env.copy()
            e["BASE_GUIDE"] = str(out / "first-weather/oracle/a/shared/allocation.npz")
            e["BASE_GUIDE_STRENGTH"] = "1"
            run([sys.executable, str(copy / "tools/quick_sim.py"), "--card", "a", "--nights", "1", "--out", str(out / "smoke-guide"), "--quiet"], out / "smoke-guide.log", e)
            write_json(out / "report.json", dict(smoke_only=True, verdict="smoke_complete", screening=screening))
            return
        if not any(r["proceed"] for r in screening.values()):
            write_json(out / "report.json", dict(verdict="stop_allocation_already_close", screening=screening, training_started=False))
            return
        scenarios = [cards.parent]
        results = {}
        selected = None
        for scenario_index in range(3):
            if scenario_index:
                name = f"d3-gate-s{SEEDS[scenario_index - 1]}"
                generated = BASE / "eval_sets" / name
                freeze(out / name, dict(seed=SEEDS[scenario_index - 1], code=source_hashes(), origin="self-generated reference weather; copied official public inputs"))
                if not (generated / "manifest.json").exists():
                    run([sys.executable, str(TOOLS / "make_holdout_set.py"), "--name", name, "--seed", str(SEEDS[scenario_index - 1])], out / name / "generation.log", env)
                manifest = json.loads((generated / "manifest.json").read_text())
                if manifest["seed"] != SEEDS[scenario_index - 1]:
                    raise ValueError("generated seed mismatch")
                scenarios.append(generated)
                write_json(out / name / "inputs.json", input_hashes(generated / "cards"))
            scenario = scenarios[scenario_index]
            env["OBSERVER_EVAL_SET"] = str(scenario)
            stage = out / ("first-weather" if scenario_index == 0 else f"weather-{scenario_index}")
            def oracle_card(c):
                if scenario_index:
                    run([sys.executable, str(TOOLS / "upper_bound.py"), "--eval-set", str(scenario), "--cards", c, "--models", "shared", "--export", str(stage / "oracle")], stage / c / "oracle.log", env)
            parallel([lambda c=c: oracle_card(c) for c in ("a", "c")], args.jobs)
            def simulate(c, version):
                dest = stage / c / version
                e = env.copy()
                if version != "baseline":
                    e["BASE_GUIDE"] = str(stage / f"oracle/{c}/shared/allocation.npz")
                    e["BASE_GUIDE_STRENGTH"] = dict(soft1="1", soft3="3", hard="hard")[version]
                run([sys.executable, str(copy / "tools/quick_sim.py"), "--card", c, "--out", str(dest), "--quiet"], stage / c / f"{version}.log", e)
                print(f"weather {scenario_index} {c} {version}: complete", flush=True)
            parallel([lambda c=c, v=v: simulate(c, v) for c in ("a", "c") for v in ("baseline", "soft1", "soft3", "hard") if not (scenario_index == 0 and v == "baseline")], args.jobs)
            scores = {c: {v: json.loads((stage / c / v / "summary.json").read_text())["total"] for v in ("baseline", "soft1", "soft3", "hard")} for c in ("a", "c")}
            gains = {c: {v: scores[c][v] / scores[c]["baseline"] - 1 for v in ("soft1", "soft3", "hard")} for c in ("a", "c")}
            results[str(scenario_index)] = dict(scores=scores, relative_gains=gains)
            if scenario_index == 0:
                eligible = [v for v in ("soft1", "soft3", "hard") if gains["c"][v] >= -0.01]
                selected = max(eligible, key=lambda v: gains["a"][v]) if eligible else None
                write_json(out / "selected-guide.json", dict(version=selected, chosen_on_weather=0, results=results["0"]))
            write_json(out / "partial-results.json", results)
        means = {c: float(np.mean([results[str(i)]["relative_gains"][c][selected] for i in (1, 2)])) if selected else None for c in ("a", "c")}
        passed = selected is not None and means["a"] >= 0.03 and means["c"] >= -0.01
        write_json(out / "report.json", dict(verdict="proceed_small_training" if passed else "stop_oracle_guidance_gate_failed",
                   screening=screening, selected=selected, validation_means=means, results=results, training_started=False))
        print(json.dumps(dict(selected=selected, validation_means=means, passed=passed)), flush=True)
    except Exception as e:
        write_json(out / "failure.json", dict(error=repr(e)))
        raise


if __name__ == "__main__":
    main()
