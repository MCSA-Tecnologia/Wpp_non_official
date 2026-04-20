# AutoWpp

Orquestrador de disparos no WhatsApp com múltiplas contas, interface web em Gradio, distribuição automática de contatos e pós-processamento de registros RO.

O projeto usa:

- `frontend.py` para a interface web.
- `orchestrator.py` para autenticação, geração da fila e coordenação do envio.
- `index.js` para conectar ao WhatsApp Web, enviar mensagens e acompanhar ACK de entrega.
- `ro_service.py` para registrar contatos elegíveis em lote após o disparo.
- `app.py` para endpoints auxiliares de integração.

## Funcionalidades

- Autenticação de 1 a 6 contas de WhatsApp com sessão local por `accountId`.
- Exibição de QR Code e logs por conta na interface web.
- Execução em 3 fases: autenticação, preparação da fila e envio.
- Upload de arquivo de contatos em `CSV` ou `XLSX`.
- Leitura de contatos diretamente do banco SQL Server quando nenhum arquivo é enviado.
- Personalização de mensagem com o placeholder `NOME_DO_CLIENTE`.
- Distribuição round-robin de contatos entre contas autenticadas.
- Geração de `contacts.json` compartilhado entre Python e Node.
- Controle de status por contato:
  - `sent`
  - `sentAt`
  - `delivered`
  - `deliveredAt`
  - `ackLevel`
  - `roRegistered`
  - `roStatus`
- Deduplicação de contatos já processados no dia com base em `contacts.json.prev`.
- Suporte a `buttonUrl` anexada à mensagem enviada.
- Registro de logs por execução em `logs/run_<timestamp>.json`.
- Disparo de lotes RO/Calltech ao final da execução com controle de sucesso e erro.
- Modo CLI para automação sem interface.
- Modo standalone do bot com captura de lead por CPF/CNPJ + e-mail quando executado sem `--no-reply`.

## Fluxo Atual

### 1. Autenticação

O orquestrador inicia os bots configurados, exibe os QR Codes e aguarda todas as contas autenticarem.

### 2. Preparação

Depois da autenticação:

- `contacts.json` é limpo para evitar reaproveitamento indevido.
- os contatos são carregados do banco ou de um arquivo `CSV/XLSX`;
- a mensagem base é aplicada a cada contato;
- `NOME_DO_CLIENTE` é substituído pelo primeiro nome, quando disponível;
- os contatos são atribuídos entre as contas autenticadas;
- o estado anterior é preservado para evitar reenvios e duplicidade em RO.

### 3. Envio

Os bots são reiniciados com sessão já autenticada e:

- cada conta envia apenas os contatos atribuídos a ela;
- o envio é marcado em `contacts.json`;
- o ACK do WhatsApp é acompanhado para registrar entrega/leitura;
- ao final, o estado da execução é salvo em `logs/`.

### 4. Pós-processamento RO

Após o disparo via interface web, `ro_service.py` identifica contatos enviados com sucesso e ainda não registrados, monta lotes e envia os registros para o endpoint configurado.

## Requisitos

### Ambiente

- Node.js 18+
- Python 3.10+ recomendado
- Google Chrome ou Chromium disponível para o Puppeteer
- SQL Server acessível para os fluxos que usam banco

### Arquivos obrigatórios

- `.env`
- `Tetrakey.json`
- `token.json`

### Dependências Python

As importações atuais do projeto exigem pelo menos:

- `pandas`
- `pyodbc`
- `python-decouple`
- `gradio`
- `requests`
- `fastapi`
- `uvicorn`
- `pydantic`
- `colorama`
- `google-api-python-client`
- `google-auth`
- `google-auth-oauthlib`

Exemplo:

```bash
pip install pandas pyodbc python-decouple gradio requests fastapi uvicorn pydantic colorama google-api-python-client google-auth google-auth-oauthlib openpyxl
```

### Dependências Node.js

O bot usa:

- `whatsapp-web.js`
- `qrcode-terminal`
- `axios`
- `googleapis`
- `puppeteer`

Exemplo:

```bash
npm install whatsapp-web.js qrcode-terminal axios googleapis puppeteer
```

Se necessário, instale também o navegador do Puppeteer:

```bash
npx puppeteer browsers install chrome
```

## Configuração

### `.env`

Preencha as variáveis usadas atualmente por `settings.py`, `index.js` e `ro_service.py`.

Exemplo mínimo:

```env
# Banco principal
SERVER=
DATABASE=
DBUSERNAME=
PASSWORD=

# Banco legado
SERVER_OLD=
DATABASE_OLD=
DBUSERNAME_OLD=
PASSWORD_OLD=

# API/header
HEADER_KEY=
AUTH_KEY_GENERAL=

# Google Sheets / lead capture
GOOGLE_SHEET_ID=
GOOGLE_SHEET_RANGE=A:D

# Relatório de erro
ERROR_REPORT_URL=
ERROR_REPORT_AUTH_TOKEN=
ERROR_REPORT_HEADER_KEY=
ERROR_REPORT_HEADER_VALUE=

# Relatório de sucesso
SUCCESS_REPORT_URL=
SUCCESS_REPORT_HEADER_KEY=
SUCCESS_REPORT_HEADER_VALUE=

# RO / Calltech
RO_CALLTECH_ENDPOINT=
RO_TIMEOUT_SECONDS=60
RO_TRIGGER_MIN_COUNT=100
RO_BATCH_SIZE=390
RO_RESUMO_ID=
RO_OPERADOR_ID=
RO_CODIGO_CAMPANHA=000000
RO_CAMPANHA_ID=0
RO_ORIGEM=API - Whatsapp Unofficial
RO_PARCEIRO=API Whatsapp Unofficial
```

### `settings.py`

Revise especialmente:

- credenciais de banco;
- `QUERY_CLIENTS_PHONE`;
- `CONTACT_MESSAGE`;
- `CONTACT_BUTTON_URL`;
- parâmetros padrão de RO.

Observação importante: a `QUERY_CLIENTS_PHONE` do repositório atual parece estar com texto inválido no início. Se você pretende rodar pelo banco, ajuste essa query antes do uso.

## Formato dos contatos

O arquivo de entrada pode ser `CSV` ou `XLSX`.

### Colunas aceitas

- `Telefone` ou `telefone` obrigatória
- `Nome` ou `nome` opcional
- `pessoaId` opcional
- `email` opcional
- `observacao` opcional

Outras colunas também podem ser aproveitadas quando presentes, como:

- `Credor`
- `Campanha`
- `Valor`
- `Aging`
- `MoInadimplentesID`
- `Pessoas_ID`

### Exemplo CSV

```csv
Nome,Telefone,pessoaId,email,observacao
Maria Silva,31999999999,12345,maria@email.com,Cliente prioritário
João Souza,41988888888,67890,joao@email.com,Carteira B
```

## Estrutura do `contacts.json`

Exemplo simplificado:

```json
[
  {
    "phone": "+5531999999999",
    "message": "Bom dia Maria, ...",
    "buttonUrl": "https://wa.me/...",
    "delay": 30000,
    "sent": false,
    "sentBy": "account_1",
    "delivered": false,
    "deliveredAt": null,
    "ackLevel": null,
    "sentAt": null,
    "pessoaId": 12345,
    "email": "maria@email.com",
    "observacao": "Cliente prioritário",
    "roRegistered": false,
    "roRegisteredAt": null,
    "roBatchId": null,
    "roStatus": null,
    "roError": null
  }
]
```

## Uso

### Interface web

Execute:

```bash
python frontend.py
```

A interface sobe em:

```text
http://127.0.0.1:8502
```

Na interface você pode:

1. Informar a mensagem base.
2. Enviar um arquivo `CSV` ou `XLSX` de contatos.
3. Escolher de 1 a 6 chips.
4. Selecionar `Credor` e `Campanha`.
5. Iniciar e acompanhar o disparo.
6. Baixar uma lista de clientes para preenchimento.

### CLI

Execute:

```bash
python orchestrator.py [opções]
```

Argumentos disponíveis:

- `--chips N` define a quantidade de contas de 1 a 6.
- `--message "texto"` sobrescreve `settings.CONTACT_MESSAGE`.
- `--csv caminho` carrega contatos de arquivo.
- `--test` usa `settings.df` em vez de consultar o banco.

Exemplos:

```bash
python orchestrator.py --chips 2
python orchestrator.py --chips 3 --message "Olá NOME_DO_CLIENTE, temos uma proposta."
python orchestrator.py --chips 2 --csv contatos.xlsx
python orchestrator.py --test --chips 1
```

### Execução direta do bot Node.js

Para depuração:

```bash
node index.js account_1 contacts.json persistent --no-reply
```

Outros modos:

```bash
node index.js account_1 contacts.json
node index.js account_1 contacts.json oneshot
```

Comportamento:

- `persistent --no-reply`: usado pelo orquestrador para disparo sem autoatendimento.
- `persistent`: mantém o bot ativo e responde mensagens recebidas.
- `oneshot`: envia as mensagens atribuídas e encerra o processo.

## Logs e saídas

- `contacts.json`: fila compartilhada e estado atual do disparo.
- `contacts.json.prev`: backup usado para deduplicação e reaproveitamento de estado.
- `logs/run_<timestamp>.json`: snapshot final da execução.
- `.wwebjs_auth/`: sessões locais das contas autenticadas.

## Problemas comuns

### O frontend abre, mas não envia

- Verifique se todas as contas autenticaram.
- Confirme se `contacts.json` foi gerado com `sentBy`.
- Verifique se existem contatos com `sent=false`.

### Falha ao carregar contatos

- O arquivo precisa ter a coluna `Telefone` ou `telefone`.
- Para `XLSX`, garanta que `openpyxl` esteja instalado.

### Erro nas variáveis de ambiente

- `index.js` exige várias chaves obrigatórias em `.env`.
- Se uma delas estiver ausente, o processo Node encerra ao iniciar.

### Falha ao consultar o banco

- Revise credenciais em `settings.py`/`.env`.
- Revise a query `QUERY_CLIENTS_PHONE`.
- Se necessário, rode em modo `--test` ou use arquivo de entrada.

### RO não registra nada

- Apenas contatos com envio bem-sucedido e `roRegistered=false` são elegíveis.
- O processamento respeita `RO_TRIGGER_MIN_COUNT`, exceto no fechamento final da execução.
- Contatos sem `pessoaId` geram erro e não entram no lote.

## Estrutura principal

```text
.
├── frontend.py
├── orchestrator.py
├── index.js
├── ro_service.py
├── app.py
├── settings.py
├── contacts.json
├── logs/
├── .wwebjs_auth/
└── .env
```
