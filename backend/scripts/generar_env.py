"""
Crea backend/.env con secretos aleatorios. Nunca subas ese archivo a git.

  python scripts/generar_env.py                                  # desarrollo con SQLite
  python scripts/generar_env.py --motor mysql --forzar           # cambiar a MySQL (crea mysql_crear_bd.sql)
  python scripts/generar_env.py --https --hosts localhost,127.0.0.1,192.168.1.50 --forzar   # demo con HTTPS

Con --forzar se reescribe la configuración pero se CONSERVAN los secretos y la base de datos elegida
(DJANGO_SECRET_KEY, CLAVE_CIFRADO_CAMPOS, ruta del admin, motor y credenciales de la BD). Si cambiara
CLAVE_CIFRADO_CAMPOS, las claves de los dispositivos guardadas ya no se podrían descifrar.
Usa --nuevos-secretos solo con una base de datos nueva.
"""
import argparse
import base64
import re
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SQL_MYSQL = BASE / "mysql_crear_bd.sql"
NOMBRE_BD = "hackatecNacional"  # nombre por defecto de la base de datos y de su usuario
NOMBRE_VALIDO = re.compile(r"^[A-Za-z0-9_]{1,32}$")
PUERTOS = {"sqlite": "", "mysql": "3306", "postgres": "5432"}
HOSTS_BD = {"sqlite": "", "mysql": "localhost", "postgres": "127.0.0.1"}


def _leer_env(ruta):
    valores = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                valores[clave.strip()] = valor.strip()
    return valores


def _sql_mysql(nombre, usuario, password):
    return f"""-- Generado por scripts/generar_env.py. Ejecútalo UNA vez como root (por ejemplo en MySQL Workbench)
-- y después BORRA este archivo: contiene la contraseña del usuario de la aplicación (la misma del .env).
CREATE DATABASE IF NOT EXISTS `{nombre}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '{usuario}'@'localhost' IDENTIFIED BY '{password}';
ALTER USER '{usuario}'@'localhost' IDENTIFIED BY '{password}';
GRANT ALL PRIVILEGES ON `{nombre}`.* TO '{usuario}'@'localhost';
-- Base de datos temporal que crea "python manage.py test":
GRANT ALL PRIVILEGES ON `test_{nombre}`.* TO '{usuario}'@'localhost';
"""


def main():
    parser = argparse.ArgumentParser(description="Genera el archivo .env del backend.")
    parser.add_argument("--hosts", default="localhost,127.0.0.1", help="Hosts permitidos; agrega la IP de la laptop servidor.")
    parser.add_argument("--https", action="store_true", help="Modo demo: DEBUG apagado y cookies solo por HTTPS.")
    parser.add_argument("--motor", choices=["sqlite", "mysql", "postgres"],
                        help="Base de datos. Si no se indica, se conserva la que ya estaba (o sqlite).")
    parser.add_argument("--bd-nombre", help=f"Nombre de la base de datos (por defecto {NOMBRE_BD}).")
    parser.add_argument("--bd-usuario", help=f"Usuario de la base de datos (por defecto {NOMBRE_BD}).")
    parser.add_argument("--sql-mysql", action="store_true", help="Volver a escribir mysql_crear_bd.sql con la contraseña actual.")
    parser.add_argument("--forzar", action="store_true", help="Reescribir un .env existente (conserva secretos y BD).")
    parser.add_argument("--nuevos-secretos", action="store_true", help="Generar secretos nuevos aunque ya existan.")
    args = parser.parse_args()

    destino = BASE / ".env"
    if destino.exists() and not args.forzar:
        print(f"Ya existe {destino}. Usa --forzar para reescribirlo (los secretos se conservan).")
        return 1

    anteriores = _leer_env(destino)
    secretos_previos = {} if args.nuevos_secretos else anteriores
    secreto = {
        "DJANGO_SECRET_KEY": secretos_previos.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50),
        "CLAVE_CIFRADO_CAMPOS": secretos_previos.get("CLAVE_CIFRADO_CAMPOS")
        or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "DJANGO_ADMIN_URL": secretos_previos.get("DJANGO_ADMIN_URL") or f"panel-{secrets.token_hex(3)}/",
    }
    conservados = [nombre for nombre in secreto if secretos_previos.get(nombre)]

    # Base de datos: si el motor no cambia se conservan nombre, usuario, host, puerto y contraseña.
    motor = args.motor or anteriores.get("DB_MOTOR") or "sqlite"
    mismo_motor = anteriores.get("DB_MOTOR", "sqlite") == motor

    def previo(clave, defecto):
        return (anteriores.get(clave) or defecto) if mismo_motor else defecto

    db = {
        "DB_NOMBRE": args.bd_nombre or previo("DB_NOMBRE", NOMBRE_BD),
        "DB_USUARIO": args.bd_usuario or previo("DB_USUARIO", NOMBRE_BD),
        "DB_HOST": previo("DB_HOST", HOSTS_BD[motor]),
        "DB_PUERTO": previo("DB_PUERTO", PUERTOS[motor]),
        "DB_PASSWORD": secretos_previos.get("DB_PASSWORD", "") if mismo_motor else "",
    }
    for campo in ("DB_NOMBRE", "DB_USUARIO"):
        if not NOMBRE_VALIDO.match(db[campo]):
            print(f"{campo} '{db[campo]}' no es válido: usa de 1 a 32 letras, números o guion bajo.")
            return 1
    nombres_cambiaron = (db["DB_NOMBRE"], db["DB_USUARIO"]) != (anteriores.get("DB_NOMBRE"), anteriores.get("DB_USUARIO"))
    password_nueva = motor != "sqlite" and not db["DB_PASSWORD"]
    if password_nueva:
        db["DB_PASSWORD"] = secrets.token_urlsafe(24)

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

# Base de datos: sqlite, mysql o postgres. SQLITE_RUTA permite guardar la BD de SQLite fuera de OneDrive.
DB_MOTOR={motor}
DB_NOMBRE={db["DB_NOMBRE"]}
DB_USUARIO={db["DB_USUARIO"]}
DB_PASSWORD={db["DB_PASSWORD"]}
DB_HOST={db["DB_HOST"]}
DB_PUERTO={db["DB_PUERTO"]}
SQLITE_RUTA={anteriores.get("SQLITE_RUTA", "")}
"""
    destino.write_text(contenido, encoding="utf-8")
    print(f"✔ {destino} {'actualizado' if anteriores else 'creado'} · BD: {motor} · modo {'HTTPS (demo)' if args.https else 'desarrollo'}")
    if conservados:
        print(f"  Secretos conservados: {', '.join(conservados)}")
    print(f"  Panel de administración de Django: /{secreto['DJANGO_ADMIN_URL']}")

    if motor == "mysql" and (password_nueva or nombres_cambiaron or args.sql_mysql):
        SQL_MYSQL.write_text(_sql_mysql(db["DB_NOMBRE"], db["DB_USUARIO"], db["DB_PASSWORD"]), encoding="utf-8")
        print(f"\n✔ {SQL_MYSQL} creado. Siguientes pasos:")
        print("  1. Ábrelo en MySQL Workbench con tu conexión de root y ejecútalo (rayo ⚡).")
        print("  2. Bórralo: contiene la contraseña del usuario de la aplicación.")
        print("  3. python manage.py migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
