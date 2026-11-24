"""Marshmallow request schemas for every /luma/* route.

ONE param per input (never a sibling `url` / `id` pair):

    event      an event id (evt-UvVJTTBckZ6kMsc), an event slug (k5woffsv)
               OR a luma.com event link
    events     up to 250 of those, comma-separated (at most 20 slugs or links)
    calendar   a calendar id (cal-...), a calendar slug OR a calendar link
    user       a username, a user id (usr-...) OR a profile link
    city       a Discover city slug (sf) OR a city link
    category   a Discover category slug (ai) OR a category link

events/search needs a place, given in ONE of three ways: `city`, a point
(`latitude` + `longitude`) or a box (`north`, `south`, `east`, `west`).
Without one the site answers for the caller's own address, which here would
be the server's.
"""
from marshmallow import ValidationError, fields, pre_load, validates_schema

import config
from luma import refs
from schema_fields import BaseSchema, ChoiceField, DateField, Flag, PageField, QueryField, RefField, StrippedString
from scraper_utils.schema import max_length as _max_length

MAX_PAGE = config.LUMA_MAX_PAGE
MAX_QUERY_LENGTH = 100
MAX_TAG_LENGTH = 80
BOX = ("north", "south", "east", "west")


class _Schema(BaseSchema):
    @pre_load
    def empty_is_absent(self, data, **kwargs):
        """An optional param sent empty (`category=`, `page=`: a form that
        sends every field) is the absent param. A required param sent
        empty is still refused."""
        required = {field.data_key or name for name, field in self.fields.items() if field.required}
        return {key: value for key, value in data.items()
                if key in required or not (isinstance(value, str) and not value.strip())}


class EventRefField(RefField):
    resolver = staticmethod(refs.resolve_event)


class EventsRefField(RefField):
    """'evt-..., k5woffsv, <link>' -> the ids and slugs, in order, repeats dropped."""
    resolver = staticmethod(refs.resolve_events)


class CalendarRefField(RefField):
    resolver = staticmethod(refs.resolve_calendar)


class UserRefField(RefField):
    resolver = staticmethod(refs.resolve_user)


class CityRefField(RefField):
    resolver = staticmethod(refs.resolve_city)


class CategoryRefField(RefField):
    resolver = staticmethod(refs.resolve_category)


class DegreesField(fields.Float):
    """A latitude (-90..90) or a longitude (-180..180), optional."""

    def __init__(self, limit, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.limit = limit

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            number = float(str(value).strip())
        except ValueError:
            raise ValidationError("Must be a number of degrees (37.7749).")
        if number != number or not -self.limit <= number <= self.limit:
            raise ValidationError(f"Must be between -{self.limit} and {self.limit}.")
        return number


def _latitude(description):
    return DegreesField(90, metadata={"description": description})


def _longitude(description):
    return DegreesField(180, metadata={"description": description})


def _page_field(rows):
    return PageField(max_page=MAX_PAGE, metadata={"description": f"page number, from 1 (up to {rows} events a page)"})


def _query_field(description):
    return QueryField(required=False, load_default=None, validate=_max_length(MAX_QUERY_LENGTH),
                      metadata={"description": description})


# ---- events --------------------------------------------------------------------------

class EventsSearchSchema(_Schema):
    city = CityRefField(required=False, load_default=None,
                        metadata={"description": "a Luma city slug (sf, nyc, london; see cities/list) or a city link: "
                                                 "events around that city"})
    latitude = _latitude("latitude of a point, with `longitude`: events around it (up to about 70 km)")
    longitude = _longitude("longitude of the point")
    north = _latitude("north edge of a box (a latitude), with `south`, `east` and `west`: events inside it")
    south = _latitude("south edge of the box (a latitude)")
    east = _longitude("east edge of the box (a longitude)")
    west = _longitude("west edge of the box (a longitude)")
    category = CategoryRefField(required=False, load_default=None,
                                metadata={"description": "only events of this category: a slug (ai, tech, food; see "
                                                         "categories/list) or a category link"})
    query = _query_field("keywords: the best matches near the place (up to 50), without further pages")
    page = _page_field(50)

    @validates_schema
    def one_place(self, data, **kwargs):
        point = [name for name in ("latitude", "longitude") if data.get(name) is not None]
        box = [name for name in BOX if data.get(name) is not None]
        ways = sum((bool(data.get("city")), bool(point), bool(box)))
        if ways != 1:
            raise ValidationError("Give the place in one way: `city`, or `latitude` and `longitude`, or the box "
                                  "`north`, `south`, `east` and `west`.", field_name="city")
        if point and len(point) != 2:
            raise ValidationError("`latitude` and `longitude` go together.",
                                  field_name="longitude" if "latitude" in point else "latitude")
        if box and len(box) != 4:
            missing = next(name for name in BOX if name not in box)
            raise ValidationError("A box needs `north`, `south`, `east` and `west`.", field_name=missing)
        if box and data["south"] >= data["north"]:
            raise ValidationError("Must be greater than `south`.", field_name="north")


class EventsDetailsSchema(_Schema):
    event = EventRefField(metadata={"description": "event id (evt-UvVJTTBckZ6kMsc), event slug (k5woffsv) or a luma.com event link"})


class EventsBatchSchema(_Schema):
    events = EventsRefField(metadata={"description": "up to 250 event ids, comma-separated; up to 20 of them may be "
                                                     "event slugs or links"})


# ---- calendars -----------------------------------------------------------------------

class CalendarsDetailsSchema(_Schema):
    calendar = CalendarRefField(metadata={"description": "calendar id (cal-JDvWKGO7IFEV3WY), calendar slug "
                                                         "(marinarunclub) or a luma.com calendar link"})


class CalendarsEventsSchema(_Schema):
    calendar = CalendarRefField(metadata={"description": "calendar id (cal-JDvWKGO7IFEV3WY), calendar slug "
                                                         "(marinarunclub) or a luma.com calendar link"})
    period = ChoiceField(list(refs.PERIODS),
                         metadata={"description": "upcoming (default, soonest first) or past (latest first)"})
    date_from = DateField(metadata={"description": "events starting on or after this day (YYYY-MM-DD, UTC), earliest "
                                                   "first; used instead of `period`"})
    date_to = DateField(metadata={"description": "events starting on or before this day (YYYY-MM-DD, UTC)"})
    tag = StrippedString(required=False, load_default=None, validate=_max_length(MAX_TAG_LENGTH),
                         metadata={"description": "only events with this tag of the calendar: its name (Run) or its id "
                                                  "(calendars/details lists them)"})
    available_only = Flag(metadata={"description": "true: only events that still take registrations"})
    query = _query_field("keywords within the calendar's events")
    page = _page_field(100)

    @validates_schema
    def check(self, data, **kwargs):
        dated = data.get("date_from") or data.get("date_to")
        if dated and data.get("period"):
            raise ValidationError("Use `period`, or `date_from` / `date_to`, not both.", field_name="period")
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise ValidationError("Must be on or after `date_from`.", field_name="date_to")


class CalendarsListSchema(_Schema):
    city = CityRefField(required=False, load_default=None,
                        metadata={"description": "the calendars this city's page features: a city slug (sf) or link"})
    category = CategoryRefField(required=False, load_default=None,
                                metadata={"description": "the calendars this category's page features: a category "
                                                         "slug (ai) or link"})

    @validates_schema
    def one_list(self, data, **kwargs):
        if data.get("city") and data.get("category"):
            raise ValidationError("Give `city` or `category`, not both (neither: the calendars Luma's Discover page "
                                  "features).", field_name="category")


# ---- users ---------------------------------------------------------------------------

class UsersDetailsSchema(_Schema):
    user = UserRefField(metadata={"description": "username (ajaytuniki), user id (usr-4RfJK79d4BBbtk2) or a luma.com profile link"})


class UsersEventsSchema(_Schema):
    user = UserRefField(metadata={"description": "username (ajaytuniki), user id (usr-4RfJK79d4BBbtk2) or a luma.com profile link"})
    period = ChoiceField(list(refs.PERIODS),
                         metadata={"description": "upcoming (default, soonest first) or past (latest first)"})
    page = _page_field(50)


# ---- cities and categories -----------------------------------------------------------

class CitiesListSchema(_Schema):
    pass


class CategoriesListSchema(_Schema):
    pass
