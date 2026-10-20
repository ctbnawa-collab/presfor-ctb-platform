"""Bridge between updated quality results, canonical agent roster and supplied UI."""
import csv
import io
import json
import math
from datetime import date,timedelta
from django.db import transaction
from django.conf import settings
from django.http import JsonResponse,HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin
from .models import AgentRendement,LigneRendement,ParametresRendement
from .identites import resolve_agent,normaliser,charger_referentiel
from .equipes import primes_equipes, regrouper_equipes
from .parcelles_count import compter_parcelles
from .exclusions import est_exclu,index_exclusions
from analyse.models import AnalyseSession,Demande,en_date

INITIAL=[('14-au-18 setp','2026-09-14','2026-09-18'),('19-au-25 setp','2026-09-19','2026-09-25'),('26 sept-au-04 oct','2026-09-26','2026-10-04')]
DEFAULT={'obj':{'BUYO':150,'SOUBRE':150,'MEAGUI':300},'seuil':100}

def config():
    row=ParametresRendement.objects.first()
    cfg=dict(row.donnees if row else DEFAULT)
    cfg.pop('weeks',None)
    return cfg

def calendar(cfg,until=None):
    # Toutes les périodes, y compris historiques, suivent samedi → vendredi.
    start=date(2026,9,12)
    until=max(until or date.today(),date.today())+timedelta(days=7)
    out=[]
    while start<=until:
        end=start+timedelta(days=6)
        key=start.isoformat()+'_'+end.isoformat()
        out.append(dict(k=key,s=start.isoformat(),e=end.isoformat()))
        start=end+timedelta(days=1)
    return out

def contexte_rendement():
    cfg=config()
    return dict(cfg=cfg,referentiel=charger_referentiel(),weeks=calendar(cfg),resolutions={})

def validate(row,demande=None,contexte=None):
    if not isinstance(row,dict):raise ValueError('Ligne invalide')
    n=str(row.get('n','')).strip()
    if not n or len(n)>60:raise ValueError('Numéro de demande requis, 60 caractères maximum')
    dt=date.fromisoformat(str(row.get('dt',''))).isoformat()
    if not '1900-01-01'<=dt<='2100-12-31':raise ValueError('Date hors période 1900 à 2100')
    dep=normaliser(row.get('d'))
    contexte=contexte if contexte is not None else contexte_rendement()
    if settings.PRESFOR_POSTGRES_SURFACES_ONLY:
        if demande is None:
            demande=(contexte['demandes'].get(n) if 'demandes' in contexte else Demande.objects.filter(num_demande=n,session__statut='termine').order_by('-session__date_creation','-pk').first())
        if not demande or demande.surface_postgres is None:
            raise ValueError(f'{n} : superficie PostgreSQL absente. Relancer une analyse sur la base terrain.')
        if dep != normaliser(demande.departement):raise ValueError('Département différent du dossier analysé')
    identite=row.get('nom_agent') or row.get('a') or row.get('code_agent')
    key=(dep,str(identite or ''))
    if key not in contexte['resolutions']:
        contexte['resolutions'][key]=resolve_agent(dep,identite,referentiel=contexte['referentiel'])
    agent=contexte['resolutions'][key]
    if not agent:raise ValueError(f'{n} : agent inconnu ou ambigu dans {dep}')
    ha=float(demande.surface_postgres) if settings.PRESFOR_POSTGRES_SURFACES_ONLY else float(str(row.get('ha','')).replace(',','.'))
    if demande is not None and demande.servitudes.get('surface_nette_ha') is not None:
        ha=float(demande.servitudes['surface_nette_ha'])
    if not math.isfinite(ha) or ha<0:raise ValueError('Superficie invalide')
    if row.get('p') not in ('c','e','t') or row.get('r') not in ('c','e','t'):raise ValueError('Statut PVCL/RDC invalide')
    weeks=contexte['weeks']
    if dt>weeks[-1]['e']:
        weeks=calendar(contexte['cfg'],date.fromisoformat(dt));contexte['weeks']=weeks
    key=''  # La semaine découle toujours de la date, samedi → vendredi.
    week=next((w for w in weeks if w['k']==key),None) if key else next((w for w in weeks if w['s']<=dt<=w['e']),None)
    if not week:raise ValueError('Semaine inconnue ou date antérieure au calendrier')
    # Période de référence déduite de la date de création.
    return dict(n=n,dt=dt,w=week['k'],d=dep,a=agent['nom_agent'],s=agent['superviseur'],nom_agent=agent['nom_agent'],code_agent=agent['code_agent'],controleur=agent['controleur'],ha=ha,p=row['p'],r=row['r'])

def rows_for(session_id=None,contexte=None):
    contexte=contexte if contexte is not None else contexte_rendement()
    sessions=AnalyseSession.objects.filter(statut='termine')
    session=sessions.filter(pk=session_id).first() if str(session_id or '').isdigit() else sessions.first()
    result={};issues=[];seen=set();excluded=index_exclusions()
    if session:
        for d in session.demandes.only('session_id','num_demande','id_parcelle_source','num_parcelle','village','source_qualite','d_creation','date_creation','departement','nom_agent','code_agent','surface_postgres','servitudes','statut_pvcl','statut_rdc','surface').order_by('-pk').iterator(chunk_size=500):
            if not d.num_demande or d.num_demande in seen:continue
            seen.add(d.num_demande)
            if est_exclu(d.id_parcelle_source,d.num_parcelle,d.village,excluded):continue
            if d.source_qualite!='v8':issues.append(f'{d.num_demande} : contrôle 8.3 non exécuté');continue
            dt=d.d_creation or en_date(d.date_creation)
            try:
                row=validate(dict(n=d.num_demande,dt=dt.isoformat() if dt else '',d=d.departement,a=d.nom_agent or d.code_agent,ha=d.surface,p='t' if d.statut_pvcl=='Pvcl terminé' else 'e' if 'cours' in d.statut_pvcl else 'c',r='t' if d.statut_rdc=='RDC terminé' else 'e' if 'cours' in d.statut_rdc else 'c'),demande=d,contexte=contexte)
                row.update(source='controle_qualite_8.3',id_parcelle=d.id_parcelle_source,code_parcelle=d.num_parcelle,village=d.village);result[row['n']]=row
            except (ValueError,TypeError) as exc:issues.append(f'{d.num_demande} : {exc}')
    return list(result.values()),issues,session

@ensure_csrf_cookie
def page(request):
    return periodic(request,mode='hebdomadaire')

@ensure_csrf_cookie
@xframe_options_sameorigin
def model_page(request):
    return page(request)

@require_http_methods(['GET','POST','DELETE'])
def api(request):
    if request.method=='GET':
        rows,issues,session=rows_for(request.GET.get('session'))
        if request.GET.get('export')=='csv':
            response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="rendement_global.csv"';response.write('\ufeff');writer=csv.writer(response,delimiter=';')
            keys=['n','dt','w','d','nom_agent','code_agent','s','controleur','ha','p','r','source'];writer.writerow(['N°DEMANDE','Date création demande','Semaine','DEPARTEMENT','nom_agent','code_agent','SUPERVISEUR','CONTROLEUR','superficie','STATUT_PVCL','STATUT_RDC','Source'])
            for row in rows:writer.writerow([{'c':'À créer','e':'En cours','t':'Terminé'}.get(row.get(k),'') if k in ('p','r') else row.get(k,'') for k in keys])
            return response
        return JsonResponse(dict(rows=rows,issues=issues,config=config(),agents=list(AgentRendement.objects.values()),session=session.pk if session else None))
    try:
        payload=json.loads(request.body)
        if not isinstance(payload,dict):raise ValueError('Objet JSON requis')
        if request.method=='DELETE':
            LigneRendement.objects.filter(num_demande=str(payload.get('n',''))).delete()
        elif 'config' in payload:
            cfg=payload['config']
            if not isinstance(cfg,dict) or not isinstance(cfg.get('obj'),dict) or not isinstance(cfg.get('weeks',{}),dict):raise ValueError('Paramètres invalides')
            if set(cfg.get('obj',{}))!={'BUYO','SOUBRE','MEAGUI'}:raise ValueError('Trois objectifs requis')
            for val in cfg['obj'].values():
                if not math.isfinite(float(val)) or float(val)<=0:raise ValueError('Objectif strictement positif requis')
            if not 0<float(cfg.get('seuil',0))<=100:raise ValueError('Seuil invalide')
            intervals=[]
            for k,w in cfg.get('weeks',{}).items():
                if len(k)>100:raise ValueError('Identifiant semaine trop long')
                s,e=date.fromisoformat(w['s']),date.fromisoformat(w['e'])
                if s.weekday()!=5 or e!=s+timedelta(days=6):raise ValueError('Une semaine doit aller du samedi au vendredi')
            candidate=calendar(cfg)
            intervals=sorted((w['s'],w['e']) for w in candidate)
            if any(a[1]>=b[0] for a,b in zip(intervals,intervals[1:])):raise ValueError('Les semaines se chevauchent')
            ParametresRendement.objects.update_or_create(pk=1,defaults={'donnees':cfg})
        else:
            raise ValueError('Le rendement est alimenté uniquement par les analyses CSV terminées.')
        return JsonResponse({'ok':True})
    except (ValueError,TypeError,KeyError,OverflowError) as exc:return JsonResponse({'error':str(exc)},status=400)


def periodic(request,mode='journalier'):
    import calendar as cal
    today=date.today()
    contexte=contexte_rendement()
    data,issues,session=rows_for(request.GET.get('session'),contexte=contexte)
    cfg=contexte['cfg']
    last=max([date.fromisoformat(r['dt']) for r in data]+[today])
    weeks=calendar(cfg,last+timedelta(days=14))
    active_weeks={r['w'] for r in data}
    options=[w for w in weeks if w['k'] in active_weeks]
    if mode=='hebdomadaire':
        default=options[-1]['k'] if options else next(w['k'] for w in weeks if w['s']<=today.isoformat()<=w['e'])
        key=request.GET.get('periode',default)
        week=next((w for w in weeks if w['k']==key),next(w for w in weeks if w['k']==default))
        start,end=date.fromisoformat(week['s']),date.fromisoformat(week['e'])
        periode=week['k']
        selection=[r for r in data if r['w']==periode]
    else:
        try:
            chosen=date.fromisoformat(request.GET.get('periode',today.isoformat()) if mode=='journalier' else request.GET.get('periode',today.strftime('%Y-%m'))+'-01')
        except ValueError:chosen=today
        start=chosen if mode=='journalier' else chosen.replace(day=1)
        end=chosen if mode=='journalier' else chosen.replace(day=cal.monthrange(chosen.year,chosen.month)[1])
        periode=chosen.isoformat() if mode=='journalier' else chosen.strftime('%Y-%m')
        selection=[r for r in data if start.isoformat()<=r['dt']<=end.isoformat()]
    primes=primes_equipes(data,cfg,weeks)
    departement=request.GET.get('departement','')
    superviseur=request.GET.get('superviseur','')
    selection=[r for r in selection if (not departement or r['d']==departement) and (not superviseur or r['s']==superviseur)]
    equipes=regrouper_equipes(selection,primes)
    groups={};daily={}
    for row in selection:
        k=(row['d'],row['s'],row['a']);g=groups.setdefault(k,dict(d=k[0],s=k[1],a=k[2],code_agent=row['code_agent'],ha=0,n=0,pt=0,rt=0,pr=0,parcelles=[]))
        g['parcelles'].append(row)
        g['ha']+=row['ha'];g['n']+=1;g['pt']+=row['p']=='t';g['rt']+=row['r']=='t';g['pr']+=row['p']=='t' and row['r']=='t'
        day=daily.setdefault(row['dt'],dict(ha=0,n=0));day['ha']+=row['ha'];day['n']+=1
    for g in groups.values():g['nb_parcelles']=compter_parcelles(g.pop('parcelles'))
    rows=sorted(groups.values(),key=lambda r:(r['d'],r['s'],-r['ha'],r['a']))
    for team in equipes:
        team['detail_agents']=[r for r in rows if r['d']==team['d'] and r['s']==team['s']]
        team['ratio']=round(team['pr']/team['n']*100,1) if team['n'] else 0
    if request.GET.get('export'):
        response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="rendement.csv"';response.write('\ufeff');w=csv.writer(response,delimiter=';')
        if request.GET.get('export')=='agents':
            w.writerow(['Département','Superviseur','nom_agent','Superficie (ha)','Nombre de parcelles','Demandes','PVCL terminés','RDC terminés','PVCL et RDC terminés'])
            for r in rows:w.writerow([r[k] for k in ('d','s','a','ha','nb_parcelles','n','pt','rt','pr')])
        else:
            w.writerow(['Département','Équipe du superviseur','Agents actifs','Superficie (ha)','Demandes','PVCL terminés','RDC terminés','PVCL et RDC terminés','Primes hebdomadaires'])
            for r in equipes:w.writerow([r[k] for k in ('d','s','effectif','ha','n','pt','rt','pr')]+[' ; '.join(p['debut']+' au '+p['fin']+' : '+p['statut'] for p in r['primes'])])
        return response
    total=dict(ha=round(sum(t['ha'] for t in equipes),2),n=len(selection),equipes=len(equipes),agents=len(rows),pr=sum(t['pr'] for t in equipes))
    chart_teams=sorted(equipes,key=lambda t:-t['ha'])[:15]
    graph=dict(noms=[t['s']+' · '+t['d'] for t in chart_teams],ha=[round(t['ha'],2) for t in chart_teams],n=[t['n'] for t in chart_teams],jours=sorted(daily),jour_ha=[round(daily[d]['ha'],2) for d in sorted(daily)],jour_n=[daily[d]['n'] for d in sorted(daily)])
    return render(request,'terrain/performance_native.html',dict(titre='Rendement '+mode,mode=mode,equipes=equipes,periode=periode,options=options,rows=rows,issues=issues[:30],nb_issues=len(issues),sessions=AnalyseSession.objects.filter(statut='termine'),session=session,total=total,graph=graph,graph_height=max(420,len(chart_teams)*44+100),cfg=cfg,departement=departement,superviseur=superviseur,superviseurs=sorted({r['s'] for r in data if not departement or r['d']==departement}),date_debut=start,date_fin=end))


def referentiel(request):
    return render(request,'terrain/referentiel_rendement.html',{'titre':'Agents et superviseurs','agents_rendement':AgentRendement.objects.all()})


def geoserver(request):
    from django.conf import settings
    return render(request,'terrain/geoserver.html',{'titre':'Carte GeoServer','geo':{'url':settings.GEOSERVER_WMS_URL,'layers':settings.GEOSERVER_LAYERS}})



def analyses_agent(request):
    from django.shortcuts import get_object_or_404
    from django.core.paginator import Paginator
    if not str(request.GET.get('session','')).isdigit():return HttpResponse('Analyse requise',status=400)
    session=get_object_or_404(AnalyseSession,pk=request.GET.get('session'),statut='termine')
    agent=get_object_or_404(AgentRendement,departement=request.GET.get('departement'),code_agent=request.GET.get('agent'))
    demandes=session.demandes.filter(departement=agent.departement,code_agent=agent.code_agent)
    debut=request.GET.get('debut','');fin=request.GET.get('fin','')
    try:
        if debut:demandes=demandes.filter(d_creation__gte=date.fromisoformat(debut))
        if fin:demandes=demandes.filter(d_creation__lte=date.fromisoformat(fin))
    except ValueError:
        return HttpResponse('Période invalide',status=400)
    return render(request,'terrain/analyses_agent.html',dict(session=session,agent=agent,page=Paginator(demandes.order_by('num_demande'),50).get_page(request.GET.get('page')),nb=demandes.count(),debut=debut,fin=fin))
