# Protocolo seguro del bus CAN propio

Es el "DBC" del sistema. Está implementado igual en `firmware/lib/protocolo/` (C++) y en
`backend/telemetria/protocolo.py` (Python).

## Capa física

- CAN 2.0A, identificadores de 11 bits, **250 kbit/s**, 8 bytes por trama.
- Controlador TWAI integrado del ESP32 más un transceptor SN65HVD230.
- Bus propio, **sin conexión eléctrica al CAN del autobús**.
- Carga del bus: unas 5 tramas/s × ~130 bits ≈ **0.3 %** de 250 kbit/s.

## Identificadores

`ID = (número de nodo << 8) | mensaje`. El nodo 1 usa los IDs `0x1MM`, el nodo 2 los `0x2MM` (hasta 7 nodos).

| Mensaje | ID nodo 1 | Contenido de los 4 bytes de datos (big-endian) | Periodo |
|---|---|---|---|
| `0x10` AC_AMBIENTE | `0x110` | [0-1] temperatura int16 ×0.1 °C · [2-3] humedad uint16 ×0.1 %HR | 2 s |
| `0x11` AC_VENTILADOR | `0x111` | [0-1] RPM uint16 | 1 s |
| `0x20` SAN_TANQUE | `0x120` | [0-1] distancia uint16 mm · [2-3] nivel uint16 ×0.1 % | 2 s |
| `0x21` SAN_ESTADO | `0x121` | [0] puerta abierta 0/1 · [1] presencia 0/1 · [2-3] usos uint16 | 1 s o al cambiar |
| `0x22` SAN_FUGA | `0x122` | [0-1] humedad del piso uint16 ×0.1 % · [2] fuga 0/1 | 5 s |
| `0x30` LUZ_ZONAS | `0x130` | [0] zona 1 % · [1] zona 2 % · [2] zona 3 encendida 0/1 | 1 s |
| `0x40` ALIMENTACION | `0x140` | [0-1] voltaje uint16 mV | 5 s |
| `0xF0` ESTADO_NODO | `0x1F0` | [0-1] minutos encendido · [2-3] bits de fallas | 5 s |

- **"Sin dato".** Cuando un sensor no responde, el nodo manda `0x7FFF` (int16), `0xFFFF` (uint16) o `0xFF` (byte).
  El servidor lo guarda con calidad `SIN_DATO` y abre una alerta de falla de sensor.
- **Bits de fallas:** 0 = DHT11 · 1 = HC-SR04 · 2 = VL53L0X · 3 = PIR calentando · 4 = error al transmitir por CAN.
- **Escala y unidades** de cada señal: tabla *Señales* del admin de Django, cargada desde `backend/telemetria/catalogo.py`.

## Formato seguro de la trama

```
 byte:   0    1    2    3  |   4    5   |   6    7
       [ datos cifrados   ] [ contador ] [  MAC   ]
                             16 bits bajos  2 bytes
```

| Paso | Cálculo |
|---|---|
| Claves por nodo | `K` = 32 bytes aleatorios (generados por Django, provisionados por el puerto serie) |
| Derivación | `K_enc = HMAC-SHA256(K, "CAN-ENC-v1")[0..15]` · `K_mac = HMAC-SHA256(K, "CAN-MAC-v1")` |
| Cifrado | `cifrado = datos XOR AES-128(K_enc, id(2) ‖ contador(4) ‖ 0×10)[0..3]` |
| Autenticación | `MAC = HMAC-SHA256(K_mac, id(2) ‖ contador(4) ‖ cifrado(4))[0..1]` |

- **Primero se cifra y luego se autentica.** El MAC cubre el ID; por eso mover una trama a otro ID la invalida.
- **El flujo de cifrado nunca se repite**, porque el par (ID, contador) es único.

## Contador de frescura (anti-repetición)

- Cada nodo tiene un contador de **32 bits que nunca se repite**. En la trama solo viajan sus 16 bits bajos.
- **Reserva en flash.** El nodo guarda en NVS "contadores reservados hasta X" cada 1000 tramas. Al reiniciar arranca
  en X, que es un múltiplo de 1000, así jamás reutiliza un contador. Además, el servidor reconoce ese salto como un
  **reinicio** y no lo cuenta como tramas perdidas.
- **Reconstrucción en el servidor** (como en AUTOSAR SecOC):
  ```
  candidato = (último & ~0xFFFF) | bajos
  nuevo     = candidato si candidato > último, si no candidato + 0x10000
  viejo     = nuevo − 0x10000
  ```
  - MAC válido con `nuevo`: trama **aceptada**.
  - MAC válido con `viejo`: **repetición (replay)**. Se rechaza, queda en la bitácora y se abre una alerta de seguridad.
  - Ninguno de los dos: **MAC inválido** (trama alterada o falsificada). Cinco o más en 60 s abren una alerta.
- **Al rotar la clave** de un nodo, el nodo y el servidor reinician el contador a 0.

## Lotes del gateway al servidor

```json
{"gw": "GW-01", "lote": 42,
 "tramas": [{"id": 272, "d": "D98F7BEA0001F8FC", "t": 1764000000123}],
 "estado": {"fw": "0.1.0", "rssi": -58, "can_rx": 1520, "can_err_rx": 0, "can_err_tx": 0,
            "can_bus_err": 0, "can_estado": "ACTIVO", "cola": 0, "descartados": 0, "heap": 182000}}
```

- **Cabeceras:** `X-Gateway: GW-01` y `X-Firma: HMAC-SHA256(clave_gateway, cuerpo)` en hexadecimal.
- **El número de lote** es único: el gateway lo reserva en flash igual que el contador del nodo.
- **Reenvío idéntico** de un lote ya recibido: el servidor responde `duplicado` y no guarda nada dos veces.
- **Mismo número con otro contenido**, o un número anterior al último: **409** y alerta de seguridad.
- **`t`** es la hora de recepción en epoch ms, con el reloj del gateway sincronizado con el servidor. 0 significa que
  aún no tiene hora, y el servidor usa su hora de recepción.

## Vector de prueba

Ambos lados deben producir exactamente esta trama. El nodo la verifica al arrancar y el backend la imprime con
`python manage.py vector_prueba`.

| Clave | ID | Contador | Datos | Trama esperada |
|---|---|---|---|---|
| `000102…1F` | `0x110` | 1 | `00EA0226` (23.4 °C, 55.0 %HR) | `D98F7BEA0001F8FC` |

## Limitaciones conocidas

- **MAC de 16 bits.** En CAN clásico solo caben 8 bytes. Un atacante tiene 1 en 65 536 de acertar por intento. Se
  compensa con la detección de ráfagas de MAC inválidos y con la alerta. **Con CAN FD** (64 bytes) se usaría un MAC
  de 8–16 bytes.
- **Pérdida masiva.** Si se pierden más de 65 536 tramas seguidas (unas 3.5 h sin gateway), el servidor no puede
  reconstruir el contador y hay que rotar la clave del nodo.
- **Doble recepción de CAN.** En casos raros, el CAN puede entregar dos veces la misma trama. El gateway descarta los
  duplicados consecutivos; si se escapara uno, el servidor lo rechazaría como repetición.
