"""Write the envelope JSON Schema to packages/shared for the web app's TS types."""

import json
from pathlib import Path

from services.agents.envelope import Envelope

path = Path(__file__).parents[2] / "packages/shared/envelope.schema.json"
path.write_text(json.dumps(Envelope.model_json_schema(), indent=2) + "\n")
print(f"wrote {path}")
