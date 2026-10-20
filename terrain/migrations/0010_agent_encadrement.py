from django.db import migrations, models

def reprendre_encadrement(apps, schema_editor):
    Agent = apps.get_model('terrain', 'Agent')
    Reference = apps.get_model('terrain', 'AgentRendement')
    alias = schema_editor.connection.alias
    for ref in Reference.objects.using(alias).all().iterator():
        Agent.objects.using(alias).filter(
            departement=ref.departement, id_machine__iexact=ref.code_agent
        ).update(superviseur=ref.superviseur, controleur=ref.controleur)

class Migration(migrations.Migration):
    dependencies = [('terrain', '0009_chargementreference')]
    operations = [
        migrations.AddField(model_name='agent', name='superviseur',
            field=models.CharField('Superviseur', max_length=180, blank=True)),
        migrations.AddField(model_name='agent', name='controleur',
            field=models.CharField('Contrôleur', max_length=120, blank=True)),
        migrations.AlterField(model_name='agent', name='fonction',
            field=models.CharField('Fonction', max_length=20, default='topographe', choices=[
                ('topographe', 'Opérateur topographe'), ('enqueteur', 'Commissaire enquêteur'),
                ('animateur', 'Animateur / sensibilisation'), ('opposition', 'Agent opposition / litige'),
                ('superviseur', 'Superviseur'), ('controleur', 'Contrôleur'),
                ('saisie', 'Agent de saisie'), ('autre', 'Autre')])),
        migrations.RunPython(reprendre_encadrement, migrations.RunPython.noop),
    ]
