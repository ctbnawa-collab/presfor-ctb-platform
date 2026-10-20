import io
from datetime import date
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from analyse.models import AnalyseSession,Demande
from .models import AgentRendement,LigneRendement,Opposition
from .performance import rows_for
from .oppositions_classeur import importer_classeur
from .oppositions import synthese

class WorkflowTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('workflow-test'))
        self.s=AnalyseSession.objects.create(nom='CSV Test',statut='termine')
        self.a=AgentRendement.objects.create(departement='BUYO',code_agent='AGENT1',nom_agent='Agent Exemple',superviseur='Superviseur Exemple')
        self.d=Demande.objects.create(session=self.s,num_demande='CSV1',code_agent='AGENT1',nom_agent='Agent Exemple',departement='BUYO',d_creation=date(2026,10,20),surface_postgres=2,source_qualite='v8',fichier_source='agent_test.csv')
    def test_manual_rows_never_feed_csv_rendement(self):
        LigneRendement.objects.create(num_demande='CSV1',donnees={'ha':999})
        LigneRendement.objects.create(num_demande='MANUAL',donnees={'ha':999})
        rows,issues,_=rows_for(self.s.pk)
        self.assertFalse(issues);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['ha'],2)
    def test_agent_link_and_selected_session_detail(self):
        r=self.client.get('/terrain/rendements/mensuel/',{'periode':'2026-10','session':self.s.pk})
        self.assertContains(r,'analyses-agent/')
        r=self.client.get('/terrain/rendements/analyses-agent/',{'session':self.s.pk,'departement':'BUYO','agent':'AGENT1','debut':'2026-10-17','fin':'2026-10-23'})
        self.assertContains(r,'CSV1');self.assertContains(r,'agent_test.csv')
        other=AnalyseSession.objects.create(nom='Other',statut='termine')
        r=self.client.get('/terrain/rendements/analyses-agent/',{'session':other.pk,'departement':'BUYO','agent':'AGENT1'})
        self.assertNotContains(r,'CSV1')
        self.assertEqual(self.client.get('/terrain/rendements/analyses-agent/').status_code,400)
    def test_webmap_groups_include_missing_sector_without_fabrication(self):
        from .views import couches_configurees
        with override_settings(PG_COUCHES_CARTE=['parcelle:shape_prcelle:Parcelles:#E0A800']):
            self.assertIn('secteur',[c['table'] for c in couches_configurees()])
        layers=[dict(table='secteur',libelle='Secteurs',couleur='#D97706',disponible=False,motif='absente')]
        with patch('terrain.views.couches_disponibles',return_value=(layers,'')):
            r=self.client.get('/terrain/carte/')
        self.assertContains(r,'Satellite Esri');self.assertContains(r,'OpenStreetMap');self.assertContains(r,'data-opacite');self.assertContains(r,'webmap-couches')
    def test_reference_workbook_groups_details_and_preserves_missing_fields(self):
        headers=['N°DOSSIER','DEPARTEMENT','SOUS-PREFECTURE','VILLAGE','NOM REQUERANT',"NOM DE L'OPPOSANT","QUALITE DE L'OPPOSANT",'CODE DE PARCELLE','SURFACE (ha)','SUPERFICIE LEVE',"DATE DE L'OPPOSITION","MOTIF DE L'OPPOSITION",'STATUT OPPOSITION','CE','OBSERVATION','STATUT PUBLICITE','CEPC']
        one=['380-001-0000001','BUYO','SP','VILLAGE','REQUERANT','OPPOSANT1','VOISIN','P1',2,None,None,'Motif',None,'CE TEST',None,'PUBLICITE 2',None]
        two=one.copy();two[5]='OPPOSANT2'
        class Sheet:
            def __init__(self,rows):self.values=rows
        class WB(dict):
            sheetnames=['SUIVI DES OPPOSITIONS','DETAILS OPPOSITIONS']
            def close(self):pass
        wb=WB({'SUIVI DES OPPOSITIONS':Sheet([headers,one]),'DETAILS OPPOSITIONS':Sheet([headers,one,two,two])})
        f=io.BytesIO(b'fixture');f.name='reference.xlsx'
        with patch('openpyxl.load_workbook',return_value=wb):
            result=importer_classeur(f);again=importer_classeur(f)
        self.assertEqual(result['dossiers'],1);self.assertEqual(again['actualise'],1)
        o=Opposition.objects.get();self.assertIsNone(o.date_opposition);self.assertEqual(o.statut,'non_renseigne');self.assertEqual(len(o.source_metadata['details']),2)
        self.assertTrue(o.source_metadata['numero_atypique'])
        self.assertEqual(synthese(Opposition.objects.all())['nb_ouvertes'],0)
        r=self.client.get('/terrain/oppositions/');self.assertContains(r,'OPPOSANT2');self.assertContains(r,'380-001-0000001')
    def test_lifted_opposition_is_not_assumed_amicable_or_dated(self):
        o=Opposition.objects.create(date_opposition=date(2026,1,1),statut='levee')
        self.assertTrue(o.est_close);self.assertIsNone(o.delai_jours)
        s=synthese(Opposition.objects.all());self.assertEqual(s['nb_reglees'],0);self.assertEqual(s['nb_levees'],1)
    def test_private_reference_bootstrap_runs_once_and_checks_integrity(self):
        import os,json,gzip,base64,hashlib
        from django.core.management import call_command,CommandError
        from .models import ChargementReference
        rows=[dict(source_reference='f'*64,num_demande='REFERENCE1',source='import',date_opposition=None,statut='non_renseigne',source_metadata={})]
        value=base64.b64encode(gzip.compress(json.dumps(rows).encode())).decode()
        env={'PRESFOR_OPPOSITIONS_BLOCS':'1','PRESFOR_OPPOSITIONS_0':value,'PRESFOR_OPPOSITIONS_SHA256':hashlib.sha256(value.encode()).hexdigest()}
        with patch.dict(os.environ,env):
            call_command('charger_oppositions_reference',stdout=io.StringIO())
            Opposition.objects.filter(num_demande='REFERENCE1').update(statut='instruction')
            call_command('charger_oppositions_reference',stdout=io.StringIO())
        self.assertEqual(Opposition.objects.get(num_demande='REFERENCE1').statut,'instruction')
        self.assertEqual(ChargementReference.objects.count(),1)
        env['PRESFOR_OPPOSITIONS_SHA256']='invalid'
        with patch.dict(os.environ,env),self.assertRaises(CommandError):call_command('charger_oppositions_reference',stdout=io.StringIO())
