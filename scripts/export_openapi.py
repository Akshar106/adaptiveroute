"""Write the API's OpenAPI schema to docs/api/openapi.json.

    uv run python scripts/export_openapi.py          # write
    uv run python scripts/export_openapi.py --check  # CI: fail if the file is stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from adaptiveroute.api.app import create_app
from adaptiveroute.config import Settings

OUT = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"


def render() -> str:
    app = create_app(Settings(_env_file=None, embedding_backend="hashing", log_json=False))  # type: ignore[call-arg]
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    spec = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != spec:
            print("docs/api/openapi.json is stale; run scripts/export_openapi.py", file=sys.stderr)
            sys.exit(1)
        print("openapi.json up to date")
    else:
        OUT.write_text(spec)
        print(f"wrote {OUT}")
