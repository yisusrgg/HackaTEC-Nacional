# Nodo 1 · ESP32 de sensores: dónde y cómo conectar

![Diagrama de conexiones del nodo 1](diagrama-nodo-sensores.svg)

El nodo lee los sensores y transmite por un **bus CAN propio** hacia el gateway. No tiene Wi-Fi ni Bluetooth, y
**no se conecta al CAN del autobús**. Eso cumple la segmentación y la no interferencia que pide Volvo.

## Materiales

- ESP32 DevKit V1 de 30 pines y su placa de expansión.
- Transceptor CAN **SN65HVD230** (3.3 V).
- Sensores: DHT11, HW-201, HC-SR04, VL53L0X, HC-SR501, LDR suelta, módulo LDR analógico, módulo LDR digital y
  sensor de humedad de suelo.
- Resistencias: 1 kΩ, 2 kΩ (o dos de 1 kΩ en serie), 10 kΩ, 100 kΩ y 33 kΩ.
- Para el bus CAN: un par trenzado. Sirve un par de un cable de red UTP.

## Antes de conectar nada: la placa de expansión

1. **Jumper de la fila V en 3.3 V.** En muchas placas un solo jumper cambia la fila V de todos los pines. Si queda
   en 5 V, las salidas analógicas pueden pasar de 3.3 V y dañar la ESP32.
2. **El HC-SR04 y el PIR van a 5 V**, desde el pin VIN o el header de 5 V de la placa.
3. **Todas las tierras unidas**: sensores, ESP32 y la GND del gateway.
4. **Alimentación**: por USB o con 7–9 V en el conector. Con 12 V, el regulador lineal se calienta mucho.

## Tabla de conexiones

| Sensor | Pin del sensor → pin de la ESP32 | Alimentación | Notas |
|---|---|---|---|
| **DHT11** (A/C) | DATA → **GPIO4** | 3.3 V | El módulo de 3 pines ya trae la resistencia pull-up |
| **HW-201** (RPM del ventilador) | OUT → **GPIO27** | 3.3 V | A 1–3 cm de las aspas con una marca blanca; gira el potenciómetro hasta que el LED parpadee con cada vuelta |
| **HC-SR04** (nivel del tanque) | TRIG → **GPIO26** · ECHO → divisor → **GPIO25** | **5 V** | ECHO → 1 kΩ → GPIO25, y GPIO25 → 2 kΩ → GND |
| **VL53L0X** (puerta del sanitario) | SDA → **GPIO21** · SCL → **GPIO22** | 3.3 V | XSHUT y GPIO1 del módulo sin conectar |
| **HC-SR501** (presencia) | OUT → **GPIO14** | **5 V** | Su salida ya es de 3.3 V. Jumper en **H** (redisparo) y tiempo al mínimo |
| **LDR suelta** (luz zona 1) | una pata a 3.3 V, la otra a **GPIO36 (VP)** | 3.3 V | Además, 10 kΩ de GPIO36 a GND |
| **Módulo LDR analógico** (zona 2) | AO → **GPIO39 (VN)** | 3.3 V | Si al tapar el sensor sube la "luz", cambia `ZONA2_INVERTIDA` |
| **Módulo LDR digital** (zona 3) | DO → **GPIO13** | 3.3 V | Ajusta su potenciómetro entre "luz encendida" y "apagada" |
| **Humedad de suelo** (fuga en el piso) | AO → **GPIO35** · VCC → **GPIO32** | desde GPIO32 | Se energiza solo al medir para que las pistas no se corroan |
| **Voltaje de alimentación** | VIN → 100 kΩ → **GPIO34** → 33 kΩ → GND | — | Mide la línea de 5 V (aguanta hasta ~12 V) |
| **SN65HVD230** (bus CAN) | CTX → **GPIO17** · CRX → **GPIO16** | 3.3 V | CANH y CANL van al bus |

**No conectes nada a:**
- GPIO12: si está en alto al encender, la ESP32 no arranca.
- GPIO1 y GPIO3: son del USB serie.
- Pines ADC2 para analógico: no funcionan con Wi-Fi. Este nodo no usa Wi-Fi, pero así el cableado sirve si algún día cambia.

Pines libres para crecer: GPIO33, GPIO23, GPIO19, GPIO18, GPIO5 y GPIO15.

## Bus CAN hacia el gateway

```
 ESP32 #1                 SN65HVD230 (nodo)            SN65HVD230 (gateway)          ESP32 #2
 GPIO17 ──────── CTX                CANH ═══════════════════ CANH           CTX ──────── GPIO17
 GPIO16 ──────── CRX                CANL ═══════════════════ CANL           CRX ──────── GPIO16
 3V3 ─────────── 3V3      (par trenzado, 120 Ω en cada extremo)             3V3 ──────── 3V3
 GND ─────────── GND ──────────────────── GND común ──────────────────────── GND ──────── GND
```

- **Terminación.** Casi todas las plaquitas SN65HVD230 traen una resistencia de 120 Ω. Para comprobarlo, mide con
  el multímetro entre CANH y CANL **con todo apagado**:
  - ≈ 60 Ω: hay dos terminaciones (correcto).
  - ≈ 120 Ω: falta una; agrega 120 Ω en el otro extremo.
  - Abierto: no hay ninguna; agrega dos.
- **Cable.** Trenza CANH con CANL. En la maqueta bastan 0.3–2 m.
- **Sin el gateway no hay confirmación.** Si el gateway no está conectado, nadie confirma las tramas (ACK) y en el
  nodo sube el contador de "fallidas". Es lo esperado.

## Orden de armado y prueba

1. **Solo la ESP32 por USB.** Sube el firmware (`pio run -e nodo_sensores -t upload`) y abre el monitor a 115200
   baudios, con fin de línea "Nueva línea". Debe aparecer:
   `[PRUEBA] Criptografía OK · trama D98F7BEA0001F8FC`
2. **Provisiona la clave.** Pega en el monitor `clave <64 hex>`. La clave sale de `python manage.py inicializar` o
   del botón *Rotar clave* del tablero. El nodo se reinicia y el LED pasa de parpadeo rápido a un destello cada 2 s.
3. **Conecta un sensor a la vez** y mira su línea en el monitor:
   ```
   [A/C] 23.4 °C · 55 %HR
   [SAN] Tanque: 152 mm → 54.8 %
   [SAN] Puerta: cerrada (41 mm) · presencia: sí · usos: 3
   [LUZ] Zona 1: 78 % · zona 2: 64 % · zona 3: encendida
   ```
4. **Conecta el SN65HVD230 y el gateway.** En el gateway, el contador de tramas "recibidas" debe subir.
5. **Usa el comando `estado`** en el nodo en cualquier momento. Muestra la clave, el contador, las tramas enviadas y
   los errores del bus.

## Calibración (archivo `firmware/include/config_nodo.h`)

| Constante | Cómo obtenerla |
|---|---|
| `TANQUE_ALTURA_MM` | Distancia que imprime el nodo con el tanque **vacío** |
| `TANQUE_ZONA_MUERTA_MM` | Distancia con el tanque **lleno**. Debe ser de al menos 20–30 mm, porque el HC-SR04 no mide más cerca |
| `PUERTA_ABIERTA_MM` | Punto medio entre los mm que imprime con la puerta cerrada y con la puerta abierta |
| `MARCAS_POR_VUELTA` | 1 con una marca reflejante; si el HW-201 detecta cada aspa, pon el número de aspas |
| `SUELO_SECO_MV` / `SUELO_MOJADO_MV` | Lectura en seco y con las puntas en un trapo mojado |
| `ZONA2_INVERTIDA` | `true` si al tapar el módulo la lectura sube |

## Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| No se puede programar o no arranca | Algo conectado a GPIO12, o GPIO2 forzado en alto | Desconecta ese cable y vuelve a intentar |
| `DHT11 sin respuesta` | DATA mal conectado | Revisa GPIO4 y la alimentación de 3.3 V |
| `HC-SR04 sin eco` | Falta 5 V o el divisor está al revés | 1 kΩ del lado del ECHO y 2 kΩ hacia GND |
| `No se detectó el VL53L0X` | SDA y SCL invertidos | SDA → 21, SCL → 22 |
| Presencia "calentando" el primer minuto | El PIR se estabiliza al encender | Es normal; el nodo lo ignora 60 s |
| "fallidas" sube sin parar | Nadie confirma en el bus | Conecta el gateway; revisa CANH/CANL, la GND común y los 60 Ω |
| Lecturas analógicas fijas en 0 o en 3300 mV | Sensor alimentado a 5 V, o pin equivocado | Fila V en 3.3 V y los pines de la tabla |
