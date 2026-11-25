"""
Simulador del nodo de sensores + gateway, para trabajar el tablero sin hardware y para la demo de ataques.

  python manage.py simular --url https://127.0.0.1:8443                # tráfico normal
  python manage.py simular --falla ac                                  # temperatura alta y ventilador detenido
  python manage.py simular --ataque replay                             # prueba las defensas

No lo corras al mismo tiempo que el gateway real: ambos usarían los mismos contadores.
"""
import hashlib
import hmac
import json
import math
import random
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from telemetria import protocolo
from telemetria.models import Gateway

MSG_AC_AMBIENTE, MSG_AC_VENTILADOR = 0x10, 0x11
MSG_SAN_TANQUE, MSG_SAN_ESTADO, MSG_SAN_FUGA = 0x20, 0x21, 0x22
MSG_LUZ, MSG_ALIMENTACION, MSG_ESTADO_NODO = 0x30, 0x40, 0xF0
TANQUE_ALTURA_MM, TANQUE_ZONA_MUERTA_MM = 300, 30


def _i16(valor):
    return int(round(valor)).to_bytes(2, "big", signed=True)


def _u16(valor):
    return max(0, min(0xFFFE, int(round(valor)))).to_bytes(2, "big")


def _u8(valor):
    return max(0, min(0xFE, int(round(valor)))).to_bytes(1, "big")


def _json(contenido):
    try:
        return json.loads(contenido or b"{}")
    except ValueError:
        return {"texto": contenido[:200].decode(errors="replace")}


class SensoresSimulados:
    """Lecturas plausibles de los sensores del nodo, con fallas opcionales para probar las alertas."""

    def __init__(self, falla):
        self.inicio = time.monotonic()
        self.falla = falla
        self.nivel = 85.0
        self.usos = 0
        self.ocupado = False

    def mensajes(self):
        t = time.monotonic() - self.inicio
        ruido = random.uniform

        if self.falla == "sensor":
            ambiente = b"\x7f\xff\xff\xff"  # el DHT11 no responde: el nodo manda "sin dato"
        else:
            temp = 31.5 + ruido(-0.3, 0.3) if self.falla == "ac" else 23.5 + 1.5 * math.sin(t / 90) + ruido(-0.2, 0.2)
            humedad = 50 + 8 * math.sin(t / 120) + ruido(-1, 1)
            ambiente = _i16(temp * 10) + _u16(humedad * 10)
        rpm = 0 if self.falla == "ac" else 1450 + ruido(-40, 40)

        if self.falla == "tanque":
            self.nivel = 12.0
        else:
            self.nivel = self.nivel - 0.15 if self.nivel > 15 else 90.0  # se vacía poco a poco y se rellena
        distancia = TANQUE_ZONA_MUERTA_MM + (100 - self.nivel) / 100 * (TANQUE_ALTURA_MM - TANQUE_ZONA_MUERTA_MM)

        # Igual que el firmware: el PIR detecta a alguien de los 10 a los 35 s de cada minuto; el sanitario sigue
        # ocupado hasta 15 s sin presencia y, al liberarse, cuenta un uso.
        fase = t % 60
        presencia = 10 <= fase < 35
        if presencia:
            self.ocupado = True
        elif self.ocupado and fase >= 50:
            self.usos += 1
            self.ocupado = False

        fuga = self.falla == "fuga"
        humedad_piso = 72 + ruido(-2, 2) if fuga else 4 + ruido(-1, 1)
        fallas = 0x0001 if self.falla == "sensor" else 0

        return [
            (MSG_AC_AMBIENTE, ambiente),
            (MSG_AC_VENTILADOR, _u16(rpm) + b"\x00\x00"),
            (MSG_SAN_TANQUE, _u16(distancia) + _u16(self.nivel * 10)),
            (MSG_SAN_ESTADO, _u8(self.ocupado) + _u8(presencia) + _u16(self.usos)),
            (MSG_SAN_FUGA, _u16(humedad_piso * 10) + _u8(fuga) + b"\x00"),
            (MSG_LUZ, _u8(80 + ruido(-3, 3)) + b"\x00\x00\x00"),
            (MSG_ALIMENTACION, _u16(5020 + ruido(-30, 30)) + b"\x00\x00"),
            (MSG_ESTADO_NODO, _u16(t // 60) + _u16(fallas)),
        ]


class Command(BaseCommand):
    help = ("Simula el nodo de sensores y el gateway enviando lotes firmados al servidor. "
            "Con --ataque prueba las defensas: repetición, MAC alterado, firma falsa o lote manipulado.")

    def add_arguments(self, parser):
        parser.add_argument("--url", default="https://127.0.0.1:8443", help="Dirección del servidor Django.")
        parser.add_argument("--ca", default="certs/ca.crt", help="Certificado de la CA propia (para HTTPS).")
        parser.add_argument("--gateway", default="GW-01")
        parser.add_argument("--intervalo", type=float, default=2.0, help="Segundos entre lotes.")
        parser.add_argument("--lotes", type=int, default=0, help="Cuántos lotes enviar (0 = sin fin).")
        parser.add_argument("--ataque", choices=["ninguno", "replay", "mac", "firma", "lote"], default="ninguno",
                            help="Se lanza una vez, en el cuarto lote.")
        parser.add_argument("--falla", choices=["ninguna", "ac", "tanque", "fuga", "sensor"], default="ninguna")

    def handle(self, *args, **opciones):
        try:
            gateway = Gateway.objects.get(identificador=opciones["gateway"])
        except Gateway.DoesNotExist:
            raise CommandError(f"No existe el gateway {opciones['gateway']}. Ejecuta primero: python manage.py inicializar")
        nodos = list(gateway.nodos.filter(activo=True).order_by("numero"))
        if not nodos:
            raise CommandError("El gateway no tiene nodos activos.")

        self.gateway = gateway
        self.url = opciones["url"].rstrip("/") + "/api/v1/ingesta/"
        self.contexto = self._contexto_tls(opciones["url"], opciones["ca"])
        self.clave_gw = gateway.obtener_clave()
        self.claves = {n.numero: protocolo.derivar_claves(n.obtener_clave()) for n in nodos}
        self.contadores = {n.numero: 0 if n.ultimo_contador is None else n.ultimo_contador + 1 for n in nodos}
        self.enviadas = 0
        lote = 0 if gateway.ultimo_lote is None else gateway.ultimo_lote + 1
        sensores = SensoresSimulados(opciones["falla"])

        self.stdout.write(f"Simulando {gateway.identificador} con {len(nodos)} nodo(s) → {self.url}   (Ctrl+C para salir)")
        anteriores, contador_lotes = [], 0
        try:
            while opciones["lotes"] == 0 or contador_lotes < opciones["lotes"]:
                tramas = self._tramas(nodos, sensores)
                if contador_lotes == 3 and opciones["ataque"] != "ninguno":
                    lote = self._atacar(opciones["ataque"], lote, tramas, anteriores)
                else:
                    self._enviar_lote(lote, tramas)
                    lote += 1
                anteriores = tramas
                contador_lotes += 1
                time.sleep(opciones["intervalo"])
        except KeyboardInterrupt:
            self.stdout.write("\nSimulación detenida.")

    # --- Construcción y envío -------------------------------------------------------------------

    def _tramas(self, nodos, sensores):
        ahora_ms = int(time.time() * 1000)
        mensajes = sensores.mensajes()
        tramas = []
        for nodo in nodos:
            for mensaje, plano in mensajes:
                can_id = (nodo.numero << 8) | mensaje
                contador = self.contadores[nodo.numero]
                self.contadores[nodo.numero] += 1
                datos = protocolo.construir_trama(self.claves[nodo.numero], can_id, contador, plano)
                tramas.append({"id": can_id, "d": datos.hex().upper(), "t": ahora_ms})
        self.enviadas += len(tramas)
        return tramas

    def _cuerpo(self, lote, tramas, **estado_extra):
        estado = {"fw": "simulador", "rssi": -55, "can_rx": self.enviadas, "can_err_rx": 0, "can_err_tx": 0,
                  "can_bus_err": 0, "can_estado": "ACTIVO", "cola": 0, "descartados": 0, **estado_extra}
        datos = {"gw": self.gateway.identificador, "lote": lote, "tramas": tramas, "estado": estado}
        return json.dumps(datos, separators=(",", ":")).encode()

    def _enviar_lote(self, lote, tramas, firma=None, etiqueta="", **estado_extra):
        cuerpo = self._cuerpo(lote, tramas, **estado_extra)
        firma = firma or hmac.new(self.clave_gw, cuerpo, hashlib.sha256).hexdigest()
        peticion = urllib.request.Request(self.url, data=cuerpo, method="POST", headers={
            "Content-Type": "application/json", "X-Gateway": self.gateway.identificador, "X-Firma": firma})
        try:
            with urllib.request.urlopen(peticion, timeout=10, context=self.contexto) as respuesta:
                codigo, datos = respuesta.status, _json(respuesta.read())
        except urllib.error.HTTPError as error:
            codigo, datos = error.code, _json(error.read())
        except (urllib.error.URLError, OSError) as error:
            codigo, datos = None, {"error": str(getattr(error, "reason", error))}
        self._informar(lote, codigo, datos, etiqueta)

    def _informar(self, lote, codigo, datos, etiqueta):
        prefijo = f"lote {lote:>5}" + (f" [{etiqueta}]" if etiqueta else "")
        if codigo == 200:
            self.stdout.write(self.style.SUCCESS(
                f"{prefijo} → 200 {datos.get('estado')}: {datos.get('aceptadas')} aceptadas, {datos.get('rechazadas')} rechazadas"))
        elif codigo is None:
            self.stdout.write(self.style.ERROR(f"{prefijo} → sin conexión: {datos.get('error')}"))
        else:
            motivo = datos.get("error") or datos.get("detail") or datos
            self.stdout.write(self.style.WARNING(f"{prefijo} → {codigo} {motivo}"))

    def _contexto_tls(self, url, ca):
        if not url.startswith("https://"):
            self.stdout.write(self.style.WARNING("Aviso: enviando sin HTTPS (solo para desarrollo con runserver)."))
            return None
        ruta = Path(ca)
        if not ruta.is_absolute():
            ruta = settings.BASE_DIR / ruta
        if not ruta.exists():
            raise CommandError(f"No encuentro el certificado de la CA en {ruta}. "
                               "Genera los certificados con: python scripts/generar_certificados.py")
        return ssl.create_default_context(cafile=str(ruta))

    # --- Ataques para la demo ---------------------------------------------------------------------

    def _atacar(self, tipo, lote, tramas, anteriores):
        aviso = self.style.WARNING
        if tipo == "replay":
            self.stdout.write(aviso("⚠ Ataque: se reinyecta en el bus una trama que ya se había enviado (replay)."))
            self._enviar_lote(lote, tramas + anteriores[:1], etiqueta="replay")
            return lote + 1
        if tipo == "mac":
            self.stdout.write(aviso("⚠ Ataque: se inyectan 5 tramas con los datos alterados (MAC inválido)."))
            falsas = []
            for trama in tramas[:5]:
                datos = bytearray.fromhex(trama["d"])
                datos[0] ^= 0x01
                falsas.append({**trama, "d": datos.hex().upper()})
            self._enviar_lote(lote, tramas + falsas, etiqueta="MAC alterado")
            return lote + 1
        if tipo == "firma":
            self.stdout.write(aviso("⚠ Ataque: lote firmado con una clave falsa."))
            self._enviar_lote(lote, tramas, firma="00" * 32, etiqueta="firma falsa")
            self.stdout.write("  El gateway legítimo reenvía el mismo lote con su firma correcta:")
            self._enviar_lote(lote, tramas, etiqueta="legítimo")
            return lote + 1
        self.stdout.write(aviso("⚠ Ataque: se reutiliza el número del lote anterior con otro contenido."))
        self._enviar_lote(lote - 1, tramas, etiqueta="lote manipulado", uptime=0)
        self.stdout.write("  El gateway legítimo sigue con el siguiente número:")
        self._enviar_lote(lote, tramas, etiqueta="legítimo")
        return lote + 1
