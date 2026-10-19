from django.conf import settings
from django.contrib.auth.views import redirect_to_login

class AccesPlateforme:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if settings.PRESFOR_AUTH_REQUIRED and not request.user.is_authenticated and not request.path.startswith(('/connexion/','/static/')):
            return redirect_to_login(request.get_full_path(),settings.LOGIN_URL)
        return self.get_response(request)
