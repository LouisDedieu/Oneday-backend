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
