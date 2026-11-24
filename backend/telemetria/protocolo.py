"""
Protocolo seguro del bus CAN propio (lado servidor).

Es el espejo exacto de firmware/lib/protocolo/protocolo.cpp; si cambias algo aquí, cámbialo allá.

Trama de 8 bytes:   [0..3] datos cifrados | [4..5] contador (16 bits bajos) | [6..7] MAC

  Claves:   K_enc = HMAC-SHA256(K, "CAN-ENC-v1")[:16]      K_mac = HMAC-SHA256(K, "CAN-MAC-v1")
  Cifrado:  datos XOR AES-128(K_enc, id(2) ‖ contador(4) ‖ 0x00 × 10)[:4]
  MAC:      HMAC-SHA256(K_mac, id(2) ‖ contador(4) ‖ datos_cifrados(4))[:2]

El contador de cada nodo es de 32 bits y nunca se repite (sobrevive a reinicios). Solo viajan sus 16 bits
bajos; el receptor reconstruye el valor completo a partir del último contador aceptado, como en AUTOSAR SecOC.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import Enum

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

RESERVA_CONTADOR = 1000  # al reiniciar, el nodo salta al siguiente múltiplo de 1000 (ver firmware)
ETIQUETA_ENC = b"CAN-ENC-v1"
ETIQUETA_MAC = b"CAN-MAC-v1"
CONTADOR_MAX = 0xFFFFFFFF

# Vector de prueba: el firmware calcula la misma trama al arrancar para comprobar que ambos lados coinciden.
VECTOR_PRUEBA = {
    "clave": bytes(range(32)),
    "can_id": 0x110,
    "contador": 1,
    "plano": bytes.fromhex("00EA0226"),  # 23.4 °C y 55.0 %HR
}


@dataclass(frozen=True)
class ClavesNodo:
    enc: bytes
    mac: bytes


class Veredicto(Enum):
    NUEVA = "nueva"  # MAC válido y contador más nuevo que el último aceptado
    ANTIGUA = "antigua"  # MAC válido pero el contador ya se usó: repetición (replay)
    INVALIDA = "invalida"  # el MAC no coincide: trama alterada o falsificada


@dataclass(frozen=True)
class Resultado:
    veredicto: Veredicto
    contador: int | None = None
    plano: bytes | None = None


def derivar_claves(clave_maestra: bytes) -> ClavesNodo:
    if len(clave_maestra) != 32:
        raise ValueError("La clave del nodo debe medir 32 bytes.")
    enc = hmac.new(clave_maestra, ETIQUETA_ENC, hashlib.sha256).digest()[:16]
    mac = hmac.new(clave_maestra, ETIQUETA_MAC, hashlib.sha256).digest()
    return ClavesNodo(enc=enc, mac=mac)


def _flujo(clave_enc: bytes, can_id: int, contador: int) -> bytes:
    bloque = can_id.to_bytes(2, "big") + contador.to_bytes(4, "big") + bytes(10)
    cifrador = Cipher(algorithms.AES(clave_enc), modes.ECB()).encryptor()
    return (cifrador.update(bloque) + cifrador.finalize())[:4]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def calcular_mac(claves: ClavesNodo, can_id: int, contador: int, cifrado: bytes) -> bytes:
    mensaje = can_id.to_bytes(2, "big") + contador.to_bytes(4, "big") + cifrado
    return hmac.new(claves.mac, mensaje, hashlib.sha256).digest()[:2]


def construir_trama(claves: ClavesNodo, can_id: int, contador: int, plano: bytes) -> bytes:
    """Arma una trama igual que el nodo. La usan el simulador y las pruebas."""
    if len(plano) != 4:
        raise ValueError("Los datos de una trama miden 4 bytes.")
    cifrado = _xor(plano, _flujo(claves.enc, can_id, contador))
    return cifrado + (contador & 0xFFFF).to_bytes(2, "big") + calcular_mac(claves, can_id, contador, cifrado)


def _mac_valido(claves: ClavesNodo, can_id: int, contador: int, cifrado: bytes, mac: bytes) -> bool:
    if not 0 <= contador <= CONTADOR_MAX:
        return False
    return hmac.compare_digest(calcular_mac(claves, can_id, contador, cifrado), mac)


def verificar_trama(claves: ClavesNodo, can_id: int, datos: bytes, ultimo_contador: int | None) -> Resultado:
    """Reconstruye el contador completo, verifica el MAC y descifra los datos."""
    if len(datos) != 8:
        return Resultado(Veredicto.INVALIDA)
    cifrado, bajos, mac = datos[:4], int.from_bytes(datos[4:6], "big"), datos[6:]

    if ultimo_contador is None:
        # Nodo recién provisionado: su contador arranca en 0.
        nuevo, viejo = bajos, None
    else:
        candidato = (ultimo_contador & ~0xFFFF) | bajos
        nuevo = candidato if candidato > ultimo_contador else candidato + 0x10000
        viejo = nuevo - 0x10000  # siempre <= ultimo_contador

    if _mac_valido(claves, can_id, nuevo, cifrado, mac):
        return Resultado(Veredicto.NUEVA, nuevo, _xor(cifrado, _flujo(claves.enc, can_id, nuevo)))
    if viejo is not None and _mac_valido(claves, can_id, viejo, cifrado, mac):
        return Resultado(Veredicto.ANTIGUA, viejo)
    return Resultado(Veredicto.INVALIDA)


def trama_vector_prueba() -> bytes:
    v = VECTOR_PRUEBA
    return construir_trama(derivar_claves(v["clave"]), v["can_id"], v["contador"], v["plano"])
