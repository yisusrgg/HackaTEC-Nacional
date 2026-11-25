#pragma once
// ===================== Configuración del ESP32 #1 (nodo de sensores) =====================
// Pines según docs/01-conexiones-nodo-sensores.md (ESP32 DevKit V1 de 30 pines).
// La clave secreta NO va aquí: se provisiona por el monitor serie y se guarda en la memoria NVS.

#define NODO_NUMERO 1             // byte alto del ID CAN (0x1MM); debe coincidir con el nodo registrado en Django
#define FIRMWARE_VERSION "0.1.0"
#define DEPURACION 1              // 1 = imprime las lecturas en el monitor serie

// --- Pines ---
#define PIN_CAN_TX 17      // sin transceptor: jumper a GPIO16 y cable al GPIO16 del gateway · con transceptor: → CTX
#define PIN_CAN_RX 16      // sin transceptor: unido a GPIO17 por el jumper · con transceptor: ← CRX
#define PIN_DHT 4          // DHT11 DATA
#define PIN_RPM 27         // HW-201 OUT (tacómetro óptico)
#define PIN_US_TRIG 26     // HC-SR04 TRIG
#define PIN_US_ECHO 25     // HC-SR04 ECHO (¡con divisor 1 kΩ / 2 kΩ!)
#define PIN_I2C_SDA 21     // VL53L0X SDA
#define PIN_I2C_SCL 22     // VL53L0X SCL
#define PIN_PIR 14         // HC-SR501 OUT
#define PIN_LDR_ZONA1 36   // VP: fotorresistencia + 10 kΩ (ADC1)
#define PIN_LDR_ZONA2 39   // VN: módulo LDR analógico AO (ADC1)
#define PIN_LDR_ZONA3 13   // módulo LDR digital DO
#define PIN_SUELO_AO 35    // sensor de humedad de suelo AO (ADC1)
#define PIN_SUELO_VCC 32   // alimenta el sensor de suelo solo al medir (evita la corrosión)
#define PIN_VOLTAJE 34     // divisor 100 kΩ / 33 kΩ desde VIN (ADC1)
#define PIN_LED 2          // LED integrado

// --- Calibración (ajústenla con su maqueta) ---
#define TANQUE_ALTURA_MM 300.0f        // distancia del sensor al fondo (tanque vacío)
#define TANQUE_ZONA_MUERTA_MM 30.0f    // distancia del sensor al nivel máximo (tanque lleno)
#define US_MUESTRAS 5                  // lecturas por medición; se usa la mediana
#define PUERTA_ABIERTA_MM 100          // VL53L0X: más lejos que esto = puerta abierta
#define PUERTA_HISTERESIS_MM 10        // evita parpadeos cerca del umbral
#define MARCAS_POR_VUELTA 1            // marcas reflejantes pegadas en el ventilador
#define RPM_ANTIRREBOTE_US 2000        // ignora pulsos más rápidos (máx. 30 000 rpm con 1 marca)
#define SUELO_SECO_MV 3000.0f          // lectura del sensor de suelo en seco
#define SUELO_MOJADO_MV 1200.0f        // lectura con las puntas mojadas
#define FUGA_UMBRAL_PCT 40.0f
#define DIVISOR_VOLTAJE ((100.0f + 33.0f) / 33.0f)
#define ZONA1_INVERTIDA false          // la LDR suelta con 10 kΩ a GND da más voltaje con más luz
#define ZONA2_INVERTIDA true           // la mayoría de módulos LDR dan MÁS voltaje con oscuridad; si no, pongan false
#define ZONA3_ACTIVA_EN_BAJO true      // la mayoría de módulos digitales ponen DO en LOW cuando hay luz
#define PIR_CALENTAMIENTO_MS 60000UL   // el HC-SR501 tarda ~1 minuto en estabilizarse

// --- Periodos (ms) ---
#define PERIODO_AMBIENTE_MS 2000       // el DHT11 no admite más de una lectura por segundo
#define PERIODO_VENTILADOR_MS 1000
#define PERIODO_TANQUE_MS 2000
#define PERIODO_SANITARIO_MS 1000      // además envía de inmediato cuando cambia la puerta o la presencia
#define PERIODO_FUGA_MS 5000
#define PERIODO_LUCES_MS 1000
#define PERIODO_ALIMENTACION_MS 5000
#define PERIODO_LATIDO_MS 5000
