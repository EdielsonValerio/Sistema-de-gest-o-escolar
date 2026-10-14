"""Perfis de acesso iniciais (grupos do Django) e suas permissões."""
from django.contrib.auth.models import Group, Permission

APPS = ["core", "accounts", "students", "academic", "enrollments", "finance", "audit", "documents"]

PERFIS = ["Administrador", "Secretaria", "Financeiro", "Consulta"]


def _codes(app, *nomes):
    return [(app, n) for n in nomes]


def _crud(app, modelo, view=True, add=True, change=True, delete=False):
    out = []
    if view:
        out.append((app, f"view_{modelo}"))
    if add:
        out.append((app, f"add_{modelo}"))
    if change:
        out.append((app, f"change_{modelo}"))
    if delete:
        out.append((app, f"delete_{modelo}"))
    return out


def permissoes_por_perfil():
    ver = lambda app, *m: [(app, f"view_{x}") for x in m]  # noqa: E731
    leitura = (
        ver("students", "aluno", "responsavel", "alunoresponsavelfinanceiro")
        + ver("academic", "anoletivo", "seriereferencia", "serieofertada", "turma")
        + ver("enrollments", "matricula")
        + ver("finance", "planofinanceiro", "parcela", "recebimento", "ajusteparcela", "estorno", "contratofinanceiro")
        + ver("core", "configuracaonumeracao", "configuracaogeral")
        + _codes("students", "ver_cpf_completo")
        + _codes("finance", "ver_relatorios")
    )
    # Consulta: somente leitura (sem CPF completo).
    consulta = [p for p in leitura if p[1] != "ver_cpf_completo"]

    secretaria = (
        _crud("students", "aluno") + _crud("students", "responsavel") + _crud("students", "alunoresponsavelfinanceiro")
        + _crud("academic", "anoletivo") + _crud("academic", "serieofertada") + _crud("academic", "turma")
        + ver("academic", "seriereferencia")
        + _crud("enrollments", "matricula")
        + _codes("enrollments", "transferir_matricula", "cancelar_matricula")
        + ver("finance", "planofinanceiro", "parcela", "contratofinanceiro")
        + _codes("finance", "gerar_cobrancas", "matricular_sem_cobranca")
        + ver("core", "configuracaonumeracao")
        + _codes("students", "ver_cpf_completo")
        + ver("documents", "modelodocumento", "documentoemitido") + _codes("documents", "emitir_documento")
    )

    financeiro = (
        ver("students", "aluno") + ver("students", "responsavel") + _crud("students", "responsavel")
        + ver("students", "alunoresponsavelfinanceiro")
        + ver("academic", "anoletivo", "seriereferencia", "serieofertada", "turma")
        + ver("enrollments", "matricula")
        + _crud("finance", "planofinanceiro")
        + ver("finance", "parcela", "recebimento", "ajusteparcela", "estorno", "contratofinanceiro")
        + _codes("finance", "aplicar_desconto", "alterar_vencimento", "corrigir_valor", "cancelar_parcela",
                 "gerar_cobrancas", "registrar_recebimento", "ver_relatorios")
        + _codes("students", "ver_cpf_completo")
        + ver("documents", "modelodocumento", "documentoemitido") + _codes("documents", "emitir_documento")
    )
    return {"Secretaria": secretaria, "Financeiro": financeiro, "Consulta": consulta}


def sincronizar_perfis(**kwargs):
    """Cria/atualiza os grupos (idempotente). Executado após `migrate`.

    O `post_migrate` deste app roda antes de os apps seguintes criarem suas permissões; por isso
    elas são criadas aqui explicitamente (idempotente) antes de montar os grupos.
    """
    from django.apps import apps as django_apps
    from django.contrib.auth.management import create_permissions

    for label in APPS:
        create_permissions(django_apps.get_app_config(label), verbosity=0)
    todas = Permission.objects.filter(content_type__app_label__in=APPS)
    mapa = {(p.content_type.app_label, p.codename): p for p in todas.select_related("content_type")}

    admin, _ = Group.objects.get_or_create(name="Administrador")
    admin.permissions.set(todas)

    for nome, itens in permissoes_por_perfil().items():
        grupo, _ = Group.objects.get_or_create(name=nome)
        perms = [mapa[i] for i in set(itens) if i in mapa]
        grupo.permissions.set(perms)
