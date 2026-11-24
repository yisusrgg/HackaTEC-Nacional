#pragma once
#include <esp_arduino_version.h>
#include <esp_task_wdt.h>

// Watchdog de tareas: si el loop se cuelga más de "segundos", el ESP32 se reinicia solo (operación continua).
inline void vigilanteIniciar(uint32_t segundos) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t config = {};
  config.timeout_ms = segundos * 1000;
  config.idle_core_mask = 0;
  config.trigger_panic = true;
  if (esp_task_wdt_reconfigure(&config) != ESP_OK) esp_task_wdt_init(&config);
#else
  esp_task_wdt_init(segundos, true);
#endif
  esp_task_wdt_add(NULL);
}

inline void vigilanteAlimentar() { esp_task_wdt_reset(); }
