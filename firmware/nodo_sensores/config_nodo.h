#pragma once
// ===================== Configuración del ESP32 #1 (nodo de sensores) =====================
// Pines según docs/01-conexiones-nodo-sensores.md y docs/diagrama-conexiones-completo.png
// (ESP32 DevKit V1 de 30 pines). La clave secreta NO va aquí: se provisiona por el monitor serie
// y se guarda en la memoria NVS.

// --- Enlace CAN SIN transceptor (prototipo actual) ---
// Cuando tengan los módulos SN65HVD230, comenten esta línea AQUÍ Y en config_gateway.h
// y vuelvan a subir los dos sketches.
#define SIN_TRANSCEPTOR 1
#define CONFIG_CAN_LISTA 1  // lo comprueba bus_can.h; no lo quiten

#define NODO_NUMERO 1             // byte alto del ID CAN (0x1MM); debe coincidir con el nodo registrado en Django
#define FIRMWARE_VERSION "0.2.0"
#define DEPURACION 1              // 1 = imprime el reporte de sensores en el monitor serie (115200 baudios)

// --- Pines ---
#define PIN_CAN_TX 17      // sin transceptor: jumper a GPIO16 y cable al GPIO16 del gateway · con transceptor: → CTX
#define PIN_CAN_RX 16      // sin transceptor: unido a GPIO17 por el jumper · con transceptor: ← CRX
#define PIN_DHT 4          // DHT11 DATA
#define PIN_RPM 27         // HW-201 OUT (tacómetro óptico)
#define PIN_US_TRIG 26     // HC-SR04 TRIG
#define PIN_US_ECHO 25     // HC-SR04 ECHO (¡con divisor 1 kΩ / 2 kΩ!)
#define PIN_PIR 14         // HC-SR501 OUT
#define PIN_LDR 39         // VN: módulo LDR analógico, salida AO (ADC1). Es la única fotorresistencia.
#define PIN_SUELO_AO 35    // sensor de humedad de suelo AO (ADC1)
#define PIN_SUELO_VCC 32   // alimenta el sensor de suelo solo al medir (evita la corrosión)
#define PIN_VOLTAJE 34     // divisor 100 kΩ / 33 kΩ desde VIN (ADC1)
#define PIN_LED 2          // LED integrado

// --- Calibración (ajústenla con su maqueta) ---
#define TANQUE_ALTURA_MM 110.0f        // distancia del sensor al fondo (vacío): recipiente de 11 cm, sensor en el borde
#define TANQUE_ZONA_MUERTA_MM 30.0f    // distancia del sensor al agua (lleno = 8 cm de agua); el HC-SR04 no mide a menos de 2-3 cm
#define US_MUESTRAS 5                  // lecturas por medición; se usa la mediana
#define MARCAS_POR_VUELTA 1            // marcas reflejantes pegadas en el ventilador
#define RPM_ANTIRREBOTE_US 2000        // ignora pulsos más rápidos (máx. 30 000 rpm con 1 marca)
#define SUELO_SECO_MV 3000.0f          // lectura del sensor de suelo en seco
#define SUELO_MOJADO_MV 1200.0f        // lectura con las puntas mojadas
#define FUGA_UMBRAL_PCT 40.0f
#define DIVISOR_VOLTAJE ((100.0f + 33.0f) / 33.0f)
#define LDR_INVERTIDA true             // la mayoría de módulos LDR dan MÁS voltaje con oscuridad; si al tapar la LDR
                                       // el % de luz SUBE, pongan false
#define PIR_CALENTAMIENTO_MS 60000UL   // el HC-SR501 tarda ~1 minuto en estabilizarse
#define SANITARIO_SALIDA_MS 15000UL    // sin presencia este tiempo = la persona salió = 1 uso (en servicio real, 30-60 s)

// --- Periodos (ms) ---
#define PERIODO_AMBIENTE_MS 2000       // el DHT11 no admite más de una lectura por segundo
#define PERIODO_VENTILADOR_MS 1000
#define PERIODO_TANQUE_MS 2000
#define PERIODO_SANITARIO_MS 1000      // además envía de inmediato cuando cambia la presencia
#define PERIODO_FUGA_MS 5000
#define PERIODO_LUCES_MS 1000
#define PERIODO_ALIMENTACION_MS 5000
#define PERIODO_LATIDO_MS 5000
#define PERIODO_REPORTE_MS 3000        // cada cuánto se imprime el reporte agrupado en el monitor serie
