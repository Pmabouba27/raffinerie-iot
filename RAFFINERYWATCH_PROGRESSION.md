# RaffineryWatch — Journal de progression

Plateforme de surveillance IoT industrielle sur pipeline raffinerie existant.

---

## Stack IoT existante (Docker Compose)

| Service | Image | Port |
|---------|-------|------|
| Mosquitto (MQTT) | eclipse-mosquitto:2 | 1883 |
| Zookeeper | confluentinc/cp-zookeeper:7.3.0 | 2181 |
| Kafka | confluentinc/cp-kafka:7.3.0 | 9092 |
| Spark Master + Worker | apache/spark:3.5.0 | 7077 / 8081 |
| MinIO | minio/minio:latest | 9002 / 9001 |
| TimescaleDB | timescale/timescaledb:latest-pg14 | 5432 |
| Grafana | grafana/grafana:latest | 3000 |

**Conteneur TimescaleDB :** `raffinerie-iot-timescaledb-1`  
**Connexion DB :** `admin / admin / iotdb`

---

## ⚠️ Note technique importante (Windows)

`psycopg2` et `pg8000` échouent tous les deux sur ce poste Windows à cause de `libpq` qui lit des chemins système contenant des caractères accentués.

**→ Toujours utiliser `subprocess + docker exec psql` pour les scripts Python qui touchent la base.**  
**→ Ne jamais tenter de connexion TCP directe depuis Python sur ce poste.**

---

## PHASE 1 — Base de données ✅ TERMINÉE

### Fichiers créés

| Fichier | Description |
|---------|-------------|
| `database/schema.sql` | Schéma complet : tables, triggers, vues |
| `database/populate_db.py` | Script Faker via subprocess+docker exec |
| `database/fix_trigger.sql` | Correctif trigger double maintenance |

### Les 10 entités

| Table | Rôle | Lignes |
|-------|------|--------|
| `utilisateurs` | Comptes app (admin / operateur / lecteur) | 25 |
| `zones` | Zones géographiques de la raffinerie | 8 |
| `types_capteurs` | Catalogue des types de mesures physiques | 6 |
| `equipements` | Machines physiques par zone | 45 |
| `capteurs` | Capteurs IoT avec seuils | 70 |
| `regles_alerte` | Règles de déclenchement d'alertes | 40+ |
| `mesures` | **Hypertable TimescaleDB** — time-series | 2000+ |
| `alertes` | Alertes générées + acquittement | 180+ |
| `maintenances` | Planning et suivi des maintenances | 55 |
| `affectations_capteurs` | Historique des affectations | 80 |

### Contraintes métier implémentées (triggers SQL)

| Trigger | Règle |
|---------|-------|
| `trg_mesure_capteur_actif` | Un capteur inactif/en_panne ne peut pas recevoir de mesures |
| `trg_mesure_plage_physique` | Les mesures hors plage physique sont rejetées |
| `trg_affectation_equip_dispo` | Un équipement en maintenance ne peut pas recevoir de capteur |
| `trg_double_maintenance` | Pas deux maintenances actives sur le même équipement |
| `trg_alerte_acquittement` | Une alerte déjà acquittée ne peut pas l'être à nouveau |

### Vues utilitaires

| Vue | Description |
|-----|-------------|
| `v_capteurs_etat` | État temps réel des capteurs + dernière mesure |
| `v_alertes_ouvertes` | Alertes ouvertes triées par priorité |
| `v_kpis` | KPIs agrégés pour le dashboard |

### Commandes de démarrage Phase 1

```bash
# 1. Démarrer TimescaleDB
docker compose up -d timescaledb

# 2. Attendre ~15 secondes puis appliquer le schéma
Get-Content database/schema.sql | docker exec -i raffinerie-iot-timescaledb-1 psql -U admin -d iotdb

# 3. Peupler la base
python database/populate_db.py
```

---

## PROBLÉMATIQUE CHOISIE — Axe Métier

**"Comment simuler des données IoT réalistes pour une raffinerie industrielle — en respectant les contraintes physiques des capteurs, les corrélations entre équipements et les comportements d'anomalie — et piloter cette simulation via une interface web ?"**

---

## PHASE 1.5 — Simulateur IoT Réaliste ✅ TERMINÉE

### Fichier : `simulateur_capteurs.py` (réécrit)

### Problème avec l'ancien simulateur

L'ancien simulateur générait des valeurs **purement aléatoires** :
```python
temp = round(random.uniform(-50, 200), 2)   # Saut de -50 à 200°C possible !
vib  = round(random.uniform(-1, 6), 2)      # Vibration négative impossible
```
Cela ne correspond à aucun comportement physique réel.

### Solution : Random Walk avec inertie

Le nouveau simulateur utilise un **random walk** (marche aléatoire) avec **lissage exponentiel** :

```
valeur(t+1) = valeur(t) × inertie + (valeur(t) + bruit + rappel) × (1 - inertie)
```

- **inertie** = 0.88 à 0.97 selon le type de capteur (température plus inerte que vibration)
- **bruit** = perturbation gaussienne centrée sur 0 (petites fluctuations naturelles)
- **rappel** = force qui tire la valeur vers la cible normale (comme un ressort)

**Résultat visible sur Grafana** : courbe lisse et progressive, jamais de saut brutal.

### 3 modes de fonctionnement

| Mode | Déclenchement | Comportement |
|------|--------------|--------------|
| 🟢 **Normal** | Par défaut | Fluctuations autour de la valeur nominale |
| 🟡 **Dégradé** | 0.3% par tick | Dérive lente vers le seuil d'alerte |
| 🔴 **Anomalie** | 1% par tick depuis dégradé | Pic brutal / dérive hors plage / panne capteur |

### Types d'anomalies simulées

- **Pic** : montée rapide sur 3 ticks, puis retour à la normale
- **Dérive** : augmentation continue hors plage physique
- **Panne** : capteur figé puis valeur nulle (rejeté par le trigger DB)

### Corrélations inter-capteurs

Les capteurs d'une même zone sont corrélés physiquement :
- Si la **température** monte dans une zone → la **pression** augmente (+2% bar/°C)
- Si la **température** monte → le **débit** réagit (+5% L/min/°C)

### Paramètres physiques réels (lus depuis TimescaleDB)

| Type | Unité | Plage physique | Zone normale |
|------|-------|---------------|-------------|
| Température | °C | 0–500 | 50–250 |
| Pression | bar | 0–100 | 5–60 |
| Débit | m³/h | 0–5000 | 100–3000 |
| Vibration | mm/s | 0–50 | 0.1–8 |
| Niveau | % | 0–100 | 20–80 |
| H2S gazeux | ppm | 0–1000 | 0–100 |

### Architecture du simulateur

```
TimescaleDB (capteurs actifs)
        ↓  subprocess + docker exec psql
   Simulateur Python
        ↓
   ┌────┴─────────────────────┐
   │  Pour chaque capteur :   │
   │  - Mode (N/D/A)          │
   │  - Random walk           │
   │  - Corrélation zone      │
   │  - Qualité mesure        │
   └───────────┬──────────────┘
               ↓                    ↓
         MQTT Broker          TimescaleDB
    (raffinerie/type/id)    (table mesures)
               ↓
           Kafka → Spark → MinIO
```

### Commandes de lancement

```powershell
# 20 capteurs, mesure toutes les 2 secondes
python simulateur_capteurs.py --nb 20 --freq 2

# Mode démo sans écriture DB
python simulateur_capteurs.py --nb 10 --no-db

# Tous les capteurs actifs (70)
python simulateur_capteurs.py --nb 70 --freq 5
```

### Visualisation Grafana (port 3000)

Dashboard **"RaffineryWatch — Surveillance IoT"** avec 6 panels :

| Panel | Requête | Capteurs |
|-------|---------|---------|
| Température | `WHERE c.code LIKE 'TEMP%'` | TEMP-032 |
| Pression | `WHERE c.code LIKE 'PRES%'` | PRES-045, PRES-047 |
| H2S gazeux | `WHERE c.code LIKE 'H2S%'` | H2S-004 … H2S-054 |
| Vibration | `WHERE c.code LIKE 'VIBR%'` | VIBR-053 … VIBR-070 |
| Débit | `WHERE c.code LIKE 'DÉBI%'` | DÉBI-001, DÉBI-031 |
| Niveau | `WHERE c.code LIKE 'NIVE%'` | NIVE-013, NIVE-020 |

**Refresh : 5s — Plage : Last 5 minutes**

---

## PHASE 2 — Backend Django REST Framework ✅ TERMINÉE (annulée — voir note)

### Structure créée (`backend/`)

```
backend/
├── manage.py
├── requirements_backend.txt
├── setup_backend.py          ← script de mise en route
├── .env.example
├── config/                   ← projet Django
│   ├── settings.py           ← JWT, CORS, TimescaleDB, apps
│   └── urls.py               ← routes principales + JWT endpoints
├── users/                    ← modèle custom AbstractBaseUser
├── metadata/                 ← Zone, TypeCapteur, Equipement, Maintenance, Affectation
├── capteurs/                 ← Capteur, Mesure (hypertable)
└── alertes/                  ← RegleAlerte, Alerte + acquittement
```

### Endpoints REST (tous préfixés `/api/`)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/token/` | Obtenir JWT (email + password) |
| POST | `/token/refresh/` | Rafraîchir le token |
| GET/POST | `/utilisateurs/` | CRUD utilisateurs (admin only) |
| GET | `/utilisateurs/me/` | Profil de l'utilisateur connecté |
| POST | `/utilisateurs/me/change-password/` | Changer son mot de passe |
| GET/POST | `/zones/` | CRUD zones |
| GET/POST | `/types-capteurs/` | CRUD types de capteurs |
| GET/POST | `/equipements/` | CRUD équipements (filtre: `?zone=`, `?statut=`) |
| GET/POST | `/maintenances/` | CRUD maintenances (filtre: `?equipement=`, `?statut=`) |
| GET/POST | `/affectations/` | CRUD affectations capteurs |
| GET/POST | `/capteurs/` | CRUD capteurs (filtre: `?zone=`, `?statut=`, `?actif=`) |
| GET | `/capteurs/{id}/mesures/` | Mesures d'un capteur (`?heures=24`) |
| GET | `/capteurs/{id}/derniere-mesure/` | Dernière mesure |
| GET/POST | `/mesures/` | CRUD mesures (filtre: `?capteur=`, `?debut=`, `?fin=`) |
| GET/POST | `/regles-alerte/` | CRUD règles d'alerte |
| GET/POST | `/alertes/` | CRUD alertes (filtre: `?statut=`, `?priorite=`) |
| GET | `/alertes/ouvertes/` | Alertes non acquittées |
| POST | `/alertes/{id}/acquitter/` | Acquitter une alerte |

### Contraintes métier implémentées (serializers + models)

| Contrainte | Où |
|-----------|-----|
| Utilisateur non authentifié → 401 | `IsAuthenticated` (global) |
| Lecteur = lecture seule | `IsOperateurOrAdmin` permission |
| Capteur inactif/en_panne → pas de mesures | `MesureSerializer.validate()` |
| Mesure hors plage physique → rejetée | `MesureSerializer.validate()` |
| seuil_min > seuil_max → rejeté | `RegleAlerteSerializer.validate()` |
| Alerte déjà acquittée → erreur | `AlerteAcquittementSerializer.validate()` |
| Équipement en maintenance → pas de capteur | `AffectationCapteurSerializer.validate()` |
| Double maintenance active → rejetée | `MaintenanceSerializer.validate()` |
| Timestamp dupliqué (même capteur) | `unique_together` sur Mesure |

### Commandes de démarrage Phase 2

```powershell
# Depuis le dossier raffinerie-iot/
cd backend

# 1. Installer les dépendances
pip install -r requirements_backend.txt

# 2. Copier et configurer .env
copy .env.example .env
# (éditer .env si besoin)

# 3. Script de mise en route complet (migrations + superuser)
python setup_backend.py

# 4. Démarrer le serveur
python manage.py runserver 8000
```

### ⚠️ Note migrations importante

Les tables SQL existent déjà (Phase 1). Le script `setup_backend.py` utilise
`migrate --fake-initial` pour que Django enregistre les migrations sans recréer les tables.

### JWT — Utilisation

```bash
# Obtenir un token
POST /api/token/
{"email": "admin@raffinerie.local", "password": "admin1234"}

# Réponse : {"access": "...", "refresh": "...", "user": {...}}

# Utiliser le token dans les headers
Authorization: Bearer <access_token>
```

---

## PHASE 3 — Frontend Django Templates + JS 🔲 À FAIRE

### Pages à créer

| Page | Description |
|------|-------------|
| Dashboard | KPIs, alertes en cours, carte des zones |
| Capteurs | Liste, CRUD, statuts, mesures |
| Alertes | Liste, acquittement, historique |
| Règles | CRUD des règles d'alerte |
| Pipeline | Statut temps réel Mosquitto/Kafka/Spark/MinIO |
| Prédictions | Interface LSTM + graphiques |
| Admin | Gestion utilisateurs, zones, équipements |

**Style :** Thème sombre industriel, accents orange/rouge, Chart.js, Grafana iframe

---

## PHASE 4 — Module IA (LSTM + Spark) 🔲 À FAIRE

- Modèle LSTM entraîné sur données TimescaleDB
- Intégration dans Spark MLlib / Structured Streaming
- API Django pour déclencher les jobs Spark
- Affichage prédictions vs valeurs réelles
- Export CSV des prédictions

---

## PHASE 5 — Déploiement 🔲 À FAIRE

- `docker-compose.yml` complet intégrant Django au pipeline IoT
- Configuration Vercel (frontend + backend Django)
- Base de données externe : Railway ou Supabase
- Variables d'environnement
- README complet (installation, configuration, lancement, déploiement)

---

## Modules de l'application (rappel)

| # | Module | Statut |
|---|--------|--------|
| 1 | Auth & Gestion utilisateurs (JWT + rôles) | 🔲 Phase 2 |
| 2 | Gestion des capteurs (CRUD + mesures) | 🔲 Phase 2 |
| 3 | Règles d'alerte et acquittement | 🔲 Phase 2 |
| 4 | Métadonnées (zones, équipements, maintenances) | 🔲 Phase 2 |
| 5 | Dashboard temps réel (Grafana + KPIs) | 🔲 Phase 3 |
| 6 | Suivi pipeline IoT | 🔲 Phase 3 |
| 7 | Prédiction IA (LSTM + Spark) | 🔲 Phase 4 |
