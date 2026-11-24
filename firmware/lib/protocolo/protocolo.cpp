#include "protocolo.h"

#include <mbedtls/aes.h>
#include <mbedtls/md.h>
#include <string.h>

namespace protocolo {

bool hmacSha256(const uint8_t *clave, size_t longitudClave, const uint8_t *datos, size_t longitudDatos, uint8_t salida[32]) {
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  return info != nullptr && mbedtls_md_hmac(info, clave, longitudClave, datos, longitudDatos, salida) == 0;
}

bool derivarClaves(const uint8_t maestra[LONGITUD_CLAVE], Claves &claves) {
  static const char ETIQUETA_ENC[] = "CAN-ENC-v1";
  static const char ETIQUETA_MAC[] = "CAN-MAC-v1";
  uint8_t temporal[32];
  bool ok = hmacSha256(maestra, LONGITUD_CLAVE, (const uint8_t *)ETIQUETA_ENC, sizeof(ETIQUETA_ENC) - 1, temporal);
  memcpy(claves.enc, temporal, sizeof(claves.enc));
  ok = ok && hmacSha256(maestra, LONGITUD_CLAVE, (const uint8_t *)ETIQUETA_MAC, sizeof(ETIQUETA_MAC) - 1, claves.mac);
  memset(temporal, 0, sizeof(temporal));
  return ok;
}

// id (2 bytes) | contador (4 bytes), en big-endian
static void escribirEncabezado(uint8_t *destino, uint16_t id, uint32_t contador) {
  destino[0] = id >> 8;
  destino[1] = id & 0xFF;
  destino[2] = contador >> 24;
  destino[3] = (contador >> 16) & 0xFF;
  destino[4] = (contador >> 8) & 0xFF;
  destino[5] = contador & 0xFF;
}

bool armarTrama(const Claves &claves, uint16_t id, uint32_t contador, const uint8_t plano[4], uint8_t trama[8]) {
  // 1) Cifrado: XOR con AES-128 de (id | contador | ceros). Nunca se repite porque el contador nunca se repite.
  uint8_t bloque[16] = {0};
  uint8_t flujo[16];
  escribirEncabezado(bloque, id, contador);
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  bool ok = mbedtls_aes_setkey_enc(&aes, claves.enc, 128) == 0 &&
            mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, bloque, flujo) == 0;
  mbedtls_aes_free(&aes);
  if (!ok) return false;
  for (int i = 0; i < 4; i++) trama[i] = plano[i] ^ flujo[i];

  // 2) Los 16 bits bajos del contador viajan en claro para que el receptor reconstruya el valor completo.
  trama[4] = (contador >> 8) & 0xFF;
  trama[5] = contador & 0xFF;

  // 3) MAC sobre id | contador completo | datos cifrados (primero se cifra y luego se autentica).
  uint8_t mensaje[10];
  escribirEncabezado(mensaje, id, contador);
  memcpy(mensaje + 6, trama, 4);
  uint8_t mac[32];
  if (!hmacSha256(claves.mac, sizeof(claves.mac), mensaje, sizeof(mensaje), mac)) return false;
  trama[6] = mac[0];
  trama[7] = mac[1];
  return true;
}

static int valorHex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool hexABytes(const char *hex, uint8_t *salida, size_t longitud) {
  if (strlen(hex) != longitud * 2) return false;
  for (size_t i = 0; i < longitud; i++) {
    int alto = valorHex(hex[2 * i]);
    int bajo = valorHex(hex[2 * i + 1]);
    if (alto < 0 || bajo < 0) return false;
    salida[i] = (uint8_t)((alto << 4) | bajo);
  }
  return true;
}

void bytesAHex(const uint8_t *datos, size_t longitud, char *salida) {
  static const char DIGITOS[] = "0123456789ABCDEF";
  for (size_t i = 0; i < longitud; i++) {
    salida[2 * i] = DIGITOS[datos[i] >> 4];
    salida[2 * i + 1] = DIGITOS[datos[i] & 0x0F];
  }
  salida[2 * longitud] = '\0';
}

bool autoprueba(Print &salida) {
  uint8_t maestra[LONGITUD_CLAVE];
  for (size_t i = 0; i < LONGITUD_CLAVE; i++) maestra[i] = (uint8_t)i;
  static const uint8_t PLANO[4] = {0x00, 0xEA, 0x02, 0x26};  // 23.4 °C y 55.0 %HR
  static const uint8_t ESPERADA[8] = {0xD9, 0x8F, 0x7B, 0xEA, 0x00, 0x01, 0xF8, 0xFC};
  Claves claves;
  uint8_t trama[8] = {0};
  bool ok = derivarClaves(maestra, claves) && armarTrama(claves, 0x110, 1, PLANO, trama) && memcmp(trama, ESPERADA, 8) == 0;
  char hex[17];
  bytesAHex(trama, sizeof(trama), hex);
  salida.printf("[PRUEBA] Criptografía %s · trama %s (esperada D98F7BEA0001F8FC)\n", ok ? "OK" : "FALLÓ", hex);
  return ok;
}

}  // namespace protocolo
