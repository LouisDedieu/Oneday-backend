-- Migration: Add city linking for automatic sync
-- This allows trips to automatically sync with their linked cities

-- ============================================================================
-- SPOTS: Link to source highlight
-- ============================================================================

-- Add city_highlight_id to spots (nullable to not break existing data)
ALTER TABLE spots
ADD COLUMN IF NOT EXISTS city_highlight_id UUID REFERENCES city_highlights(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_spots_city_highlight_id ON spots(city_highlight_id);

-- Add source_city_id to spots for direct city reference
ALTER TABLE spots
ADD COLUMN IF NOT EXISTS source_city_id UUID REFERENCES cities(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_spots_source_city_id ON spots(source_city_id);

-- ============================================================================
-- ITINERARY_DAYS: Link to source city for full sync
-- ============================================================================

-- Add linked_city_id to itinerary_days (when a day comes from a saved city)
ALTER TABLE itinerary_days
ADD COLUMN IF NOT EXISTS linked_city_id UUID REFERENCES cities(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_itinerary_days_linked_city_id ON itinerary_days(linked_city_id);
