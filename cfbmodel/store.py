"""JSON file helpers."""
import json
from pathlib import Path


def load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def save(path, obj, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    else:
        text = json.dumps(obj, indent=1, sort_keys=True)
    path.write_text(text + "\n")
