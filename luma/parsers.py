"""api.luma.com JSON -> clean dicts for every /luma/* endpoint. Pure
functions, no I/O; every parser tolerates a missing or null field.

Fixtures (luma/fixtures/, raw answers captured 2026-11-21; the ones over
100 KB are kept packed as .json.gz):
  events_near_point.json.gz    /discover/get-paginated-events, 48 rows near San Francisco, more pages
  events_one_row.json          the same call with pagination_limit=1 (the smallest page with a cursor)
  events_keyword.json.gz       the same call with query=hackathon: 20 rows, no next page
  events_empty.json            a keyword with no result: {"entries": [], "has_more": false}
  event_free.json              /event/get: a free in-person event, public address and guest list
  event_paid_tiers.json        three ticket types (two paid, one free), address for guests only
  event_sold_out_waitlist.json sold out, waitlist on, guest list hidden
  event_address_guests_only.json  seven hosts, eight registration questions, address for guests only
  event_online.json            location_type "meet": no address, no join link for a visitor
  event_manual_address.json    the sparse one: a typed-in address, no hosts, guest list hidden
  events_by_ids.json           /event/get-by-ids: two rows
  slug_event.json              /url for an event slug: {kind: "event", data}
  slug_calendar.json.gz        /url for a calendar slug: {kind: "calendar", data}
  calendar.json.gz             /calendar/get: a club's calendar with six tags
  calendar_events_past.json    /calendar/get-items: six past rows, each with its tags
  calendars_city.json          /discover/get-calendars: the calendars a city's page features
  calendars_category.json      /discover/category/get-page: a category with its calendars
  calendars_featured.json      /discover/get-landing-calendars
  user.json, user_host.json    /user/profile: a guest with a username, a host without one
  user_events.json             /user/profile/events-hosting: seven past rows, last page
  cities.json.gz               /discover/bootstrap-page (87 cities), fetched through a datacenter
                               exit on 2026-11-23: the answer prints the caller's address
  city.json                    /discover/get-place
  categories.json              /discover/category/list-categories (12 categories)
  not_found.json               the site's 404 body
Captured 2026-11-23:
  calendar_events_external.json  /calendar/get-items: an event hosted outside Luma, then one hosted on it
  calendar_no_slug.json        /calendar/get: a calendar that has no slug
Captured 2026-11-24:
  calendar_personal.json       /calendar/get: a personal calendar (Luma names it "Personal")
  calendar_events_external_host.json  /calendar/get-items: an event hosted outside Luma whose
                               calendar names its organizer (`event.host`), then one hosted on it

What stays out, by the owner's rule (2026-11-21): values the site's data
carries and its pages do not show (the Stripe account id, the analytics and
pixel ids, the tax settings, the id of the account that created an event),
and the viewer's own session state (guest_data, coupon, is_subscriber...).
When an organiser hides the guest list the site sends the counts as 0: they
are answered null, and so are the per-ticket counts. A ticket type the
organiser marked hidden is left out.

Two values the data carries and no page prints are returned only when the
caller passes unshown=True (config.LUMA_SHOW_UNSHOWN_FIELDS): a person's
clock `timezone` and a calendar's `plan` (free / plus).
"""
import re
from datetime import datetime, timedelta

from luma import refs
from scraper_utils.dates import iso_datetime, iso_day
from scraper_utils.geo import distance_km  # noqa: F401  (the keyword search's reach, luma/api.py)
from scraper_utils.text import clean, multiline
from scraper_utils.values import amount, as_dict, as_list, bool_or_none, g, num

ONLINE_TYPES = {"offline": False, "missing": None}      # location_type -> is_online; every other value is a meeting tool
_DURATION_RE = re.compile(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")
_UTC_TIME = "%Y-%m-%dT%H:%M:%SZ"


# ---- small helpers -------------------------------------------------------------------

def _money(cents):
    """Cents -> an amount in whole units (2900 -> 29, 2950 -> 29.5); None stays None."""
    value = num(cents)
    return None if value is None else amount(value / 100)


def _whole(value):
    """A count the site sends as an int; anything else (null, a bool, text) -> None."""
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else None


def _currency(value):
    return value.upper() if isinstance(value, str) and value.strip() else None


def end_from_duration(start_at, duration):
    """The end of an event from its start ('2026-11-27T03:30:00Z') and the
    ISO 8601 duration the site keeps for an event hosted elsewhere
    ('P0Y0M0DT9H0M0S'; the hours may pass 24). None when either cannot be
    read, for a zero duration, and for one in months or years, whose length
    depends on the calendar (all 126 rows read on 2026-11-23 were in hours)."""
    match = _DURATION_RE.fullmatch(duration) if isinstance(duration, str) else None
    if not match:
        return None
    years, months, days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    if years or months or not (days or hours or minutes or seconds):
        return None
    try:
        end = datetime.strptime(start_at, _UTC_TIME) + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    except (TypeError, ValueError, OverflowError):
        return None
    return end.strftime(_UTC_TIME)


def _coordinate(raw):
    raw = as_dict(raw)
    return num(raw.get("latitude")), num(raw.get("longitude"))


# ---- rich text -----------------------------------------------------------------------

_BLOCKS = {"paragraph", "heading", "bullet_list", "ordered_list", "list_item", "blockquote", "code_block",
           "horizontal_rule", "button", "image"}


def _inline(node):
    """The text of a node's inline content (text runs, line breaks, anything nested)."""
    out = []
    for child in as_list(as_dict(node).get("content")):
        child = as_dict(child)
        kind = child.get("type")
        if kind == "text":
            out.append(child.get("text") or "")
        elif kind == "hard_break":
            out.append("\n")
        elif kind not in _BLOCKS:
            out.append(_inline(child))
    return "".join(out)


def _block_lines(node, lines, prefix=""):
    """Append the lines of one block node to `lines`."""
    node = as_dict(node)
    kind = node.get("type")
    if kind in ("bullet_list", "ordered_list"):
        for index, item in enumerate(as_list(node.get("content")), 1):
            mark = "- " if kind == "bullet_list" else f"{index}. "
            inner = []
            for child in as_list(as_dict(item).get("content")):
                _block_lines(child, inner, prefix + "  ")
            first = inner[0].strip() if inner else ""
            lines.append(f"{prefix}{mark}{first}")
            lines.extend(inner[1:])
    elif kind == "button":
        attrs = as_dict(node.get("attrs"))
        label, href = clean(attrs.get("text")), clean(attrs.get("href"))
        if label or href:
            lines.append(prefix + (f"{label}: {href}" if label and href else (label or href)))
    elif kind == "horizontal_rule":
        lines.append(prefix + "---")
    elif kind in ("paragraph", "heading", "code_block"):
        lines.append(prefix + _inline(node))
    else:
        nested = [c for c in as_list(node.get("content")) if as_dict(c).get("type") in _BLOCKS]
        if nested:
            for child in nested:
                _block_lines(child, lines, prefix)
        else:
            text = _inline(node)
            if text:
                lines.append(prefix + text)


def doc_text(doc):
    """The site's rich-text tree ({"type": "doc", "content": [...]}) -> plain
    text: one block per line, list items with a dash or a number, a blank
    line between paragraphs. None for an empty or missing tree."""
    blocks = []
    for node in as_list(as_dict(doc).get("content")):
        lines = []
        _block_lines(node, lines)
        blocks.append("\n".join(line.rstrip() for line in lines))
    return multiline("\n\n".join(block for block in blocks if block.strip()))


def doc_links(doc):
    """Every link a rich-text tree carries (link marks and buttons), in order, repeats dropped."""
    found = []

    def walk(node):
        node = as_dict(node)
        for mark in as_list(node.get("marks")):
            href = clean(g(mark, "attrs", "href", default=None)) if as_dict(mark).get("type") == "link" else None
            if href and href not in found:
                found.append(href)
        if node.get("type") == "button":
            href = clean(g(node, "attrs", "href", default=None))
            if href and href not in found:
                found.append(href)
        for child in as_list(node.get("content")):
            walk(child)

    walk(doc)
    return found


# ---- people --------------------------------------------------------------------------

def parse_person(raw, unshown=False):
    """A host, a featured guest or a profile's user -> one shape. None for a missing record."""
    raw = as_dict(raw)
    if not raw.get("api_id") and not raw.get("name"):
        return None
    username = clean(raw.get("username"))
    return {
        "id": raw.get("api_id"),
        "name": clean(raw.get("name")),
        "first_name": clean(raw.get("first_name")),
        "last_name": clean(raw.get("last_name")),
        "username": username,
        "link": refs.user_link(username, raw.get("api_id")),
        "bio": multiline(raw.get("bio_short")),
        "avatar": clean(raw.get("avatar_url")),
        "is_verified": bool_or_none(raw.get("is_verified")),
        **({"timezone": clean(raw.get("timezone"))} if unshown else {}),
        "website": clean(raw.get("website")),
        "social": refs.social_links(raw),
    }


def parse_user(payload, unshown=False):
    """/user/profile -> the profile. `website` and `social` sit together so
    website contacts land right after them."""
    payload = as_dict(payload)
    person = parse_person(payload.get("user"), unshown) or {}
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "username": person.get("username"),
        "link": person.get("link"),
        "bio": person.get("bio"),
        "avatar": person.get("avatar"),
        "is_verified": person.get("is_verified"),
        **({"timezone": person.get("timezone")} if unshown else {}),
        "joined_at": iso_datetime(payload.get("joined_at")),
        "events_hosted_count": _whole(payload.get("event_hosted_count")),
        "events_attended_count": _whole(payload.get("event_attended_count")),
        "website": person.get("website"),
        "social": person.get("social") or refs.social_links({}),
    }


# ---- calendars -----------------------------------------------------------------------

def _calendar_name(raw, owner):
    """A calendar's name. Luma names every personal calendar "Personal"
    (176 of 176 seen, 2026-11-24): it answers its owner's name instead. A
    personal calendar with a name of its own keeps it."""
    name = clean(raw.get("name"))
    if raw.get("is_personal") is True and (owner or {}).get("name") and (name or "").casefold() in ("", "personal"):
        return owner["name"]
    return name


def parse_calendar_summary(raw, unshown=False):
    """The calendar object the site attaches to an event and to its lists
    (the organiser's page) -> one shape. None for a missing record."""
    raw = as_dict(raw)
    if not raw.get("api_id"):
        return None
    slug = clean(raw.get("slug"))
    owner = parse_person(raw.get("personal_user"), unshown)
    place = as_dict(raw.get("location"))
    latitude, longitude = _coordinate(raw.get("coordinate"))
    return {
        "id": raw.get("api_id"),
        "name": _calendar_name(raw, owner),
        "slug": slug,
        # a personal calendar has no page of its own: the site sends its /calendar/<id> to the owner's
        # profile, so that is its link; any other calendar without a slug lives at /calendar/<id>
        "link": refs.page_link(slug) or (owner or {}).get("link") or refs.calendar_page_link(raw.get("api_id")),
        "description": multiline(raw.get("description_short")),
        "avatar": clean(raw.get("avatar_url")),
        "cover_image": clean(raw.get("cover_image_url")),
        "social_image": clean(raw.get("social_image_url")),
        "is_verified": bool_or_none(raw.get("is_verified")),
        "is_personal": bool_or_none(raw.get("is_personal")),
        **({"plan": clean(raw.get("luma_plan"))} if unshown else {}),
        "access_level": clean(raw.get("access_level")),
        "event_submission": clean(raw.get("event_submission_restriction")),
        "launch_status": clean(raw.get("launch_status")),
        "start_date": iso_day(raw.get("start_date")),
        "end_date": iso_day(raw.get("end_date")),
        "timezone": clean(raw.get("timezone")) or clean(place.get("timezone")),
        "location": {
            "city": clean(place.get("city")),
            "region": clean(place.get("region")),
            "country": clean(place.get("country")),
            "country_code": clean(place.get("country_code")),
            "place_id": clean(place.get("place_id")),
            "latitude": latitude,
            "longitude": longitude,
        },
        "refund_policy": doc_text(raw.get("refund_policy")),
        "owner": owner,
        "website": clean(raw.get("website")),
        "social": refs.social_links(raw),
    }


def _tag(raw):
    raw = as_dict(raw)
    return {
        "id": raw.get("api_id"),
        "name": clean(raw.get("name")),
        "color": clean(raw.get("color")),
        "upcoming_event_count": _whole(raw.get("upcoming_event_count")),
    }


def _image(raw):
    """{cdn_url, width, height} -> {link, width, height}; None for a missing image."""
    raw = as_dict(raw)
    link = clean(raw.get("cdn_url"))
    if not link:
        return None
    return {"link": link, "width": _whole(raw.get("width")), "height": _whole(raw.get("height"))}


def parse_calendar(payload, unshown=False):
    """/calendar/get (or the data of /url for a calendar slug) -> the calendar
    with its counts and tags. Its events are served by calendars/events."""
    payload = as_dict(payload)
    summary = parse_calendar_summary(payload.get("calendar"), unshown) or {}
    head = {key: summary.get(key) for key in ("id", "name", "slug", "link", "description", "avatar", "cover_image",
                                              "social_image", "is_verified", "is_personal", "plan",
                                              "access_level", "event_submission", "launch_status", "start_date",
                                              "end_date", "timezone") if key != "plan" or unshown}
    return {
        **head,
        "follower_count": _whole(payload.get("follower_count")),
        "upcoming_event_count": _whole(payload.get("upcoming_event_count")),
        "has_upcoming_events": bool_or_none(payload.get("has_upcoming_events")),
        "can_subscribe": bool_or_none(payload.get("can_subscribe")),
        "location": summary.get("location"),
        "tags": [_tag(tag) for tag in as_list(payload.get("tags"))],
        "categories": [_category_ref(c) for c in as_list(payload.get("categories")) if isinstance(c, dict)],
        "membership_tiers": [_membership_tier(t) for t in as_list(payload.get("membership_tiers")) if isinstance(t, dict)],
        "event_start_times": [t for t in (iso_datetime(v) for v in as_list(payload.get("event_start_ats"))) if t],
        "cover": _image(payload.get("cover_image")),
        "refund_policy": summary.get("refund_policy"),
        "owner": summary.get("owner"),
        "website": summary.get("website"),
        "social": summary.get("social") or refs.social_links({}),
    }


def _membership_tier(raw):
    """A calendar's membership tier, as far as its keys are known: named values only."""
    return {
        "id": raw.get("api_id"),
        "name": clean(raw.get("name")),
        "description": multiline(raw.get("description")),
    }


def parse_calendar_card(raw, unshown=False):
    """One row of a list of calendars. The city list sends the bare calendar
    object; the category and Discover lists wrap it with its counts."""
    raw = as_dict(raw)
    wrapped = isinstance(raw.get("calendar"), dict)
    summary = parse_calendar_summary(raw["calendar"] if wrapped else raw, unshown)
    if summary is None:
        return None
    counts = raw if wrapped else {}
    card = {}
    for key, value in summary.items():
        card[key] = value
        if key == "is_personal":        # the counts sit with the calendar's own facts, before its dates and place
            card["follower_count"] = _whole(counts.get("follower_count"))
            card["upcoming_event_count"] = _whole(counts.get("upcoming_event_count"))
    card["start_date"] = iso_day(counts.get("start_date")) or summary["start_date"]
    card["end_date"] = iso_day(counts.get("end_date")) or summary["end_date"]
    return card


def parse_calendar_cards(rows, unshown=False):
    """A list of calendar rows -> cards, repeats dropped (a category's two lists overlap)."""
    cards, seen = [], set()
    for raw in as_list(rows):
        card = parse_calendar_card(raw, unshown)
        if card and card["id"] not in seen:
            seen.add(card["id"])
            cards.append(card)
    return cards


# ---- events --------------------------------------------------------------------------

def parse_location(event):
    """An event's place. An event whose address is for guests only carries
    the city and an approximate coordinate; an online event carries nothing."""
    event = as_dict(event)
    geo = as_dict(event.get("geo_address_info"))
    latitude, longitude = _coordinate(event.get("coordinate"))
    hidden = event.get("geo_address_visibility") == "guests-only" or geo.get("mode") == "obfuscated"
    return {
        "venue": clean(geo.get("address")),
        "address": clean(geo.get("full_address")),
        "short_address": clean(geo.get("short_address")),
        "neighborhood": clean(geo.get("sublocality")),
        "city": clean(geo.get("city")),
        "city_state": clean(geo.get("city_state")),
        "region": clean(geo.get("region")),
        "region_code": clean(geo.get("region_short")),
        "country": clean(geo.get("country")),
        "country_code": clean(geo.get("country_code")),
        "directions": multiline(geo.get("description")),
        "latitude": latitude,
        "longitude": longitude,
        "is_address_hidden": hidden,
        "is_coordinate_approximate": hidden and latitude is not None,
        "google_place_id": clean(geo.get("place_id")),
        "apple_place_id": clean(geo.get("apple_maps_place_id")),
        "source": clean(geo.get("type")),
    }


def _is_online(location_type):
    if not isinstance(location_type, str) or not location_type:
        return None
    return ONLINE_TYPES.get(location_type, True)


def parse_tickets(entry):
    """The ticket summary of a row: the cheapest and the dearest price, what is left."""
    info = as_dict(as_dict(entry).get("ticket_info"))
    price, top = as_dict(info.get("price")), as_dict(info.get("max_price"))
    return {
        "is_free": bool_or_none(info.get("is_free")),
        "price": _money(price.get("cents")),
        "max_price": _money(top.get("cents")),
        "currency": _currency(price.get("currency") or top.get("currency")),
        "is_price_flexible": bool_or_none(price.get("is_flexible")),
        "is_sold_out": bool_or_none(info.get("is_sold_out")),
        "is_near_capacity": bool_or_none(info.get("is_near_capacity")),
        "is_approval_required": bool_or_none(info.get("require_approval")),
        "spots_remaining": _whole(info.get("spots_remaining")),
    }


def _shown_count(value, hidden):
    """A guest or ticket count: null when the organiser hides the guest list (the site sends 0 then)."""
    return None if hidden else _whole(value)


def _organizer_name(calendar, hosts):
    """The name of whoever runs the event: the calendar's (its owner's for a
    personal calendar), else the first host's. An event a calendar lists from
    another site has neither: parse_calendar_event_row names it."""
    candidates = [(calendar or {}).get("name")] + [host.get("name") for host in hosts]
    return next((name for name in candidates if name), None)


def _organizer_website(calendar, hosts):
    """The website of whoever runs the event: the calendar's, else its
    owner's (a personal calendar), else the first host's that has one."""
    calendar = calendar or {}
    candidates = [calendar.get("website"), (calendar.get("owner") or {}).get("website")]
    candidates += [host.get("website") for host in hosts]
    return next((site for site in candidates if site), None)


def parse_event_row(entry, unshown=False):
    """One row of any event list (and the head of an event's own record)."""
    entry = as_dict(entry)
    event = as_dict(entry.get("event"))
    slug = clean(event.get("url"))
    guests_hidden = event.get("show_guest_list") is False
    city = as_dict(entry.get("featured_city"))
    calendar = parse_calendar_summary(entry.get("calendar"), unshown)
    hosts = [p for p in (parse_person(h, unshown) for h in as_list(entry.get("hosts"))) if p]
    return {
        "id": event.get("api_id") or entry.get("api_id"),
        "name": clean(event.get("name")),
        "slug": slug,
        "link": refs.page_link(slug),
        "start_at": iso_datetime(event.get("start_at") or entry.get("start_at")),
        "end_at": iso_datetime(event.get("end_at")),
        "timezone": clean(event.get("timezone")),
        "location_type": clean(event.get("location_type")),
        "is_online": _is_online(event.get("location_type")),
        "event_type": clean(event.get("event_type")),
        "visibility": clean(event.get("visibility")),
        "cover_image": clean(event.get("cover_url")),
        "social_image": clean(event.get("social_image_url")),
        "registration_status": clean(entry.get("registration_availability")),
        "guest_count": _shown_count(entry.get("guest_count"), guests_hidden),
        "ticket_count": _shown_count(entry.get("ticket_count"), guests_hidden),
        "is_guest_list_public": bool_or_none(event.get("show_guest_list")),
        "is_rsvp_hidden": bool_or_none(event.get("hide_rsvp")),
        "is_waitlist_active": bool_or_none(entry.get("waitlist_active")),
        "waitlist_status": clean(event.get("waitlist_status")),
        "is_recurring": event.get("recurrence_id") is not None,
        "tickets": parse_tickets(entry),
        "location": parse_location(event),
        "featured_city": {"id": city.get("api_id"), "slug": clean(city.get("slug")), "name": clean(city.get("name")),
                          "link": refs.page_link(clean(city.get("slug")))} if city else None,
        "calendar": calendar,
        "hosts": hosts,
        "organizer_name": _organizer_name(calendar, hosts),
        "organizer_website": _organizer_website(calendar, hosts),
        "featured_guests": [p for p in (parse_person(h, unshown) for h in as_list(entry.get("featured_guests"))) if p],
    }


def parse_calendar_event_row(entry, unshown=False):
    """A row of a calendar's list: the event row plus the calendar's own tags
    on it. An event hosted outside Luma (`platform` "external") has no slug
    and no page here: its `url` is its page on the other site, its end is
    its start plus `duration_interval`, and its organizer is the name the
    calendar typed in `host` (74 of 126 such rows carried one, 2026-11-23)."""
    entry = as_dict(entry)
    row = parse_event_row(entry, unshown)
    if entry.get("platform") not in (None, "luma"):
        event = as_dict(entry.get("event"))
        url = clean(event.get("url"))
        row["slug"] = None
        row["link"] = url if url and url.startswith(("http://", "https://")) else None
        row["end_at"] = row["end_at"] or end_from_duration(row["start_at"], event.get("duration_interval"))
        row["organizer_name"] = row["organizer_name"] or clean(event.get("host"))
    row["platform"] = clean(entry.get("platform"))
    row["is_managed_by_calendar"] = bool_or_none(entry.get("is_manager"))     # false: an event of another calendar, listed here
    row["tags"] = [_tag_ref(tag) for tag in as_list(entry.get("tags")) if isinstance(tag, dict)]
    return row


def _tag_ref(raw):
    return {"id": raw.get("api_id"), "name": clean(raw.get("name")), "color": clean(raw.get("color"))}


def parse_events_page(payload, row=parse_event_row, unshown=False):
    """A cursor page -> (rows, the cursor that opens the next page or None)."""
    payload = as_dict(payload)
    rows = [row(entry, unshown) for entry in as_list(payload.get("entries")) if isinstance(entry, dict)]
    return rows, next_cursor(payload)


def _category_ref(raw):
    slug = clean(raw.get("slug"))
    return {"id": raw.get("api_id"), "slug": slug, "name": clean(raw.get("name")), "link": refs.page_link(slug)}


def _ticket_type(raw, guests_hidden):
    raw = as_dict(raw)
    return {
        "id": raw.get("api_id"),
        "name": clean(raw.get("name")),
        "type": clean(raw.get("type")),
        "description": multiline(raw.get("description")),
        "price": _money(raw.get("cents")),
        "min_price": _money(raw.get("min_cents")),
        "currency": _currency(raw.get("currency")),
        "is_price_flexible": bool_or_none(raw.get("is_flexible")),
        "is_disabled": bool_or_none(raw.get("is_disabled")),
        "is_approval_required": bool_or_none(raw.get("require_approval")),
        "max_capacity": _whole(raw.get("max_capacity")),
        "max_per_guest": _whole(raw.get("max_per_guest")),
        "spots_remaining": _whole(raw.get("spots_remaining")),
        "guest_count": _shown_count(raw.get("num_guests"), guests_hidden),
        "tickets_registered_count": _shown_count(raw.get("num_tickets_registered"), guests_hidden),
        "sales_start_at": iso_datetime(raw.get("valid_start_at")),
        "sales_end_at": iso_datetime(raw.get("valid_end_at")),
        "start_at": iso_datetime(raw.get("start_at")),
        "end_at": iso_datetime(raw.get("end_at")),
        "timezone": clean(raw.get("timezone")),
        "membership_restriction": clean(raw.get("membership_restriction")) if isinstance(raw.get("membership_restriction"), str) else None,
        "token_requirements": [r for r in as_list(raw.get("ethereum_token_requirements")) if r],
    }


def _question(raw):
    raw = as_dict(raw)
    return {
        "id": raw.get("id"),
        "label": clean(raw.get("label")),
        "type": clean(raw.get("question_type")),
        "is_required": bool_or_none(raw.get("required")),
        "is_multiline": bool(raw.get("multiline")),
        "is_multiple_choice": bool(raw.get("multiple")),
        "options": [o for o in (clean(v) for v in as_list(raw.get("options"))) if o],
        "job_title_label": clean(raw.get("job_title_label")),
    }


def _with_type_prices(tickets, ticket_types):
    """The ticket summary, its price range read from the ticket types when
    the site leaves it empty (it does for a sold-out paid event)."""
    priced = [t for t in ticket_types if t["price"] is not None and t["currency"]]
    if tickets["price"] is not None or tickets["is_free"] is not False or not priced:
        return tickets
    if len({t["currency"] for t in priced}) != 1:
        return tickets
    low, high = min(t["price"] for t in priced), max(t["price"] for t in priced)
    return {**tickets, "price": low, "max_price": high if high != low else None, "currency": priced[0]["currency"]}


def parse_event(payload, unshown=False):
    """/event/get -> the event in full: the list row, then its description,
    ticket types, registration form, categories and the organiser's calendar."""
    payload = as_dict(payload)
    row = parse_event_row(payload, unshown)
    guests_hidden = as_dict(payload.get("event")).get("show_guest_list") is False
    description = payload.get("description_mirror")
    head = {key: row[key] for key in ("id", "name", "slug", "link", "start_at", "end_at", "timezone", "location_type",
                                      "is_online", "event_type", "visibility", "cover_image", "social_image")}
    tail = {key: row[key] for key in ("location", "featured_city", "calendar", "hosts", "organizer_name", "organizer_website",
                                      "featured_guests")}
    ticket_types = [_ticket_type(t, guests_hidden) for t in as_list(payload.get("ticket_types"))
                    if isinstance(t, dict) and not t.get("is_hidden")]
    return {
        **head,
        "description": doc_text(description),
        "description_links": doc_links(description),
        "language": clean(payload.get("locale")),
        "categories": [_category_ref(c) for c in as_list(payload.get("categories")) if isinstance(c, dict)],
        "registration_status": row["registration_status"],
        "guest_count": row["guest_count"],
        "ticket_count": row["ticket_count"],
        "is_guest_list_public": row["is_guest_list_public"],
        "is_rsvp_hidden": row["is_rsvp_hidden"],
        "is_waitlist_active": row["is_waitlist_active"],
        "waitlist_status": row["waitlist_status"],
        "is_recurring": row["is_recurring"],
        "is_sold_out": bool_or_none(payload.get("sold_out")),
        "has_available_ticket_types": bool_or_none(payload.get("has_available_ticket_types")),
        "has_multiple_ticket_types": bool_or_none(payload.get("has_multiple_ticket_types")),
        "can_register_for_multiple_tickets": bool_or_none(payload.get("can_register_for_multiple_tickets")),
        "is_ticket_transfer_enabled": bool_or_none(payload.get("ticket_transfer_enabled")),
        "can_contact_host_via_chat": bool_or_none(payload.get("contact_host_via_chat")),
        "accepts_usdc_for_usd": bool_or_none(payload.get("accepts_usdc_for_usd")),
        "tickets": _with_type_prices(row["tickets"], ticket_types),
        "ticket_types": ticket_types,
        "registration": {
            "name_requirement": clean(payload.get("name_requirement")),
            "phone_number_requirement": clean(payload.get("phone_number_requirement")),
            "eth_address_requirement": clean(payload.get("eth_address_requirement")) if isinstance(payload.get("eth_address_requirement"), str) else None,
            "solana_address_requirement": clean(payload.get("solana_address_requirement")) if isinstance(payload.get("solana_address_requirement"), str) else None,
            "questions": [_question(q) for q in as_list(payload.get("registration_questions")) if isinstance(q, dict)],
        },
        "refund_policy": doc_text(payload.get("refund_policy")),
        **tail,
        "collaborating_calendars": [c for c in (parse_calendar_summary(as_dict(raw).get("calendar") or raw, unshown)
                                                for raw in as_list(payload.get("collaborating_calendars"))) if c],
    }


def parse_batch(rows, unshown=False):
    """/event/get-by-ids rows -> {event id: list row}. The site answers in
    its own order and leaves unknown ids out."""
    by_id = {}
    for raw in as_list(rows):
        row = parse_event_row(raw, unshown)
        if row["id"]:
            by_id[row["id"]] = row
    return by_id


# ---- cities and categories -----------------------------------------------------------

def parse_city(raw):
    """A Discover city. The list wraps the place with its event count; a
    single city answers {"place": {...}}. The distance the list carries is
    from the caller's own address and is left out."""
    raw = as_dict(raw)
    place = as_dict(raw.get("place")) or raw
    if not place.get("api_id"):
        return None
    slug = clean(place.get("slug"))
    latitude, longitude = _coordinate(place.get("coordinate"))
    return {
        "id": place.get("api_id"),
        "name": clean(place.get("name")),
        "slug": slug,
        "link": refs.page_link(slug),
        "description": multiline(place.get("description")),
        "event_count": _whole(raw.get("event_count")) if _whole(raw.get("event_count")) is not None else _whole(place.get("event_count")),
        "continent": clean(place.get("geo_continent_name")),
        "continent_code": clean(place.get("geo_continent")),
        "timezone": clean(place.get("timezone")),
        "latitude": latitude,
        "longitude": longitude,
        "languages": [v for v in as_list(place.get("locales")) if isinstance(v, str)],
        "featured_event_ids": [v for v in as_list(place.get("featured_event_api_ids")) if isinstance(v, str)],
        "publication_name": clean(place.get("publication_name")),
        "icon": clean(place.get("icon_url")),
        "hero_image": clean(place.get("hero_image_desktop_url")),
        "hero_image_mobile": clean(place.get("hero_image_mobile_url")),
        "hero_image_square": clean(place.get("hero_image_square_url")),
        "social_image": clean(place.get("social_image_url")),
    }


def parse_cities(payload):
    """/discover/bootstrap-page -> the cities, by continent and name (the
    site sorts them by distance from the caller)."""
    cities = [c for c in (parse_city(raw) for raw in as_list(as_dict(payload).get("places"))) if c]
    return sorted(cities, key=lambda c: (c["continent"] or "", c["name"] or ""))


def parse_category(raw):
    """A Discover category. The list wraps the record with its counts; a
    category page answers {"category": {...}, num_upcoming_events, subscriber_count}."""
    raw = as_dict(raw)
    record = as_dict(raw.get("category")) or raw
    if not record.get("api_id"):
        return None
    slug = clean(record.get("slug"))

    def first(*values):
        return next((v for v in (_whole(value) for value in values) if v is not None), None)

    return {
        "id": record.get("api_id"),
        "name": clean(record.get("name")),
        "slug": slug,
        "link": refs.page_link(slug),
        "page_title": clean(record.get("page_title")),
        "description": multiline(record.get("description")),
        "upcoming_event_count": first(raw.get("num_upcoming_events"), raw.get("event_count"), record.get("event_count")),
        "subscriber_count": first(raw.get("subscriber_count"), record.get("subscriber_count")),
        "icon": clean(record.get("icon_url")),
        "simple_icon": clean(record.get("simple_icon_url")),
        "hero_image": clean(record.get("hero_image_desktop_url")),
        "social_image": clean(record.get("social_image_url")),
        "created_at": iso_datetime(record.get("created_at")),
    }


def parse_categories(payload):
    """/discover/category/list-categories -> the categories, in the site's order."""
    return [c for c in (parse_category(raw) for raw in as_list(as_dict(payload).get("entries"))) if c]


def next_cursor(payload):
    """The cursor that opens the page after this one; None on the last page."""
    payload = as_dict(payload)
    cursor = payload.get("next_cursor")
    return cursor if payload.get("has_more") and isinstance(cursor, str) and cursor else None
