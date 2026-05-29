"""
Vues Django — RaffineryWatch
Toutes les requêtes IoT utilisent psycopg2 directement (Django ORM via managed=False models).
"""
import json
import subprocess
import threading
import os
from datetime import datetime, timezone

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_POST
from .models import Capteur, Zone, TypeCapteur, Equipement, RegleAlerte
from .forms import CapteurForm, ZoneForm, RegleAlerteForm, UtilisateurCreateForm, UtilisateurEditForm, InscriptionForm
from django.contrib.auth import login as auth_login

# Décorateur : accès réservé aux admins
def admin_required(view_func):
    return user_passes_test(lambda u: u.is_staff, login_url='/')(view_func)

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


# ── Inscription ───────────────────────────────────────────────────────────────
def inscription(request):
    """Page d'inscription publique — accessible sans être connecté."""
    if request.user.is_authenticated:
        return redirect('dashboard')  # Déjà connecté → pas besoin de s'inscrire

    if request.method == 'POST':
        form = InscriptionForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            user = User.objects.create_user(
                username   = d['username'],
                email      = d['email'],
                password   = d['password1'],
                first_name = d['first_name'],
                last_name  = d['last_name'],
            )
            # Nouveau compte = opérateur par défaut (is_staff=False)
            # On redirige vers la connexion — l'utilisateur se connecte ensuite
            messages.success(request, f'Compte créé ! Connectez-vous avec votre identifiant « {user.username} ».')
            return redirect('login')
    else:
        form = InscriptionForm()

    return render(request, 'registration/inscription.html', {'form': form})


# ── Dashboard ─────────────────────────────────────────────────────────────────
@login_required
def dashboard(request):
    # KPIs — requête nommée pour correspondre aux variables du template
    kpis = {}
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM capteurs WHERE statut = 'actif')  AS capteurs_actifs,
                    (SELECT COUNT(*) FROM capteurs)                          AS total_capteurs,
                    (SELECT COUNT(*) FROM alertes  WHERE statut = 'ouverte') AS alertes_ouvertes,
                    (SELECT COUNT(*) FROM mesures
                       WHERE timestamp >= NOW() - INTERVAL '5 minutes')     AS mesures_recentes;
            """)
            cols = [desc[0] for desc in cur.description]
            row  = cur.fetchone()
            kpis = dict(zip(cols, row)) if row else {}
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
                import sys as _sys
                _sim_proc  = subprocess.Popen(
                    [_sys.executable, sim_path, '--nb', str(nb), '--freq', str(freq)],
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

    lois_physiques = [
        {'icone': '🌡️', 'nom': 'Gay-Lussac (T↔P)',  'desc': 'Hausse temp → hausse pression proportionnelle'},
        {'icone': '💧', 'nom': 'Cavitation (Q↔Vib)', 'desc': 'Débit < 40 % nominal → vibrations ×3'},
        {'icone': '📉', 'nom': 'Niveau → Débit',     'desc': 'Niveau bas → pompe aspire à vide'},
        {'icone': '⚗️', 'nom': 'T → H2S',            'desc': 'Surchauffe → évaporation H2S accrue'},
        {'icone': '🕐', 'nom': 'Cycle journalier',   'desc': 'Modulation sinusoïdale ±8 % sur 24 h'},
        {'icone': '⚠️', 'nom': 'Cascade panne',      'desc': 'Panne pompe → défaillance en chaîne'},
    ]

    return render(request, 'core/simulation.html', {
        'sim_running':     _sim_running(),
        'sim_params':      _sim_params,
        'modes':           modes,
        'capteurs_config': capteurs_config,
        'lois_physiques':  lois_physiques,
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
def alerte_detail(request, alerte_id):
    alerte = {}
    mesures_voisines = []
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    a.id, c.code, z.nom, tc.nom, tc.unite_mesure,
                    a.message, a.priorite, a.statut,
                    TO_CHAR(a.created_at AT TIME ZONE 'UTC', 'DD/MM/YYYY HH24:MI:SS'),
                    TO_CHAR(a.timestamp_acquittement AT TIME ZONE 'UTC', 'DD/MM/YYYY HH24:MI:SS'),
                    a.valeur_declencheur,
                    c.seuil_min, c.seuil_max,
                    c.id AS capteur_id
                FROM alertes a
                JOIN capteurs c  ON c.id  = a.capteur_id
                JOIN types_capteurs tc ON tc.id = c.type_capteur_id
                LEFT JOIN zones z ON z.id = c.zone_id
                WHERE a.id = %s;
            """, [alerte_id])
            row = cur.fetchone()
            if row:
                alerte = {
                    'id': row[0], 'code': row[1], 'zone': row[2],
                    'type': row[3], 'unite': row[4], 'message': row[5],
                    'priorite': row[6], 'statut': row[7],
                    'created_at': row[8], 'acquittee_at': row[9],
                    'valeur': row[10], 'seuil_min': row[11], 'seuil_max': row[12],
                    'capteur_id': row[13],
                    'is_ia': '[IA -' in (row[5] or ''),
                    'is_lstm': '[IA - LSTM]' in (row[5] or ''),
                    'is_if': '[IA - Isolation Forest]' in (row[5] or ''),
                }
    except Exception:
        pass

    # 10 mesures du capteur autour de l'heure de l'alerte
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT ROUND(m.valeur::numeric, 3),
                       TO_CHAR(m.timestamp AT TIME ZONE 'UTC', 'HH24:MI:SS')
                FROM mesures m
                WHERE m.capteur_id = %s
                  AND m.timestamp BETWEEN
                      (SELECT created_at FROM alertes WHERE id = %s) - INTERVAL '2 minutes'
                      AND
                      (SELECT created_at FROM alertes WHERE id = %s) + INTERVAL '2 minutes'
                ORDER BY m.timestamp DESC
                LIMIT 10;
            """, [alerte.get('capteur_id'), alerte_id, alerte_id])
            mesures_voisines = cur.fetchall()
    except Exception:
        pass

    # Autres alertes du même capteur
    autres_alertes = []
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.message, a.priorite,
                       TO_CHAR(a.created_at AT TIME ZONE 'UTC', 'DD/MM HH24:MI')
                FROM alertes a
                WHERE a.capteur_id = %s AND a.id != %s
                ORDER BY a.created_at DESC
                LIMIT 5;
            """, [alerte.get('capteur_id'), alerte_id])
            autres_alertes = cur.fetchall()
    except Exception:
        pass

    return render(request, 'core/alerte_detail.html', {
        'alerte': alerte,
        'mesures_voisines': mesures_voisines,
        'autres_alertes': autres_alertes,
    })


@login_required
@require_POST
def acquitter(request, alerte_id):
    try:
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE alertes
                SET statut = 'acquittee',
                    timestamp_acquittement = NOW(),
                    acquittee_par = %s
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

    pipeline_steps = [
        {'label': 'Simulateur\nPython',  'icon': 'cpu',              'color': '#f97316'},
        {'label': 'Mosquitto\n:1883',    'icon': 'router',           'color': '#3b82f6'},
        {'label': 'Kafka\n:9092',        'icon': 'boxes',            'color': '#8b5cf6'},
        {'label': 'Spark\n:7077',        'icon': 'lightning-charge', 'color': '#eab308'},
        {'label': 'TimescaleDB\n:5432',  'icon': 'database',         'color': '#22c55e'},
        {'label': 'MinIO\n:9002',        'icon': 'bucket',           'color': '#f97316'},
        {'label': 'Grafana\n:3000',      'icon': 'graph-up',         'color': '#ff6b35'},
    ]

    return render(request, 'core/pipeline.html', {
        'services':         services,
        'mesures_last_min': mesures_last_min,
        'sim_running':      _sim_running(),
        'pipeline_steps':   pipeline_steps,
    })


def _check_port(host, port, timeout=1):
    """Vérifie si un port TCP est ouvert (sonde réseau)."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 'running'
    except Exception:
        return 'down'


def _get_docker_status():
    # Django tourne dans Docker → on sonde les noms de services internes
    # Fallback sur localhost pour execution hors Docker
    services_def = [
        ('mqtt',         'Mosquitto MQTT',  ':1883', 'mqtt',         1883, None),
        ('zookeeper',    'Zookeeper',       ':2181', 'zookeeper',    2181, None),
        ('kafka',        'Kafka',           ':9092', 'kafka',        9092, None),
        ('spark-master', 'Spark Master',    ':8081', 'spark-master', 8080, 'http://localhost:8081'),
        ('spark-worker', 'Spark Worker',    ':8082', 'spark-worker', 8081, None),
        ('minio',        'MinIO',           ':9001', 'minio',        9000, 'http://localhost:9001'),
        ('timescaledb',  'TimescaleDB',     ':5432', 'timescaledb',  5432, None),
        ('grafana',      'Grafana',         ':3000', 'grafana',      3000, 'http://localhost:3000'),
    ]
    result = []
    for key, label, port_label, host, port, url in services_def:
        state = _check_port(host, port)
        if state == 'down':
            state = _check_port('localhost', port)
        result.append({
            'service': key,
            'label':   label,
            'port':    port_label,
            'state':   state,
            'url':     url,
        })
    return result


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


# ══════════════════════════════════════════════════════════════════════════════
# SECTION GESTION — CRUD dans l'interface (hors admin Django)
# ══════════════════════════════════════════════════════════════════════════════

# ── Hub ───────────────────────────────────────────────────────────────────────
@login_required
def gestion_index(request):
    stats = {}
    try:
        stats['capteurs']   = Capteur.objects.count()
        stats['zones']      = Zone.objects.count()
        stats['regles']     = RegleAlerte.objects.count()
        stats['utilisateurs'] = User.objects.count()
    except Exception:
        pass
    return render(request, 'core/gestion/index.html', {'stats': stats})


# ── Capteurs ──────────────────────────────────────────────────────────────────
@login_required
def gestion_capteurs(request):
    if request.method == 'POST':
        form = CapteurForm(request.POST)
        if form.is_valid():
            # Génère un code unique automatiquement
            d   = form.cleaned_data
            tc  = d['type_capteur']
            prefix = tc.nom[:4].upper().replace(' ', '')
            with connection.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM capteurs WHERE type_capteur_id = %s", [tc.id])
                n = cur.fetchone()[0] + 1
            code = f"{prefix}-{n:03d}-NEW"
            try:
                Capteur.objects.create(
                    code         = code,
                    nom          = d['nom'],
                    type_capteur = d['type_capteur'],
                    zone         = d['zone'],
                    equipement   = d.get('equipement'),
                    statut       = d['statut'],
                    seuil_min    = d.get('seuil_min'),
                    seuil_max    = d.get('seuil_max'),
                )
                messages.success(request, f'Capteur « {d["nom"]} » créé (code : {code}).')
            except Exception as e:
                messages.error(request, f'Erreur : {e}')
            return redirect('gestion_capteurs')
        # form invalide → retombe en bas avec erreurs
    else:
        form = CapteurForm()

    capteurs = Capteur.objects.select_related('type_capteur', 'zone').order_by('code')
    return render(request, 'core/gestion/capteurs.html', {'form': form, 'capteurs': capteurs})


@login_required
def gestion_capteur_edit(request, pk):
    capteur = get_object_or_404(Capteur, pk=pk)
    if request.method == 'POST':
        form = CapteurForm(request.POST, instance=capteur)
        if form.is_valid():
            form.save()
            messages.success(request, f'Capteur « {capteur.nom} » mis à jour.')
            return redirect('gestion_capteurs')
    else:
        form = CapteurForm(instance=capteur)
    return render(request, 'core/gestion/capteur_edit.html', {'form': form, 'capteur': capteur})


@login_required
@require_POST
def gestion_capteur_delete(request, pk):
    capteur = get_object_or_404(Capteur, pk=pk)
    nom = capteur.nom
    try:
        capteur.delete()
        messages.success(request, f'Capteur « {nom} » supprimé.')
    except Exception as e:
        messages.error(request, f'Impossible de supprimer : {e}')
    return redirect('gestion_capteurs')


# ── Zones ─────────────────────────────────────────────────────────────────────
@login_required
def gestion_zones(request):
    if request.method == 'POST':
        form = ZoneForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f'Zone « {form.cleaned_data["nom"]} » créée.')
            return redirect('gestion_zones')
    else:
        form = ZoneForm()

    zones = Zone.objects.all().order_by('nom')
    # Ajouter nb_capteurs et nb_equipements pour chaque zone
    zones_data = []
    for z in zones:
        zones_data.append({
            'obj':           z,
            'nb_capteurs':   Capteur.objects.filter(zone=z).count(),
            'nb_equipements': Equipement.objects.filter(zone=z).count(),
        })
    return render(request, 'core/gestion/zones.html', {'form': form, 'zones': zones_data})


@login_required
def gestion_zone_edit(request, pk):
    zone = get_object_or_404(Zone, pk=pk)
    if request.method == 'POST':
        form = ZoneForm(request.POST, instance=zone)
        if form.is_valid():
            form.save()
            messages.success(request, f'Zone « {zone.nom} » mise à jour.')
            return redirect('gestion_zones')
    else:
        form = ZoneForm(instance=zone)
    return render(request, 'core/gestion/zone_edit.html', {'form': form, 'zone': zone})


@login_required
@require_POST
def gestion_zone_delete(request, pk):
    zone = get_object_or_404(Zone, pk=pk)
    nom = zone.nom
    try:
        zone.delete()
        messages.success(request, f'Zone « {nom} » supprimée.')
    except Exception as e:
        messages.error(request, f'Impossible de supprimer (des capteurs y sont liés ?) : {e}')
    return redirect('gestion_zones')


# ── Règles d'alerte ───────────────────────────────────────────────────────────
@login_required
def gestion_regles(request):
    if request.method == 'POST':
        form = RegleAlerteForm(request.POST)
        if form.is_valid():
            regle = form.save(commit=False)
            # nom obligatoire en DB — généré automatiquement
            d = form.cleaned_data
            cap  = d.get('capteur')
            type_r = d.get('type_regle', '')
            regle.nom = f"Règle {type_r.replace('_',' ')} — {cap.code if cap else '?'}"
            regle.save()
            messages.success(request, f'Règle créée : {regle.nom}')
            return redirect('gestion_regles')
    else:
        form = RegleAlerteForm()

    regles = RegleAlerte.objects.select_related('capteur', 'capteur__type_capteur').order_by('-id')
    return render(request, 'core/gestion/regles.html', {'form': form, 'regles': regles})


@login_required
@require_POST
def gestion_regle_toggle(request, pk):
    regle = get_object_or_404(RegleAlerte, pk=pk)
    regle.actif = not regle.actif
    regle.save(update_fields=['actif'])
    etat = 'activée' if regle.actif else 'désactivée'
    messages.success(request, f'Règle #{pk} {etat}.')
    return redirect('gestion_regles')


@login_required
@require_POST
def gestion_regle_delete(request, pk):
    regle = get_object_or_404(RegleAlerte, pk=pk)
    regle.delete()
    messages.success(request, f'Règle #{pk} supprimée.')
    return redirect('gestion_regles')


# ── Utilisateurs ──────────────────────────────────────────────────────────────
@login_required
@admin_required
def gestion_utilisateurs(request):
    if request.method == 'POST':
        form = UtilisateurCreateForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            u = User.objects.create_user(
                username   = d['username'],
                email      = d['email'],
                password   = d['password'],
                first_name = d.get('first_name', ''),
                last_name  = d.get('last_name', ''),
            )
            u.is_staff = (d['role'] == 'admin')
            u.save()
            messages.success(request, f'Compte « {u.username} » créé.')
            return redirect('gestion_utilisateurs')
    else:
        form = UtilisateurCreateForm()

    utilisateurs = User.objects.all().order_by('username')
    return render(request, 'core/gestion/utilisateurs.html', {'form': form, 'utilisateurs': utilisateurs})


@login_required
@admin_required
def gestion_utilisateur_edit(request, pk):
    u = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        form = UtilisateurEditForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            u.first_name = d['first_name']
            u.last_name  = d['last_name']
            u.email      = d['email']
            u.is_staff   = (d['role'] == 'admin')
            if d.get('new_password'):
                u.set_password(d['new_password'])
            u.save()
            messages.success(request, f'Compte « {u.username} » mis à jour.')
            return redirect('gestion_utilisateurs')
    else:
        form = UtilisateurEditForm(initial={
            'first_name': u.first_name,
            'last_name':  u.last_name,
            'email':      u.email,
            'role':       'admin' if u.is_staff else 'operateur',
        })
    return render(request, 'core/gestion/utilisateur_edit.html', {'form': form, 'u': u})


@login_required
@admin_required
@require_POST
def gestion_utilisateur_delete(request, pk):
    u = get_object_or_404(User, pk=pk)
    if u == request.user:
        messages.error(request, 'Vous ne pouvez pas supprimer votre propre compte.')
        return redirect('gestion_utilisateurs')
    nom = u.username
    u.delete()
    messages.success(request, f'Compte « {nom} » supprimé.')
    return redirect('gestion_utilisateurs')
