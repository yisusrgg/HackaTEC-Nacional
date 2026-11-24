#pragma once
#include <Arduino.h>

// Protocolo seguro del bus CAN propio. Es el espejo de backend/telemetria/protocolo.py:
// si cambias algo aquí, cámbialo allá (y en docs/03-protocolo-can.md).
//
// Trama de 8 bytes:  [0..3] datos cifrados | [4..5] contador (16 bits bajos) | [6..7] MAC
//   K_enc   = HMAC-SHA256(K, "CAN-ENC-v1")[0..15]        K_mac = HMAC-SHA256(K, "CAN-MAC-v1")
//   cifrado = datos XOR AES-128(K_enc, id(2) | contador(4) | 0x00 x 10)[0..3]
//   MAC     = HMAC-SHA256(K_mac, id(2) | contador(4) | cifrado(4))[0..1]
namespace protocolo {

constexpr uint32_t RESERVA_CONTADOR = 1000;  // igual que RESERVA_CONTADOR en el backend
constexpr size_t LONGITUD_CLAVE = 32;

// Mensajes (byte bajo del ID CAN). ID completo = (número de nodo << 8) | mensaje.
constexpr uint8_t MSG_AC_AMBIENTE = 0x10;    // [0-1] temperatura int16 x0.1 °C · [2-3] humedad uint16 x0.1 %HR
constexpr uint8_t MSG_AC_VENTILADOR = 0x11;  // [0-1] rpm uint16
constexpr uint8_t MSG_SAN_TANQUE = 0x20;     // [0-1] distancia uint16 mm · [2-3] nivel uint16 x0.1 %
constexpr uint8_t MSG_SAN_ESTADO = 0x21;     // [0] puerta abierta · [1] presencia · [2-3] usos uint16
constexpr uint8_t MSG_SAN_FUGA = 0x22;       // [0-1] humedad del piso uint16 x0.1 % · [2] fuga
constexpr uint8_t MSG_LUZ_ZONAS = 0x30;      // [0] zona 1 % · [1] zona 2 % · [2] zona 3 encendida
constexpr uint8_t MSG_ALIMENTACION = 0x40;   // [0-1] voltaje uint16 mV
constexpr uint8_t MSG_ESTADO_NODO = 0xF0;    // [0-1] minutos encendido · [2-3] bits de fallas

// Valores que se mandan cuando el sensor no respondió.
constexpr int16_t SIN_DATO_I16 = 0x7FFF;
constexpr uint16_t SIN_DATO_U16 = 0xFFFF;
constexpr uint8_t SIN_DATO_U8 = 0xFF;

struct Claves {
  uint8_t enc[16];
  uint8_t mac[32];
};

inline uint16_t idCan(uint8_t nodo, uint8_t mensaje) { return (uint16_t(nodo) << 8) | mensaje; }
inline void escribirU16(uint8_t *destino, uint16_t valor) {
  destino[0] = valor >> 8;
  destino[1] = valor & 0xFF;
}
inline void escribirI16(uint8_t *destino, int16_t valor) { escribirU16(destino, (uint16_t)valor); }

bool hmacSha256(const uint8_t *clave, size_t longitudClave, const uint8_t *datos, size_t longitudDatos, uint8_t salida[32]);
bool derivarClaves(const uint8_t maestra[LONGITUD_CLAVE], Claves &claves);
bool armarTrama(const Claves &claves, uint16_t id, uint32_t contador, const uint8_t plano[4], uint8_t trama[8]);
bool hexABytes(const char *hex, uint8_t *salida, size_t longitud);
void bytesAHex(const uint8_t *datos, size_t longitud, char *salida);  // salida: 2 * longitud + 1 caracteres

// Calcula la trama del vector de prueba y la compara con la del backend (python manage.py vector_prueba).
bool autoprueba(Print &salida);

}  // namespace protocolo
