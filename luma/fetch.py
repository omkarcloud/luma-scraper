"""Luma transport: plain curl_cffi with chrome TLS on api.luma.com, the JSON
API the site's own pages call. No browser, no login, no cookie and no
token. One session per request thread on the exit config.LUMA_EXIT names
(the server's own IP in production); a retryable failure drops the session,
so the retry rides a fresh connection.

Every call is a GET that answers JSON (2026-11-21):

  /discover/get-paginated-events   events near a point (latitude, longitude),
                  inside a box (north, south, east, west) or of a city's own
                  page (discover_place_api_id), optionally in one category
                  (slug) or matching a keyword (query). 50 rows a page at
                  most; pagination_cursor is the previous page's next_cursor.
                  A keyword list has no next page. Without a place the site
                  answers for the caller's own address, so a place is always
                  sent.
  /event/get?event_api_id=         one event in full; takes the id or the slug
  /event/get-by-ids?event_api_ids= list rows for up to 250 ids (ids only;
                  unknown ids are left out, more than 250 are cut)
  /url?url=<slug>                  what a slug is: {kind: event | calendar |
                  discover-place | category, data}
  /calendar/get?api_id=            a calendar (id only; a slug goes through /url)
  /calendar/get-items              a calendar's events: period future | past |
                  specific (after + before), tag_api_ids, available_only, query
  /user/profile?username=          a profile; takes the username or the id
  /user/profile/events-hosting     the events a user hosts (user_api_id only),
                  period future | past, 50 rows a page
  /discover/bootstrap-page         the cities of the Discover page
  /discover/get-place              one city, by slug or by discover_place_api_id
  /discover/get-calendars          the calendars a city's page features
  /discover/category/list-categories, /discover/category/get-page
  /discover/get-landing-calendars  the calendars the Discover page features

The headers are the ones the site's own script adds to these calls. The
answers were the same without them on 2026-11-21, so none is a key.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  LumaUpstreamError  transport failure / 5xx / unexpected body   retryable
  LumaBlocked        401 / 403 / 429 / a challenge or non-JSON page  retryable
  LumaBadRequest     the site rejected the parameters (400)       never retried
  LumaNotFound       unknown event / calendar / user / city        never retried
"""
import threading

import config
import egress
from luma.refs import SITE
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import pages, retry, sessions

IMPERSONATE = "chrome"
API = "https://api.luma.com"
API_TIMEOUT = 25

EVENTS_PAGE = 50            # /discover/get-paginated-events serves at most 50 rows (100, 200 and 500 answer as 50)
USER_EVENTS_PAGE = 50       # /user/profile/events-hosting too (100 and 200 answer as 50)
CALENDAR_EVENTS_PAGE = 100  # /calendar/get-items takes more (500 was accepted); 100 keeps a page under ~1 MB upstream
MAX_BATCH = 250             # /event/get-by-ids reads the first 250 ids


class LumaUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unexpected payload — retryable."""


class LumaBlocked(LumaUpstreamError, Blocked):
    """401 / 403 / 429, a challenge page or a page where JSON was expected — retryable."""


class LumaBadRequest(BadRequest):
    """The site rejected the parameters (HTTP 400) — never retried."""


class LumaNotFound(NotFound):
    """No such event, calendar, user, city or category — never retried."""


# ---- sessions ------------------------------------------------------------------------

# One curl session per request thread (a curl handle must not be shared
# across threads). The calls are stateless: no cookie or token is carried.
_local = threading.local()


def _session():
    return sessions.thread_session(_local, lambda: egress.exit_session(config.LUMA_EXIT, impersonate=IMPERSONATE))


def _drop_session():
    """Close the thread's session so the retry rides a fresh connection / exit."""
    sessions.drop(_local)


def _retrying(fn):
    """fn() under the shared retry policy: only LumaUpstreamError (Blocked
    included) is retried; NotFound and BadRequest surface at once."""
    return retry.run(fn, config, LumaUpstreamError, lambda e: _drop_session())


# ---- one request ---------------------------------------------------------------------

def api_headers(page_path="/discover"):
    """What the site's script sends with a data call made from the page at
    `page_path` (samples/calls/*.request.json, 2026-11-21). The session adds
    its own user-agent, client hints, accept-language and accept-encoding."""
    return {
        "accept": "*/*",
        "origin": SITE,
        "priority": "u=1, i",
        "referer": SITE + "/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "x-luma-client-type": "luma-web",
        "x-luma-client-version": config.LUMA_CLIENT_VERSION,
        "x-luma-timezone": config.LUMA_TIMEZONE,
        "x-luma-web-url": SITE + page_path,
    }


def _message(resp):
    """The site's own words for a refusal: {"message": "...", "code": null}."""
    try:
        return str(resp.json().get("message") or "")[:200]
    except Exception:
        return ""


def _classify(resp, label):
    """A 200 passes. 404 -> NotFound; 400 -> BadRequest with the site's
    message; 401 / 403 / 429 and a challenge page -> Blocked; anything else
    -> upstream error."""
    status = resp.status_code
    if pages.is_challenged(status, resp.headers, resp.text[:4000] if status != 200 else ""):
        raise LumaBlocked(f"challenge page for {label}")
    if status == 200:
        return
    if status == 404:
        raise LumaNotFound(f"{label} not found")
    if status == 400:
        raise LumaBadRequest(_message(resp) or f"the site rejected {label}")
    if status in (401, 403, 429):
        raise LumaBlocked(f"HTTP {status} for {label}: {_message(resp)}".rstrip(": "))
    raise LumaUpstreamError(f"HTTP {status} for {label}")


def _get(path, params, label, page_path="/discover"):
    """GET api.luma.com<path> -> the decoded JSON object."""
    def once():
        try:
            resp = _session().get(API + path, params=params, headers=api_headers(page_path), timeout=API_TIMEOUT)
        except Exception as e:
            raise LumaUpstreamError(f"request failed for {label}: {type(e).__name__}: {e}")
        _classify(resp, label)
        try:
            payload = resp.json()
        except Exception:
            raise LumaBlocked(f"non-JSON answer for {label}: {resp.text[:120]!r}")
        if not isinstance(payload, dict):
            raise LumaUpstreamError(f"unexpected payload for {label}: {resp.text[:120]!r}")
        return payload
    return _retrying(once)


def _paged(path, params, limit, cursor, label, page_path):
    """One page of a cursor list -> the payload, which must carry `entries`."""
    query = list(params) + [("pagination_limit", limit)]
    if cursor:
        query.append(("pagination_cursor", cursor))
    payload = _get(path, query, label, page_path)
    if not isinstance(payload.get("entries"), list):
        raise LumaUpstreamError(f"{label} answered no entries: {str(payload)[:120]!r}")
    return payload


# ---- events --------------------------------------------------------------------------

def fetch_events(place, category=None, query=None, cursor=None, page_path="/discover"):
    """One page of events. `place` is {"latitude", "longitude"}, a box
    {"north", "south", "east", "west"} or {"discover_place_api_id"} (the
    city page's own short list); `category` a category slug; `query` a keyword."""
    params = [(key, value) for key, value in place.items()]
    if category:
        params.append(("slug", category))
    if query:
        params.append(("query", query))
    return _paged("/discover/get-paginated-events", params, EVENTS_PAGE, cursor, "events", page_path)


def fetch_event(ref):
    """One event in full, by id or slug. Unknown -> NotFound."""
    payload = _get("/event/get", [("event_api_id", ref)], f"event {ref}", f"/{ref}")
    if not isinstance(payload.get("event"), dict):
        raise LumaUpstreamError(f"event {ref} answered no event: {str(payload)[:120]!r}")
    return payload


def fetch_events_by_ids(ids):
    """List rows for up to MAX_BATCH event ids -> the raw rows the site
    knows (unknown ids are simply absent; the order is the site's)."""
    payload = _get("/event/get-by-ids", [("event_api_ids", event_id) for event_id in ids[:MAX_BATCH]],
                   f"events {','.join(ids)[:60]}")
    rows = payload.get("entries")
    if not isinstance(rows, list):
        raise LumaUpstreamError(f"batch answered no entries: {str(payload)[:120]!r}")
    return rows


def fetch_slug(slug):
    """What the site's own resolver says a slug is: {"kind": "event" |
    "calendar" | "discover-place" | "category", "data": {...}}. Unknown -> NotFound."""
    payload = _get("/url", [("url", slug)], f"{slug}", f"/{slug}")
    if not isinstance(payload.get("data"), dict):
        raise LumaUpstreamError(f"{slug} answered no data: {str(payload)[:120]!r}")
    return payload


# ---- calendars -----------------------------------------------------------------------

def fetch_calendar(calendar_id):
    """A calendar by id (cal-...). Unknown -> NotFound."""
    payload = _get("/calendar/get", [("api_id", calendar_id)], f"calendar {calendar_id}", "/calendar/" + calendar_id)
    if not isinstance(payload.get("calendar"), dict):
        raise LumaUpstreamError(f"calendar {calendar_id} answered no calendar: {str(payload)[:120]!r}")
    return payload


def fetch_calendar_events(calendar_id, period, after=None, before=None, tag_id=None, available_only=False,
                          query=None, cursor=None, page_path="/discover"):
    """One page of a calendar's events. `period` is the site's own: future,
    past, or specific with `after` and `before` (both, as ISO instants)."""
    params = [("calendar_api_id", calendar_id), ("period", period)]
    if period == "specific":
        params += [("after", after), ("before", before)]
    if tag_id:
        params.append(("tag_api_ids", tag_id))
    if available_only:
        params.append(("available_only", "true"))
    if query:
        params.append(("query", query))
    return _paged("/calendar/get-items", params, CALENDAR_EVENTS_PAGE, cursor, f"events of calendar {calendar_id}", page_path)


def fetch_place_calendars(place_id, page_path="/discover"):
    """The calendars a city's page features."""
    payload = _get("/discover/get-calendars", [("discover_place_api_id", place_id)], f"calendars of {place_id}", page_path)
    if not isinstance(payload.get("calendars"), list):
        raise LumaUpstreamError(f"calendars of {place_id} answered no list: {str(payload)[:120]!r}")
    return payload


def fetch_landing_calendars():
    """The calendars the Discover page itself features."""
    return _get("/discover/get-landing-calendars", [], "featured calendars")


# ---- users ---------------------------------------------------------------------------

def fetch_user(ref):
    """A profile by username or id. Unknown -> NotFound."""
    payload = _get("/user/profile", [("username", ref)], f"user {ref}", f"/user/{ref}")
    if not isinstance(payload.get("user"), dict):
        raise LumaUpstreamError(f"user {ref} answered no user: {str(payload)[:120]!r}")
    return payload


def fetch_user_events(user_id, period, cursor=None):
    """One page of the events a user hosts (the id only: a username or an
    unknown id answers an empty list)."""
    params = [("user_api_id", user_id), ("period", period)]
    return _paged("/user/profile/events-hosting", params, USER_EVENTS_PAGE, cursor, f"events of user {user_id}", f"/user/{user_id}")


# ---- cities and categories -----------------------------------------------------------

def fetch_cities():
    """The Discover page's data: every city with its event count."""
    payload = _get("/discover/bootstrap-page", [], "cities")
    if not isinstance(payload.get("places"), list):
        raise LumaUpstreamError(f"cities answered no places: {str(payload)[:120]!r}")
    return payload


def fetch_city(ref):
    """One city by slug or id (discplace-...). Unknown -> NotFound."""
    key = "discover_place_api_id" if ref.startswith("discplace-") else "slug"
    payload = _get("/discover/get-place", [(key, ref)], f"city {ref}", f"/{ref}")
    if not isinstance(payload.get("place"), dict):
        raise LumaUpstreamError(f"city {ref} answered no place: {str(payload)[:120]!r}")
    return payload


def fetch_categories():
    """Every Discover category (12 on 2026-11-21, in one page)."""
    return _paged("/discover/category/list-categories", [], 20, None, "categories", "/discover")


def fetch_category(slug):
    """A category's page: its record, counts and featured calendars. Unknown -> NotFound."""
    payload = _get("/discover/category/get-page", [("slug", slug)], f"category {slug}", f"/{slug}")
    if not isinstance(payload.get("category"), dict):
        raise LumaUpstreamError(f"category {slug} answered no category: {str(payload)[:120]!r}")
    return payload
