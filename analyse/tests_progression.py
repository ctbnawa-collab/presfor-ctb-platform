import json
import uuid
from django.http import HttpResponse
from django.test import TestCase, RequestFactory
from terrain.identites import charger_referentiel, resolve_agent
from terrain.models import AgentRendement
from .models import AnalyseSession
from .views import progression_analyse, une_analyse_a_la_fois, _analyse_lock


class ProgressionTests(TestCase):
    def test_referentiel_loaded_once_for_large_batch(self):
        AgentRendement.objects.create(departement='BUYO',code_agent='AGENT1',nom_agent='Agent Exemple',superviseur='Superviseur Exemple')
        with self.assertNumQueries(1):
            rows=charger_referentiel()
            for _ in range(1000):
                self.assertEqual(resolve_agent('BUYO','AGENT1',referentiel=rows)['nom_agent'],'Agent Exemple')
                self.assertEqual(resolve_agent('MEAGUI','AGENT1',referentiel=rows),{})

    def test_progress_is_scoped_to_random_token(self):
        token=str(uuid.uuid4())
        session=AnalyseSession.objects.create(nom='Test',statut='en_cours',qualite_v8={'progress_token':token,'progress':{'done':3,'total':41,'phase':'Contrôle qualité des CSV'}})
        req=RequestFactory().get('/',{'token':token})
        response=progression_analyse(req)
        self.assertEqual(json.loads(response.content)['done'],3)
        self.assertEqual(response['Cache-Control'],'no-store')
        session.statut='termine';session.save()
        self.assertIn('url',json.loads(progression_analyse(req).content))
        self.assertEqual(json.loads(progression_analyse(RequestFactory().get('/',{'token':str(uuid.uuid4())})).content),{'statut':'transfert'})
        self.assertEqual(progression_analyse(RequestFactory().get('/',{'token':'invalide'})).status_code,400)

    def test_only_one_analysis_but_navigation_is_available(self):
        view=une_analyse_a_la_fois(lambda request:HttpResponse('OK'))
        _analyse_lock.acquire()
        try:
            self.assertEqual(view(RequestFactory().post('/')).status_code,429)
            self.assertEqual(view(RequestFactory().get('/')).status_code,200)
        finally:_analyse_lock.release()
        self.assertEqual(view(RequestFactory().post('/')).status_code,200)

    def test_lock_released_after_exception(self):
        def fail(request):raise ValueError('test')
        with self.assertRaises(ValueError):une_analyse_a_la_fois(fail)(RequestFactory().post('/'))
        self.assertTrue(_analyse_lock.acquire(blocking=False))
        _analyse_lock.release()

    def test_csv_files_are_read_one_at_a_time(self):
        import io
        import pandas as pd
        from unittest.mock import patch
        from .processing import run_analyse
        files=[(str(i)+'.csv',io.BytesIO(b'CSV TEST')) for i in range(41)]
        events=[]
        def progress(done,total,phase):
            events.append(done)
            for _,handle in files[done:]:self.assertEqual(handle.tell(),0)
        with patch('analyse.processing.process_csv',return_value=(pd.DataFrame(),None,None,{})),patch('analyse.processing.analyser_v8',return_value=({},'')):
            with self.assertRaises(ValueError):
                run_analyse(files,source_parcelles='postgis',progression=progress)
        self.assertEqual(events,list(range(41)))
        self.assertTrue(all(handle.tell()>0 for _,handle in files))
