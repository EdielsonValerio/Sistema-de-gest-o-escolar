"""Alertas de segurança: resume os eventos recentes e avisa (e-mail aos administradores do sistema) quando um limite
é ultrapassado. Agende de hora em hora (cron). Sem e-mail configurado, apenas imprime (e sai com código 2)."""
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit.models import EventoSeguranca, RegistroAuditoria

# evento -> quantidade a partir da qual vira alerta (por escola, na janela)
LIMITES = {"LOGIN_BLOQUEADO": 3, "LOGIN_FALHA": 30, "MFA_FALHA": 5, "WEBHOOK_FALHA": 3, "ACESSO_NEGADO": 15, "LIMITE_TAXA": 5,
           "TOKEN_INVALIDO": 5}  # nosec B105 (rótulo de evento, não é senha)
# ações auditadas que merecem aviso a cada ocorrência
ACOES_SENSIVEIS = ("USUARIO_ALTERADO", "COBRANCA_CONFIG_ALTERADA", "COBRANCA_WEBHOOK_TOKEN_RENOVADO", "COBRANCA_ATIVADA", "COBRANCA_SUSPENSA", "IA_CONFIGURADA", "SENHA_REDEFINIDA",
                   "ADMIN_ESCOLA_CRIADO")


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--horas", type=int, default=1)

    def handle(self, *a, **o):
        desde = timezone.now() - timedelta(hours=o["horas"])
        alertas = []
        cont = Counter()
        for e in EventoSeguranca.objects.filter(criado_em__gte=desde):
            cont[(e.instituicao_id, e.tipo)] += 1
        nomes = {}
        for (esc, tipo), n in sorted(cont.items(), key=lambda x: -x[1]):
            if n >= LIMITES.get(tipo, 10 ** 9):
                from apps.core.models import Instituicao
                nome = nomes.setdefault(esc, str(Instituicao.objects.filter(pk=esc).first() or "sem escola"))
                alertas.append(f"{nome}: {n} evento(s) {tipo} na última(s) {o['horas']} h")
        for r in RegistroAuditoria.objects.filter(criado_em__gte=desde, acao__in=ACOES_SENSIVEIS).select_related("instituicao"):
            alertas.append(f"{r.instituicao}: ação sensível {r.acao} por {r.usuario_nome or '—'}")
        if not alertas:
            self.stdout.write("Sem alertas.")
            return
        texto = "\n".join(alertas)
        self.stdout.write(texto)
        destinos = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email="").values_list("email", flat=True))
        if destinos and getattr(settings, "EMAIL_CONFIGURADO", False):
            send_mail("[EduNex] Alertas de segurança", texto, None, destinos, fail_silently=False)
        else:
            raise SystemExit(2)
