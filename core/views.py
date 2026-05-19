"""
Vues Django — RaffineryWatch
Toutes les requêtes IoT utilisent psycopg2 directement (Django ORM via managed=False models).
"""
import json
import subprocess
import threading
import os
from datetime import datetime, timezone

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_POST

# ── État global simulateur ────────────────────────────────────────────────────
_sim_proc   = None
_sim_lock   = threading.Lock()
_sim_params = {"nb": 20, "freq": 2}


def _raw(sql):
    """Exécute du SQL brut et retourne les rows."""
    with connection.cursor() as cur:
        cur.execute(sql)
        try:
            return cur.fetchall()
        except Exception:
            return []


# ── Dashboard ─────────────────────────────────────────────────────────────────
@login_required
def dashboard(request):
    # KPIs
    kpis = {}
    try:
        rows = _raw("SELECT * FROM v_kpis;")
        kpis = {r[0]: r[1] for r in rows}
    except Exception:
        pass

    # Alertes ouvertes (10 dernières)
    alertes = []
    try:
        alertes = _raw("""
            SELECT a.id, c.code, tc.nom, a.message, a.priorite,
                   TO_CHAR(a.created_at AT TIME ZONE 'UTC', 'DD/MM HH24:MI')
            FROM alertes a
            JOIN capteurs c ON c.id = a.capteur_id
            JOIN types_capteurs tc ON tc.id = c.type_capteur_id
            WHERE a.statut = 'ouverte'
            ORDER BY CASE a.priorite
                WHEN 'critique' THEN 1 WHEN 'haute' THEN 2
                WHEN 'moyenne'  THEN 3 ELSE 4 END,
                a.created_at DESC
            LIMIT 10;
        """)
    except Exception:
        pass

    # Moyennes des 5 dernières minutes par type
    mesures = []
    try:
        mesures = _raw("""
            SELECT tc.nom, ROUND(AVG(m.valeur)::numeric, 2), tc.unite_mesure,
                   COUNT(DISTINCT m.capteur_id)
            FROM mesures m
            JOIN capteurs c ON c.id = m.capteur_id
            JOIN types_capteurs tc ON tc.id = c.type_capteur_id
            WHERE m.timestamp >= NOW() - INTERVAL '5 minutes'
            GROUP BY tc.nom, tc.unite_mesure
            ORDER BY tc.nom;
        """)
    except Exception:
        pass

    return render(request, 'core/dashboard.html', {
        'kpis':        kpis,
        'alertes':     alertes,
        'mesures':     mesures,
        'sim_running': _sim_running(),
        'sim_params':  _sim_params,
    })


# ── Simulation ────────────────────────────────────────────────────────────────
def _sim_running():
    global _sim_proc
    with _sim_lock:
        return _sim_proc is not None and _sim_proc.poll() is None


@login_required
def simulation(request):
    global _sim_proc, _sim_params

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'start':
            nb   = int(request.POST.get('nb',   20))
            freq = int(request.POST.get('freq', 2))
            with _sim_lock:
                if _sim_proc and _sim_proc.poll() is None:
                    _sim_proc.terminate()
                    _sim_proc.wait(timeout=3)
                sim_path = os.path.join(os.path.dirname(__file__), '..', 'simulateur_capteurs.py')
                _sim_proc  = subprocess.Popen(
                    ['python', sim_path, '--nb', str(nb), '--freq', str(freq)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _sim_params = {'nb': nb, 'freq': freq}
            messages.success(request, f'Simulateur démarré — {nb} capteurs, 1 mesure toutes les {freq}s')

        elif action == 'stop':
            with _sim_lock:
                if _sim_proc and _sim_proc.poll() is None:
                    _sim_proc.terminate()
                    _sim_proc.wait(timeout=5)
                    _sim_proc = None
            messages.warning(request, 'Simulateur arrêté.')

        return redirect('simulation')

    # Répartition modes (dernière minute)
    modes = None
    try:
        rows = _raw("""
            SELECT
                COUNT(*) FILTER (WHERE m.valeur BETWEEN c.seuil_min AND c.seuil_max),
                COUNT(*) FILTER (WHERE m.valeur < c.seuil_min OR m.valeur > c.seuil_max),
                COUNT(*)
            FROM (
                SELECT DISTINCT ON (capteur_id) capteur_id, valeur
                FROM mesures ORDER BY capteur_id, timestamp DESC
            ) m
            JOIN capteurs c ON c.id = m.capteur_id
            WHERE c.seuil_min IS NOT NULL AND c.seuil_max IS NOT NULL;
        """)
        if rows:
            modes = rows[0]
    except Exception:
        pass

    # Config physique des capteurs
    capteurs_config = []
    try:
        capteurs_config = _raw("""
            SELECT tc.nom, tc.unite_mesure,
                   tc.valeur_min_physique, tc.valeur_max_physique,
                   COUNT(c.id) FILTER (WHERE c.statut = 'actif')
            FROM types_capteurs tc
            LEFT JOIN capteurs c ON c.type_capteur_id = tc.id
            GROUP BY tc.nom, tc.unite_mesure, tc.valeur_min_physique, tc.valeur_max_physique
            ORDER BY tc.nom;
        """)
    except Exception:
        pass

    return render(request, 'core/simulation.html', {
        'sim_running':     _sim_running(),
        'sim_params':      _sim_params,
        'modes':           modes,
        'capteurs_config': capteurs_config,
    })


# ── Alertes ───────────────────────────────────────────────────────────────────
@login_required
def alertes(request):
    toutes = []
    try:
        toutes = _raw("""
            SELECT a.id, c.code, z.nom, tc.nom,
                   a.message, a.priorite, a.statut,
                   TO_CHAR(a.created_at AT TIME ZONE 'UTC', 'DD/MM/YYYY HH24:MI')
            FROM alertes a
            JOIN capteurs c ON c.id = a.capteur_id
            JOIN types_capteurs tc ON tc.id = c.type_capteur_id
            LEFT JOIN zones z ON z.id = c.zone_id
            ORDER BY
                CASE a.statut WHEN 'ouverte' THEN 0 ELSE 1 END,
                CASE a.priorite WHEN 'critique' THEN 1 WHEN 'haute' THEN 2
                                WHEN 'moyenne' THEN 3 ELSE 4 END,
                a.created_at DESC
            LIMIT 50;
        """)
    except Exception:
        pass

    stats = []
    try:
        stats = _raw("SELECT statut, COUNT(*) FROM alertes GROUP BY statut ORDER BY statut;")
    except Exception:
        pass

    return render(request, 'core/alertes.html', {'alertes': toutes, 'stats': stats})


@login_required
@require_POST
def acquitter(request, alerte_id):
    try:
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE alertes
                SET statut = 'acquittee', acquitte_at = NOW(), acquitte_par = %s
                WHERE id = %s AND statut = 'ouverte';
            """, [request.user.id, alerte_id])
        messages.success(request, f'Alerte #{alerte_id} acquittée.')
    except Exception as e:
        messages.error(request, f'Erreur : {e}')
    return redirect('alertes')


# ── Pipeline ──────────────────────────────────────────────────────────────────
@login_required
def pipeline(request):
    services = _get_docker_status()

    mesures_last_min = '—'
    try:
        rows = _raw("SELECT COUNT(*) FROM mesures WHERE timestamp >= NOW() - INTERVAL '1 minute';")
        if rows:
            mesures_last_min = rows[0][0]
    except Exception:
        pass

    return render(request, 'core/pipeline.html', {
        'services':        services,
        'mesures_last_min': mesures_last_min,
        'sim_running':     _sim_running(),
    })


def _get_docker_status():
    services_def = [
        ('mqtt',         'Mosquitto MQTT',  ':1883'),
        ('zookeeper',    'Zookeeper',       ':2181'),
        ('kafka',        'Kafka',           ':9092'),
        ('spark-master', 'Spark Master',    ':8081'),
        ('spark-worker', 'Spark Worker',    '—'),
        ('minio',        'MinIO',           ':9001'),
        ('timescaledb',  'TimescaleDB',     ':5432'),
        ('grafana',      'Grafana',         ':3000'),
    ]
    try:
        r = subprocess.run(
            ['docker', 'compose', 'ps', '--format', 'json'],
            capture_output=True, shell=False,
            cwd=os.path.join(os.path.dirname(__file__), '..'),
        )
        containers = {}
        for line in r.stdout.decode('utf-8', errors='replace').splitlines():
            try:
                obj = json.loads(line)
                svc = obj.get('Service', '')
                containers[svc] = obj.get('State', 'unknown')
            except Exception:
                pass
        result = []
        for key, label, port in services_def:
            state = containers.get(key, 'absent')
            result.append({'service': key, 'label': label, 'port': port, 'state': state})
        return result
    except Exception:
        return [{'service': s, 'label': l, 'port': p, 'state': 'erreur'}
                for s, l, p in services_def]


# ── API JSON ──────────────────────────────────────────────────────────────────
@login_required
def api_mesures(request, type_nom):
    """
    Retourne une série temporelle pour un type de capteur.
    Moyenne par minute sur les 30 dernières minutes.
    """
    rows = []
    unite = ''
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    date_trunc('minute', m.timestamp) AS minute,
                    ROUND(AVG(m.valeur)::numeric, 2)   AS val,
                    tc.unite_mesure
                FROM mesures m
                JOIN capteurs c ON c.id = m.capteur_id
                JOIN types_capteurs tc ON tc.id = c.type_capteur_id
                WHERE tc.nom = %s
                  AND m.timestamp >= NOW() - INTERVAL '30 minutes'
                GROUP BY minute, tc.unite_mesure
                ORDER BY minute ASC;
            """, [type_nom])
            rows = cur.fetchall()
    except Exception:
        pass

    labels  = []
    data    = []
    for row in rows:
        ts   = row[0]
        val  = float(row[1]) if row[1] is not None else None
        unite = row[2] or ''
        labels.append(ts.strftime('%H:%M') if hasattr(ts, 'strftime') else str(ts)[:16])
        data.append(val)

    # Valeur courante (dernière mesure toutes capteurs confondus)
    current = None
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT ROUND(AVG(m.valeur)::numeric, 2)
                FROM mesures m
                JOIN capteurs c ON c.id = m.capteur_id
                JOIN types_capteurs tc ON tc.id = c.type_capteur_id
                WHERE tc.nom = %s
                  AND m.timestamp >= NOW() - INTERVAL '1 minute';
            """, [type_nom])
            r = cur.fetchone()
            if r and r[0] is not None:
                current = float(r[0])
    except Exception:
        pass

    return JsonResponse({
        'type':    type_nom,
        'unite':   unite,
        'labels':  labels,
        'data':    data,
        'current': current,
    })


@login_required
def api_status(request):
    mesures = []
    try:
        rows = _raw("""
            SELECT tc.nom, ROUND(AVG(m.valeur)::numeric, 2), tc.unite_mesure
            FROM mesures m
            JOIN capteurs c ON c.id = m.capteur_id
            JOIN types_capteurs tc ON tc.id = c.type_capteur_id
            WHERE m.timestamp >= NOW() - INTERVAL '1 minute'
            GROUP BY tc.nom, tc.unite_mesure ORDER BY tc.nom;
        """)
        mesures = [{'type': r[0], 'moyenne': str(r[1]), 'unite': r[2]} for r in rows]
    except Exception:
        pass

    alertes_count = 0
    try:
        rows = _raw("SELECT COUNT(*) FROM alertes WHERE statut = 'ouverte';")
        if rows:
            alertes_count = rows[0][0]
    except Exception:
        pass

    return JsonResponse({
        'simulateur':      'running' if _sim_running() else 'stopped',
        'params':          _sim_params,
        'alertes_ouvertes': alertes_count,
        'mesures':         mesures,
        'timestamp':       datetime.now(timezone.utc).isoformat(),
    })
