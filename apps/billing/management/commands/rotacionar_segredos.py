"""Rotação da chave do cofre de segredos (Fernet).

Procedimento: 1) gere uma chave nova; 2) em BILLING_CRYPTO_KEY coloque a nova ANTES da antiga, separadas por vírgula;
3) reinicie o sistema; 4) rode este comando; 5) depois de conferir, remova a chave antiga do .env.
Cobre: chaves de API e token de webhook do banco, chave da IA e segredos de MFA. Idempotente.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import MfaDispositivo
from apps.assistente.models import ConfiguracaoIA
from apps.billing import crypto
from apps.billing.models import ConfiguracaoBancaria


class Command(BaseCommand):
    help = __doc__

    @transaction.atomic
    def handle(self, *a, **o):
        total = 0
        for model, campos in ((ConfiguracaoBancaria, ("api_key_cifrada", "webhook_token_cifrado")),
                              (ConfiguracaoIA, ("api_key_cifrada",)), (MfaDispositivo, ("segredo_cifrado",))):
            for obj in model.objects.all():
                for campo in campos:
                    antigo = getattr(obj, campo)
                    if antigo:
                        try:
                            setattr(obj, campo, crypto.recifrar(antigo))
                        except Exception as exc:  # chave antiga ausente: nada é gravado (transação)
                            raise SystemExit(f"{model.__name__}#{obj.pk}.{campo}: não decifrado ({type(exc).__name__}). "
                                             "Mantenha a chave antiga em BILLING_CRYPTO_KEY e tente de novo.")
                obj.save(update_fields=list(campos))
                total += 1
        self.stdout.write(self.style.SUCCESS(f"{total} registro(s) regravado(s) com a chave mais recente."))
