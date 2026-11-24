#pragma once
#include <driver/twai.h>

// Bus CAN propio a 250 kbit/s con tramas estándar de 11 bits (controlador TWAI integrado del ESP32 +
// transceptor SN65HVD230). Este bus NO se conecta al CAN del autobús.

inline bool canIniciar(int pinTx, int pinRx, uint32_t colaTx, uint32_t colaRx) {
  twai_general_config_t general = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)pinTx, (gpio_num_t)pinRx, TWAI_MODE_NORMAL);
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
