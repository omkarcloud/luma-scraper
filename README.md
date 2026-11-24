# 🚀 Luma Scraper

Luma Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Luma event data for free.

## ✨ What Can I Get?

- 🎟️ **60,000+ public event and calendar pages** — time, venue, ticket prices, guest count, hosts, description and the full registration form
- 🌍 **Upcoming events in 87 cities and 12 categories** — 3,200+ in AI and 3,000+ in tech, by city, map point, box or keyword
- 🏢 **Organizer calendars, like Google DeepMind's 31,000+ followers** — website, socials, location, tags, past and upcoming events
- 👤 **Hosts and featured guests** — name, bio, website, LinkedIn, X, Instagram, events hosted and attended

## 🎟️ Example: A Full Luma Event

```json
{
  "id": "evt-mgs8akIoPXqIV1l",
  "name": "Agentic PM Kickoff w/ PostHog, Linear, Supabase & Dreambase",
  "link": "https://luma.com/posthog-tpmz",
  "start_at": "2026-09-10T01:00:00Z",
  "end_at": "2026-09-10T04:00:00Z",
  "timezone": "America/Los_Angeles",
  "description": "As software drives itself, what happens to the PM role?…",
  "categories": [{ "slug": "ai", "name": "AI", "link": "https://luma.com/ai" }],
  "registration_status": "waitlist",
  "guest_count": 836,
  "tickets": { "is_free": true, "is_sold_out": true, "is_approval_required": true, "spots_remaining": 0 },
  "ticket_types": [{ "name": "Standard", "type": "free", "is_approval_required": true, "guest_count": 836 }],
  "registration": {
    "questions": [
      { "label": "What company do you work for?", "type": "company", "is_required": true },
      { "label": "What is your LinkedIn profile?", "type": "linkedin", "is_required": true }
    ]
  },
  "location": { "venue": "2360 3rd St", "address": "2360 3rd St, San Francisco, CA 94107, USA", "neighborhood": "Dogpatch", "city": "San Francisco", "country": "United States", "latitude": 37.7597267, "longitude": -122.3888066 },
  "calendar": { "id": "cal-qJCKF7ct5XX3pwB", "name": "PostHog", "link": "https://luma.com/posthog", "website": "https://posthog.com", "social": { "x": "https://x.com/posthog", "linkedin": "https://linkedin.com/company/posthog" } },
  "hosts": [
    { "name": "Supabase", "link": "https://luma.com/user/Supabase", "website": "https://supabase.com/", "social": { "x": "https://x.com/supabase", "linkedin": "https://linkedin.com/company/31546644" } },
    { "name": "Linear", "link": "https://luma.com/user/linear", "website": "https://linear.app", "social": { "x": "https://x.com/linear", "linkedin": "https://linkedin.com/company/linearapp" } }
  ],
  "organizer_website": "https://posthog.com"
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Luma Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/luma-scraper
cd luma-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

No proxy needed: Luma answers your own connection.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/events/details?event=posthog-tpmz"
```

```json
{
  "id": "evt-mgs8akIoPXqIV1l",
  "name": "Agentic PM Kickoff w/ PostHog, Linear, Supabase & Dreambase",
  "slug": "posthog-tpmz",
  "link": "https://luma.com/posthog-tpmz",
  "start_at": "2026-09-10T01:00:00Z",
  "end_at": "2026-09-10T04:00:00Z",
  "timezone": "America/Los_Angeles",
  "registration_status": "waitlist",
  "guest_count": 836,
  "tickets": { "is_free": true, "is_sold_out": true, "is_approval_required": true },
  "location": {
    "venue": "2360 3rd St",
    "address": "2360 3rd St, San Francisco, CA 94107, USA",
    "city": "San Francisco",
    "latitude": 37.7597267,
    "longitude": -122.3888066
  },
  "calendar": { "name": "PostHog", "link": "https://luma.com/posthog", "website": "https://posthog.com" },
  "hosts": [{ "name": "Supabase", "website": "https://supabase.com/" }, { "name": "Linear", "website": "https://linear.app" }],
  "organizer_website": "https://posthog.com"
}
```

All 10 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

10 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Event Details | `/events/details` | One full event with tickets, hosts, guests and registration form (website contacts: hosted API only) |
| Search Events | `/events/search` | Events by city, map point, box, category or keyword, 50 per page (website contacts: hosted API only) |
| Bulk Events | `/events/batch` | Up to 250 events in a single call (website contacts: hosted API only) |
| Calendar Details | `/calendars/details` | An organizer's website, socials, followers, location and tags (website contacts: hosted API only) |
| Calendar Events | `/calendars/events` | A calendar's upcoming or past events, by date, tag or keyword (website contacts: hosted API only) |
| Featured Calendars | `/calendars/list` | The calendars Luma features for a city or category (website contacts: hosted API only) |
| User Details | `/users/details` | A host's name, bio, website, socials and event counts (website contacts: hosted API only) |
| User Events | `/users/events` | The upcoming or past events a host runs (website contacts: hosted API only) |
| Cities | `/cities/list` | All 87 Luma cities with slugs, coordinates and event counts |
| Categories | `/categories/list` | All 12 categories with upcoming events and subscribers |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-luma-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-luma-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-luma-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/events/details",
    params={"event": "posthog-tpmz"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Luma Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/luma-scraper-github-whatsapp)

[![Ask Us by Email about Luma Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Luma%20Scraper%20API&body=I%20need%20help%20using%20the%20Luma%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/luma-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/luma-scraper)
