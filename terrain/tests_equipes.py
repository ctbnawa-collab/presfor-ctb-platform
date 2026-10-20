from datetime import date
from django.test import SimpleTestCase
from .equipes import primes_equipes, regrouper_equipes


class EquipesTests(SimpleTestCase):
    cfg = {'obj': {'BUYO': 150, 'SOUBRE': 150, 'MEAGUI': 300}, 'seuil': 100}
    weeks = [{'k': 'S1', 's': '2026-10-05', 'e': '2026-10-11'},
             {'k': 'S2', 's': '2026-10-12', 'e': '2026-10-18'}]

    def row(self, dep='BUYO', agent='A', ha=150, p='t', r='t'):
        return dict(w='S1', d=dep, s='Superviseur Exemple', a=agent, ha=ha, p=p, r=r)

    def test_same_supervisor_separate_departments(self):
        rows = [self.row(), self.row('MEAGUI')]
        primes = primes_equipes(rows, self.cfg, self.weeks)
        teams = regrouper_equipes(rows, primes)
        self.assertEqual(len(teams), 2)
        self.assertEqual(sum(t['ha'] for t in teams), 300)
        self.assertTrue(primes[('S1', 'BUYO', 'Superviseur Exemple')]['eligible'])
        self.assertFalse(primes[('S1', 'MEAGUI', 'Superviseur Exemple')]['eligible'])

    def test_one_unfinished_agent_blocks_supervisor_at_full_threshold(self):
        rows = [self.row(), self.row(agent='B', r='e')]
        prime = primes_equipes(rows, self.cfg, self.weeks, date(2026, 10, 18))[('S1', 'BUYO', 'Superviseur Exemple')]
        self.assertEqual(prime['statut'], 'En attente')
        self.assertEqual(prime['agents_primes'], 1)
        self.assertEqual(prime['objectif'], 300)
        self.assertEqual(primes_equipes(rows, self.cfg, self.weeks, date(2026, 10, 19))[('S1', 'BUYO', 'Superviseur Exemple')]['statut'], 'Non éligible')

    def test_daily_selection_keeps_full_week_prime(self):
        rows = [self.row(ha=75), self.row(ha=75)]
        primes = primes_equipes(rows, self.cfg, self.weeks)
        team = regrouper_equipes(rows[:1], primes)[0]
        self.assertEqual(team['ha'], 75)
        self.assertTrue(team['primes'][0]['eligible'])
        self.assertEqual(team['pr'], 1)

    def test_threshold_is_configurable(self):
        rows = [self.row(), self.row(agent='B', ha=0, p='c', r='c')]
        cfg = dict(self.cfg, seuil=50)
        self.assertTrue(primes_equipes(rows, cfg, self.weeks)[('S1', 'BUYO', 'Superviseur Exemple')]['eligible'])
        self.assertEqual(regrouper_equipes([], {}), [])
