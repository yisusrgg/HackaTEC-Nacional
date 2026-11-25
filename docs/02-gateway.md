# ESP32 #2 · Gateway (módulo de adquisición local)

## Qué hace

1. **Recibe** del bus CAN las tramas seguras del nodo. Lo hace en una tarea propia que sella la hora de cada trama
   aunque el loop esté ocupado enviando por HTTPS.
2. **Agrupa** hasta 40 tramas, o lo que llegue en 2 s, en un **lote numerado**. El número nunca se repite, ni tras un
   reinicio.
3. **Firma** cada lote con HMAC-SHA256 y su clave propia (cabeceras `X-Gateway` y `X-Firma`).
4. **Envía** el lote por **HTTPS** a `POST /api/v1/ingesta/`. Verifica el certificado del servidor contra la CA
   propia, así un intermediario no puede hacerse pasar por el servidor.
5. **Sin red, guarda** los lotes en la flash (LittleFS, unos 10 min) y los reenvía **en orden** al volver, con
   reintentos cada 1 s, 2 s, 4 s… hasta 30 s.
6. **Sincroniza su hora** con el servidor (`GET /api/v1/hora/`), sin depender de internet ni de NTP.
7. **Reporta en cada lote** el estado del bus CAN (contadores de error TX/RX, errores de bus), el Wi-Fi y la cola.
   Esto se ve en el tablero.

**Lo que no hace:** descifrar ni modificar las tramas, porque no tiene las claves de los nodos. Aunque alguien tome
el control del gateway, no puede falsificar lecturas de los sensores. El servidor verifica el MAC de cada trama con
la clave del nodo; esa es la protección **extremo a extremo** de nodo a base de datos.

## Conexiones

**Sin transceptor (configuración actual, `-DSIN_TRANSCEPTOR=1`).** Solo dos cables; el esquema completo está en
[diagrama-enlace-gateway.png](diagrama-enlace-gateway.png):

| ESP32 #2 (gateway) | Se conecta a |
|---|---|
| GPIO16 (RX) | GPIO17 del nodo, con una resistencia de 330 Ω en serie si hay |
| GND | GND del nodo |
| GPIO17 (TX) | **Nada.** El gateway solo escucha (`LISTEN_ONLY`) |

**Con transceptor SN65HVD230** (si los consiguen; se quita la línea `-DSIN_TRANSCEPTOR=1` y se vuelve a subir el
firmware): 3V3 → 3V3, GND → GND, CTX → GPIO17, CRX → GPIO16, y CANH/CANL al transceptor del nodo.

Aliméntalo por USB desde la laptop o con un cargador de celular. El LED integrado (GPIO2) indica el estado.

| LED | Significado |
|---|---|
| Parpadeo rápido | Falta configuración (Wi-Fi, servidor, id o clave) |
| Parpadeo lento | Configurado, pero sin Wi-Fi |
| Destellos | Conectado, vaciando lotes guardados |
| Fijo | Todo en orden |

## Paso a paso

### 1. Certificados HTTPS (una vez, en la laptop que será el servidor)

```
cd backend
python scripts/generar_certificados.py --ip <IP de la laptop>
```

Esto crea la CA propia y el certificado del servidor. Además escribe `firmware/include/ca_cert.h`, que contiene el
certificado **público** de la CA.

Si en el evento cambia la IP, vuelve a correrlo con la IP nueva. Reutiliza la misma CA, así que **no hay que
recompilar** el firmware.

### 2. Compilar y subir

Abre la carpeta `firmware` en VS Code con la extensión PlatformIO y ejecuta:

```
pio run -e gateway -t upload
pio device monitor
```

### 3. Provisionar por el monitor serie (115200 baudios)

```
id GW-01
ssid NombreDeTuRed
pass ContraseñaDelWifi
servidor https://<IP de la laptop>:8443
clave <64 caracteres hex>
```

- La `clave` sale de `python manage.py inicializar` o de **Tablero → Dispositivos → Rotar clave**.
- Al recibir la clave, el gateway reinicia su numeración de lotes, vacía la cola y se reinicia solo.
- La contraseña y la clave se guardan en la memoria NVS; **nunca van en el código**.

Al arrancar debes ver:

```
[WIFI] Conectado · IP 192.168.x.x · -58 dBm
[HORA] Sincronizada con el servidor.
[ENVÍO] {"estado":"procesado","lote":0,"aceptadas":9,"rechazadas":0}
```

Otros comandos: `estado` (diagnóstico completo), `reiniciar` y `borrar SI` (borra toda la configuración).

### 4. Detalles de red en el evento

- **El ESP32 solo usa Wi-Fi de 2.4 GHz.** Si usas el hotspot del celular, ponlo en 2.4 GHz o en "máxima
  compatibilidad".
- La laptop y el gateway deben estar en la **misma red**.
- **Firewall de Windows:** la primera vez que arranques `uvicorn`, permite el acceso en **redes privadas**. Si no, el
  gateway no llega al puerto 8443.

## Cómo responde el servidor y qué hace el gateway

| Respuesta | Significado | Acción del gateway |
|---|---|---|
| 200 `procesado` | Lote guardado | Lo borra de la cola |
| 200 `duplicado` | Ya lo tenía (se perdió la respuesta anterior) | Lo borra; no se duplica nada |
| 400 / 409 | Malformado o número de lote repetido | Lo descarta para no bloquear la cola |
| 401 | Firma inválida (clave incorrecta o revocada) | Lo conserva y reintenta |
| 429 / 5xx / sin red | Saturado o caído | Lo conserva y reintenta con espera creciente |

## Controles de seguridad del gateway (para la documentación del reto)

- **Autenticación de dispositivo:** cada lote va firmado con HMAC-SHA256 y la clave propia del gateway. La clave es
  revocable desde el tablero.
- **Cifrado en tránsito:** TLS. El gateway solo acepta un servidor con un certificado firmado por la CA propia.
- **Anti-repetición:** número de lote único más la huella del contenido, y además el contador de cada trama.
- **Segmentación:** el gateway nunca se conecta al CAN del autobús. El nodo de sensores no tiene radio. Sin
  transceptor, además, el enlace es físicamente de un solo sentido: el GPIO17 del gateway no está conectado, así que no
  tiene por dónde mandarle nada al nodo.
- **Gestión de credenciales:** Wi-Fi y clave en NVS, provisionadas por el puerto serie. La rotación de clave invalida
  la anterior.

**Limitaciones a declarar:**
- La NVS no está cifrada. En producción se activarían *flash encryption* y *NVS encryption* del ESP32; queman eFuses
  y son irreversibles, así que no se prueban en la placa del prototipo.
- La actualización OTA firmada queda como siguiente paso.
- Hoy cualquiera con acceso físico al USB puede reprovisionar la clave. Lo nota el servidor, porque las tramas dejan
  de verificar.
