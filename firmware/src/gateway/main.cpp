// =====================================================================================================
// ESP32 #2 · GATEWAY (módulo de adquisición local)
// Recibe por el bus CAN las tramas seguras del nodo, las agrupa en lotes numerados, firma cada lote con
// HMAC-SHA256 y lo sube a Django por HTTPS verificando el certificado del servidor con la CA propia.
// No descifra ni modifica las tramas: su integridad se comprueba de punta a punta en el servidor.
// Si no hay red, guarda los lotes en la flash (LittleFS) y los reenvía en orden cuando vuelve.
// Guía: docs/02-gateway.md
// =====================================================================================================
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include <atomic>

#include "bus_can.h"
#include "ca_cert.h"
#include "config_gateway.h"
#include "protocolo.h"
#include "vigilante.h"

struct Configuracion {
  String ssid, password, servidor, id;
  uint8_t clave[protocolo::LONGITUD_CLAVE];
  bool tieneClave = false;
};

struct TramaRx {
  uint16_t id;
  uint8_t datos[8];
  uint32_t ms;  // millis() al recibirla
};

enum class Resultado { Ok, Reintentar, Descartar };

static Configuracion cfg;
static Preferences prefs;
static QueueHandle_t colaRx;
static WiFiClientSecure tls;
static HTTPClient http;

static std::atomic<uint32_t> canRecibidas{0};
static std::atomic<uint32_t> canDescartadas{0};
static TramaRx lote[LOTE_MAX_TRAMAS];
static size_t loteN = 0;
static uint32_t loteInicioMs = 0;
static uint32_t siguienteLote = 0, reservaLoteHasta = 0;
static uint32_t lotesDescartados = 0, archivosEnCola = 0;
static uint32_t proximoIntentoMs = 0, esperaMs = 1000;
static bool horaSincronizada = false;
static int64_t epochBaseMs = 0;
static uint32_t millisBase = 0, ultimaSincroMs = 0, ultimoWifiMs = 0, ultimoReporteMs = 0;

// ---------------------------------------------------------------------------------------------------
// Configuración en NVS
// ---------------------------------------------------------------------------------------------------
static String leerTexto(const char *clave) { return prefs.isKey(clave) ? prefs.getString(clave, "") : String(); }

static bool configCompleta() {
  return cfg.tieneClave && cfg.ssid.length() > 0 && cfg.servidor.length() > 0 && cfg.id.length() > 0;
}

static void cargarConfig() {
  prefs.begin("gw", false);
  cfg.ssid = leerTexto("ssid");
  cfg.password = leerTexto("pass");
  cfg.servidor = leerTexto("servidor");
  cfg.id = leerTexto("id");
  cfg.tieneClave = prefs.isKey("clave") && prefs.getBytes("clave", cfg.clave, sizeof(cfg.clave)) == sizeof(cfg.clave);
  // Igual que el contador del nodo: al arrancar se salta al siguiente bloque para no repetir números de lote.
  siguienteLote = prefs.isKey("lote_res") ? prefs.getUInt("lote_res", 0) : 0;
  reservaLoteHasta = siguienteLote + RESERVA_LOTE;
  prefs.putUInt("lote_res", reservaLoteHasta);
}

static uint32_t tomarNumeroLote() {
  if (siguienteLote >= reservaLoteHasta) {
    reservaLoteHasta = siguienteLote + RESERVA_LOTE;
    prefs.putUInt("lote_res", reservaLoteHasta);
  }
  return siguienteLote++;
}

// ---------------------------------------------------------------------------------------------------
// Cola de lotes en flash (LittleFS): un archivo por lote, nombrado con su número para mantener el orden
// ---------------------------------------------------------------------------------------------------
static String nombreBase(const char *ruta) {
  String nombre(ruta);
  int barra = nombre.lastIndexOf('/');
  return barra >= 0 ? nombre.substring(barra + 1) : nombre;
}

static uint32_t contarCola() {
  uint32_t n = 0;
  File carpeta = LittleFS.open("/cola");
  if (!carpeta || !carpeta.isDirectory()) return 0;
  for (File archivo = carpeta.openNextFile(); archivo; archivo = carpeta.openNextFile()) {
    if (!archivo.isDirectory()) n++;
    archivo.close();
  }
  return n;
}

static bool rutaMasAntigua(String &ruta) {
  File carpeta = LittleFS.open("/cola");
  if (!carpeta || !carpeta.isDirectory()) return false;
  String menor;
  for (File archivo = carpeta.openNextFile(); archivo; archivo = carpeta.openNextFile()) {
    String nombre = nombreBase(archivo.name());
    archivo.close();
    if (menor.isEmpty() || nombre < menor) menor = nombre;
  }
  if (menor.isEmpty()) return false;
  ruta = "/cola/" + menor;
  return true;
}

static void guardarEnCola(uint32_t numero, const String &cuerpo) {
  if (archivosEnCola >= COLA_MAX_ARCHIVOS) {
    String vieja;
    if (rutaMasAntigua(vieja) && LittleFS.remove(vieja)) {
      archivosEnCola--;
      lotesDescartados++;
    }
  }
  char ruta[32];
  snprintf(ruta, sizeof(ruta), "/cola/%010lu.json", (unsigned long)numero);
  File archivo = LittleFS.open(ruta, "w");
  if (!archivo) {
    lotesDescartados++;
    return;
  }
  archivo.print(cuerpo);
  archivo.close();
  archivosEnCola++;
}

static void borrarCola() {
  for (int intentos = 0; intentos < COLA_MAX_ARCHIVOS + 10; intentos++) {
    String ruta;
    if (!rutaMasAntigua(ruta) || !LittleFS.remove(ruta)) break;
  }
  archivosEnCola = contarCola();
}

// ---------------------------------------------------------------------------------------------------
// Hora: se sincroniza con el servidor (no depende de internet ni de NTP)
// ---------------------------------------------------------------------------------------------------
static uint64_t epochDe(uint32_t ms) {
  if (!horaSincronizada) return 0;  // 0 = el servidor usará su hora de recepción
  int32_t delta = (int32_t)(ms - millisBase);
  return (uint64_t)(epochBaseMs + delta);
}

static void sincronizarHora() {
  if (!configCompleta() || WiFi.status() != WL_CONNECTED) return;
  uint32_t ahora = millis();
  uint32_t intervalo = horaSincronizada ? HORA_RESINCRONIZAR_MS : 5000;
  if (ultimaSincroMs != 0 && ahora - ultimaSincroMs < intervalo) return;
  ultimaSincroMs = ahora;

  if (!http.begin(tls, cfg.servidor + RUTA_HORA)) return;
  http.setTimeout(5000);
  uint32_t t0 = millis();
  int codigo = http.GET();
  if (codigo == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      int64_t epoch = doc["epoch_ms"] | (int64_t)0;
      if (epoch > 0) {
        uint32_t t1 = millis();
        epochBaseMs = epoch + (t1 - t0) / 2;  // compensa la mitad del viaje de ida y vuelta
        millisBase = t1;
        if (!horaSincronizada) Serial.println(F("[HORA] Sincronizada con el servidor."));
        horaSincronizada = true;
      }
    }
  } else {
    Serial.printf("[HORA] No se pudo sincronizar (%d %s)\n", codigo, codigo < 0 ? HTTPClient::errorToString(codigo).c_str() : "");
  }
  http.end();
}

// ---------------------------------------------------------------------------------------------------
// Envío de lotes
// ---------------------------------------------------------------------------------------------------
static bool listoParaEnviar() {
  return configCompleta() && WiFi.status() == WL_CONNECTED && (int32_t)(millis() - proximoIntentoMs) >= 0;
}

static void programarReintento() {
  proximoIntentoMs = millis() + esperaMs;
  esperaMs = esperaMs * 2 > ESPERA_MAX_MS ? ESPERA_MAX_MS : esperaMs * 2;
}

static Resultado enviarLote(const String &cuerpo) {
  uint8_t mac[32];
  char firma[65];
  protocolo::hmacSha256(cfg.clave, sizeof(cfg.clave), (const uint8_t *)cuerpo.c_str(), cuerpo.length(), mac);
  protocolo::bytesAHex(mac, sizeof(mac), firma);

  if (!http.begin(tls, cfg.servidor + RUTA_INGESTA)) {
    programarReintento();
    return Resultado::Reintentar;
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Gateway", cfg.id);
  http.addHeader("X-Firma", firma);
  int codigo = http.POST((uint8_t *)cuerpo.c_str(), cuerpo.length());
  String respuesta = codigo > 0 ? http.getString() : String();
  http.end();

  if (codigo == 200) {
    esperaMs = 1000;
    Serial.printf("[ENVÍO] %s\n", respuesta.c_str());
    return Resultado::Ok;
  }
  if (codigo == 400 || codigo == 409) {  // el servidor nunca lo aceptará: se descarta para no bloquear la cola
    Serial.printf("[ENVÍO] Rechazado (%d), se descarta: %s\n", codigo, respuesta.c_str());
    return Resultado::Descartar;
  }
  // 401 (clave), 429 (límite), 5xx o error de red: se conserva y se reintenta más tarde
  Serial.printf("[ENVÍO] Falló (%d %s); reintento en %lu ms\n", codigo,
                codigo < 0 ? HTTPClient::errorToString(codigo).c_str() : respuesta.c_str(), (unsigned long)esperaMs);
  programarReintento();
  return Resultado::Reintentar;
}

static String serializarLote(uint32_t numero) {
  JsonDocument doc;
  doc["gw"] = cfg.id;
  doc["lote"] = numero;
  JsonArray tramas = doc["tramas"].to<JsonArray>();
  char hex[17];
  for (size_t i = 0; i < loteN; i++) {
    JsonObject trama = tramas.add<JsonObject>();
    trama["id"] = lote[i].id;
    protocolo::bytesAHex(lote[i].datos, 8, hex);
    trama["d"] = String(hex);
    trama["t"] = epochDe(lote[i].ms);
  }
  twai_status_info_t can = {};
  twai_get_status_info(&can);
  uint32_t recibidas = canRecibidas.load();
  JsonObject estado = doc["estado"].to<JsonObject>();
  estado["fw"] = GATEWAY_FW;
  estado["uptime"] = millis() / 1000;
  estado["rssi"] = WiFi.RSSI();
  estado["can_rx"] = recibidas;
  estado["can_err_rx"] = can.rx_error_counter;
  estado["can_err_tx"] = can.tx_error_counter;
  estado["can_bus_err"] = can.bus_error_count;
  estado["can_estado"] = canTextoEstado(can);
  estado["cola"] = archivosEnCola;
  estado["descartados"] = lotesDescartados + canDescartadas.load();
  estado["heap"] = ESP.getFreeHeap();
  String salida;
  serializeJson(doc, salida);
  return salida;
}

static void cerrarLote() {
  uint32_t numero = tomarNumeroLote();
  String cuerpo = serializarLote(numero);
  loteN = 0;
  // Solo se envía directo si no hay lotes viejos esperando; así el servidor siempre los recibe en orden.
  if (archivosEnCola == 0 && listoParaEnviar()) {
    Resultado resultado = enviarLote(cuerpo);
    if (resultado == Resultado::Ok) return;
    if (resultado == Resultado::Descartar) {
      lotesDescartados++;
      return;
    }
  }
  guardarEnCola(numero, cuerpo);
}

static void enviarPendientes() {
  if (archivosEnCola == 0 || !listoParaEnviar()) return;
  String ruta;
  if (!rutaMasAntigua(ruta)) {
    archivosEnCola = 0;
    return;
  }
  File archivo = LittleFS.open(ruta, "r");
  if (!archivo) {
    LittleFS.remove(ruta);
    archivosEnCola = contarCola();
    return;
  }
  archivo.setTimeout(0);
  String cuerpo = archivo.readString();
  archivo.close();
  Resultado resultado = enviarLote(cuerpo);  // mismo cuerpo exacto: si ya se había recibido, el servidor responde "duplicado"
  if (resultado == Resultado::Reintentar) return;
  if (resultado == Resultado::Descartar) lotesDescartados++;
  LittleFS.remove(ruta);
  if (archivosEnCola > 0) archivosEnCola--;
}

static void recolectar() {
  TramaRx trama;
  while (loteN < LOTE_MAX_TRAMAS && xQueueReceive(colaRx, &trama, 0) == pdTRUE) {
    if (loteN == 0) loteInicioMs = millis();
    lote[loteN++] = trama;
  }
  if (loteN > 0 && (loteN >= LOTE_MAX_TRAMAS || millis() - loteInicioMs >= LOTE_MAX_MS)) cerrarLote();
}

// ---------------------------------------------------------------------------------------------------
// Recepción CAN en su propia tarea: sella la hora de cada trama aunque el loop esté ocupado con HTTPS
// ---------------------------------------------------------------------------------------------------
static void tareaCan(void *) {
  twai_message_t mensaje;
  TramaRx ultima = {};
  for (;;) {
    if (twai_receive(&mensaje, pdMS_TO_TICKS(500)) != ESP_OK) {
      canRecuperar();
      continue;
    }
    if (mensaje.extd || mensaje.rtr || mensaje.data_length_code != 8) {
      canDescartadas++;  // el protocolo solo usa tramas estándar de 8 bytes
      continue;
    }
    TramaRx trama;
    trama.id = (uint16_t)mensaje.identifier;
    memcpy(trama.datos, mensaje.data, 8);
    trama.ms = millis();
    if (trama.id == ultima.id && memcmp(trama.datos, ultima.datos, 8) == 0) continue;  // doble recepción de CAN
    ultima = trama;
    canRecibidas++;
    if (xQueueSend(colaRx, &trama, 0) != pdTRUE) canDescartadas++;
  }
}

// ---------------------------------------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------------------------------------
static void conectarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(cfg.ssid.c_str(), cfg.password.c_str());
  ultimoWifiMs = millis();
  Serial.printf("[WIFI] Conectando a «%s»...\n", cfg.ssid.c_str());
}

static void mantenerWifi() {
  static bool conectadoAntes = false;
  bool conectado = WiFi.status() == WL_CONNECTED;
  if (conectado && !conectadoAntes) {
    Serial.printf("[WIFI] Conectado · IP %s · %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  }
  conectadoAntes = conectado;
  if (conectado || !configCompleta()) return;
  if (millis() - ultimoWifiMs > 20000) {
    WiFi.disconnect();
    conectarWifi();
  }
}

// ---------------------------------------------------------------------------------------------------
// Monitor serie: provisión y diagnóstico
// ---------------------------------------------------------------------------------------------------
static void imprimirAyuda() {
  Serial.println(F("Comandos del gateway:"));
  Serial.println(F("  ssid <nombre de la red>        pass <contraseña del Wi-Fi>"));
  Serial.println(F("  servidor https://<IP>:8443     id <identificador, p. ej. GW-01>"));
  Serial.println(F("  clave <64 hex del tablero>     estado | reiniciar | borrar SI"));
}

static void imprimirEstado() {
  twai_status_info_t can = {};
  twai_get_status_info(&can);
  bool conectado = WiFi.status() == WL_CONNECTED;
  Serial.printf("Gateway %s · firmware %s\n", cfg.id.length() ? cfg.id.c_str() : "(sin id)", GATEWAY_FW);
  Serial.printf("  Servidor: %s\n", cfg.servidor.length() ? cfg.servidor.c_str() : "(sin configurar)");
  Serial.printf("  Wi-Fi: %s · %s\n", cfg.ssid.length() ? cfg.ssid.c_str() : "(sin configurar)",
                conectado ? WiFi.localIP().toString().c_str() : "desconectado");
  Serial.printf("  Clave: %s · hora: %s\n", cfg.tieneClave ? "provisionada" : "FALTA",
                horaSincronizada ? "sincronizada" : "sin sincronizar");
  Serial.printf("  Próximo lote: %lu · en cola: %lu · descartados: %lu\n", (unsigned long)siguienteLote,
                (unsigned long)archivosEnCola, (unsigned long)lotesDescartados);
  Serial.printf("  Bus CAN: %s · recibidas %lu · errores RX %lu TX %lu · bus %lu\n", canTextoEstado(can),
                (unsigned long)canRecibidas.load(), (unsigned long)can.rx_error_counter, (unsigned long)can.tx_error_counter,
                (unsigned long)can.bus_error_count);
  Serial.printf("  Memoria libre: %lu bytes\n", (unsigned long)ESP.getFreeHeap());
}

static bool identificadorValido(const String &texto) {
  if (texto.length() < 2 || texto.length() > 32) return false;
  for (size_t i = 0; i < texto.length(); i++) {
    char c = texto[i];
    if (!isalnum((unsigned char)c) && c != '-' && c != '_') return false;
  }
  return true;
}

static void procesarComando(String linea) {
  linea.trim();
  if (linea.isEmpty()) return;
  int espacio = linea.indexOf(' ');
  String comando = espacio < 0 ? linea : linea.substring(0, espacio);
  String argumento = espacio < 0 ? String() : linea.substring(espacio + 1);
  comando.toLowerCase();
  argumento.trim();

  if (comando == "ssid") {
    cfg.ssid = argumento;
    prefs.putString("ssid", argumento);
    Serial.println(F("[OK] Red guardada. Escribe 'reiniciar' cuando termines de configurar."));
  } else if (comando == "pass") {
    cfg.password = argumento;
    prefs.putString("pass", argumento);
    Serial.println(F("[OK] Contraseña guardada (no se muestra)."));
  } else if (comando == "servidor") {
    while (argumento.endsWith("/")) argumento.remove(argumento.length() - 1);
    if (!argumento.startsWith("https://")) {
      Serial.println(F("[ERROR] El servidor debe empezar con https:// (por ejemplo https://192.168.1.50:8443)."));
      return;
    }
    cfg.servidor = argumento;
    prefs.putString("servidor", argumento);
    Serial.println(F("[OK] Servidor guardado."));
  } else if (comando == "id") {
    if (!identificadorValido(argumento)) {
      Serial.println(F("[ERROR] Usa de 2 a 32 letras, números, guion o guion bajo (igual que en el tablero)."));
      return;
    }
    cfg.id = argumento;
    prefs.putString("id", argumento);
    Serial.println(F("[OK] Identificador guardado."));
  } else if (comando == "clave") {
    uint8_t nueva[protocolo::LONGITUD_CLAVE];
    if (!protocolo::hexABytes(argumento.c_str(), nueva, sizeof(nueva))) {
      Serial.println(F("[ERROR] La clave debe tener 64 caracteres hexadecimales (cópiala del tablero)."));
      return;
    }
    prefs.putBytes("clave", nueva, sizeof(nueva));
    prefs.putUInt("lote_res", 0);  // el servidor reinicia la numeración al rotar la clave
    memset(nueva, 0, sizeof(nueva));
    borrarCola();                  // los lotes pendientes eran de la clave anterior
    Serial.println(F("[OK] Clave guardada; numeración de lotes reiniciada y cola vaciada. Reiniciando..."));
    delay(300);
    ESP.restart();
  } else if (comando == "estado") {
    imprimirEstado();
  } else if (comando == "reiniciar") {
    ESP.restart();
  } else if (comando == "borrar") {
    if (argumento != "SI") {
      Serial.println(F("Para borrar toda la configuración escribe exactamente: borrar SI"));
      return;
    }
    prefs.clear();
    borrarCola();
    Serial.println(F("[OK] Configuración borrada. Reiniciando..."));
    delay(300);
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
    } else if (linea.length() < 200) {
      linea += c;
    }
  }
}

// ---------------------------------------------------------------------------------------------------
static void actualizarLed() {
  uint32_t ms = millis();
  bool encendido;
  if (!configCompleta()) encendido = (ms / 100) % 2;                // parpadeo rápido: falta provisionar
  else if (WiFi.status() != WL_CONNECTED) encendido = (ms / 500) % 2;  // lento: sin Wi-Fi
  else if (archivosEnCola > 0) encendido = (ms / 150) % 4 == 0;     // destellos: vaciando la cola
  else encendido = true;                                            // fijo: todo en orden
  digitalWrite(PIN_LED, encendido ? HIGH : LOW);
}

static void reportar() {
  if (millis() - ultimoReporteMs < 15000) return;
  ultimoReporteMs = millis();
  twai_status_info_t can = {};
  twai_get_status_info(&can);
  Serial.printf("[ESTADO] Wi-Fi %s · CAN %s · recibidas %lu · en cola %lu · próximo lote %lu\n",
                WiFi.status() == WL_CONNECTED ? "OK" : "sin conexión", canTextoEstado(can),
                (unsigned long)canRecibidas.load(), (unsigned long)archivosEnCola, (unsigned long)siguienteLote);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIN_LED, OUTPUT);
  Serial.printf("\n=== Gateway de telemetría · firmware %s ===\n", GATEWAY_FW);

  cargarConfig();
  if (!LittleFS.begin(true)) {
    Serial.println(F("[ERROR] No se pudo montar LittleFS: sin red se perderán los lotes."));
  } else {
    if (!LittleFS.exists("/cola")) LittleFS.mkdir("/cola");
    archivosEnCola = contarCola();
  }

  colaRx = xQueueCreate(128, sizeof(TramaRx));
  Serial.printf("[CAN] Modo: %s\n", TEXTO_MODO_CAN);
  if (!canIniciar(PIN_CAN_TX, PIN_CAN_RX, MODO_CAN_GATEWAY, 4, 64)) {
    Serial.println(F("[ERROR] No arrancó el bus CAN: revisa los pines 16/17 (y el transceptor, si lo usan)."));
  }
  xTaskCreatePinnedToCore(tareaCan, "can_rx", 4096, nullptr, 5, nullptr, 1);

  if (strlen(CA_CERT) < 100) {
    Serial.println(F("[ERROR] Falta el certificado de la CA: corre backend/scripts/generar_certificados.py y recompila."));
  }
  tls.setCACert(CA_CERT);  // solo acepta un servidor con certificado firmado por la CA propia
  http.setReuse(true);     // mantiene la conexión TLS abierta entre lotes

  if (configCompleta()) {
    conectarWifi();
  } else {
    Serial.println(F("[AVISO] Falta configurar el gateway."));
    imprimirAyuda();
  }
  vigilanteIniciar(30);
}

void loop() {
  atenderSerial();
  mantenerWifi();
  sincronizarHora();
  recolectar();
  enviarPendientes();
  actualizarLed();
  reportar();
  vigilanteAlimentar();
  delay(5);
}
