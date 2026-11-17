from .tenancy import em_modo_suporte, login_do_usuario


def instituicao(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    escola = getattr(request, "instituicao", None)
    pre = 0
    if escola is not None and "prematricula" in (escola.modulos or []) and user.has_perm("students.view_aluno"):
        from apps.students.models import Aluno
        pre = Aluno.objects.filter(instituicao=escola, pre_matricula_online=True).count()
    return {"instituicao_atual": escola, "modulos_ativos": set(getattr(escola, "modulos", None) or []), "login_exibicao": login_do_usuario(user), "pre_matriculas_novas": pre,
            "modo_suporte": em_modo_suporte(user, request),
            "troca_obrigatoria": bool(getattr(getattr(user, "perfil", None), "deve_trocar_senha", False))}


def csp(request):
    """Nonce da Content-Security-Policy desta requisição (usado em <script nonce="{{ csp_nonce }}">)."""
    return {"csp_nonce": getattr(request, "csp_nonce", "")}
