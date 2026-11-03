from .tenancy import login_do_usuario


def instituicao(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"instituicao_atual": getattr(request, "instituicao", None), "login_exibicao": login_do_usuario(user)}
