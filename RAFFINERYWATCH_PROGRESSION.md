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

## PHASE 2 — Backend Django REST Framework 🔲 À FAIRE

### Ce qu'on va construire

- Projet Django avec apps : `users`, `capteurs`, `alertes`, `pipeline`
- Modèles Django correspondant au schéma Phase 1
- Serializers DRF pour chaque entité
- ViewSets avec contraintes métier (rôles, statuts, seuils)
- Authentification JWT (`djangorestframework-simplejwt`)
- Admin Django configuré pour toutes les entités
- CORS configuré pour le frontend

### Contraintes métier à implémenter côté Django

- Un utilisateur non authentifié ne peut rien faire
- Un Lecteur ne peut pas écrire
- Un capteur désactivé ne peut pas recevoir de mesures
- Les mesures hors plage physique sont rejetées
- Une règle ne peut pas avoir seuil_min > seuil_max
- Une alerte déjà acquittée ne peut pas l'être à nouveau
- Un équipement en maintenance ne peut pas recevoir de capteur
- Deux mesures d'un même capteur ne peuvent pas avoir le même timestamp

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
