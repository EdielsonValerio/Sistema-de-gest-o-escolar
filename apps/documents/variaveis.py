"""Variáveis de documentos, substituição controlada e sanitização do HTML do editor.

Nada aqui executa código: o texto do modelo só aceita `[nomeDaVariavel]` presentes no catálogo, e todo valor
é escapado antes de entrar no HTML. O HTML salvo passa por uma lista de permissões (tags e estilos).
"""
import re
from html import escape
from html.parser import HTMLParser

from django.utils import timezone

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]

# (grupo, [(chave, descrição, exemplo)])
CATALOGO = [
    ("Instituição", [
        ("nomeEscola", "Nome da instituição", "Escola Modelo"),
        ("cnpjEscola", "CNPJ da instituição", "00.000.000/0001-00"),
    ]),
    ("Aluno", [
        ("nomeAluno", "Nome completo do aluno", "Maria Souza Lima"),
        ("nomeSocialAluno", "Nome social (ou nome completo, se não houver)", "Maria Souza Lima"),
        ("codigoUnicoAluno", "Código único (permanente) do aluno", "2027000001"),
        ("matriculaUnica", "Código único do aluno (sinônimo)", "2027000001"),
        ("dataNascimentoAluno", "Data de nascimento", "10/05/2015"),
        ("idadeAluno", "Idade em anos", "12"),
        ("sexoAluno", "Sexo", "Feminino"),
        ("cpfAluno", "CPF do aluno", "000.000.000-00"),
        ("documentoAluno", "Documento de identificação (tipo e número)", "RG 1234567"),
        ("nacionalidadeAluno", "Nacionalidade", "Brasileira"),
        ("naturalidadeAluno", "Naturalidade", "Cuiabá"),
        ("emailAluno", "E-mail do aluno", "aluno@exemplo.com"),
        ("telefoneAluno", "Telefone do aluno", "(65) 3000-0000"),
        ("celularAluno", "Celular do aluno", "(65) 99999-0000"),
        ("enderecoAluno", "Endereço completo do aluno", "Rua das Flores, 100 – Centro, Cuiabá/MT, CEP 78000-000"),
        ("situacaoCadastroAluno", "Situação cadastral (ativo/inativo)", "Ativo"),
        ("dataCadastroAluno", "Data do cadastro", "02/01/2027"),
    ]),
    ("Filiação", [
        ("nomeMae", "Nome da mãe", "Ana Souza"),
        ("cpfMae", "CPF da mãe", "000.000.000-00"),
        ("telefoneMae", "Telefone da mãe", "(65) 99999-1111"),
        ("nomePai", "Nome do pai", "Carlos Lima"),
        ("cpfPai", "CPF do pai", "000.000.000-00"),
        ("telefonePai", "Telefone do pai", "(65) 99999-2222"),
        ("nomeResponsavelLegal", "Nome do responsável legal", "Ana Souza"),
        ("cpfResponsavelLegal", "CPF do responsável legal", "000.000.000-00"),
        ("telefoneResponsavelLegal", "Telefone do responsável legal", "(65) 99999-1111"),
    ]),
    ("Matrícula e turma", [
        ("numeroMatricula", "Número da matrícula atual (turma oficial)", "1012027-0001"),
        ("situacaoMatricula", "Situação da matrícula atual", "Ativa"),
        ("dataIngressoMatricula", "Data de ingresso na matrícula atual", "01/02/2027"),
        ("anoLetivo", "Ano letivo da matrícula atual", "2027"),
        ("serieAluno", "Série da matrícula atual", "1º ano do Ensino Fundamental"),
        ("turmaAluno", "Turma da matrícula atual", "1º Ano A"),
        ("codigoTurma", "Código interno da turma", "T-0001"),
    ]),
    ("Responsável financeiro", [
        ("nomeResponsavel", "Nome ou razão social do responsável", "Ana Souza"),
        ("documentoResponsavel", "CPF/CNPJ do responsável", "000.000.000-00"),
        ("emailResponsavel", "E-mail de cobrança do responsável", "responsavel@exemplo.com"),
        ("telefoneResponsavel", "Telefone do responsável", "(65) 99999-1111"),
        ("enderecoResponsavel", "Endereço do responsável", "Rua das Flores, 100 – Centro, Cuiabá/MT, CEP 78000-000"),
        ("parentescoResponsavel", "Relação do responsável com o aluno", "Mãe"),
    ]),
    ("Financeiro", [
        ("totalDevido", "Total devido (parcelas não canceladas)", "R$ 6.000,00"),
        ("totalPago", "Total já pago", "R$ 1.500,00"),
        ("saldoDevedor", "Saldo devedor em aberto", "R$ 4.500,00"),
        ("qtdParcelasAbertas", "Quantidade de parcelas em aberto", "9"),
        ("qtdParcelasVencidas", "Quantidade de parcelas vencidas", "0"),
    ]),
    ("Emissão", [
        ("dataAtual", "Data de emissão (dd/mm/aaaa)", "13/10/2026"),
        ("dataExtenso", "Data de emissão por extenso", "13 de outubro de 2026"),
        ("anoAtual", "Ano da emissão", "2026"),
        ("usuarioEmissor", "Usuário que emitiu o documento", "secretaria"),
    ]),
]

CHAVES = {c.lower(): c for _, itens in CATALOGO for c, _, _ in itens}
EXEMPLOS = {c: ex for _, itens in CATALOGO for c, _, ex in itens}
_RE_VAR = re.compile(r"\[([A-Za-z][A-Za-z0-9_]*)\]")
VAZIO = "—"


def variaveis_desconhecidas(texto):
    return sorted({m for m in _RE_VAR.findall(texto or "") if m.lower() not in CHAVES})


def _fmt_data(d):
    return d.strftime("%d/%m/%Y") if d else VAZIO


def _brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _endereco(o):
    partes = [o.logradouro, o.numero, o.complemento]
    linha1 = ", ".join(p for p in partes if p)
    local = " – ".join(p for p in [o.bairro, "/".join(p for p in [o.municipio, o.uf] if p)] if p)
    cep = f"CEP {o.cep}" if o.cep else ""
    return ", ".join(p for p in [linha1, local, cep] if p) or VAZIO


def _matricula_atual(aluno):
    mats = list(aluno.matriculas.filter(tipo="OFICIAL").select_related(
        "turma", "turma__serie_ofertada__serie_referencia", "ano_letivo").order_by("-data_ingresso", "-id"))
    vigentes = [m for m in mats if m.status in ("PENDENTE", "ATIVA")]
    return (vigentes or mats or [None])[0]


def montar_contexto(*, instituicao, usuario, aluno=None, responsavel=None):
    """Valores (texto puro) de todas as variáveis do catálogo. Ausente → '—'."""
    ctx = {c: VAZIO for c in EXEMPLOS}
    hoje = timezone.localdate()
    ctx.update(nomeEscola=instituicao.nome, cnpjEscola=instituicao.cnpj or VAZIO, dataAtual=_fmt_data(hoje),
               dataExtenso=f"{hoje.day} de {MESES[hoje.month - 1]} de {hoje.year}", anoAtual=str(hoje.year),
               usuarioEmissor=usuario.get_username() if usuario else VAZIO)

    vinculo = None
    if aluno is not None:
        a = aluno
        doc = " ".join(p for p in [a.get_tipo_documento_display() if a.tipo_documento else "", a.numero_documento]
                       if p)
        ctx.update(
            nomeAluno=a.nome, nomeSocialAluno=a.nome_exibicao, codigoUnicoAluno=a.codigo, matriculaUnica=a.codigo,
            dataNascimentoAluno=_fmt_data(a.data_nascimento), idadeAluno=str(a.idade),
            sexoAluno=a.get_sexo_display(), cpfAluno=a.cpf or VAZIO, documentoAluno=doc or VAZIO,
            nacionalidadeAluno=a.nacionalidade or VAZIO, naturalidadeAluno=a.naturalidade or VAZIO,
            emailAluno=a.email or VAZIO, telefoneAluno=a.telefone or VAZIO, celularAluno=a.celular or VAZIO,
            enderecoAluno=_endereco(a), situacaoCadastroAluno="Ativo" if a.ativo else "Inativo",
            dataCadastroAluno=_fmt_data(timezone.localtime(a.criado_em).date()),
            nomeMae=a.nome_mae or VAZIO, cpfMae=a.cpf_mae or VAZIO, telefoneMae=a.telefone_mae or VAZIO,
            nomePai=a.nome_pai or VAZIO, cpfPai=a.cpf_pai or VAZIO, telefonePai=a.telefone_pai or VAZIO,
            nomeResponsavelLegal=a.nome_responsavel_legal or VAZIO,
            cpfResponsavelLegal=a.cpf_responsavel_legal or VAZIO,
            telefoneResponsavelLegal=a.telefone_responsavel_legal or VAZIO,
        )
        m = _matricula_atual(a)
        if m:
            serie = getattr(getattr(m.turma, "serie_ofertada", None), "serie_referencia", None)
            ctx.update(numeroMatricula=m.numero or VAZIO, situacaoMatricula=m.get_status_display(),
                       dataIngressoMatricula=_fmt_data(m.data_ingresso),
                       anoLetivo=str(m.ano_letivo.ano) if m.ano_letivo else VAZIO,
                       serieAluno=serie.nome if serie else VAZIO, turmaAluno=m.turma.nome,
                       codigoTurma=m.turma.codigo)
        if responsavel is None:
            vinculo = a.responsaveis_financeiros.filter(fim__isnull=True).select_related("responsavel").first()
            responsavel = vinculo.responsavel if vinculo else None
        elif responsavel is not None:
            vinculo = a.responsaveis_financeiros.filter(responsavel=responsavel).order_by("fim", "-inicio").first()

    if responsavel is not None:
        r = responsavel
        ctx.update(nomeResponsavel=r.nome, documentoResponsavel=r.documento or VAZIO,
                   emailResponsavel=r.email_cobranca or VAZIO, telefoneResponsavel=r.telefone or VAZIO,
                   enderecoResponsavel=_endereco(r),
                   parentescoResponsavel=vinculo.get_parentesco_display() if vinculo else VAZIO)

    # Financeiro: só para quem pode ver parcelas (evita expor valores a perfis sem essa permissão).
    if aluno is not None and usuario is not None and usuario.has_perm("finance.view_parcela"):
        from decimal import Decimal

        parcelas = list(aluno.parcelas.exclude(status="CANCELADA"))
        devido = sum((p.valor_devido for p in parcelas), Decimal("0"))
        pago = sum((p.valor_pago for p in parcelas), Decimal("0"))
        abertas = [p for p in parcelas if p.status in ("ABERTA", "PARCIAL")]
        ctx.update(totalDevido=_brl(devido), totalPago=_brl(pago), saldoDevedor=_brl(devido - pago),
                   qtdParcelasAbertas=str(len(abertas)),
                   qtdParcelasVencidas=str(sum(1 for p in abertas if p.vencida)))
    return ctx


def renderizar(html, valores):
    """Troca [variavel] pelo valor (escapado). Desconhecidas permanecem como estão."""
    mapa = {k.lower(): v for k, v in valores.items()}

    def troca(m):
        v = mapa.get(m.group(1).lower())
        return m.group(0) if v is None else escape(str(v))

    return _RE_VAR.sub(troca, html or "")


# --------------------------------------------------------------------------- sanitização
_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "ul", "ol", "li", "div", "span", "hr",
         "blockquote", "table", "thead", "tbody", "tr", "td", "th"}
_VAZIAS = {"br", "hr"}
_DESCARTA_CONTEUDO = {"script", "style", "iframe", "object", "embed", "template", "noscript"}
_RE_ALIGN = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.I)
_EQUIV = {"strong": "strong", "b": "strong", "em": "em", "i": "em"}


class _Sanitizador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.pilha, self.ignorando = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _DESCARTA_CONTEUDO:
            self.ignorando += 1
            return
        if tag not in _TAGS or self.ignorando:
            return
        estilo = ""
        for nome, valor in attrs:
            if nome == "style":
                m = _RE_ALIGN.search(valor or "")
                if m:
                    estilo = f' style="text-align: {m.group(1).lower()}"'
        if tag in _VAZIAS:
            self.out.append(f"<{tag}>")
            return
        self.pilha.append(tag)
        self.out.append(f"<{tag}{estilo}>")

    def handle_startendtag(self, tag, attrs):
        if tag in _VAZIAS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _DESCARTA_CONTEUDO:
            self.ignorando = max(0, self.ignorando - 1)
            return
        if tag in _TAGS and tag not in _VAZIAS and tag in self.pilha and not self.ignorando:
            while self.pilha:
                t = self.pilha.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.ignorando:
            self.out.append(escape(data, quote=False))

    def resultado(self):
        while self.pilha:
            self.out.append(f"</{self.pilha.pop()}>")
        return "".join(self.out)


def sanitizar_html(html):
    p = _Sanitizador()
    p.feed(html or "")
    p.close()
    return p.resultado()


def texto_puro(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()
