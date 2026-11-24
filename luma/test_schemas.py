"""Offline tests for the /luma/* request schemas and the reference resolvers.

    python -m pytest luma/test_schemas.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from luma import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

EVENT_ID = "evt-UvVJTTBckZ6kMsc"
CALENDAR_ID = "cal-JDvWKGO7IFEV3WY"
USER_ID = "usr-4RfJK79d4BBbtk2"
PLACE_ID = "discplace-BDj7GNbGlsF7Cka"


def ok(schema, **query):
    data, error = load_query(schema, query)
    assert error is None, error
    return data


def refused(schema, field, **query):
    data, error = load_query(schema, query)
    assert data is None and field in error["errors"], error
    return " ".join(error["errors"][field])


# ---- refs --------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (EVENT_ID, EVENT_ID),
    (" k5woffsv ", "k5woffsv"),
    ("smart-glass-hack-nyc", "smart-glass-hack-nyc"),
    ("https://luma.com/k5woffsv?tk=abc", "k5woffsv"),
    ("https://lu.ma/smart-glass-hack-nyc", "smart-glass-hack-nyc"),
    ("luma.com/k5woffsv", "k5woffsv"),
    ("https://luma.com/event/" + EVENT_ID, EVENT_ID),
])
def test_resolve_event(value, expected):
    assert refs.resolve_event(value) == expected


@pytest.mark.parametrize("value", [CALENDAR_ID, USER_ID, "https://luma.com/user/someone", "https://luma.com/",
                                   "https://luma.com/discover", "has space", "", "https://example.com/k5woffsv"])
def test_resolve_event_refuses(value):
    with pytest.raises(ValueError):
        refs.resolve_event(value)


def test_resolve_events():
    assert refs.resolve_events(f"{EVENT_ID}, k5woffsv,{EVENT_ID}, https://luma.com/abc") == [EVENT_ID, "k5woffsv", "abc"]
    ids = [f"evt-aaaaaa{i:04d}" for i in range(251)]
    assert len(refs.resolve_events(",".join(ids[:250]))) == 250
    with pytest.raises(ValueError, match="At most 250 events"):
        refs.resolve_events(",".join(ids))
    with pytest.raises(ValueError, match="At most 20 of the events may be slugs or links"):
        refs.resolve_events(",".join(f"slug{i}" for i in range(21)))
    with pytest.raises(ValueError):
        refs.resolve_events(" , ")


@pytest.mark.parametrize("value, expected", [
    (CALENDAR_ID, CALENDAR_ID),
    ("marinarunclub", "marinarunclub"),
    ("https://luma.com/marinarunclub?k=c", "marinarunclub"),
    ("https://luma.com/calendar/" + CALENDAR_ID, CALENDAR_ID),
])
def test_resolve_calendar(value, expected):
    assert refs.resolve_calendar(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("ajaytuniki", "ajaytuniki"),
    ("@ajaytuniki", "ajaytuniki"),
    (USER_ID, USER_ID),
    ("https://luma.com/user/ajaytuniki", "ajaytuniki"),
    ("https://luma.com/@ajaytuniki", "ajaytuniki"),
    ("https://luma.com/user/" + USER_ID, USER_ID),
])
def test_resolve_user(value, expected):
    assert refs.resolve_user(value) == expected


def test_resolvers_refuse_another_kind_of_record():
    for resolver, wrong in ((refs.resolve_calendar, EVENT_ID), (refs.resolve_user, EVENT_ID),
                            (refs.resolve_user, "https://luma.com/k5woffsv"), (refs.resolve_city, CALENDAR_ID),
                            (refs.resolve_city, "discover"), (refs.resolve_category, EVENT_ID),
                            (refs.resolve_category, "")):
        with pytest.raises(ValueError, match="Must be a Luma"):
            resolver(wrong)


def test_resolve_city_and_category():
    assert refs.resolve_city("SF") == "sf"
    assert refs.resolve_city("https://luma.com/sf/map") == "sf"
    assert refs.resolve_city(PLACE_ID) == PLACE_ID
    assert refs.resolve_category("AI") == "ai"
    assert refs.resolve_category("cat-ai") == "ai"
    assert refs.resolve_category("https://luma.com/ai") == "ai"


def test_links_out():
    assert refs.page_link("k5woffsv") == "https://luma.com/k5woffsv" and refs.page_link(None) is None
    assert refs.user_link("ajaytuniki", USER_ID) == "https://luma.com/user/ajaytuniki"
    assert refs.user_link(None, USER_ID) == "https://luma.com/user/" + USER_ID
    assert refs.user_link(None, None) is None
    assert refs.calendar_page_link("cal-JDvWKGO7IFEV3WY") == "https://luma.com/calendar/cal-JDvWKGO7IFEV3WY"
    assert refs.calendar_page_link(None) is None


# ---- events/search -----------------------------------------------------------------

def test_events_search_takes_the_place_in_one_way():
    assert ok(schemas.EventsSearchSchema, city="https://luma.com/sf") == {
        "city": "sf", "latitude": None, "longitude": None, "north": None, "south": None, "east": None, "west": None,
        "category": None, "query": None, "page": 1}
    point = ok(schemas.EventsSearchSchema, latitude="37.7749", longitude="-122.4194", category="AI",
               query=" hack  night ", page="3")
    assert (point["latitude"], point["longitude"]) == (37.7749, -122.4194)
    assert point["category"] == "ai" and point["query"] == "hack night" and point["page"] == 3
    box = ok(schemas.EventsSearchSchema, north="40.92", south="40.49", east="-73.70", west="-74.26")
    assert (box["north"], box["south"], box["east"], box["west"]) == (40.92, 40.49, -73.70, -74.26)
    for query in ({}, {"category": "ai"}, {"city": "sf", "latitude": "1", "longitude": "2"},
                  {"latitude": "1", "longitude": "2", "north": "2", "south": "1", "east": "2", "west": "1"}):
        assert "one way" in refused(schemas.EventsSearchSchema, "city", **query)


def test_events_search_refusals():
    assert "go together" in refused(schemas.EventsSearchSchema, "longitude", latitude="37.77")
    assert "go together" in refused(schemas.EventsSearchSchema, "latitude", longitude="-122.4")
    assert "between -90 and 90" in refused(schemas.EventsSearchSchema, "latitude", latitude="95", longitude="2")
    assert "between -180 and 180" in refused(schemas.EventsSearchSchema, "longitude", latitude="5", longitude="200")
    assert "number of degrees" in refused(schemas.EventsSearchSchema, "latitude", latitude="north", longitude="2")
    assert "needs" in refused(schemas.EventsSearchSchema, "east", north="40", south="39")
    assert "greater than" in refused(schemas.EventsSearchSchema, "north", north="40", south="40.4", east="1", west="0")
    assert "Must be a Luma category" in refused(schemas.EventsSearchSchema, "category", city="sf", category="a b")
    assert "Must be a luma.com link" in refused(schemas.EventsSearchSchema, "city", city="https://example.com/sf")
    assert "Must be a Luma city" in refused(schemas.EventsSearchSchema, "city", city="san francisco")
    assert "Unknown field" in refused(schemas.EventsSearchSchema, "sort", city="sf", sort="date")
    assert "less than or equal to 60" in refused(schemas.EventsSearchSchema, "page", city="sf", page="61")
    refused(schemas.EventsSearchSchema, "page", city="sf", page="0")
    refused(schemas.EventsSearchSchema, "query", city="sf", query="x" * 101)


def test_an_optional_param_sent_empty_is_the_absent_param():
    assert ok(schemas.EventsSearchSchema, city="sf", category="", query=" ", page="", latitude="")["page"] == 1
    assert ok(schemas.CalendarsEventsSchema, calendar="x", period="", tag="", available_only="", date_from="") == {
        "calendar": "x", "period": None, "date_from": None, "date_to": None, "tag": None, "available_only": None,
        "query": None, "page": 1}
    assert "Must not be empty" in refused(schemas.EventsDetailsSchema, "event", event="")


# ---- the one-input endpoints -------------------------------------------------------

def test_one_param_per_input():
    assert ok(schemas.EventsDetailsSchema, event="https://luma.com/k5woffsv") == {"event": "k5woffsv"}
    assert ok(schemas.EventsBatchSchema, events=f"{EVENT_ID},k5woffsv") == {"events": [EVENT_ID, "k5woffsv"]}
    assert ok(schemas.CalendarsDetailsSchema, calendar="https://luma.com/marinarunclub") == {"calendar": "marinarunclub"}
    assert ok(schemas.UsersDetailsSchema, user="https://luma.com/user/ajaytuniki") == {"user": "ajaytuniki"}
    assert ok(schemas.UsersEventsSchema, user="@ajaytuniki", period="PAST") == {"user": "ajaytuniki", "period": "past",
                                                                               "page": 1}
    assert ok(schemas.CitiesListSchema) == {} and ok(schemas.CategoriesListSchema) == {}
    assert "Missing data" in refused(schemas.EventsDetailsSchema, "event")
    assert "Must be a Luma event id" in refused(schemas.EventsDetailsSchema, "event", event=CALENDAR_ID)
    assert "At most 20" in refused(schemas.EventsBatchSchema, "events", events=",".join(f"s{i}" for i in range(21)))
    assert "Must be a Luma calendar id" in refused(schemas.CalendarsDetailsSchema, "calendar", calendar=EVENT_ID)
    assert "Must be a Luma username" in refused(schemas.UsersDetailsSchema, "user", user="https://luma.com/k5woffsv")
    assert "Unknown field" in refused(schemas.CitiesListSchema, "country", country="us")
    assert "Unknown field" in refused(schemas.EventsDetailsSchema, "url", event="k5woffsv", url="x")


# ---- calendars/events, calendars/list, users/events --------------------------------

def test_calendars_events():
    data = ok(schemas.CalendarsEventsSchema, calendar="marinarunclub", period="Past", tag="Run", available_only="true",
              query="trot", page="2")
    assert data == {"calendar": "marinarunclub", "period": "past", "date_from": None, "date_to": None, "tag": "Run",
                    "available_only": True, "query": "trot", "page": 2}
    dated = ok(schemas.CalendarsEventsSchema, calendar=CALENDAR_ID, date_from="2026-10-01", date_to="2026-10-31")
    assert (dated["date_from"], dated["date_to"], dated["period"]) == ("2026-10-01", "2026-10-31", None)
    assert ok(schemas.CalendarsEventsSchema, calendar=CALENDAR_ID, date_to="2026-10-31")["date_from"] is None
    assert "upcoming, past" in refused(schemas.CalendarsEventsSchema, "period", calendar="x", period="all")
    assert "not both" in refused(schemas.CalendarsEventsSchema, "period", calendar="x", period="past",
                                 date_from="2026-10-01")
    assert "on or after" in refused(schemas.CalendarsEventsSchema, "date_to", calendar="x", date_from="2026-10-02",
                                    date_to="2026-10-01")
    assert "YYYY-MM-DD" in refused(schemas.CalendarsEventsSchema, "date_from", calendar="x", date_from="10/01/2026")
    assert "true or false" in refused(schemas.CalendarsEventsSchema, "available_only", calendar="x", available_only="maybe")
    refused(schemas.CalendarsEventsSchema, "tag", calendar="x", tag="t" * 81)


def test_calendars_list_and_users_events():
    assert ok(schemas.CalendarsListSchema) == {"city": None, "category": None}
    assert ok(schemas.CalendarsListSchema, city="SF") == {"city": "sf", "category": None}
    assert ok(schemas.CalendarsListSchema, category="https://luma.com/ai") == {"city": None, "category": "ai"}
    assert "not both" in refused(schemas.CalendarsListSchema, "category", city="sf", category="ai")
    assert "upcoming, past" in refused(schemas.UsersEventsSchema, "period", user="ajaytuniki", period="hosting")


def test_every_field_is_described():
    for name in dir(schemas):
        schema = getattr(schemas, name)
        if isinstance(schema, type) and issubclass(schema, schemas.BaseSchema) and name.endswith("Schema"):
            for field_name, field in schema().fields.items():
                assert field.metadata.get("description"), f"{name}.{field_name} has no description"
