"""Offline tests for luma/api.py: how a `page` rides the site's cursors,
how a place and a tag are resolved, and what the batch answers. The
upstream calls are faked; nothing reaches the network.

    python -m pytest luma/test_api.py -q
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from luma import api, refs  # noqa: E402
from luma.fetch import LumaNotFound, LumaUpstreamError  # noqa: E402
from luma.test_parsers import load  # noqa: E402

PAGES = 3       # the fake list: pages 1..3, cursors "c2" and "c3"


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """Every call builds its answer (the response cache is off), and the cursor memo starts empty."""
    monkeypatch.setattr(api, "cached_call", lambda namespace, data, fn, ttl: fn(**data))
    api._cursors.clear()
    yield
    api._cursors.clear()


def entry(n):
    return {"api_id": f"evt-{n:08d}", "event": {"api_id": f"evt-{n:08d}", "url": f"slug{n}", "name": f"Event {n}"}}


def fake_list(calls):
    """A cursor list of PAGES pages of two rows; records the cursor of every call."""
    def page(cursor):
        calls.append(cursor)
        number = 1 if cursor is None else int(cursor[1:])
        more = number < PAGES
        return {"entries": [entry(number * 10), entry(number * 10 + 1)], "has_more": more,
                "next_cursor": f"c{number + 1}" if more else "c-stale"}
    return page


# ---- paging --------------------------------------------------------------------------

def test_a_cold_page_walks_the_pages_before_it_once(monkeypatch):
    calls = []
    page = fake_list(calls)
    monkeypatch.setattr(api, "fetch_events", lambda place, category, query, cursor, page_path: page(cursor))
    third = api.events_search(latitude=37.7, longitude=-122.4, page=3)
    assert calls == [None, "c2", "c3"]
    assert [e["id"] for e in third["events"]] == ["evt-00000030", "evt-00000031"]
    assert third["pagination"] == {"page": 3, "items_per_page": 50, "total_pages": None, "total_count": None,
                                   "has_next": False}       # the stale cursor of the last page is not followed
    calls.clear()
    second = api.events_search(latitude=37.7, longitude=-122.4, page=2)
    assert calls == ["c2"] and second["pagination"]["has_next"] is True     # its cursor is known: one call
    calls.clear()
    past = api.events_search(latitude=37.7, longitude=-122.4, page=4)
    assert calls == [] and past["events"] == [] and past["pagination"]["has_next"] is False
    assert api.events_search(latitude=37.7, longitude=-122.4, page=9)["events"] == []
    assert calls == []          # page 9 is cold: the pages before it lead back to the known end, at no call


def test_the_last_page_the_api_takes_has_no_next(monkeypatch):
    # the list goes on upstream, but page 61 is refused: page 60 must not point at it
    monkeypatch.setattr(api.config, "LUMA_MAX_PAGE", 3)
    monkeypatch.setattr(api, "fetch_events", lambda place, category, query, cursor, page_path:
                        {"entries": [entry(1)], "has_more": True, "next_cursor": "c"})
    assert api.events_search(latitude=37.7, longitude=-122.4, page=2)["pagination"]["has_next"] is True
    assert api.events_search(latitude=37.7, longitude=-122.4, page=3)["pagination"]["has_next"] is False


def test_a_kept_cursor_is_forgotten_after_an_hour(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(api, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    pages, calls = [1], []

    def fetch_events(place, category, query, cursor, page_path):
        calls.append(cursor)
        number = 1 if cursor is None else int(cursor[1:])
        more = number < pages[0]
        return {"entries": [entry(number)], "has_more": more, "next_cursor": f"c{number + 1}" if more else None}

    monkeypatch.setattr(api, "fetch_events", fetch_events)
    assert api.events_search(latitude=37.7, longitude=-122.4)["pagination"]["has_next"] is False
    pages[0] = 2                # the list grew a second page
    calls.clear()
    assert api.events_search(latitude=37.7, longitude=-122.4, page=2)["events"] == [] and calls == []   # the end is still known
    clock[0] += api.ttl.CURSOR_CACHE.total_seconds() + 1
    grown = api.events_search(latitude=37.7, longitude=-122.4, page=2)
    assert calls == [None, "c2"] and [e["id"] for e in grown["events"]] == ["evt-00000002"]


def test_two_lists_keep_their_own_cursors(monkeypatch):
    calls = []
    page = fake_list(calls)
    monkeypatch.setattr(api, "fetch_events", lambda place, category, query, cursor, page_path: page(cursor))
    api.events_search(latitude=37.7, longitude=-122.4, page=2)
    calls.clear()
    api.events_search(latitude=40.7, longitude=-74.0, page=2)
    assert calls == [None, "c2"]


def test_a_keyword_has_one_page(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "fetch_events", lambda place, category, query, cursor, page_path:
                        calls.append((query, cursor)) or {"entries": [entry(1)], "has_more": True, "next_cursor": "c2"})
    first = api.events_search(latitude=1.0, longitude=2.0, query="hackathon")
    assert len(first["events"]) == 1 and first["pagination"]["has_next"] is False and first["query"] == "hackathon"
    assert api.events_search(latitude=1.0, longitude=2.0, query="hackathon", page=2)["events"] == []
    assert calls == [("hackathon", None)]


def test_a_keyword_row_from_far_away_is_left_out(monkeypatch):
    def at(n, latitude, longitude):
        row = entry(n)
        row["event"]["coordinate"] = {"latitude": latitude, "longitude": longitude} if latitude is not None else None
        return row

    rows = [at(1, 37.80, -122.44), at(2, 33.02, -96.70), at(3, None, None), at(4, 37.33, -121.87)]
    monkeypatch.setattr(api, "fetch_events", lambda *a: {"entries": rows, "has_more": False})
    near = api.events_search(latitude=37.7749, longitude=-122.4194, query="hack")
    assert [e["id"] for e in near["events"]] == ["evt-00000001", "evt-00000003", "evt-00000004"]   # Texas is out
    box = api.events_search(north=38.0, south=37.0, east=-121.5, west=-123.0, query="hack")
    assert [e["id"] for e in box["events"]] == ["evt-00000001", "evt-00000003", "evt-00000004"]
    api._cursors.clear()
    everything = api.events_search(latitude=37.7749, longitude=-122.4194)          # no keyword: the site's list as it is
    assert len(everything["events"]) == 4


def test_a_keyword_row_inside_the_box_is_kept_however_large_the_box(monkeypatch):
    def at(n, latitude, longitude):
        row = entry(n)
        row["event"]["coordinate"] = {"latitude": latitude, "longitude": longitude}
        return row

    # San Francisco, Sydney, Port Moresby, Suva (Fiji), Delhi
    rows = [at(1, 37.77, -122.42), at(2, -33.87, 151.21), at(3, -9.44, 147.18), at(4, -18.14, 178.44), at(5, 28.61, 77.21)]
    monkeypatch.setattr(api, "fetch_events", lambda *a: {"entries": rows, "has_more": False})

    def kept(**box):
        return [e["id"][-1] for e in api.events_search(query="hack", **box)["events"]]

    assert kept(north=85.0, south=-85.0, east=180.0, west=-180.0) == ["1", "2", "3", "4", "5"]      # the whole map
    assert kept(north=50.0, south=-10.0, east=150.0, west=60.0) == ["3", "5"]       # Asia: Port Moresby sits in its far corner
    assert kept(north=-10.0, south=-25.0, east=-170.0, west=170.0) == ["4"]         # a box over the date line: Fiji, not Delhi
    assert kept(north=-10.0, south=-25.0, east=179.0, west=177.0) == ["4"]
    assert kept(north=-17.0, south=-19.0, east=-178.0, west=-179.9) == ["4"]        # Suva is 180 km west of it, across the line


# ---- events/search: the place --------------------------------------------------------

def test_a_city_is_searched_around_its_coordinate(monkeypatch):
    seen = {}

    def fetch_events(place, category, query, cursor, page_path):
        seen.update(place=place, category=category, page_path=page_path)
        return {"entries": [], "has_more": False}

    monkeypatch.setattr(api, "fetch_events", fetch_events)
    monkeypatch.setattr(api, "fetch_city", lambda ref: load("city.json"))
    monkeypatch.setattr(api, "fetch_categories", lambda: load("categories.json"))
    answer = api.events_search(city="sf", category="ai")
    assert seen == {"place": {"latitude": 37.7749, "longitude": -122.4194}, "category": "ai", "page_path": "/sf"}
    assert answer["city"] == {"id": "discplace-BDj7GNbGlsF7Cka", "name": "San Francisco", "slug": "sf",
                              "link": "https://luma.com/sf"}
    assert answer["category"] == {"id": "cat-ai", "name": "AI", "slug": "ai", "link": "https://luma.com/ai"}
    assert answer["events"] == []
    api.events_search(north=40.9, south=40.4, east=-73.7, west=-74.2)
    assert seen["place"] == {"north": 40.9, "south": 40.4, "east": -73.7, "west": -74.2} and seen["category"] is None


def test_an_unknown_city_or_category_is_not_found(monkeypatch):
    def no_city(ref):
        raise LumaNotFound("city atlantis not found")

    monkeypatch.setattr(api, "fetch_city", no_city)
    monkeypatch.setattr(api, "fetch_categories", lambda: load("categories.json"))
    monkeypatch.setattr(api, "fetch_events", lambda *a: pytest.fail("the list must not be asked"))
    with pytest.raises(LumaNotFound, match="city atlantis not found"):
        api.events_search(city="atlantis")
    # the site would answer a city's list for a city slug sent as a category: it is checked first
    with pytest.raises(LumaNotFound, match="category sf not found"):
        api.events_search(latitude=1.0, longitude=2.0, category="sf")


def test_a_category_by_its_id(monkeypatch):
    # categories/list answers ids that are not cat-<slug>: cat-fooddrink is food, cat-0Km9ZnuBjFAjwFl is fitness
    seen = []
    monkeypatch.setattr(api, "fetch_categories", lambda: load("categories.json"))
    monkeypatch.setattr(api, "fetch_events", lambda place, category, query, cursor, page_path:
                        seen.append(category) or {"entries": [], "has_more": False})
    for given in ("cat-fooddrink", "cat-0Km9ZnuBjFAjwFl", "cat-ai", "food"):
        answer = api.events_search(latitude=1.0, longitude=2.0, category=refs.resolve_category(given))
        assert answer["category"]["slug"] == seen[-1]
    assert seen == ["food", "fitness", "ai", "food"]

    def fetch_category(slug):
        seen.append(slug)
        if slug != "ai":
            raise LumaNotFound("x")
        return load("calendars_category.json")

    monkeypatch.setattr(api, "fetch_category", fetch_category)
    monkeypatch.setattr(api, "fetch_categories", lambda: {"entries": [{"category": {"api_id": "cat-AzVAf6VmE9JEre4",
                                                                                     "slug": "ai", "name": "AI"}}]})
    del seen[:]
    assert api.calendars_list(city=None, category=refs.resolve_category("cat-AzVAf6VmE9JEre4"))["category"]["slug"] == "ai"
    assert seen == ["azvaf6vme9jere4", "ai"]


# ---- events/details, events/batch ----------------------------------------------------

def test_event_details_and_its_fallback(monkeypatch):
    monkeypatch.setattr(api, "fetch_event", lambda ref: load("event_free.json"))
    assert api.events_details(event="k5woffsv")["id"] == "evt-UvVJTTBckZ6kMsc"

    def gone(ref):
        raise LumaNotFound("x")

    monkeypatch.setattr(api, "fetch_event", gone)
    with pytest.raises(LumaNotFound, match="event nope not found"):
        api.events_details(event="nope")

    def failing(ref):
        raise LumaUpstreamError("HTTP 502")

    monkeypatch.setattr(api, "fetch_event", failing)
    monkeypatch.setattr(api, "fetch_slug", lambda slug: load("slug_event.json"))
    assert api.events_details(event="k5woffsv")["name"] == "MRC HOT TROT Powered by Rox and On"
    with pytest.raises(LumaUpstreamError):          # the resolver reads slugs, not ids
        api.events_details(event="evt-UvVJTTBckZ6kMsc")
    monkeypatch.setattr(api, "fetch_slug", lambda slug: load("slug_calendar.json"))
    with pytest.raises(LumaNotFound):
        api.events_details(event="marinarunclub")


def test_batch_answers_in_the_order_asked(monkeypatch):
    rows = load("events_by_ids.json")["entries"]
    asked = {}

    def by_ids(ids):
        asked["ids"] = ids
        return list(reversed(rows))         # the site answers in its own order

    def one(ref):
        if ref == "k5woffsv":
            return load("event_free.json")
        raise LumaNotFound("x")

    monkeypatch.setattr(api, "fetch_events_by_ids", by_ids)
    monkeypatch.setattr(api, "fetch_event", one)
    answer = api.events_batch(events=["evt-pZzt2IshuxE9DD2", "k5woffsv", "evt-doesnotexist0000", "gone-slug",
                                      "evt-ir5RsHXH4gDHNbd"])
    assert asked["ids"] == ["evt-pZzt2IshuxE9DD2", "evt-doesnotexist0000", "evt-ir5RsHXH4gDHNbd"]   # slugs go one by one
    assert [e["id"] for e in answer["events"]] == ["evt-pZzt2IshuxE9DD2", "evt-UvVJTTBckZ6kMsc", "evt-ir5RsHXH4gDHNbd"]
    assert answer["not_found"] == ["evt-doesnotexist0000", "gone-slug"]
    assert (answer["requested_count"], answer["found_count"]) == (5, 3)
    assert "description" not in answer["events"][1]         # a slug's row has the shape of the others


def test_batch_gives_an_event_asked_twice_once(monkeypatch):
    monkeypatch.setattr(api, "fetch_events_by_ids", lambda ids: [{"api_id": "evt-UvVJTTBckZ6kMsc",
                                                                  **{k: v for k, v in load("event_free.json").items()
                                                                     if k in ("event", "calendar", "hosts")}}])
    monkeypatch.setattr(api, "fetch_event", lambda ref: load("event_free.json"))
    answer = api.events_batch(events=["evt-UvVJTTBckZ6kMsc", "k5woffsv"])
    assert [e["id"] for e in answer["events"]] == ["evt-UvVJTTBckZ6kMsc"] and answer["not_found"] == []


# ---- calendars -----------------------------------------------------------------------

def test_calendar_by_id_and_by_slug(monkeypatch):
    monkeypatch.setattr(api, "fetch_calendar", lambda ref: load("calendar.json"))
    monkeypatch.setattr(api, "fetch_slug", lambda slug: load("slug_calendar.json"))
    by_id = api.calendars_details("cal-JDvWKGO7IFEV3WY")
    assert by_id == api.calendars_details("marinarunclub") and by_id["name"] == "Marina Run Club Events"
    monkeypatch.setattr(api, "fetch_slug", lambda slug: load("slug_event.json"))
    with pytest.raises(LumaNotFound, match="k5woffsv is an event"):
        api.calendars_details("k5woffsv")
    monkeypatch.setattr(api, "fetch_slug", lambda slug: {"kind": "discover-place", "data": {}})
    with pytest.raises(LumaNotFound, match="calendar sf not found"):
        api.calendars_details("sf")

    def gone(ref):
        raise LumaNotFound("x")

    monkeypatch.setattr(api, "fetch_calendar", gone)
    with pytest.raises(LumaNotFound, match="calendar cal-doesnotexist0000 not found"):
        api.calendars_details("cal-doesnotexist0000")


def test_calendar_events_send_the_site_s_own_tokens(monkeypatch):
    sent = []

    def fetch_calendar_events(calendar_id, period, after, before, tag_id, available_only, query, cursor, page_path):
        sent.append((calendar_id, period, after, before, tag_id, available_only, query, cursor, page_path))
        return load("calendar_events_past.json")

    monkeypatch.setattr(api, "fetch_calendar_events", fetch_calendar_events)
    monkeypatch.setattr(api, "fetch_slug", lambda slug: load("slug_calendar.json"))
    answer = api.calendars_events("marinarunclub", period="past", tag="run", available_only=True, query="trot")
    assert sent[-1] == ("cal-JDvWKGO7IFEV3WY", "past", None, None, "tag-W2KjxJG6bjIUicV", True, "trot", None,
                        "/marinarunclub")
    assert answer["calendar"] == {"id": "cal-JDvWKGO7IFEV3WY", "name": "Marina Run Club Events",
                                  "slug": "marinarunclub", "link": "https://luma.com/marinarunclub"}
    assert answer["tag"] == {"id": "tag-W2KjxJG6bjIUicV", "name": "Run"} and answer["period"] == "past"
    assert len(answer["events"]) == 6 and answer["events"][0]["tags"][0]["name"] == "Run"
    assert answer["pagination"]["items_per_page"] == 100 and answer["pagination"]["has_next"] is False
    api.calendars_events("marinarunclub")
    assert sent[-1][1:6] == ("future", None, None, None, False)
    api.calendars_events("marinarunclub", tag="tag-W2KjxJG6bjIUicV")
    assert sent[-1][4] == "tag-W2KjxJG6bjIUicV"
    # a date range: the site wants both ends as instants, and leaves the `before` instant out
    dated = api.calendars_events("marinarunclub", date_from="2026-10-01", date_to="2026-10-31")
    assert sent[-1][1:4] == ("specific", "2026-10-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z")
    assert (dated["period"], dated["date_from"], dated["date_to"]) == (None, "2026-10-01", "2026-10-31")
    api.calendars_events("marinarunclub", date_to="2026-12-31")
    assert sent[-1][1:4] == ("specific", "1970-01-01T00:00:00.000Z", "2027-01-01T00:00:00.000Z")
    api.calendars_events("marinarunclub", date_from="2026-10-01")
    assert sent[-1][2:4] == ("2026-10-01T00:00:00.000Z", "2100-01-01T00:00:00.000Z")
    api.calendars_events("marinarunclub", date_to="9999-12-31")         # the last day a date holds: no day after it
    assert sent[-1][1:4] == ("specific", "1970-01-01T00:00:00.000Z", "9999-12-31T00:00:00.000Z")
    with pytest.raises(LumaNotFound, match="tag 'Yoga' not found on calendar Marina Run Club Events"):
        api.calendars_events("marinarunclub", tag="Yoga")


def test_calendars_list(monkeypatch):
    monkeypatch.setattr(api, "fetch_city", lambda ref: load("city.json"))
    asked = {}
    monkeypatch.setattr(api, "fetch_place_calendars",
                        lambda place_id, page_path: asked.update(place=place_id) or load("calendars_city.json"))
    monkeypatch.setattr(api, "fetch_category", lambda slug: load("calendars_category.json"))
    monkeypatch.setattr(api, "fetch_landing_calendars", lambda: load("calendars_featured.json"))
    city = api.calendars_list(city="sf", category=None)
    assert asked["place"] == "discplace-BDj7GNbGlsF7Cka"
    assert city["count"] == 7 and city["city"]["slug"] == "sf" and city["category"] is None
    category = api.calendars_list(city=None, category="ai")
    assert category["count"] == 10 and category["category"]["slug"] == "ai"
    assert api.calendars_list(city=None, category=None)["count"] == 18

    def gone(slug):
        raise LumaNotFound("x")

    monkeypatch.setattr(api, "fetch_category", gone)
    monkeypatch.setattr(api, "fetch_categories", lambda: load("categories.json"))
    with pytest.raises(LumaNotFound, match="category nope not found"):
        api.calendars_list(city=None, category="nope")


# ---- users, cities, categories -------------------------------------------------------

def test_user_events_ask_by_the_user_s_id(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "fetch_user", lambda ref: load("user.json"))
    monkeypatch.setattr(api, "fetch_user_events",
                        lambda user_id, period, cursor: sent.append((user_id, period, cursor)) or load("user_events.json"))
    answer = api.users_events("ajaytuniki", period="past")
    assert sent == [("usr-iSyVp7KRDGTBctH", "past", None)]      # the site reads the id only
    assert answer["user"] == {"id": "usr-iSyVp7KRDGTBctH", "name": "Ajay Tuniki", "username": "ajaytuniki",
                              "link": "https://luma.com/user/ajaytuniki"}
    assert answer["period"] == "past" and len(answer["events"]) == 7
    api.users_events("ajaytuniki")
    assert sent[-1][1] == "future"

    def gone(ref):
        raise LumaNotFound("x")

    monkeypatch.setattr(api, "fetch_user", gone)
    with pytest.raises(LumaNotFound, match="user nobody not found"):
        api.users_events("nobody")
    with pytest.raises(LumaNotFound, match="user nobody not found"):
        api.users_details("nobody")


def test_cities_and_categories(monkeypatch):
    monkeypatch.setattr(api, "fetch_cities", lambda: load("cities.json"))
    monkeypatch.setattr(api, "fetch_categories", lambda: load("categories.json"))
    cities = api.cities_list()
    assert cities["count"] == 87 and cities["cities"][0]["name"] == "Cape Town"
    categories = api.categories_list()
    assert categories["count"] == 12 and categories["categories"][5]["slug"] == "ai"
