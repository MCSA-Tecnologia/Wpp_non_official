# AutoWpp

Orquestrador de disparo de mensagens no WhatsApp com múltiplas contas, distribuição automática de contatos e captura de leads por autoatendimento.

Este projeto combina:
- **Python (`orchestrator.py`)** para coordenar autenticação, geração de contatos e ciclo de execução.
- **Node.js (`index.js`)** para conectar no WhatsApp Web, enviar mensagens, confirmar entrega e atender respostas.

---

## 1) Visão geral da arquitetura

### Componentes principais

1. **`orchestrator.py` (controle central)**
   - Busca contatos no banco de dados.
   - Inicia instâncias Node por conta (ex.: `account_1`, `account_2`).
   - Aguarda autenticação das contas via QR Code.
   - Gera/atualiza `contacts.json` com distribuição por conta (`sentBy`).
   - Reinicia os bots para envio e monitoramento.

2. **`index.js` (bot por conta)**
   - Faz login com sessão local (`LocalAuth`) por `accountId`.
   - Envia somente os contatos atribuídos à conta atual.
   - Atualiza status de envio (`sent`, `sentAt`) e entrega (`delivered`, `ackLevel`, `deliveredAt`).
   - Mantém auto-resposta ativa para capturar CPF/CNPJ + e-mail.

3. **`contacts.json` (fila de envios)**
   - Arquivo intermediário compartilhado entre Python e Node.
   - Define mensagem, telefone, atraso e conta responsável (`sentBy`).

---

## 2) Fluxo operacional completo (3 fases)

O `orchestrator.py` executa um fluxo em 3 fases para reduzir conflitos de browser/sessão e garantir distribuição correta:

### Fase 1 — Autenticação
- Inicia todos os bots definidos em `ACCOUNTS`.
- Exibe QR Code de cada conta no terminal.
- Aguarda **todas** as contas iniciadas autenticarem com sucesso.
- Limpa `contacts.json` antes do envio para evitar reaproveitar dados antigos.

### Fase 2 — Preparação e distribuição
- Para os bots para liberar recursos/processos.
- Busca contatos do banco SQL Server.
- Converte os dados para `contacts.json`.
- Distribui os contatos entre contas autenticadas em round-robin.
- Remove contatos já entregues no mesmo dia (com base no backup `contacts.json.prev`) para reduzir duplicidade.

### Fase 3 — Envio e monitoramento
- Reinicia os bots com sessões já autenticadas.
- Cada conta envia apenas contatos com `sentBy == accountId`.
- O bot permanece ativo para:
  - confirmar ACK de entrega/leitura;
  - responder mensagens recebidas;
  - capturar leads (CPF/CNPJ + e-mail) e registrar no Google Sheets.

---

## 3) Pré-requisitos

- **Node.js 18+**
- **Python 3.8+**
- **SQL Server** acessível com credenciais em `settings.py`
- Arquivos de integração Google:
  - `Tetrakey.json` (credenciais OAuth)
  - `token.json` (token OAuth)

Instalação de dependências:

```bash
npm install whatsapp-web.js qrcode-terminal axios googleapis
pip install pandas pyodbc
```

---

## 4) Configuração detalhada

## 4.1 Arquivo `.env`

Crie um `.env` na raiz do projeto com:

```env
# Google Sheets
GOOGLE_SHEET_ID=
GOOGLE_SHEET_RANGE=A:D

# Relatório de erros
ERROR_REPORT_URL=
ERROR_REPORT_AUTH_TOKEN=
ERROR_REPORT_HEADER_KEY=
ERROR_REPORT_HEADER_VALUE=

# Relatório de sucesso
SUCCESS_REPORT_URL=
SUCCESS_REPORT_HEADER_KEY=
SUCCESS_REPORT_HEADER_VALUE=
```

> Observação importante: no estado atual do código, essas variáveis são tratadas como obrigatórias em `index.js`. Se estiverem vazias, o processo encerra com erro.

## 4.2 `settings.py`

Garanta que o arquivo contenha:
- Credenciais de conexão (`SERVER_OLD`, `DATABASE_OLD`, `USERNAME_OLD`, `PASSWORD_OLD`).
- Consulta SQL em `QUERY_CLIENTS_PHONE` com coluna **`Telefone`**.
- Mensagem padrão em `CONTACT_MESSAGE` (string ou objeto com `DEFAULT_MESSAGE`).

---

## 5) Estrutura do `contacts.json`

Exemplo gerado pelo orquestrador:

```json
[
  {
    "phone": "+5511999999999",
    "message": "Olá! Temos uma proposta para você.",
    "delay": 30000,
    "sent": false,
    "sentBy": "account_1",
    "delivered": false,
    "deliveredAt": null,
    "ackLevel": null,
    "sentAt": null
  }
]
```

### Significado dos campos
- `phone`: número normalizado, idealmente com DDI (`+55...`).
- `message`: texto a enviar.
- `delay`: espera base (ms) entre mensagens da mesma conta.
- `sent`: marca envio concluído com sucesso.
- `sentBy`: conta responsável por este contato.
- `sentAt`: timestamp de envio ou string de erro (`ERROR | code | message | timestamp`).
- `delivered`: indica confirmação de entrega (ACK >= 2).
- `deliveredAt`: timestamp da confirmação.
- `ackLevel`: nível de confirmação WhatsApp (`2=delivered`, `3=read`, `4=played`).

---

## 6) Principais funções e como elas funcionam

## 6.1 `orchestrator.py`

### `fetch_negociador_df()`
- Tenta executar `QUERY_CLIENTS_PHONE` no SQL Server via `pyodbc`.
- Retorna `DataFrame` com contatos.
- Em falha, usa fallback `settings.df`.

### `df_to_contacts_json(df, message, output_path, account_ids)`
- Valida presença da coluna `Telefone`.
- Normaliza números para formato com `+55` quando necessário.
- Monta lista de contatos com campos de rastreio de envio/entrega.
- Distribui `sentBy` alternando entre `account_ids`.
- Salva JSON formatado.

### `start_bot(account)`
- Executa `node index.js <account_id> contacts.json` em subprocesso.
- Redireciona logs para monitoramento no terminal do orquestrador.

### `monitor_authentication(process, account)`
- Lê saída do processo Node em tempo real.
- Marca conta como autenticada ao detectar logs de sucesso.
- Mostra lembrete ao detectar geração de QR.

### `wait_for_all_authentication()`
- Aguarda até todas as contas iniciadas autenticarem.
- Timeout padrão de 120s.
- Em timeout, lista contas autenticadas e não autenticadas.

### `build_contacts_json_final()`
- Gera `contacts.json` final somente após autenticação.
- Reaplica distribuição round-robin entre contas válidas.
- Remove contatos já entregues no dia atual (se existir backup prévio).

### `monitor_and_commands(accounts)`
Loop de comandos interativos:
- `status`: estado das contas.
- `stats`: totais, enviados, pendentes e erros por conta.
- `delivery`: métricas de entrega/leitura.
- `terminate`: encerra tudo com segurança.

### `main()`
- Executa as 3 fases (autenticação → preparação → envio).
- Mantém o processo vivo para monitoramento e auto-resposta.

---

## 6.2 `index.js`

### `loadEnv()` e `requireEnv(key)`
- `loadEnv` lê manualmente `.env` e injeta no `process.env`.
- `requireEnv` lança erro se variável obrigatória não existir.

### `loadContacts()`
- Lê `contacts.json` com retentativas simples.
- Usado por funções de envio e atualização de status.

### `markContactAsSent(phoneNumber, success, error)`
- Atualiza `sent`, `sentBy` e `sentAt` de um contato.
- Em falha, grava string estruturada de erro em `sentAt`.
- Persiste alterações de forma atômica (`.tmp` + `rename`).

### `markContactAsDelivered(phoneNumber, ackLevel)`
- Marca `delivered=true`, define `deliveredAt` e `ackLevel`.
- Só aplica se o contato já estiver `sent=true`.

### `waitForDelivery(client, message, timeoutMs)`
- Aguarda evento `MESSAGE_ACK` da mensagem enviada.
- Resolve com nível ACK recebido ou `null` em timeout.

### `sendMessagesAndStayAlive()`
- Filtra contatos da conta atual (`sentBy === accountId` e `sent=false`).
- Envia mensagens com delay + jitter.
- Atualiza status de envio e entrega.
- Ao final, mantém bot ativo para auto-resposta.

### `sendMessagesAndExit()`
- Fluxo one-shot: envia e finaliza processo.

### `client.on('message_create', ...)`
- Fluxo de captura de lead:
  1. solicita/valida CPF ou CNPJ;
  2. solicita e-mail;
  3. registra em Google Sheets;
  4. reporta sucesso em endpoint externo.

---

## 7) Como rodar o programa (passo a passo)

## 7.1 Execução recomendada (orquestrador)

1. Instale dependências:
   ```bash
   npm install whatsapp-web.js qrcode-terminal axios googleapis
   pip install pandas pyodbc
   ```
2. Configure `.env` e `settings.py`.
3. Inicie o orquestrador:
   ```bash
   python3 orchestrator.py
   ```
4. Escaneie o QR Code de cada conta solicitada.
5. Aguarde conclusão das fases automaticamente.
6. Use comandos no terminal para acompanhar (`status`, `stats`, `delivery`, `terminate`).

## 7.2 Execução de depuração (conta única)

```bash
node index.js account_1 contacts.json
```

Para modo one-shot:

```bash
node index.js account_1 contacts.json oneshot
```

---

## 8) Resultados esperados

Ao executar corretamente, você deve observar:

1. **Na autenticação**
   - Logs de QR code.
   - Mensagem `Authenticated successfully` / `Client is ready`.

2. **Na preparação**
   - Geração do `contacts.json` com distribuição por `sentBy`.
   - Possível remoção de contatos já entregues no dia.

3. **No envio**
   - Logs de envio por número.
   - Atualização de `sent=true` e `sentAt`.
   - Quando disponível, confirmação de entrega com `ackLevel >= 2`.

4. **No pós-envio**
   - Bot segue ativo para responder mensagens.
   - Leads preenchidos são enviados para Google Sheets.
   - Endpoint de sucesso recebe payload configurado.

---

## 9) Troubleshooting rápido

### Erro de variáveis de ambiente ausentes
- Sintoma: processo Node encerra ao iniciar.
- Verifique e preencha chaves obrigatórias no `.env`.

### Conta autenticou, mas não envia
- Verifique se há contatos com `sentBy` da conta e `sent=false`.
- Use comando `stats` para confirmar distribuição.

### Conflito de sessão/browser
- Evite subir múltiplos `node index.js` manualmente para o mesmo `accountId`.
- Prefira sempre o fluxo único com `python3 orchestrator.py`.

### Entrega não confirmada
- ACK depende de retorno do WhatsApp/WebSocket; pode haver envio com `sent=true` sem `delivered=true` imediato.

---

## 10) Boas práticas operacionais

- Execute uma conta por perfil (`accountId`) para evitar colisão de sessão.
- Mantenha backup de `contacts.json` e monitoramento diário de falhas.
- Valide periodicamente tokens e credenciais Google (`token.json`, `Tetrakey.json`).
- Faça testes com poucos contatos antes de lotes grandes.
