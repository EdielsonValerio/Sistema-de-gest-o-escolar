# EduNex — Políticas de segurança (aplicadas pelo sistema)

> Texto técnico de apoio. **Não é parecer jurídico**: a conformidade com a LGPD e a definição dos papéis de controlador e
> operador devem ser validadas por profissional especializado.

## 1. Senhas
Mínimo de 12 caracteres, bloqueio de senhas comuns, apenas numéricas ou parecidas com os dados do usuário; Argon2
(hashes antigos são regravados no próximo login); senhas longas e gerenciadores de senhas aceitos. Nenhuma senha em texto
puro, e-mail ou log. Contas novas **não recebem senha por e-mail**: recebem link de ativação (uso único, 3 dias).
Senha definida por administrador é provisória e deve ser trocada no primeiro acesso.

## 2. Bloqueio de login e recuperação
5 falhas por conta ou 30 por IP em 15 min bloqueiam temporariamente (15 min, dobrando até 4 h; nunca permanente, para
impedir negação de serviço contra uma conta). Chaves de conta/IP são HMAC (não guardam o login). A recuperação de senha
responde igual para contas existentes e inexistentes, é limitada (3 por conta, 10 por IP), usa token de uso único com
validade de 1 hora, invalida sessões e avisa o titular. Webhook bancário: 10 tokens errados por IP bloqueiam o IP.

## 3. Autenticação multifator
TOTP (RFC 6238) com 8 códigos de recuperação de uso único (guardados com hash). Obrigatória, em produção, para o
administrador do sistema e os perfis Administrador e Financeiro. Enquanto pendente, nenhuma tela além do cadastro/validação
da MFA e da saída fica acessível; o login do Django admin é redirecionado ao login protegido. Falhas limitadas por conta.
Administrador da escola pode redefinir a MFA de usuário da própria escola (auditado).

## 4. Sessões
Cookie `HttpOnly`, `Secure` (produção) e `SameSite=Lax`; validade de 12 h; chave de sessão trocada no login (anti
session fixation); logout invalida; redefinição de senha invalida as demais sessões; escola inativada derruba as sessões.

## 5. Controle de acesso
Autorização sempre no servidor (permissões por perfil: Administrador, Secretaria, Financeiro, Consulta, Professor,
Coordenador; administrador do sistema separado do administrador da escola). Todo dado de negócio pertence a uma escola e é
filtrado por ela; chaves estrangeiras entre escolas são recusadas; IDs do navegador nunca são confiáveis. Acessos negados
geram evento de segurança.

## 6. Retenção de dados
| Dado | Retenção | Observação |
|------|----------|------------|
| Tentativas de login | 2 dias | `limpar_seguranca` |
| Tokens de conta | removidos 30 dias após expirar | idem |
| Conversas do assistente de IA | 180 dias sem atividade | idem; ajustável (`--dias-ia`) |
| Eventos de segurança | 400 dias | idem |
| **Auditoria** | **não é apagada pelo sistema** | trilha imutável |
| **Documentos escolares e dados financeiros** | **sem exclusão automática** | há obrigações legais de guarda; prazos devem ser definidos pela escola/jurídico |
| Dados cadastrais de alunos/responsáveis | enquanto houver vínculo ou obrigação legal | exclusão/anonimização sob decisão do controlador |

## 7. Proteção de dados pessoais
* **Exportação do titular:** botão "Dados do titular" na ficha (permissão `exportar_dados_titular`, só Administrador): JSON
  com cadastro, responsáveis, matrículas, parcelas, documentos emitidos, histórico externo e resultados finais; auditado.
* **Minimização:** CPF mascarado por padrão; ver CPF completo exige permissão; auditoria e eventos não guardam CPF, senhas,
  tokens nem endereços; logs não registram formulários.
* **Crianças e adolescentes:** acesso restrito por perfil e escola; fotos só por tela autenticada, reprocessadas sem EXIF.
* **Assistente de IA:** as ferramentas enviam **somente números agregados** (grupos com menos de 3 pessoas são agrupados);
  CPF, CNPJ, e-mail, telefone e nomes de pessoas cadastradas na escola digitados na pergunta são removidos antes do envio; chave da IA é cifrada; uso mensal limitado
  por escola; conversas guardadas por 180 dias. **Limite conhecido:** nomes de pessoas não cadastradas ou escritos com erro não são
  detectados — orientar os usuários. A escola deve informar os titulares sobre o uso de fornecedor de IA e do envio ao exterior.
* **Segredos:** chaves de API, token de webhook e segredos TOTP cifrados (Fernet) com rotação (`rotacionar_segredos`).

## 8. Auditoria
Registro imutável (inclusão apenas) de operações administrativas e financeiras: usuário, escola, data/hora, ação, registro e
descrição. Eventos de segurança (bloqueios, falhas de MFA/webhook, acessos negados, tokens inválidos) em tela própria e com
alerta por e-mail (`alertas_seguranca`). Ações sensíveis alertadas: alteração de usuário/perfil, configuração/ativação/suspensão
da cobrança, renovação do token do webhook, configuração da IA, redefinição de senha e criação de administrador.

## 9. Resposta a incidentes (roteiro)
1. **Conter:** inativar usuários/escola afetados (derruba sessões); suspender a cobrança; revogar chaves no provedor.
2. **Preservar:** exportar Auditoria e Eventos de segurança do período; guardar logs do Nginx; não reiniciar sem registrar.
3. **Avaliar:** quais escolas, titulares e categorias de dados; houve acesso a dados pessoais? (usar Auditoria e Eventos).
4. **Rotacionar:** chave do cofre, SECRET_KEY, chaves de API, token do webhook, senhas e MFA dos envolvidos.
5. **Comunicar:** o controlador (escola) avalia a comunicação à ANPD e aos titulares dentro do prazo legal, com apoio jurídico.
6. **Corrigir e aprender:** causa raiz, teste que reproduz a falha, atualização deste relatório.
