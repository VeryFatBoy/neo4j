"""Load config.yaml and check it. Returns (config, errors); the app stops with a clear message if errors is not empty."""
from pathlib import Path

import yaml


def _need(cfg, path, kind, errors):
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            errors.append(f"config.yaml: missing '{path}'")
            return None
        node = node[part]
    if not isinstance(node, kind) or (isinstance(node, bool) and kind is not bool):
        names = "/".join(k.__name__ for k in (kind if isinstance(kind, tuple) else (kind,)))
        errors.append(f"config.yaml: '{path}' should be {names}")
        return None
    return node


def load_config(path="config.yaml"):
    errors = []
    try:
        with open(path, encoding="utf-8-sig") as f:   # utf-8-sig: an editor that adds a BOM doesn't break it
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}, [f"Can't find {path}."]
    except yaml.YAMLError as exc:
        return {}, [f"{path} isn't valid YAML: {exc}"]
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"Can't read {path}: {exc}"]
    if not isinstance(cfg, dict):
        return {}, [f"{path} should hold settings, one 'name: value' per line."]
    for p, k in [("app.title", str), ("app.basemap", bool), ("app.low_margin", (int, float)),
                 ("neo4j.uri_env", str), ("neo4j.username_env", str),
                 ("neo4j.password_env", str), ("neo4j.database_env", str),
                 ("jev.hosted.model", str), ("jev.hosted.request_timeout_ms", int),
                 ("jev.local.url", str), ("jev.local.model", str), ("jev.local.request_timeout_ms", int),
                 ("journey.origin", str), ("journey.destination", str), ("journey.lines", list),
                 ("routes.k", int), ("routes.max_k", int), ("routes.pool", int),
                 ("routes.max_overlap", (int, float)), ("routes.interchange_cost", (int, float)),
                 ("gds.graph_name", str),
                 ("paths.stations", str), ("paths.connections", str), ("paths.lines", str), ("paths.replay", str),
                 ("replay.record", bool)]:
        _need(cfg, p, k, errors)
    if "connect_timeout_s" in (cfg.get("neo4j") or {}):   # optional
        _need(cfg, "neo4j.connect_timeout_s", (int, float), errors)
    if not errors:
        r = cfg["routes"]
        if not 2 <= r["k"] <= r["max_k"]:
            errors.append("config.yaml: 'routes.k' should be between 2 and 'routes.max_k'")
        if r["pool"] < r["max_k"]:
            errors.append("config.yaml: 'routes.pool' should be at least 'routes.max_k'")
        if not 0 <= r["max_overlap"] <= 1:
            errors.append("config.yaml: 'routes.max_overlap' should be between 0 and 1")
        if r["interchange_cost"] < 0:
            errors.append("config.yaml: 'routes.interchange_cost' can't be negative")
        if not 0 <= cfg["app"]["low_margin"] <= 1:
            errors.append("config.yaml: 'app.low_margin' should be between 0 and 1")
        for p in ("jev.hosted.request_timeout_ms", "jev.local.request_timeout_ms"):
            a, b, c = p.split(".")
            if cfg[a][b][c] <= 0:
                errors.append(f"config.yaml: '{p}' should be more than 0")
        if cfg.get("neo4j", {}).get("connect_timeout_s", 8) <= 0:
            errors.append("config.yaml: 'neo4j.connect_timeout_s' should be more than 0")
        if not all(isinstance(x, str) for x in cfg["journey"]["lines"]):
            errors.append("config.yaml: 'journey.lines' should be a list of line names")
    if not errors:   # paths in the file are relative to the file, so the app works from any folder
        base = Path(path).resolve().parent
        for key, value in cfg["paths"].items():
            p = Path(value).expanduser()
            cfg["paths"][key] = str(p if p.is_absolute() else (base / p).resolve())
    return cfg, errors
