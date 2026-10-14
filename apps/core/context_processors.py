def instituicao(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    try:
        from .tenancy import instituicao_do_usuario
        return {"instituicao_atual": instituicao_do_usuario(user)}
    except Exception:  # usuário sem perfil: não derruba a renderização
        return {}
