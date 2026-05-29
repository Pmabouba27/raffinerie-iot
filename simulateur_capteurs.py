"""
simulateur_capteurs.py — RaffineryWatch
Simulateur IoT réaliste pour raffinerie industrielle.

Réalisme physique (Axe Métier + IA) :
  - Corrélations physiques inter-capteurs basées sur des lois réelles :
      • Température ↔ Pression  (loi de Gay-Lussac : P ∝ T)
      • Débit ↔ Vibration       (cavitation : faible débit → vibration haute)
      • Niveau ↔ Débit          (niveau bas → chute de performance pompe)
      • Température ↔ H2S       (hausse T → évaporation H2S accrue)
  - Cycle journalier (démarrage matin, régime stable, ralentissement nuit)
  - Défaillances progressives avec pré-signature (vibration monte AVANT la panne)
  - Cascades de défaillances : panne pompe → chute débit → hausse température aval
  - 3 modes : NORMAL → DÉGRADE → ANOMALIE
  - Publication MQTT + Kafka (Spark) + écriture TimescaleDB

Usage :
  python simulateur_capteurs.py [--nb 10] [--freq 2] [--mode normal]
"""

import os
import time
import json
import random
import math
import argparse
import signal
import sys
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────
# Configuration — lues depuis les variables d'environnement
# (identiques à celles injectées par Docker Compose)
# ─────────────────────────────────────────────────────────
MQTT_HOST = os.environ.get('MQTT_HOST', 'localhost')
MQTT_PORT = 1883

import psycopg2
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST',     'localhost'),
    'port':     int(os.environ.get('DB_PORT', '5432')),
    'dbname':   os.environ.get('DB_NAME',     'iotdb'),
    'user':     os.environ.get('DB_USER',     'admin'),
    'password': os.environ.get('DB_PASSWORD', 'admin'),
}
_db_conn = None

def get_db_conn():
    """Retourne une connexion psycopg2 persistante, reconnecte si besoin."""
    global _db_conn
    try:
        if _db_conn is None or _db_conn.closed:
            _db_conn = psycopg2.connect(**DB_CONFIG)
        return _db_conn
    except Exception as e:
        print(f"  [DB] Connexion échouée: {e}")
        return None

# Probabilités de transition de mode (par tick)
P_NORMAL_TO_DEGRADE  = 0.003   # 0.3% chance de passer en dégradé
P_DEGRADE_TO_ANOMALIE = 0.01   # 1%  chance de passer en anomalie
P_ANOMALIE_TO_NORMAL  = 0.02   # 2%  chance de revenir à normal
P_DEGRADE_TO_NORMAL   = 0.008  # 0.8% chance de revenir à normal

# ─────────────────────────────────────────────────────────
# Plages physiques réalistes par type de capteur
# (utilisées si la DB n'est pas disponible)
# ─────────────────────────────────────────────────────────
PLAGES_DEFAUT = {
    # Clés sans accents (nom DB normalisé)
    "temperature": {
        "unite": "°C",
        "plage_min": -50.0,  "plage_max": 700.0,
        "normal_min": 20.0,  "normal_max": 280.0,
        "bruit": 0.5,        "inertie": 0.92,
    },
    # Alias avec accent (correspond au nom réel en DB)
    "température": {
        "unite": "°C",
        "plage_min": -50.0,  "plage_max": 700.0,
        "normal_min": 20.0,  "normal_max": 280.0,
        "bruit": 0.5,        "inertie": 0.92,
    },
    "pression": {
        "unite": "bar",
        "plage_min": -1.0,   "plage_max": 350.0,
        "normal_min": 1.0,   "normal_max": 120.0,
        "bruit": 0.3,        "inertie": 0.95,
    },
    "debit": {
        "unite": "m3/h",
        "plage_min": 0.0,    "plage_max": 5000.0,
        "normal_min": 10.0,  "normal_max": 900.0,
        "bruit": 5.0,        "inertie": 0.88,
    },
    # Alias avec accent
    "débit": {
        "unite": "m3/h",
        "plage_min": 0.0,    "plage_max": 5000.0,
        "normal_min": 10.0,  "normal_max": 900.0,
        "bruit": 5.0,        "inertie": 0.88,
    },
    "vibration": {
        "unite": "mm/s",
        "plage_min": 0.0,    "plage_max": 50.0,
        "normal_min": 0.1,   "normal_max": 8.0,
        "bruit": 0.2,        "inertie": 0.80,
    },
    "niveau": {
        "unite": "%",
        "plage_min": 0.0,    "plage_max": 100.0,
        "normal_min": 20.0,  "normal_max": 80.0,
        "bruit": 0.3,        "inertie": 0.97,
    },
    # H2S gazeux → clé "h2s"
    "h2s": {
        "unite": "ppm",
        "plage_min": 0.0,    "plage_max": 1000.0,
        "normal_min": 0.0,   "normal_max": 8.0,
        "bruit": 0.1,        "inertie": 0.93,
    },
    "humidite": {
        "unite": "%",
        "plage_min": 0.0,    "plage_max": 100.0,
        "normal_min": 30.0,  "normal_max": 70.0,
        "bruit": 0.4,        "inertie": 0.93,
    },
}

# ─────────────────────────────────────────────────────────
# Accès TimescaleDB via psycopg2 (connexion directe)
# ─────────────────────────────────────────────────────────
def charger_capteurs(nb_max):
    """Charge les capteurs actifs depuis TimescaleDB."""
    conn = get_db_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, c.code, c.nom,
                   tc.nom AS type_nom, tc.unite_mesure,
                   tc.valeur_min_physique, tc.valeur_max_physique,
                   c.seuil_min, c.seuil_max,
                   COALESCE(e.nom, 'Inconnu') AS equipement_nom,
                   COALESCE(z.nom, 'Zone-' || c.zone_id::text) AS zone_nom,
                   COALESCE(c.zone_id, 0) AS zone_id
            FROM capteurs c
            JOIN types_capteurs tc      ON tc.id = c.type_capteur_id
            LEFT JOIN equipements e     ON e.id  = c.equipement_id
            LEFT JOIN zones z           ON z.id  = c.zone_id
            WHERE c.statut = 'actif'
            ORDER BY c.zone_id, c.id
            LIMIT %s;
        """, [nb_max])
        rows = cur.fetchall()
        cur.close()
        return [list(r) for r in rows]
    except Exception as e:
        print(f"  [DB] Erreur chargement capteurs: {e}")
        return []


def inserer_mesure(capteur_id, valeur, qualite):
    """Insère une mesure dans TimescaleDB."""
    conn = get_db_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mesures (capteur_id, valeur, timestamp, qualite, source) "
            "VALUES (%s, %s, NOW(), %s, 'simulateur') "
            "ON CONFLICT (capteur_id, timestamp) DO NOTHING;",
            [capteur_id, round(valeur, 4), qualite]
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"  [DB] Erreur insertion: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Classe EtatCapteur — cœur du réalisme physique
# ─────────────────────────────────────────────────────────
class EtatCapteur:
    """
    Maintient l'état physique d'un capteur entre deux ticks.

    Réalisme :
      - inertie : résistance au changement (0=aucune, 1=infinie)
      - random walk : petites perturbations gaussiennes
      - modes : NORMAL / DÉGRADE / ANOMALIE
      - anomalies : dérive, pic, panne
    """

    MODES = ["normal", "degrade", "anomalie"]

    def __init__(self, capteur_row):
        (self.id, self.identifiant, self.nom,
         self.type_nom, self.unite,
         plage_min, plage_max,
         seuil_min, seuil_max,
         self.equipement, self.zone, self.zone_id) = capteur_row

        self.plage_min = float(plage_min)
        self.plage_max = float(plage_max)
        self.seuil_min = float(seuil_min) if seuil_min else None
        self.seuil_max = float(seuil_max) if seuil_max else None

        # Récupérer les paramètres physiques du type
        type_key = self.type_nom.lower().split()[0]
        params = PLAGES_DEFAUT.get(type_key, {
            "normal_min": self.plage_min * 0.2,
            "normal_max": self.plage_max * 0.7,
            "bruit": (self.plage_max - self.plage_min) * 0.005,
            "inertie": 0.90,
        })

        self.normal_min = params.get("normal_min", self.plage_min * 0.2)
        self.normal_max = params.get("normal_max", self.plage_max * 0.7)
        self.bruit      = params.get("bruit", 1.0)
        self.inertie    = params.get("inertie", 0.90)

        # Valeur de départ : dans la zone normale
        self.valeur  = random.uniform(self.normal_min, self.normal_max)
        self.cible   = self.valeur   # cible vers laquelle dériver
        self.mode    = "normal"
        self.ticks_mode = 0          # durée dans le mode actuel
        self.en_panne = False
        # Pré-signature : nombre de ticks de dégradation progressive avant anomalie
        self._pre_signature_ticks = 0
        self._pre_signature_actif = False

    # ── Transition de mode ────────────────────────────────
    def _changer_mode(self):
        r = random.random()
        if self.mode == "normal":
            if r < P_NORMAL_TO_DEGRADE:
                self.mode = "degrade"
                # Cible : zone de stress (entre normal_max et seuil_max)
                limite_haute = self.seuil_max or self.plage_max * 0.85
                self.cible = random.uniform(self.normal_max, limite_haute)
                self.ticks_mode = 0
        elif self.mode == "degrade":
            if r < P_DEGRADE_TO_ANOMALIE:
                # Pré-signature : 5 ticks de montée progressive avant anomalie franche
                self._pre_signature_actif = True
                self._pre_signature_ticks = 0
                if self._pre_signature_ticks >= 5:
                    self.mode = "anomalie"
                    self._pre_signature_actif = False
                    self.ticks_mode = 0
            elif r < P_DEGRADE_TO_ANOMALIE + P_DEGRADE_TO_NORMAL:
                self.mode = "normal"
                self.cible = random.uniform(self.normal_min, self.normal_max)
                self.ticks_mode = 0
        elif self.mode == "anomalie":
            if r < P_ANOMALIE_TO_NORMAL:
                self.mode = "normal"
                self.cible = random.uniform(self.normal_min, self.normal_max)
                self.ticks_mode = 0
                self.en_panne = False

    # ── Calcul de la prochaine valeur ─────────────────────
    def tick(self, correlation_delta=0.0):
        """
        Calcule la prochaine valeur du capteur.

        correlation_delta : influence d'un capteur voisin (ex. +2.5°C
                            parce que la température de zone monte)
        """
        if self.en_panne:
            return None, "mauvaise"

        self._changer_mode()
        self.ticks_mode += 1

        # Pré-signature : montée progressive juste avant l'anomalie
        if self._pre_signature_actif:
            self._pre_signature_ticks += 1
            intensite = self._pre_signature_ticks / 5.0   # 0→1 sur 5 ticks
            bruit  = random.gauss(0, self.bruit * (1 + intensite * 2))
            rappel = (self.cible - self.valeur) * (0.06 + intensite * 0.08)
            delta  = bruit + rappel + correlation_delta * 0.6
            if self._pre_signature_ticks >= 5:
                self.mode = "anomalie"
                self._pre_signature_actif = False
                self.ticks_mode = 0

        elif self.mode == "normal":
            # Random walk autour de la valeur normale + cycle journalier
            bruit  = random.gauss(0, self.bruit)
            rappel = (self.cible - self.valeur) * 0.05
            delta  = bruit + rappel + correlation_delta * 0.3

        elif self.mode == "degrade":
            # Dérive progressive vers la cible de stress
            bruit  = random.gauss(0, self.bruit * 2)
            rappel = (self.cible - self.valeur) * 0.08
            delta  = bruit + rappel + correlation_delta * 0.5

        elif self.mode == "anomalie":
            # Choisir le type d'anomalie au premier tick
            if self.ticks_mode == 1:
                self._type_anomalie = random.choice(["pic", "derive", "panne"])

            if self._type_anomalie == "pic":
                # Pic brutal puis retour
                if self.ticks_mode <= 3:
                    delta = (self.plage_max - self.valeur) * 0.4
                else:
                    delta = (self.normal_max - self.valeur) * 0.15
                bruit = random.gauss(0, self.bruit * 3)
                delta += bruit

            elif self._type_anomalie == "derive":
                # Dérive lente hors plage
                delta = (self.plage_max - self.normal_max) * 0.04
                delta += random.gauss(0, self.bruit)

            elif self._type_anomalie == "panne":
                # Capteur tombe en panne → valeur figée ou à 0
                if self.ticks_mode > 3:
                    self.en_panne = True
                    return None, "mauvaise"
                delta = random.gauss(0, self.bruit * 5)

        # Appliquer l'inertie (lissage exponentiel)
        nouvelle = self.valeur * self.inertie + (self.valeur + delta) * (1 - self.inertie)

        # Contraindre dans la plage physique
        nouvelle = max(self.plage_min, min(self.plage_max, nouvelle))
        self.valeur = nouvelle

        # Qualité de la mesure
        qualite = self._evaluer_qualite()
        return round(nouvelle, 3), qualite

    def _evaluer_qualite(self):
        """Qualité basée sur la position dans la plage."""
        if self.mode == "anomalie":
            return "mauvaise"
        if self.mode == "degrade":
            return "douteuse"
        # Vérifier par rapport aux seuils d'alerte
        if self.seuil_max and self.valeur > self.seuil_max * 0.95:
            return "douteuse"
        if self.seuil_min and self.valeur < self.seuil_min * 1.05:
            return "douteuse"
        return "bonne"


# ─────────────────────────────────────────────────────────
# Moteur physique — corrélations réelles inter-capteurs
# ─────────────────────────────────────────────────────────
class MoteurPhysique:
    """
    Moteur de corrélations physiques entre capteurs d'une même zone.

    Lois implémentées :
      1. Gay-Lussac (T↔P)   : une hausse de température entraîne une hausse de pression
      2. Cavitation (Q↔Vib) : un débit trop faible provoque des vibrations sur la pompe
      3. Niveau→Débit        : un niveau bas dégrade les performances de la pompe
      4. Température→H2S     : une haute température accélère l'évaporation du H2S
      5. Cycle journalier    : modulation sinusoïdale sur 24h (démarrage, régime, arrêt)
      6. Cascade de panne    : si une pompe tombe → débit chute → température monte
    """

    # Température de référence normale (°C) pour les calculs Gay-Lussac
    T_REF = 75.0
    # Pression de référence normale (bar)
    P_REF = 2.8
    # Débit de référence normal (m³/h)
    Q_REF = 200.0

    def __init__(self):
        # État moyen par zone  {zone_id: {type: valeur_moyenne}}
        self.etat_zone = {}
        # Ticks de simulation (pour cycle journalier)
        self._tick_count = 0
        # Cascades actives  {zone_id: True/False}
        self._cascade_active = {}

    def mettre_a_jour(self, zone_id, type_capteur, valeur):
        """Met à jour l'état moyen d'une zone après chaque mesure."""
        if zone_id not in self.etat_zone:
            self.etat_zone[zone_id] = {}
        tc = type_capteur.lower()
        # Lissage exponentiel : moyenne mobile (α=0.1)
        ancienne = self.etat_zone[zone_id].get(tc, valeur)
        self.etat_zone[zone_id][tc] = 0.9 * ancienne + 0.1 * valeur

    def incrementer_tick(self):
        self._tick_count += 1

    def cycle_journalier(self):
        """
        Facteur multiplicatif [0.92 – 1.08] simulant le cycle opérationnel 24h.
        Pic à 10h (démarrage full régime), creux à 3h (maintenance nocturne).
        """
        heure_simulee = (self._tick_count * 2 / 3600) % 24   # ~2s par tick
        return 1.0 + 0.08 * math.sin(2 * math.pi * (heure_simulee - 4) / 24)

    def declencher_cascade(self, zone_id):
        """Déclenche une cascade de panne dans une zone (ex: panne pompe)."""
        self._cascade_active[zone_id] = True

    def arreter_cascade(self, zone_id):
        self._cascade_active.pop(zone_id, None)

    def get_delta_correlation(self, zone_id, type_capteur):
        """
        Calcule le delta de corrélation physique pour un capteur donné.
        Retourne un float représentant l'influence des capteurs voisins.
        """
        tc   = type_capteur.lower()
        zone = self.etat_zone.get(zone_id, {})
        delta = 0.0

        # ── Loi 1 : Gay-Lussac T↔P ──────────────────────────────
        # ΔP = P_ref × (T_zone / T_ref - 1) × k
        if "pression" in tc:
            t_zone = zone.get("température", self.T_REF)
            delta += self.P_REF * ((t_zone / self.T_REF) - 1.0) * 0.4

        # ── Loi 2 : Débit ↔ Vibration (cavitation) ──────────────
        # Quand le débit chute sous 40% du nominal → vibrations augmentent
        if "vibration" in tc:
            q_zone = zone.get("débit", self.Q_REF)
            ratio_q = q_zone / self.Q_REF
            if ratio_q < 0.4:
                # Cavitation sévère : delta vibration fort
                delta += (0.4 - ratio_q) * 12.0
            elif ratio_q < 0.7:
                # Pré-cavitation : légère augmentation
                delta += (0.7 - ratio_q) * 4.0

        # ── Loi 3 : Niveau bas → chute de débit ─────────────────
        if "débit" in tc:
            niv = zone.get("niveau", 60.0)
            if niv < 25.0:
                # Niveau critique → pompe aspire à vide
                delta -= (25.0 - niv) * 3.0
            elif niv < 40.0:
                delta -= (40.0 - niv) * 1.0

        # ── Loi 4 : Température → évaporation H2S ───────────────
        if "h2s" in tc:
            t_zone = zone.get("température", self.T_REF)
            if t_zone > 80.0:
                delta += (t_zone - 80.0) * 0.15

        # ── Loi 5 : Cascade de panne (pompe tombée) ──────────────
        if self._cascade_active.get(zone_id, False):
            if "débit" in tc:
                delta -= 80.0   # chute débit brutale
            if "température" in tc:
                delta += 15.0   # surchauffe aval
            if "vibration" in tc:
                delta += 5.0

        # ── Loi 6 : Cycle journalier (modulation ±8%) ────────────
        cycle = self.cycle_journalier()
        if "température" in tc or "pression" in tc or "débit" in tc:
            delta *= cycle

        return delta


# ─────────────────────────────────────────────────────────
# Simulateur principal
# ─────────────────────────────────────────────────────────
class Simulateur:

    def __init__(self, nb_capteurs=20, frequence=2.0, ecrire_db=True):
        self.nb_capteurs = nb_capteurs
        self.frequence   = frequence   # secondes entre deux ticks
        self.ecrire_db   = ecrire_db
        self.actif          = False
        self.etats          = []
        self.moteur         = MoteurPhysique()
        self.mqtt_client    = None
        self.kafka_producer = None
        self.stats = {
            "ticks": 0,
            "mesures_publiees": 0,
            "anomalies": 0,
            "pannes": 0,
        }

    def initialiser(self):
        """Charge les capteurs et connecte MQTT."""
        print(f"\n{'='*60}")
        print(f"  RaffineryWatch — Simulateur IoT Réaliste")
        print(f"{'='*60}")
        print(f"  Capteurs demandés : {self.nb_capteurs}")
        print(f"  Fréquence         : {self.frequence}s")
        print(f"  Écriture DB       : {'OUI' if self.ecrire_db else 'NON'}")
        print()

        # Charger depuis TimescaleDB
        rows = charger_capteurs(self.nb_capteurs)
        if rows:
            print(f"  ✅ {len(rows)} capteurs chargés depuis TimescaleDB")
            self.etats = [EtatCapteur(r) for r in rows]
        else:
            # Fallback : capteurs synthétiques si DB indisponible
            print("  ⚠️  DB inaccessible — mode démo (capteurs synthétiques)")
            self.etats = self._capteurs_demo()

        # Connexion MQTT
        try:
            import paho.mqtt.client as mqtt
            self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
            print(f"  ✅ MQTT connecté sur {MQTT_HOST}:{MQTT_PORT}")
        except Exception as e:
            print(f"  ⚠️  MQTT indisponible ({e}) — publication désactivée")
            self.mqtt_client = None

        # Connexion Kafka
        KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
        try:
            from confluent_kafka import Producer
            self.kafka_producer = Producer({'bootstrap.servers': KAFKA_BROKER})
            print(f"  ✅ Kafka connecté sur {KAFKA_BROKER}")
        except Exception as e:
            print(f"  ⚠️  Kafka indisponible ({e}) — publication Kafka désactivée")
            self.kafka_producer = None

        print()
        self._afficher_capteurs()

    def _capteurs_demo(self):
        """Capteurs synthétiques pour fonctionner sans DB."""
        types = list(PLAGES_DEFAUT.items())
        etats = []
        for i in range(min(self.nb_capteurs, 12)):
            type_nom, params = types[i % len(types)]
            # Simuler une ligne de DB
            row = [
                i + 1,
                f"CAP-DEMO-{i+1:03d}",
                f"Capteur {type_nom} {i+1}",
                type_nom,
                params["unite"],
                params["plage_min"],
                params["plage_max"],
                params["normal_min"],
                params["normal_max"],
                f"Équipement-{i+1}",
                f"Zone-{(i // 4) + 1}",
                (i // 4) + 1,
            ]
            etats.append(EtatCapteur(row))
        return etats

    def _afficher_capteurs(self):
        print(f"  {'ID':<12} {'Type':<15} {'Zone':<20} {'Valeur init':>12} {'Unité'}")
        print(f"  {'-'*70}")
        for e in self.etats:
            print(
                f"  {e.identifiant:<12} {e.type_nom:<15} "
                f"{e.zone:<20} {e.valeur:>10.2f}  {e.unite}"
            )
        print()

    def _publier_mqtt(self, etat, valeur, qualite):
        """Publie une mesure sur le topic MQTT correspondant."""
        if not self.mqtt_client:
            return
        topic = f"raffinerie/{etat.type_nom.lower().replace(' ', '_')}/{etat.identifiant}"
        payload = json.dumps({
            "capteur_id":   etat.id,
            "identifiant":  etat.identifiant,
            "machine_id":   etat.equipement,
            "zone":         etat.zone,
            "valeur":       valeur,
            "unite":        etat.unite,
            "qualite":      qualite,
            "mode":         etat.mode,
            "timestamp":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type_capteur": etat.type_nom,
        })
        try:
            self.mqtt_client.publish(topic, payload)
        except Exception:
            pass

    def _publier_kafka(self, etat, valeur, qualite):
        """Publie une mesure sur le topic Kafka 'sensor-data' (lu par Spark)."""
        if not self.kafka_producer:
            return
        payload = json.dumps({
            "capteur_id":   etat.id,
            "identifiant":  etat.identifiant,
            "machine_id":   etat.equipement,
            "zone":         etat.zone,
            "valeur":       valeur,
            "unite":        etat.unite,
            "qualite":      qualite,
            "mode":         etat.mode,
            "timestamp":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type_capteur": etat.type_nom,
        })
        try:
            self.kafka_producer.produce("sensor-data", payload.encode("utf-8"))
            self.kafka_producer.poll(0)
        except Exception:
            pass

    def tick(self):
        """Un cycle de simulation pour tous les capteurs."""
        self.stats["ticks"] += 1
        self.moteur.incrementer_tick()
        lignes = []

        for etat in self.etats:
            # Delta de corrélation physique (lois Gay-Lussac, cavitation, etc.)
            delta_corr = self.moteur.get_delta_correlation(
                etat.zone_id, etat.type_nom
            )
            valeur, qualite = etat.tick(correlation_delta=delta_corr)

            if valeur is None:
                # Capteur en panne → déclencher cascade dans la zone
                self.stats["pannes"] += 1
                if "vibration" in etat.type_nom.lower() or "débit" in etat.type_nom.lower():
                    self.moteur.declencher_cascade(etat.zone_id)
                lignes.append(
                    f"  ❌ {etat.identifiant:<12} {etat.type_nom:<14} "
                    f"[PANNE — cascade déclenchée zone {etat.zone_id}]"
                )
                continue

            # Mettre à jour l'état physique de la zone
            self.moteur.mettre_a_jour(etat.zone_id, etat.type_nom, valeur)

            # Publier MQTT
            self._publier_mqtt(etat, valeur, qualite)

            # Publier Kafka (topic sensor-data — lu par Spark)
            self._publier_kafka(etat, valeur, qualite)

            # Écrire en DB
            if self.ecrire_db:
                inserer_mesure(etat.id, valeur, qualite)

            self.stats["mesures_publiees"] += 1
            if etat.mode == "anomalie":
                self.stats["anomalies"] += 1

            # Icône de mode
            icone = {"normal": "🟢", "degrade": "🟡", "anomalie": "🔴"}.get(
                etat.mode, "⚪"
            )
            lignes.append(
                f"  {icone} {etat.identifiant:<12} {etat.type_nom:<14} "
                f"{valeur:>10.3f} {etat.unite:<8} [{qualite}]"
            )

        # Affichage
        print(f"\n── Tick #{self.stats['ticks']:04d} "
              f"— {datetime.now().strftime('%H:%M:%S')} "
              f"— Anomalies: {self.stats['anomalies']} "
              f"— Pannes: {self.stats['pannes']} ──")
        for l in lignes:
            print(l)

    def lancer(self):
        """Boucle principale du simulateur."""
        self.actif = True
        print(f"\n  Simulation démarrée (Ctrl+C pour arrêter)\n")
        try:
            while self.actif:
                self.tick()
                time.sleep(self.frequence)
        except KeyboardInterrupt:
            self.arreter()

    def arreter(self):
        self.actif = False
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        print(f"\n\n{'='*60}")
        print(f"  Simulateur arrêté")
        print(f"  Ticks          : {self.stats['ticks']}")
        print(f"  Mesures publiées: {self.stats['mesures_publiees']}")
        print(f"  Anomalies vues  : {self.stats['anomalies']}")
        print(f"  Pannes capteurs : {self.stats['pannes']}")
        print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='RaffineryWatch - Simulateur IoT realiste'
    )
    parser.add_argument('--nb',   type=int, default=20,
                        help='Nombre de capteurs (defaut: 20)')
    parser.add_argument('--freq', type=int, default=2,
                        help='Frequence secondes entre mesures (defaut: 2)')
    parser.add_argument('--mode', default='normal',
                        choices=['normal', 'degrade', 'anomalie'],
                        help='Mode initial')
    args = parser.parse_args()

    sim = Simulateur(nb_capteurs=args.nb, frequence=args.freq)

    def signal_handler(sig, frame):
        sim.arreter()
        sys.exit(0)

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    sim.initialiser()
    sim.lancer()


if __name__ == '__main__':
    main()
