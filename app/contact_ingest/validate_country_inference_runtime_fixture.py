from __future__ import annotations

import json

from app.contact_ingest.country_inference_runtime import (
    CONTACT_COUNTRY_RUNTIME_MODEL_VERSION,
    build_contact_scoped_city_model,
)
from app.contact_ingest.validate_country_inference_fixture import ENTITY_IDS, seed_fixture
from app.db import postgres_conn


def validate() -> dict[str, object]:
    seed_fixture()

    # The base fixture has three authoritative AU/Sydney entities, but only the
    # first is contact-owned. Make the other two explicit contact entities so the
    # runtime learner has the required three independent source-grounded samples.
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for key in ("seed_au_2", "seed_au_3"):
                cur.execute(
                    """
                    INSERT INTO contact.channel(
                        entity_id, channel_type, channel_value, normalized_value
                    ) VALUES (%s, 'EMAIL', %s, %s)
                    """,
                    (
                        ENTITY_IDS[key],
                        f"{key}@runtime-fixture.example",
                        f"{key}@runtime-fixture.example",
                    ),
                )
        conn.commit()

    model = build_contact_scoped_city_model()
    assert model["sydney"] == ("AU", 1.0, 3)

    return {
        "status": "PASS",
        "runtime_model_version": CONTACT_COUNTRY_RUNTIME_MODEL_VERSION,
        "sydney": model["sydney"],
        "city_keys": len(model),
        "global_entity_mention_training_scan": False,
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
