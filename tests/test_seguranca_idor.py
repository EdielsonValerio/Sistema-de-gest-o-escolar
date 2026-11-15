"""Varredura de isolamento entre escolas (IDOR/BOLA): o administrador da Escola B tenta, em TODAS as rotas com
parâmetros numéricos, ler (GET) e alterar (POST, inclusive com IDs da Escola A no corpo) dados da Escola A.

Critérios: nenhuma resposta mostra dado da Escola A; nada da Escola A muda; nada da Escola B passa a apontar
para a Escola A. Usa apenas dados fictícios."""
import re
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.core import serializers
from django.urls import URLPattern, URLResolver, get_resolver

from apps.diario import emissao, servicos
from apps.diario.models import HistoricoExterno
from apps.payables.models import ContaPagar

from .helpers import criar_plano, criar_usuario
from .test_diario import Base

# rotas públicas, de sessão ou de integração (têm proteção própria e testes próprios)
IGNORAR = ("admin/", "entrar", "sair", "pre-matricula", "webhook", "minha-senha", "ativar", "recuperar", "definir",
           "mfa", "escola/")
PARAM = re.compile(r"<(?:int:)?\w+>")


def _rotas(padroes=None, prefixo=""):
    for p in padroes if padroes is not None else get_resolver().url_patterns:
        if isinstance(p, URLResolver):
            yield from _rotas(p.url_patterns, prefixo + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefixo + str(p.pattern)


class VarreduraEntreEscolasTests(Base):
    def setUp(self):
        super().setUp()
        self.adm_b = criar_usuario(self.b, "Administrador")
        # dados reconhecíveis da Escola A
        for m in self.mat:
            m.aluno.nome_mae = "Mãe ZZA " + str(m.pk)
            m.aluno.save()
        criar_plano(self.a, self.turma, nome="Plano ZZA")
        ContaPagar.objects.create(instituicao=self.a, numero="ZZA1", descricao="Conta ZZA", prestador="Fornecedor ZZA",
                                  valor=Decimal("10"), vencimento=date(2027, 3, 1))
        av = servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
            "periodo": self.p1, "titulo": "Prova ZZA", "tipo": "NORMAL", "data": date(2027, 3, 20),
            "nota_maxima": Decimal(10), "peso": Decimal(1)})
        servicos.lancar_notas(av, {m.pk: Decimal("7") for m in self.mat}, self.prof1)
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(conteudo="Conteúdo ZZA"),
                             presencas=self.presencas(), finalizar=True, por=self.prof1)
        servicos.fechar_diario(self.aloc, self.p1, self.prof1)
        self.doc = emissao.emitir_boletim(self.mat[0], self.sec)
        HistoricoExterno.objects.create(instituicao=self.a, aluno=self.mat[0].aluno, ano=2025, serie="4º",
                                        escola="Escola ZZA Antiga", cidade_uf="X/CE", resultado="APROVADO",
                                        criado_por=self.sec)
        self.marcas = ["ZZA", "Aluno 0 Diário", "Aluno 1 Diário", "Aluno 2 Diário", "5º Ano A", "Português",
                       self.mat[0].numero]

    def _modelos_da_escola(self, escola):
        for model in django_apps.get_models():
            if model._meta.app_label in ("auth", "contenttypes", "sessions", "admin"):
                continue
            if any(f.name == "instituicao" for f in model._meta.fields):
                yield model, model.objects.filter(instituicao=escola)

    def _foto(self, escola):
        return {m._meta.label: serializers.serialize("json", qs.order_by("pk")) for m, qs in self._modelos_da_escola(escola)}

    def test_escola_b_nao_le_nem_altera_dados_da_escola_a(self):
        antes = self._foto(self.a)
        c = self.cli(self.adm_b)
        ids = list(range(1, 16))
        corpo = {k: i for k in ("aluno", "turma", "responsavel", "plano", "disciplina", "professor", "matricula",
                                "parcela", "ano_letivo", "periodo", "serie_ofertada", "alocacao", "modelo", "conta",
                                "instituicao", "usuario") for i in (1,)}
        achados, testadas = [], 0
        for rota in sorted(set(_rotas())):
            if any(rota.startswith(x) or x in rota for x in IGNORAR) or "^" in rota or "(?" in rota:
                continue
            if not PARAM.search(rota):
                continue
            for i in ids:
                url = "/" + PARAM.sub(str(i), rota)
                for metodo in ("get", "post"):
                    r = getattr(c, metodo)(url, corpo) if metodo == "post" else c.get(url)
                    testadas += 1
                    if r.status_code >= 500:
                        achados.append((metodo, url, r.status_code))
                    elif r.status_code == 200 and any(m in r.content.decode(errors="ignore") for m in self.marcas):
                        achados.append((metodo, url, "VAZOU dado da Escola A"))
        self.assertGreater(testadas, 300)
        self.assertEqual(achados, [])
        self.assertEqual(self._foto(self.a), antes, "a Escola B alterou dados da Escola A")

    def test_nada_da_escola_b_aponta_para_a_escola_a(self):
        c = self.cli(self.adm_b)
        for rota in sorted(set(_rotas())):
            if any(x in rota for x in IGNORAR) or "^" in rota or "(?" in rota or PARAM.search(rota):
                continue
            c.get("/" + rota)
        for model, qs in self._modelos_da_escola(self.b):
            for obj in qs:
                for f in model._meta.fields:
                    if f.is_relation and f.name != "instituicao" and getattr(obj, f.name + "_id", None):
                        alvo = getattr(obj, f.name)
                        if hasattr(alvo, "instituicao_id"):
                            self.assertEqual(alvo.instituicao_id, self.b.pk, f"{model.__name__}.{f.name}")
