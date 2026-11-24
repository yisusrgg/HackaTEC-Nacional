import json

from django import template

register = template.Library()


@register.filter
def json_compacto(valor, limite=300):
    """Muestra un diccionario como JSON de una línea (escapado por la plantilla)."""
    texto = json.dumps(valor, ensure_ascii=False, separators=(", ", ": "))
    return texto if len(texto) <= int(limite) else texto[: int(limite)] + "…"
