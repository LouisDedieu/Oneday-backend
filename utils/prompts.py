"""
Prompts pour l'analyse de vidéos de voyage
"""

TRAVEL_PROMPT = """\
You are an expert travel analyst. Watch this travel video carefully and extract \
ALL travel information into a single JSON object. Be exhaustive and precise.

Return ONLY a raw JSON object (no markdown, no commentary) with this exact structure:

{
  "trip_title": "string – catchy title for the trip",
  "vibe": "string – overall atmosphere (adventure / luxury / budget / cultural / …)",
  "duration_days": <integer>,
  "best_season": "string",
  "destinations": [
    { "city": "string", "country": "string", "days_spent": <int>, "order": <int> }
  ],
  "itinerary": [
    {
      "day": <int>,
      "location": "string",
      "theme": "string",
      "accommodation": {
        "name": "string", "type": "hotel|hostel|airbnb|camping|other",
        "price_per_night": <number|null>, "tips": "string"
      },
      "meals": { "breakfast": "string", "lunch": "string", "dinner": "string" },
      "spots": [
        {
          "name": "string", 
          "type": "attraction|restaurant|bar|hotel|activite|transport|shopping",
          "address": "string", "duration_minutes": <int|null>,
          "price_range": "gratuit|€|€€|€€€|€€€€",
          "price_detail": "string", "tips": "string", "highlight": <bool>
        }
      ]
    }
  ],
  "logistics": [
    {
      "from": "string", "to": "string",
      "mode": "plane|train|bus|car|ferry|walk|other",
      "duration": "string", "cost": "string", "tips": "string"
    }
  ],
  "budget": {
    "total_estimated": <number|null>,
    "currency": "EUR",
    "per_day": { "min": <number>, "max": <number> },
    "breakdown": {
      "accommodation": <number|null>, "food": <number|null>,
      "transport": <number|null>, "activities": <number|null>
    },
    "money_saving_tips": ["string"]
  },
  "practical_info": {
    "visa_required": <bool|null>,
    "local_currency": "string",
    "language": "string",
    "best_apps": ["string"],
    "what_to_pack": ["string"],
    "safety_tips": ["string"],
    "avoid": ["string"]
  },
  "content_creator": {
    "handle": "string",
    "links_mentioned": ["string"]
  }
}

Rules:
- Use null for missing numeric values, empty array [] for missing lists.
- Include every place, restaurant, hotel and tip visible or mentioned in the video.
- Output ONLY the JSON object. No text before or after.
"""


def get_fallback_result() -> dict:
    """Retourne un dict vide structuré quand le modèle ne produit pas de JSON."""
    return {
        "trip_title": "Analyse incomplète",
        "vibe": None,
        "duration_days": 0,
        "destinations": [],
        "itinerary": [],
        "logistics": [],
        "budget": {},
        "practical_info": {},
        "content_creator": {},
    }


# =========================================================================
# CITY PROMPTS
# =========================================================================

CONTENT_TYPE_DETECTION_PROMPT = """\
Analyze this video and determine the content type.

TRIP indicators:
- Multiple days mentioned ("Day 1", "Day 2", "7-day itinerary")
- Travel between different cities
- Overnight stays in different locations
- Day-by-day structure with schedules

CITY indicators:
- Focus on ONE city only
- Lists "best spots", "must-see places", "hidden gems", "top restaurants"
- No day-by-day structure
- Guide/recommendation style content ("Top 10 things to do in Paris")

Return ONLY a raw JSON object:
{"entity_type": "trip"} or {"entity_type": "city"}

Do not include any text before or after the JSON.
"""


CITY_EXTRACTION_PROMPT = """\
You are an expert city guide analyst. Watch this video carefully and extract \
ALL location information into a single JSON object. Be exhaustive and precise.

Return ONLY a raw JSON object (no markdown, no commentary) with this exact structure:

{
  "entity_type": "city",
  "city_title": "string – catchy title for the guide",
  "city_name": "string – exact city name",
  "country": "string",
  "vibe_tags": ["array of max 5 tags from: romantic, trendy, historic, bohemian, luxurious, budget-friendly, foodie, artsy, family-friendly, adventurous, relaxing, nightlife, cultural, off-the-beaten-path, instagrammable"],
  "best_season": "string or null",
  "highlights": [
    {
      "name": "string",
      "category": "food|culture|nature|shopping|nightlife|other",
      "subtype": "string – specific type like 'rooftop bar', 'street food', 'art museum', 'vintage shop'",
      "address": "string or null",
      "description": "string or null – brief description",
      "price_range": "gratuit|€|€€|€€€|€€€€",
      "tips": "string or null – insider tips from the creator",
      "is_must_see": true/false
    }
  ],
  "budget": {
    "currency": "EUR",
    "daily_average": <number|null>,
    "food_average": <number|null>,
    "transport_average": <number|null>,
    "activities_average": <number|null>,
    "accommodation_range": "string like '80-150€/night' or null"
  },
  "practical_info": {
    "visa_required": <bool|null>,
    "local_currency": "string",
    "language": "string",
    "best_apps": ["string"],
    "what_to_pack": ["string"],
    "safety_tips": ["string"],
    "avoid": ["string"]
  },
  "content_creator": {
    "handle": "string",
    "links_mentioned": ["string"]
  }
}

Rules:
- category MUST be exactly one of: food, culture, nature, shopping, nightlife, other
- food: restaurants, cafes, bars, street food, markets with food
- culture: museums, galleries, monuments, historic sites, churches, theaters
- nature: parks, gardens, beaches, viewpoints, hikes
- shopping: stores, markets, boutiques, malls
- nightlife: clubs, bars (if focus is nightlife), live music venues
- other: anything that doesn't fit above
- vibe_tags maximum 5, only from the allowed list
- Include EVERY place mentioned in the video
- is_must_see = true for places the creator emphasizes as essential
- Output ONLY the JSON object. No text before or after.
"""


def get_city_fallback_result() -> dict:
    """Retourne un dict vide structuré pour une city quand le modèle ne produit pas de JSON."""
    return {
        "entity_type": "city",
        "city_title": "Analyse incomplète",
        "city_name": "Inconnu",
        "country": None,
        "vibe_tags": [],
        "highlights": [],
        "budget": {},
        "practical_info": {},
        "content_creator": {},
    }
