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
from .models import AgentRendement,LigneRendement,ParametresRendement
from .identites import resolve_agent,normaliser
from .exclusions import est_exclu,index_exclusions
from analyse.models import AnalyseSession,Demande,en_date

INITIAL=[('14-au-18 setp','2026-09-14','2026-09-18'),('19-au-25 setp','2026-09-19','2026-09-25'),('26 sept-au-04 oct','2026-09-26','2026-10-04')]
DEFAULT={'obj':{'BUYO':150,'SOUBRE':150,'MEAGUI':300},'seuil':100}

def config():
    row=ParametresRendement.objects.first()
    return row.donnees if row else DEFAULT

def calendar(cfg,until=None):
    weeks={k:dict(k=k,s=s,e=e) for k,s,e in INITIAL}
    weeks.update({k:dict(k=k,s=w['s'],e=w['e']) for k,w in cfg.get('weeks',{}).items()})
    out=sorted(weeks.values(),key=lambda w:w['s'])
    end=date.fromisoformat(out[-1]['e']);until=max(until or date.today(),date.today())+timedelta(days=7)
    while end<until:
        start=end+timedelta(days=1);end=start+timedelta(days=6);key=start.isoformat()+'_'+end.isoformat()
        out.append(dict(k=key,s=start.isoformat(),e=end.isoformat()))
    return out

def validate(row,demande=None):
    if not isinstance(row,dict):raise ValueError('Ligne invalide')
    n=str(row.get('n','')).strip()
    if not n or len(n)>60:raise ValueError('Numéro de demande requis, 60 caractères maximum')
    dt=date.fromisoformat(str(row.get('dt',''))).isoformat()
    if not '1900-01-01'<=dt<='2100-12-31':raise ValueError('Date hors période 1900 à 2100')
    dep=normaliser(row.get('d'))
    if settings.PRESFOR_POSTGRES_SURFACES_ONLY:
        demande=demande or Demande.objects.filter(num_demande=n,session__statut='termine').order_by('-session__date_creation','-pk').first()
        if not demande or demande.surface_postgres is None:
            raise ValueError(f'{n} : superficie PostgreSQL absente. Relancer une analyse sur la base terrain.')
        if dep != normaliser(demande.departement):raise ValueError('Département différent du dossier analysé')
    agent=resolve_agent(dep,row.get('nom_agent') or row.get('a') or row.get('code_agent'))
    if not agent:raise ValueError(f'{n} : agent inconnu ou ambigu dans {dep}')
    ha=float(demande.surface_postgres) if settings.PRESFOR_POSTGRES_SURFACES_ONLY else float(str(row.get('ha','')).replace(',','.'))
    if not math.isfinite(ha) or ha<0:raise ValueError('Superficie invalide')
    if row.get('p') not in ('c','e','t') or row.get('r') not in ('c','e','t'):raise ValueError('Statut PVCL/RDC invalide')
    cfg=config();weeks=calendar(cfg,date.fromisoformat(dt))
    key=str(row.get('w',''))
    week=next((w for w in weeks if w['k']==key),None) if key else next((w for w in weeks if w['s']<=dt<=w['e']),None)
    if not week:raise ValueError('Semaine inconnue ou date antérieure au calendrier')
    # Supplied model permits an explicit reporting week independent of creation date.
    return dict(n=n,dt=dt,w=week['k'],d=dep,a=agent['nom_agent'],s=agent['superviseur'],nom_agent=agent['nom_agent'],code_agent=agent['code_agent'],controleur=agent['controleur'],ha=ha,p=row['p'],r=row['r'])

def rows_for(session_id=None):
    sessions=AnalyseSession.objects.filter(statut='termine')
    session=sessions.filter(pk=session_id).first() if str(session_id or '').isdigit() else sessions.first()
    result={};issues=[];seen=set();excluded=index_exclusions()
    if session:
        for d in session.demandes.order_by('-pk'):
            if not d.num_demande or d.num_demande in seen:continue
            seen.add(d.num_demande)
            if est_exclu(d.id_parcelle_source,d.num_parcelle,d.village,excluded):continue
            if d.source_qualite!='v8':issues.append(f'{d.num_demande} : contrôle 8.3 non exécuté');continue
            dt=d.d_creation or en_date(d.date_creation)
            try:
                row=validate(dict(n=d.num_demande,dt=dt.isoformat() if dt else '',d=d.departement,a=d.nom_agent or d.code_agent,ha=d.surface,p='t' if d.statut_pvcl=='Pvcl terminé' else 'e' if 'cours' in d.statut_pvcl else 'c',r='t' if d.statut_rdc=='RDC terminé' else 'e' if 'cours' in d.statut_rdc else 'c'),demande=d)
                row['source']='controle_qualite_8.3';result[row['n']]=row
            except (ValueError,TypeError) as exc:issues.append(f'{d.num_demande} : {exc}')
    for row in LigneRendement.objects.all():
        # Explicit manual imports are stored separately and tagged, never alter quality results.
        try:
            value=validate(row.donnees) if settings.PRESFOR_POSTGRES_SURFACES_ONLY else row.donnees
            result[row.num_demande]={**value,'source':'saisie_ou_import'}
        except (ValueError,TypeError) as exc:issues.append(f'{row.num_demande} : {exc}')
    return list(result.values()),issues,session

@ensure_csrf_cookie
def page(request):
    return render(request,'terrain/performance.html',{'titre':'Rendement hebdomadaire','sessions':AnalyseSession.objects.filter(statut='termine'),'selection':request.GET.get('session','')})

@ensure_csrf_cookie
def model_page(request):
    return render(request,'terrain/performance_model.html')

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
                if s>e:raise ValueError('Dates de semaine invalides')
            candidate=calendar(cfg)
            intervals=sorted((w['s'],w['e']) for w in candidate)
            if any(a[1]>=b[0] for a,b in zip(intervals,intervals[1:])):raise ValueError('Les semaines se chevauchent')
            ParametresRendement.objects.update_or_create(pk=1,defaults={'donnees':cfg})
        else:
            values=payload.get('rows',[payload.get('row')])
            if not isinstance(values,list) or len(values)>10000:raise ValueError('Lot invalide, maximum 10000 lignes')
            clean=[validate(r) for r in values]
            with transaction.atomic():
                for row in clean:LigneRendement.objects.update_or_create(num_demande=row['n'],defaults={'donnees':row})
        return JsonResponse({'ok':True})
    except (ValueError,TypeError,KeyError,OverflowError) as exc:return JsonResponse({'error':str(exc)},status=400)


def periodic(request,mode='journalier'):
    import calendar as cal
    today=date.today()
    try:
        chosen=date.fromisoformat(request.GET.get('periode',today.isoformat()) if mode=='journalier' else request.GET.get('periode',today.strftime('%Y-%m'))+'-01')
    except ValueError:chosen=today
    start=chosen if mode=='journalier' else chosen.replace(day=1)
    end=chosen if mode=='journalier' else chosen.replace(day=cal.monthrange(chosen.year,chosen.month)[1])
    data,issues,session=rows_for(request.GET.get('session'));groups={}
    for row in data:
        if not start.isoformat()<=row['dt']<=end.isoformat():continue
        k=(row['d'],row['s'],row['a']);g=groups.setdefault(k,dict(d=k[0],s=k[1],a=k[2],ha=0,n=0,pt=0,rt=0))
        g['ha']+=row['ha'];g['n']+=1;g['pt']+=row['p']=='t';g['rt']+=row['r']=='t'
    rows=sorted(groups.values(),key=lambda r:-r['ha'])
    if request.GET.get('export')=='csv':
        response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="rendement.csv"';response.write('\ufeff');w=csv.writer(response,delimiter=';');w.writerow(['Département','Superviseur','nom_agent','Superficie','Demandes','PVCL terminés','RDC terminés'])
        for r in rows:w.writerow([r[k] for k in ('d','s','a','ha','n','pt','rt')])
        return response
    return render(request,'terrain/performance_period.html',dict(titre='Rendement '+mode,mode=mode,periode=chosen.isoformat() if mode=='journalier' else chosen.strftime('%Y-%m'),rows=rows,issues=issues,sessions=AnalyseSession.objects.filter(statut='termine'),session=session))


def referentiel(request):
    return render(request,'terrain/referentiel_rendement.html',{'titre':'Agents et superviseurs','agents_rendement':AgentRendement.objects.all()})


def geoserver(request):
    from django.conf import settings
    return render(request,'terrain/geoserver.html',{'titre':'Carte GeoServer','geo':{'url':settings.GEOSERVER_WMS_URL,'layers':settings.GEOSERVER_LAYERS}})
