from contextlib import nullcontext
from unittest.mock import patch
from django.test import SimpleTestCase, RequestFactory
from django.template.loader import render_to_string
from terrain.forms import AgentForm
from terrain.models import Agent
from terrain.agents_registre import fusionner_agents

class AgentFormTests(SimpleTestCase):
    def donnees(self, fonction='controleur'):
        return dict(id_machine='EX01',nom='Exemple',prenoms='Test',departement='BUYO',
                    fonction=fonction,superviseur='Superviseur Exemple',controleur='Controleur Exemple',
                    objectif_journalier=10,actif='on')
    def valide(self, fonction='controleur'):
        form=AgentForm(data=self.donnees(fonction))
        with patch.object(Agent,'validate_unique'), patch.object(Agent,'validate_constraints'):
            self.assertTrue(form.is_valid(),form.errors.as_json())
        return form
    def test_roles_and_fields_available_in_creation(self):
        html=render_to_string('terrain/agent_form.html',{'form':AgentForm(),'titre':'Nouvel agent'},
            request=RequestFactory().get('/terrain/agents/nouveau/'))
        self.assertIn('name="superviseur"',html)
        self.assertIn('name="controleur"',html)
        self.assertIn('value="controleur"',html)
        self.assertIn('value="superviseur"',html)
    def test_both_roles_validate(self):
        for fonction in ('controleur','superviseur'):
            self.valide(fonction)
    def test_values_are_kept_on_unsaved_instance(self):
        form=self.valide()
        agent=form.save(commit=False)
        self.assertEqual(agent.superviseur,'Superviseur Exemple')
        self.assertEqual(agent.controleur,'Controleur Exemple')
    def test_save_updates_matching_rendement_reference(self):
        form=self.valide()
        with patch('terrain.forms.transaction.atomic',return_value=nullcontext()), patch.object(Agent,'save') as save, patch('terrain.forms.AgentRendement.objects.filter') as reference:
            agent=form.save()
            save.assert_called_once()
            reference.assert_called_once_with(departement='BUYO',code_agent__iexact='ex01')
            reference.return_value.update.assert_called_once_with(superviseur=agent.superviseur,controleur=agent.controleur)
    def test_editing_prefills_both_fields(self):
        agent=Agent(id_machine='ex01',nom='Exemple',departement='BUYO',superviseur='Equipe Exemple',controleur='Controle Exemple')
        form=AgentForm(instance=agent)
        self.assertEqual(form['superviseur'].value(),'Equipe Exemple')
        self.assertEqual(form['controleur'].value(),'Controle Exemple')
    def test_list_keeps_encadrement_for_complementary_profile(self):
        row=fusionner_agents([], [dict(id=1,id_machine='ex01',departement='BUYO',nom='Exemple',prenoms='',superviseur='Equipe Exemple',controleur='Controle Exemple')])[0]
        self.assertEqual(row['superviseur'],'Equipe Exemple')
        self.assertEqual(row['controleur'],'Controle Exemple')
