from datetime import date
from django.test import TestCase
from shapely.geometry import box,mapping
from .models import CoucheServitude
from .servitudes import CalculServitudes,TMCI,contexte
from .performance import calendar

class ServitudesTests(TestCase):
    def setUp(self):contexte.cache_clear()
    def test_union_and_status_without_deduction(self):
        CoucheServitude.objects.create(cle='routes',libelle='Routes',role='deduction',revision='1',geometries=[mapping(box(0,0,60,100)),mapping(box(40,0,80,100))])
        CoucheServitude.objects.create(cle='PUD',libelle='PUD Soubré',role='statut',revision='2',geometries=[mapping(box(0,0,100,100))])
        r=CalculServitudes().calculer(mapping(box(0,0,100,100)),TMCI,surface_origine=1.01)
        self.assertEqual(r['surface_servitude_ha'],.8)
        self.assertEqual(r['surface_nette_ha'],.2)
        self.assertEqual(r['surface_origine_ha'],1.01)
        self.assertEqual(r['statuts_zonage'],['PUD Soubré'])
    def test_missing_layers_not_zero(self):
        self.assertNotIn('surface_nette_ha',CalculServitudes().calculer(mapping(box(0,0,1,1)),TMCI))
    def test_calendar_saturday_friday_even_with_old_configuration(self):
        weeks=calendar({'weeks':{'old':{'s':'2026-09-26','e':'2026-10-04'}}},date(2026,11,1))
        for w in weeks:
            self.assertEqual(date.fromisoformat(w['s']).weekday(),5)
            self.assertEqual(date.fromisoformat(w['e']).weekday(),4)
        w=next(w for w in weeks if w['s']<='2026-10-03'<=w['e'])
        self.assertEqual((w['s'],w['e']),('2026-10-03','2026-10-09'))
    def test_project_layer_api_requires_login_and_preserves_role(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        CoucheServitude.objects.create(cle='PUD',libelle='PUD',role='statut',revision='2',geometries=[mapping(box(350000,600000,350100,600100))])
        c=Client();self.assertEqual(c.get('/terrain/api/servitudes/PUD/').status_code,302)
        c.force_login(get_user_model().objects.create_user('test-carto'))
        response=c.get('/terrain/api/servitudes/PUD/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['features'][0]['properties']['role'],'statut')
    def test_quality_postgres_keeps_gross_and_computes_net(self):
        from unittest.mock import patch
        import json
        from analyse.processing import load_parcelles_from_postgis
        CoucheServitude.objects.create(cle='routes',libelle='Routes',role='deduction',revision='1',geometries=[mapping(box(350000,600000,350050,600100))])
        data={'code_parcelle':'001','surface_ha':1,'geometrie_calcul':json.dumps(mapping(box(350000,600000,350100,600100))),'srid_calcul':0}
        with patch('terrain.db.executer_fichier',return_value=[data]):result=load_parcelles_from_postgis()
        entry=next(iter(result.values()))[0]
        self.assertEqual(entry['surface_ha'],1)
        self.assertEqual(entry['servitudes']['surface_nette_ha'],.5)
    def test_rendement_uses_net_without_overwriting_postgres(self):
        from analyse.models import AnalyseSession,Demande
        from .models import AgentRendement
        from .performance import rows_for
        from django.test import override_settings
        s=AnalyseSession.objects.create(nom='Test net',statut='termine')
        AgentRendement.objects.create(departement='BUYO',code_agent='AGENT1',nom_agent='Agent Exemple',superviseur='Superviseur Exemple')
        d=Demande.objects.create(session=s,num_demande='NET1',departement='BUYO',nom_agent='Agent Exemple',d_creation=date(2026,10,20),source_qualite='v8',surface_postgres=1,servitudes={'surface_nette_ha':.6},statut_pvcl='Pvcl terminé',statut_rdc='RDC terminé')
        with override_settings(PRESFOR_POSTGRES_SURFACES_ONLY=True):rows,issues,_=rows_for(s.pk)
        self.assertFalse(issues)
        self.assertEqual(rows[0]['ha'],.6)
        self.assertEqual(rows[0]['w'],'2026-10-17_2026-10-23')
        d.refresh_from_db();self.assertEqual(d.surface_postgres,1)
