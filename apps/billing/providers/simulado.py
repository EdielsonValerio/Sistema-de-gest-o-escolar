"""Banco simulado: permite treinar o fluxo completo (teste, pagamento, ativação) sem banco de verdade.

Os códigos gerados têm o formato real, mas **não são pagáveis**.
"""
from decimal import Decimal

from django.utils import timezone

from ..codigos import barras_para_linha, brcode, montar_barras, qrcode_base64
from ..models import TituloSimulado
from .base import Emitido, ErroProvedor, Provedor, Situacao, Titulo


class Simulado(Provedor):
    nome = "Banco simulado"

    def verificar(self):
        return None

    def _emitido(self, id_externo, valor, vencimento, descricao=""):
        n = int(id_externo.split("-")[1])
        barras = montar_barras(banco="999", valor_centavos=int(valor * 100), vencimento=vencimento,
                               campo_livre=f"{n:025d}")
        pix = brcode(chave="00000000-0000-4000-8000-000000000000", valor=valor, nome="BOLETO SIMULADO",
                     cidade="SIMULADO", txid=f"SIM{n}")
        return Emitido(id_externo=id_externo, nosso_numero=f"{n:011d}", linha_digitavel=barras_para_linha(barras),
                       codigo_barras=barras, pix_copia_cola=pix, pix_qrcode=qrcode_base64(pix))

    def emitir(self, titulo: Titulo):
        if titulo.valor < Decimal("1.00"):
            raise ErroProvedor("O valor mínimo do boleto é R$ 1,00.")
        t = TituloSimulado.objects.create(id_externo="SIM-0", valor=titulo.valor, vencimento=titulo.vencimento)
        t.id_externo = f"SIM-{t.pk}"
        t.save(update_fields=["id_externo"])
        return self._emitido(t.id_externo, titulo.valor, titulo.vencimento)

    def _titulo(self, id_externo):
        t = TituloSimulado.objects.filter(id_externo=id_externo).first()
        if t is None:
            raise ErroProvedor("Boleto não encontrado no banco simulado.")
        return t

    def consultar(self, id_externo):
        t = self._titulo(id_externo)
        if t.cancelado:
            return Situacao("CANCELADA")
        if t.pago:
            return Situacao("PAGA", valor_pago=t.valor_pago, data_pagamento=t.pago_em)
        return Situacao("PENDENTE")

    def cancelar(self, id_externo):
        t = self._titulo(id_externo)
        if t.pago:
            raise ErroProvedor("O boleto já foi pago e não pode ser cancelado.")
        t.cancelado = True
        t.save(update_fields=["cancelado"])

    def alterar(self, id_externo, *, valor, vencimento):
        t = self._titulo(id_externo)
        if t.pago or t.cancelado:
            raise ErroProvedor("Boleto pago ou cancelado não pode ser alterado.")
        t.valor, t.vencimento = valor, vencimento
        t.save(update_fields=["valor", "vencimento"])
        return self._emitido(id_externo, valor, vencimento)

    # ---- só existe no simulado: faz de conta que o pagador pagou
    @staticmethod
    def simular_pagamento(id_externo, valor=None, data=None):
        t = TituloSimulado.objects.get(id_externo=id_externo)
        t.pago, t.valor_pago, t.pago_em = True, valor if valor is not None else t.valor, data or timezone.localdate()
        t.save()
