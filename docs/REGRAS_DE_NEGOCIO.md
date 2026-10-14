# Regras de negócio e limitações

## Cadastros
- **Código único do aluno**: `[AnoCadastro][Sequencial]`, gerado em transação, imutável, independente de turma/matrícula.
- Aluno pode existir sem matrícula; situação cadastral (ativo/inativo) é independente da situação da matrícula.
- **Responsável financeiro** é entidade própria (reutilizável entre alunos). "Próprio aluno" copia os dados do aluno
  (snapshot). A troca de responsável encerra o vínculo vigente e cria outro (histórico); parcelas já geradas
  mantêm o responsável original.
- **Séries**: tabela de referência com códigos **internos** (não são códigos oficiais do MEC; campo de código
  oficial é opcional, com fonte). Séries ofertadas por ano letivo, sem duplicidade.
- **Turmas** oficiais (ano letivo + série ofertada do mesmo ano) e não oficiais (ano de referência + período).
  Datas coerentes e vínculos compatíveis são garantidos por constraints e validações. Nome livre; `codigo` e
  `numero` são estáveis (renomear não altera matrículas antigas).

## Matrículas
- Estados: Pendente → Ativa → Transferida / Concluída; Cancelada a partir de Pendente/Ativa. Estados finais não mudam.
- Uma matrícula oficial vigente por aluno e ano letivo (constraint parcial no banco + serviço).
- Várias turmas não oficiais permitidas, dentro do período da turma; não afetam a matrícula oficial.
- Transferência encerra a matrícula anterior (Transferida) e cria a nova ligada a ela.
- Capacidade máxima da turma é respeitada. Número de matrícula é gerado uma vez e nunca renumerado.
- **Numeração**: formato por variáveis permitidas (`[AnoLetivo] [AnoCadastro] [NumeroSerie] [NumeroTurma]
  [Sequencial] [CodigoAluno]`), substituição por *whitelist* (sem execução de código), prévia antes de salvar,
  escopo (instituição, ano, série ou turma) e contador transacional.

## Financeiro
- Plano financeiro por turma (vários por turma). Alterar o plano **não** altera parcelas já geradas.
- Matrícula + geração de parcelas ocorrem na mesma transação; um contrato por matrícula (sem duplicar cobranças
  em reenvio). Sem plano, a matrícula pode ser concluída sem cobrança com permissão específica.
- Parcela guarda valor original, desconto, acréscimo, valor devido, valor pago e saldo. "Vencida" é **derivada**
  (aberta/parcial com vencimento anterior a hoje). Parcelas canceladas não são apagadas.
- Descontos, correções de valor e mudanças de vencimento exigem permissão e justificativa e gravam valores
  anterior/posterior, usuário e data.
- **Recebimento**: dinheiro, PIX, crédito, débito; valor por parcela limitado ao saldo; troco só em dinheiro e
  nunca vira receita; operação atômica e idempotente (token único); comprovante imprimível.
- **Estorno**: permissão específica + justificativa; preserva o recebimento, reverte as alocações e recalcula saldos.
- Valores em `Decimal` (`ROUND_HALF_UP`), nunca `float`.

## Perfis
Administrador (tudo), Secretaria (cadastros e matrículas; sem operações financeiras sensíveis), Financeiro
(planos, ajustes, recebimentos, relatórios), Consulta (somente leitura). Verificação no servidor.

## Limitações conhecidas / versões futuras
- Sem integração real com PIX, adquirentes, bancos, boletos ou NF; PIX/cartão só registram referência.
- Estorno apenas administrativo (sem movimentação bancária).
- Não há exceção configurável para duas matrículas oficiais no mesmo ano (a regra é fixa).
- Multa/juros: juros simples calculados no recebimento quando habilitado no plano; sem job de recálculo diário.
- Sem adiantamento/crédito de saldo (excedente em dinheiro é troco).
- Sem boletim/notas/frequência, portal de pais, envio de e-mail/SMS de cobrança, nem relatórios exportáveis (CSV/PDF).
- Multi-instituição: dados isolados por instituição, mas sem tela de administração de instituições nem multitenancy completo.
- No SQLite o bloqueio de linha (`select_for_update`) não tem efeito; concorrência é protegida por transação
  serializada e constraints. Use PostgreSQL em produção com vários operadores.
- Bootstrap e ícones vêm de CDN (requer internet no navegador).

## Documentos (Configurações → Documentos)
- **Tipos**: lista predefinida em `apps/documents/tipos.py` (códigos estáveis; 61 tipos). Cada **modelo** tem nome, tipo,
  destinatário (**Aluno** ou **Responsável**), leiaute, "inserir cabeçalho padrão" e situação (ativo/inativo).
  Só modelos **ativos** aparecem para emissão. Modelos não são excluídos (inative-os).
- **Leiaute**: editor simples (negrito, itálico, sublinhado, título, listas, alinhamento, linha) com painel de
  variáveis `[nomeAluno]`, `[codigoUnicoAluno]` etc. (catálogo em `apps/documents/variaveis.py`). Variáveis inexistentes
  são rejeitadas ao salvar; nenhum código é executado. O HTML é sanitizado no servidor (lista de tags/estilos permitidos)
  e os valores são escapados na emissão. Dados ausentes aparecem como "—".
- **Cabeçalho padrão** (uma configuração por instituição): logotipo (imagem até 2 MB), título, linhas complementares e
  rodapé. Aplica-se aos modelos com "inserir cabeçalho padrão" marcado, quando o cabeçalho estiver ativo.
- **Emissão**: botão **Documentos** na ficha do aluno (modelos "Aluno") e no detalhe do responsável (modelos
  "Responsável", com aluno de referência opcional, que precisa estar vinculado a ele). O documento é gravado como
  **instantâneo** (`DocumentoEmitido`): alterar o modelo depois não muda documentos já emitidos; a reimpressão é idêntica.
  Cada emissão e cada alteração de modelo/cabeçalho vai para a auditoria (sem dados pessoais).
- **Permissões**: configurar (criar/editar modelos e cabeçalho) = Administrador; emitir e consultar emitidos =
  Administrador, Secretaria e Financeiro. As variáveis financeiras só são preenchidas para quem pode ver parcelas.
- **Limitações**: sem numeração/assinatura digital nem verificação por QR code; PDF via "Imprimir / salvar em PDF" do
  navegador; sem histórico de versões dos modelos; campos livres (notas, ocorrências) ainda não são variáveis.
