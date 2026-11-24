from django.conf import settings


class CabecerasSeguridadMiddleware:
    """
    Política de seguridad de contenido (CSP): el navegador solo ejecuta scripts servidos por este servidor.
    Por eso no hay <script> en línea en las plantillas ni librerías desde CDN.
    """

    CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.prefijo_admin = "/" + settings.ADMIN_URL.lstrip("/")

    def __call__(self, request):
        respuesta = self.get_response(request)
        if not request.path.startswith(self.prefijo_admin):  # el admin de Django usa estilos en línea
            respuesta.headers.setdefault("Content-Security-Policy", self.CSP)
        respuesta.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return respuesta
