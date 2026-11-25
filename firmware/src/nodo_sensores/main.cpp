// =====================================================================================================
// ESP32 #1 · NODO DE SENSORES DE CARROCERÍA
// Lee los sensores, arma tramas seguras (cifrado + contador + MAC) y las transmite por el bus CAN propio.
// No usa Wi-Fi ni Bluetooth: su única salida es el bus CAN hacia el gateway (segmentación).
// Conexiones: docs/01-conexiones-nodo-sensores.md · Protocolo: docs/03-protocolo-can.md
// =====================================================================================================
#include <Arduino.h>
#include <DHT.h>
#include <Preferences.h>
#include <VL53L0X.h>
#include <Wire.h>
#include <stdarg.h>

#include "bus_can.h"
#include "config_nodo.h"
#include "protocolo.h"
#include "vigilante.h"

using namespace protocolo;

// Bits del campo "fallas" del latido (MSG_ESTADO_NODO)
enum : uint16_t {
  FALLA_DHT = 1 << 0,
  FALLA_ULTRASONICO = 1 << 1,
  FALLA_TOF = 1 << 2,
  PIR_CALENTANDO = 1 << 3,
  FALLA_CAN_TX = 1 << 4,
};

// ---------- Seguridad ----------
static Preferences prefs;
static Claves claves;
static bool hayClave = false;
static uint32_t contador = 0;      // contador de frescura de 32 bits: nunca se repite
static uint32_t reservaHasta = 0;  // los contadores hasta aquí ya están reservados en la flash
static uint32_t tramasEnviadas = 0, tramasFallidas = 0;
static uint16_t fallas = 0;

// ---------- Sensores ----------
static DHT dht(PIN_DHT, DHT11);
static VL53L0X tof;
static bool tofListo = false;
static float ultimaTemperatura = 25.0f;  // compensa la velocidad del sonido del HC-SR04
static bool temperaturaValida = false;

static portMUX_TYPE muxRpm = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t pulsosRpm = 0;
static volatile uint32_t ultimoPulsoUs = 0;
static uint32_t ultimaLecturaRpmMs = 0;

// Máquina de estados del sanitario: puerta se cierra → hay presencia → puerta se abre = 1 uso
enum class EstadoSanitario { LIBRE, PUERTA_CERRADA, OCUPADO };
static EstadoSanitario estadoSanitario = EstadoSanitario::LIBRE;
static uint16_t usos = 0;
static uint8_t puertaEstado = SIN_DATO_U8;
static uint16_t distanciaPuertaMm = 0;  // se imprime para calibrar PUERTA_ABIERTA_MM
static uint8_t puertaEnviada = SIN_DATO_U8, presenciaEnviada = SIN_DATO_U8;
static uint32_t ultimoEnvioSanitarioMs = 0;

// ---------- Utilidades ----------
static float limitar(float valor, float minimo, float maximo) {
  return valor < minimo ? minimo : (valor > maximo ? maximo : valor);
}

static void depurarf(const char *formato, ...) {
#if DEPURACION
  char texto[160];
  va_list argumentos;
  va_start(argumentos, formato);
  vsnprintf(texto, sizeof(texto), formato, argumentos);
  va_end(argumentos);
  Serial.println(texto);
#endif
}

// ---------- Clave y contador (NVS) ----------
static void cargarSeguridad() {
  prefs.begin("nodo", false);
  uint8_t maestra[LONGITUD_CLAVE];
  if (prefs.isKey("clave") && prefs.getBytes("clave", maestra, LONGITUD_CLAVE) == LONGITUD_CLAVE) {
    hayClave = derivarClaves(maestra, claves);
  }
  memset(maestra, 0, sizeof(maestra));
  // Al arrancar se salta al siguiente bloque reservado: tras un reinicio nunca se reutiliza un contador.
  contador = prefs.isKey("reserva") ? prefs.getUInt("reserva", 0) : 0;
  reservaHasta = contador + RESERVA_CONTADOR;
  prefs.putUInt("reserva", reservaHasta);
}

static uint32_t siguienteContador() {
  if (contador >= reservaHasta) {
    reservaHasta = contador + RESERVA_CONTADOR;
    prefs.putUInt("reserva", reservaHasta);  // una escritura en flash cada 1000 tramas
  }
  return contador++;
}

// ---------- Envío por CAN ----------
static void enviar(uint8_t mensaje, const uint8_t plano[4]) {
  if (!hayClave) return;  // sin clave no se transmite nada
  canRecuperar();
  twai_message_t trama = {};
  trama.identifier = idCan(NODO_NUMERO, mensaje);
  trama.data_length_code = 8;
  if (!armarTrama(claves, (uint16_t)trama.identifier, siguienteContador(), plano, trama.data)) return;
  if (twai_transmit(&trama, pdMS_TO_TICKS(20)) == ESP_OK) {
    tramasEnviadas++;
    fallas &= ~FALLA_CAN_TX;
  } else {
    tramasFallidas++;  // el controlador no logra transmitir: sin transceptor, ¿falta el jumper GPIO17↔GPIO16?;
                       // con transceptor, ¿nadie confirma las tramas (gateway apagado) o el bus está mal cableado?
    fallas |= FALLA_CAN_TX;
  }
}

// ---------- A/C: DHT11 ----------
static void tareaAmbiente() {
  float temperatura = dht.readTemperature();
  float humedad = dht.readHumidity();
  uint8_t p[4];
  if (isnan(temperatura) || isnan(humedad)) {
    fallas |= FALLA_DHT;
    temperaturaValida = false;
    escribirI16(p, SIN_DATO_I16);
    escribirU16(p + 2, SIN_DATO_U16);
    depurarf("[A/C] DHT11 sin respuesta");
  } else {
    fallas &= ~FALLA_DHT;
    ultimaTemperatura = temperatura;
    temperaturaValida = true;
    escribirI16(p, (int16_t)lroundf(temperatura * 10));
    escribirU16(p + 2, (uint16_t)lroundf(humedad * 10));
    depurarf("[A/C] %.1f °C · %.0f %%HR", temperatura, humedad);
  }
  enviar(MSG_AC_AMBIENTE, p);
}

// ---------- A/C: HW-201 como tacómetro óptico ----------
static void IRAM_ATTR isrRpm() {
  uint32_t ahora = micros();
  portENTER_CRITICAL_ISR(&muxRpm);
  if (ahora - ultimoPulsoUs > RPM_ANTIRREBOTE_US) {
    pulsosRpm++;
    ultimoPulsoUs = ahora;
  }
  portEXIT_CRITICAL_ISR(&muxRpm);
}

static void tareaVentilador() {
  portENTER_CRITICAL(&muxRpm);
  uint32_t pulsos = pulsosRpm;
  pulsosRpm = 0;
  portEXIT_CRITICAL(&muxRpm);
  uint32_t ahora = millis();
  uint32_t intervalo = ahora - ultimaLecturaRpmMs;
  ultimaLecturaRpmMs = ahora;
  uint32_t rpm = intervalo ? (pulsos * 60000UL) / (intervalo * MARCAS_POR_VUELTA) : 0;
  uint8_t p[4] = {0, 0, 0, 0};
  escribirU16(p, (uint16_t)(rpm > 0xFFFE ? 0xFFFE : rpm));
  depurarf("[A/C] Ventilador: %lu rpm", (unsigned long)rpm);
  enviar(MSG_AC_VENTILADOR, p);
}

// ---------- Sanitario: HC-SR04 (nivel del tanque) ----------
static float medirDistanciaMm() {
  float lecturas[US_MUESTRAS];
  int n = 0;
  float velocidad = 331.3f + 0.606f * (temperaturaValida ? ultimaTemperatura : 25.0f);  // m/s
  for (int i = 0; i < US_MUESTRAS; i++) {
    digitalWrite(PIN_US_TRIG, LOW);
    delayMicroseconds(2);
    digitalWrite(PIN_US_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_US_TRIG, LOW);
    unsigned long duracion = pulseIn(PIN_US_ECHO, HIGH, 30000UL);
    if (duracion > 0) lecturas[n++] = duracion * velocidad / 2000.0f;  // µs · m/s / 2 → mm
    delay(60);  // deja que se apaguen los ecos antes del siguiente disparo
  }
  if (n < 3) return NAN;
  for (int i = 1; i < n; i++) {  // ordenamiento por inserción para obtener la mediana
    float valor = lecturas[i];
    int j = i - 1;
    while (j >= 0 && lecturas[j] > valor) {
      lecturas[j + 1] = lecturas[j];
      j--;
    }
    lecturas[j + 1] = valor;
  }
  return lecturas[n / 2];
}

static void tareaTanque() {
  float distancia = medirDistanciaMm();
  uint8_t p[4];
  if (isnan(distancia)) {
    fallas |= FALLA_ULTRASONICO;
    escribirU16(p, SIN_DATO_U16);
    escribirU16(p + 2, SIN_DATO_U16);
    depurarf("[SAN] HC-SR04 sin eco");
  } else {
    fallas &= ~FALLA_ULTRASONICO;
    float nivel = limitar((TANQUE_ALTURA_MM - distancia) * 100.0f / (TANQUE_ALTURA_MM - TANQUE_ZONA_MUERTA_MM), 0, 100);
    escribirU16(p, (uint16_t)lroundf(limitar(distancia, 0, 65534)));
    escribirU16(p + 2, (uint16_t)lroundf(nivel * 10));
    depurarf("[SAN] Tanque: %.0f mm → %.1f %%", distancia, nivel);
  }
  enviar(MSG_SAN_TANQUE, p);
}

// ---------- Sanitario: VL53L0X (puerta) + HC-SR501 (presencia) ----------
static uint8_t leerPuerta() {
  if (!tofListo) return SIN_DATO_U8;
  uint16_t mm = tof.readRangeSingleMillimeters();
  if (tof.timeoutOccurred()) {
    fallas |= FALLA_TOF;
    return SIN_DATO_U8;
  }
  fallas &= ~FALLA_TOF;
  distanciaPuertaMm = mm;
  // Fuera de rango (~8190 mm) = nada cerca = puerta abierta. La histéresis evita parpadeos.
  if (mm > PUERTA_ABIERTA_MM + PUERTA_HISTERESIS_MM) puertaEstado = 1;
  else if (mm < PUERTA_ABIERTA_MM - PUERTA_HISTERESIS_MM) puertaEstado = 0;
  else if (puertaEstado == SIN_DATO_U8) puertaEstado = mm > PUERTA_ABIERTA_MM ? 1 : 0;
  return puertaEstado;
}

static uint8_t leerPresencia() {
  if (millis() < PIR_CALENTAMIENTO_MS) {
    fallas |= PIR_CALENTANDO;
    return SIN_DATO_U8;
  }
  fallas &= ~PIR_CALENTANDO;
  return digitalRead(PIN_PIR) == HIGH ? 1 : 0;
}

static void actualizarUsos(uint8_t puerta, uint8_t presencia) {
  if (puerta == SIN_DATO_U8) return;
  switch (estadoSanitario) {
    case EstadoSanitario::LIBRE:
      if (puerta == 0) estadoSanitario = EstadoSanitario::PUERTA_CERRADA;
      break;
    case EstadoSanitario::PUERTA_CERRADA:
      if (puerta == 1) estadoSanitario = EstadoSanitario::LIBRE;
      else if (presencia == 1) estadoSanitario = EstadoSanitario::OCUPADO;
      break;
    case EstadoSanitario::OCUPADO:
      if (puerta == 1) {
        usos++;
        estadoSanitario = EstadoSanitario::LIBRE;
      }
      break;
  }
}

static void tareaSanitario() {  // corre cada 250 ms; envía al cambiar o cada PERIODO_SANITARIO_MS
  uint8_t puerta = leerPuerta();
  uint8_t presencia = leerPresencia();
  actualizarUsos(puerta, presencia);
  bool cambio = puerta != puertaEnviada || presencia != presenciaEnviada;
  if (!cambio && millis() - ultimoEnvioSanitarioMs < PERIODO_SANITARIO_MS) return;
  puertaEnviada = puerta;
  presenciaEnviada = presencia;
  ultimoEnvioSanitarioMs = millis();
  uint8_t p[4] = {puerta, presencia, 0, 0};
  escribirU16(p + 2, usos);
  depurarf("[SAN] Puerta: %s (%u mm) · presencia: %s · usos: %u",
           puerta == SIN_DATO_U8 ? "?" : (puerta ? "abierta" : "cerrada"), distanciaPuertaMm,
           presencia == SIN_DATO_U8 ? "calentando" : (presencia ? "sí" : "no"), usos);
  enviar(MSG_SAN_ESTADO, p);
}

// ---------- Sanitario: sensor de humedad de suelo como detector de fugas ----------
static void tareaFuga() {
  digitalWrite(PIN_SUELO_VCC, HIGH);  // se energiza solo al medir para que las pistas no se corroan
  delay(20);
  uint32_t suma = 0;
  for (int i = 0; i < 8; i++) suma += analogReadMilliVolts(PIN_SUELO_AO);
  digitalWrite(PIN_SUELO_VCC, LOW);
  float milivolts = suma / 8.0f;
  float humedad = limitar((SUELO_SECO_MV - milivolts) * 100.0f / (SUELO_SECO_MV - SUELO_MOJADO_MV), 0, 100);
  uint8_t fuga = humedad >= FUGA_UMBRAL_PCT ? 1 : 0;
  uint8_t p[4] = {0, 0, fuga, 0};
  escribirU16(p, (uint16_t)lroundf(humedad * 10));
  depurarf("[SAN] Piso: %.0f mV → %.0f %% %s", milivolts, humedad, fuga ? "¡FUGA!" : "");
  enviar(MSG_SAN_FUGA, p);
}

// ---------- Iluminación: 3 fotorresistencias ----------
static uint8_t porcentajeLuz(int pin, bool invertida) {
  uint32_t suma = 0;
  for (int i = 0; i < 4; i++) suma += analogReadMilliVolts(pin);
  float porcentaje = (suma / 4.0f) * 100.0f / 3300.0f;
  if (invertida) porcentaje = 100.0f - porcentaje;
  return (uint8_t)lroundf(limitar(porcentaje, 0, 100));
}

static void tareaLuces() {
  uint8_t zona1 = porcentajeLuz(PIN_LDR_ZONA1, ZONA1_INVERTIDA);
  uint8_t zona2 = porcentajeLuz(PIN_LDR_ZONA2, ZONA2_INVERTIDA);
  int lectura = digitalRead(PIN_LDR_ZONA3);
  uint8_t zona3 = ZONA3_ACTIVA_EN_BAJO ? (lectura == LOW) : (lectura == HIGH);
  uint8_t p[4] = {zona1, zona2, zona3, 0};
  depurarf("[LUZ] Zona 1: %u %% · zona 2: %u %% · zona 3: %s", zona1, zona2, zona3 ? "encendida" : "apagada");
  enviar(MSG_LUZ_ZONAS, p);
}

// ---------- Eléctrico: voltaje de alimentación ----------
static void tareaAlimentacion() {
  uint32_t suma = 0;
  for (int i = 0; i < 8; i++) suma += analogReadMilliVolts(PIN_VOLTAJE);
  float milivolts = (suma / 8.0f) * DIVISOR_VOLTAJE;
  uint8_t p[4] = {0, 0, 0, 0};
  escribirU16(p, (uint16_t)lroundf(limitar(milivolts, 0, 65534)));
  depurarf("[ELEC] Alimentación: %.2f V", milivolts / 1000.0f);
  enviar(MSG_ALIMENTACION, p);
}

// ---------- Latido del nodo ----------
static void tareaLatido() {
  uint8_t p[4];
  uint32_t minutos = millis() / 60000UL;
  escribirU16(p, (uint16_t)(minutos > 0xFFFE ? 0xFFFE : minutos));
  escribirU16(p + 2, fallas);
  enviar(MSG_ESTADO_NODO, p);
  depurarf("[NODO] contador %lu · enviadas %lu · fallidas %lu · fallas 0x%04X", (unsigned long)contador,
           (unsigned long)tramasEnviadas, (unsigned long)tramasFallidas, fallas);
}

// ---------- Monitor serie: provisión y diagnóstico ----------
static void imprimirAyuda() {
  Serial.println(F("Comandos: estado | clave <64 hex> | borrar SI | prueba | reiniciar"));
}

static void imprimirEstado() {
  twai_status_info_t can = {};
  twai_get_status_info(&can);
  Serial.printf("Nodo %d · firmware %s\n", NODO_NUMERO, FIRMWARE_VERSION);
  Serial.printf("  Clave: %s\n", hayClave ? "provisionada" : "FALTA (usa: clave <64 hex>)");
  Serial.printf("  Contador: %lu (reservado hasta %lu)\n", (unsigned long)contador, (unsigned long)reservaHasta);
  Serial.printf("  Tramas enviadas: %lu · fallidas: %lu\n", (unsigned long)tramasEnviadas, (unsigned long)tramasFallidas);
  Serial.printf("  Bus CAN: %s · errores TX %lu · RX %lu · bus %lu\n", canTextoEstado(can), (unsigned long)can.tx_error_counter,
                (unsigned long)can.rx_error_counter, (unsigned long)can.bus_error_count);
  Serial.printf("  VL53L0X: %s · fallas: 0x%04X\n", tofListo ? "listo" : "NO DETECTADO", fallas);
}

static void procesarComando(String linea) {
  linea.trim();
  if (linea.isEmpty()) return;
  int espacio = linea.indexOf(' ');
  String comando = espacio < 0 ? linea : linea.substring(0, espacio);
  String argumento = espacio < 0 ? String() : linea.substring(espacio + 1);
  comando.toLowerCase();
  argumento.trim();

  if (comando == "estado") {
    imprimirEstado();
  } else if (comando == "clave") {
    uint8_t nueva[LONGITUD_CLAVE];
    if (!hexABytes(argumento.c_str(), nueva, LONGITUD_CLAVE)) {
      Serial.println(F("[ERROR] La clave debe tener 64 caracteres hexadecimales (cópiala del tablero)."));
      return;
    }
    prefs.putBytes("clave", nueva, LONGITUD_CLAVE);
    prefs.putUInt("reserva", 0);  // con la clave nueva el contador arranca en 0 (el servidor también lo reinicia)
    memset(nueva, 0, sizeof(nueva));
    Serial.println(F("[OK] Clave guardada. Reiniciando..."));
    delay(300);
    ESP.restart();
  } else if (comando == "borrar") {
    if (argumento != "SI") {
      Serial.println(F("Para borrar la clave escribe exactamente: borrar SI"));
      return;
    }
    prefs.clear();
    Serial.println(F("[OK] Clave y contador borrados. Reiniciando..."));
    delay(300);
    ESP.restart();
  } else if (comando == "prueba") {
    autoprueba(Serial);
  } else if (comando == "reiniciar") {
    ESP.restart();
  } else {
    imprimirAyuda();
  }
}

static void atenderSerial() {
  static String linea;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      procesarComando(linea);
      linea = "";
    } else if (linea.length() < 160) {
      linea += c;
    }
  }
}

static void actualizarLed() {
  uint32_t ms = millis();
  // Destello corto cada 2 s = funcionando · parpadeo rápido = falta la clave
  bool encendido = hayClave ? (ms % 2000) < 80 : (ms / 150) % 2;
  digitalWrite(PIN_LED, encendido ? HIGH : LOW);
}

// ---------- Planificador simple ----------
struct Tarea {
  void (*funcion)();
  uint32_t periodoMs;
  uint32_t proximaMs;
};

static Tarea tareas[] = {
    {tareaAmbiente, PERIODO_AMBIENTE_MS, 500},
    {tareaVentilador, PERIODO_VENTILADOR_MS, 1000},
    {tareaTanque, PERIODO_TANQUE_MS, 1500},
    {tareaSanitario, 250, 700},
    {tareaFuga, PERIODO_FUGA_MS, 2500},
    {tareaLuces, PERIODO_LUCES_MS, 1200},
    {tareaAlimentacion, PERIODO_ALIMENTACION_MS, 3000},
    {tareaLatido, PERIODO_LATIDO_MS, 4000},
};

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\n=== Nodo de sensores %d · firmware %s ===\n", NODO_NUMERO, FIRMWARE_VERSION);

  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_US_TRIG, OUTPUT);
  digitalWrite(PIN_US_TRIG, LOW);
  pinMode(PIN_US_ECHO, INPUT);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_LDR_ZONA3, INPUT_PULLUP);
  pinMode(PIN_SUELO_VCC, OUTPUT);
  digitalWrite(PIN_SUELO_VCC, LOW);
  pinMode(PIN_RPM, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_RPM), isrRpm, FALLING);  // HW-201 pone OUT en LOW al ver la marca
  analogReadResolution(12);

  autoprueba(Serial);
  cargarSeguridad();
  if (!hayClave) {
    Serial.println(F("[AVISO] Nodo sin clave: no transmitirá hasta provisionarlo."));
    imprimirAyuda();
  }
  Serial.printf("[CAN] Modo: %s\n", TEXTO_MODO_CAN);
  if (!canIniciar(PIN_CAN_TX, PIN_CAN_RX, MODO_CAN_NODO, 16, 4)) {
    Serial.println(F("[ERROR] No arrancó el bus CAN: revisa los pines 16/17 (y el transceptor, si lo usan)."));
  }

  dht.begin();
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  tof.setTimeout(500);
  tofListo = tof.init();
  if (!tofListo) Serial.println(F("[ERROR] No se detectó el VL53L0X (revisa SDA=21, SCL=22 y 3.3 V)."));

  ultimaLecturaRpmMs = millis();
  vigilanteIniciar(10);
}

void loop() {
  atenderSerial();
  uint32_t ahora = millis();
  for (Tarea &tarea : tareas) {
    if ((int32_t)(ahora - tarea.proximaMs) >= 0) {
      tarea.proximaMs = ahora + tarea.periodoMs;
      tarea.funcion();
    }
  }
  actualizarLed();
  vigilanteAlimentar();
  delay(5);
}
