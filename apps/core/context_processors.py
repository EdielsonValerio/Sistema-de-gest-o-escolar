from .tenancy import em_modo_suporte, login_do_usuario


def instituicao(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"instituicao_atual": getattr(request, "instituicao", None), "login_exibicao": login_do_usuario(user),
            "modo_suporte": em_modo_suporte(user, request),
            "troca_obrigatoria": bool(getattr(getattr(user, "perfil", None), "deve_trocar_senha", False))}
