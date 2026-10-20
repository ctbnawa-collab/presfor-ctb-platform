import unittest
from terrain.agents_registre import fusionner_agents

class RegistreTests(unittest.TestCase):
    def ref(self, dep='BUYO'):
        return dict(departement=dep,code_agent='A01',nom_agent='Agent Exemple',superviseur='Superviseur Exemple',controleur='')
    def fiche(self, dep='BUYO', **kwargs):
        return dict(id=7,departement=dep,id_machine='a01',nom='Ancien',prenoms='Nom',telephone='0123',actif=True,**kwargs)
    def test_reference_without_profile_is_visible(self):
        row=fusionner_agents([self.ref()],[])[0]
        self.assertEqual(row['nom_complet'],'Agent Exemple')
        self.assertIsNone(row['pk'])
        self.assertIsNone(row['actif'])
    def test_matching_profile_keeps_contact_and_identity(self):
        fiche=self.fiche()
        row=fusionner_agents([self.ref()],[fiche])[0]
        self.assertEqual((row['pk'],row['telephone'],row['nom_complet']),(7,'0123','Agent Exemple'))
        self.assertEqual(fiche['nom'],'Ancien')
    def test_codes_from_other_departments_are_separate(self):
        rows=fusionner_agents([self.ref()],[self.fiche('SOUBRE')])
        self.assertEqual(len(rows),2)
        self.assertIsNone(next(r for r in rows if r['referentiel'])['pk'])
    def test_complementary_profile_is_preserved(self):
        row=fusionner_agents([],[self.fiche()])[0]
        self.assertEqual(row['nom_complet'],'Ancien Nom')
        self.assertFalse(row['referentiel'])
    def test_ambiguous_profiles_are_not_attached(self):
        other=self.fiche();other['id']=8
        rows=fusionner_agents([self.ref()],[self.fiche(),other])
        self.assertEqual(len(rows),3)
        self.assertIsNone(next(r for r in rows if r['referentiel'])['pk'])
