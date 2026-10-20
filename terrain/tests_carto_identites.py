import io,json,zipfile
from unittest.mock import patch
import shapefile
from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import ParcellePublicite
from .shapefiles import importer_publicite
from .geo import ShapefileIllisible
from .db import lire_parcelles_carte
from .views import executer_lecture_couche


def archive(code='P01'):
    shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
    w=shapefile.Writer(shp=shp,shx=shx,dbf=dbf)
    for name in ['code_parce','obs','num_demand','sous_pref','village','code_uniqu','indice_uni','id_parcell']:
        w.field(name,'C',100)
    w.poly([[[-6.6,6],[-6.6,6.01],[-6.59,6.01],[-6.59,6],[-6.6,6]]])
    w.record(code,'NOM EXEMPLE PRENOMS','DEM-TEST','SOUS PREF TEST','VILLAGE TEST','CODE-TEST','INDICE-TEST','ID-TEST');w.close()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for ext,b in [('shp',shp),('shx',shx),('dbf',dbf)]:z.writestr('test.'+ext,b.getvalue())
        z.writestr('test.prj','GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
    out.seek(0);return out

class CartoIdentitesTests(TestCase):
    def test_replace_and_map_properties(self):
        ParcellePublicite.objects.create(code_parcelle='ANCIENNE')
        result=importer_publicite(archive(),remplacer=True)
        self.assertEqual(result['importees'],1)
        p=ParcellePublicite.objects.get()
        self.assertEqual(p.titulaire,'NOM EXEMPLE PRENOMS')
        self.assertEqual((p.code_unique,p.indice_unique),('CODE-TEST','INDICE-TEST'))
        self.assertEqual(p.sous_prefecture,'SOUS PREF TEST')
        self.client.force_login(get_user_model().objects.create_user('map-test'))
        r=self.client.get('/terrain/api/parcelles/')
        self.assertEqual(r.status_code,200)
        attrs=r.json()['features'][0]['properties']
        self.assertEqual(attrs['nom_prenom'],'NOM EXEMPLE PRENOMS')
        self.assertEqual(attrs['indice_unique'],'INDICE-TEST')
        self.assertEqual(attrs['num_demande'],'DEM-TEST')
    def test_empty_replacement_keeps_old_layer(self):
        ParcellePublicite.objects.create(code_parcelle='ANCIENNE')
        with self.assertRaises(ShapefileIllisible):importer_publicite(archive(code=''),remplacer=True)
        self.assertEqual(ParcellePublicite.objects.get().code_parcelle,'ANCIENNE')
    def test_postgres_join_is_used_even_with_requested_fields(self):
        with patch('terrain.db.lire_parcelles_carte',return_value=[{'nom':'EXEMPLE'}]) as read:
            r=executer_lecture_couche({'table':'parcelle','colonne':'shape_prcelle'},['code_parcelle'],None,20)
        read.assert_called_once_with(None,20)
        self.assertEqual(r[0]['nom'],'EXEMPLE')
    def test_spatial_query_has_bound_parameters_and_left_joins(self):
        with patch('terrain.db.executer',return_value=[]) as execute:
            lire_parcelles_carte((1,2,3,4),30)
        sql,params=execute.call_args.args
        self.assertIn('LEFT JOIN LATERAL',sql)
        self.assertIn('ST_PointOnSurface',sql)
        self.assertIn('personne',sql)
        self.assertIn('code_village',sql)
        self.assertEqual(params,{'limite':30,'x1':1,'y1':2,'x2':3,'y2':4})
    def test_replacement_shortcut_preselects_publicite(self):
        self.client.force_login(get_user_model().objects.create_user('replace-test'))
        r=self.client.get('/terrain/couches/importer/?remplacer_pub=1')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.context['form'].initial,{'couche':'publicite','remplacer':True})
