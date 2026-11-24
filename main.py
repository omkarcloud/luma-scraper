"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from luma import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # one event in full: time, venue, ticket types, guest count, hosts, description, registration form
    save("events_details.json", run(schemas.EventsDetailsSchema, api.events_details, event='posthog-tpmz'))

    # the events around San Francisco, 50 per page
    save("events_search.json", run(schemas.EventsSearchSchema, api.events_search, city='sf'))

    # three events in one call (up to 250 ids)
    save("events_batch.json", run(schemas.EventsBatchSchema, api.events_batch, events='evt-mgs8akIoPXqIV1l,evt-Eb5pVtJcbSWiWKC,evt-7I630jRiJSIMh45'))
