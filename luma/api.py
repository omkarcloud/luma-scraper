"""The /luma/* endpoint functions (route_glue's route_table calls them with
the schema's loaded kwargs).

Lists. The site pages its lists with a cursor and publishes no total, the
API takes `page`. Each page of a list is cached (cache.cached_call) with
the cursor that opens the page after it, and those cursors are also kept
in this process, so a client walking a list pays one upstream call a page.
A cold page N walks the pages before it once.

Shared pieces, each cached so the endpoints that read them pay one fetch:

  luma.calendar    a calendar in full: calendars/details answers it,
                   calendars/events takes its id and its tags from it
  luma.user        a profile: users/details answers it, users/events
                   takes the user's id from it
  luma.city        a city: events/search and calendars/list take its
                   coordinate and its id from it
  luma.categories  the category list: categories/list answers it,
                   events/search checks a category against it
"""
import threading
import time
from collections import OrderedDict
from datetime import date, timedelta

import config
from cache import cached_call
from luma import cache_config as ttl
from luma import parsers
from luma import refs
from luma.fetch import (CALENDAR_EVENTS_PAGE, EVENTS_PAGE, USER_EVENTS_PAGE, LumaNotFound, LumaUpstreamError,
                        fetch_calendar, fetch_calendar_events, fetch_categories, fetch_category, fetch_cities,
                        fetch_city, fetch_event, fetch_events, fetch_events_by_ids, fetch_landing_calendars,
                        fetch_place_calendars, fetch_slug, fetch_user, fetch_user_events)
from scraper_utils import fanout

SLUG_WORKERS = 5            # events/batch: the slugs and links of one call are read side by side
CURSOR_MEMO_CAP = 20000     # cursors kept in this process (about 100 bytes each)
OPEN_FROM = "1970-01-01"    # the other end of a date range given with one end: the site wants both
OPEN_TO = "2100-01-01"

_PERIOD_NAMES = {token: name for name, token in refs.PERIODS.items()}
_BRIEF = ("id", "name", "slug", "link")


def _unshown():
    return bool(config.LUMA_SHOW_UNSHOWN_FIELDS)


# ---- cursor-paged lists --------------------------------------------------------------

# (list name, frozen params, page) -> (the cursor that opens the page, or False = no such page; when it was kept)
_cursors = OrderedDict()
_cursors_lock = threading.Lock()


def _list_key(name, params):
    return (name, tuple(sorted((key, str(value)) for key, value in params.items() if value is not None)))


def _known_cursor(key, page):
    """(True, cursor or False) when this process knows what opens `page`, else (False, None)."""
    with _cursors_lock:
        if (key, page) in _cursors:
            cursor, kept_at = _cursors[(key, page)]
            if time.monotonic() - kept_at < ttl.CURSOR_CACHE.total_seconds():
                _cursors.move_to_end((key, page))
                return True, cursor
            del _cursors[(key, page)]       # too old to trust: the list is walked again, and may have grown
    return False, None


def _remember_cursor(key, page, cursor):
    with _cursors_lock:
        _cursors[(key, page)] = (cursor or False, time.monotonic())
        _cursors.move_to_end((key, page))
        while len(_cursors) > CURSOR_MEMO_CAP:
            _cursors.popitem(last=False)


def _list_page(name, params, page, fetch_page, parse_row, pageable=True):
    """Page `page` of a cursor list -> {"rows": [...], "next": the cursor of
    the page after it or None}. `fetch_page(cursor)` reads one upstream page;
    `params` identify the list (the cache key with the page)."""
    key = _list_key(name, params)

    def build(**_):
        cursor = _cursor_for(page)
        if cursor is False:
            return {"rows": [], "next": None}       # a page past the end is an empty page
        rows, following = parsers.parse_events_page(fetch_page(cursor), parse_row, _unshown())
        return {"rows": rows, "next": following if pageable and rows else None}

    def _cursor_for(wanted):
        if wanted == 1:
            return None
        if not pageable:
            return False
        known, cursor = _known_cursor(key, wanted)
        if known:
            return cursor
        before = _list_page(name, params, wanted - 1, fetch_page, parse_row, pageable)
        return before["next"] or False

    result = cached_call(f"luma.{name}", {**params, "page": page}, build, ttl.LIST_CACHE)
    _remember_cursor(key, page + 1, result.get("next"))
    return result


def _pagination(page, per_page, result):
    # the site publishes no total: has_next follows its own has_more, up to the last page the API takes
    return {"page": page, "items_per_page": per_page, "total_pages": None, "total_count": None,
            "has_next": bool(result.get("next")) and page < config.LUMA_MAX_PAGE}


def _brief(record):
    return {key: record.get(key) for key in _BRIEF} if record else None


# ---- cities and categories (shared) --------------------------------------------------

def _fetch_city(city):
    return parsers.parse_city(fetch_city(city))


def _city(ref):
    """A city by slug or id (cached). Unknown -> NotFound."""
    try:
        record = cached_call("luma.city", {"city": ref}, _fetch_city, ttl.PLACES_CACHE)
    except LumaNotFound:
        record = None
    if not record:
        raise LumaNotFound(f"city {ref} not found (cities/list has every city)")
    return record


def _fetch_categories():
    return parsers.parse_categories(fetch_categories())


def _categories():
    return cached_call("luma.categories", {}, _fetch_categories, ttl.PLACES_CACHE)


def _category(slug):
    """A category of the Discover list, by slug or by its id without the
    "cat-" (refs.resolve_category cuts it off, and an id is not always
    cat-<slug>: cat-fooddrink is food). Unknown -> NotFound."""
    records = _categories()
    for record in records:
        if record["slug"] == slug:
            return record
    for record in records:
        if (record["id"] or "").lower() == f"cat-{slug}":
            return record
    raise LumaNotFound(f"category {slug} not found (categories/list has every category)")


def _cities_list():
    cities = parsers.parse_cities(fetch_cities())
    return {"count": len(cities), "cities": cities}


def cities_list(**data):
    return cached_call("luma.cities", data, _cities_list, ttl.PLACES_CACHE)


def categories_list(**data):
    categories = _categories()
    return {"count": len(categories), "categories": categories}


# ---- events/search -------------------------------------------------------------------

def _box_longitude(place, longitude):
    """The longitude of the box nearest `longitude`: itself when the box
    holds it (a box whose west lies past its east spans the date line),
    else the nearer of the two edges, counted around the globe."""
    west, east = place["west"], place["east"]
    if (west <= longitude <= east) if west <= east else (longitude >= west or longitude <= east):
        return longitude
    return min((west, east), key=lambda edge: abs((longitude - edge + 180) % 360 - 180))


def _far_from(place, row):
    """True for a row that provably lies outside the place searched: farther
    than the reach from the point, or from the nearest point of the box (a
    row inside the box is never far, however large the box). The
    site's keyword search matches loosely and pads a thin result with
    events from anywhere (a nonsense word near San Francisco answered an
    event in Texas, "hackathon" in New York one in India): those are left
    out. A row without a coordinate cannot be placed and is kept."""
    latitude, longitude = row["location"]["latitude"], row["location"]["longitude"]
    if latitude is None or longitude is None:
        return False
    if "latitude" in place:
        nearest = (place["latitude"], place["longitude"])
    else:
        nearest = (min(max(latitude, place["south"]), place["north"]), _box_longitude(place, longitude))
    return parsers.distance_km(*nearest, latitude, longitude) > config.LUMA_KEYWORD_REACH_KM


def events_search(city=None, latitude=None, longitude=None, north=None, south=None, east=None, west=None,
                  category=None, query=None, page=1):
    city_record = _city(city) if city else None
    category_record = _category(category) if category else None
    if city_record:
        if city_record["latitude"] is None or city_record["longitude"] is None:
            raise LumaUpstreamError(f"city {city} answered no coordinate")
        place = {"latitude": city_record["latitude"], "longitude": city_record["longitude"]}
        page_path = f"/{city_record['slug']}"
    elif latitude is not None:
        place = {"latitude": latitude, "longitude": longitude}
        page_path = f"/{category}" if category else "/discover"
    else:
        place = {"north": north, "south": south, "east": east, "west": west}
        page_path = f"/{category}/map" if category else "/discover"
    slug = category_record["slug"] if category_record else None

    def fetch_page(cursor):
        return fetch_events(place, slug, query, cursor, page_path)

    # a keyword answers its 50 best matches and no further page
    result = _list_page("events", {**place, "category": slug, "query": query}, page, fetch_page,
                        parsers.parse_event_row, pageable=not query)
    events = [row for row in result["rows"] if not _far_from(place, row)] if query else result["rows"]
    return {
        "city": _brief(city_record),
        "category": _brief(category_record),
        "query": query,
        "events": events,
        "pagination": _pagination(page, EVENTS_PAGE, result),
    }


# ---- events/details, events/batch ----------------------------------------------------

def _event(event):
    try:
        payload = fetch_event(event)
    except LumaNotFound:
        raise LumaNotFound(f"event {event} not found")
    except LumaUpstreamError:
        # the site's slug resolver carries the same record for an event slug
        if refs.EVENT_ID_RE.match(event):
            raise
        resolved = fetch_slug(event)
        if resolved.get("kind") != "event":
            raise LumaNotFound(f"event {event} not found")
        payload = resolved["data"]
    return parsers.parse_event(payload, _unshown())


def events_details(**data):
    return cached_call("luma.event", data, _event, ttl.EVENT_CACHE)


def _slug_row(slug):
    try:
        return parsers.parse_event_row(fetch_event(slug), _unshown())
    except LumaNotFound:
        return None


def _events_batch(events):
    ids = [ref for ref in events if refs.EVENT_ID_RE.match(ref)]
    slugs = [ref for ref in events if ref not in ids]
    by_ref = parsers.parse_batch(fetch_events_by_ids(ids), _unshown()) if ids else {}
    if slugs:
        rows = fanout.per_call([lambda slug=slug: _slug_row(slug) for slug in slugs], SLUG_WORKERS)
        by_ref.update({slug: row for slug, row in zip(slugs, rows) if row})
    found, seen = [], set()
    for ref in events:
        row = by_ref.get(ref)
        if row and row["id"] not in seen:        # an id and its own slug in one call: one row
            seen.add(row["id"])
            found.append(row)
    return {
        "requested_count": len(events),
        "found_count": len(found),
        "not_found": [ref for ref in events if ref not in by_ref],
        "events": found,
    }


def events_batch(**data):
    return cached_call("luma.batch", data, _events_batch, ttl.EVENT_CACHE)


# ---- calendars -----------------------------------------------------------------------

def _fetch_calendar(calendar):
    try:
        if refs.CALENDAR_ID_RE.match(calendar):
            payload = fetch_calendar(calendar)
        else:
            # a calendar slug and an event slug look alike: the site's own resolver says which it is
            resolved = fetch_slug(calendar)
            payload = resolved["data"] if resolved.get("kind") == "calendar" else None
            if payload is None and resolved.get("kind") == "event":
                raise LumaNotFound(f"calendar {calendar} not found: {calendar} is an event (events/details reads it)")
    except LumaNotFound as e:
        raise LumaNotFound(str(e) if "is an event" in str(e) else f"calendar {calendar} not found")
    if payload is None:
        raise LumaNotFound(f"calendar {calendar} not found")
    return parsers.parse_calendar(payload, _unshown())


def _calendar(ref):
    """A calendar by id or slug (cached). Unknown -> NotFound."""
    return cached_call("luma.calendar", {"calendar": ref}, _fetch_calendar, ttl.CALENDAR_CACHE)


def calendars_details(calendar):
    return _calendar(calendar)


def _tag(record, tag):
    """The calendar's own tag named `tag` (its name, any case, or its id). Unknown -> NotFound."""
    wanted = tag.casefold()
    for known in record.get("tags") or []:
        if known["id"] == tag or (known["name"] or "").casefold() == wanted:
            return known
    names = ", ".join(t["name"] for t in record.get("tags") or [] if t["name"]) or "it has none"
    raise LumaNotFound(f"tag {tag!r} not found on calendar {record['name'] or record['id']} (its tags: {names})")


def _day_after(day):
    try:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    except OverflowError:       # 9999-12-31, the last day a date holds, has no day after
        return day


def calendars_events(calendar, period=None, date_from=None, date_to=None, tag=None, available_only=None,
                     query=None, page=1):
    record = _calendar(calendar)
    tag_record = _tag(record, tag) if tag else None
    if date_from or date_to:
        token = "specific"
        after = f"{date_from or OPEN_FROM}T00:00:00.000Z"
        before = f"{_day_after(date_to) if date_to else OPEN_TO}T00:00:00.000Z"     # `before` leaves the instant itself out
    else:
        token, after, before = refs.PERIODS[period or "upcoming"], None, None
    calendar_id = record["id"]
    page_path = f"/{record['slug']}" if record.get("slug") else "/discover"

    def fetch_page(cursor):
        return fetch_calendar_events(calendar_id, token, after, before, tag_record["id"] if tag_record else None,
                                     bool(available_only), query, cursor, page_path)

    params = {"calendar": calendar_id, "period": token, "after": after, "before": before,
              "tag": tag_record["id"] if tag_record else None, "available_only": bool(available_only), "query": query}
    result = _list_page("calendar_events", params, page, fetch_page, parsers.parse_calendar_event_row)
    return {
        "calendar": _brief(record),
        "period": _PERIOD_NAMES.get(token),
        "date_from": date_from,
        "date_to": date_to,
        "tag": {key: tag_record[key] for key in ("id", "name")} if tag_record else None,
        "is_available_only": bool(available_only),
        "query": query,
        "events": result["rows"],
        "pagination": _pagination(page, CALENDAR_EVENTS_PAGE, result),
    }


def _calendars_list(city, category):
    city_record = category_record = None
    if city:
        city_record = _city(city)
        rows = fetch_place_calendars(city_record["id"], f"/{city_record['slug']}").get("calendars")
    elif category:
        try:
            payload = fetch_category(category)
        except LumaNotFound:
            # not a slug: an id whose slug the category list names (cat-fooddrink is food), else NotFound
            slug = _category(category)["slug"]
            if slug == category:
                raise
            payload = fetch_category(slug)
        category_record = parsers.parse_category(payload)
        rows = (payload.get("featured_calendars") or []) + (payload.get("timeline_calendars") or [])
    else:
        payload = fetch_landing_calendars()
        rows = (payload.get("major_calendars") or []) + (payload.get("global_calendars") or [])
    calendars = parsers.parse_calendar_cards(rows, _unshown())
    return {
        "city": _brief(city_record),
        "category": _brief(category_record),
        "count": len(calendars),
        "calendars": calendars,
    }


def calendars_list(**data):
    return cached_call("luma.calendars", data, _calendars_list, ttl.CALENDARS_LIST_CACHE)


# ---- users ---------------------------------------------------------------------------

def _fetch_user(user):
    return parsers.parse_user(fetch_user(user), _unshown())


def _user(ref):
    """A profile by username or id (cached). Unknown -> NotFound."""
    try:
        return cached_call("luma.user", {"user": ref}, _fetch_user, ttl.USER_CACHE)
    except LumaNotFound:
        raise LumaNotFound(f"user {ref} not found")


def users_details(user):
    return _user(user)


def users_events(user, period=None, page=1):
    record = _user(user)
    token = refs.PERIODS[period or "upcoming"]
    user_id = record["id"]

    def fetch_page(cursor):
        return fetch_user_events(user_id, token, cursor)

    result = _list_page("user_events", {"user": user_id, "period": token}, page, fetch_page, parsers.parse_event_row)
    return {
        "user": {key: record.get(key) for key in ("id", "name", "username", "link")},
        "period": _PERIOD_NAMES[token],
        "events": result["rows"],
        "pagination": _pagination(page, USER_EVENTS_PAGE, result),
    }
