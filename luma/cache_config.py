"""Cache TTL per /luma/* surface (cache.py, keyed on the validated params)."""
from datetime import timedelta

# events/search pages: new events are published all day, and each row
# carries a guest count and what is left of its tickets
EVENTS_CACHE = timedelta(minutes=15)
# events/details and events/batch: the guest count, the tickets left and
# the registration status move through the day
EVENT_CACHE = timedelta(minutes=15)
# calendars/events and users/events pages: the same rows as a search page
LIST_CACHE = timedelta(minutes=15)
# calendars/details: the calendar's own text changes rarely; its follower
# and upcoming-event counts move a little each hour
CALENDAR_CACHE = timedelta(hours=1)
# calendars/list: the calendars a city, a category or the Discover page
# features are picked by hand and change over days
CALENDARS_LIST_CACHE = timedelta(hours=6)
# users/details: a profile's text changes rarely, its event counts daily
USER_CACHE = timedelta(hours=6)
# cities/list, categories/list and a single city: the lists change when the
# site adds a city or a category; the event counts in them move daily
PLACES_CACHE = timedelta(hours=12)
# the cursor that opens page N of a list (kept so a client walking a list
# pays one upstream call a page). A cursor is a position (start time and
# event id), so it stays right as events are added; an hour bounds how long
# a page boundary can lag behind a past list that gained events
CURSOR_CACHE = timedelta(hours=1)
