-- Migration: Ajouter content_type et image_count pour support carrousels
-- Appliquer dans Supabase SQL Editor

-- Ajouter content_type aux trips
ALTER TABLE trips ADD COLUMN IF NOT EXISTS content_type VARCHAR(20) DEFAULT 'video';
ALTER TABLE trips ADD COLUMN IF NOT EXISTS image_count INTEGER DEFAULT 0;

-- Ajouter content_type aux cities
ALTER TABLE cities ADD COLUMN IF NOT EXISTS content_type VARCHAR(20) DEFAULT 'video';
ALTER TABLE cities ADD COLUMN IF NOT EXISTS image_count INTEGER DEFAULT 0;

-- Ajouter content_type aux analysis_jobs
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS content_type VARCHAR(20) DEFAULT 'video';
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS image_count INTEGER DEFAULT 0;

-- Index pour requêtes filtrées par type
CREATE INDEX IF NOT EXISTS idx_trips_content_type ON trips(content_type);
CREATE INDEX IF NOT EXISTS idx_cities_content_type ON cities(content_type);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_content_type ON analysis_jobs(content_type);

-- Commentaire
COMMENT ON COLUMN trips.content_type IS 'Type de contenu: video ou carousel';
COMMENT ON COLUMN trips.image_count IS 'Nombre d''images pour les carrousels (0 pour vidéos)';
COMMENT ON COLUMN cities.content_type IS 'Type de contenu: video ou carousel';
COMMENT ON COLUMN cities.image_count IS 'Nombre d''images pour les carrousels (0 pour vidéos)';
COMMENT ON COLUMN analysis_jobs.content_type IS 'Type de contenu: video ou carousel';
COMMENT ON COLUMN analysis_jobs.image_count IS 'Nombre d''images pour les carrousels (0 pour vidéos)';
