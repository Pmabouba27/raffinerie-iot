"""
simulateur_capteurs.py — RaffineryWatch
Simulateur IoT réaliste pour raffinerie industrielle.

Réalisme physique :
  - Valeurs initialisées dans la plage normale de chaque type de capteur
  - Évolution progressive via random walk (pas de sauts brutaux)
  - 3 modes : NORMAL → DÉGRADE → ANOMALIE
  - Anomalies : dérive lente, pic brutal, panne capteur
  - Corrélations inter-capteurs dans la même zone
  - Lecture des capteurs actifs depuis TimescaleDB
  - Publication MQTT + écriture TimescaleDB

Usage :
  python simulateur_capteurs.py [--nb 10] [--freq 2] [--mode normal]
"""

import time
import json
import random
import math
import subprocess
import argparse
import signal
import sys
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────
CONTAINER   = "raffinerie-iot-timescaledb-1"
DB_USER     = "admin"
DB_NAME     = "iotdb"
MQTT_HOST   = "localhost"
MQTT_PORT   = 1883

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
    "temperature": {
        "unite": "°C",
        "plage_min": 0.0,   "plage_max": 500.0,
        "normal_min": 50.0, "normal_max": 250.0,
        "bruit": 0.5,       "inertie": 0.92,
    },
    "pression": {
        "unite": "bar",
        "plage_min": 0.0,   "plage_max": 100.0,
        "normal_min": 5.0,  "normal_max": 60.0,
        "bruit": 0.3,       "inertie": 0.95,
    },
    "debit": {
        "unite": "L/min",
        "plage_min": 0.0,   "plage_max": 1000.0,
        "normal_min": 100.0,"normal_max": 700.0,
        "bruit": 2.0,       "inertie": 0.88,
    },
    "vibration": {
        "unite": "mm/s",
        "plage_min": 0.0,   "plage_max": 50.0,
        "normal_min": 0.1,  "normal_max": 8.0,
        "bruit": 0.2,       "inertie": 0.80,
    },
    "niveau": {
        "unite": "%",
        "plage_min": 0.0,   "plage_max": 100.0,
        "normal_min": 20.0, "normal_max": 80.0,
        "bruit": 0.3,       "inertie": 0.97,
    },
    "humidite": {
        "unite": "%",
        "plage_min": 0.0,   "plage_max": 100.0,
        "normal_min": 30.0, "normal_max": 70.0,
        "bruit": 0.4,       "inertie": 0.93,
    },
}

# ─────────────────────────────────────────────────────────
# Accès TimescaleDB via subprocess
# ─────────────────────────────────────────────────────────
def psql(sql, fetch=False):
    cmd = [
        "docker", "exec", "-i", CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-v", "ON_ERROR_STOP=1",
        "-t", "-A", "-F", "|",
    ]
    proc = subprocess.run(
        cmd,
        input=sql.encode("utf-8"),
        capture_output=True,
        shell=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        if err:
            print(f"  [DB] Erreur: {err[:120]}")
        return []
    if fetch:
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return []
        return [line.split("|") for line in out.split("\n") if line.strip()]
    return True


def charger_capteurs(nb_max):
    """Charge les capteurs actifs depuis TimescaleDB."""
    rows = psql(f"""
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
        LIMIT {nb_max};
    """, fetch=True)
    return rows


def inserer_mesure(capteur_id, valeur, qualite):
    """Insère une mesure dans TimescaleDB."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")
    valeur_r = round(valeur, 4)
    sql = (
        f"INSERT INTO mesures (capteur_id, valeur, timestamp, qualite, source) "
        f"VALUES ({capteur_id}, {valeur_r}, '{ts}', '{qualite}', 'simulateur') "
        f"ON CONFLICT (capteur_id, timestamp) DO NOTHING;"
    )
    psql(sql)


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
                self.mode = "anomalie"
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

        if self.mode == "normal":
            # Random walk autour de la valeur normale
            bruit = random.gauss(0, self.bruit)
            # Rappel élastique vers la cible normale
            rappel = (self.cible - self.valeur) * 0.05
            delta = bruit + rappel + correlation_delta * 0.3

        elif self.mode == "degrade":
            # Dérive progressive vers la cible de stress
            bruit = random.gauss(0, self.bruit * 2)
            rappel = (self.cible - self.valeur) * 0.08
            delta = bruit + rappel + correlation_delta * 0.5

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
# Gestion des corrélations entre zones
# ─────────────────────────────────────────────────────────
class GestionnaireZones:
    """
    Corrélations physiques entre capteurs d'une même zone.
    Si la température monte dans une zone, la pression suit (légèrement).
    """

    def __init__(self):
        self.temp_par_zone = {}   # zone_id → température moyenne

    def mettre_a_jour(self, zone_id, type_capteur, valeur):
        if "temp" in type_capteur.lower():
            self.temp_par_zone[zone_id] = valeur

    def get_correlation(self, zone_id, type_capteur):
        """Retourne un delta de corrélation pour un capteur donné."""
        if zone_id not in self.temp_par_zone:
            return 0.0
        temp_zone = self.temp_par_zone[zone_id]
        # La pression et le débit réagissent à la température
        if "pression" in type_capteur.lower():
            return (temp_zone - 150) * 0.02   # +2% bar par °C au-dessus de 150°C
        if "debit" in type_capteur.lower():
            return (temp_zone - 150) * 0.05
        return 0.0


# ─────────────────────────────────────────────────────────
# Simulateur principal
# ─────────────────────────────────────────────────────────
class Simulateur:

    def __init__(self, nb_capteurs=20, frequence=2.0, ecrire_db=True):
        self.nb_capteurs = nb_capteurs
        self.frequence   = frequence   # secondes entre deux ticks
        self.ecrire_db   = ecrire_db
        self.actif       = False
        self.etats       = []
        self.zones_mgr   = GestionnaireZones()
        self.mqtt_client = None
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

    def tick(self):
        """Un cycle de simulation pour tous les capteurs."""
        self.stats["ticks"] += 1
        lignes = []

        for etat in self.etats:
            # Corrélation de zone
            delta_corr = self.zones_mgr.get_correlation(
                etat.zone_id, etat.type_nom
            )
            valeur, qualite = etat.tick(correlation_delta=delta_corr)

            if valeur is None:
                # Capteur en panne
                self.stats["pannes"] += 1
                lignes.append(
                    f"  ❌ {etat.identifiant:<12} {etat.type_nom:<14} "
                    f"[PANNE]"
                )
                continue

            # Mettre à jour les corrélations de zone
            self.zones_mgr.mettre_a_jour(etat.zone_id, etat.type_nom, valeur)

            # Publier MQTT
            self._publier_mqtt(etat, valeur, qualite)

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
        description="RaffineryWatch — Simulateur IoT réaliste"
    )
    parser.add_argument(
        "--nb", type=int, default=20,
        help="Nombre de capteurs à simuler (défaut: 20)"
    )
    parser.add_argument(
        "--freq", type=float, default=2.0,
        help="Secondes entre deux ticks (défaut: 2)"
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Désactiver l'écriture en base"
    )
    args = parser.parse_args()

    sim = Simulateur(
        nb_capteurs=args.nb,
        frequence=args.freq,
        ecrire_db=not args.no_db,
    )

    # Gestion propre de Ctrl+C
    def handler(sig, frame):
        sim.arreter()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    sim.initialiser()
    sim.lancer()


if __name__ == "__main__":
    main()
