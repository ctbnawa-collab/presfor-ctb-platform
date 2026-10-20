import io
from django.test import SimpleTestCase
from .csv_leger import compacter_export
from .processing import process_csv
from .qualite_v8 import analyser
from pandas.testing import assert_frame_equal

FIXTURE='''### TABLE: applicant
applicantNumber;firstname;lastname;numParcelleOF;photoidentity;identityDocumentPhoto
APT380-001-000001;JEAN;EXEMPLE;P1;PHOTO;SIGNATURE
### TABLE: village
codeVillage;label;codeSubPrefecture
380-001;VILLAGE TEST;380
### TABLE: subprefecture
codeSubPrefecture;label;codeDepartement
380;SOUS PREFECTURE TEST;MEAGUI
### TABLE: department
codeDepartement;label
MEAGUI;MEAGUI
### TABLE: member_type
codeMemberType;label
1;Détenteur de droit
### TABLE: minutes
minutesNumber
### TABLE: certificate_order
requestNumber;creationDate
380-001-000001;19-10-2026
'''


class CsvLegerTests(SimpleTestCase):
    def test_large_pieces_are_compacted_without_changing_quality(self):
        source=FIXTURE.replace('PHOTO','A'*1024*1024).replace('SIGNATURE','B'*1024*1024).encode()
        stream=io.BytesIO(source)
        compact=compacter_export(stream)
        self.assertLess(len(compact),2000)
        self.assertFalse(stream.closed)
        assert_frame_equal(process_csv(source.decode())[0],process_csv(compact.decode())[0])
        before,_=analyser(source);after,_=analyser(compact)
        for value in before.values():value.pop('SOURCE_FICHIER',None)
        for value in after.values():value.pop('SOURCE_FICHIER',None)
        self.assertEqual(before,after)

    def test_absent_pieces_and_na_are_preserved(self):
        for value in ('','nan','None','none','NULL','NA','  '):
            with self.subTest(value=value):
                source=FIXTURE.replace('PHOTO',value).replace('SIGNATURE',value).encode()
                before,_=analyser(source);after,_=analyser(compacter_export(source))
                for result in (before,after):
                    for row in result.values():row.pop('SOURCE_FICHIER',None)
                self.assertEqual(before,after)

    def test_multiline_fields_encoding_and_unused_tables(self):
        source=FIXTURE.replace('JEAN;EXEMPLE','"JEAN\nTEST";EXEMPLE')+'### TABLE: unused\npayload\n'+('A'*100000)+'\n'
        compact=compacter_export(source.encode('latin-1')).decode()
        self.assertIn('Détenteur',compact)
        self.assertNotIn('A'*1000,compact)
        self.assertIn('JEAN\nTEST',compact)
