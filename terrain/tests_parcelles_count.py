import unittest
from terrain.parcelles_count import compter_parcelles

class ComptageParcellesTests(unittest.TestCase):
    def test_two_requests_for_one_postgres_parcel_count_once(self):
        self.assertEqual(compter_parcelles([{'n':'D1','id_parcelle':'ID1'},{'n':'D2','id_parcelle':'ID1'},{'n':'D3','id_parcelle':'ID2'}]),2)
    def test_same_code_in_different_villages_is_not_merged(self):
        self.assertEqual(compter_parcelles([{'d':'BUYO','village':'A','code_parcelle':'P1'},{'d':'BUYO','village':'B','code_parcelle':'P1'},{'d':'BUYO','village':'A','code_parcelle':'P1'}]),2)
    def test_missing_parcel_is_not_replaced_by_request_number(self):
        self.assertEqual(compter_parcelles([{'n':'D1'},{'n':'D2','code_parcelle':'P1'}]),0)
    def test_postgres_id_takes_precedence_over_display_code(self):
        self.assertEqual(compter_parcelles([{'id_parcelle':'ID1','code_parcelle':'P1'},{'id_parcelle':'ID1','code_parcelle':'P2'}]),1)

    def test_missing_id_does_not_duplicate_an_identified_parcel(self):
        self.assertEqual(compter_parcelles([{'d':'BUYO','village':'A','code_parcelle':'P1','id_parcelle':'ID1'},{'d':'BUYO','village':'A','code_parcelle':'P1'}]),1)
