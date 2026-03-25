-- Migration: Ajouter support pour articles de blog
-- Appliquer dans Supabase SQL Editor

-- Ajouter word_count et estimated_read_time aux trips
ALTER TABLE trips ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS estimated_read_time INTEGER DEFAULT 0;

-- Ajouter word_count et estimated_read_time aux cities
ALTER TABLE cities ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS estimated_read_time INTEGER DEFAULT 0;

-- Ajouter word_count et estimated_read_time aux analysis_jobs
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0;
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS estimated_read_time INTEGER DEFAULT 0;

-- Mettre à jour content_type pour accepter 'blog'
-- (PostgreSQL enum n'est pas utilisé, VARCHAR suffit)

-- Commentaire
COMMENT ON COLUMN trips.word_count IS 'Nombre de mots pour les articles de blog (0 pour vidéos/carrousels)';
COMMENT ON COLUMN trips.estimated_read_time IS 'Temps de lecture estimé en minutes pour les articles de blog';
COMMENT ON COLUMN cities.word_count IS 'Nombre de mots pour les articles de blog (0 pour vidéos/carrousels)';
COMMENT ON COLUMN cities.estimated_read_time IS 'Temps de lecture estimé en minutes pour les articles de blog';
COMMENT ON COLUMN analysis_jobs.word_count IS 'Nombre de mots pour les articles de blog (0 pour vidéos/carrousels)';
COMMENT ON COLUMN analysis_jobs.estimated_read_time IS 'Temps de lecture estimé en minutes pour les articles de blog';
