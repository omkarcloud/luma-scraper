"""Reference resolvers, link builders and the choice tables for /luma/*.

ONE param per input: `event` accepts an event id, an event slug OR an event
link; `calendar`, `user`, `city` and `category` likewise. Resolvers raise
ValueError with a user-facing message (RefField turns it into a 400).

Identifier shapes (luma.com, 2026-11-21):
  * event     an id (evt-UvVJTTBckZ6kMsc) or the slug its link ends in
              (luma.com/k5woffsv, luma.com/smart-glass-hack-nyc; lu.ma is
              the same site). luma.com/event/evt-... redirects to the slug
              link. The site's event call takes either form; the batch call
              takes ids only.
  * calendar  an id (cal-JDvWKGO7IFEV3WY) or its slug (luma.com/marinarunclub;
              luma.com/calendar/cal-... redirects there). An event link and a
              calendar link look alike: the site's own resolver says which
              one a slug is.
  * user      a username (luma.com/user/ajaytuniki, luma.com/@ajaytuniki) or
              an id (usr-4RfJK79d4BBbtk2): many hosts have no username and
              their profile link carries the id.
  * city      the slug of one of the cities the site's Discover page lists
              (sf, nyc, london: luma.com/sf) or its id (discplace-...).
  * category  the slug of a Discover category (ai, tech: luma.com/ai); its
              id is cat-<slug>.
  * tag       a calendar's own tag: its name ("Run") or its id (tag-...).
"""
import re
from urllib.parse import unquote, urlparse

SITE = "https://luma.com"
HOSTS = ("luma.com", "lu.ma")

EVENT_ID_RE = re.compile(r"^evt-[A-Za-z0-9]{6,40}$")
CALENDAR_ID_RE = re.compile(r"^cal-[A-Za-z0-9]{6,40}$")
USER_ID_RE = re.compile(r"^usr-[A-Za-z0-9]{6,40}$")
PLACE_ID_RE = re.compile(r"^discplace-[A-Za-z0-9]{6,40}$")
TAG_ID_RE = re.compile(r"^tag-[A-Za-z0-9]{6,40}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

MAX_BATCH_EVENTS = 250      # the site's own batch call reads the first 250 ids and drops the rest (2026-11-21)
MAX_BATCH_SLUGS = 20        # a slug or a link costs one call of its own: the batch call takes ids only

# The first path segment of the site's own pages: never an event or a calendar slug.
_RESERVED = {"user", "event", "calendar", "discover", "home", "signin", "signup", "create", "pricing", "explore",
             "settings", "help", "legal", "terms", "privacy", "app", "ios", "android", "api", "search", "map"}

PERIODS = {"upcoming": "future", "past": "past"}       # ours -> the site's `period`

_EVENT_HELP = ("Must be a Luma event id (evt-UvVJTTBckZ6kMsc), an event slug (k5woffsv) or an event link "
               "(https://luma.com/k5woffsv).")
_CALENDAR_HELP = ("Must be a Luma calendar id (cal-JDvWKGO7IFEV3WY), a calendar slug (marinarunclub) or a calendar "
                  "link (https://luma.com/marinarunclub).")
_USER_HELP = ("Must be a Luma username (ajaytuniki), a user id (usr-4RfJK79d4BBbtk2) or a profile link "
              "(https://luma.com/user/ajaytuniki).")
_CITY_HELP = "Must be a Luma city slug (sf, nyc, london) or a city link (https://luma.com/sf)."
_CATEGORY_HELP = "Must be a Luma category slug (ai, tech, food) or a category link (https://luma.com/ai)."


# ---- links in -------------------------------------------------------------------------

def _segments(value):
    """The path segments of a luma.com / lu.ma link (None when the value is
    not a link); a link to another site raises ValueError."""
    if not value.startswith(("http://", "https://", "//")):
        head = value.split("/", 1)[0].lower()
        if "/" not in value or not any(head == h or head.endswith("." + h) for h in HOSTS):
            return None
        value = "https://" + value
    elif value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in HOSTS):
        raise ValueError("Must be a luma.com link.")
    return [unquote(part) for part in (parsed.path or "/").split("/") if part]


def _slug_of(value, id_re, id_segment, help_text):
    """A bare id or slug, or the one a link carries: /<slug>, /<id_segment>/<id>."""
    value = (value or "").strip()
    parts = _segments(value)
    if parts is None:
        if id_re.match(value) or (SLUG_RE.match(value) and not _foreign_id(value, id_re)):
            return value
        raise ValueError(help_text)
    if len(parts) >= 2 and parts[0].lower() == id_segment and id_re.match(parts[1]):
        return parts[1]
    if parts and parts[0].lower() not in _RESERVED and SLUG_RE.match(parts[0]) and not _foreign_id(parts[0], id_re):
        return parts[0]
    raise ValueError(help_text)


def _foreign_id(value, own):
    """True for an id of another kind of record (a cal- id given as an event)."""
    return any(shape is not own and shape.match(value)
               for shape in (EVENT_ID_RE, CALENDAR_ID_RE, USER_ID_RE, PLACE_ID_RE, TAG_ID_RE))


def resolve_event(value):
    """Event id, slug or link -> the id or the slug (the site's event call takes both)."""
    return _slug_of(value, EVENT_ID_RE, "event", _EVENT_HELP)


def resolve_events(value):
    """'evt-..., k5woffsv, <link>' -> the ids and slugs in the order given,
    repeats dropped. At most MAX_BATCH_EVENTS, of which at most
    MAX_BATCH_SLUGS may be slugs or links."""
    refs = []
    for raw in str(value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        ref = resolve_event(item)
        if ref not in refs:
            refs.append(ref)
    if not refs:
        raise ValueError(_EVENT_HELP)
    if len(refs) > MAX_BATCH_EVENTS:
        raise ValueError(f"At most {MAX_BATCH_EVENTS} events per call.")
    if sum(1 for ref in refs if not EVENT_ID_RE.match(ref)) > MAX_BATCH_SLUGS:
        raise ValueError(f"At most {MAX_BATCH_SLUGS} of the events may be slugs or links; the rest must be event ids (evt-...).")
    return refs


def resolve_calendar(value):
    """Calendar id, slug or link -> the id or the slug."""
    return _slug_of(value, CALENDAR_ID_RE, "calendar", _CALENDAR_HELP)


def resolve_user(value):
    """Username, user id or profile link -> the username or the id."""
    value = (value or "").strip()
    parts = _segments(value)
    if parts is None:
        name = value[1:] if value.startswith("@") else value
    elif len(parts) >= 2 and parts[0].lower() == "user":
        name = parts[1]
    elif len(parts) == 1 and parts[0].startswith("@"):
        name = parts[0][1:]
    else:
        raise ValueError(_USER_HELP)
    if USER_ID_RE.match(name) or (SLUG_RE.match(name) and not _foreign_id(name, USER_ID_RE)):
        return name
    raise ValueError(_USER_HELP)


def resolve_city(value):
    """City slug, id or link -> the slug (lower case) or the id."""
    value = (value or "").strip()
    parts = _segments(value)
    slug = value if parts is None else (parts[0] if parts else "")
    if PLACE_ID_RE.match(slug):
        return slug
    if SLUG_RE.match(slug) and slug.lower() not in _RESERVED and not _foreign_id(slug, PLACE_ID_RE):
        return slug.lower()
    raise ValueError(_CITY_HELP)


def resolve_category(value):
    """Category slug, id (cat-ai) or link -> the slug, lower case."""
    value = (value or "").strip()
    parts = _segments(value)
    slug = value if parts is None else (parts[0] if parts else "")
    if slug.lower().startswith("cat-"):
        slug = slug[4:]
    if SLUG_RE.match(slug) and slug.lower() not in _RESERVED and not _foreign_id(slug, None):
        return slug.lower()
    raise ValueError(_CATEGORY_HELP)


# ---- links out ------------------------------------------------------------------------

def page_link(slug):
    """The site link of an event, a calendar, a city or a category: they share one namespace."""
    return f"{SITE}/{slug}" if slug else None


def calendar_page_link(calendar_id):
    """The page of a calendar that has no slug: luma.com/calendar/<id>."""
    return f"{SITE}/calendar/{calendar_id}" if calendar_id else None


def user_link(username, user_id):
    """A profile link: by username when the user has one, else by id."""
    key = username or user_id
    return f"{SITE}/user/{key}" if key else None


def _handle(value):
    value = value.strip() if isinstance(value, str) else ""
    return value or None


def social_links(record):
    """The record's handles as the links the site itself builds from them."""
    record = record if isinstance(record, dict) else {}
    out = {}
    for name, key, prefix in (("instagram", "instagram_handle", "https://instagram.com/"),
                              ("x", "twitter_handle", "https://x.com/"),
                              ("linkedin", "linkedin_handle", "https://linkedin.com"),
                              ("tiktok", "tiktok_handle", "https://tiktok.com/@"),
                              ("youtube", "youtube_handle", "https://youtube.com/@")):
        handle = _handle(record.get(key))
        if handle is None:
            out[name] = None
        elif handle.startswith(("http://", "https://")):
            out[name] = handle
        elif name == "linkedin":
            out[name] = prefix + (handle if handle.startswith("/") else "/" + handle)
        else:
            out[name] = prefix + handle.lstrip("@/")
    return out
