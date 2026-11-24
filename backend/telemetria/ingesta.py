"""
Procesamiento de los lotes que manda el gateway.

Por cada trama: identifica el nodo, reconstruye el contador, verifica el MAC (integridad extremo a extremo
desde el nodo), descifra, decodifica con el catálogo, guarda las mediciones y evalúa alertas.
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar

from . import protocolo
from .alertas import GestorAlertas
from .models import Gateway, Lote, Medicion, Senal

Tipo = EventoSeguridad.Tipo
Sev = EventoSeguridad.Severidad

# Solo se guarda esta información del reporte de estado del gateway (lo demás se ignora).
CLAVES_ESTADO = {
    "fw", "uptime", "rssi", "can_rx", "can_err_rx", "can_err_tx", "can_bus_err", "can_estado",
    "cola", "descartados", "heap",
}


class LoteRechazado(Exception):
    """El número de lote ya se usó con otro contenido, o es anterior al último: posible repetición."""


@dataclass
class ResultadoLote:
    estado: str  # "procesado" o "duplicado"
    lote: int
    aceptadas: int = 0
    rechazadas: int = 0


def procesar_lote(gateway: Gateway, datos: dict, cuerpo_crudo: bytes, ip) -> ResultadoLote:
    numero = datos["lote"]
    huella = hashlib.sha256(cuerpo_crudo).hexdigest()
    with transaction.atomic():
        gw = Gateway.objects.select_for_update().get(pk=gateway.pk)
        previo = Lote.objects.filter(gateway=gw, epoca=gw.epoca, numero=numero).first()
        if previo is not None:
            if previo.huella == huella:
                # El gateway reenvió un lote que ya procesamos (se perdió nuestra respuesta): no se duplica nada.
                return ResultadoLote("duplicado", numero, previo.aceptadas, previo.rechazadas)
            raise LoteRechazado(f"El lote {numero} ya se recibió con otro contenido.")
        if gw.ultimo_lote is not None and numero <= gw.ultimo_lote:
            raise LoteRechazado(f"El lote {numero} es anterior al último recibido ({gw.ultimo_lote}).")

        aceptadas, rechazadas = _ProcesadorLote(gw).procesar(datos["tramas"])

        Lote.objects.create(gateway=gw, epoca=gw.epoca, numero=numero, huella=huella, ip=ip,
                            tramas=len(datos["tramas"]), aceptadas=aceptadas, rechazadas=rechazadas)
        gw.ultimo_lote = numero
        gw.ultimo_contacto = timezone.now()
        gw.estado = _limpiar_estado(datos.get("estado") or {})
        gw.firmware = str(gw.estado.get("fw", ""))[:20]
        gw.save(update_fields=["ultimo_lote", "ultimo_contacto", "estado", "firmware"])
    return ResultadoLote("procesado", numero, aceptadas, rechazadas)


def _limpiar_estado(estado: dict) -> dict:
    limpio = {}
    for clave, valor in estado.items():
        if clave not in CLAVES_ESTADO or isinstance(valor, bool):
            continue
        if isinstance(valor, (int, float)):
            limpio[clave] = valor
        elif isinstance(valor, str):
            limpio[clave] = valor[:40]
    return limpio


class _ProcesadorLote:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.ahora = timezone.now()
        self.ahora_ms = int(self.ahora.timestamp() * 1000)
        self.nodos = {n.numero: n for n in gateway.nodos.all()}
        self.claves = {}
        self.catalogo = {}
        for senal in Senal.objects.all():
            self.catalogo.setdefault(senal.mensaje, []).append(senal)
        self.mediciones = []
        self.nodos_modificados = {}
        self.incidentes = {}
        self.alertas = GestorAlertas()

    def procesar(self, tramas):
        aceptadas = rechazadas = 0
        for trama in tramas:
            if self._procesar_trama(trama):
                aceptadas += 1
            else:
                rechazadas += 1
        Medicion.objects.bulk_create(self.mediciones)
        for nodo in self.nodos_modificados.values():
            nodo.save(update_fields=["ultimo_contador", "ultimo_contacto", "tramas_recibidas",
                                     "tramas_perdidas", "tramas_rechazadas", "reinicios"])
        self._registrar_incidentes()
        return aceptadas, rechazadas

    def _procesar_trama(self, trama) -> bool:
        can_id, datos = trama["id"], trama["d"]
        numero_nodo, mensaje = can_id >> 8, can_id & 0xFF
        nodo = self.nodos.get(numero_nodo)
        if nodo is None:
            self._incidente(Tipo.NODO_DESCONOCIDO, numero_nodo, trama)
            return False
        if not nodo.activo:
            self._incidente(Tipo.NODO_REVOCADO, numero_nodo, trama)
            return False

        resultado = protocolo.verificar_trama(self._claves(nodo), can_id, datos, nodo.ultimo_contador)
        if resultado.veredicto is not protocolo.Veredicto.NUEVA:
            es_replay = resultado.veredicto is protocolo.Veredicto.ANTIGUA
            nodo.tramas_rechazadas += 1
            self.nodos_modificados[nodo.pk] = nodo
            self._incidente(Tipo.REPLAY_TRAMA if es_replay else Tipo.MAC_INVALIDO, numero_nodo, trama)
            return False

        self._actualizar_contadores(nodo, resultado.contador)
        ts = self._marca_tiempo(trama.get("t", 0))
        for senal in self.catalogo.get(mensaje, []):
            valor, calidad = senal.decodificar(resultado.plano)
            self.mediciones.append(
                Medicion(nodo=nodo, senal=senal, valor=valor, calidad=calidad, contador=resultado.contador, ts=ts)
            )
            self.alertas.evaluar(nodo, senal, valor, calidad)
        return True

    def _claves(self, nodo):
        if nodo.pk not in self.claves:
            self.claves[nodo.pk] = protocolo.derivar_claves(nodo.obtener_clave())
        return self.claves[nodo.pk]

    def _actualizar_contadores(self, nodo, contador):
        if nodo.ultimo_contador is not None:
            salto = contador - nodo.ultimo_contador
            if salto > 1:
                # Tras reiniciar, el nodo arranca justo en un múltiplo de la reserva: no son tramas perdidas.
                if contador % protocolo.RESERVA_CONTADOR == 0:
                    nodo.reinicios += 1
                else:
                    nodo.tramas_perdidas += salto - 1
        nodo.ultimo_contador = contador
        nodo.tramas_recibidas += 1
        nodo.ultimo_contacto = self.ahora
        self.nodos_modificados[nodo.pk] = nodo

    def _marca_tiempo(self, t_ms):
        # El gateway sella cada trama con su hora (sincronizada con este servidor). 0 = sin hora.
        if t_ms and 1_600_000_000_000 < t_ms <= self.ahora_ms + 60_000:
            return datetime.fromtimestamp(t_ms / 1000, tz=dt_timezone.utc)
        return self.ahora

    def _incidente(self, tipo, numero_nodo, trama):
        info = self.incidentes.setdefault((tipo, numero_nodo), {"cantidad": 0, "ejemplos": []})
        info["cantidad"] += 1
        if len(info["ejemplos"]) < 3:
            info["ejemplos"].append({"id": f"0x{trama['id']:03X}", "datos": trama["d"].hex().upper()})

    def _registrar_incidentes(self):
        """Un solo evento por tipo y nodo en cada lote, para que un ataque no inunde la bitácora."""
        gw = self.gateway.identificador
        for (tipo, numero_nodo), info in self.incidentes.items():
            critico = tipo in (Tipo.MAC_INVALIDO, Tipo.REPLAY_TRAMA)
            registrar(tipo, severidad=Sev.CRITICA if critico else Sev.ADVERTENCIA, actor=f"{gw}/nodo-{numero_nodo}",
                      detalle={"gateway": gw, "cantidad": info["cantidad"], "ejemplos": info["ejemplos"]})
            nodo = self.nodos.get(numero_nodo)
            if nodo is None:
                continue
            if tipo == Tipo.REPLAY_TRAMA:
                self.alertas.seguridad(f"SEG_REPLAY:nodo={nodo.pk}",
                                       f"{nodo}: se repitieron tramas ya recibidas (posible ataque de repetición)", nodo=nodo)
            elif tipo == Tipo.MAC_INVALIDO and _fallos_mac_en_ventana(nodo, info["cantidad"]):
                self.alertas.seguridad(f"SEG_MAC:nodo={nodo.pk}",
                                       f"{nodo}: tramas con MAC inválido (posible inyección o alteración en el bus)", nodo=nodo)


def _fallos_mac_en_ventana(nodo, cantidad) -> bool:
    clave = f"mac-fallos:{nodo.pk}"
    cache.add(clave, 0, timeout=60)
    try:
        total = cache.incr(clave, cantidad)
    except ValueError:  # la clave expiró entre add() e incr()
        cache.set(clave, cantidad, timeout=60)
        total = cantidad
    return total >= settings.TELEMETRIA["MAC_FALLIDOS_PARA_ALERTA"]
