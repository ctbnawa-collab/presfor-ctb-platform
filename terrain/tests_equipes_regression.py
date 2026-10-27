import unittest
from datetime import date
from terrain.equipes import primes_equipes, regrouper_equipes

class EquipeCalculsTests(unittest.TestCase):
    cfg={'obj':{'BUYO':150,'SOUBRE':150,'MEAGUI':300},'seuil':100}
    weeks=[{'k':'S1','s':'2026-10-03','e':'2026-10-09'},{'k':'S2','s':'2026-10-10','e':'2026-10-16'}]
    def row(self,dep='BUYO',sup='S1',agent='X',ha=150,p='t',r='t',ident='P1'):
        return dict(d=dep,s=sup,a=agent,code_agent=agent,ha=ha,p=p,r=r,w='S1',id_parcelle=ident)
    def teams(self,rows):
        return regrouper_equipes(rows,primes_equipes(rows,self.cfg,self.weeks,date(2026,10,12)))
    def test_meilleure_performance_par_departement_et_egalites(self):
        rows=[self.row(sup='A',ha=160),self.row(sup='B',ha=160),self.row(sup='C',ha=100),
              self.row(dep='SOUBRE',sup='D',ha=50)]
        teams=self.teams(rows)
        self.assertEqual({(t['d'],t['s']) for t in teams if t['meilleure_performance']},{('BUYO','A'),('BUYO','B'),('SOUBRE','D')})
    def test_zero_ne_designe_pas_de_meilleur(self):
        self.assertFalse(self.teams([self.row(ha=0)])[0]['meilleure_performance'])
    def test_identite_et_parcelles_distinctes(self):
        rows=[self.row(agent='A',ha=75,ident='P1'),self.row(agent='A',ha=75,ident='P1'),self.row(agent='B',ha=150,ident='P2')]
        team=self.teams(rows)[0]
        self.assertEqual((team['effectif'],team['n'],team['nb_parcelles']),(2,3,2))
        self.assertTrue(team['primes'][0]['eligible'])
        self.assertEqual(team['primes'][0]['objectif'],300)
    def test_prime_exige_les_dossiers_termines(self):
        p=primes_equipes([self.row(r='e')],self.cfg,self.weeks,date(2026,10,12))[('S1','BUYO','S1')]
        self.assertFalse(p['eligible'])
        self.assertEqual(p['statut'],'En attente')
    def test_prime_complete_conservee_sur_selection_partielle(self):
        rows=[self.row(ha=75),self.row(ha=75)]
        primes=primes_equipes(rows,self.cfg,self.weeks,date(2026,10,12))
        team=regrouper_equipes(rows[:1],primes)[0]
        self.assertEqual(team['ha'],75)
        self.assertTrue(team['primes'][0]['eligible'])

if __name__=='__main__': unittest.main()
