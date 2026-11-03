"""Clientes do modelo de IA: Claude (API Messages com uso de ferramentas) e um modo de demonstração offline.

Os dois têm a mesma interface `mensagem(system, mensagens, ferramentas, max_tokens)` e devolvem o mesmo formato da
API Messages: `{"content": [blocos], "stop_reason": "...", "usage": {"input_tokens": n, "output_tokens": n}}`.
Nunca coloque a chave de API em log nem em mensagem de erro.
"""
import json
import urllib.error
import urllib.request

URL = "https://api.anthropic.com/v1/messages"
VERSAO_API = "2023-06-01"
TIMEOUT = 60


class ErroIA(Exception):
    """Falha ao consultar a IA. A mensagem é segura para o usuário."""


class ClienteClaude:
    def __init__(self, api_key, modelo):
        self.api_key, self.modelo = api_key, modelo

    def _post(self, corpo):
        req = urllib.request.Request(URL, data=json.dumps(corpo).encode(), method="POST", headers={
            "x-api-key": self.api_key, "anthropic-version": VERSAO_API, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (URL fixa, https)
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise ErroIA(self._mensagem_http(exc)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ErroIA("Não foi possível conectar ao serviço de IA. Tente novamente em instantes.") from None
        except ValueError:
            raise ErroIA("Resposta inesperada do serviço de IA.") from None

    @staticmethod
    def _mensagem_http(exc):
        if exc.code == 401:
            return "A chave de API da escola foi recusada (401). Confira a chave na configuração do assistente."
        if exc.code == 403:
            return "A chave de API da escola não tem permissão para este modelo (403)."
        if exc.code == 404:
            return "Modelo de IA não encontrado para esta chave. Escolha outro modelo na configuração."
        if exc.code == 429:
            return "O limite de uso da chave de API da escola foi atingido. Tente novamente mais tarde."
        if exc.code >= 500:
            return "O serviço de IA está indisponível no momento. Tente novamente em instantes."
        return f"O serviço de IA recusou a solicitação ({exc.code})."

    def mensagem(self, *, system, mensagens, ferramentas, max_tokens=1500):
        corpo = {"model": self.modelo, "max_tokens": max_tokens, "system": system, "messages": mensagens}
        if ferramentas:
            corpo["tools"] = ferramentas
        return self._post(corpo)


# --------------------------------------------------------------------------- demonstração (sem rede)
def _tool_results(mensagens):
    """Resultados de ferramentas da última rodada: {tool_use_id: conteúdo}."""
    ultimo = mensagens[-1] if mensagens else {}
    out = {}
    if isinstance(ultimo.get("content"), list):
        for b in ultimo["content"]:
            if b.get("type") == "tool_result":
                try:
                    out[b["tool_use_id"]] = json.loads(b["content"])
                except (ValueError, TypeError):
                    out[b["tool_use_id"]] = {}
    return out


class ClienteDemo:
    """Faz de conta que é a IA: escolhe a ferramenta por palavras-chave. Serve para treinar e testar a tela."""

    modelo = "demonstracao"

    def _resp(self, blocos, parar):
        return {"content": blocos, "stop_reason": "tool_use" if parar else "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 10}}

    def mensagem(self, *, system, mensagens, ferramentas, max_tokens=1500):
        nomes = {f["name"] for f in ferramentas}
        ultimo = mensagens[-1]
        if isinstance(ultimo["content"], list):  # voltou de uma ferramenta
            return self._depois_da_ferramenta(mensagens, nomes)
        texto = ultimo["content"].lower()
        escolha = None
        if "matricul" in texto and ("receb" in texto or "valor" in texto):
            escolha = "evolucao_matriculas_e_recebimentos"
        elif "inadimpl" in texto or "atras" in texto or "vencid" in texto:
            escolha = "inadimplencia_por_turma"
        elif "receb" in texto:
            escolha = "recebimentos_por_mes"
        elif "turma" in texto or "matricul" in texto:
            escolha = "matriculas_por_turma"
        if escolha not in nomes:
            return self._resp([{"type": "text", "text": "(modo demonstração) Posso mostrar matrículas por turma, "
                                "recebimentos por mês, inadimplência por turma e a evolução de matrículas × valor "
                                "recebido, conforme as suas permissões. Peça, por exemplo: “gráfico de alunos "
                                "matriculados e valor recebido”."}], False)
        return self._resp([{"type": "tool_use", "id": "demo_1", "name": escolha, "input": {}}], True)

    def _depois_da_ferramenta(self, mensagens, nomes):
        blocos = [b for m in mensagens if isinstance(m["content"], list) for b in m["content"]]
        if any(b.get("type") == "tool_use" and b.get("name") == "emitir_grafico" for b in blocos):
            return self._resp([{"type": "text", "text": "(modo demonstração) Aqui está o gráfico com os dados "
                                "agregados da escola. Passe o mouse sobre as barras e linhas para ver os valores."}], False)
        usado = next((b["name"] for b in reversed(blocos) if b.get("type") == "tool_use"), "")
        dados = next(iter(_tool_results(mensagens).values()), {})
        linhas = dados.get("linhas") or []
        if not linhas:
            return self._resp([{"type": "text", "text": "(modo demonstração) Não há dados no período."}], False)
        rot = [str(l.get("mes") or l.get("turma")) for l in linhas]
        if usado == "evolucao_matriculas_e_recebimentos":
            series = [{"nome": "Alunos matriculados", "valores": [l["alunos_matriculados"] for l in linhas],
                       "tipo": "bar", "eixo": "esquerda", "unidade": ""},
                      {"nome": "Valor recebido", "valores": [l["valor_recebido"] for l in linhas], "tipo": "line",
                       "eixo": "direita", "unidade": "R$"}]
        elif usado == "inadimplencia_por_turma":
            series = [{"nome": "Valor vencido", "valores": [l["valor_vencido"] for l in linhas], "tipo": "bar",
                       "eixo": "esquerda", "unidade": "R$"}]
        elif usado == "recebimentos_por_mes":
            series = [{"nome": "Valor recebido", "valores": [l["valor_recebido"] for l in linhas], "tipo": "bar",
                       "eixo": "esquerda", "unidade": "R$"}]
        else:
            series = [{"nome": "Matrículas ativas", "valores": [l["matriculas_ativas"] for l in linhas], "tipo": "bar",
                       "eixo": "esquerda", "unidade": ""}]
        return self._resp([{"type": "tool_use", "id": "demo_2", "name": "emitir_grafico",
                            "input": {"titulo": "Gráfico gerado pelo assistente", "rotulos": rot, "series": series}}], True)
