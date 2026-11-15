"""Rotina de retenção: remove dados de segurança e de IA que já cumpriram sua finalidade.

NÃO apaga trilhas de auditoria (RegistroAuditoria é imutável e preserva rastreabilidade legal) nem documentos
escolares/financeiros. Agende diariamente (cron). Prazos ajustáveis por opção.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import TentativaLogin, TokenConta
from apps.assistente.models import Conversa
from apps.audit.models import EventoSeguranca


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--dias-tentativas", type=int, default=2, help="Falhas de login (padrão 2 dias).")
        parser.add_argument("--dias-tokens", type=int, default=30, help="Tokens usados/expirados (padrão 30).")
        parser.add_argument("--dias-ia", type=int, default=180, help="Conversas do assistente de IA (padrão 180).")
        parser.add_argument("--dias-eventos", type=int, default=400, help="Eventos de segurança (padrão 400).")
        parser.add_argument("--simular", action="store_true", help="Só mostra o que seria removido.")

    def handle(self, *a, **o):
        agora = timezone.now()
        alvos = {
            "tentativas de login": TentativaLogin.objects.filter(criado_em__lt=agora - timedelta(days=o["dias_tentativas"])),
            "tokens de conta vencidos": TokenConta.objects.filter(expira_em__lt=agora - timedelta(days=o["dias_tokens"])),
            "conversas de IA": Conversa.objects.filter(atualizada_em__lt=agora - timedelta(days=o["dias_ia"])),
            "eventos de segurança": EventoSeguranca.objects.filter(criado_em__lt=agora - timedelta(days=o["dias_eventos"])),
        }
        for nome, qs in alvos.items():
            n = qs.count()
            if not o["simular"]:
                qs.delete()
            self.stdout.write(f"{nome}: {n} {'a remover' if o['simular'] else 'removido(s)'}")
