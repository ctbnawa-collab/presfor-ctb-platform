"""Emprises du projet QGIS : calcul métrique, sans second tampon."""
import hashlib
import io
import json
import zipfile
from functools import lru_cache
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib import messages
from django.views.decorators.http import require_POST
from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping, box
from shapely.ops import transform, unary_union
from shapely import make_valid
from shapely.strtree import STRtree
from .models import CoucheServitude

TMCI='+proj=tmerc +lat_0=0 +lon_0=-5.5 +k=0.9996 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs'
ROLES={
 'Barrage_Buyo':'deduction','Lignes_HTA_15kv_servi_7_5m':'deduction',
 'Lignes_HTB_225kV_servi_15m':'deduction','Lignes_HTB_90kV_servi_15m':'deduction',
 'Route_12_5m_':'deduction','Route_20m':'deduction','water_way_nouveau':'deduction',
 'Parc_et_reserve':'deduction','PUD_SOUBRE':'statut','village_175_v2':'statut',
 'Departs_Nawa':'reference','Limite_Sous_prefectures_Nawa':'reference',
 'Code_village_presfor_Nawa':'reference',
 'Lignes_HTA_intérieur':'reference','Lignes_HTB_225kV_reseau_national':'reference',
 'Lignes_HTB_90kV_reseau_national':'reference','Ligne_400kV':'reference'}

def polygonal(g):
    g=make_valid(g)
    if g.geom_type in ('Polygon','MultiPolygon'):return g
    return unary_union([p for p in getattr(g,'geoms',[]) if p.geom_type in ('Polygon','MultiPolygon')])

def importer_projet(fichier):
    import shapefile
    z=zipfile.ZipFile(fichier)
    if sum(i.file_size for i in z.infolist())>150*1024*1024:
        raise ValueError('Archive décompressée trop volumineuse (150 Mo maximum).')
    objets=[]
    for n in z.namelist():
        if not n.lower().endswith('.shp'):continue
        stem=n.rsplit('/',1)[-1][:-4]
        if stem not in ROLES:continue  # aucune donnée nominative des parcelles importée
        base=n[:-4]
        crs=CRS.from_wkt(z.read(base+'.prj').decode('utf-8-sig'))
        projection=Transformer.from_crs(crs,TMCI,always_xy=True).transform
        rd=shapefile.Reader(shp=io.BytesIO(z.read(n)),shx=io.BytesIO(z.read(base+'.shx')))
        geoms=[]
        for raw in rd.iterShapes():
            if raw.shapeType==0:continue
            g=transform(projection,shape(raw.__geo_interface__))
            if ROLES[stem]!='reference':g=polygonal(g)
            if not g.is_empty:geoms.append(mapping(g))
        if not geoms:continue
        revision=hashlib.sha256(json.dumps(geoms,sort_keys=True).encode()).hexdigest()
        objets.append((stem,dict(libelle=stem.replace('_',' '),role=ROLES[stem],geometries=geoms,revision=revision)))
    if not objets:raise ValueError('Aucune couche reconnue du projet parcellaire.')
    with transaction.atomic():
        for cle,defaults in objets:CoucheServitude.objects.update_or_create(cle=cle,defaults=defaults)
    contexte.cache_clear()
    return len(objets)

@lru_cache(maxsize=1)
def contexte(revisions):
    couches=list(CoucheServitude.objects.exclude(role='reference'))
    deductions=[shape(g) for c in couches if c.role=='deduction' for g in c.geometries]
    statuts=[(c.libelle,STRtree([shape(g) for g in c.geometries])) for c in couches if c.role=='statut']
    return STRtree(deductions),statuts

class CalculServitudes:
    def __init__(self):
        self.projection=Transformer.from_crs(4326,TMCI,always_xy=True).transform
        rev=tuple(CoucheServitude.objects.order_by('cle').values_list('cle','revision'))
        self.disponible=bool(rev)
        self.tree,self.statuts=contexte(rev)
    def calculer(self,geometry,crs='EPSG:4326',surface_origine=None):
        if not self.disponible:return {'calcul_servitudes':'Non calculé — couches non importées'}
        projection=self.projection if crs=='EPSG:4326' else Transformer.from_crs(crs,TMCI,always_xy=True).transform
        g=polygonal(transform(projection,shape(geometry)))
        if g.is_empty or g.area<=0:raise ValueError('Géométrie parcellaire sans superficie exploitable.')
        ids=self.tree.query(g,predicate='intersects')
        retrait=unary_union([g.intersection(self.tree.geometries[i]) for i in ids])
        brute=g.area/10000;deduite=min(brute,retrait.area/10000)
        return dict(surface_geometrique_ha=round(brute,6),surface_origine_ha=surface_origine,
                    surface_servitude_ha=round(deduite,6),surface_nette_ha=round(max(0,brute-deduite),6),
                    statuts_zonage=[nom for nom,t in self.statuts if len(t.query(g,predicate='intersects')) and any(g.intersection(t.geometries[i]).area>0 for i in t.query(g,predicate='intersects'))],
                    calcul_servitudes='Calculé — TMCI-5.5, emprises fusionnées')

@require_POST
def importer(request):
    if not request.user.is_staff:return JsonResponse({'erreur':'Accès administrateur requis'},status=403)
    try:
        n=importer_projet(request.FILES['projet'])
        messages.success(request,f'{n} couches importées : servitudes et statuts disponibles sur la carte.')
    except (ValueError,KeyError,zipfile.BadZipFile) as exc:
        messages.error(request,str(exc))
    return redirect('carte')

def couche(request,cle):
    from django.shortcuts import get_object_or_404
    c=get_object_or_404(CoucheServitude,cle=cle)
    projection=Transformer.from_crs(TMCI,4326,always_xy=True).transform
    bbox=request.GET.get('bbox','')
    try:emprise=box(*map(float,bbox.split(','))) if bbox else None
    except (ValueError,TypeError):return JsonResponse({'erreur':'Emprise invalide'},status=400)
    features=[]
    for raw in c.geometries:
        g=transform(projection,shape(raw))
        if emprise is not None and not g.intersects(emprise):continue
        features.append(dict(type='Feature',geometry=mapping(g),properties=dict(couche=c.libelle,role=c.role)))
    return JsonResponse(dict(type='FeatureCollection',features=features,nb=len(features),total=len(features),tronque=False,couche=c.libelle))
