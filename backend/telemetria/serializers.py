from rest_framework import serializers


class TramaSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=0, max_value=0x7FF)  # ID estándar de 11 bits
    d = serializers.RegexField(r"^[0-9a-fA-F]{16}$")  # los 8 bytes de la trama en hexadecimal
    t = serializers.IntegerField(min_value=0, required=False, default=0)  # hora de recepción (epoch ms)

    def validate_d(self, valor):
        return bytes.fromhex(valor)


class LoteSerializer(serializers.Serializer):
    gw = serializers.CharField(max_length=32)
    lote = serializers.IntegerField(min_value=0, max_value=0xFFFFFFFF)
    tramas = serializers.ListField(child=TramaSerializer(), max_length=200)
    estado = serializers.DictField(required=False, default=dict)
