# Sistema ciberseguro de detección y transmisión telemática para autobuses

HackaTec 2026 · Reto Volvo Buses México. Prototipo que captura variables de la carrocería (aire acondicionado,
sanitario, iluminación y sistema eléctrico), las transmite de forma segura fuera del vehículo y las muestra en un
tablero con tendencias y alertas.

```
 Sensores ──► ESP32 #1 · nodo (sin Wi-Fi)
                 │  bus CAN propio, sin conexión al CAN del autobús
                 │  cada trama: datos cifrados (AES) + contador anti-repetición + MAC
                 ▼
              ESP32 #2 · gateway ── lotes numerados y firmados (HMAC) ── HTTPS con CA propia ──┐
                 (guarda en flash si no hay red)                                               │
                                                                                               ▼
                              Django + DRF: verifica firma, contador y MAC · descifra · clasifica
                                │   base de datos local (claves de dispositivos cifradas)
                                ▼
                              Tablero (plantillas Django + Tailwind + Chart.js) con roles y bitácora
```

## Estructura

| Carpeta | Contenido |
|---|---|
| [docs/01-conexiones-nodo-sensores.md](docs/01-conexiones-nodo-sensores.md) | Dónde y cómo conectar cada sensor al ESP32 #1, con [diagrama](docs/diagrama-nodo-sensores.svg) |
| [docs/02-gateway.md](docs/02-gateway.md) | Cómo armar, provisionar y probar el ESP32 #2 |
| [docs/03-protocolo-can.md](docs/03-protocolo-can.md) | Formato de las tramas, criptografía y anti-repetición (el "DBC") |
| `firmware/` | PlatformIO: `nodo_sensores` y `gateway`, con el protocolo compartido en `lib/protocolo` |
| `backend/` | Django + DRF: apps `telemetria` (ingesta y modelos), `tablero` (web y roles), `auditoria` (bitácora) |

## Arranque rápido (Windows · PowerShell)

> OneDrive sincroniza `backend/.venv` y `backend/node_modules` (miles de archivos). Si pueden, saquen el
> proyecto de OneDrive.

### 1. Backend (Python 3.11 o superior y MySQL 8; probado con Python 3.14)

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/generar_env.py --motor mysql
```

Ese comando crea el `.env` y el archivo `mysql_crear_bd.sql`. Ese SQL crea la base `hackatecNacional` (utf8mb4) y
un usuario con el mismo nombre y contraseña aleatoria, la misma que quedó en el `.env`. Para otros nombres usen
`--bd-nombre` y `--bd-usuario`.

1. Abre `mysql_crear_bd.sql` en **MySQL Workbench** con tu conexión de root y ejecútalo (botón del rayo).
2. **Bórralo**, porque contiene la contraseña.
3. Termina con:

```
python manage.py migrate
python manage.py inicializar --usuarios-demo
```

- **La base se elige con `DB_MOTOR`** en el `.env` (`mysql`, `sqlite` o `postgres`). La conexión se arma en
  `config/settings.py`. Para trabajar sin MySQL, `python scripts/generar_env.py --motor sqlite --forzar`.
- **`requirements.txt`** fija las versiones exactas con las que pasan las pruebas, así todo el equipo instala lo
  mismo.
- **Si PowerShell no deja ejecutar `activate`** ("la ejecución de scripts está deshabilitada"), no hace falta activar:
  usen `.venv\Scripts\python.exe` en lugar de `python`. Por ejemplo, `.venv\Scripts\python.exe manage.py migrate`.

`inicializar` crea los roles, el catálogo de señales, el autobús, gateway y nodo de demostración, y tres usuarios
(uno por rol). Las contraseñas y las claves de provisión **se muestran una sola vez**.

### 2. Frontend: Tailwind y Chart.js se sirven desde Django, sin CDN

```
npm install
npm run build
```

Mientras editan plantillas, usen `npm run css:watch`.

### 3. Probar sin hardware

```
python manage.py runserver
python manage.py simular --url http://127.0.0.1:8000
```

Con el simulador corriendo, entren a `http://127.0.0.1:8000` y prueben fallas y ataques:

```
python manage.py simular --url http://127.0.0.1:8000 --falla ac
python manage.py simular --url http://127.0.0.1:8000 --ataque replay
```

- `--falla` acepta `ac`, `tanque`, `fuga` o `sensor`.
- `--ataque` acepta `replay`, `mac`, `firma` o `lote`. El ataque se lanza en el cuarto lote y luego se ve en
  **Alertas** y **Seguridad**.

### 4. Demo real con HTTPS y los dos ESP32

```
python scripts/generar_certificados.py --ip <IP de la laptop>
python scripts/generar_env.py --https --hosts localhost,127.0.0.1,<IP de la laptop> --forzar
uvicorn config.asgi:application --host 0.0.0.0 --port 8443 --ssl-keyfile certs/servidor.key --ssl-certfile certs/servidor.crt
```

1. Importen `backend/certs/ca.crt` como CA raíz de confianza para que el navegador no muestre advertencias.
2. Compilen y suban el firmware (ver [docs/02-gateway.md](docs/02-gateway.md)) y provisionen las claves por el
   monitor serie.

## Roles

| Rol | Puede |
|---|---|
| Administrador | Todo: dispositivos (rotar claves, revocar), bitácora de seguridad, umbrales y usuarios en el admin |
| Ingeniería | Ver el tablero y exportar CSV |
| Técnico | Ver el tablero y atender o resolver alertas |
| Gateway | No es un usuario: se autentica con su firma HMAC y solo puede enviar lotes |

## Cómo se cubren los requisitos de seguridad de Volvo

| Control obligatorio | Implementación |
|---|---|
| Autenticación de dispositivos | MAC por trama con la clave del nodo, más HMAC por lote con la clave del gateway; ambas revocables |
| Cifrado en tránsito | AES por trama desde el nodo, más HTTPS con certificado verificado contra la CA propia |
| Cifrado en reposo | Claves de dispositivos cifradas (Fernet) en la BD, más BitLocker en la laptop servidor |
| Control de acceso | Roles con mínimo privilegio; los accesos denegados quedan registrados |
| Gestión de credenciales | Secretos en `.env` y en la NVS del ESP32, nunca en el código. Claves mostradas una vez y rotables. Argon2 para contraseñas. Bloqueo tras 5 intentos de login |
| Registro de eventos | Bitácora de solo agregar con hash encadenado SHA-256; el tablero verifica que nadie la alteró |
| Actualización segura | Siguiente paso: OTA firmada (ver pendientes) |
| Segmentación | Bus CAN propio sin conexión al vehículo; el nodo no tiene radio; el gateway nunca escribe en ningún bus del vehículo |

La protección **extremo a extremo** se ve en el MAC: lo calcula el nodo y lo verifica el servidor, así que ni el
gateway ni la red pueden alterar una lectura sin que se detecte.

Para la **confiabilidad**:
- Lotes idempotentes y almacenamiento en flash sin red.
- Porcentaje de pérdida por nodo, calculado con el contador de cada trama.
- Detección de sensores sin dato o fuera de rango.
- Watchdog en ambos ESP32.

## Pruebas

```
cd backend
python manage.py test
```

Con MySQL, Django crea y borra una base temporal `test_hackatecNacional`; el script `mysql_crear_bd.sql` ya le da
permiso al usuario para eso.

Son 56 pruebas: protocolo, ingesta, ataques (replay, MAC alterado, firma falsa, lote manipulado), bitácora
encadenada, permisos por rol y bloqueo de login.

## Pendientes sugeridos

- [ ] OTA firmada para el gateway: el admin sube el firmware y su firma ECDSA, y el ESP32 la verifica antes de
      instalar.
- [ ] Documentos del reto: modelo de amenazas (STRIDE), controles implementados, limitaciones y plan de validación.
- [ ] Prueba de no interferencia: captura de errores CAN = 0 y carga del bus en el tablero durante la demo.
- [ ] Maqueta: ventilador de 12 V como A/C, tanque transparente, puerta con el VL53L0X y tiras LED como luces.
