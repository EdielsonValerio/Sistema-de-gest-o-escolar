import warnings
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from apps.academic.models import SerieReferencia
from apps.accounts.permissions import PERFIS, sincronizar_perfis

from .helpers import criar_instituicao, criar_turma_oficial, criar_usuario


class PerfisEPermissoesTests(TestCase):
    def test_grupos_possuem_permissoes_de_todos_os_apps(self):
        sincronizar_perfis()
        for nome in PERFIS:
            self.assertTrue(Group.objects.get(name=nome).permissions.exists(), nome)
        adm = Group.objects.get(name="Administrador")
        apps = set(adm.permissions.values_list("content_type__app_label", flat=True))
        self.assertTrue({"students", "academic", "enrollments", "finance", "audit"} <= apps)

    def test_consulta_nao_tem_permissao_de_escrita(self):
        sincronizar_perfis()
        codenames = set(Group.objects.get(name="Consulta").permissions.values_list("codename", flat=True))
        self.assertFalse([c for c in codenames if c.startswith(("add_", "change_", "delete_"))])
        self.assertNotIn("registrar_recebimento", codenames)

    def test_administrador_acessa_telas_principais(self):
        inst = criar_instituicao()
        adm = criar_usuario(inst, "Administrador")
        self.client.force_login(adm)
        for url in ("/", "/alunos/", "/secretaria/turmas/", "/financeiro/planos/", "/financeiro/caixa/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class SeriesPadraoTests(TestCase):
    def test_series_carregadas_apos_migrate(self):
        self.assertTrue(SerieReferencia.objects.exists())

    def test_comando_e_idempotente(self):
        total = SerieReferencia.objects.count()
        call_command("carregar_series", stdout=StringIO())
        self.assertEqual(SerieReferencia.objects.count(), total)

    def test_codigos_sao_internos_e_sem_codigo_oficial(self):
        self.assertFalse(SerieReferencia.objects.exclude(codigo_oficial="").exists())


class PaginacaoOrdenadaTests(TestCase):
    def test_listagens_com_agregacao_sao_ordenadas(self):
        inst = criar_instituicao()
        adm = criar_usuario(inst, "Administrador")
        criar_turma_oficial(inst, numero=1)
        criar_turma_oficial(inst, numero=2, codigo_serie="EF1-02")
        self.client.force_login(adm)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for url in ("/secretaria/turmas/", "/secretaria/series/", "/financeiro/planos/"):
                self.assertEqual(self.client.get(url).status_code, 200, url)
