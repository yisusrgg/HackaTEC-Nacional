def ip_de(request):
    """IP del cliente. No se confía en X-Forwarded-For porque el servidor no está detrás de un proxy."""
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None
