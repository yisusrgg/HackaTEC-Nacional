#pragma once
// ===================== Configuración del ESP32 #2 (gateway) =====================
// La identidad, el Wi-Fi, el servidor y la clave NO van aquí: se provisionan por el monitor serie
// y se guardan en la memoria NVS (ver docs/02-gateway.md).

// --- Enlace CAN SIN transceptor (prototipo actual) ---
// Cuando tengan los módulos SN65HVD230, comenten esta línea AQUÍ Y en config_nodo.h
// y vuelvan a subir los dos sketches.
#define SIN_TRANSCEPTOR 1
#define CONFIG_CAN_LISTA 1  // lo comprueba bus_can.h; no lo quiten

#define GATEWAY_FW "0.2.0"

#define PIN_CAN_TX 17  // sin transceptor: SIN CONECTAR (el gateway solo escucha) · con transceptor: → CTX
#define PIN_CAN_RX 16  // sin transceptor: cable desde GPIO17 del nodo · con transceptor: ← CRX
#define PIN_LED 2

#define LOTE_MAX_TRAMAS 40       // tramas por lote...
#define LOTE_MAX_MS 2000         // ...o cada 2 s, lo que ocurra primero
#define COLA_MAX_ARCHIVOS 300    // lotes guardados en flash sin red (~10 minutos); luego se descartan los más viejos
#define RESERVA_LOTE 100         // la numeración de lotes salta 100 al reiniciar: nunca se repite un número
#define HORA_RESINCRONIZAR_MS (10UL * 60UL * 1000UL)
#define HTTP_TIMEOUT_MS 8000
#define ESPERA_MAX_MS 30000      // reintentos con espera exponencial: 1 s, 2 s, 4 s… hasta 30 s
#define RUTA_INGESTA "/api/v1/ingesta/"
#define RUTA_HORA "/api/v1/hora/"
