"""
RaffineryWatch — Service de détection d'anomalies hybride IA
============================================================
Phase 1 : Isolation Forest  (scikit-learn)  → anomalies ponctuelles
Phase 2 : LSTM Autoencoder  (TensorFlow)    → dérives temporelles

Stratégie d'entraînement HYBRID LEARNING :
  - Démarrage : entraînement synthétique immédiat (cold start → détection opérationnelle dès le 1er message)
  - Après MINIO_RETRAIN_AFTER_N points réels collectés dans MinIO : ré-entraînement automatique sur données réelles
  - Avantage : zéro fenêtre aveugle AU démarrage + modèle de plus en plus précis avec les vraies données

Flux : Kafka sensor-data → détection → alertes (TimescaleDB)
       Spark traitement_kpi.py → MinIO raffinerie-raw/raw → ré-entraînement IF
"""

import os
import io
import json
import time
import threading
import logging
import numpy as np
import psycopg2
from confluent_kafka import Consumer, KafkaError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("anomalie_detection")

# ─── Configuration ────────────────────────────────────────────────────────────
KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
KAFKA_TOPIC  = 'sensor-data'
KAFKA_GROUP  = 'anomalie-detector'

DB_CONFIG = {
    'host':     os.environ.get('DB_HOST',     'timescaledb'),
    'port':     int(os.environ.get('DB_PORT', '5432')),
    'dbname':   os.environ.get('DB_NAME',     'iotdb'),
    'user':     os.environ.get('DB_USER',     'admin'),
    'password': os.environ.get('DB_PASSWORD', 'admin'),
}

# ─── Configuration MinIO (hybrid learning) ────────────────────────────────────
MINIO_ENDPOINT        = os.environ.get('MINIO_ENDPOINT',  'minio:9000')
MINIO_ACCESS_KEY      = os.environ.get('MINIO_ACCESS_KEY')   # injecté via docker-compose
MINIO_SECRET_KEY      = os.environ.get('MINIO_SECRET_KEY')   # injecté via docker-compose
MINIO_BUCKET          = 'raffinerie-raw'          # bucket écrit par traitement_kpi.py
MINIO_PREFIX          = 'raw/'                    # préfixe des fichiers JSON bruts
MINIO_RETRAIN_AFTER_N = 500   # nb de points réels par type avant ré-entraînement
MINIO_CHECK_INTERVAL  = 3600  # vérifier MinIO toutes les 60 min (en secondes)

# Plages normales par type de capteur — synchronisées avec PLAGES_DEFAUT du simulateur
# simulateur_capteurs.py lignes 69-128
NORMAL_RANGES = {
    'Température': (20.0,  280.0),   # normal_min/max simulateur
    'Pression':    (1.0,   120.0),
    'Débit':       (10.0,  900.0),
    'Vibration':   (0.1,   8.0),
    'Niveau':      (20.0,  80.0),
    'H2S gazeux':  (0.0,   30.0),  # élargi : corrélation T→H2S génère des valeurs jusqu'à 30 ppm
}

# Anti-spam : délai minimum (secondes) entre deux alertes pour le même capteur
ALERT_COOLDOWN = 30
_last_alert_ts = {}   # {(capteur_id, source): timestamp}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — ISOLATION FOREST
# ═══════════════════════════════════════════════════════════════════════════════

class IsolationForestDetector:
    """
    Détecte les anomalies ponctuelles (valeur isolée statistiquement).
    Entraîné sur données synthétiques représentant un fonctionnement normal.
    """

    def __init__(self):
        self.models  = {}
        self.scalers = {}
        self._train()

    def _train(self):
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        log.info("🧠 [IF] Entraînement Isolation Forest...")
        np.random.seed(42)

        for sensor_type, (low, high) in NORMAL_RANGES.items():
            mid   = (low + high) / 2
            sigma = (high - low) / 6   # ≈ 99.7 % des valeurs dans [low, high]

            # 500 points normaux + 25 anomalies pour calibrer contamination=0.05
            normal    = np.random.normal(mid, sigma, (500, 1))
            anomalies = np.concatenate([
                np.random.uniform(high * 1.2, high * 2.0, (13, 1)),
                np.random.uniform(low  * 0.0, low  * 0.5 if low > 0 else -high, (12, 1)),
            ])
            X_train = np.vstack([normal, anomalies])

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X_train)

            model = IsolationForest(
                n_estimators=150,
                contamination=0.05,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X_scaled)

            self.models[sensor_type]  = model
            self.scalers[sensor_type] = scaler

        log.info(f"✅ [IF] Prêt pour {len(self.models)} types de capteurs")

    def retrain_from_minio(self) -> bool:
        """
        Lit les données réelles stockées dans MinIO par Spark (traitement_kpi.py)
        et ré-entraîne l'Isolation Forest si suffisamment de points sont disponibles.
        Retourne True si le ré-entraînement a eu lieu, False sinon.
        """
        try:
            from minio import Minio
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler

            client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=False,
            )

            # Vérifier que le bucket existe
            if not client.bucket_exists(MINIO_BUCKET):
                log.warning(f"[IF] Bucket MinIO '{MINIO_BUCKET}' non trouvé — ré-entraînement ignoré")
                return False

            # Lire tous les fichiers JSON du bucket
            data_by_type = {t: [] for t in NORMAL_RANGES}
            objects = list(client.list_objects(MINIO_BUCKET, prefix=MINIO_PREFIX, recursive=True))
            log.info(f"[IF] MinIO : {len(objects)} fichiers trouvés dans {MINIO_BUCKET}/{MINIO_PREFIX}")

            for obj in objects:
                try:
                    response = client.get_object(MINIO_BUCKET, obj.object_name)
                    content  = response.read().decode('utf-8')
                    response.close()

                    # Chaque ligne est un objet JSON (format JSON Lines)
                    for line in content.strip().split('\n'):
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        sensor_type = record.get('type_capteur', '')
                        valeur      = record.get('valeur')
                        mode        = record.get('mode', 'normal')
                        # N'utiliser QUE les données normales pour entraîner le modèle
                        if sensor_type in data_by_type and valeur is not None and mode == 'normal':
                            data_by_type[sensor_type].append(float(valeur))
                except Exception as e:
                    log.warning(f"[IF] Erreur lecture fichier MinIO {obj.object_name}: {e}")
                    continue

            # Vérifier si on a assez de données par type
            types_ok = {t: vals for t, vals in data_by_type.items() if len(vals) >= MINIO_RETRAIN_AFTER_N}
            if not types_ok:
                counts = {t: len(v) for t, v in data_by_type.items()}
                log.info(f"[IF] Pas encore assez de données réelles (besoin {MINIO_RETRAIN_AFTER_N}/type) : {counts}")
                return False

            # ─── Ré-entraînement sur les données réelles ─────────────────────
            log.info(f"🔄 [IF] Ré-entraînement sur données MinIO réelles pour {list(types_ok.keys())}...")
            for sensor_type, valeurs in types_ok.items():
                X_real = np.array(valeurs).reshape(-1, 1)

                scaler   = StandardScaler()
                X_scaled = scaler.fit_transform(X_real)

                model = IsolationForest(
                    n_estimators=150,
                    contamination=0.05,
                    random_state=42,
                    n_jobs=-1,
                )
                model.fit(X_scaled)

                # Mise à jour atomique du modèle (thread-safe)
                self.models[sensor_type]  = model
                self.scalers[sensor_type] = scaler
                log.info(f"  ✅ {sensor_type} ré-entraîné sur {len(valeurs)} points réels (MinIO)")

            log.info(f"✅ [IF] Hybrid Learning terminé — modèles mis à jour depuis MinIO")
            return True

        except ImportError:
            log.warning("[IF] Package 'minio' non installé — ré-entraînement MinIO ignoré")
            return False
        except Exception as e:
            log.error(f"[IF] Erreur ré-entraînement MinIO : {e}")
            return False

    def predict(self, sensor_type: str, valeur: float):
        """Retourne (is_anomaly: bool, score: float)."""
        if sensor_type not in self.models:
            return False, 0.0

        X = self.scalers[sensor_type].transform([[valeur]])
        pred  = self.models[sensor_type].predict(X)[0]       # -1=anomalie  1=normal
        score = self.models[sensor_type].score_samples(X)[0] # plus négatif = plus anormal
        return (pred == -1), abs(score)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — LSTM AUTOENCODER
# ═══════════════════════════════════════════════════════════════════════════════

TENSORFLOW_AVAILABLE = False
try:
    import tensorflow as tf
    from tensorflow import keras
    tf.get_logger().setLevel('ERROR')
    TENSORFLOW_AVAILABLE = True
    log.info("✅ TensorFlow disponible — LSTM activé")
except ImportError:
    log.warning("⚠️  TensorFlow non installé — LSTM désactivé (Isolation Forest actif)")


class LSTMDetector:
    """
    Détecte les dérives temporelles progressives non visibles point par point.
    Architecture : Encoder LSTM → Bottleneck → Decoder LSTM (Autoencoder)
    Anomalie = erreur de reconstruction > seuil (95e percentile sur données normales)
    """

    SEQ_LEN = 20   # fenêtre de 20 mesures consécutives par capteur

    def __init__(self):
        self.models     = {}
        self.scalers    = {}
        self.thresholds = {}
        self.buffers    = {}   # {capteur_id: [valeurs récentes]}
        self._sensor_type_map = {}  # {capteur_id: sensor_type}
        if TENSORFLOW_AVAILABLE:
            self._train()

    def _build_model(self):
        inp = keras.Input(shape=(self.SEQ_LEN, 1))
        # Encoder
        x = keras.layers.LSTM(32, activation='tanh', return_sequences=True)(inp)
        x = keras.layers.LSTM(16, activation='tanh', return_sequences=False)(x)
        # Bottleneck
        x = keras.layers.RepeatVector(self.SEQ_LEN)(x)
        # Decoder
        x = keras.layers.LSTM(16, activation='tanh', return_sequences=True)(x)
        x = keras.layers.LSTM(32, activation='tanh', return_sequences=True)(x)
        out = keras.layers.TimeDistributed(keras.layers.Dense(1))(x)
        model = keras.Model(inp, out)
        model.compile(optimizer='adam', loss='mse')
        return model

    def _train(self):
        from sklearn.preprocessing import MinMaxScaler

        log.info("🧠 [LSTM] Entraînement LSTM Autoencoder...")
        np.random.seed(42)

        for sensor_type, (low, high) in NORMAL_RANGES.items():
            mid = (low + high) / 2
            amp = (high - low) / 4

            # Signal sinusoïdal + bruit gaussien → comportement normal réaliste
            t      = np.linspace(0, 8 * np.pi, 3000)
            signal = mid + amp * np.sin(t) + np.random.normal(0, amp * 0.08, len(t))

            scaler = MinMaxScaler(feature_range=(-1, 1))
            signal_scaled = scaler.fit_transform(signal.reshape(-1, 1)).flatten()

            # Construire les séquences d'entraînement
            seqs = np.array([
                signal_scaled[i: i + self.SEQ_LEN]
                for i in range(len(signal_scaled) - self.SEQ_LEN)
            ]).reshape(-1, self.SEQ_LEN, 1)

            model = self._build_model()
            model.fit(
                seqs, seqs,
                epochs=15,
                batch_size=64,
                validation_split=0.1,
                verbose=0,
            )

            # Seuil = 95e percentile des erreurs sur données normales
            preds  = model.predict(seqs, verbose=0)
            errors = np.mean(np.abs(seqs - preds), axis=(1, 2))
            threshold = float(np.percentile(errors, 95))

            self.models[sensor_type]    = model
            self.scalers[sensor_type]   = scaler
            self.thresholds[sensor_type] = threshold
            log.info(f"  [{sensor_type}] seuil={threshold:.5f}")

        log.info(f"✅ [LSTM] Prêt pour {len(self.models)} types de capteurs")

    def add_measurement(self, capteur_id: int, sensor_type: str, valeur: float):
        """
        Ajoute une mesure au buffer et détecte si la séquence est anormale.
        Retourne (is_anomaly: bool, ratio: float)
        ratio = erreur / seuil  →  > 1 = anomalie confirmée
        """
        if not TENSORFLOW_AVAILABLE or sensor_type not in self.models:
            return False, 0.0

        self._sensor_type_map[capteur_id] = sensor_type
        buf = self.buffers.setdefault(capteur_id, [])
        buf.append(valeur)
        if len(buf) > self.SEQ_LEN:
            buf.pop(0)

        if len(buf) < self.SEQ_LEN:
            return False, 0.0   # pas encore assez de données

        scaler    = self.scalers[sensor_type]
        model     = self.models[sensor_type]
        threshold = self.thresholds[sensor_type]

        seq_scaled = scaler.transform(
            np.array(buf).reshape(-1, 1)
        ).reshape(1, self.SEQ_LEN, 1)

        pred  = model.predict(seq_scaled, verbose=0)
        error = float(np.mean(np.abs(seq_scaled - pred)))
        ratio = error / threshold if threshold > 0 else 0.0

        return ratio > 1.0, ratio


# ═══════════════════════════════════════════════════════════════════════════════
# INSERTION ALERTE EN BASE
# ═══════════════════════════════════════════════════════════════════════════════

def _can_alert(capteur_id: int, source: str) -> bool:
    """Retourne True si on peut émettre une alerte (anti-spam)."""
    key = (capteur_id, source)
    now = time.time()
    if now - _last_alert_ts.get(key, 0) < ALERT_COOLDOWN:
        return False
    _last_alert_ts[key] = now
    return True


def inserer_alerte(conn, capteur_id: int, valeur: float, message: str, priorite: str):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alertes
                    (capteur_id, valeur_declencheur, message, priorite, statut, timestamp_alerte)
                VALUES (%s, %s, %s, %s, 'ouverte', NOW())
            """, (capteur_id, round(float(valeur), 4), message, priorite))
        conn.commit()
        log.info(f"🚨 [{priorite.upper()}] capteur={capteur_id} | {message[:70]}")
    except Exception as e:
        conn.rollback()
        log.error(f"Erreur DB: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("  RaffineryWatch — Détection d'anomalies IA")
    log.info("  Phase 1 : Isolation Forest")
    log.info("  Phase 2 : LSTM Autoencoder" + (" [ACTIF]" if TENSORFLOW_AVAILABLE else " [DÉSACTIVÉ]"))
    log.info("=" * 60)

    # Initialiser les modèles — entraînement synthétique immédiat (cold start)
    if_detector   = IsolationForestDetector()
    lstm_detector = LSTMDetector()

    # ─── Thread de ré-entraînement MinIO (hybrid learning) ────────────────────
    def minio_retrain_loop():
        """
        Tourne en arrière-plan. Vérifie MinIO toutes les MINIO_CHECK_INTERVAL secondes.
        Si assez de données réelles sont disponibles, ré-entraîne l'Isolation Forest.
        Le modèle reste opérationnel pendant le ré-entraînement (pas d'interruption).
        """
        log.info(f"🔄 [Hybrid] Thread MinIO démarré — vérification toutes les {MINIO_CHECK_INTERVAL//60} min")
        time.sleep(60)  # attendre 1 min avant la première vérification (laisser Spark écrire)
        while True:
            try:
                retrained = if_detector.retrain_from_minio()
                if retrained:
                    log.info("🎯 [Hybrid] Modèle IF mis à jour depuis MinIO — meilleure précision attendue")
            except Exception as e:
                log.error(f"[Hybrid] Erreur thread MinIO : {e}")
            time.sleep(MINIO_CHECK_INTERVAL)

    retrain_thread = threading.Thread(target=minio_retrain_loop, daemon=True, name="minio-retrain")
    retrain_thread.start()

    # Connexion TimescaleDB avec retry
    log.info("🔌 Connexion TimescaleDB...")
    conn = None
    for attempt in range(15):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            log.info("✅ TimescaleDB connecté")
            break
        except Exception as e:
            log.warning(f"  Tentative {attempt + 1}/15 : {e}")
            time.sleep(5)
    if conn is None:
        log.error("❌ Impossible de se connecter à TimescaleDB — arrêt")
        return

    # Consumer Kafka avec retry
    log.info("🔌 Connexion Kafka...")
    for attempt in range(15):
        try:
            consumer = Consumer({
                'bootstrap.servers': KAFKA_BROKER,
                'group.id':          KAFKA_GROUP,
                'auto.offset.reset': 'latest',
            })
            consumer.subscribe([KAFKA_TOPIC])
            # Vérifier que la connexion fonctionne
            consumer.poll(2.0)
            log.info(f"✅ Kafka connecté sur '{KAFKA_BROKER}' — topic '{KAFKA_TOPIC}'")
            break
        except Exception as e:
            log.warning(f"  Tentative {attempt + 1}/15 : {e}")
            time.sleep(5)

    log.info("🔍 Détection en cours...\n")
    counters = {'total': 0, 'if_alerts': 0, 'lstm_alerts': 0}

    try:
        while True:
            # Maintenir la connexion DB active
            try:
                conn.isolation_level  # ping léger
            except Exception:
                log.warning("Reconnexion DB...")
                try:
                    conn = psycopg2.connect(**DB_CONFIG)
                except Exception:
                    pass

            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error(f"Kafka error: {msg.error()}")
                continue

            try:
                data = json.loads(msg.value().decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            capteur_id  = data.get('capteur_id')
            sensor_type = data.get('type_capteur', '')
            valeur      = data.get('valeur', 0.0)
            identifiant = data.get('identifiant', f'capteur_{capteur_id}')

            if capteur_id is None or not sensor_type or valeur is None:
                continue

            counters['total'] += 1

            # ── Phase 1 : Isolation Forest ──────────────────────────────
            is_anom_if, score_if = if_detector.predict(sensor_type, valeur)
            if is_anom_if and _can_alert(capteur_id, 'IF'):
                msg_if = (
                    f"[IA - Isolation Forest] Anomalie ponctuelle sur {identifiant} "
                    f"({sensor_type}) — valeur: {valeur:.2f} | score: {score_if:.3f}"
                )
                inserer_alerte(conn, capteur_id, valeur, msg_if, 'haute')
                counters['if_alerts'] += 1

            # ── Phase 2 : LSTM Autoencoder ──────────────────────────────
            is_anom_lstm, ratio_lstm = lstm_detector.add_measurement(
                capteur_id, sensor_type, valeur
            )
            if is_anom_lstm and _can_alert(capteur_id, 'LSTM'):
                msg_lstm = (
                    f"[IA - LSTM] Dérive temporelle sur {identifiant} "
                    f"({sensor_type}) — erreur reconstruction: {ratio_lstm:.2f}x seuil"
                )
                inserer_alerte(conn, capteur_id, valeur, msg_lstm, 'critique')
                counters['lstm_alerts'] += 1

            # Log de progression toutes les 100 mesures
            if counters['total'] % 100 == 0:
                log.info(
                    f"📊 {counters['total']} mesures traitées | "
                    f"IF: {counters['if_alerts']} alertes | "
                    f"LSTM: {counters['lstm_alerts']} alertes"
                )

    except KeyboardInterrupt:
        log.info("\nArrêt demandé (Ctrl+C)")
    finally:
        consumer.close()
        if conn and not conn.closed:
            conn.close()
        log.info(
            f"Bilan final : {counters['total']} mesures | "
            f"{counters['if_alerts']} alertes IF | "
            f"{counters['lstm_alerts']} alertes LSTM"
        )


if __name__ == '__main__':
    main()
