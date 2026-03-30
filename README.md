# AutoWpp

Orquestrador de disparo de mensagens no WhatsApp com múltiplas contas, distribuição automática de contatos e captura de leads por autoatendimento.

Este projeto combina:
- **Python (`orchestrator.py`)** para coordenar autenticação, geração de contatos e ciclo de execução.
- **Node.js (`index.js`)** para conectar no WhatsApp Web, enviar mensagens, confirmar entrega e atender respostas.
- **Gradio (`frontend.py`)** para interface web com controle visual de contas, mensagem e upload de CSV.
- **CLI (`orchestrator.py --chips ...`)** para automação via terminal/scripts.

---

## 1) Visão geral da arquitetura

### Componentes principais

1. **`orchestrator.py` (controle central + CLI)**
   - Busca contatos no banco de dados ou de um CSV enviado pelo usuário.
   - Inicia instâncias Node por conta (ex.: `account_1` a `account_6`).
   - Aguarda autenticação das contas via QR Code.
   - Gera variantes de mensagem (`+0` a `+14`) e distribui ciclicamente entre contatos.
   - Suporta personalização com `NOME_DO_CLIENTE` (substituído pelo primeiro nome do CSV).
   - Gera/atualiza `contacts.json` com distribuição por conta (`sentBy`).
   - Reinicia os bots para envio e monitoramento.
   - Salva log de cada execução em `logs/run_<timestamp>.json`.
   - Modo CLI para automação via terminal.

2. **`frontend.py` (interface Gradio)**
   - Interface web para controle visual do orquestrador.
   - Campo de mensagem com suporte a `NOME_DO_CLIENTE`.
   - Upload de CSV de contatos (Nome, Telefone).
   - Dropdown para selecionar número de chips/contas (1–6).
   - Displays em tempo real por conta (QR code, logs de envio).
   - Botões Run/Stop com desabilitação durante execução.

3. **`index.js` (bot por conta)**
   - Faz login com sessão local (`LocalAuth`) por `accountId`.
   - Envia somente os contatos atribuídos à conta atual.
   - Atualiza status de envio (`sent`, `sentAt`) e entrega (`delivered`, `ackLevel`, `deliveredAt`).
   - Flag `--no-reply` desabilita auto-resposta quando executado pelo orquestrador.
   - Auto-resposta ativa (modo standalone) para capturar CPF/CNPJ + e-mail.

4. **`contacts.json` (fila de envios)**
   - Arquivo intermediário compartilhado entre Python e Node.
   - Define mensagem, telefone, atraso e conta responsável (`sentBy`).

5. **`logs/` (histórico de execuções)**
   - Um arquivo JSON por execução com timestamp, resumo e todos os contatos.

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
- Busca contatos do banco SQL Server **ou** usa CSV enviado pelo usuário (CSV tem prioridade).
- Gera 15 variantes da mensagem base (`mensagem+0` a `mensagem+14`).
- Converte os dados para `contacts.json`, atribuindo variantes ciclicamente.
- Se a mensagem contém `NOME_DO_CLIENTE`, substitui pelo primeiro nome de cada contato (coluna `Nome` do CSV).
- Distribui os contatos entre contas autenticadas em round-robin.
- Remove contatos já entregues no mesmo dia (com base no backup `contacts.json.prev`), exceto quando CSV é usado.

### Fase 3 — Envio e monitoramento
- Reinicia os bots com sessões já autenticadas e flag `--no-reply`.
- Cada conta envia apenas contatos com `sentBy == accountId`.
- O bot confirma ACK de entrega/leitura.
- Ao final, salva log completo em `logs/run_<timestamp>.json`.

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
pip install pandas pyodbc python-decouple gradio
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

### `generate_message_variants(base_message, count=15)`
- Gera array de variantes da mensagem (`mensagem+0` a `mensagem+14`).
- Função placeholder — substituir corpo por lógica de variação real futuramente.

### `df_to_contacts_json(df, message, output_path, account_ids)`
- Valida presença da coluna `Telefone`.
- Normaliza números para formato com `+55` quando necessário.
- Atribui variantes de mensagem ciclicamente entre contatos.
- Se `Nome` presente no DataFrame e mensagem contém `NOME_DO_CLIENTE`, substitui pelo primeiro nome.
- Distribui `sentBy` alternando entre `account_ids`.
- Salva JSON formatado.

### `start_bot(account)`
- Executa `node index.js <account_id> contacts.json persistent --no-reply` em subprocesso.
- Redireciona logs para monitoramento no terminal do orquestrador.

### `monitor_authentication(process, account)`
- Lê saída do processo Node em tempo real.
- Marca conta como autenticada ao detectar logs de sucesso.
- Mostra lembrete ao detectar geração de QR.

### `wait_for_all_authentication()`
- Aguarda até todas as contas iniciadas autenticarem.
- Timeout padrão de 120s.
- Em timeout, lista contas autenticadas e não autenticadas.

### `build_contacts_json_final(custom_message=None)`
- Gera `contacts.json` final somente após autenticação.
- Usa mensagem customizada se fornecida, senão `settings.CONTACT_MESSAGE`.
- Gera variantes de mensagem antes de montar contatos.
- Reaplica distribuição round-robin entre contas válidas.
- Remove contatos já entregues no dia atual (se existir backup prévio), exceto quando CSV é usado.

### `log_sent_messages()`
- Captura estado final do `contacts.json` após envio.
- Salva em `logs/run_<timestamp>.json` com resumo (total, enviados, erros, entregues, contas usadas).

### `main(tests=False, custom_message=None)`
- Executa as 3 fases (autenticação → preparação → envio).
- Aceita mensagem customizada e usa CSV de contatos se disponível.
- Salva log ao final da execução.

### `cli()`
- Entry point CLI com `argparse`. Veja seção 7.2 abaixo.

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

### `client.on('message_create', ...)` (somente sem `--no-reply`)
- Fluxo de captura de lead:
  1. solicita/valida CPF ou CNPJ;
  2. solicita e-mail;
  3. registra em Google Sheets;
  4. reporta sucesso em endpoint externo.
- Desabilitado quando o bot é iniciado com flag `--no-reply` (padrão do orquestrador).

---

## 7) Como rodar o programa

### 7.1 Interface Web (Gradio)

```bash
python frontend.py
```

Abre em `http://localhost:7860`. Na interface:

1. **(Opcional)** Escreva a mensagem no campo **Mensagem**. Deixe vazio para usar `settings.CONTACT_MESSAGE`.
   - Use `NOME_DO_CLIENTE` na mensagem para personalizar com o primeiro nome do CSV.
2. **(Opcional)** Faça upload de um **CSV** com colunas `Nome` e `Telefone`. Sem CSV, usa query do banco.
3. Selecione o **Número de Chips** (1–6).
4. Clique em **Run Disparos**.
5. Escaneie os QR Codes exibidos nos painéis de cada conta.
6. Acompanhe o progresso em tempo real. Use **Stop** para interromper.

### 7.2 CLI (automação via terminal)

```bash
python orchestrator.py [opções]
```

**Argumentos:**

| Flag | Tipo | Obrigatório | Descrição |
|------|------|:-----------:|-----------|
| `--test` | flag | Não | Modo teste (usa `settings.df` em vez do banco) |
| `--chips N` | int (1–6) | Não | Número de contas. Omitir usa `ACCOUNTS` do código |
| `--message "..."` | string | Não | Mensagem base. Omitir usa `settings.CONTACT_MESSAGE` |
| `--csv path` | string | Não | Caminho do CSV (colunas: `Nome`, `Telefone`). Omitir usa query do banco |

**Exemplos:**

```bash
# Produção: 3 chips, mensagem personalizada, contatos de CSV
python orchestrator.py --chips 3 --message "Oi NOME_DO_CLIENTE, temos uma oferta!" --csv contatos.csv

# Produção: 2 chips, mensagem e contatos do banco (defaults)
python orchestrator.py --chips 2

# Produção: tudo default (ACCOUNTS do código + banco + settings.CONTACT_MESSAGE)
python orchestrator.py

# Teste: 1 chip, usa settings.df como contatos
python orchestrator.py --test --chips 1
```

### 7.3 CSV de contatos — formato esperado

```csv
Nome,Telefone
Jon Doe,31991376705
Jessie J,4197233448
Mister X,3198347777
```

- **`Telefone`** (obrigatório): número do celular. Normalizado automaticamente para `+55...`.
- **`Nome`** (opcional): usado para substituir `NOME_DO_CLIENTE` na mensagem (apenas primeiro nome).
- Quando CSV é fornecido, a deduplicação de contatos já enviados no dia é **desabilitada** (CSV tem prioridade).

### 7.4 Execução direta do bot (depuração)

```bash
# Modo persistente com auto-resposta
node index.js account_1 contacts.json

# Modo persistente sem auto-resposta (como o orquestrador usa)
node index.js account_1 contacts.json persistent --no-reply

# Modo one-shot (envia e encerra)
node index.js account_1 contacts.json oneshot
```

---

## 8) Resultados esperados

Ao executar corretamente, você deve observar:

1. **Na autenticação**
   - Logs de QR code (escaneáveis no painel da interface ou no terminal).
   - Mensagem `Authenticated successfully` / `Client is ready`.

2. **Na preparação**
   - `Generated 15 message variant(s)` — variantes da mensagem criadas.
   - Geração do `contacts.json` com distribuição por `sentBy` e variantes cíclicas.
   - Possível remoção de contatos já entregues no dia (somente sem CSV).

3. **No envio**
   - Logs de envio por número.
   - Atualização de `sent=true` e `sentAt`.
   - Quando disponível, confirmação de entrega com `ackLevel >= 2`.

4. **No pós-envio**
   - Log salvo em `logs/run_<timestamp>.json`.
   - Bots encerrados automaticamente.

### Estrutura do log (`logs/run_*.json`)

```json
{
  "run_timestamp": "2026-03-30T14:25:00.123456",
  "summary": {
    "total": 100,
    "sent": 95,
    "errors": 3,
    "delivered": 80,
    "accounts_used": ["account_1", "account_2"]
  },
  "contacts": [ ... ]
}
```

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
