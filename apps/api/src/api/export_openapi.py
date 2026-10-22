"""Write the API contract to contracts/openapi.json. CI fails if the committed copy is stale."""

import json
from pathlib import Path

from api.main import app

OUT = Path(__file__).resolve().parents[4] / "contracts" / "openapi.json"


def main() -> None:
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
