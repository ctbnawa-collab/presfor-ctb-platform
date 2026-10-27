from datetime import date
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

class RendementAffichageTests(SimpleTestCase):
    def test_classement_prime_et_lien_agent(self):
        semaine=dict(debut='2026-10-03',fin='2026-10-09',echeance=date(2026,10,16),objectif=150,agents_primes=1,effectif=1,eligible=True,statut='Éligible')
        agent=dict(d='BUYO',s='Superviseur Exemple',a='Agent Exemple',code_agent='EX01',ha=150,nb_parcelles=2,n=3,pt=3,rt=3,pr=3)
        equipe=dict(d='BUYO',s='Superviseur Exemple',effectif=1,nb_parcelles=2,n=3,ha=150,pt=3,rt=3,pr=3,ratio=100,primes=[semaine],meilleure_performance=True,detail_agents=[agent])
        contexte=dict(titre='Rendement hebdomadaire',mode='hebdomadaire',equipes=[equipe],session={'pk':42},
            date_debut=date(2026,10,3),date_fin=date(2026,10,9),periode='S1',sessions=[],options=[],superviseurs=[],
            total={'ha':150,'n':3,'equipes':1,'agents':1,'pr':3},graph={'noms':[],'ha':[],'n':[],'jours':[],'jour_ha':[],'jour_n':[]},
            graph_height=420,cfg={'obj':{'BUYO':150,'SOUBRE':150,'MEAGUI':300},'seuil':100})
        html=render_to_string('terrain/performance_native.html',contexte,request=RequestFactory().get('/terrain/rendements/'))
        self.assertIn('BUYO',html)
        self.assertIn('Équipe Superviseur Exemple',html)
        self.assertIn('Meilleure superficie du département',html)
        self.assertIn('Prime hebdomadaire',html)
        self.assertIn('Éligible',html)
        self.assertIn('Agent Exemple',html)
        self.assertIn('session=42&amp;departement=BUYO&amp;agent=EX01&amp;debut=2026-10-03&amp;fin=2026-10-09',html)
        self.assertIn('2 parcelle(s)',html)
