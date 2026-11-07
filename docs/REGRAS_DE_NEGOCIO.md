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

## Várias escolas num único sistema
- **Escola = `Instituicao`**. Cada escola tem nome (único), CNPJ opcional, **logotipo**, situação (ativa/inativa) e
  data de cadastro. Escolas inativas somem da tela de login e seus usuários perdem o acesso (sessões abertas são
  encerradas na próxima requisição).
- **Login**: escola → usuário → senha. A mensagem de erro é a mesma para qualquer falha (não revela se a escola ou o
  usuário existem). O login é único **dentro** da escola; o `username` interno do Django é `"<id da escola>.<login>"`.
  A sessão guarda a escola escolhida e é encerrada se não corresponder à escola do usuário.
- **Administrador do sistema** (superusuário sem escola): cadastra/edita/inativa escolas, cria administradores de
  escola e redefine senhas (**Sistema → Escolas**) e mantém a tabela compartilhada de **séries de referência**.
  Não abre telas de secretaria/financeiro (HTTP 403). Superusuários nunca são gerenciados pelo administrador da escola.
- **Administrador da escola** (perfil *Administrador*, sem `is_staff`/`is_superuser`): total sobre os dados da sua
  escola; só vê e gerencia usuários da própria escola; não acessa a área do sistema.
- **Isolamento dos dados**: (1) todas as tabelas de negócio têm `instituicao_id` NOT NULL (as tabelas filhas
  `SerieOfertada`, `AlunoResponsavelFinanceiro`, `AjusteParcela`, `AlocacaoRecebimento` e `Estorno` herdam a escola
  do registro pai); (2) toda consulta das telas filtra pela escola do usuário e acesso a registro de outra escola
  retorna 404; (3) antes de gravar, o sistema confere que os registros relacionados (aluno, turma, responsável,
  parcela…) pertencem à mesma escola e recusa a gravação caso contrário; (4) códigos e numerações são independentes
  por escola; (5) arquivos enviados (logotipos) usam nomes aleatórios.
- **Compartilhado entre escolas**: somente a tabela de séries de referência (estrutura educacional) e os perfis de
  acesso (grupos), que não contêm dados de escola.
- **Limitações**: o isolamento é garantido pela aplicação e pelas constraints; não há *row-level security* no
  PostgreSQL nem bancos separados por escola. `bulk_create`/`update` em massa não passam pela conferência de escola.
  Fotos de alunos e logotipos ficam em `media/` e, em produção, devem ser servidos pelo servidor web (não por DEBUG).

## Contas a pagar (Financeiro → Contas a pagar)
Registra as **saídas financeiras** da escola. O que sai é debitado do **saldo total recebido** no contas a receber.

- **Cadastro**: descrição, categoria, prestador/fornecedor, CPF/CNPJ do prestador (validado), nº do documento
  (nota fiscal, boleto, contrato), **valor**, **data de pagamento (vencimento)** e observações. Cada conta recebe um
  número `CP-AAAA-00001` (sequência por escola e ano). A **repetição mensal** (1 a 36 meses) cria uma conta por mês
  com o mesmo valor (ex.: aluguel); datas de fim de mês são ajustadas (31/01 → 28/02). Reenviar o formulário não
  duplica contas (token de idempotência).
- **Saldo disponível** = recebimentos confirmados (valor aplicado às parcelas, **sem troco**; recebimentos estornados
  não contam) **−** liquidações ativas. É sempre **calculado**, nunca armazenado: estornos de recebimentos ou de
  liquidações se refletem sozinhos. Cadastrar uma conta **não** altera o saldo; só a liquidação debita.
- **Situações**: *Em aberto*, *Liquidada* e *Desativada*. "Vencida" não é uma situação gravada: é uma conta em aberto
  com data de pagamento anterior a hoje. Nada é excluído fisicamente.
- **Liquidar**: informa data (não pode ser futura), forma de pagamento (dinheiro, PIX, transferência, boleto, cartão,
  cheque, outra), referência (comprovante/autenticação; número completo de cartão é recusado) e observação. O valor
  liquidado vem preenchido com o da conta e pode ser alterado (juros, multa, desconto); a conta mantém o valor
  original. Uma conta só pode ter **uma liquidação ativa** (constraint no banco).
- **Saldo negativo (Configurações gerais → "permitir saldo negativo no contas a pagar")**:
  - **Desmarcado (padrão)**: a liquidação que deixaria o saldo abaixo de zero é recusada com a mensagem
    *"Não é permitido saldo negativo. Saldo disponível: R$ …; valor a liquidar: R$ …"*. A conta continua em aberto.
  - **Marcado**: a liquidação é aceita e o saldo fica negativo (destacado em vermelho e registrado na auditoria).
  - A verificação acontece **na liquidação**, único momento em que o dinheiro sai. Cadastrar contas futuras maiores
    que o saldo é permitido (a tela de detalhe avisa quando a liquidação seria recusada).
  - Duas liquidações simultâneas **não** conseguem gastar o mesmo saldo: a operação é serializada por escola.
- **Desativar**: exige motivo; a conta deixa de ser cobrada e sai das notificações, mas o registro é preservado. Pode
  ser **reativada**. Conta liquidada não pode ser desativada (estorne a liquidação antes).
- **Estornar liquidação** (correção de lançamento errado): exige permissão própria e justificativa; preserva o registro
  original, devolve o valor ao saldo e reabre a conta. A conta pode ser liquidada de novo.
- **Editar**: somente contas **em aberto**; alterações ficam na auditoria (campos alterados).
- **Notificações**: sino na barra superior (com contador), cartão no dashboard e contador no menu mostram as contas em
  aberto **vencidas** ou que vencem dentro do prazo de aviso (**Configurações gerais → "avisar com antecedência de
  (dias)"**, padrão 5, de 0 a 60), da mais urgente para a menos. Quem não tem acesso ao contas a pagar não vê nada disso.
- **Permissões**: Administrador (tudo, inclusive estornar liquidações); Financeiro (cadastrar, editar, liquidar,
  desativar e reativar); Consulta (somente leitura); Secretaria (sem acesso). Verificadas no servidor.
- **Isolamento**: contas, liquidações, numeração, saldo e as duas configurações acima são **por escola**; a escola não
  usa o saldo de outra e o acesso a conta de outra escola retorna 404.
- **Auditoria**: criação, alteração, liquidação (com saldo resultante), desativação, reativação e estorno.
- **Limitações**: o estorno de um recebimento (contas a receber) **não** é bloqueado quando reduz o saldo abaixo do
  que já foi pago (o saldo apenas fica negativo e destacado); não há conciliação bancária, anexos (nota fiscal),
  centros de custo, liquidação parcial em várias baixas nem aviso por e-mail/WhatsApp; os relatórios de contas a pagar
  se resumem à lista filtrável com total.

## Cobrança bancária — boleto registrado com QR Code Pix (Configurações → Cobrança bancária)

Cada escola tem a **sua** configuração (provedor, ambiente, chave de API e dados do banco). Escolas diferentes podem usar bancos/provedores diferentes.

**Fluxo obrigatório (tela única `/configuracoes/cobranca/`):**
1. **Dados do banco:** provedor (hoje *Asaas* ou *Banco simulado* para treinamento), ambiente (sandbox/produção), chave de API (criptografada, nunca exibida) e dados de conferência (banco, agência, conta, beneficiário, instruções).
2. **Boleto de teste:** boleto de valor baixo (padrão R$ 5,00) com pagador informado na tela. **Não é cobrança de aluno e não gera recebimento nem altera o saldo.**
3. **Confirmar o recebimento:** pelo aviso do banco (webhook) ou pelo botão *consultar no banco*. No modo simulado há o botão *Simular pagamento*.
4. **Ativar:** só é liberado com o teste **pago**, feito com a configuração (provedor + ambiente + chave) que está valendo. Trocar qualquer um desses três depois volta a escola para *Em configuração* e exige novo teste.

**Depois de ativa:**
- Parcela **nova** nasce com boleto registrado no banco (com linha digitável, código de barras, QR Code Pix e “copia e cola”). Parcelas **existentes** em aberto ganham boleto pelo botão *Gerar boletos das parcelas em aberto* (até 100 por clique).
- O pagador do boleto é o **responsável financeiro** da parcela; ele precisa ter CPF/CNPJ válido. Se faltar, o boleto fica com situação *Erro* e a mensagem diz o que corrigir; depois é só *Tentar de novo*.
- Parcela a vencer: boleto com o valor do principal e **multa/juros do contrato** (o banco calcula o atraso). Parcela já vencida: o banco não aceita vencimento no passado, então o boleto sai com vencimento **hoje** e os encargos já somados.
- **Pagamento no banco** → baixa automática: gera `Recebimento` na forma **Boleto bancário** (operador “Banco (provedor)”), quita a parcela e audita. O aviso é **confirmado direto no banco** antes de baixar (aviso forjado não baixa nada) e é **idempotente** (mesmo evento ou mesmo boleto nunca gera dois recebimentos).
- **Divergências** (valor pago diferente do devido, parcela já quitada/cancelada, estorno no banco) **não** são resolvidas automaticamente: ficam marcadas na lista de Cobranças para conferência.
- Desconto, mudança de vencimento, correção de valor e cancelamento de parcela **atualizam ou cancelam o boleto no banco**. Parcela recebida no caixa tem o boleto pendente cancelado.
- Falha do banco nunca quebra matrícula, ajuste ou recebimento: o boleto fica em *Erro* e pode ser reenviado. A rotina `python manage.py conciliar_cobrancas` (ou o botão *Conciliar com o banco agora*) consulta os boletos pendentes e recupera avisos perdidos.
- **Boleto não é forma de caixa:** o operador não escolhe “Boleto” no caixa; essa forma só entra pela baixa do banco.

**Permissões:** `configurar_cobranca` (só Administrador da escola); `view_cobranca`, `gerar_boleto`, `cancelar_boleto` (Financeiro); Consulta só visualiza; Secretaria não acessa.

**Segurança:** chave de API e token do webhook ficam **criptografados** (`BILLING_CRYPTO_KEY` no `.env`; sem ela deriva-se da `DJANGO_SECRET_KEY`). O webhook (`/webhooks/cobranca/<id>/`) exige o token no cabeçalho `asaas-access-token`, limita o tamanho do corpo e cada escola tem URL e token próprios. Nenhum segredo vai para log, auditoria ou mensagens de erro.

**Limitações conhecidas:**
- Só o conector **Asaas** (e o simulado) existe; bancos diretos exigem um conector próprio (`apps/billing/providers/`) e homologação do banco.
- O conector Asaas foi escrito conforme a documentação pública e testado com respostas simuladas; **precisa ser validado no sandbox do Asaas com a conta da escola** antes de produção (ex.: confirmar o QR Code Pix no boleto, que exige chave Pix cadastrada na conta Asaas; no sandbox o QR Code não é pagável e multa/juros não são aplicados).
- O webhook precisa de URL pública HTTPS (`DJANGO_SITE_URL`).
- Estorno de um recebimento originado por boleto reabre a parcela, mas **não** gera novo boleto automaticamente (use *Gerar boleto* na parcela).
- Pagamento parcial de boleto não é comum nos bancos; se ocorrer, a parcela fica *Parcial* e a cobrança marcada com divergência.

## Assistente de IA (Configurações → Assistente de IA)

- **Módulo opcional.** O administrador do sistema o habilita por escola no cadastro da escola (assinatura contratada). Só então
  a escola vê a tela de integração.
- **Chave própria.** Cada escola informa a sua chave de API da Anthropic (Claude) e paga o uso direto ao fornecedor; o sistema só integra.
  A chave é gravada criptografada e nunca é exibida de novo.
- **Ativação.** Exige: módulo habilitado, chave informada, termos de uso aceitos (usuário e data) e teste de conexão bem-sucedido.
  Trocar a chave desativa o assistente até novo teste. O administrador da escola pode desativar a qualquer momento.
- **Quem usa.** Administrador, Secretaria e Financeiro (Consulta não). Cada pessoa só tem as análises que as suas permissões já permitem.
- **Dados e LGPD.** O assistente só lê **totais**: nunca nomes de alunos/responsáveis, CPF, endereço, telefone, e-mail ou fornecedores.
  Grupos com menos de 3 pessoas são reunidos em "Outros". CPF, CNPJ, e-mail e telefone digitados na pergunta são removidos antes do envio.
  Nada é alterado no sistema pela IA (somente leitura).
- **Limite.** A escola define um limite mensal de tokens; ao atingi-lo o assistente trava até o mês seguinte (0 = sem limite).
- **Auditoria.** Ficam registradas a habilitação, a configuração, a ativação e cada pergunta (ferramentas usadas e consumo), mas **não o texto da pergunta**.
- **Confiança nos números.** Os valores vêm do sistema, não da "memória" da IA; mesmo assim, confira dados críticos antes de decidir.

## Administradores, primeiro acesso e níveis de acesso

**Dois níveis de administração**
- **Administrador do sistema (superadmin):** entra pelo "modo sistema" do login. Cadastra escolas e administradores escolares,
  redefine senhas e cuida das configurações globais (séries de referência etc.). Para ver ou gerenciar os dados de uma escola ele usa
  **Escolas → Acessar dados** (*modo suporte*): a escola fica aberta na sessão, aparece uma faixa "Modo suporte" e cada abertura é
  registrada na auditoria (`SUPORTE_ACESSO_ESCOLA`). Sem escola aberta, as telas de secretaria/financeiro respondem 403. Dá para trocar de
  escola a qualquer momento e sair com **Sair da escola**.
- **Administrador da escola:** só enxerga e gerencia a própria escola (alunos, turmas, séries, usuários, configurações) e as próprias
  credenciais. Não acessa a área do sistema (403) nem outras escolas; adulterar a sessão não abre outra escola.
- A validação é feita no servidor em toda requisição (permissões + escola da sessão/perfil); ocultar o menu é só conveniência.

**Primeiro acesso (senha provisória)**
1. Ao cadastrar um administrador (ou redefinir a senha dele), a senha informada é **provisória** e o perfil fica marcado para troca.
   O mesmo vale para usuários criados ou com senha redefinida por outra pessoa na tela *Usuários e permissões*.
2. Opcionalmente o sistema **envia um e-mail** com o link de acesso, a escola, o usuário e a senha provisória (requer e-mail do usuário e
   `EMAIL_HOST` configurado). Se o envio falhar, o cadastro continua e o operador é avisado para informar a senha por outro meio.
3. No primeiro login o usuário é levado à tela **Crie a sua senha**; nenhuma outra tela funciona (só sair). Precisa informar a senha
   provisória, uma nova senha válida (as regras de senha do sistema) e diferente da atual.
4. Concluída a troca: a senha provisória deixa de valer, o acesso normal é liberado e fica registrado `SENHA_INICIAL_SUBSTITUIDA`
   (trocas voluntárias posteriores: `SENHA_ALTERADA_PELO_USUARIO`, em *Alterar minha senha*, no menu do usuário).
5. Usuários já existentes **não** são afetados (a marcação nasce desligada). Senhas nunca vão para a auditoria nem para o log.

