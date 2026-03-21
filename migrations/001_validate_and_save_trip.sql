-- Migration: Fonction RPC atomique pour valider et sauvegarder un trip
-- Cette fonction effectue syncDestinations + saveTrip en une seule transaction
--
-- Pour appliquer cette migration dans Supabase:
-- 1. Aller dans le Dashboard Supabase > SQL Editor
-- 2. Coller ce script et exécuter

CREATE OR REPLACE FUNCTION validate_and_save_trip(
    p_trip_id UUID,
    p_user_id UUID,
    p_notes TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_invalid_day_ids UUID[];
    v_valid_days RECORD;
    v_active_dest_ids UUID[];
    v_orphan_dest_ids UUID[];
    v_dest RECORD;
    v_days_by_dest JSON;
    v_new_order INT;
BEGIN
    -- ══════════════════════════════════════════════════════════════════════════
    -- PARTIE 1: syncDestinations (nettoyage des données non-validées)
    -- ══════════════════════════════════════════════════════════════════════════

    -- 1. Récupérer les IDs des jours non-validés
    SELECT ARRAY_AGG(id) INTO v_invalid_day_ids
    FROM itinerary_days
    WHERE trip_id = p_trip_id AND validated = FALSE;

    -- 2. Supprimer les spots des jours non-validés
    IF v_invalid_day_ids IS NOT NULL AND array_length(v_invalid_day_ids, 1) > 0 THEN
        DELETE FROM spots WHERE itinerary_day_id = ANY(v_invalid_day_ids);

        -- 3. Supprimer les jours non-validés
        DELETE FROM itinerary_days WHERE id = ANY(v_invalid_day_ids);
    END IF;

    -- 4. Récupérer les destination_ids encore référencés par les jours validés
    SELECT ARRAY_AGG(DISTINCT destination_id) INTO v_active_dest_ids
    FROM itinerary_days
    WHERE trip_id = p_trip_id AND destination_id IS NOT NULL;

    -- 5. Trouver et supprimer les destinations orphelines
    IF v_active_dest_ids IS NULL THEN
        v_active_dest_ids := ARRAY[]::UUID[];
    END IF;

    SELECT ARRAY_AGG(id) INTO v_orphan_dest_ids
    FROM destinations
    WHERE trip_id = p_trip_id AND NOT (id = ANY(v_active_dest_ids));

    IF v_orphan_dest_ids IS NOT NULL AND array_length(v_orphan_dest_ids, 1) > 0 THEN
        DELETE FROM destinations WHERE id = ANY(v_orphan_dest_ids);
    END IF;

    -- 6. Mettre à jour days_spent pour chaque destination restante
    UPDATE destinations d
    SET days_spent = (
        SELECT COUNT(*)
        FROM itinerary_days id
        WHERE id.destination_id = d.id AND id.trip_id = p_trip_id
    )
    WHERE d.trip_id = p_trip_id;

    -- 7. Recalculer visit_order séquentiel (1, 2, 3, ...)
    v_new_order := 0;
    FOR v_dest IN
        SELECT id FROM destinations
        WHERE trip_id = p_trip_id
        ORDER BY visit_order
    LOOP
        v_new_order := v_new_order + 1;
        UPDATE destinations SET visit_order = v_new_order WHERE id = v_dest.id;
    END LOOP;

    -- ══════════════════════════════════════════════════════════════════════════
    -- PARTIE 2: saveTrip (ajout dans user_saved_trips)
    -- ══════════════════════════════════════════════════════════════════════════

    INSERT INTO user_saved_trips (user_id, trip_id, notes)
    VALUES (p_user_id, p_trip_id, p_notes)
    ON CONFLICT (user_id, trip_id) DO UPDATE SET notes = COALESCE(EXCLUDED.notes, user_saved_trips.notes);

    -- ══════════════════════════════════════════════════════════════════════════
    -- RETOUR
    -- ══════════════════════════════════════════════════════════════════════════

    RETURN json_build_object(
        'success', TRUE,
        'synced', TRUE,
        'saved', TRUE
    );

EXCEPTION
    WHEN OTHERS THEN
        -- En cas d'erreur, la transaction est automatiquement rollback
        RAISE EXCEPTION 'validate_and_save_trip failed: %', SQLERRM;
END;
$$;

-- Accorder les permissions pour que l'API puisse appeler la fonction
GRANT EXECUTE ON FUNCTION validate_and_save_trip(UUID, UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION validate_and_save_trip(UUID, UUID, TEXT) TO service_role;

-- Commentaire pour documentation
COMMENT ON FUNCTION validate_and_save_trip IS 'Fonction atomique qui synchronise les destinations (supprime jours/spots non-validés) et sauvegarde le trip pour l''utilisateur. Garantit la cohérence transactionnelle.';
