"""Offline tests for luma/parsers.py against the raw captures in fixtures/
(taken 2026-11-21, untrimmed), plus the transport's reading of the site's
refusals.

    python -m pytest luma/test_parsers.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from luma import fetch, parsers, refs  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    """A fixture by its name; the large ones are kept packed (name + ".gz")."""
    path = os.path.join(FIXTURES, name)
    if os.path.exists(path + ".gz"):
        with gzip.open(path + ".gz", "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def keys_of(value, found=None):
    """Every dict key anywhere in a parsed answer."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            keys_of(item, found)
    elif isinstance(value, list):
        for item in value:
            keys_of(item, found)
    return found


# ---- event lists ---------------------------------------------------------------------

def test_events_page_near_a_point():
    rows, cursor = parsers.parse_events_page(load("events_near_point.json"))
    assert len(rows) == 48 and cursor and cursor.startswith("eyJ")
    first = rows[0]
    assert first["id"] == "evt-0YE1ZHMxURzdozg"
    assert first["name"] == "TRIPLE THREAT by YR/ZRO"
    assert first["link"] == "https://luma.com/gplg6x6x"
    assert first["start_at"] == "2026-11-21T06:00:00Z" and first["end_at"] == "2026-11-22T00:00:00Z"
    assert first["timezone"] == "America/Los_Angeles"
    assert first["is_online"] is False and first["location_type"] == "offline"
    assert first["guest_count"] == 187 and first["is_guest_list_public"] is True
    assert first["tickets"]["is_free"] is True and first["tickets"]["price"] is None
    assert first["location"]["address"] == "540 S 10th St, San Jose, CA 95112, USA"
    assert first["location"]["city"] == "San Jose" and first["location"]["country_code"] == "US"
    assert first["location"]["latitude"] == 37.33262
    assert first["calendar"]["id"].startswith("cal-")
    assert all(row["id"].startswith("evt-") and row["link"].startswith("https://luma.com/") for row in rows)
    assert all(row["start_at"].endswith("Z") for row in rows)


def test_events_page_every_kind_of_row():
    rows, _ = parsers.parse_events_page(load("events_near_point.json"))
    paid = [row for row in rows if row["tickets"]["is_free"] is False and row["tickets"]["price"] is not None]
    assert paid and all(row["tickets"]["currency"] == "USD" and row["tickets"]["price"] > 0 for row in paid)
    hidden_address = [row for row in rows if row["location"]["is_address_hidden"]]
    assert hidden_address
    for row in hidden_address:      # the city and an approximate point, never the street
        assert row["location"]["address"] is None and row["location"]["venue"] is None
        assert row["location"]["city"] and row["location"]["is_coordinate_approximate"] is True
    hidden_guests = [row for row in rows if row["is_guest_list_public"] is False]
    assert hidden_guests
    assert all(row["guest_count"] is None and row["ticket_count"] is None and row["featured_guests"] == []
               for row in hidden_guests)
    shown = [row for row in rows if row["is_guest_list_public"]]
    assert all(isinstance(row["guest_count"], int) for row in shown)


def test_events_page_one_row_keyword_and_empty():
    rows, cursor = parsers.parse_events_page(load("events_one_row.json"))
    assert len(rows) == 1 and cursor
    rows, cursor = parsers.parse_events_page(load("events_keyword.json"))
    assert len(rows) == 20 and cursor is None           # a keyword list has no next page
    assert parsers.parse_events_page(load("events_empty.json")) == ([], None)
    assert parsers.parse_events_page(None) == ([], None)
    assert parsers.parse_events_page({"entries": [None, "x"], "has_more": True, "next_cursor": None}) == ([], None)


def test_next_cursor_needs_has_more():
    # the site repeats a cursor on its last page: following it would loop
    assert parsers.next_cursor({"has_more": False, "next_cursor": "abc"}) is None
    assert parsers.next_cursor({"has_more": True, "next_cursor": "abc"}) == "abc"
    assert parsers.next_cursor({"has_more": True}) is None


def test_calendar_events_carry_their_tags():
    rows, cursor = parsers.parse_events_page(load("calendar_events_past.json"), parsers.parse_calendar_event_row)
    assert len(rows) == 6 and cursor is None
    assert rows[0]["id"] == "evt-zWRsskn4OBElOXa"      # the event's id, not the calendar row's calev- id
    assert rows[0]["platform"] == "luma" and rows[0]["is_managed_by_calendar"] is True
    assert all("Run" in [tag["name"] for tag in row["tags"]] for row in rows)
    assert set(rows[0]["tags"][0]) == {"id", "name", "color"}


def test_a_calendar_row_hosted_outside_luma():
    rows, _ = parsers.parse_events_page(load("calendar_events_external.json"), parsers.parse_calendar_event_row)
    external, hosted = rows
    assert external["platform"] == "external" and external["name"] == "Gemini 4 Sprints - Bangalore Edition"
    assert external["slug"] is None
    assert external["link"] == "https://rsvp.withgoogle.com/events/gemini-sprints?pli=1"     # the event's own page
    assert external["start_at"] == "2026-11-27T03:30:00Z"
    assert external["end_at"] == "2026-11-27T12:30:00Z"                                      # + P0Y0M0DT9H0M0S
    assert external["location"]["city"] == "Bengaluru"
    # the row next to it, hosted on Luma, keeps its slug, its Luma link and the end the site sends
    assert hosted["platform"] == "luma" and hosted["slug"]
    assert hosted["link"] == "https://luma.com/" + hosted["slug"] and hosted["end_at"]


def test_an_external_row_with_an_odd_value():
    def row(**event):
        return parsers.parse_calendar_event_row({"api_id": "calev-x", "platform": "external",
                                                 "event": {"name": "x", "start_at": "2026-11-27T03:30:00.000Z", **event}})

    assert row(url="not a link")["link"] is None and row(url="not a link")["slug"] is None
    assert row()["link"] is None and row()["end_at"] is None                    # no url, no duration
    assert row(url="http://a.example/e", duration_interval="junk")["link"] == "http://a.example/e"
    assert row(duration_interval="junk")["end_at"] is None
    assert row(end_at="2026-11-27T05:00:00.000Z", duration_interval="PT9H")["end_at"] == "2026-11-27T05:00:00Z"
    # a row without a platform is read as a Luma event
    plain = parsers.parse_calendar_event_row({"event": {"api_id": "evt-x", "url": "abc"}})
    assert plain["slug"] == "abc" and plain["link"] == "https://luma.com/abc"


def test_end_from_duration():
    start = "2026-11-27T03:30:00Z"
    assert parsers.end_from_duration(start, "P0Y0M0DT9H0M0S") == "2026-11-27T12:30:00Z"
    assert parsers.end_from_duration(start, "P0Y0M0DT174H59M0S") == "2026-12-04T10:29:00Z"   # hours past 24
    assert parsers.end_from_duration(start, "P0Y0M2DT1H30M0S") == "2026-11-29T05:00:00Z"
    assert parsers.end_from_duration(start, "PT45M") == "2026-11-27T04:15:00Z"
    for odd in (None, "", "P", "junk", 9, "P0Y0M0DT0H0M0S", "P0Y1M0DT0H0M0S", "P1Y"):
        assert parsers.end_from_duration(start, odd) is None, odd
    assert parsers.end_from_duration(None, "PT1H") is None
    assert parsers.end_from_duration("2026-11-27", "PT1H") is None
    assert parsers.end_from_duration("9999-12-31T23:00:00Z", "PT2H") is None        # past the last date Python holds


def test_user_events():
    rows, cursor = parsers.parse_events_page(load("user_events.json"))
    assert len(rows) == 7 and cursor is None
    party = rows[0]
    assert party["name"] == "ANNUAL MRC HALLOWEEN PARTY"
    assert party["tickets"]["price"] == 30 and party["tickets"]["max_price"] == 45
    assert party["tickets"]["currency"] == "USD" and party["tickets"]["spots_remaining"] == 32


def test_batch_rows_by_id():
    by_id = parsers.parse_batch(load("events_by_ids.json")["entries"])
    assert list(by_id) == ["evt-ir5RsHXH4gDHNbd", "evt-pZzt2IshuxE9DD2"]
    assert by_id["evt-ir5RsHXH4gDHNbd"]["link"].startswith("https://luma.com/")
    assert parsers.parse_batch(None) == {}


# ---- one event -----------------------------------------------------------------------

def test_event_free_with_public_address():
    event = parsers.parse_event(load("event_free.json"))
    assert list(event)[:4] == ["id", "name", "slug", "link"]
    assert event["id"] == "evt-UvVJTTBckZ6kMsc" and event["link"] == "https://luma.com/k5woffsv"
    assert event["description"].startswith("THE MRC HOT TROT IS BACK FOR ROUND TWO.\n\nMRC'S second annual")
    assert "\n- COFFEE CART\n- ON Giveaways" in event["description"]
    assert event["description_links"] == ["https://www.marinarunclub.com/become-a-member",
                                          "https://members.marinarunclub.com/login"]
    assert [c["slug"] for c in event["categories"]] == ["fitness", "running"]
    assert event["guest_count"] == 232 and event["ticket_count"] == 232
    assert event["tickets"]["is_free"] is True
    assert event["ticket_types"][0]["name"] == "Attendee" and event["ticket_types"][0]["guest_count"] == 232
    assert event["registration"] == {"name_requirement": "full-name", "phone_number_requirement": "required",
                                     "eth_address_requirement": None, "solana_address_requirement": None,
                                     "questions": []}
    assert event["location"]["venue"] == "500 Marina Blvd" and event["location"]["neighborhood"] == "Marina District"
    assert event["location"]["google_place_id"] == "ChIJ67eSyyuBhYARCdqfcO9Eds0"
    assert event["featured_city"] == {"id": "discplace-BDj7GNbGlsF7Cka", "slug": "sf", "name": "San Francisco",
                                      "link": "https://luma.com/sf"}
    assert event["calendar"]["website"] == "https://www.marinarunclub.com"
    assert event["organizer_website"] == "https://www.marinarunclub.com"
    assert list(event)[-5:] == ["hosts", "organizer_name", "organizer_website", "featured_guests", "collaborating_calendars"]
    assert event["organizer_name"] == "Marina Run Club Events"
    assert event["calendar"]["social"]["linkedin"] == "https://linkedin.com/company/99214553"
    host = event["hosts"][0]
    assert host["name"] == "Marina Run Club" and host["link"] == "https://luma.com/user/usr-4RfJK79d4BBbtk2"
    assert host["social"]["instagram"] == "https://instagram.com/marinarunclub_sf"
    assert len(event["featured_guests"]) == 10


def test_event_from_the_slug_resolver_is_the_same_record():
    resolved = load("slug_event.json")
    assert resolved["kind"] == "event"
    assert parsers.parse_event(resolved["data"])["id"] == "evt-UvVJTTBckZ6kMsc"


def test_event_paid_tiers():
    event = parsers.parse_event(load("event_paid_tiers.json"))
    assert event["tickets"]["price"] == 29 and event["tickets"]["max_price"] == 49      # cents upstream
    assert event["tickets"]["currency"] == "USD" and event["tickets"]["is_approval_required"] is True
    assert [(t["name"], t["type"], t["price"], t["currency"]) for t in event["ticket_types"]] == [
        ("Standard", "fiat-price", 49, "USD"), ("Nonprofit Community Partner", "free", None, None),
        ("Virtual", "fiat-price", 29, "USD")]
    assert event["guest_count"] == 7 and event["ticket_count"] == 9
    assert event["location"]["is_address_hidden"] is True and event["location"]["city"] == "Bronx"
    question = event["registration"]["questions"][0]
    assert question["type"] == "select" and question["is_multiple_choice"] is True and len(question["options"]) > 3
    assert event["calendar"]["is_personal"] is True
    assert event["calendar"]["owner"]["name"] == "Create Labs"
    assert event["calendar"]["name"] == "Create Labs"                  # Luma names it "Personal"
    assert event["organizer_name"] == "Create Labs"
    assert event["calendar"]["link"] == "https://luma.com/user/usr-6CBfWF8QaRFpEqj"     # a personal calendar has no page
    assert event["calendar"]["website"] is None
    assert event["organizer_website"] == "https://createlabs.io"       # the owner's, for a personal calendar


def test_event_sold_out_with_a_hidden_guest_list():
    event = parsers.parse_event(load("event_sold_out_waitlist.json"))
    assert event["is_sold_out"] is True and event["registration_status"] == "waitlist"
    assert event["is_waitlist_active"] is True and event["is_guest_list_public"] is False
    # the organiser hides the guests: the site sends 0 and still fills the ticket type's own count
    assert event["guest_count"] is None and event["ticket_count"] is None
    ticket = event["ticket_types"][0]
    assert ticket["guest_count"] is None and ticket["tickets_registered_count"] is None
    assert ticket["price"] == 45 and ticket["max_capacity"] == 70
    # the site leaves the summary price empty for a sold-out paid event
    assert event["tickets"]["is_free"] is False
    assert event["tickets"]["price"] == 45 and event["tickets"]["currency"] == "USD"
    assert event["tickets"]["max_price"] is None and event["tickets"]["spots_remaining"] == 1


def test_event_with_many_hosts_and_questions():
    event = parsers.parse_event(load("event_address_guests_only.json"))
    assert len(event["hosts"]) == 7 and len(event["featured_guests"]) == 10
    assert len(event["registration"]["questions"]) == 8
    company = event["registration"]["questions"][1]
    assert company["type"] == "company" and company["job_title_label"] == "What is your job title?"
    assert event["description_links"][0] == "https://worn-hacks.com"
    assert event["tickets"]["spots_remaining"] == 36


def test_event_online_has_no_place():
    event = parsers.parse_event(load("event_online.json"))
    assert event["is_online"] is True and event["location_type"] == "meet"
    location = event["location"]
    assert location["address"] is None and location["city"] is None and location["latitude"] is None
    assert location["is_address_hidden"] is False and location["is_coordinate_approximate"] is False


def test_event_sparse():
    event = parsers.parse_event(load("event_manual_address.json"))
    assert event["hosts"] == [] and event["featured_guests"] == []
    assert event["organizer_website"] == "https://www.patteries.com/"     # no hosts: the calendar's own
    assert parsers.parse_event({})["organizer_website"] is None
    assert event["location"]["venue"] == "XAMK Kotka campus, Fifth Floor" and event["location"]["source"] == "manual"
    assert event["location"]["address"] is None and event["location"]["latitude"] is None
    assert event["guest_count"] is None
    assert [c["slug"] for c in event["categories"]] == ["ai", "climate"]


def test_event_parsers_survive_junk():
    empty = parsers.parse_event({})
    assert empty["id"] is None and empty["ticket_types"] == [] and empty["hosts"] == []
    assert parsers.parse_event(None)["location"]["city"] is None
    assert parsers.parse_event_row({"event": {"api_id": "evt-x", "geo_address_info": "junk"}})["id"] == "evt-x"


EVENT_FIXTURES = ["event_free.json", "event_paid_tiers.json", "event_sold_out_waitlist.json",
                  "event_address_guests_only.json", "event_online.json", "event_manual_address.json"]


@pytest.mark.parametrize("name", EVENT_FIXTURES)
def test_event_leaves_out_what_no_page_shows(name):
    raw = load(name)
    event = parsers.parse_event(raw)
    text = json.dumps(event)
    for hidden in (raw.get("stripe_account_id"), (raw.get("event") or {}).get("user_api_id")):
        if hidden and hidden not in [h.get("api_id") for h in raw.get("hosts") or []]:
            assert hidden not in text
    found = keys_of(event)
    assert not found & {"stripe_account_id", "google_measurement_id", "meta_pixel_id", "tax_config", "user_api_id",
                        "guest_data", "coupon", "plan", "is_hidden"}
    assert "timezone" not in keys_of(event["hosts"]) and "timezone" not in keys_of(event["featured_guests"])
    assert all(key == key.lower() and not key.endswith("_url") for key in found)


def test_unshown_fields_only_when_asked():
    raw = load("event_free.json")
    assert parsers.parse_event(raw, unshown=True)["calendar"]["plan"] == "plus"
    assert parsers.parse_event(raw, unshown=True)["hosts"][0]["timezone"] == "America/Los_Angeles"
    assert "plan" not in parsers.parse_event(raw)["calendar"]
    assert parsers.parse_user(load("user.json"), unshown=True)["timezone"] == "America/Los_Angeles"
    assert "timezone" not in parsers.parse_user(load("user.json"))
    assert parsers.parse_calendar(load("calendar.json"), unshown=True)["plan"] == "plus"
    assert "plan" not in parsers.parse_calendar(load("calendar.json"))


def test_a_hidden_ticket_type_is_left_out():
    raw = load("event_paid_tiers.json")
    raw["ticket_types"][0]["is_hidden"] = True
    assert [t["name"] for t in parsers.parse_event(raw)["ticket_types"]] == ["Nonprofit Community Partner", "Virtual"]


def test_distance_km():
    assert round(parsers.distance_km(37.7749, -122.4194, 40.7128, -74.0060)) == 4129      # San Francisco - New York
    assert parsers.distance_km(10.0, 20.0, 10.0, 20.0) == 0
    assert round(parsers.distance_km(0, 0, 0, 180)) == 20015                             # half the globe


# ---- rich text -----------------------------------------------------------------------

def test_doc_text_and_links():
    doc = {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Agenda"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Doors "}, {"type": "text", "text": "6pm",
                                                                             "marks": [{"type": "bold"}]},
                                          {"type": "hard_break"}, {"type": "text", "text": "Talks 7pm"}]},
        {"type": "paragraph"},
        {"type": "ordered_list", "content": [
            {"type": "list_item", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "One"}]}]},
            {"type": "list_item", "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Two", "marks": [{"type": "link", "attrs": {"href": "https://a.example/x"}}]}]}]}]},
        {"type": "horizontal_rule"},
        {"type": "button", "attrs": {"href": "https://b.example/join", "text": "Become a member"}},
    ]}
    assert parsers.doc_text(doc) == ("Agenda\n\nDoors 6pm\nTalks 7pm\n\n1. One\n2. Two\n\n---\n\n"
                                     "Become a member: https://b.example/join")
    assert parsers.doc_links(doc) == ["https://a.example/x", "https://b.example/join"]
    assert parsers.doc_text({"type": "doc", "content": [{"type": "paragraph"}]}) is None       # an empty refund policy
    assert parsers.doc_text(None) is None and parsers.doc_links(None) == []


# ---- calendars -----------------------------------------------------------------------

def test_calendar():
    calendar = parsers.parse_calendar(load("calendar.json"))
    assert calendar["id"] == "cal-JDvWKGO7IFEV3WY" and calendar["link"] == "https://luma.com/marinarunclub"
    assert calendar["name"] == "Marina Run Club Events"
    assert calendar["follower_count"] == 437 and calendar["upcoming_event_count"] == 8
    assert calendar["location"]["city"] == "San Francisco" and calendar["location"]["latitude"] == 37.7749295
    assert [tag["name"] for tag in calendar["tags"]] == ["Educational", "Happy Hour/Social", "Marina Run Club",
                                                         "Member Event", "Run", "Social"]
    assert calendar["tags"][4] == {"id": "tag-W2KjxJG6bjIUicV", "name": "Run", "color": "blue",
                                   "upcoming_event_count": 0}
    assert len(calendar["event_start_times"]) == 75 and calendar["event_start_times"][0] == "2025-01-11T17:30:00Z"
    assert calendar["cover"]["width"] == 1820
    assert calendar["refund_policy"] is None and calendar["owner"] is None
    # website_contacts lands after the calendar's own contact fields
    assert list(calendar)[-2:] == ["website", "social"]
    assert calendar["website"] == "https://www.marinarunclub.com"
    assert calendar["social"] == {"instagram": "https://instagram.com/marinarunclub_sf", "x": None,
                                  "linkedin": "https://linkedin.com/company/99214553", "tiktok": None, "youtube": None}
    assert not keys_of(calendar) & {"stripe_account_id", "tax_config", "google_measurement_id", "meta_pixel_id"}


def test_a_personal_calendar_answers_its_owner_name():
    raw = load("calendar_personal.json")
    assert raw["calendar"]["name"] == "Personal" and raw["calendar"]["is_personal"] is True
    calendar = parsers.parse_calendar(raw)
    assert calendar["name"] == "Sreepad Krishnan" and calendar["is_personal"] is True
    assert calendar["link"] == "https://luma.com/user/sreepad"
    # in a list row too
    rows, _ = parsers.parse_events_page(load("events_near_point.json"))
    personal = [row["calendar"] for row in rows if row["calendar"] and row["calendar"]["is_personal"]]
    assert personal and all(card["name"] not in (None, "Personal") for card in personal)
    assert all(card["name"] == card["owner"]["name"] for card in personal)


def test_a_personal_calendar_with_a_name_of_its_own_keeps_it():
    owner = {"api_id": "usr-x", "name": "Ada Lovelace"}
    named = parsers.parse_calendar_summary({"api_id": "cal-x", "is_personal": True, "name": "Ada's Salon", "personal_user": owner})
    assert named["name"] == "Ada's Salon"
    plain = parsers.parse_calendar_summary({"api_id": "cal-x", "is_personal": True, "name": "personal", "personal_user": owner})
    assert plain["name"] == "Ada Lovelace"
    lone = parsers.parse_calendar_summary({"api_id": "cal-x", "is_personal": True, "name": "Personal"})
    assert lone["name"] == "Personal"        # no owner to name it after
    company = parsers.parse_calendar_summary({"api_id": "cal-x", "is_personal": False, "name": "Personal", "personal_user": owner})
    assert company["name"] == "Personal"


def test_an_event_from_another_site_names_its_organizer():
    rows, _ = parsers.parse_events_page(load("calendar_events_external_host.json"), parsers.parse_calendar_event_row)
    external, hosted = rows
    assert external["platform"] == "external" and external["name"] == "Gemini 4 NYC Hackathon"
    assert external["organizer_name"] == "Cerebral Valley"            # the calendar's `host`, trimmed
    assert external["calendar"] is None and external["hosts"] == []
    assert external["link"] == "https://cerebralvalley.ai/e/gemini-4-nyc"
    assert hosted["platform"] == "luma" and hosted["organizer_name"] == hosted["calendar"]["name"]
    # an external row whose calendar names no organizer
    rows, _ = parsers.parse_events_page(load("calendar_events_external.json"), parsers.parse_calendar_event_row)
    assert rows[0]["platform"] == "external" and rows[0]["organizer_name"] is None


def test_the_organizer_name_falls_back_to_a_host():
    row = parsers.parse_event_row({"event": {"api_id": "evt-x"}, "hosts": [{"api_id": "usr-x", "name": "Grace Hopper"}]})
    assert row["organizer_name"] == "Grace Hopper"
    assert parsers.parse_event_row({})["organizer_name"] is None


def test_a_calendar_without_a_slug_links_to_its_page():
    calendar = parsers.parse_calendar(load("calendar_no_slug.json"))
    assert calendar["name"] == "The Social House Calendar" and calendar["slug"] is None
    assert calendar["is_personal"] is False
    assert calendar["link"] == "https://luma.com/calendar/cal-grB1VLiXTiTmcF7"
    card = parsers.parse_calendar_card(load("calendar_no_slug.json")["calendar"])
    assert card["link"] == "https://luma.com/calendar/cal-grB1VLiXTiTmcF7"
    # a personal calendar keeps its owner's profile, where the site sends it; without an owner, its own page
    assert parsers.parse_calendar_summary({"api_id": "cal-x", "is_personal": True})["link"] == "https://luma.com/calendar/cal-x"


def test_calendar_from_the_slug_resolver_is_the_same_record():
    resolved = load("slug_calendar.json")
    assert resolved["kind"] == "calendar"
    assert parsers.parse_calendar(resolved["data"]) == parsers.parse_calendar(load("calendar.json"))


def test_calendar_cards_of_a_city():
    cards = parsers.parse_calendar_cards(load("calendars_city.json")["calendars"])
    assert len(cards) == 7
    first = cards[0]
    assert first["name"] == "J.P. Morgan Healthcare Conference"
    assert first["link"] == "https://luma.com/jpm-healthcare-conference"
    assert first["start_date"] == "2027-01-11" and first["end_date"] == "2027-01-14"
    assert first["website"].startswith("https://www.jpmorgan.com/")
    assert first["follower_count"] is None          # the city list sends the bare calendar, without counts
    assert first["access_level"] == "public" and first["launch_status"] == "launched"
    assert any(card["refund_policy"] for card in cards)
    assert list(first)[-2:] == ["website", "social"]


def test_calendar_cards_of_a_category_and_of_discover():
    page = load("calendars_category.json")
    cards = parsers.parse_calendar_cards(page["featured_calendars"] + page["timeline_calendars"])
    assert len(cards) == 10 and len({card["id"] for card in cards}) == 10
    assert cards[0]["name"] == "OpenClaw Meetups"
    assert cards[0]["follower_count"] == 22870 and cards[0]["upcoming_event_count"] == 7
    assert parsers.parse_calendar_cards(page["featured_calendars"] * 2) == cards[:5]      # repeats dropped
    landing = load("calendars_featured.json")
    cards = parsers.parse_calendar_cards(landing["major_calendars"] + landing["global_calendars"])
    assert len(cards) == 18
    assert cards[0]["start_date"] == "2026-11-28" and cards[0]["end_date"] == "2026-11-29"
    assert parsers.parse_calendar_cards(None) == [] and parsers.parse_calendar_card({}) is None


# ---- users ---------------------------------------------------------------------------

def test_user_with_a_username():
    user = parsers.parse_user(load("user.json"))
    assert user["id"] == "usr-iSyVp7KRDGTBctH" and user["username"] == "ajaytuniki"
    assert user["link"] == "https://luma.com/user/ajaytuniki"
    assert user["bio"].startswith("Building Compute Infrastructure") and "\n" in user["bio"]
    assert user["joined_at"] == "2025-04-19T08:15:36Z"
    assert user["events_hosted_count"] == 0 and user["events_attended_count"] == 105
    assert user["website"] is None
    assert user["social"] == {"instagram": "https://instagram.com/ajaytuniki", "x": "https://x.com/ajaytuniki",
                              "linkedin": "https://linkedin.com/in/ajaykumartuniki", "tiktok": None, "youtube": None}
    assert list(user)[-2:] == ["website", "social"]


def test_user_without_a_username():
    user = parsers.parse_user(load("user_host.json"))
    assert user["username"] is None and user["link"] == "https://luma.com/user/usr-4RfJK79d4BBbtk2"
    assert user["bio"] is None          # the site sends ""
    assert user["events_hosted_count"] == 10
    assert parsers.parse_user({})["id"] is None and parsers.parse_person({}) is None


def test_social_links():
    assert refs.social_links({"youtube_handle": "https://www.youtube.com/channel/UC6C", "tiktok_handle": "@abc",
                              "linkedin_handle": "in/someone", "twitter_handle": " "}) == {
        "instagram": None, "x": None, "linkedin": "https://linkedin.com/in/someone",
        "tiktok": "https://tiktok.com/@abc", "youtube": "https://www.youtube.com/channel/UC6C"}
    assert set(refs.social_links(None).values()) == {None}


# ---- cities and categories -----------------------------------------------------------

def test_cities():
    cities = parsers.parse_cities(load("cities.json"))
    assert len(cities) == 87 and len({city["slug"] for city in cities}) == 87
    assert cities[0]["name"] == "Cape Town" and cities[0]["continent"] == "Africa"       # by continent, then name
    sf = next(city for city in cities if city["slug"] == "sf")
    assert sf["id"] == "discplace-BDj7GNbGlsF7Cka" and sf["link"] == "https://luma.com/sf"
    assert (sf["latitude"], sf["longitude"]) == (37.7749, -122.4194)
    assert sf["timezone"] == "America/Los_Angeles" and sf["continent_code"] == "na"
    assert isinstance(sf["event_count"], int)
    # the list measures each city from the caller: that stays out
    assert not keys_of(cities) & {"distance_km", "client_ip", "is_subscriber"}
    assert parsers.parse_cities({}) == []


def test_city():
    city = parsers.parse_city(load("city.json"))
    assert city["slug"] == "sf" and city["name"] == "San Francisco" and city["event_count"] == 34
    assert city["latitude"] == 37.7749 and city["languages"] == ["en"]
    assert all(ref.startswith("evt-") for ref in city["featured_event_ids"])
    assert parsers.parse_city({}) is None


def test_categories():
    categories = parsers.parse_categories(load("categories.json"))
    assert [c["slug"] for c in categories] == ["family", "books", "games", "tech", "food", "ai", "running", "arts",
                                               "climate", "fitness", "wellness", "crypto"]
    family = categories[0]
    assert family["id"] == "cat-family" and family["link"] == "https://luma.com/family"
    assert family["upcoming_event_count"] == 586 and family["subscriber_count"] == 2694
    assert family["created_at"] == "2026-08-17T20:15:06Z"
    page = parsers.parse_category(load("calendars_category.json"))
    assert page["slug"] == "ai" and page["upcoming_event_count"] == 3226 and page["subscriber_count"] == 115562
    assert parsers.parse_categories(None) == [] and parsers.parse_category({}) is None


# ---- the transport's reading of a refusal --------------------------------------------

class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return json.loads(self.text)


def test_not_found_body_is_a_404():
    with pytest.raises(fetch.LumaNotFound, match="event evt-nope not found"):
        fetch._classify(FakeResponse(404, load("not_found.json")), "event evt-nope")


def test_refusals():
    with pytest.raises(fetch.LumaBadRequest, match="Invalid request."):
        fetch._classify(FakeResponse(400, {"message": "Invalid request.", "code": None}), "events")
    with pytest.raises(fetch.LumaBlocked, match="You are not signed in"):
        fetch._classify(FakeResponse(401, {"message": "You are not signed in.", "code": None}), "search")
    with pytest.raises(fetch.LumaBlocked):
        fetch._classify(FakeResponse(429, "slow down"), "events")
    with pytest.raises(fetch.LumaBlocked, match="challenge"):
        fetch._classify(FakeResponse(403, "<title>Just a moment...</title>", {"cf-mitigated": "challenge"}), "events")
    with pytest.raises(fetch.LumaUpstreamError):
        fetch._classify(FakeResponse(502, "bad gateway"), "events")
    assert fetch._classify(FakeResponse(200, {"entries": []}), "events") is None
    assert not issubclass(fetch.LumaNotFound, fetch.LumaUpstreamError)      # a 404 is never retried


def test_headers_are_the_page_s_own():
    headers = fetch.api_headers("/sf")
    assert headers["x-luma-client-type"] == "luma-web" and headers["x-luma-web-url"] == "https://luma.com/sf"
    assert headers["origin"] == "https://luma.com" and headers["referer"] == "https://luma.com/"
    # the session sets these itself
    assert not {"user-agent", "accept-language", "accept-encoding", "cookie"} & set(headers)
