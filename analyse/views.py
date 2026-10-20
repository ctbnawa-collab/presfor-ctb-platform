import io
import uuid
import logging
import threading
from functools import wraps
from django.views.decorators.http import require_GET
from django.db.models import Count, Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.db import transaction
from .models import (AnalyseSession, StatDepartement, Demande, DEPARTEMENTS,
                     BASES_DATE, COULEURS_ETAPES, ETAPES_CF, en_date)
from .forms import UploadForm
from .processing import run_analyse


# ── Filtre par date, commun aux vues d'instruction ────────────────────────────
def appliquer_filtre_date(demandes, request):
    """Restreint un queryset de demandes à une période.

    La base de date est choisie par l'utilisateur : création de la demande,
    fin du PVCL, fin du RDC… « Toutes les dates » garde une demande dès qu'une
    de ses dates tombe dans la période, et conserve celles qui n'en ont aucune —
    même logique que le filtre appliqué au moment du traitement.

    Renvoie (queryset, contexte) où contexte alimente le formulaire.
    """
    debut = en_date(request.GET.get('d1', '')) or _iso(request.GET.get('d1', ''))
    fin   = en_date(request.GET.get('d2', '')) or _iso(request.GET.get('d2', ''))
    base  = request.GET.get('base', 'toutes')
    bases_valides = {c for c, _ in BASES_DATE}
    if base not in bases_valides:
        base = 'toutes'

    contexte = {
        'd1': request.GET.get('d1', ''), 'd2': request.GET.get('d2', ''),
        'base': base, 'bases_date': BASES_DATE,
        'filtre_date_actif': bool(debut or fin),
    }

    if not (debut or fin):
        return demandes, contexte

    champs = {'creation': 'd_creation', 'pvcl_fin': 'd_pvcl_fin',
              'rdc_fin': 'd_rdc_fin', 'pvcl': 'd_pvcl', 'rdc': 'd_rdc'}

    if base == 'toutes':
        condition = Q()
        for champ in champs.values():
            partiel = Q(**{f'{champ}__isnull': False})
            if debut:
                partiel &= Q(**{f'{champ}__gte': debut})
            if fin:
                partiel &= Q(**{f'{champ}__lte': fin})
            condition |= partiel
        # Les demandes sans aucune date sont conservées
        sans_date = Q(d_creation__isnull=True, d_pvcl__isnull=True,
                      d_pvcl_fin__isnull=True, d_rdc__isnull=True, d_rdc_fin__isnull=True)
        return demandes.filter(condition | sans_date), contexte

    champ = champs[base]
    filtre = {f'{champ}__isnull': False}
    if debut:
        filtre[f'{champ}__gte'] = debut
    if fin:
        filtre[f'{champ}__lte'] = fin
    return demandes.filter(**filtre), contexte


def _iso(v):
    """Lit une date au format AAAA-MM-JJ envoyé par un champ <input type=date>."""
    from datetime import datetime as _dt
    try:
        return _dt.strptime(str(v).strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def compteurs_periode(demandes):
    """Indicateurs recalculés sur le périmètre filtré."""
    total = demandes.count()
    return {
        'total': total,
        'pvcl_termine': demandes.filter(statut_pvcl='Pvcl terminé').count(),
        'pvcl_encours': demandes.filter(statut_pvcl='Pvcl en cours').count(),
        'pvcl_a_creer': demandes.filter(statut_pvcl='Pvcl à créer').count(),
        'rdc_termine':  demandes.filter(statut_rdc='RDC terminé').count(),
        'rdc_encours':  demandes.filter(statut_rdc='RDC encours').count(),
        'rdc_a_creer':  demandes.filter(statut_rdc='RDC à créer').count(),
        'avec_remarque': demandes.exclude(
            Q(remarque_pvcl__in=['', 'RAS']) & Q(remarque_rdc__in=['', 'RAS'])
            & Q(remarque_servitude__in=['', 'RAS'])
            & Q(doublon_fiche__in=['', 'RAS'])
            & Q(remarque_equipe__in=['', 'RAS'])).count(),
        'surface': round(demandes.aggregate(s=Sum('surface'))['s'] or 0, 1),
    }


def remarques_frequentes(demandes, maxi=12):
    """Motifs de blocage les plus fréquents sur le périmètre filtré.

    Regroupe sur l'intitulé du motif — « Signature manquante », « Ajouter
    (Rep. Géomètre) »… — sans les listes de noms qui les suivent, afin d'obtenir
    un classement lisible et cliquable.
    """
    from collections import Counter

    VOLETS = (('remarque_pvcl', 'PVCL'), ('remarque_rdc', 'RDC'),
              ('remarque_servitude', 'Servitude'), ('doublon_fiche', 'Doublon'),
              ('remarque_equipe', 'Équipe'))
    compte = Counter()
    for valeurs in demandes.values_list(*[c for c, _ in VOLETS]):
        vus = set()
        for texte, (_, volet) in zip(valeurs, VOLETS):
            for ligne in (texte or '').split('\n'):
                t = ligne.strip()
                if not t or t.startswith('•'):
                    continue
                intitule = t.split(' :')[0].split(':')[0].strip()
                if intitule and (volet, intitule) not in vus:
                    vus.add((volet, intitule))
                    compte[(volet, intitule)] += 1

    total = demandes.count() or 1
    lignes = [{'volet': v, 'motif': m, 'nb': n, 'pct': round(n / total * 100, 1)}
              for (v, m), n in compte.most_common(maxi)]
    return lignes


def repartition_etapes(demandes):
    """Nombre de demandes par étape de la chaîne CF, dans l'ordre d'avancement."""
    brut = dict(demandes.values_list('etape').annotate(n=Count('id')))
    total = sum(brut.values()) or 1
    lignes = []
    for etape in ETAPES_CF:
        n = brut.get(etape, 0)
        lignes.append({'etape': etape, 'nb': n,
                       'pct': round(n / total * 100, 1),
                       'couleur': COULEURS_ETAPES.get(etape, '#EEF2F6')})
    autres = sum(v for k, v in brut.items() if k not in COULEURS_ETAPES and k)
    if autres:
        lignes.append({'etape': 'Non déterminée', 'nb': autres,
                       'pct': round(autres / total * 100, 1), 'couleur': '#EEF2F6'})
    return lignes


# ── Dashboard ─────────────────────────────────────────────────────────────────
def dashboard(request):
    sessions = AnalyseSession.objects.filter(statut='termine')[:5]
    session  = sessions.first()
    stats_nawa = None
    stats_depts = []
    if session:
        stats_nawa  = session.stats.filter(departement='NAWA').first()
        stats_depts = list(session.stats.filter(departement__in=DEPARTEMENTS))
    return render(request, 'analyse/dashboard.html', {
        'sessions': sessions,
        'session': session,
        'stats_nawa': stats_nawa,
        'stats_depts': stats_depts,
        'departements': DEPARTEMENTS,
    })


_analyse_lock = threading.Lock()


def une_analyse_a_la_fois(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != 'POST': return view(request, *args, **kwargs)
        if not _analyse_lock.acquire(blocking=False):
            return HttpResponse('Une analyse est déjà en cours. Attendez sa fin avant de renvoyer un lot.', status=429)
        try: return view(request, *args, **kwargs)
        finally: _analyse_lock.release()
    return wrapped


# ── Upload & Traitement ────────────────────────────────────────────────────────
@une_analyse_a_la_fois
def upload(request):
    if request.method == 'POST':
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            nom        = form.cleaned_data['nom']
            date_debut = form.cleaned_data.get('date_debut')
            date_fin   = form.cleaned_data.get('date_fin')

            # Récupérer les fichiers CSV (multi-upload)
            csv_files_uploaded = request.FILES.getlist('csv_files')
            parcelle_f = request.FILES.get('parcelle_file')

            if not csv_files_uploaded:
                messages.error(request, "Veuillez sélectionner au moins un fichier CSV.")
                return render(request, 'analyse/upload.html', {'form': form})

            try: progress_token = str(uuid.UUID(request.POST.get('progress_token','')))
            except ValueError: progress_token = str(uuid.uuid4())
            # Créer la session
            session = AnalyseSession.objects.create(
                nom=nom, date_debut_filtre=date_debut,
                date_fin_filtre=date_fin, statut='en_cours',
                rattachement=form.cleaned_data.get('rattachement', 'parcelle'),
                nb_csv=len(csv_files_uploaded),
                qualite_v8={'progress_token':progress_token,'progress':{'done':0,'total':len(csv_files_uploaded),'phase':'Lecture des fichiers'}})

            def progression(done,total,phase):
                session.qualite_v8={'progress_token':progress_token,'progress':{'done':done,'total':total,'phase':phase}}
                AnalyseSession.objects.filter(pk=session.pk).update(qualite_v8=session.qualite_v8)
                logging.getLogger(__name__).info('Analyse %s : %s (%s/%s)',session.pk,phase,done,total)

            try:
                csv_list = [(f.name, f) for f in csv_files_uploaded]
                parcelle_io = io.BytesIO(parcelle_f.read()) if parcelle_f else None

                source = form.cleaned_data.get('source_parcelles', 'postgis')
                rattachement = form.cleaned_data.get('rattachement', 'parcelle')
                result = run_analyse(csv_list, parcelle_io, date_debut, date_fin,
                                     source_parcelles=source,
                                     rattachement=rattachement, progression=progression)

                session.rapprochement = result.get('rapprochement') or {}
                progression(len(csv_list),len(csv_list),'Enregistrement des résultats')
                session.qualite_v8 = {**(result.get('qualite_v8') or {}),**session.qualite_v8}
                for avertissement in (session.qualite_v8.get('avertissements') or []):
                    messages.warning(request, avertissement)

                with transaction.atomic():
                    # Sauvegarder les stats (exclure 'etapes' qui n'est pas un champ modèle)
                    for dept, s in result['stats'].items():
                        s_db = {k: v for k, v in s.items() if k != 'etapes'}
                        StatDepartement.objects.create(
                            session=session, departement=dept, **s_db)

                    # Sauvegarder les demandes
                    df = result['demandes']
                    bulk = []
                    from terrain.identites import resolve_agent, charger_referentiel
                    referentiel = charger_referentiel()
                    for _,row in df.iterrows():
                        identite = resolve_agent(row.get('departement',''), row.get('identite_agent',''), row.get('fichier_source',''), referentiel=referentiel)

                        bulk.append(Demande(
                            session=session,
                            nom_agent=identite.get('nom_agent',''),
                            code_agent=identite.get('code_agent',''),
                            superviseur=identite.get('superviseur',''),
                            controleur=identite.get('controleur',''),
                            departement=row.get('departement',''),
                            sous_prefecture=row.get('sous_prefecture',''),
                            village=row.get('village',''),
                            nom=row.get('nom',''),
                            prenom=row.get('prenom',''),
                            num_demande=row.get('num_demande',''),
                            num_parcelle=row.get('num_parcelle',''),
                            type_certificat=row.get('type_certificat',''),
                            statut_pvcl=row.get('statut_pvcl',''),
                            statut_rdc=row.get('statut_rdc',''),
                            etape=row.get('etape',''),
                            date_creation=row.get('date_creation',''),
                            date_pvcl=row.get('date_pvcl',''),
                            date_rdc=row.get('date_rdc',''),
                            date_pvcl_fin=row.get('date_pvcl_fin',''),
                            date_rdc_fin=row.get('date_rdc_fin',''),
                            date_annonce=row.get('date_annonce',''),
                            date_ouverture=row.get('date_ouverture',''),
                            date_cloture=row.get('date_cloture',''),
                            date_epc=row.get('date_epc',''),
                            date_validation=row.get('date_validation',''),
                            d_creation=en_date(row.get('date_creation','')),
                            d_pvcl=en_date(row.get('date_pvcl','')),
                            d_pvcl_fin=en_date(row.get('date_pvcl_fin','')),
                            d_rdc=en_date(row.get('date_rdc','')),
                            d_rdc_fin=en_date(row.get('date_rdc_fin','')),
                            d_validation=en_date(row.get('date_validation','')),
                            remarque_pvcl=row.get('remarque_pvcl',''),
                            remarque_rdc=row.get('remarque_rdc',''),
                            remarque_servitude=row.get('remarque_servitude',''),
                            remarque_equipe=row.get('remarque_equipe',''),
                            doublon_fiche=row.get('doublon_fiche',''),
                            mode_acquisition=row.get('mode_acquisition','')[:120],
                            photo_id=row.get('photo_id',''),
                            source_qualite=row.get('source_qualite','plateforme'),
                            surface_postgres=float(row['surface_postgres']) if 'surface_postgres' in row and str(row['surface_postgres']) not in ('nan','None','') else None,
                            id_parcelle_source=str(row.get('id_parcelle_source','')),
                            surface=float(row['surface']) if 'surface' in row and str(row['surface']) not in ('nan','None','') else None,
                            fichier_source=row.get('fichier_source',''),
                        ))
                    Demande.objects.bulk_create(bulk, batch_size=500)

                    session.qualite_v8['progress']['phase']='Analyse terminée'
                    session.statut = 'termine'
                    session.save()

                messages.success(request, f"✅ Analyse terminée — {len(df)} demandes traitées.")
                return redirect('session_detail', pk=session.pk)

            except Exception as e:
                import traceback
                session.statut = 'erreur'
                session.message_erreur = traceback.format_exc()
                session.save()
                if form.cleaned_data.get('source_parcelles') == 'postgis':
                    messages.error(
                        request,
                        f"Erreur lors du traitement : {e} — si la base terrain est "
                        f"injoignable, vérifiez la connexion PostgreSQL puis réessayez.")
                else:
                    messages.error(request, f"Erreur lors du traitement : {e}")
    else:
        form = UploadForm()
    return render(request, 'analyse/upload.html', {'form': form})


@require_GET
def progression_analyse(request):
    try: token=str(uuid.UUID(request.GET.get('token','')))
    except ValueError: return JsonResponse({'error':'Identifiant invalide'},status=400)
    session=AnalyseSession.objects.filter(qualite_v8__progress_token=token).first()
    if not session: return JsonResponse({'statut':'transfert'})
    result={'statut':session.statut,**session.qualite_v8.get('progress',{})}
    if session.statut=='termine':
        from django.urls import reverse
        result['url']=reverse('session_detail',kwargs={'pk':session.pk})
    response=JsonResponse(result)
    response['Cache-Control']='no-store'
    return response


# ── Détail d'une session ───────────────────────────────────────────────────────
def session_detail(request, pk):
    session = get_object_or_404(AnalyseSession, pk=pk)
    stats_nawa  = session.stats.filter(departement='NAWA').first()
    stats_depts = list(session.stats.filter(departement__in=DEPARTEMENTS))
    return render(request, 'analyse/session_detail.html', {
        'session': session,
        'stats_nawa': stats_nawa,
        'stats_depts': stats_depts,
        'departements': DEPARTEMENTS,
    })


# ── Analyse par département ────────────────────────────────────────────────────
def departement(request, nom, pk):
    session = get_object_or_404(AnalyseSession, pk=pk)
    stat    = get_object_or_404(StatDepartement, session=session, departement=nom)
    demandes = session.demandes.filter(departement=nom)

    # Filtre par date : il s'applique avant tous les autres
    demandes, ctx_date = appliquer_filtre_date(demandes, request)

    q_sp      = request.GET.get('sp', '')
    q_etape   = request.GET.get('etape', '')
    q_pvcl    = request.GET.get('pvcl', '')
    q_rdc     = request.GET.get('rdc', '')
    q_rech    = request.GET.get('q', '').strip()
    q_remarque = request.GET.get('remarque', '')
    if q_sp:     demandes = demandes.filter(sous_prefecture__icontains=q_sp)
    if q_etape:  demandes = demandes.filter(etape=q_etape)
    if q_pvcl:   demandes = demandes.filter(statut_pvcl=q_pvcl)
    if q_rdc:    demandes = demandes.filter(statut_rdc=q_rdc)
    if q_remarque == '1':
        demandes = demandes.exclude(
            Q(remarque_pvcl__in=['', 'RAS']) & Q(remarque_rdc__in=['', 'RAS'])
            & Q(remarque_servitude__in=['', 'RAS'])
            & Q(doublon_fiche__in=['', 'RAS'])
            & Q(remarque_equipe__in=['', 'RAS']))
    q_motif = request.GET.get('motif', '').strip()
    if q_motif:
        demandes = demandes.filter(
            Q(remarque_pvcl__icontains=q_motif) | Q(remarque_rdc__icontains=q_motif)
            | Q(remarque_servitude__icontains=q_motif)
            | Q(doublon_fiche__icontains=q_motif)
            | Q(remarque_equipe__icontains=q_motif))
    if q_rech:
        demandes = demandes.filter(
            Q(num_demande__icontains=q_rech) | Q(num_parcelle__icontains=q_rech)
            | Q(nom__icontains=q_rech) | Q(prenom__icontains=q_rech)
            | Q(village__icontains=q_rech))

    base = session.demandes.filter(departement=nom)
    sp_list = base.order_by('sous_prefecture').values_list(
        'sous_prefecture', flat=True).distinct()

    if request.GET.get('export') == 'csv':
        return _export_demandes_csv(demandes, f'demandes_{nom}.csv')

    contexte = {
        'session': session, 'stat': stat, 'nom': nom,
        'demandes': demandes[:500],
        'total': demandes.count(),
        'etapes': ETAPES_CF,
        'sp_list': sorted(s for s in sp_list if s),
        'q_sp': q_sp, 'q_etape': q_etape, 'q_pvcl': q_pvcl, 'q_rdc': q_rdc,
        'q': q_rech, 'q_remarque': q_remarque,
        'statuts_pvcl': ['Pvcl à créer', 'Pvcl en cours', 'Pvcl terminé'],
        'statuts_rdc':  ['RDC à créer', 'RDC encours', 'RDC terminé'],
        'compteurs': compteurs_periode(demandes),
        'etapes_repartition': repartition_etapes(demandes),
        'remarques_frequentes': remarques_frequentes(demandes),
        'q_motif': q_motif,
    }
    contexte.update(ctx_date)
    return render(request, 'analyse/departement.html', contexte)


def demande_detail(request, pk):
    """Fiche complète d'un dossier : toutes les remarques, sans troncature."""
    demande = get_object_or_404(Demande.objects.select_related('session'), pk=pk)
    voisines = (Demande.objects
                .filter(session=demande.session, num_parcelle=demande.num_parcelle)
                .exclude(pk=demande.pk)[:10]) if demande.num_parcelle else []
    return render(request, 'analyse/demande_detail.html', {
        'd': demande, 'session': demande.session, 'voisines': voisines,
        'titre': f'Dossier {demande.num_demande}',
    })


def _export_demandes_csv(demandes, nom_fichier):
    import csv
    reponse = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    reponse['Content-Disposition'] = f'attachment; filename="{nom_fichier}"'
    reponse.write('\ufeff')
    w = csv.writer(reponse, delimiter=';')
    w.writerow(['N° demande', 'Département', 'Sous-préfecture', 'Village', 'Nom', 'Prénom',
                'N° parcelle', 'Type', 'Statut PVCL', 'Statut RDC', 'Étape',
                'Date demande', 'Date PVCL', 'Fin PVCL', 'Date RDC', 'Fin RDC',
                'Annonce pub', 'Ouverture pub', 'Clôture pub', 'Constat EPC',
                'Validation', 'Surface (ha)', 'Remarque', 'nom_agent', 'code_agent', 'superviseur', 'controleur'])
    for d in demandes:
        w.writerow([d.num_demande, d.departement, d.sous_prefecture, d.village,
                    d.nom, d.prenom, d.num_parcelle, d.type_certificat,
                    d.statut_pvcl, d.statut_rdc, d.etape,
                    d.date_creation, d.date_pvcl, d.date_pvcl_fin,
                    d.date_rdc, d.date_rdc_fin, d.date_annonce, d.date_ouverture,
                    d.date_cloture, d.date_epc, d.date_validation,
                    d.surface if d.surface is not None else '', d.remarque_courte,d.nom_agent,d.code_agent,d.superviseur,d.controleur])
    return reponse


# ── Région NAWA ───────────────────────────────────────────────────────────────
def region_nawa(request, pk):
    session    = get_object_or_404(AnalyseSession, pk=pk)
    stat_nawa  = get_object_or_404(StatDepartement, session=session, departement='NAWA')
    stats_depts = list(session.stats.filter(departement__in=DEPARTEMENTS))

    demandes, ctx_date = appliquer_filtre_date(session.demandes.all(), request)
    par_dept = []
    for d in DEPARTEMENTS:
        sous_ensemble = demandes.filter(departement=d)
        c = compteurs_periode(sous_ensemble)
        c['departement'] = d
        par_dept.append(c)

    contexte = {
        'session': session,
        'stat': stat_nawa,
        'stats_depts': stats_depts,
        'departements': DEPARTEMENTS,
        'compteurs': compteurs_periode(demandes),
        'par_dept': par_dept,
        'etapes_repartition': repartition_etapes(demandes),
        'remarques_frequentes': remarques_frequentes(demandes),
    }
    contexte.update(ctx_date)
    return render(request, 'analyse/region.html', contexte)


# ── API JSON pour les graphiques ───────────────────────────────────────────────
def api_stats(request, pk):
    session = get_object_or_404(AnalyseSession, pk=pk)
    stats   = session.stats.filter(departement__in=DEPARTEMENTS)
    data = {
        'labels': [s.departement for s in stats],
        'demandes':    [s.nb_demandes for s in stats],
        'rdc_encours': [s.nb_rdc_encours for s in stats],
        'rdc_termine': [s.nb_rdc_termine for s in stats],
        'pvcl_encours':[s.nb_pvcl_encours for s in stats],
        'pvcl_termine':[s.nb_pvcl_termine for s in stats],
        'superficie_levee':    [s.superficie_levee for s in stats],
        'superficie_enquetee': [s.superficie_enquetee for s in stats],
    }
    return JsonResponse(data)


# ── Suppression de session ─────────────────────────────────────────────────────
def delete_session(request, pk):
    session = get_object_or_404(AnalyseSession, pk=pk)
    if request.method == 'POST':
        session.delete()
        messages.success(request, "Session supprimée.")
        return redirect('dashboard')
    return render(request, 'analyse/confirm_delete.html', {'session': session})
