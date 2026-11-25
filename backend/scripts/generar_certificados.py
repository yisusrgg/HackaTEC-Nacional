"""
Genera una CA propia y el certificado HTTPS del servidor Django (sin depender de OpenSSL ni de terceros).

  python scripts/generar_certificados.py --ip 192.168.1.50

Crea en backend/certs/:
  ca.key        llave privada de la CA  → guárdala FUERA del servidor después de generar los certificados
  ca.crt        certificado de la CA     → va en el firmware del gateway y en los navegadores
  servidor.key  llave privada HTTPS del servidor
  servidor.crt  certificado HTTPS del servidor (válido para localhost, 127.0.0.1 y las IP indicadas)
y escribe firmware/gateway/ca_cert.h (sketch del gateway) para que compruebe que habla con el servidor legítimo.

Si la IP de la laptop cambia en el evento, vuelve a correrlo con la IP nueva: reutiliza la misma CA,
así que no hay que volver a compilar el firmware.
"""
import argparse
import datetime as dt
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

BASE = Path(__file__).resolve().parent.parent
RAIZ = BASE.parent
ORGANIZACION = "HackaTec 2026 Telemetria"
SOLO_FIRMA = dict(content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                  encipher_only=False, decipher_only=False)


def _ip_local():
    """IP de la laptop en la red local (no envía paquetes; solo consulta la ruta)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return ipaddress.ip_address(s.getsockname()[0])
    except OSError:
        return None


def _guardar_llave(llave, ruta):
    ruta.write_bytes(llave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))


def _guardar_cert(cert, ruta):
    ruta.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def crear_ca(dias):
    llave = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "MX"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZACION),
        x509.NameAttribute(NameOID.COMMON_NAME, "CA Telemetria de Carroceria"),
    ])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))
        .not_valid_after(ahora + dt.timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, **SOLO_FIRMA), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(llave.public_key()), critical=False)
        .sign(llave, hashes.SHA256())
    )
    return llave, cert


def crear_servidor(ca_llave, ca_cert, ips, nombres, dias):
    llave = ec.generate_private_key(ec.SECP256R1())
    ahora = dt.datetime.now(dt.timezone.utc)
    alternativos = [x509.DNSName(n) for n in nombres] + [x509.IPAddress(ip) for ip in ips]
    # mbedTLS (ESP32) compara el host como texto contra los nombres DNS: cada IP va también como DNSName.
    alternativos += [x509.DNSName(str(ip)) for ip in ips]
    ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZACION),
            x509.NameAttribute(NameOID.COMMON_NAME, str(ips[-1])),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))
        .not_valid_after(ahora + dt.timedelta(days=dias))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, **SOLO_FIRMA), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(llave.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
        .sign(ca_llave, hashes.SHA256())
    )
    return llave, cert


def escribir_header(ca_cert, ruta):
    pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode().strip()
    lineas = "\n".join(f'    "{linea}\\n"' for linea in pem.splitlines())
    fecha = dt.date.today().isoformat()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        "#pragma once\n"
        f"// Certificado de la CA propia, generado por backend/scripts/generar_certificados.py ({fecha}).\n"
        "// Es público: el gateway lo usa para comprobar que habla con el servidor legítimo (evita un intermediario).\n"
        "static const char CA_CERT[] =\n"
        f"{lineas};\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Genera la CA propia y el certificado HTTPS del servidor.")
    parser.add_argument("--ip", action="append", default=[], help="IP de la laptop servidor en la red local (se puede repetir).")
    parser.add_argument("--dns", action="append", default=[], help="Nombre adicional del servidor (se puede repetir).")
    parser.add_argument("--salida", default=str(BASE / "certs"), help="Carpeta de salida.")
    parser.add_argument("--header", default=str(RAIZ / "firmware" / "gateway" / "ca_cert.h"),
                        help="Header para el sketch del gateway.")
    parser.add_argument("--nueva-ca", action="store_true", help="Crear una CA nueva aunque ya exista (obliga a recompilar el gateway).")
    args = parser.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)

    ips = [ipaddress.ip_address("127.0.0.1")]
    for texto in args.ip:
        ip = ipaddress.ip_address(texto)
        if ip not in ips:
            ips.append(ip)
    if not args.ip:
        detectada = _ip_local()
        if detectada and detectada not in ips:
            ips.append(detectada)
            print(f"• No indicaste --ip; uso la IP detectada {detectada}.")
    nombres = ["localhost", *args.dns]

    ruta_ca_llave, ruta_ca_cert = salida / "ca.key", salida / "ca.crt"
    if ruta_ca_llave.exists() and ruta_ca_cert.exists() and not args.nueva_ca:
        ca_llave = serialization.load_pem_private_key(ruta_ca_llave.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ruta_ca_cert.read_bytes())
        print("• Reutilizando la CA existente.")
    else:
        ca_llave, ca_cert = crear_ca(dias=3 * 365)
        _guardar_llave(ca_llave, ruta_ca_llave)
        _guardar_cert(ca_cert, ruta_ca_cert)
        print("✔ CA nueva creada.")

    llave, cert = crear_servidor(ca_llave, ca_cert, ips, nombres, dias=397)
    _guardar_llave(llave, salida / "servidor.key")
    _guardar_cert(cert, salida / "servidor.crt")
    escribir_header(ca_cert, Path(args.header))

    print(f"✔ Certificado del servidor para: {', '.join(nombres + [str(ip) for ip in ips])}")
    print(f"✔ Header del firmware: {args.header}")
    print("\nSiguientes pasos:")
    print("  1. Arranca el servidor con HTTPS:")
    print("     uvicorn config.asgi:application --host 0.0.0.0 --port 8443 "
          "--ssl-keyfile certs/servidor.key --ssl-certfile certs/servidor.crt")
    print("  2. En el .env: DJANGO_HTTPS=1 y agrega la IP a DJANGO_ALLOWED_HOSTS.")
    print("  3. Para que el navegador confíe en el tablero, importa certs/ca.crt como CA raíz de confianza.")
    print("  4. Compila y sube el firmware del gateway (ya incluye la CA).")
    print("  5. Mueve certs/ca.key a una memoria USB: con ella cualquiera podría emitir certificados válidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
