import csv
import json
import time
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Max, Min, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from analyse.models import AnalyseSession, DEPARTEMENTS as DEPTS_ANALYSE
from . import oppositions as opp
from . import qualite as q
from . import rendement as rdt
from .db import (PostgisIndisponible, apercu_table, bornes_dates_disponibles,
                 compter_couche, executer_fichier, lire_couche, lister_colonnes,
                 lister_couches_geo, lister_tables, tester_connexion)
from .exclusions import (filtrer_lignes, filtrer_publiees,
                         index_exclusions)
from .forms import (AgentForm, ExclusionForm, ImportForm, ImportShapefileForm,
                    OppositionForm, PeriodeForm)
from .geo import (CRS_TERRAIN, ShapefileIllisible, enveloppe, reprojeter_geojson,
                  transformateur, transformateur_direct)
from .imports import (colonne, en_date, en_float, lire_tableau, normaliser,
                      serialiser)
from .models import (Agent, ControleQualite, DEPARTEMENTS, Opposition,
                     ParcelleExclue, ParcellePublicite, REGION, normaliser_code)
from .shapefiles import (compter_doublons, importer_exclusions,
                         importer_publicite, nettoyer_doublons)


# ── Utilitaires partagés ─────────────────────────────────────────────────────
def _periode(request, jours=30):
    """Lit la période depuis la querystring, avec repli sur les N derniers jours."""
    form = PeriodeForm(request.GET or None, jours_defaut=jours)
    if request.GET and form.is_valid():
        return form, form.cleaned_data['date_debut'], form.cleaned_data['date_fin'], \
            form.cleaned_data.get('departement') or ''
    fin = date.today()
    return form, fin - timedelta(days=jours), fin, ''


def _agents_index():
    """Index des agents par (id_machine, departement) et par id_machine seul."""
    index = {}
    for a in Agent.objects.all():
        index[(a.id_machine, a.departement)] = a
        for d in a.departement.split('/'):
            index[(a.id_machine, d.strip().upper())] = a
        index.setdefault(a.id_machine, a)
    return index


def _charger_parcelles(date_debut, date_fin, departement='', compteur=None):
    """Lit les parcelles de la base terrain, exclusions déjà retirées.

    Les parcelles déclarées « à supprimer » sont masquées ici, une fois pour
    toutes : contrôle qualité, rendements et statistiques travaillent donc
    tous sur le même périmètre.
    """
    lignes = executer_fichier('parcelles', date_debut, date_fin)
    if departement:
        lignes = [l for l in lignes
                  if str(l.get('depart') or '').strip().upper() == departement.upper()]
    lignes, retirees = filtrer_lignes(lignes)
    if compteur is not None:
        compteur['exclues'] = retirees
    return lignes


def _export_csv(nom_fichier, entetes, lignes):
    reponse = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    reponse['Content-Disposition'] = f'attachment; filename="{nom_fichier}"'
    reponse.write('\ufeff')
    writer = csv.writer(reponse, delimiter=';')
    writer.writerow(entetes)
    writer.writerows(lignes)
    return reponse


# ── Connexion à la base terrain ──────────────────────────────────────────────
def connexion(request):
    ok, message, details = tester_connexion()
    tables, plage = [], None
    if ok:
        try:
            tables = lister_tables()
        except PostgisIndisponible as exc:
            messages.warning(request, f"Lecture du catalogue impossible : {exc}")
        try:
            plage = bornes_dates_disponibles()
        except PostgisIndisponible:
            plage = None
    cfg = settings.POSTGIS
    return render(request, 'terrain/connexion.html', {
        'ok': ok, 'message': message, 'details': details,
        'tables': tables, 'plage': plage,
        'config': {'hote': cfg['HOST'], 'port': cfg['PORT'],
                   'base': cfg['NAME'], 'utilisateur': cfg['USER'],
                   'mot_de_passe_defini': bool(cfg['PASSWORD'])},
        'colonne_date_borne': settings.PG_COLONNE_DATE_BORNE,
        'titre': 'Base terrain',
    })


def explorateur(request):
    table = request.GET.get('table', '')
    colonnes, apercu, erreur = [], [], ''
    try:
        tables = lister_tables()
    except PostgisIndisponible as exc:
        tables, erreur = [], str(exc)
    if table and not erreur:
        try:
            colonnes = lister_colonnes(table)
            apercu = apercu_table(table, limite=15)
        except PostgisIndisponible as exc:
            erreur = str(exc)
    entetes = list(apercu[0].keys()) if apercu else []
    return render(request, 'terrain/explorateur.html', {
        'tables': tables, 'table': table, 'colonnes': colonnes,
        'apercu': [[serialiser(l.get(c)) for c in entetes] for l in apercu],
        'entetes': entetes, 'erreur': erreur,
        'titre': 'Explorateur de base',
    })


# ── Contrôle qualité ─────────────────────────────────────────────────────────
def controle_qualite(request):
    form, d1, d2, dept = _periode(request, jours=30)
    contexte = {'form': form, 'date_debut': d1, 'date_fin': d2, 'departement': dept,
                'titre': 'Contrôle qualité'}
    depart = time.perf_counter()
    compteur = {}
    try:
        parcelles = _charger_parcelles(d1, d2, dept, compteur)
        bornes = executer_fichier('bornes', d1, d2)
    except PostgisIndisponible as exc:
        contexte['erreur'] = str(exc)
        return render(request, 'terrain/qualite.html', contexte)

    machines = {a.id_machine for a in Agent.objects.all()}
    parcelles, constats_p = q.controler_parcelles(parcelles, machines)
    bornes, constats_b = q.controler_bornes(bornes)
    constats = constats_p + constats_b

    p_err = sum(1 for l in parcelles if l['nb_erreurs_ligne'])
    p_ale = sum(1 for l in parcelles if l['nb_alertes_ligne'] and not l['nb_erreurs_ligne'])
    b_err = sum(1 for l in bornes if l['nb_erreurs_ligne'])
    b_ale = sum(1 for l in bornes if l['nb_alertes_ligne'] and not l['nb_erreurs_ligne'])

    score_p = q.score_qualite(len(parcelles), p_err, p_ale)
    score_b = q.score_qualite(len(bornes), b_err, b_ale)
    total = len(parcelles) + len(bornes)
    score_global = round((score_p * len(parcelles) + score_b * len(bornes)) / total, 1) if total else 100.0

    gravite_filtre = request.GET.get('gravite', '')
    regle_filtre = request.GET.get('regle', '')
    detail = constats
    if gravite_filtre:
        detail = [c for c in detail if c['gravite'] == gravite_filtre]
    if regle_filtre:
        detail = [c for c in detail if c['regle'] == regle_filtre]

    if request.GET.get('export') == 'csv':
        return _export_csv(
            f'controle_qualite_{d1}_{d2}.csv',
            ['Objet', 'Référence', 'Gravité', 'Règle', 'Détail', 'Agent',
             'Département', 'Sous-préfecture', 'Village', 'Jour'],
            [[c['objet'], c['reference'], c['gravite'], c['regle'], c['detail'],
              c['agent'], c['departement'], c['sous_pref'], c['village'],
              c['jour'].isoformat() if c['jour'] else ''] for c in detail])

    contexte.update({
        'nb_parcelles': len(parcelles), 'nb_bornes': len(bornes),
        'parcelles_erreur': p_err, 'parcelles_alerte': p_ale,
        'bornes_erreur': b_err, 'bornes_alerte': b_ale,
        'parcellesconformes': len(parcelles) - p_err,
        'taux_conformite': round((len(parcelles) - p_err) / len(parcelles) * 100, 1) if parcelles else 0,
        'score_parcelles': score_p, 'score_bornes': score_b, 'score_global': score_global,
        'nb_constats_erreur': sum(1 for c in constats if c['gravite'] == q.ERREUR),
        'nb_constats_alerte': sum(1 for c in constats if c['gravite'] == q.ALERTE),
        'regles': q.synthese_regles(constats),
        'par_departement': q.synthese_par_departement(constats_p),
        'detail': detail[:400], 'nb_detail': len(detail),
        'gravite_filtre': gravite_filtre, 'regle_filtre': regle_filtre,
        'duree_ms': int((time.perf_counter() - depart) * 1000),
        'nb_exclues': compteur.get('exclues', 0),
    })

    if request.GET.get('enregistrer') == '1':
        ControleQualite.objects.create(
            libelle=f'Contrôle {d1:%d/%m/%Y} → {d2:%d/%m/%Y}' + (f' — {dept}' if dept else ''),
            date_debut=d1, date_fin=d2,
            nb_parcelles=len(parcelles), nb_bornes=len(bornes),
            nb_erreurs=contexte['nb_constats_erreur'],
            nb_alertes=contexte['nb_constats_alerte'],
            score=score_global, duree_ms=contexte['duree_ms'],
            resultat={'regles': [{k: v for k, v in r.items() if k != 'exemples'}
                                 for r in contexte['regles']]})
        messages.success(request, 'Contrôle enregistré dans l\'historique.')

    return render(request, 'terrain/qualite.html', contexte)


def historique_qualite(request):
    return render(request, 'terrain/qualite_historique.html', {
        'controles': ControleQualite.objects.all()[:50],
        'titre': 'Historique des contrôles',
    })


# ── Rendement des levés ──────────────────────────────────────────────────────
def rendement_leves(request):
    form, d1, d2, dept = _periode(request, jours=30)
    contexte = {'form': form, 'date_debut': d1, 'date_fin': d2, 'departement': dept,
                'titre': 'Rendement des levés'}
    compteur = {}
    try:
        parcelles = _charger_parcelles(d1, d2, dept, compteur)
    except PostgisIndisponible as exc:
        contexte['erreur'] = str(exc)
        return render(request, 'terrain/rendement.html', contexte)
    contexte['nb_exclues'] = compteur.get('exclues', 0)

    machines = {a.id_machine for a in Agent.objects.all()}
    parcelles, _ = q.controler_parcelles(parcelles, machines)

    agents = rdt.par_agent(parcelles, _agents_index(), settings.OBJECTIF_JOURNALIER_DEFAUT)
    jours = rdt.par_jour(parcelles)
    depts = rdt.par_departement(parcelles)

    if request.GET.get('export') == 'csv':
        return _export_csv(
            f'rendement_agents_{d1}_{d2}.csv',
            ['nom_agent', 'ID machine', 'Fonction', 'Département', 'Parcelles',
             'Surface (ha)', 'Jours actifs', 'Moyenne/jour', 'Objectif',
             'Atteinte (%)', 'Conformité (%)', 'Villages'],
            [[a['nom'], a['id_machine'], a['fonction'], a['departement'],
              a['nb_parcelles'], a['surface_ha'], a['nb_jours'], a['moyenne_jour'],
              a['objectif'], a['taux_objectif'], a['taux_conformite'], a['nb_villages']]
             for a in agents])

    classement = sorted(agents, key=lambda a: (-a['surface_ha'], -a['nb_parcelles'], a['nom']))[:15]
    surface_totale = round(sum(a['surface_ha'] for a in agents), 2)
    nb_jours_actifs = len(jours)
    contexte.update({
        'agents': agents, 'jours': jours, 'depts': depts,
        'nb_parcelles': len(parcelles),
        'surface_totale': surface_totale,
        'nb_agents': len(agents),
        'nb_agents_enregistres': sum(1 for a in agents if a['enregistre']),
        'nb_agents_inconnus': sum(1 for a in agents if not a['enregistre']),
        'nb_jours_actifs': nb_jours_actifs,
        'moyenne_jour': round(len(parcelles) / nb_jours_actifs, 1) if nb_jours_actifs else 0,
        'surface_moyenne': round(surface_totale / len(parcelles), 2) if parcelles else 0,
        'graph_jours': json.dumps([j['jour'].strftime('%d/%m') for j in jours]),
        'graph_parcelles': json.dumps([j['nb_parcelles'] for j in jours]),
        'graph_agents': json.dumps([j['nb_agents'] for j in jours]),
        'graph_surfaces_jours': [j['surface_ha'] for j in jours],
        'graph_surfaces_agents': [a['surface_ha'] for a in classement],
        'graph_noms': [a['nom'] for a in classement],
        'graph_prod': json.dumps([a['nb_parcelles'] for a in classement]),
    })
    return render(request, 'terrain/rendement.html', contexte)


def rendement_agent(request, pk):
    agent = get_object_or_404(Agent, pk=pk)
    form, d1, d2, _ = _periode(request, jours=60)
    contexte = {'agent': agent, 'form': form, 'date_debut': d1, 'date_fin': d2,
                'titre': f'Fiche agent — {agent.nom_complet}'}
    try:
        lignes, _ = filtrer_lignes([
            l for l in executer_fichier('parcelles', d1, d2)
            if str(l.get('id_machine') or '').strip() == agent.id_machine])
        lignes, constats = q.controler_parcelles(lignes, {agent.id_machine})
        contexte.update({
            'lignes': lignes[:300], 'nb_lignes': len(lignes),
            'jours': rdt.par_jour(lignes),
            'constats': q.synthese_regles(constats),
            'synthese': next(iter(rdt.par_agent(lignes, _agents_index(),
                                                agent.objectif_journalier)), None),
        })
    except PostgisIndisponible as exc:
        contexte['erreur'] = str(exc)
    contexte['oppositions'] = agent.oppositions.all()[:20]
    return render(request, 'terrain/agent_fiche.html', contexte)


# ── Agents ───────────────────────────────────────────────────────────────────
def agents(request):
    liste = Agent.objects.all()
    recherche = request.GET.get('q', '').strip()
    dept = request.GET.get('departement', '')
    fonction = request.GET.get('fonction', '')
    statut = request.GET.get('statut', '')
    if recherche:
        from django.db.models import Q
        liste = liste.filter(Q(nom__icontains=recherche) | Q(prenoms__icontains=recherche)
                             | Q(id_machine__icontains=recherche)
                             | Q(matricule__icontains=recherche))
    if dept:
        liste = liste.filter(departement=dept)
    if fonction:
        liste = liste.filter(fonction=fonction)
    if statut == 'actif':
        liste = liste.filter(actif=True)
    elif statut == 'inactif':
        liste = liste.filter(actif=False)

    if request.GET.get('export') == 'csv':
        return _export_csv('agents.csv',
                           ['ID machine', 'Nom', 'Prénoms', 'Fonction', 'Département',
                            'Sous-préfecture', 'Téléphone', 'Matricule', 'Objectif', 'Actif'],
                           [[a.id_machine, a.nom, a.prenoms, a.get_fonction_display(),
                             a.departement, a.sous_prefecture, a.telephone, a.matricule,
                             a.objectif_journalier, 'oui' if a.actif else 'non']
                            for a in liste])

    return render(request, 'terrain/agents.html', {
        'agents': liste, 'total': Agent.objects.count(),
        'nb_actifs': Agent.objects.filter(actif=True).count(),
        'par_dept': [{'nom': d, 'nb': Agent.objects.filter(departement__contains=d).count()}
                     for d in DEPARTEMENTS],
        'q': recherche, 'departement': dept, 'fonction': fonction, 'statut': statut,
        'fonctions': Agent.FONCTIONS, 'departements': DEPARTEMENTS,
        'titre': 'Agents',
    })


def agent_form(request, pk=None):
    agent = get_object_or_404(Agent, pk=pk) if pk else None
    if request.method == 'POST':
        form = AgentForm(request.POST, instance=agent)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'Agent « {obj.nom_complet} » enregistré.')
            return redirect('agents')
    else:
        form = AgentForm(instance=agent)
    return render(request, 'terrain/agent_form.html', {
        'form': form, 'agent': agent,
        'titre': 'Modifier un agent' if agent else 'Nouvel agent',
    })


def agent_supprimer(request, pk):
    agent = get_object_or_404(Agent, pk=pk)
    if request.method == 'POST':
        nom = agent.nom_complet
        agent.delete()
        messages.success(request, f'Agent « {nom} » supprimé.')
        return redirect('agents')
    return render(request, 'terrain/confirmer.html', {
        'objet': agent, 'retour': 'agents',
        'question': f'Supprimer définitivement l\'agent « {agent.nom_complet} » ?',
        'titre': 'Supprimer un agent',
    })


def agents_importer(request):
    """Import CSV/Excel + reprise de l'ancienne table AGENT_MAP."""
    if request.method == 'POST' and request.POST.get('action') == 'reprise':
        from analyse.processing import AGENT_MAP
        cree = maj = 0
        for (machine, dept), nom in AGENT_MAP.items():
            parties = nom.split(' ', 1)
            obj, created = Agent.objects.update_or_create(
                id_machine=machine, departement=dept,
                defaults={'nom': parties[0],
                          'prenoms': parties[1] if len(parties) > 1 else ''})
            cree += int(created)
            maj += int(not created)
        messages.success(request, f'Reprise terminée : {cree} agent(s) créé(s), {maj} mis à jour.')
        return redirect('agents')

    resume = None
    if request.method == 'POST':
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                df = lire_tableau(form.cleaned_data['fichier'])
            except Exception as exc:
                messages.error(request, f'Lecture impossible : {exc}')
                return render(request, 'terrain/agents_import.html', {'form': form})
            if form.cleaned_data['remplacer']:
                Agent.objects.all().delete()
            idx = {normaliser(c): c for c in df.columns}
            cree = maj = ignore = 0
            with transaction.atomic():
                for _, ligne in df.iterrows():
                    d = ligne.to_dict()
                    machine = colonne(d, ['id_machine', 'idmachine', 'machine', 'agent', 'code_agent'], idx)
                    if not machine:
                        ignore += 1
                        continue
                    dept = colonne(d, ['departement', 'depart', 'dept'], idx).upper() or 'SOUBRE'
                    nom_complet = colonne(d, ['nom_complet', 'nomcomplet', 'nom_prenoms'], idx)
                    nom = colonne(d, ['nom'], idx) or nom_complet.split(' ')[0]
                    prenoms = colonne(d, ['prenoms', 'prenom'], idx) or \
                        (' '.join(nom_complet.split(' ')[1:]) if nom_complet else '')
                    _, created = Agent.objects.update_or_create(
                        id_machine=machine.strip().lower(), departement=dept,
                        defaults={
                            'nom': nom or machine,
                            'prenoms': prenoms,
                            'sous_prefecture': colonne(d, ['sous_prefecture', 'sous_pref', 'souspref'], idx),
                            'telephone': colonne(d, ['telephone', 'tel', 'contact'], idx),
                            'matricule': colonne(d, ['matricule'], idx),
                        })
                    cree += int(created)
                    maj += int(not created)
            resume = {'cree': cree, 'maj': maj, 'ignore': ignore, 'lignes': len(df)}
            messages.success(request, f'{cree} agent(s) créé(s), {maj} mis à jour, {ignore} ignoré(s).')
    else:
        form = ImportForm()
    return render(request, 'terrain/agents_import.html', {
        'form': form, 'resume': resume, 'titre': 'Importer des agents'})


# ── Opposition / règlement de litige ─────────────────────────────────────────
def oppositions_liste(request):
    liste = Opposition.objects.select_related('agent')
    dept = request.GET.get('departement', '')
    statut = request.GET.get('statut', '')
    recherche = request.GET.get('q', '').strip()
    if dept:
        liste = liste.filter(departement=dept)
    if statut:
        liste = liste.filter(statut=statut)
    if recherche:
        from django.db.models import Q
        liste = liste.filter(Q(code_parcelle__icontains=recherche)
                             | Q(opposant__icontains=recherche)
                             | Q(demandeur__icontains=recherche)
                             | Q(village__icontains=recherche))

    if request.GET.get('export') == 'csv':
        return _export_csv('oppositions.csv',
                           ['Code parcelle', 'Département', 'Sous-préfecture', 'Village',
                            'Demandeur', 'Opposant', 'Type', 'Date opposition',
                            'Date règlement', 'Statut', 'Délai (j)', 'Agent'],
                           [[o.code_parcelle, o.departement, o.sous_prefecture, o.village,
                             o.demandeur, o.opposant, o.get_type_litige_display(),
                             o.date_opposition, o.date_reglement or '',
                             o.get_statut_display(), o.delai_jours or '',
                             o.agent.nom_complet if o.agent else ''] for o in liste])

    return render(request, 'terrain/oppositions.html', {
        'oppositions': liste[:500], 'total': liste.count(),
        'synthese': opp.synthese(liste),
        'statuts': Opposition.STATUTS, 'departements': DEPARTEMENTS,
        'departement': dept, 'statut': statut, 'q': recherche,
        'titre': 'Oppositions et litiges',
    })


def opposition_form(request, pk=None):
    objet = get_object_or_404(Opposition, pk=pk) if pk else None
    if request.method == 'POST':
        form = OppositionForm(request.POST, instance=objet)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'Opposition {obj.code_parcelle or ""} enregistrée.')
            return redirect('oppositions')
    else:
        form = OppositionForm(instance=objet)
    return render(request, 'terrain/opposition_form.html', {
        'form': form, 'objet': objet,
        'titre': 'Modifier une opposition' if objet else 'Nouvelle opposition',
    })


def opposition_supprimer(request, pk):
    objet = get_object_or_404(Opposition, pk=pk)
    if request.method == 'POST':
        objet.delete()
        messages.success(request, 'Opposition supprimée.')
        return redirect('oppositions')
    return render(request, 'terrain/confirmer.html', {
        'objet': objet, 'retour': 'oppositions',
        'question': f'Supprimer l\'opposition sur la parcelle {objet.code_parcelle} ?',
        'titre': 'Supprimer une opposition',
    })


def oppositions_rendement(request):
    form, d1, d2, dept = _periode(request, jours=180)
    liste = Opposition.objects.filter(date_opposition__gte=d1, date_opposition__lte=d2)
    if dept:
        liste = liste.filter(departement=dept)

    lignes_pg, message_pg = opp.depuis_postgis(d1, d2)
    serie = opp.serie_mensuelle(liste)
    return render(request, 'terrain/oppositions_rendement.html', {
        'form': form, 'date_debut': d1, 'date_fin': d2, 'departement': dept,
        'synthese': opp.synthese(liste),
        'par_statut': opp.par_statut(liste),
        'par_type': opp.par_type(liste),
        'par_agent': opp.par_agent(liste),
        'par_departement': opp.par_departement(liste),
        'serie': serie,
        'graph_mois': json.dumps([s['mois'] for s in serie]),
        'graph_ouvertes': json.dumps([s['ouvertes'] for s in serie]),
        'graph_reglees': json.dumps([s['reglees'] for s in serie]),
        'lignes_pg': lignes_pg[:100], 'nb_pg': len(lignes_pg), 'message_pg': message_pg,
        'titre': 'Rendement opposition / litige',
    })


def oppositions_importer(request):
    resume = None
    if request.method == 'POST':
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                df = lire_tableau(form.cleaned_data['fichier'])
            except Exception as exc:
                messages.error(request, f'Lecture impossible : {exc}')
                return render(request, 'terrain/oppositions_import.html', {'form': form})
            if form.cleaned_data['remplacer']:
                Opposition.objects.filter(source='import').delete()
            idx = {normaliser(c): c for c in df.columns}
            cree = ignore = 0
            statuts_valides = {c for c, _ in Opposition.STATUTS}
            with transaction.atomic():
                for _, ligne in df.iterrows():
                    d = ligne.to_dict()
                    d_opp = en_date(colonne(d, ['date_opposition', 'dateopposition', 'date'], idx))
                    if not d_opp:
                        ignore += 1
                        continue
                    statut = colonne(d, ['statut', 'etat'], idx).lower()
                    Opposition.objects.create(
                        code_parcelle=colonne(d, ['code_parcelle', 'codeparcelle', 'parcelle'], idx),
                        num_demande=colonne(d, ['num_demande', 'numdemande', 'demande'], idx),
                        departement=colonne(d, ['departement', 'depart', 'dept'], idx).upper() or 'SOUBRE',
                        sous_prefecture=colonne(d, ['sous_prefecture', 'sous_pref'], idx),
                        village=colonne(d, ['village', 'name_villa'], idx),
                        demandeur=colonne(d, ['demandeur', 'titulaire'], idx),
                        opposant=colonne(d, ['opposant'], idx),
                        motif=colonne(d, ['motif', 'objet'], idx),
                        date_opposition=d_opp,
                        date_reglement=en_date(colonne(d, ['date_reglement', 'datereglement'], idx)),
                        statut=statut if statut in statuts_valides else 'enregistree',
                        issue=colonne(d, ['issue', 'decision', 'resultat'], idx),
                        source='import')
                    cree += 1
            resume = {'cree': cree, 'ignore': ignore, 'lignes': len(df)}
            messages.success(request, f'{cree} opposition(s) importée(s), {ignore} ignorée(s).')
    else:
        form = ImportForm()
    return render(request, 'terrain/oppositions_import.html', {
        'form': form, 'resume': resume, 'titre': 'Importer des oppositions'})


# ── Parcelles en publicité ───────────────────────────────────────────────────
def publicite(request):
    toutes = ParcellePublicite.objects.all()
    liste = filtrer_publiees(toutes)

    dept = request.GET.get('departement', '')
    recherche = request.GET.get('q', '').strip()
    if dept:
        liste = liste.filter(departement=dept)
    if recherche:
        liste = liste.filter(Q(code_parcelle__icontains=recherche)
                             | Q(titulaire__icontains=recherche)
                             | Q(village__icontains=recherche))

    if request.GET.get('export') == 'csv':
        return _export_csv('parcelles_publiees.csv',
                           ['Code parcelle', 'N° demande', 'Département',
                            'Sous-préfecture', 'Village', 'Titulaire', 'Prénoms',
                            'Téléphone', 'CNI', 'Type', 'Surface (ha)',
                            'Commissaire enquêteur'],
                           [[p.code_parcelle, p.num_demande, p.departement,
                             p.sous_prefecture, p.village, p.titulaire, p.prenoms,
                             p.telephone, p.cni, p.type_certificat,
                             p.surface_ha or '', p.commissaire] for p in liste])

    base = filtrer_publiees(toutes)
    surface = liste.aggregate(total=Sum('surface_ha'))['total'] or 0
    return render(request, 'terrain/publicite.html', {
        'parcelles': liste[:500], 'total': liste.count(),
        'surface_totale': round(surface, 1),
        'nb_masquees': toutes.count() - base.count(),
        'nb_sans_geometrie': liste.filter(geojson='').count(),
        'par_dept': [{'nom': d, 'nb': base.filter(departement=d).count()}
                     for d in DEPARTEMENTS],
        'departements': DEPARTEMENTS, 'departement': dept, 'q': recherche,
        'villages': sorted(v for v in base.order_by('village')
                           .values_list('village', flat=True).distinct() if v),
        'titre': 'Parcelles en publicité',
    })


def publicite_importer(request):
    resume = None
    if request.method == 'POST':
        form = ImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                df = lire_tableau(form.cleaned_data['fichier'])
            except Exception as exc:
                messages.error(request, f'Lecture impossible : {exc}')
                return render(request, 'terrain/publicite_import.html', {'form': form})
            if form.cleaned_data['remplacer']:
                ParcellePublicite.objects.all().delete()
            idx = {normaliser(c): c for c in df.columns}
            nom_fichier = form.cleaned_data['fichier'].name
            cree = ignore = 0
            with transaction.atomic():
                for _, ligne in df.iterrows():
                    d = ligne.to_dict()
                    code = colonne(d, ['code_parcelle', 'codeparcelle', 'parcelle',
                                       'num_parcelle', 'numparcelleof'], idx)
                    if not code:
                        ignore += 1
                        continue
                    ParcellePublicite.objects.create(
                        code_parcelle=code,
                        num_demande=colonne(d, ['num_demande', 'applicantnumber', 'demande'], idx),
                        departement=colonne(d, ['departement', 'depart', 'dept'], idx).upper(),
                        sous_prefecture=colonne(d, ['sous_prefecture', 'sous_pref'], idx),
                        village=colonne(d, ['village', 'name_villa'], idx),
                        titulaire=colonne(d, ['titulaire', 'nom', 'demandeur'], idx),
                        surface_ha=en_float(colonne(d, ['surface_ha', 'surface', 'superficie'], idx)),
                        date_ouverture=en_date(colonne(d, ['date_ouverture', 'openingdate', 'ouverture'], idx)),
                        date_cloture=en_date(colonne(d, ['date_cloture', 'closingdate', 'cloture'], idx)),
                        statut=colonne(d, ['statut', 'etape'], idx),
                        fichier_source=nom_fichier)
                    cree += 1
            resume = {'cree': cree, 'ignore': ignore, 'lignes': len(df)}
            messages.success(request, f'{cree} parcelle(s) importée(s), {ignore} ignorée(s).')
    else:
        form = ImportForm()
    return render(request, 'terrain/publicite_import.html', {
        'form': form, 'resume': resume, 'titre': 'Importer les parcelles en publicité'})


# ── Vue globale des activités ────────────────────────────────────────────────
def global_activite(request):
    form, d1, d2, dept = _periode(request, jours=30)
    session = AnalyseSession.objects.filter(statut='termine').first()

    contexte = {'form': form, 'date_debut': d1, 'date_fin': d2, 'departement': dept,
                'session': session, 'titre': 'Activité globale'}

    # Volet terrain (PostGIS)
    try:
        parcelles = _charger_parcelles(d1, d2, dept)
        machines = {a.id_machine for a in Agent.objects.all()}
        parcelles, _ = q.controler_parcelles(parcelles, machines)
        agents_actifs = {str(l.get('id_machine') or '').strip() for l in parcelles if l.get('id_machine')}
        contexte.update({
            'terrain_ok': True,
            'nb_leves': len(parcelles),
            'surface_levee': round(sum(float(l.get('surface_ha') or 0) for l in parcelles), 2),
            'nb_agents_actifs': len(agents_actifs),
            'nb_levesconformes': sum(1 for l in parcelles if l['conforme']),
            'depts_terrain': rdt.par_departement(parcelles),
            'jours': rdt.par_jour(parcelles),
            'top_agents': sorted(
                rdt.par_agent(parcelles, _agents_index(), settings.OBJECTIF_JOURNALIER_DEFAUT),
                key=lambda a: (-a['surface_ha'], -a['nb_parcelles'], a['nom']))[:8],
        })
        contexte['taux_conformite'] = round(
            contexte['nb_levesconformes'] / len(parcelles) * 100, 1) if parcelles else 0
        contexte['graph_jours'] = json.dumps(
            [j['jour'].strftime('%d/%m') for j in contexte['jours']])
        contexte['graph_surfaces'] = [j['surface_ha'] for j in contexte['jours']]
        contexte['graph_leves'] = json.dumps(
            [j['nb_parcelles'] for j in contexte['jours']])
    except PostgisIndisponible as exc:
        contexte.update({'terrain_ok': False, 'erreur_terrain': str(exc)})

    # Volet instruction (CSV Digifor, via la section Analyse)
    if session:
        stats = {s.departement: s for s in session.stats.all()}
        nawa = stats.get('NAWA')
        contexte.update({
            'stats_nawa': nawa,
            'stats_depts': [stats[d] for d in DEPTS_ANALYSE if d in stats],
        })

    # Volet opposition
    liste_opp = Opposition.objects.filter(date_opposition__gte=d1, date_opposition__lte=d2)
    if dept:
        liste_opp = liste_opp.filter(departement=dept)
    contexte['opp'] = opp.synthese(liste_opp)

    # Volet publicité
    pub = filtrer_publiees(ParcellePublicite.objects.all())
    if dept:
        pub = pub.filter(departement=dept)
    contexte['nb_publicite'] = pub.count()
    contexte['nb_exclusions'] = ParcelleExclue.objects.filter(active=True).count()

    # Chaîne de traitement (entonnoir)
    if session:
        nawa = contexte.get('stats_nawa')
        contexte['entonnoir'] = [
            {'etape': 'Parcelles levées', 'valeur': contexte.get('nb_leves', 0), 'couleur': '#1F4E79'},
            {'etape': 'Demandes créées', 'valeur': nawa.nb_demandes if nawa else 0, 'couleur': '#2E75B6'},
            {'etape': 'PVCL terminés', 'valeur': nawa.nb_pvcl_termine if nawa else 0, 'couleur': '#41A0C4'},
            {'etape': 'RDC terminés', 'valeur': nawa.nb_rdc_termine if nawa else 0, 'couleur': '#70AD47'},
            {'etape': 'En publicité', 'valeur': contexte['nb_publicite'], 'couleur': '#FFC000'},
        ]
        maxi = max((e['valeur'] for e in contexte['entonnoir']), default=0) or 1
        for e in contexte['entonnoir']:
            e['largeur'] = round(e['valeur'] / maxi * 100, 1)

    return render(request, 'terrain/global.html', contexte)


# ── API graphiques ───────────────────────────────────────────────────────────
def api_rendement(request):
    _, d1, d2, dept = _periode(request, jours=30)
    try:
        lignes = _charger_parcelles(d1, d2, dept)
    except PostgisIndisponible as exc:
        return JsonResponse({'erreur': str(exc)}, status=503)
    jours = rdt.par_jour(lignes)
    return JsonResponse({
        'jours': [j['jour'].isoformat() for j in jours],
        'parcelles': [j['nb_parcelles'] for j in jours],
        'surface': [j['surface_ha'] for j in jours],
        'agents': [j['nb_agents'] for j in jours],
    })


# ── Carte Leaflet ────────────────────────────────────────────────────────────
def _date_iso(v):
    """Lit une date AAAA-MM-JJ envoyée par un champ <input type="date">."""
    from datetime import datetime as _dt
    try:
        return _dt.strptime(str(v).strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _bbox_depuis_requete(request):
    brut = request.GET.get('bbox', '')
    try:
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in brut.split(','))
        return lon_min, lat_min, lon_max, lat_max
    except (ValueError, AttributeError):
        return None


def carte(request):
    """Vue cartographique : consultation, édition et exclusion des parcelles."""
    pub = ParcellePublicite.objects.all()
    exclusions = ParcelleExclue.objects.filter(active=True)
    visibles = filtrer_publiees(pub)

    etendue = pub.exclude(lat__isnull=True).aggregate(
        lat_min=Min('lat_min'), lat_max=Max('lat_max'),
        lon_min=Min('lon_min'), lon_max=Max('lon_max'))
    # Emprise par défaut : la région NAWA, si aucune parcelle n'est géoréférencée.
    etendue_js = {
        'latMin': etendue['lat_min'] if etendue['lat_min'] is not None else 5.2,
        'latMax': etendue['lat_max'] if etendue['lat_max'] is not None else 6.7,
        'lonMin': etendue['lon_min'] if etendue['lon_min'] is not None else -7.3,
        'lonMax': etendue['lon_max'] if etendue['lon_max'] is not None else -6.2,
    }

    # order_by() explicite : sans lui, l'ordre par défaut du modèle
    # (departement, code_parcelle) entre dans le SELECT DISTINCT et renvoie
    # une valeur par parcelle — d'où les listes déroulantes remplies de doublons.
    departements = sorted(
        d for d in pub.order_by('departement')
                      .values_list('departement', flat=True).distinct() if d)
    villages = sorted(
        v for v in visibles.order_by('village')
                           .values_list('village', flat=True).distinct() if v)
    sous_prefectures = sorted(
        s for s in visibles.order_by('sous_prefecture')
                           .values_list('sous_prefecture', flat=True).distinct() if s)

    couches, message_couches = couches_disponibles()
    from .models import CoucheServitude
    couches += [dict(table='projet-'+c.cle,libelle=c.libelle,couleur=('#dc3545' if c.role=='deduction' else '#8e44ad'),disponible=True) for c in CoucheServitude.objects.only('cle','libelle','role')]

    return render(request, 'terrain/carte.html', {
        'titre': 'Carte des parcelles',
        'nb_total': pub.count(),
        'nb_visibles': visibles.count(),
        'nb_exclues': exclusions.count(),
        'nb_masquees': pub.count() - visibles.count(),
        'nb_sans_geometrie': pub.filter(geojson='').count(),
        'etendue': etendue,
        # Sérialisé en JSON : injecter un flottant directement dans du JavaScript
        # produirait « 5,29 » avec la locale française et casserait le script.
        'etendue_json': json.dumps(etendue_js),
        'departements': departements,
        'villages': villages,
        'sous_prefectures': sous_prefectures,
        'region': REGION,
        'motifs': ParcelleExclue.MOTIFS,
        'couches': couches,
        'couches_json': json.dumps([
            {'table': c['table'], 'libelle': c['libelle'], 'couleur': c['couleur']}
            for c in couches if c.get('disponible')]),
        'message_couches': message_couches,
    })


def api_parcelles(request):
    """GeoJSON des parcelles, filtré par emprise, département, village ou code."""
    couche = request.GET.get('couche', 'publiee')
    limite = min(int(request.GET.get('limite', 1500) or 1500), 5000)

    if couche == 'exclue':
        qs = ParcelleExclue.objects.filter(active=True).exclude(geojson='')
        entites = []
        for p in qs[:limite]:
            entites.append({
                'type': 'Feature',
                'geometry': json.loads(p.geojson),
                'properties': {'pk': p.pk, 'couche': 'exclue',
                               'code_parcelle': p.code_parcelle,
                               'motif': p.get_motif_display(),
                               'village': p.village, 'departement': p.departement,
                               'id_machine': p.id_machine,
                               'commentaire': p.commentaire},
            })
        return JsonResponse({'type': 'FeatureCollection', 'features': entites,
                             'nb': len(entites), 'tronque': qs.count() > limite})

    qs = ParcellePublicite.objects.exclude(geojson='')
    if not request.GET.get('inclure_exclues'):
        qs = filtrer_publiees(qs)

    dept       = request.GET.get('departement', '')
    village    = request.GET.get('village', '')
    sous_pref  = request.GET.get('sous_prefecture', '')
    recherche  = request.GET.get('q', '').strip()
    if dept:
        qs = qs.filter(departement=dept)
    if village:
        # iexact : le village vient parfois d'un CSV (« Pétigoa 2 ») et parfois
        # du shapefile (« PETIGOA 2 »).
        qs = qs.filter(Q(village__iexact=village) | Q(village__icontains=village))
    if sous_pref:
        qs = qs.filter(Q(sous_prefecture__iexact=sous_pref)
                       | Q(sous_prefecture__icontains=sous_pref))

    # Recherche multi-champs : code unique, code parcelle, identité,
    # numéro de demande, CNI, téléphone, commissaire enquêteur.
    if recherche:
        qs = qs.filter(Q(code_parcelle__icontains=recherche)
                       | Q(code_norm__icontains=normaliser_code(recherche))
                       | Q(code_unique__icontains=recherche)
                       | Q(num_demande__icontains=recherche)
                       | Q(titulaire__icontains=recherche)
                       | Q(prenoms__icontains=recherche)
                       | Q(cni__icontains=recherche)
                       | Q(telephone__icontains=recherche)
                       | Q(commissaire__icontains=recherche)
                       | Q(village__icontains=recherche))

    # Filtre par date d'ouverture de publicité
    d1 = _date_iso(request.GET.get('d1', ''))
    d2 = _date_iso(request.GET.get('d2', ''))
    if d1:
        qs = qs.filter(date_ouverture__gte=d1)
    if d2:
        qs = qs.filter(date_ouverture__lte=d2)

    bbox = _bbox_depuis_requete(request)
    if bbox:
        lon_min, lat_min, lon_max, lat_max = bbox
        qs = qs.filter(lon_max__gte=lon_min, lon_min__lte=lon_max,
                       lat_max__gte=lat_min, lat_min__lte=lat_max)

    total = qs.count()
    entites = []
    champs = ('pk', 'geojson', 'code_parcelle', 'num_demande', 'departement',
              'sous_prefecture', 'village', 'titulaire', 'prenoms', 'telephone',
              'cni', 'type_certificat', 'commissaire', 'surface_ha', 'statut',
              'observation', 'id_machine')
    from .servitudes import CalculServitudes
    calculateur=CalculServitudes()
    for p in qs.only(*[c for c in champs if c != 'pk'])[:limite]:
        try:
            geometrie = json.loads(p.geojson)
        except (ValueError, TypeError):
            continue
        entites.append({
            'type': 'Feature',
            'geometry': geometrie,
            'properties': {
                'pk': p.pk, 'couche': 'publiee',
                'code_parcelle': p.code_parcelle, 'num_demande': p.num_demande,
                'departement': p.departement, 'sous_prefecture': p.sous_prefecture,
                'village': p.village, 'titulaire': p.titulaire, 'prenoms': p.prenoms,
                'telephone': p.telephone, 'cni': p.cni,
                'type_certificat': p.type_certificat, 'commissaire': p.commissaire,
                'surface_ha': p.surface_ha, 'statut': p.statut,
                **calculateur.calculer(geometrie,surface_origine=p.surface_ha),
                'observation': p.observation, 'id_machine': p.id_machine,
            },
        })
    return JsonResponse({'type': 'FeatureCollection', 'features': entites,
                         'nb': len(entites), 'total': total,
                         'tronque': total > limite})


@require_POST
def api_parcelle_maj(request, pk):
    """Enregistre les attributs et/ou la géométrie modifiés depuis la carte."""
    parcelle = get_object_or_404(ParcellePublicite, pk=pk)
    try:
        charge = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'ok': False, 'erreur': 'Requête illisible.'}, status=400)

    modifiables = {
        'code_parcelle', 'num_demande', 'departement', 'sous_prefecture', 'village',
        'titulaire', 'prenoms', 'telephone', 'cni', 'type_certificat',
        'commissaire', 'statut', 'observation',
    }
    attributs = charge.get('attributs') or {}
    for cle, valeur in attributs.items():
        if cle in modifiables:
            setattr(parcelle, cle, str(valeur or '')[:200])
    if 'surface_ha' in attributs:
        try:
            parcelle.surface_ha = float(attributs['surface_ha'])
        except (TypeError, ValueError):
            parcelle.surface_ha = None

    geometrie = charge.get('geometrie')
    if geometrie:
        if geometrie.get('type') not in ('Polygon', 'MultiPolygon'):
            return JsonResponse(
                {'ok': False, 'erreur': "Seuls les polygones sont acceptés."}, status=400)
        parcelle.geojson = json.dumps(geometrie, separators=(',', ':'))
        env = enveloppe(geometrie)
        if env:
            (parcelle.lon_min, parcelle.lat_min, parcelle.lon_max,
             parcelle.lat_max, parcelle.lon, parcelle.lat) = env

    parcelle.save()
    return JsonResponse({
        'ok': True,
        'message': f'Parcelle {parcelle.code_parcelle} enregistrée.',
        'geometrie_modifiee': bool(geometrie),
    })


@require_POST
def api_parcelle_exclure(request, pk):
    """Bascule l'exclusion d'une parcelle depuis la carte."""
    parcelle = get_object_or_404(ParcellePublicite, pk=pk)
    try:
        charge = json.loads(request.body or '{}')
    except ValueError:
        charge = {}

    norme = normaliser_code(parcelle.code_parcelle)
    if parcelle.id_parcelle:
        existante = ParcelleExclue.objects.filter(id_parcelle=parcelle.id_parcelle).first()
    else:
        existante = ParcelleExclue.objects.filter(
            code_norm=norme, village=parcelle.village).first()

    if charge.get('exclure', True):
        motifs_valides = {c for c, _ in ParcelleExclue.MOTIFS}
        motif = charge.get('motif', 'autre')
        motif = motif if motif in motifs_valides else 'autre'
        if existante:
            existante.active = True
            existante.motif = motif
            existante.commentaire = charge.get('commentaire', existante.commentaire)
            existante.save()
        else:
            ParcelleExclue.objects.create(
                id_parcelle=parcelle.id_parcelle,
                code_parcelle=parcelle.code_parcelle, motif=motif,
                departement=parcelle.departement, village=parcelle.village,
                id_machine=parcelle.id_machine,
                commentaire=charge.get('commentaire', ''),
                fichier_source='carte')
        return JsonResponse({'ok': True, 'exclue': True,
                             'message': f'{parcelle.code_parcelle} masquée de l\'affichage.'})

    if existante:
        existante.active = False
        existante.save()
    return JsonResponse({'ok': True, 'exclue': False,
                         'message': f'{parcelle.code_parcelle} de nouveau visible.'})


# ── Liste des exclusions ─────────────────────────────────────────────────────
def exclusions(request):
    liste = ParcelleExclue.objects.all()
    recherche = request.GET.get('q', '').strip()
    motif = request.GET.get('motif', '')
    if recherche:
        liste = liste.filter(code_parcelle__icontains=recherche)
    if motif:
        liste = liste.filter(motif=motif)

    if request.GET.get('export') == 'csv':
        return _export_csv('parcelles_exclues.csv',
                           ['Code parcelle', 'Motif', 'Département', 'Village',
                            'Machine', 'Date du levé', 'Active', 'Commentaire'],
                           [[e.code_parcelle, e.get_motif_display(), e.departement,
                             e.village, e.id_machine, e.date_creation_levee,
                             'oui' if e.active else 'non', e.commentaire] for e in liste])

    return render(request, 'terrain/exclusions.html', {
        'exclusions': liste[:1000],
        'total': ParcelleExclue.objects.count(),
        'nb_actives': ParcelleExclue.objects.filter(active=True).count(),
        'nb_masquees': (ParcellePublicite.objects.count()
                        - filtrer_publiees(ParcellePublicite.objects.all()).count()),
        'motifs': ParcelleExclue.MOTIFS, 'q': recherche, 'motif': motif,
        'titre': 'Parcelles à supprimer',
    })


def exclusion_form(request, pk=None):
    objet = get_object_or_404(ParcelleExclue, pk=pk) if pk else None
    if request.method == 'POST':
        form = ExclusionForm(request.POST, instance=objet)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'Exclusion « {obj.code_parcelle} » enregistrée.')
            return redirect('exclusions')
    else:
        form = ExclusionForm(instance=objet)
    return render(request, 'terrain/exclusion_form.html', {
        'form': form, 'objet': objet,
        'titre': 'Modifier une exclusion' if objet else 'Exclure une parcelle',
    })


def exclusion_basculer(request, pk):
    objet = get_object_or_404(ParcelleExclue, pk=pk)
    objet.active = not objet.active
    objet.save()
    messages.success(
        request,
        f'{objet.code_parcelle} : exclusion {"activée" if objet.active else "levée"}.')
    return redirect('exclusions')


def exclusion_supprimer(request, pk):
    objet = get_object_or_404(ParcelleExclue, pk=pk)
    if request.method == 'POST':
        objet.delete()
        messages.success(request, 'Exclusion retirée de la liste.')
        return redirect('exclusions')
    return render(request, 'terrain/confirmer.html', {
        'objet': objet, 'retour': 'exclusions',
        'question': f'Retirer {objet.code_parcelle} de la liste des exclusions ?',
        'titre': 'Retirer une exclusion',
    })


# ── Import des couches shapefile ─────────────────────────────────────────────
def importer_couche(request):
    resume = None

    if request.method == 'POST' and request.POST.get('action') == 'dedoublonner':
        supprimes = nettoyer_doublons()
        if supprimes:
            messages.success(
                request,
                f"{supprimes} ligne(s) en double supprimée(s) : il ne reste "
                f"qu'un enregistrement par code parcelle.")
        else:
            messages.info(request, 'Aucun doublon à supprimer.')
        return redirect('importer_couche')

    if request.method == 'POST':
        form = ImportShapefileForm(request.POST, request.FILES)
        if form.is_valid():
            archive = form.cleaned_data['archive']
            remplacer = form.cleaned_data['remplacer']
            try:
                if form.cleaned_data['couche'] == 'publicite':
                    resume = importer_publicite(archive, archive.name, remplacer)
                    messages.success(
                        request,
                        f"{resume['importees']} parcelle(s) publiée(s) importée(s) "
                        f"depuis une couche en {resume['crs']}.")
                    if resume.get('doublons'):
                        messages.warning(
                            request,
                            f"{resume['doublons']} code(s) parcelle existaient déjà : "
                            f"la couche a été ajoutée aux données en place, pas "
                            f"substituée. Utilisez « Nettoyer les doublons » "
                            f"ci-dessous, ou relancez l'import en cochant "
                            f"« Remplacer la couche existante ».")
                else:
                    resume = importer_exclusions(
                        archive, archive.name,
                        motif=form.cleaned_data.get('motif') or 'geometrie',
                        remplacer=remplacer)
                    messages.success(
                        request,
                        f"{resume['importees']} parcelle(s) ajoutée(s) à la liste "
                        f"d'exclusion — elles sont désormais masquées partout.")
            except ShapefileIllisible as exc:
                messages.error(request, str(exc))
            except Exception as exc:
                messages.error(request, f'Import interrompu : {exc}')
    else:
        form = ImportShapefileForm()
    return render(request, 'terrain/couche_import.html', {
        'form': form, 'resume': resume,
        'nb_publiees': ParcellePublicite.objects.count(),
        'nb_exclues': ParcelleExclue.objects.count(),
        'doublons': compter_doublons(),
        'titre': 'Importer une couche shapefile',
    })


# ── Couches PostGIS sur la carte ─────────────────────────────────────────────
def couches_configurees():
    """Couches déclarées dans .env : table, colonne géométrique, libellé, couleur."""
    couches = []
    for brut in getattr(settings, 'PG_COUCHES_CARTE', []):
        morceaux = [m.strip() for m in brut.split(':')]
        if len(morceaux) < 2 or not morceaux[0]:
            continue
        table, colonne = morceaux[0], morceaux[1]
        couches.append({
            'table': table,
            'colonne': colonne,
            'libelle': morceaux[2] if len(morceaux) > 2 and morceaux[2] else table,
            'couleur': morceaux[3] if len(morceaux) > 3 and morceaux[3] else '#2E75B6',
        })
    return couches


def couches_disponibles():
    """Croise les couches déclarées avec celles réellement présentes en base.

    Renvoie (liste, message). Une couche déclarée mais absente est signalée
    plutôt que silencieusement ignorée : c'est le cas typique d'un nom de table
    à corriger dans le .env.
    """
    declarees = couches_configurees()
    try:
        presentes = {c['table_name']: c for c in lister_couches_geo()}
    except PostgisIndisponible as exc:
        for c in declarees:
            c['disponible'] = False
            c['motif'] = 'base terrain injoignable'
        return declarees, str(exc)

    for c in declarees:
        info = presentes.get(c['table'])
        c['disponible'] = info is not None
        if info:
            c['type_geom'] = info.get('type_geom', '')
            # Le .env peut se tromper de colonne : le catalogue fait foi.
            c['colonne'] = info.get('colonne_geom') or c['colonne']
        else:
            c['motif'] = f"table « {c['table']} » absente de la base"

    # Couches présentes en base mais non déclarées, proposées en complément
    declarees_noms = {c['table'] for c in declarees}
    for nom, info in presentes.items():
        if nom not in declarees_noms:
            declarees.append({
                'table': nom, 'colonne': info['colonne_geom'],
                'libelle': nom, 'couleur': '#6C757D',
                'disponible': True, 'type_geom': info.get('type_geom', ''),
                'suggeree': True,
            })
    return declarees, ''


def api_couche(request, table):
    """GeoJSON d'une couche de la base terrain, reprojeté en WGS84."""
    autorisees = {c['table']: c for c in couches_disponibles()[0] if c.get('disponible')}
    couche = autorisees.get(table)
    if couche is None:
        return JsonResponse(
            {'erreur': f"Couche « {table} » indisponible sur la base terrain."},
            status=404)

    limite = min(int(request.GET.get('limite', 1200) or 1200), 4000)
    bbox_geo = _bbox_depuis_requete(request)
    bbox_projete = None
    projeter_inverse = transformateur(CRS_TERRAIN)
    if bbox_geo:
        direct = transformateur_direct(CRS_TERRAIN)
        lon_min, lat_min, lon_max, lat_max = bbox_geo
        coins = [direct(lon_min, lat_min), direct(lon_max, lat_min),
                 direct(lon_min, lat_max), direct(lon_max, lat_max)]
        xs = [c[0] for c in coins]
        ys = [c[1] for c in coins]
        bbox_projete = (min(xs), min(ys), max(xs), max(ys))

    colonnes = [c.strip() for c in request.GET.get('champs', '').split(',') if c.strip()]
    try:
        lignes = executer_lecture_couche(couche, colonnes, bbox_projete, limite)
        total = compter_couche(couche['table'], couche['colonne'], bbox_projete)
    except PostgisIndisponible as exc:
        return JsonResponse({'erreur': str(exc)}, status=503)

    entites = []
    for l in lignes:
        brut = l.pop('geojson', None)
        if not brut:
            continue
        try:
            geometrie = reprojeter_geojson(json.loads(brut), projeter_inverse)
        except (ValueError, TypeError, KeyError):
            continue
        entites.append({
            'type': 'Feature',
            'geometry': geometrie,
            'properties': {k: serialiser(v) for k, v in l.items()},
        })

    return JsonResponse({
        'type': 'FeatureCollection', 'features': entites,
        'nb': len(entites), 'total': total, 'tronque': total > limite,
        'couche': couche['libelle'],
    })


def executer_lecture_couche(couche, colonnes, bbox, limite):
    """Lit une couche en ne demandant que des colonnes réellement existantes."""
    if not colonnes:
        try:
            dispo = [c['column_name'] for c in lister_colonnes(couche['table'])]
        except PostgisIndisponible:
            dispo = []
        # Quelques attributs utiles à l'affichage, s'ils existent
        souhaitees = ['code_parcelle', 'id_parcelle', 'code_unique', 'id_machine',
                      'date_creation', 'name_villa', 'statut', 'depart',
                      'sous-pref', 'id_borne', 'nom', 'label', 'libelle']
        colonnes = [c for c in souhaitees if c in dispo][:8]
    return lire_couche(couche['table'], couche['colonne'], colonnes, bbox, limite)
