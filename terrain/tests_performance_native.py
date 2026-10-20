from datetime import date
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from analyse.models import AnalyseSession, Demande
from .models import AgentRendement
from .performance import rows_for


class PerformanceNativeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='native-test',password='test'))
        self.session=AnalyseSession.objects.create(nom='Analyse de test',statut='termine')
        AgentRendement.objects.create(departement='BUYO',code_agent='AGENT1',nom_agent='Agent Exemple',superviseur='Superviseur Exemple')
        Demande.objects.bulk_create([Demande(session=self.session,num_demande='N'+str(i),departement='BUYO',code_agent='AGENT1',nom_agent='Agent Exemple',d_creation=date(2026,10,19),source_qualite='v8',surface_postgres=2.5,statut_pvcl='Pvcl terminé',statut_rdc='RDC terminé') for i in range(1000)])

    def test_identity_and_config_do_not_query_per_demand(self):
        with CaptureQueriesContext(connection) as queries:
            rows,issues,_=rows_for(self.session.pk)
        self.assertEqual(len(rows),1000)
        self.assertFalse(issues)
        self.assertLessEqual(len(queries),10)
        self.assertEqual(sum(r['ha'] for r in rows),2500)

    def test_all_periods_use_native_view_and_common_totals(self):
        for url in ['/terrain/rendements/?periode=2026-10-19_2026-10-25','/terrain/rendements/journalier/?periode=2026-10-19','/terrain/rendements/mensuel/?periode=2026-10']:
            with self.subTest(url=url):
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertTemplateUsed(response,'terrain/performance_native.html')
                self.assertNotContains(response,'<iframe')
                self.assertEqual(response.context['total']['ha'],2500)
                self.assertEqual(response.context['equipes'][0]['pr'],1000)
                self.assertTrue(response.context['equipes'][0]['primes'][0]['eligible'])

    def test_filters_and_csv_exports_use_same_teams(self):
        response=self.client.get('/terrain/rendements/',{'periode':'2026-10-19_2026-10-25','departement':'BUYO','superviseur':'Superviseur Exemple','export':'csv'})
        self.assertContains(response,'Superviseur Exemple')
        response=self.client.get('/terrain/rendements/',{'departement':'MEAGUI'})
        self.assertEqual(response.context['total']['n'],0)
        self.assertEqual(response.context['equipes'],[])
