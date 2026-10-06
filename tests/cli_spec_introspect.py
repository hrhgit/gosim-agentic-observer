"""Describes the Python survey26 parser as the shared command spec (cli/spec.json) format."""
import argparse

COMMON = {"--json", "--token", "--lang", "--api", "-h", "--help"}


def _action(a):
    kind = ("flag" if isinstance(a, argparse._StoreTrueAction) else
            "flag_false" if isinstance(a, argparse._StoreFalseAction) else "value")
    item = {"flags": list(a.option_strings), "dest": a.dest, "kind": kind}
    if kind != "value" and a.default is None:
        item["default"] = None  # unset unless given (e.g. --lock/--unlock)
    if kind == "value":
        item["type"] = "int" if a.type is int else "str"
        if a.choices is not None:
            item["choices"] = [c for c in a.choices]
        if a.default is not None and a.default is not argparse.SUPPRESS:
            item["default"] = a.default
        if a.metavar:
            item["metavar"] = a.metavar
    if a.required:
        item["required"] = True
    item["help"] = a.help or ""
    return item


def describe(parser):
    commands = []

    def walk(p, path, short=""):
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        func = p._defaults.get("func")
        entry = {"path": path, "help": p.description or short, "group": bool(subs), "func": func.__name__ if func else None,
                 "positionals": [], "options": []}
        for a in p._actions:
            if isinstance(a, (argparse._SubParsersAction, argparse._HelpAction, argparse._VersionAction)):
                continue
            if a.option_strings:
                if set(a.option_strings) & COMMON:
                    continue
                entry["options"].append(_action(a))
            else:
                pos = {"name": a.dest, "nargs": a.nargs if a.nargs is not None else 1, "help": a.help or ""}
                if a.choices is not None:
                    pos["choices"] = list(a.choices)
                if a.default is not None:
                    pos["default"] = a.default
                entry["positionals"].append(pos)
        if subs:
            entry["subcommand_dest"] = subs[0].dest
        if path:
            commands.append(entry)
        for sub in subs:
            helps = {c.dest: c.help or "" for c in sub._choices_actions}
            for name, child in sub.choices.items():
                walk(child, path + [name], helps.get(name, ""))
    walk(parser, [])
    return commands


def spec(module):
    parser = module.build_parser()
    return {"version": module.__version__, "prog": parser.prog, "description": parser.description, "epilog": parser.epilog,
            "commands": describe(parser)}
