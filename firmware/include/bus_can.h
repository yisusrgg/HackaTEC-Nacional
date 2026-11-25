#pragma once
#include <driver/twai.h>

// Bus CAN propio a 250 kbit/s con tramas estándar de 11 bits (controlador TWAI integrado del ESP32).
// Este bus NO se conecta al CAN del autobús.
//
// Dos formas de cablearlo; se elige con -DSIN_TRANSCEPTOR=1 en platformio.ini (ver docs/01 y el diagrama del enlace):
//  • SIN transceptor (prototipo): el nodo transmite en modo NO_ACK con sus pines TX y RX unidos por un jumper, y un
//    cable lleva esa señal al RX del gateway, que solo escucha (LISTEN_ONLY). Enlace de un solo sentido a 3.3 V.
//  • CON transceptores SN65HVD230 (producción): ambos en modo NORMAL, par trenzado CANH/CANL con 120 Ω en los extremos.
#ifdef SIN_TRANSCEPTOR
constexpr twai_mode_t MODO_CAN_NODO = TWAI_MODE_NO_ACK;
constexpr twai_mode_t MODO_CAN_GATEWAY = TWAI_MODE_LISTEN_ONLY;
constexpr const char *TEXTO_MODO_CAN = "SIN transceptor (nodo NO_ACK, gateway LISTEN_ONLY)";
#else
constexpr twai_mode_t MODO_CAN_NODO = TWAI_MODE_NORMAL;
constexpr twai_mode_t MODO_CAN_GATEWAY = TWAI_MODE_NORMAL;
constexpr const char *TEXTO_MODO_CAN = "CON transceptor SN65HVD230 (modo NORMAL)";
#endif

inline bool canIniciar(int pinTx, int pinRx, twai_mode_t modo, uint32_t colaTx, uint32_t colaRx) {
  twai_general_config_t general = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)pinTx, (gpio_num_t)pinRx, modo);
  general.tx_queue_len = colaTx;
  general.rx_queue_len = colaRx;
  twai_timing_config_t tiempos = TWAI_TIMING_CONFIG_250KBITS();
  twai_filter_config_t filtro = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  if (twai_driver_install(&general, &tiempos, &filtro) != ESP_OK) return false;
  return twai_start() == ESP_OK;
}

// Si el controlador quedó en bus-off (demasiados errores seguidos), lo recupera y lo vuelve a arrancar.
inline void canRecuperar() {
  twai_status_info_t estado;
  if (twai_get_status_info(&estado) != ESP_OK) return;
  if (estado.state == TWAI_STATE_BUS_OFF) {
    twai_initiate_recovery();
  } else if (estado.state == TWAI_STATE_STOPPED) {
    twai_start();
  }
}

// Estado legible: ACTIVO (<96 errores), ADVERTENCIA (>=96), ERROR-PASIVO (>=128), BUS-OFF, RECUPERANDO o DETENIDO.
inline const char *canTextoEstado(const twai_status_info_t &estado) {
  switch (estado.state) {
    case TWAI_STATE_RUNNING: {
      uint32_t peor = estado.tx_error_counter > estado.rx_error_counter ? estado.tx_error_counter : estado.rx_error_counter;
      if (peor >= 128) return "ERROR-PASIVO";
      if (peor >= 96) return "ADVERTENCIA";
      return "ACTIVO";
    }
    case TWAI_STATE_BUS_OFF:
      return "BUS-OFF";
    case TWAI_STATE_RECOVERING:
      return "RECUPERANDO";
    default:
      return "DETENIDO";
  }
}
