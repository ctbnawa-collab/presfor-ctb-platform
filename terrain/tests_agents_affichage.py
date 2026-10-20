from django.test import SimpleTestCase
from django.template.loader import render_to_string
from terrain.agents_registre import fusionner_agents

class AffichageAgentsTests(SimpleTestCase):
    def test_reference_names_are_rendered_without_profile(self):
        rows=fusionner_agents([dict(departement='BUYO',code_agent='EX01',nom_agent='Nom Exemple Complet',superviseur='Superviseur Exemple',controleur='')],[])
        html=render_to_string('terrain/agents.html',dict(agents=rows,nb_reference=1,total=1,nb_actifs=0,par_dept=[],departements=[],fonctions=[]))
        self.assertIn('Nom Exemple Complet',html)
        self.assertIn('EX01',html)
        self.assertIn('Superviseur Exemple',html)
        self.assertIn('Non renseigné',html)
    def test_analysis_link_uses_department_and_code(self):
        rows=fusionner_agents([dict(departement='BUYO',code_agent='EX01',nom_agent='Nom Exemple Complet',superviseur='Superviseur Exemple',controleur='')],[])
        html=render_to_string('terrain/agents.html',dict(agents=rows,derniere_analyse={'pk':42},nb_reference=1,total=1,nb_actifs=0,par_dept=[],departements=[],fonctions=[]))
        self.assertIn('session=42&amp;departement=BUYO&amp;agent=EX01',html)
