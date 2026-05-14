-- =============================================================================
-- RAFFINERYWATCH — Schéma de base de données
-- Base    : TimescaleDB (PostgreSQL 14)  —  base : iotdb
-- Connexion : admin / admin / localhost:5432
-- =============================================================================
-- Ordre de création (respecte les dépendances FK) :
--   1. utilisateurs
--   2. zones
--   3. types_capteurs
--   4. equipements
--   5. capteurs
--   6. regles_alerte
--   7. mesures           (hypertable TimescaleDB)
--   8. alertes
--   9. maintenances
--  10. affectations_capteurs
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- =============================================================================
-- 1. UTILISATEURS — Comptes de la plateforme RaffineryWatch
-- =============================================================================
CREATE TYPE role_utilisateur AS ENUM ('admin', 'operateur', 'lecteur');

CREATE TABLE IF NOT EXISTS utilisateurs (
    id                  SERIAL PRIMARY KEY,
    username            VARCHAR(50)  NOT NULL UNIQUE,
    email               VARCHAR(150) NOT NULL UNIQUE,
    -- hash bcrypt stocké par Django, placeholder ici
    password_hash       VARCHAR(255) NOT NULL DEFAULT '',
    prenom              VARCHAR(80),
    nom                 VARCHAR(80),
    role                role_utilisateur NOT NULL DEFAULT 'lecteur',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    date_inscription    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    derniere_connexion  TIMESTAMPTZ
);

COMMENT ON TABLE utilisateurs IS
    'Comptes utilisateurs RaffineryWatch — sera remplacé par auth_user Django en Phase 2';
COMMENT ON COLUMN utilisateurs.role IS
    'admin : accès total | operateur : lecture + écriture | lecteur : consultation seule';

-- =============================================================================
-- 2. ZONES — Zones géographiques / fonctionnelles de la raffinerie
-- =============================================================================
CREATE TABLE IF NOT EXISTS zones (
    id              SERIAL PRIMARY KEY,
    code            VARCHAR(20)  NOT NULL UNIQUE,
    nom             VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    localisation    VARCHAR(200),
    superficie_m2   NUMERIC(10, 2) CHECK (superficie_m2 > 0),
    actif           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE zones IS 'Zones géographiques et fonctionnelles de la raffinerie';

-- =============================================================================
-- 3. TYPES_CAPTEURS — Catalogue des types de mesures physiques
-- =============================================================================
CREATE TABLE IF NOT EXISTS types_capteurs (
    id                  SERIAL PRIMARY KEY,
    code                VARCHAR(20)  NOT NULL UNIQUE,
    -- Ex : TEMP, PRES, VIB, DEBIT, NIVEAU, H2S
    nom                 VARCHAR(100) NOT NULL UNIQUE,
    unite_mesure        VARCHAR(30)  NOT NULL,
    description         TEXT,
    -- Plage physique possible — toute mesure hors plage est rejetée
    valeur_min_physique NUMERIC(12, 4) NOT NULL,
    valeur_max_physique NUMERIC(12, 4) NOT NULL,
    CONSTRAINT chk_plage_physique
        CHECK (valeur_min_physique < valeur_max_physique),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE types_capteurs IS
    'Catalogue des types de mesures — définit la plage physique admissible';

-- =============================================================================
-- 4. EQUIPEMENTS — Machines et équipements physiques de la raffinerie
-- =============================================================================
CREATE TYPE statut_equipement AS ENUM ('actif', 'en_maintenance', 'hors_service');

CREATE TABLE IF NOT EXISTS equipements (
    id                  SERIAL PRIMARY KEY,
    code                VARCHAR(50)  NOT NULL UNIQUE,
    nom                 VARCHAR(150) NOT NULL,
    type_equipement     VARCHAR(80)  NOT NULL,
    zone_id             INT NOT NULL REFERENCES zones(id) ON DELETE RESTRICT,
    statut              statut_equipement NOT NULL DEFAULT 'actif',
    fabricant           VARCHAR(100),
    modele              VARCHAR(100),
    numero_serie        VARCHAR(100) UNIQUE,
    date_installation   DATE,
    derniere_maintenance DATE,
    description         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE equipements IS
    'Équipements physiques (pompes, vannes, échangeurs…) par zone';

-- =============================================================================
-- 5. CAPTEURS — Capteurs IoT individuels
-- =============================================================================
CREATE TYPE statut_capteur AS ENUM ('actif', 'inactif', 'maintenance', 'en_panne');

CREATE TABLE IF NOT EXISTS capteurs (
    id                  SERIAL PRIMARY KEY,
    code                VARCHAR(50)  NOT NULL UNIQUE,
    nom                 VARCHAR(150) NOT NULL,
    type_capteur_id     INT NOT NULL REFERENCES types_capteurs(id) ON DELETE RESTRICT,
    equipement_id       INT REFERENCES equipements(id) ON DELETE SET NULL,
    zone_id             INT REFERENCES zones(id) ON DELETE SET NULL,
    statut              statut_capteur NOT NULL DEFAULT 'actif',
    -- Seuils d'alerte propres au capteur (complètent les règles globales)
    seuil_min           NUMERIC(12, 4),
    seuil_max           NUMERIC(12, 4),
    CONSTRAINT chk_seuils_capteur
        CHECK (seuil_min IS NULL OR seuil_max IS NULL OR seuil_min < seuil_max),
    topic_mqtt          VARCHAR(200),
    firmware_version    VARCHAR(30),
    date_installation   DATE,
    derniere_calibration DATE,
    description         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_capteurs_type    ON capteurs (type_capteur_id);
CREATE INDEX IF NOT EXISTS idx_capteurs_equip   ON capteurs (equipement_id);
CREATE INDEX IF NOT EXISTS idx_capteurs_zone    ON capteurs (zone_id);
CREATE INDEX IF NOT EXISTS idx_capteurs_statut  ON capteurs (statut);

COMMENT ON TABLE capteurs IS
    'Capteurs IoT — un capteur inactif/en_panne ne peut pas recevoir de mesures';

-- =============================================================================
-- 6. REGLES_ALERTE — Règles de déclenchement d'alertes
-- =============================================================================
CREATE TYPE type_regle    AS ENUM ('seuil_haut', 'seuil_bas', 'valeur_nulle', 'derive_anormale');
CREATE TYPE priorite_enum AS ENUM ('critique', 'haute', 'moyenne', 'basse');

CREATE TABLE IF NOT EXISTS regles_alerte (
    id              SERIAL PRIMARY KEY,
    nom             VARCHAR(150) NOT NULL,
    -- Règle liée à un capteur spécifique OU à un type de capteur (pas les deux)
    capteur_id      INT REFERENCES capteurs(id)       ON DELETE CASCADE,
    type_capteur_id INT REFERENCES types_capteurs(id) ON DELETE CASCADE,
    CONSTRAINT chk_regle_cible
        CHECK (
            (capteur_id IS NOT NULL AND type_capteur_id IS NULL)
            OR
            (capteur_id IS NULL AND type_capteur_id IS NOT NULL)
        ),
    type_regle      type_regle    NOT NULL,
    valeur_seuil    NUMERIC(12, 4),
    -- Pour derive_anormale : plage normale
    seuil_min       NUMERIC(12, 4),
    seuil_max       NUMERIC(12, 4),
    CONSTRAINT chk_regle_seuils
        CHECK (seuil_min IS NULL OR seuil_max IS NULL OR seuil_min < seuil_max),
    priorite        priorite_enum NOT NULL DEFAULT 'moyenne',
    actif           BOOLEAN NOT NULL DEFAULT TRUE,
    description     TEXT,
    cree_par        INT REFERENCES utilisateurs(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_regles_capteur ON regles_alerte (capteur_id) WHERE capteur_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_regles_type    ON regles_alerte (type_capteur_id) WHERE type_capteur_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_regles_actif   ON regles_alerte (actif) WHERE actif = TRUE;

COMMENT ON TABLE regles_alerte IS
    'Règles d''alerte par capteur ou par type — seuil_min doit être < seuil_max';

-- =============================================================================
-- 7. MESURES — Données time-series (hypertable TimescaleDB)
-- =============================================================================
CREATE TYPE qualite_mesure AS ENUM ('bonne', 'douteuse', 'mauvaise');

CREATE TABLE IF NOT EXISTS mesures (
    id          BIGSERIAL,
    capteur_id  INT NOT NULL REFERENCES capteurs(id) ON DELETE CASCADE,
    valeur      NUMERIC(12, 4) NOT NULL,
    timestamp   TIMESTAMPTZ   NOT NULL,
    qualite     qualite_mesure NOT NULL DEFAULT 'bonne',
    source      TEXT NOT NULL DEFAULT 'mqtt',
    -- Contrainte métier : pas deux mesures au même timestamp pour un capteur
    CONSTRAINT uq_mesure_capteur_ts UNIQUE (capteur_id, timestamp),
    PRIMARY KEY (id, timestamp)   -- requis par TimescaleDB
);

-- Conversion en hypertable (partitionnement par timestamp)
SELECT create_hypertable('mesures', 'timestamp', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_mesures_capteur_ts
    ON mesures (capteur_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mesures_qualite
    ON mesures (qualite) WHERE qualite != 'bonne';

COMMENT ON TABLE mesures IS
    'Hypertable TimescaleDB — données brutes des capteurs IoT';

-- =============================================================================
-- 8. ALERTES — Alertes générées lors du dépassement de règles
-- =============================================================================
CREATE TYPE statut_alerte AS ENUM ('ouverte', 'acquittee', 'resolue', 'fausse_alerte');

CREATE TABLE IF NOT EXISTS alertes (
    id                      SERIAL PRIMARY KEY,
    capteur_id              INT NOT NULL REFERENCES capteurs(id) ON DELETE CASCADE,
    regle_id                INT REFERENCES regles_alerte(id) ON DELETE SET NULL,
    valeur_declencheur      NUMERIC(12, 4),
    message                 TEXT NOT NULL,
    priorite                priorite_enum NOT NULL DEFAULT 'moyenne',
    statut                  statut_alerte NOT NULL DEFAULT 'ouverte',
    -- Opérateur qui a acquitté (NULL si pas encore acquittée)
    acquittee_par           INT REFERENCES utilisateurs(id) ON DELETE SET NULL,
    commentaire_acquittement TEXT,
    timestamp_alerte        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_acquittement  TIMESTAMPTZ,
    timestamp_resolution    TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Contrainte : si acquittée, l'opérateur doit être renseigné
    CONSTRAINT chk_acquittement
        CHECK (statut NOT IN ('acquittee', 'resolue') OR acquittee_par IS NOT NULL),
    -- Contrainte : timestamp acquittement obligatoire si acquittée
    CONSTRAINT chk_ts_acquittement
        CHECK (statut NOT IN ('acquittee', 'resolue') OR timestamp_acquittement IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_alertes_capteur
    ON alertes (capteur_id, timestamp_alerte DESC);
CREATE INDEX IF NOT EXISTS idx_alertes_statut_ouvert
    ON alertes (statut) WHERE statut = 'ouverte';
CREATE INDEX IF NOT EXISTS idx_alertes_priorite
    ON alertes (priorite, statut);

COMMENT ON TABLE alertes IS
    'Alertes générées par violation de règles — une alerte acquittée ne peut pas l''être à nouveau';

-- =============================================================================
-- 9. MAINTENANCES — Planification et suivi des maintenances
-- =============================================================================
CREATE TYPE type_maintenance   AS ENUM ('preventive', 'corrective', 'predictive', 'amelioratrice');
CREATE TYPE statut_maintenance AS ENUM ('planifiee', 'en_cours', 'terminee', 'annulee');

CREATE TABLE IF NOT EXISTS maintenances (
    id                  SERIAL PRIMARY KEY,
    equipement_id       INT NOT NULL REFERENCES equipements(id) ON DELETE CASCADE,
    responsable_id      INT NOT NULL REFERENCES utilisateurs(id) ON DELETE RESTRICT,
    type_maintenance    type_maintenance   NOT NULL,
    statut              statut_maintenance NOT NULL DEFAULT 'planifiee',
    -- 1 = critique, 5 = basse priorité
    priorite            SMALLINT NOT NULL DEFAULT 3
        CHECK (priorite BETWEEN 1 AND 5),
    date_planifiee      DATE NOT NULL,
    date_debut          TIMESTAMPTZ,
    date_fin            TIMESTAMPTZ,
    duree_estimee_h     NUMERIC(5, 1) CHECK (duree_estimee_h > 0),
    description         TEXT NOT NULL,
    rapport             TEXT,
    cout_estime         NUMERIC(12, 2) CHECK (cout_estime >= 0),
    cout_reel           NUMERIC(12, 2) CHECK (cout_reel >= 0),
    CONSTRAINT chk_dates_maintenance
        CHECK (date_fin IS NULL OR date_debut IS NULL OR date_fin >= date_debut),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_maintenances_equip
    ON maintenances (equipement_id, date_planifiee DESC);
CREATE INDEX IF NOT EXISTS idx_maintenances_actif
    ON maintenances (statut) WHERE statut IN ('planifiee', 'en_cours');

COMMENT ON TABLE maintenances IS
    'Maintenances planifiées/correctives/prédictives par équipement';

-- =============================================================================
-- 10. AFFECTATIONS_CAPTEURS — Historique des affectations + changements de config
-- =============================================================================
CREATE TABLE IF NOT EXISTS affectations_capteurs (
    id              SERIAL PRIMARY KEY,
    capteur_id      INT NOT NULL REFERENCES capteurs(id) ON DELETE CASCADE,
    equipement_id   INT NOT NULL REFERENCES equipements(id) ON DELETE CASCADE,
    date_debut      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    date_fin        TIMESTAMPTZ,
    motif           VARCHAR(200),
    effectue_par    INT REFERENCES utilisateurs(id) ON DELETE SET NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dates_affectation
        CHECK (date_fin IS NULL OR date_fin > date_debut)
);

CREATE INDEX IF NOT EXISTS idx_affectations_capteur
    ON affectations_capteurs (capteur_id, date_debut DESC);
CREATE INDEX IF NOT EXISTS idx_affectations_equip
    ON affectations_capteurs (equipement_id, date_debut DESC);

COMMENT ON TABLE affectations_capteurs IS
    'Historique des affectations de capteurs aux équipements';

-- =============================================================================
-- TRIGGERS — updated_at automatique
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'zones','equipements','capteurs','regles_alerte','maintenances'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_updated_at_%s
             BEFORE UPDATE ON %s
             FOR EACH ROW EXECUTE FUNCTION fn_updated_at()',
            t, t
        );
    END LOOP;
END;
$$;

-- =============================================================================
-- TRIGGER — Bloquer les mesures sur capteur non actif
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_check_capteur_actif()
RETURNS TRIGGER AS $$
DECLARE s statut_capteur;
BEGIN
    SELECT statut INTO s FROM capteurs WHERE id = NEW.capteur_id;
    IF s != 'actif' THEN
        RAISE EXCEPTION
            'Capteur % non actif (statut: %). Mesure refusée.',
            NEW.capteur_id, s;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_mesure_capteur_actif
BEFORE INSERT ON mesures
FOR EACH ROW EXECUTE FUNCTION fn_check_capteur_actif();

-- =============================================================================
-- TRIGGER — Rejeter les mesures hors plage physique
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_check_plage_physique()
RETURNS TRIGGER AS $$
DECLARE
    v_min NUMERIC; v_max NUMERIC; v_nom TEXT;
BEGIN
    SELECT tc.valeur_min_physique, tc.valeur_max_physique, tc.nom
    INTO v_min, v_max, v_nom
    FROM capteurs c
    JOIN types_capteurs tc ON c.type_capteur_id = tc.id
    WHERE c.id = NEW.capteur_id;

    IF NEW.valeur < v_min OR NEW.valeur > v_max THEN
        RAISE EXCEPTION
            'Mesure % hors plage physique [%, %] pour type "%" — rejetée.',
            NEW.valeur, v_min, v_max, v_nom;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_mesure_plage_physique
BEFORE INSERT ON mesures
FOR EACH ROW EXECUTE FUNCTION fn_check_plage_physique();

-- =============================================================================
-- TRIGGER — Bloquer affectation sur équipement en maintenance
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_check_equip_disponible()
RETURNS TRIGGER AS $$
DECLARE s statut_equipement;
BEGIN
    SELECT statut INTO s FROM equipements WHERE id = NEW.equipement_id;
    IF s = 'en_maintenance' THEN
        RAISE EXCEPTION
            'Équipement % est en maintenance — affectation impossible.',
            NEW.equipement_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_affectation_equip_dispo
BEFORE INSERT ON affectations_capteurs
FOR EACH ROW EXECUTE FUNCTION fn_check_equip_disponible();

-- =============================================================================
-- TRIGGER — Bloquer double maintenance planifiée/en_cours sur même équipement
-- =============================================================================
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
            'Équipement % a déjà une maintenance active — impossible d''en créer une autre.',
            NEW.equipement_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_double_maintenance
BEFORE INSERT ON maintenances
FOR EACH ROW EXECUTE FUNCTION fn_check_double_maintenance();

-- =============================================================================
-- TRIGGER — Bloquer la ré-acquittement d'une alerte déjà acquittée
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_check_acquittement()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.statut IN ('acquittee', 'resolue')
       AND NEW.statut = 'acquittee'
       AND NEW.acquittee_par IS NOT NULL THEN
        RAISE EXCEPTION
            'Alerte % déjà acquittée — impossible de l''acquitter à nouveau.',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_alerte_acquittement
BEFORE UPDATE ON alertes
FOR EACH ROW EXECUTE FUNCTION fn_check_acquittement();

-- =============================================================================
-- VUES UTILITAIRES
-- =============================================================================

-- Vue : état temps réel des capteurs
CREATE OR REPLACE VIEW v_capteurs_etat AS
SELECT
    c.id, c.code, c.nom AS capteur_nom, c.statut,
    c.seuil_min, c.seuil_max,
    tc.nom AS type_mesure, tc.unite_mesure,
    e.code AS equip_code, e.nom AS equip_nom, e.statut AS equip_statut,
    z.code AS zone_code, z.nom AS zone_nom,
    lm.valeur AS derniere_valeur,
    lm.timestamp AS dernier_timestamp,
    lm.qualite AS derniere_qualite
FROM capteurs c
JOIN types_capteurs tc ON c.type_capteur_id = tc.id
LEFT JOIN equipements e ON c.equipement_id = e.id
LEFT JOIN zones z ON COALESCE(c.zone_id, e.zone_id) = z.id
LEFT JOIN LATERAL (
    SELECT valeur, timestamp, qualite
    FROM mesures WHERE capteur_id = c.id
    ORDER BY timestamp DESC LIMIT 1
) lm ON TRUE;

COMMENT ON VIEW v_capteurs_etat IS
    'État temps réel des capteurs avec leur dernière mesure';

-- Vue : alertes ouvertes enrichies
CREATE OR REPLACE VIEW v_alertes_ouvertes AS
SELECT
    a.id, a.priorite, a.valeur_declencheur, a.message,
    a.timestamp_alerte,
    EXTRACT(EPOCH FROM (NOW() - a.timestamp_alerte))/60 AS duree_minutes,
    c.code AS capteur_code, c.nom AS capteur_nom,
    tc.nom AS type_mesure, tc.unite_mesure,
    z.nom AS zone_nom,
    r.nom AS regle_nom
FROM alertes a
JOIN capteurs c ON a.capteur_id = c.id
JOIN types_capteurs tc ON c.type_capteur_id = tc.id
LEFT JOIN zones z ON COALESCE(c.zone_id, (SELECT zone_id FROM equipements WHERE id = c.equipement_id)) = z.id
LEFT JOIN regles_alerte r ON a.regle_id = r.id
WHERE a.statut = 'ouverte'
ORDER BY
    CASE a.priorite WHEN 'critique' THEN 1 WHEN 'haute' THEN 2 WHEN 'moyenne' THEN 3 ELSE 4 END,
    a.timestamp_alerte ASC;

COMMENT ON VIEW v_alertes_ouvertes IS
    'Alertes ouvertes triées par priorité puis ancienneté';

-- Vue : KPIs dashboard
CREATE OR REPLACE VIEW v_kpis AS
SELECT
    (SELECT COUNT(*) FROM capteurs WHERE statut = 'actif')                        AS capteurs_actifs,
    (SELECT COUNT(*) FROM capteurs WHERE statut = 'en_panne')                     AS capteurs_en_panne,
    (SELECT COUNT(*) FROM alertes  WHERE statut = 'ouverte')                      AS alertes_ouvertes,
    (SELECT COUNT(*) FROM alertes  WHERE statut = 'ouverte' AND priorite = 'critique') AS alertes_critiques,
    (SELECT COUNT(*) FROM maintenances WHERE statut = 'en_cours')                 AS maintenances_en_cours,
    (SELECT COUNT(*) FROM maintenances WHERE statut = 'planifiee'
        AND date_planifiee <= CURRENT_DATE + INTERVAL '7 days')                   AS maintenances_semaine,
    (SELECT COUNT(*) FROM mesures
        WHERE timestamp > NOW() - INTERVAL '1 hour')                              AS mesures_derniere_heure,
    (SELECT COUNT(*) FROM equipements WHERE statut = 'en_maintenance')            AS equips_en_maintenance;

COMMENT ON VIEW v_kpis IS 'KPIs agrégés pour le dashboard temps réel';

-- =============================================================================
-- FIN DU SCHÉMA RAFFINERYWATCH
-- =============================================================================
