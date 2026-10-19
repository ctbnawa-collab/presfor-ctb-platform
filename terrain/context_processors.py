from django.conf import settings


def identite(request):
    """Expose l'identité de l'application à tous les templates."""
    return {
        'APP_NOM': settings.APP_NOM,
        'APP_SOUS_TITRE': settings.APP_SOUS_TITRE,
        'APP_PROJET': settings.APP_PROJET,
    }
