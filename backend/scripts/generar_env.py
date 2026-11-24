"""
Crea backend/.env con secretos aleatorios. Nunca subas ese archivo a git.

  python scripts/generar_env.py                                   # desarrollo (runserver, sin HTTPS)
  python scripts/generar_env.py --https --hosts localhost,127.0.0.1,192.168.1.50 --forzar   # demo con HTTPS

Con --forzar se reescribe la configuración pero se CONSERVAN los secretos que ya existían (DJANGO_SECRET_KEY,
CLAVE_CIFRADO_CAMPOS y la ruta del admin): si cambiara CLAVE_CIFRADO_CAMPOS, las claves de los dispositivos
guardadas en la base de datos ya no se podrían descifrar. Usa --nuevos-secretos solo con una base de datos nueva.
"""
import argparse
import base64
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SECRETOS = ("DJANGO_SECRET_KEY", "CLAVE_CIFRADO_CAMPOS", "DJANGO_ADMIN_URL")


def _leer_env(ruta):
    valores = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                valores[clave.strip()] = valor.strip()
    return valores


def main():
    parser = argparse.ArgumentParser(description="Genera el archivo .env del backend.")
    parser.add_argument("--hosts", default="localhost,127.0.0.1", help="Hosts permitidos; agrega la IP de la laptop servidor.")
    parser.add_argument("--https", action="store_true", help="Modo demo: DEBUG apagado y cookies solo por HTTPS.")
    parser.add_argument("--forzar", action="store_true", help="Reescribir un .env existente (conserva los secretos).")
    parser.add_argument("--nuevos-secretos", action="store_true", help="Generar secretos nuevos aunque ya existan.")
    args = parser.parse_args()

    destino = BASE / ".env"
    if destino.exists() and not args.forzar:
        print(f"Ya existe {destino}. Usa --forzar para reescribirlo (los secretos se conservan).")
        return 1

    anteriores = {} if args.nuevos_secretos else _leer_env(destino)
    secreto = {
        "DJANGO_SECRET_KEY": anteriores.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50),
        "CLAVE_CIFRADO_CAMPOS": anteriores.get("CLAVE_CIFRADO_CAMPOS")
        or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "DJANGO_ADMIN_URL": anteriores.get("DJANGO_ADMIN_URL") or f"panel-{secrets.token_hex(3)}/",
    }
    conservados = [nombre for nombre in SECRETOS if anteriores.get(nombre)]

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    origenes = ",".join(f"https://{h}:8443" for h in hosts) if args.https else ""
    contenido = f"""# Generado por scripts/generar_env.py — NO subir a git.
DJANGO_SECRET_KEY={secreto["DJANGO_SECRET_KEY"]}
DJANGO_DEBUG={0 if args.https else 1}
DJANGO_ALLOWED_HOSTS={",".join(hosts)}
DJANGO_HTTPS={1 if args.https else 0}
DJANGO_HSTS_SEGUNDOS=0
DJANGO_CSRF_ORIGENES={origenes}
DJANGO_ADMIN_URL={secreto["DJANGO_ADMIN_URL"]}

# Cifra en la base de datos las claves de los gateways y nodos. Si se pierde, hay que rotar todas las claves.
CLAVE_CIFRADO_CAMPOS={secreto["CLAVE_CIFRADO_CAMPOS"]}

# sqlite (por defecto) o postgres. SQLITE_RUTA permite guardar la BD fuera de OneDrive.
DB_MOTOR=sqlite
SQLITE_RUTA={anteriores.get("SQLITE_RUTA", "")}
DB_NOMBRE=telemetria
DB_USUARIO=telemetria
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PUERTO=5432
"""
    destino.write_text(contenido, encoding="utf-8")
    print(f"✔ {destino} {'actualizado' if conservados else 'creado'} · modo {'HTTPS (demo)' if args.https else 'desarrollo'}")
    if conservados:
        print(f"  Secretos conservados: {', '.join(conservados)}")
    print(f"  Panel de administración de Django: /{secreto['DJANGO_ADMIN_URL']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
