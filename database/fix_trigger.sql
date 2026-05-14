-- Fix du trigger double maintenance + nettoyage pour re-run
DELETE FROM affectations_capteurs;
DELETE FROM maintenances;

CREATE OR REPLACE FUNCTION fn_check_double_maintenance()
RETURNS TRIGGER AS $$
DECLARE nb INT;
BEGIN
    SELECT COUNT(*) INTO nb
    FROM maintenances
    WHERE equipement_id = NEW.equipement_id
      AND statut IN ('planifiee', 'en_cours')
      AND id != COALESCE(NEW.id, -1);
    -- On bloque uniquement si la nouvelle maintenance est aussi active
    IF nb > 0 AND NEW.statut IN ('planifiee', 'en_cours') THEN
        RAISE EXCEPTION
            'Equipement % a deja une maintenance active.', NEW.equipement_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
