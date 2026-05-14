"""
populate_db.py — Population de la base RaffineryWatch avec Faker
================================================================
Utilise subprocess + docker exec + psql.
AUCUNE connexion TCP directe (évite les bugs Windows psycopg2/pg8000).

Prérequis :
    pip install faker
    docker compose up -d timescaledb

Utilisation :
    python database/populate_db.py
"""

import os
import random
import sys
import subprocess
from datetime import datetime, timedelta, date

from faker import Faker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONTAINER = os.getenv("CONTAINER", "raffinerie-iot-timescaledb-1")
DB_USER   = os.getenv("DB_USER",   "admin")
DB_NAME   = os.getenv("DB_NAME",   "iotdb")

fake = Faker("fr_FR")
random.seed(42)
Faker.seed(42)

# Volumes cibles
NB_UTILISATEURS = 25
NB_EQUIPEMENTS  = 45
NB_CAPTEURS     = 70
NB_REGLES       = 40
NB_MESURES      = 2000
NB_ALERTES      = 180
NB_MAINTENANCES = 55
NB_AFFECTATIONS = 80

# ---------------------------------------------------------------------------
# Données métier réalistes
# ---------------------------------------------------------------------------
ZONES_DATA = [
    ("DIST-ATM",  "Distillation atmosphérique",   "Unité de distillation principale du brut",     "Secteur A - Bloc 1", 12500.0),
    ("DIST-VIDE", "Distillation sous vide",        "Distillation du résidu atmosphérique",         "Secteur A - Bloc 2",  8200.0),
    ("FCC",       "Craquage catalytique",          "Conversion des fractions lourdes en essences", "Secteur B",          15000.0),
    ("HYDRO",     "Hydrotraitement",               "Désulfuration et hydrogénation",               "Secteur C",           6800.0),
    ("STOCK-BR",  "Stockage brut",                 "Bacs de stockage pétrole brut",                "Secteur D",          45000.0),
    ("STOCK-PF",  "Stockage produits finis",       "Bacs essence, gazole, kérosène",               "Secteur E",          32000.0),
    ("UTIL",      "Utilités et contrôle",          "DCS central, vapeur, eau, électricité",        "Bâtiment central",    1200.0),
    ("CHARG",     "Zone de chargement",            "Quais chargement camions et pipelines",        "Secteur F",           9500.0),
]

TYPES_CAPTEURS_DATA = [
    ("TEMP",  "Température",   "°C",    "Mesure de température des fluides",        -50.0,   700.0),
    ("PRES",  "Pression",      "bar",   "Mesure de pression manométrique",           -1.0,   350.0),
    ("VIB",   "Vibration",     "mm/s",  "Vitesse vibratoire machines tournantes",    0.0,    50.0),
    ("DEBIT", "Débit",         "m3/h",  "Débit volumique des fluides",               0.0,  5000.0),
    ("NIV",   "Niveau",        "%",     "Niveau de remplissage cuves et bacs",       0.0,   100.0),
    ("H2S",   "H2S gazeux",    "ppm",   "Concentration sulfure hydrogène",           0.0,  1000.0),
]

TYPES_EQUIP = [
    "Pompe centrifuge", "Pompe volumétrique", "Compresseur centrifuge",
    "Compresseur a vis", "Echangeur de chaleur", "Colonne de distillation",
    "Four de chauffage", "Reacteur catalytique", "Separateur gaz-liquide",
    "Ballon tampon", "Bac de stockage", "Vanne de regulation", "Filtre",
]

FABRICANTS = [
    "Flowserve", "KSB", "Sulzer", "Atlas Copco", "Alfa Laval",
    "Siemens", "ABB", "Emerson", "Honeywell", "Yokogawa", "Schneider",
]

TOPIC_MAP = {
    "Température": "raffinerie/temp",
    "Pression":    "raffinerie/pression",
    "Vibration":   "raffinerie/vib",
    "Débit":       "raffinerie/debit",
    "Niveau":      "raffinerie/niveau",
    "H2S gazeux":  "raffinerie/h2s",
}

# Valeurs normales d'exploitation par type
PLAGES_NORMALES = {
    "Température": (20.0,  280.0),
    "Pression":    (1.0,   120.0),
    "Vibration":   (0.0,   8.0),
    "Débit":       (10.0,  900.0),
    "Niveau":      (10.0,  90.0),
    "H2S gazeux":  (0.0,   8.0),
}

# Seuils par type (bas_critique, bas_warn, haut_warn, haut_critique)
SEUILS_TYPE = {
    "Température": (-10.0,  5.0,   220.0, 280.0),
    "Pression":    (0.5,    1.0,   130.0, 160.0),
    "Vibration":   (None,   None,  10.0,  20.0),
    "Débit":       (5.0,    15.0,  850.0, 1000.0),
    "Niveau":      (5.0,    10.0,  88.0,  95.0),
    "H2S gazeux":  (None,   None,  10.0,  50.0),
}

DESCRIPTIONS_MAINTENANCE = [
    "Inspection generale et graissage des roulements",
    "Verification des joints et remplacement si uses",
    "Controle vibratoire et alignement laser",
    "Nettoyage des filtres et verification parametres",
    "Remplacement correctif suite a defaillance detectee",
    "Maintenance predictive basee sur analyse vibratoire",
    "Revision generale planifiee annuelle",
    "Inspection reglementaire obligatoire",
]

MOTIFS_AFFECTATION = [
    "Deplacement pour maintenance preventive",
    "Remplacement capteur defectueux",
    "Reorganisation de zone",
    "Ajout point de mesure supplementaire",
    "Mise a niveau firmware",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def randdate(ago_max=365*5, ago_min=0):
    """Date aléatoire dans le passé."""
    d = date.today() - timedelta(days=random.randint(ago_min, ago_max))
    return d

def randts(ago_max_days=30, ago_min_days=0):
    """Timestamp aléatoire (UTC)."""
    start = datetime.utcnow() - timedelta(days=ago_max_days)
    end   = datetime.utcnow() - timedelta(days=ago_min_days)
    delta = max(1, (end - start).total_seconds())
    return start + timedelta(seconds=random.uniform(0, delta))

def progress(label, i, total):
    filled = int(28 * i / total)
    bar    = "█" * filled + "░" * (28 - filled)
    print(f"\r  {label:28s} [{bar}] {i}/{total}", end="", flush=True)
    if i == total:
        print()

def V(val):
    """Formate une valeur Python en littéral SQL sûr."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, float):
        return repr(round(val, 6))
    if isinstance(val, int):
        return str(val)
    if isinstance(val, datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, date):
        return f"'{val.strftime('%Y-%m-%d')}'"
    s = str(val).replace("'", "''")
    return f"'{s}'"

# ---------------------------------------------------------------------------
# Exécution SQL via docker exec psql (pas de TCP)
# ---------------------------------------------------------------------------
def psql(sql, fetch=False):
    """Envoie du SQL au conteneur via docker exec."""
    cmd = ["docker", "exec", "-i", CONTAINER,
           "psql", "-U", DB_USER, "-d", DB_NAME,
           "-v", "ON_ERROR_STOP=1"]
    if fetch:
        cmd += ["-t", "-A", "-F", "\t"]

    proc = subprocess.run(cmd, input=sql.encode("utf-8"), capture_output=True)

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        snippet = sql[:400].replace("\n", " ")
        raise RuntimeError(f"Erreur SQL :\n{err}\nSQL : {snippet}")

    if fetch:
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return []
        return [line.split("\t") for line in out.split("\n") if line.strip()]
    return None

def test_connexion():
    try:
        psql("SELECT 1;")
        return True
    except Exception:
        return False

def fetch_ids(table, order="id"):
    """Retourne tous les IDs d'une table."""
    rows = psql(f"SELECT id FROM {table} ORDER BY {order};", fetch=True)
    return [int(r[0]) for r in rows]

# ---------------------------------------------------------------------------
# 1. UTILISATEURS
# ---------------------------------------------------------------------------
def insert_utilisateurs():
    print(f"\n👤 Insertion de {NB_UTILISATEURS} utilisateurs...")
    used_emails = set()
    used_usernames = set()
    sql_parts = []

    # Comptes fixes
    fixed = [
        ("admin",     "admin@raffinerywatch.io",     "Admin",    "System",   "admin"),
        ("operateur1","op1@raffinerywatch.io",        "Mamadou",  "Diallo",   "operateur"),
        ("lecteur1",  "lecteur1@raffinerywatch.io",   "Fatou",    "Camara",   "lecteur"),
    ]
    for username, email, prenom, nom, role in fixed:
        used_emails.add(email)
        used_usernames.add(username)
        sql_parts.append(f"""
INSERT INTO utilisateurs (username, email, prenom, nom, role, is_active)
VALUES ({V(username)}, {V(email)}, {V(prenom)}, {V(nom)}, {V(role)}, TRUE)
ON CONFLICT (email) DO NOTHING;
""")

    roles_poids = ["admin", "operateur", "operateur", "operateur",
                   "lecteur", "lecteur", "lecteur", "lecteur"]

    for i in range(NB_UTILISATEURS - len(fixed)):
        prenom = fake.first_name()
        nom    = fake.last_name()
        p_asc  = prenom.encode("ascii", errors="ignore").decode().lower().replace(" ", "") or f"user{i}"
        n_asc  = nom.encode("ascii",   errors="ignore").decode().lower().replace(" ", "") or f"op{i}"

        username = f"{p_asc}.{n_asc}"
        suf = 1
        while username in used_usernames:
            username = f"{p_asc}.{n_asc}{suf}"; suf += 1
        used_usernames.add(username)

        email = f"{username}@raffinerywatch.io"
        suf   = 1
        while email in used_emails:
            email = f"{p_asc}.{n_asc}{suf}@raffinerywatch.io"; suf += 1
        used_emails.add(email)

        role = random.choice(roles_poids)
        sql_parts.append(f"""
INSERT INTO utilisateurs (username, email, prenom, nom, role, is_active)
VALUES ({V(username)}, {V(email)}, {V(prenom)}, {V(nom)}, {V(role)}, TRUE)
ON CONFLICT (email) DO NOTHING;
""")
        progress("Utilisateurs", i + 1, NB_UTILISATEURS - len(fixed))

    psql("\n".join(sql_parts))
    ids = fetch_ids("utilisateurs")
    print(f"  ✅ {len(ids)} utilisateurs insérés")
    return ids

# ---------------------------------------------------------------------------
# 2. ZONES
# ---------------------------------------------------------------------------
def insert_zones():
    print("\n📍 Insertion des zones...")
    sql_parts = []
    for code, nom, desc, loc, sup in ZONES_DATA:
        sql_parts.append(f"""
INSERT INTO zones (code, nom, description, localisation, superficie_m2, actif)
VALUES ({V(code)}, {V(nom)}, {V(desc)}, {V(loc)}, {V(sup)}, TRUE)
ON CONFLICT (code) DO NOTHING;
""")
    psql("\n".join(sql_parts))
    rows = psql("SELECT id FROM zones ORDER BY id;", fetch=True)
    ids  = [int(r[0]) for r in rows]
    print(f"  ✅ {len(ids)} zones insérées")
    return ids

# ---------------------------------------------------------------------------
# 3. TYPES DE CAPTEURS
# ---------------------------------------------------------------------------
def insert_types_capteurs():
    print("\n🔬 Insertion des types de capteurs...")
    sql_parts = []
    for code, nom, unite, desc, vmin, vmax in TYPES_CAPTEURS_DATA:
        sql_parts.append(f"""
INSERT INTO types_capteurs (code, nom, unite_mesure, description, valeur_min_physique, valeur_max_physique)
VALUES ({V(code)}, {V(nom)}, {V(unite)}, {V(desc)}, {V(vmin)}, {V(vmax)})
ON CONFLICT (code) DO NOTHING;
""")
    psql("\n".join(sql_parts))
    rows = psql(
        "SELECT id, nom, unite_mesure, valeur_min_physique, valeur_max_physique FROM types_capteurs ORDER BY id;",
        fetch=True
    )
    types = [{"id": int(r[0]), "nom": r[1], "unite": r[2], "vmin": float(r[3]), "vmax": float(r[4])} for r in rows]
    print(f"  ✅ {len(types)} types insérés")
    return types

# ---------------------------------------------------------------------------
# 4. ÉQUIPEMENTS
# ---------------------------------------------------------------------------
def insert_equipements(zone_ids):
    print(f"\n⚙️  Insertion de {NB_EQUIPEMENTS} équipements...")
    prefixes   = ["PUMP", "COMP", "HEX", "COL", "FOUR", "REACT", "SEP", "BAC", "FILT", "VAN"]
    used_codes = set()
    sql_parts  = []

    for i in range(NB_EQUIPEMENTS):
        while True:
            code = f"{random.choice(prefixes)}-{random.randint(100, 999)}"
            if code not in used_codes:
                used_codes.add(code); break

        type_eq = random.choice(TYPES_EQUIP)
        zone_id = random.choice(zone_ids)
        statut  = random.choices(
            ["actif", "en_maintenance", "hors_service"],
            weights=[78, 12, 10]
        )[0]
        d_install = randdate(365*10, 180)
        d_maint   = randdate(365, 30) if random.random() > 0.25 else None

        sql_parts.append(f"""
INSERT INTO equipements
    (code, nom, type_equipement, zone_id, statut,
     fabricant, modele, numero_serie, date_installation, derniere_maintenance, description)
VALUES (
    {V(code)}, {V(f"{type_eq} {code}")}, {V(type_eq)}, {zone_id}, {V(statut)},
    {V(random.choice(FABRICANTS))},
    {V(f"Modele-{random.randint(1000,9999)}")},
    {V(f"SN-{random.randint(10000,99999)}")},
    {V(d_install)}, {V(d_maint)},
    {V(f"Equipement {type_eq} — zone {zone_id}")}
)
ON CONFLICT (code) DO NOTHING;
""")
        progress("Équipements", i + 1, NB_EQUIPEMENTS)

    psql("\n".join(sql_parts))
    ids = fetch_ids("equipements")
    print(f"  ✅ {len(ids)} équipements insérés")
    return ids

# ---------------------------------------------------------------------------
# 5. CAPTEURS
# ---------------------------------------------------------------------------
def insert_capteurs(type_infos, eq_ids, zone_ids):
    print(f"\n📡 Insertion de {NB_CAPTEURS} capteurs...")
    used_codes = set()
    sql_parts  = []

    for i in range(NB_CAPTEURS):
        tinfo  = random.choice(type_infos)
        prefix = tinfo["nom"][:4].upper().replace(" ", "")
        while True:
            code = f"{prefix}-{i+1:03d}"
            if code not in used_codes:
                used_codes.add(code); break

        statut  = random.choices(
            ["actif", "inactif", "maintenance", "en_panne"],
            weights=[68, 12, 12, 8]
        )[0]
        eq_id   = random.choice(eq_ids) if random.random() > 0.08 else None
        zone_id = random.choice(zone_ids) if random.random() > 0.3 else None
        topic   = TOPIC_MAP.get(tinfo["nom"], "raffinerie/divers")

        # Seuils opérationnels (plus restrictifs que la plage physique)
        seuils  = SEUILS_TYPE.get(tinfo["nom"])
        s_min   = round(seuils[0] * random.uniform(0.9, 1.1), 2) if seuils and seuils[0] else None
        s_max   = round(seuils[3] * random.uniform(0.9, 1.1), 2) if seuils and seuils[3] else None
        if s_min and s_max and s_min >= s_max:
            s_min = None  # Sécurité

        sql_parts.append(f"""
INSERT INTO capteurs
    (code, nom, type_capteur_id, equipement_id, zone_id, statut,
     seuil_min, seuil_max, topic_mqtt, firmware_version, date_installation, description)
VALUES (
    {V(code)}, {V(f"Capteur {tinfo['nom']} {code}")},
    {tinfo['id']}, {V(eq_id)}, {V(zone_id)}, {V(statut)},
    {V(s_min)}, {V(s_max)},
    {V(topic)},
    {V(f"v{random.randint(1,3)}.{random.randint(0,9)}")},
    {V(randdate(365*5, 60))},
    {V(f"Capteur {tinfo['nom']} unite {code}")}
)
ON CONFLICT (code) DO NOTHING;
""")
        progress("Capteurs", i + 1, NB_CAPTEURS)

    psql("\n".join(sql_parts))

    rows = psql(
        "SELECT id, code, statut, type_capteur_id FROM capteurs ORDER BY id;",
        fetch=True
    )
    type_by_id = {t["id"]: t for t in type_infos}
    capteurs = [
        {"id": int(r[0]), "code": r[1], "statut": r[2], "type": type_by_id.get(int(r[3]), type_infos[0])}
        for r in rows
    ]
    print(f"  ✅ {len(capteurs)} capteurs insérés")
    return capteurs

# ---------------------------------------------------------------------------
# 6. RÈGLES D'ALERTE
# ---------------------------------------------------------------------------
def insert_regles(capteurs, type_infos, user_ids):
    print(f"\n📋 Insertion de {NB_REGLES} règles d'alerte...")
    sql_parts    = []
    types_regles = ["seuil_haut", "seuil_bas", "valeur_nulle", "derive_anormale"]
    priorites    = ["critique", "haute", "moyenne", "basse"]

    for i in range(NB_REGLES):
        # 60% règles par capteur, 40% par type
        if random.random() < 0.6:
            c = random.choice(capteurs)
            cap_id  = c["id"]
            type_id = None
            tnom    = c["type"]["nom"]
        else:
            cap_id  = None
            t       = random.choice(type_infos)
            type_id = t["id"]
            tnom    = t["nom"]

        type_regle = random.choice(types_regles)
        seuils     = SEUILS_TYPE.get(tnom, (None, None, None, None))
        s_min = s_max = v_seuil = None

        if type_regle == "seuil_haut":
            v_seuil = round((seuils[2] or 100) * random.uniform(0.95, 1.05), 2)
        elif type_regle == "seuil_bas":
            v = seuils[0]
            v_seuil = round(v * random.uniform(0.95, 1.05), 2) if v else None
        elif type_regle == "derive_anormale":
            lo, hi = PLAGES_NORMALES.get(tnom, (0, 100))
            s_min  = round(lo * 0.8, 2)
            s_max  = round(hi * 1.2, 2)

        priorite = random.choices(priorites, weights=[15, 30, 40, 15])[0]
        nom_regle = f"Règle {type_regle.replace('_',' ')} — {tnom}"

        sql_parts.append(f"""
INSERT INTO regles_alerte
    (nom, capteur_id, type_capteur_id, type_regle,
     valeur_seuil, seuil_min, seuil_max, priorite, actif, cree_par)
VALUES (
    {V(nom_regle)}, {V(cap_id)}, {V(type_id)}, {V(type_regle)},
    {V(v_seuil)}, {V(s_min)}, {V(s_max)},
    {V(priorite)}, TRUE, {V(random.choice(user_ids))}
);
""")
        progress("Règles", i + 1, NB_REGLES)

    psql("\n".join(sql_parts))
    ids = fetch_ids("regles_alerte")
    print(f"  ✅ {len(ids)} règles insérées")
    return ids

# ---------------------------------------------------------------------------
# 7. MESURES (hypertable, bulk par lot de 200)
# ---------------------------------------------------------------------------
def insert_mesures(capteurs):
    print(f"\n📊 Insertion de {NB_MESURES} mesures time-series...")
    actifs = [c for c in capteurs if c["statut"] == "actif"]
    if not actifs:
        print("  ⚠️  Aucun capteur actif — 0 mesures insérées.")
        return

    rows = []
    seen = set()
    attempts = 0
    while len(rows) < NB_MESURES and attempts < NB_MESURES * 5:
        attempts += 1
        c   = random.choice(actifs)
        typ = c["type"]["nom"]

        if random.random() < 0.92:
            lo, hi  = PLAGES_NORMALES.get(typ, (0, 100))
            val     = round(random.uniform(lo, hi), 4)
            qualite = "bonne"
        else:
            lo, hi  = c["type"]["vmin"], c["type"]["vmax"]
            val     = round(random.uniform(lo, hi), 4)
            qualite = random.choice(["douteuse", "douteuse", "mauvaise"])

        ts  = randts(30, 0).replace(microsecond=0)
        key = (c["id"], ts)
        if key in seen:
            continue
        seen.add(key)
        rows.append((c["id"], val, ts, qualite))

    BATCH = 200
    nb_done = 0
    for i in range(0, len(rows), BATCH):
        batch  = rows[i:i+BATCH]
        values = ",\n".join([
            f"({r[0]}, {V(r[1])}, {V(r[2])}, {V(r[3])}, 'simulation')"
            for r in batch
        ])
        psql(f"INSERT INTO mesures (capteur_id, valeur, timestamp, qualite, source)\nVALUES\n{values}\nON CONFLICT DO NOTHING;")
        nb_done += len(batch)
        progress("Mesures", min(nb_done, NB_MESURES), NB_MESURES)

    print(f"  ✅ {nb_done} mesures insérées")

# ---------------------------------------------------------------------------
# 8. ALERTES
# ---------------------------------------------------------------------------
def insert_alertes(capteurs, regle_ids, user_ids):
    print(f"\n🔔 Insertion de {NB_ALERTES} alertes...")
    actifs    = [c for c in capteurs if c["statut"] == "actif"]
    priorites = ["critique", "haute", "moyenne", "basse"]
    statuts   = ["ouverte", "acquittee", "resolue", "fausse_alerte"]
    sql_parts = []
    commentaires = [
        "Intervention effectuee, situation normalisee.",
        "Verification sur site, equipement OK.",
        "Action corrective lancee, suivi en cours.",
        "Capteur recalibre, valeurs revenues a la normale.",
        "Faux positif confirme apres inspection visuelle.",
    ]

    for i in range(NB_ALERTES):
        c = random.choice(actifs)
        t = c["type"]

        seuils = SEUILS_TYPE.get(t["nom"], (None, None, None, None))
        hc     = seuils[3]
        val    = round((hc or 100) * random.uniform(1.01, 1.25), 4) if hc else round(random.uniform(50, 200), 4)
        val    = max(t["vmin"], min(t["vmax"], val))

        priorite = random.choices(priorites, weights=[20, 30, 35, 15])[0]
        statut   = random.choices(statuts,   weights=[25, 30, 38,  7])[0]
        msg      = f"Valeur {val:.2f} {t['unite']} hors seuil sur {c['code']}"
        ts_a     = randts(30, 0)
        regle_id = random.choice(regle_ids) if regle_ids else None

        acq_par = ts_acq = ts_res = commentaire = None
        if statut in ("acquittee", "resolue"):
            acq_par    = random.choice(user_ids)
            ts_acq     = ts_a + timedelta(minutes=random.randint(5, 600))
            commentaire = random.choice(commentaires)
        if statut == "resolue":
            ts_res = ts_acq + timedelta(hours=random.randint(1, 48))

        sql_parts.append(f"""
INSERT INTO alertes
    (capteur_id, regle_id, valeur_declencheur, message, priorite, statut,
     acquittee_par, commentaire_acquittement,
     timestamp_alerte, timestamp_acquittement, timestamp_resolution)
VALUES (
    {c['id']}, {V(regle_id)}, {V(val)}, {V(msg)}, {V(priorite)}, {V(statut)},
    {V(acq_par)}, {V(commentaire)},
    {V(ts_a)}, {V(ts_acq)}, {V(ts_res)}
);
""")
        progress("Alertes", i + 1, NB_ALERTES)

    psql("\n".join(sql_parts))
    print(f"  ✅ {len(sql_parts)} alertes insérées")

# ---------------------------------------------------------------------------
# 9. MAINTENANCES
# ---------------------------------------------------------------------------
def insert_maintenances(eq_ids, user_ids):
    print(f"\n🔧 Insertion de {NB_MAINTENANCES} maintenances...")
    eq_actifs = set()
    sql_parts = []
    TYPES_M   = ["preventive", "corrective", "predictive", "amelioratrice"]
    PRIOS     = [1, 2, 2, 3, 3, 3, 4, 5]

    for i in range(NB_MAINTENANCES):
        candidats = [e for e in eq_ids if e not in eq_actifs]
        if not candidats:
            eq_actifs.clear()
            candidats = eq_ids
        eq_id  = random.choice(candidats)
        type_m = random.choice(TYPES_M)
        statut = random.choices(
            ["planifiee", "en_cours", "terminee", "annulee"],
            weights=[22, 8, 62, 8]
        )[0]
        d_plan  = randdate(90, -30)
        d_debut = d_fin = rapport = None

        if statut in ("en_cours", "terminee"):
            d_debut = datetime.combine(d_plan, datetime.min.time()) + timedelta(hours=8)
        if statut == "terminee":
            d_fin   = d_debut + timedelta(hours=random.uniform(2, 72))
            rapport = random.choice([
                "Travaux effectues sans anomalie. Equipement remis en service.",
                "Remplacement piece realise. Test fonctionnel OK.",
                "Inspection terminee. Aucune defaillance detectee.",
            ])
        if statut in ("planifiee", "en_cours"):
            eq_actifs.add(eq_id)

        sql_parts.append(f"""
INSERT INTO maintenances
    (equipement_id, responsable_id, type_maintenance, statut, priorite,
     date_planifiee, date_debut, date_fin, duree_estimee_h,
     description, rapport, cout_estime, cout_reel)
VALUES (
    {eq_id}, {random.choice(user_ids)},
    {V(type_m)}, {V(statut)}, {random.choice(PRIOS)},
    {V(d_plan)}, {V(d_debut)}, {V(d_fin)},
    {V(round(random.uniform(2, 72), 1))},
    {V(random.choice(DESCRIPTIONS_MAINTENANCE))},
    {V(rapport)},
    {V(round(random.uniform(500, 50000), 2))},
    {V(round(random.uniform(400, 60000), 2)) if statut == 'terminee' else 'NULL'}
);
""")
        progress("Maintenances", i + 1, NB_MAINTENANCES)

    psql("\n".join(sql_parts))
    print(f"  ✅ {len(sql_parts)} maintenances insérées")

# ---------------------------------------------------------------------------
# 10. AFFECTATIONS DE CAPTEURS
# ---------------------------------------------------------------------------
def insert_affectations(capteurs, eq_ids, user_ids):
    print(f"\n🔗 Insertion de {NB_AFFECTATIONS} affectations...")
    sql_parts = []

    # Seuls les équipements actifs ou hors_service peuvent recevoir une affectation
    rows_dispo = psql(
        "SELECT id FROM equipements WHERE statut != 'en_maintenance' ORDER BY id;",
        fetch=True
    )
    eq_dispo = [int(r[0]) for r in rows_dispo] or eq_ids

    for i in range(NB_AFFECTATIONS):
        c      = random.choice(capteurs)
        eq_id  = random.choice(eq_dispo)
        d_deb  = randts(365, 30)
        d_fin  = d_deb + timedelta(days=random.randint(10, 300)) if random.random() < 0.7 else None

        sql_parts.append(f"""
INSERT INTO affectations_capteurs
    (capteur_id, equipement_id, date_debut, date_fin, motif, effectue_par)
VALUES (
    {c['id']}, {eq_id},
    {V(d_deb)}, {V(d_fin)},
    {V(random.choice(MOTIFS_AFFECTATION))},
    {V(random.choice(user_ids))}
);
""")
        progress("Affectations", i + 1, NB_AFFECTATIONS)

    psql("\n".join(sql_parts))
    print(f"  ✅ {len(sql_parts)} affectations insérées")

# ---------------------------------------------------------------------------
# RÉSUMÉ FINAL
# ---------------------------------------------------------------------------
def afficher_resume():
    tables = [
        "utilisateurs", "zones", "types_capteurs", "equipements",
        "capteurs", "regles_alerte", "mesures", "alertes",
        "maintenances", "affectations_capteurs",
    ]
    print("\n  📋 Résumé des lignes insérées :")
    total = 0
    for t in tables:
        rows = psql(f"SELECT COUNT(*) FROM {t};", fetch=True)
        n    = int(rows[0][0]) if rows else 0
        total += n
        print(f"     {t:30s} : {n:>5} lignes")
    print(f"     {'─'*36}")
    print(f"     {'TOTAL':30s} : {total:>5} lignes\n")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  RAFFINERYWATCH — Population de la base de données")
    print("=" * 60)
    print(f"  Conteneur : {CONTAINER}")
    print(f"  Base      : {DB_NAME}  |  User : {DB_USER}")
    print()

    print("⏳ Vérification du conteneur Docker...")
    if not test_connexion():
        print(f"\n❌ Conteneur '{CONTAINER}' inaccessible.")
        print("   → docker compose up -d timescaledb")
        print("   → docker ps  (vérifier le nom exact)")
        sys.exit(1)
    print("  ✅ Conteneur accessible\n")

    try:
        user_ids = insert_utilisateurs()
        zone_ids = insert_zones()
        types    = insert_types_capteurs()
        eq_ids   = insert_equipements(zone_ids)
        capteurs = insert_capteurs(types, eq_ids, zone_ids)
        rgl_ids  = insert_regles(capteurs, types, user_ids)
        insert_mesures(capteurs)
        insert_alertes(capteurs, rgl_ids, user_ids)
        insert_maintenances(eq_ids, user_ids)
        insert_affectations(capteurs, eq_ids, user_ids)

        print("\n" + "=" * 60)
        print("  ✅ Population terminée avec succès !")
        print("=" * 60)
        afficher_resume()

    except Exception as e:
        print(f"\n\n❌ Erreur : {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
