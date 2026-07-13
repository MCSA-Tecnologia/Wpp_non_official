# AutoWpp 2

Orquestrador de disparos no WhatsApp com múltiplas contas (1–6 chips), interface web em Gradio, distribuição automática de contatos e pós-processamento RO/Calltech.

Reescrita do projeto original com a mesma lógica de negócio, porém com arquitetura corrigida e simplificada:

| Área | v1 (antigo) | v2 (este projeto) |
|---|---|---|
| Escrita em `contacts.json` | Node e Python escreviam no mesmo arquivo (race condition + busy-wait) | Bots só **anexam** eventos em `runtime/updates_<conta>.jsonl`; o Python é o único que escreve `contacts.json` (merge idempotente) |
| QR Code | Parse de stdout do Node | Arquivo `runtime/qr_<conta>.txt` + render em PNG no Gradio |
| Status das contas | Parse de logs | `runtime/status_<conta>.json` estruturado |
| Validação de número | Nenhuma (erro só na hora do envio) | `getNumberId` antes do envio — número sem WhatsApp vira falha limpa |
| ACK de entrega | Parcial | Evento `message_ack` gravado (delivered/read) com janela de graça configurável |
| Timeout do sender | 60s fixos (matava lotes grandes) | Watchdog de inatividade configurável |
| Delay entre envios | Fixo | Aleatório dentro de janela `MIN/MAX_SEND_DELAY_MS` |
| Query SQL | Texto inválido no início | Corrigida e movida para `.env` (sobrescrevível) |
| Banco | Obrigatório para importar `settings` | Totalmente opcional (import lazy do `pyodbc`) |

Biblioteca de conexão: [`whatsapp-web.js`](https://wwebjs.dev/) (não oficial, via WhatsApp Web + Puppeteer). Alternativa sem navegador: [`@whiskeysockets/baileys`](https://github.com/WhiskeySockets/Baileys) — o desenho por eventos JSONL permite trocar o `bot/index.js` sem tocar no Python.

> **Aviso**: bibliotecas não oficiais violam os Termos de Serviço do WhatsApp e há risco de banimento dos números. Use com volumes moderados, delays realistas e apenas para contatos com relação legítima com a empresa (ex.: cobrança de clientes da carteira). O sistema não contorna bloqueios — os delays servem só para cadência natural de envio.

## Estrutura

```text
autowpp2/
├── frontend.py          # Interface web (Gradio)
├── orchestrator.py      # Fases: auth → fila → envio → RO (também CLI)
├── contacts_loader.py   # CSV/XLSX/banco, normalização, dedup, round-robin
├── ro_service.py        # Registro RO/Calltech em lotes
├── settings.py          # Configuração central (lê .env)
├── bot/
│   └── index.js         # Bot Node (whatsapp-web.js): auth e envio + ACK
├── runtime/             # qr_*.txt, status_*.json, updates_*.jsonl (gerados)
├── logs/                # run_<timestamp>.json (gerados)
├── samples/modelo_contatos.csv
├── install.bat          # Instalador Windows
├── install.sh           # Instalador Linux/macOS
├── requirements.txt
├── package.json
└── .env.example
```

## Instalação

Pré-requisitos: **Python 3.10+** e **Node.js 18+** no PATH.

### Windows

```bat
install.bat
```

O script verifica Python/Node, roda `pip install -r requirements.txt`, `npm install`, baixa o Chrome do Puppeteer e cria o `.env` a partir do `.env.example`.

### Linux / macOS

```bash
./install.sh
```

### Manual

```bash
pip install -r requirements.txt
npm install
npx puppeteer browsers install chrome
cp .env.example .env
```

Para carga via SQL Server, instale também: `pip install pyodbc` (e o ODBC Driver 17/18 da Microsoft).

## Configuração (`.env`)

Todos os valores têm default — o projeto roda com `.env` vazio usando arquivos CSV/XLSX. Principais variáveis:

| Variável | Default | Descrição |
|---|---|---|
| `MIN_SEND_DELAY_MS` / `MAX_SEND_DELAY_MS` | 20000 / 45000 | Janela de delay aleatório entre mensagens, por conta |
| `ACK_GRACE_MS` | 30000 | Tempo esperando ACKs de entrega após o último envio |
| `VALIDATE_NUMBERS` | True | Verifica se o número tem WhatsApp antes de enviar |
| `DEFAULT_COUNTRY_CODE` | 55 | DDI adicionado a números com 10–11 dígitos |
| `SERVER`, `DATABASE`, `DBUSERNAME`, `PASSWORD` | vazio | SQL Server (opcional) |
| `AUTH_MAX_RETRIES` | 2 | Tentativas extras de autenticação por conta (novo QR a cada tentativa) |
| `QUERY_CREDOR_CAMPANHA` | ver `settings.py` | Query dos dropdowns Credor/Campanha na interface |
| `RO_ENABLED` | True | Liga/desliga o pós-processamento RO |
| `RO_TRIGGER_MIN_COUNT`, `RO_BATCH_SIZE` | 100 / 390 | Regras de lote do RO |
| `CONTACT_MESSAGE`, `CONTACT_BUTTON_URL` | ver `settings.py` | Mensagem base e URL anexada |

## Formato dos contatos (CSV/XLSX)

Colunas reconhecidas (case-insensitive, sem acento):

- `Telefone` (obrigatória) — aceita `31 9137-6705`, `31991376705`, `5531991376705`, `+55...`
- `Nome` (opcional) — usado no placeholder `NOME_DO_CLIENTE` (só o primeiro nome)
- `pessoaId` / `Pessoas_ID` / `MoInadimplentesID` (opcional — **necessário para RO**)
- `email`, `observacao` (opcionais)

Exemplo (`samples/modelo_contatos.csv`):

```csv
Nome,Telefone,pessoaId,email,observacao
Maria Silva,31999999999,12345,maria@email.com,Cliente prioritário
João Souza,41988888888,67890,joao@email.com,Carteira B
```

Regras aplicadas na carga:

1. Normalização para `+55DDDNXXXXXXXX` (números sem DDD são descartados como inválidos).
2. Deduplicação dentro do arquivo.
3. Deduplicação contra a execução anterior: telefone com envio bem-sucedido **hoje** (em `contacts.json`/`contacts.json.prev`) não entra na fila.
4. Distribuição round-robin entre as contas autenticadas (`sentBy`).

## Uso

### Interface web

```bash
python frontend.py
# http://127.0.0.1:8502
```

1. Escolha a quantidade de chips e clique em **Autenticar chips** — escaneie os QR Codes exibidos (WhatsApp → Aparelhos conectados). Se uma conta falhar, ela é **reautenticada automaticamente** (até `AUTH_MAX_RETRIES` tentativas extras, com um QR novo a cada tentativa); sobrou falha, use o botão **🔁 Reautenticar contas com falha**. Sessões ficam salvas em `.wwebjs_auth/`; nas próximas execuções não pede QR.
2. Ajuste a mensagem base (`NOME_DO_CLIENTE` é substituído pelo primeiro nome), envie o arquivo de contatos (ou deixe vazio para carregar do banco). Em **Credor/Campanha**, clique em **🗄️ Carregar Credores/Campanhas do banco** para preencher os dropdowns via SQL (`QUERY_CREDOR_CAMPANHA`); selecionar um credor filtra as campanhas dele. Digitação manual continua funcionando (dropdowns aceitam valor livre). A interface é organizada em seções recolhíveis (Autenticação, Mensagem/Contatos, Credor/Campanha, QRs, Contas, Logs).
3. Clique em **2) Disparar** e acompanhe QRs, tabela de contas, progresso e logs em tempo real.
4. Ao final, o RO roda automaticamente (a menos que "Pular RO" esteja marcado). O botão **Processar RO agora** reprocessa pendências a qualquer momento.

Controles de interrupção:

- **⏹️ Parar autenticação** — cancela o escaneamento em andamento (mata os bots de auth e limpa os QR Codes pendentes).
- **⏹️ Parar disparo** — encerra os bots de envio imediatamente. Contatos já enviados permanecem marcados em `contacts.json` (não repetem no próximo disparo); o RO automático é pulado — use **Processar RO agora** para registrar o que já foi enviado.
- **🔓 Desautenticar todos os chips** — faz logout de cada conta (`client.logout()`, o aparelho some de "Aparelhos conectados" no WhatsApp) e apaga as sessões locais em `.wwebjs_auth/`. A próxima autenticação começa do zero com QR novo.

### CLI

```bash
python orchestrator.py --chips 2 --csv contatos.xlsx
python orchestrator.py --chips 3 --message "Olá NOME_DO_CLIENTE, temos uma proposta."
python orchestrator.py --test --chips 1              # dataframe de teste embutido
python orchestrator.py --chips 2                     # sem arquivo = carga via banco
python orchestrator.py --chips 2 --csv c.csv --skip-ro
python orchestrator.py --chips 4 --csv c.csv --parallel-auth
```

Na CLI a autenticação é **sequencial** por padrão (um QR por vez no terminal, mais legível). Use `--parallel-auth` para autenticar todas de uma vez.

### Bot Node direto (debug)

```bash
node bot/index.js account_1 auth                 # autentica e sai
node bot/index.js account_1 send contacts.json   # envia os contatos atribuídos e sai
node bot/index.js account_1 logout               # desvincula o aparelho e sai
```

## Fluxo de uma execução

1. **Auth** — um processo Node por conta em modo `auth`; QR em `runtime/qr_<conta>.txt`; ao ficar `ready` a sessão é persistida e o processo encerra.
2. **Preparação** — `contacts.json` anterior vira `contacts.json.prev`; contatos são carregados, normalizados, deduplicados, recebem a mensagem renderizada e o `sentBy`.
3. **Envio** — bots em modo `send` (start escalonado em 3s para não estourar RAM com vários Chromes). Cada envio/erro/ACK vira uma linha em `runtime/updates_<conta>.jsonl`; o orquestrador mescla no `contacts.json` a cada 5s.
4. **RO** — contatos com `sent=true` e `roRegistered=false` são montados em lotes (`RO_BATCH_SIZE`) e enviados ao endpoint Calltech; sucesso/erro fica gravado por contato para retry.
5. **Log** — snapshot completo em `logs/run_<timestamp>.json`.

## Estrutura do `contacts.json`

```json
{
  "phone": "+5531999999999",
  "name": "Maria Silva",
  "message": "Bom dia Maria, ...",
  "buttonUrl": "https://wa.me/...",
  "sent": true,
  "sentBy": "account_1",
  "sentAt": "2026-07-10T12:34:56.000Z",
  "delivered": true,
  "deliveredAt": "2026-07-10T12:35:01.000Z",
  "ackLevel": 2,
  "error": null,
  "pessoaId": "12345",
  "email": "maria@email.com",
  "observacao": "Cliente prioritário",
  "roRegistered": true,
  "roRegisteredAt": "2026-07-10T15:40:00+00:00",
  "roBatchId": "RO-20260710-124000-001",
  "roStatus": "success",
  "roError": null
}
```

`ackLevel`: 1 = recebido pelo servidor, 2 = entregue, 3 = lido, 4 = reproduzido.

## Problemas comuns

**QR não aparece na interface** — aguarde ~15–30s (o Chrome headless demora a subir). Veja os logs da conta na própria interface; se o estado for `error`, apague `.wwebjs_auth/session-<conta>` e autentique de novo.

**"Number is not registered on WhatsApp"** — o número não tem conta; o contato é marcado como falha e reportado (se `ERROR_REPORT_URL` estiver configurada). Para pular a validação: `VALIDATE_NUMBERS=False`.

**Envio parado / watchdog** — se nada acontecer por `SEND_INACTIVITY_TIMEOUT_MS` (5 min), o bot encerra com erro. Reexecute o disparo: contatos já enviados não repetem (o filtro é `sent=false` e sem `error`).

**Chrome não encontrado** — rode `npx puppeteer browsers install chrome`, ou instale o Google Chrome no sistema.

**RO não registra** — só entram contatos com envio ok, `roRegistered=false` **e** `pessoaId` preenchido. Contato sem `pessoaId` gera erro individual e fica de fora do lote.

**Falha na carga via banco** — confira `SERVER/DATABASE/DBUSERNAME/PASSWORD` no `.env`, o driver ODBC instalado (`ODBC_DRIVER`) e a `QUERY_CLIENTS_PHONE` (pode ser sobrescrita no `.env`). Alternativa: use CSV/XLSX ou `--test`.

**Conta banida/desconectada no meio do envio** — o bot encerra com `disconnected`; os contatos restantes daquela conta continuam `sent=false`. Redistribua rodando a preparação de novo com menos chips ou reatribua manualmente o `sentBy` no `contacts.json`.

## Migração da v1

- As sessões de `.wwebjs_auth/` da v1 são compatíveis (mesmos `clientId`s `account_N`) — copie a pasta para não escanear de novo.
- `contacts.json` mantém o mesmo schema (com campos novos `name` e `error`), então o `contacts.json.prev` da v1 funciona para dedup.
- O modo standalone de autoatendimento/captura de lead (CPF/CNPJ + e-mail) da v1 **não** foi portado — o bot v2 é somente disparo. Se precisar, ele pode ser adicionado como um terceiro modo (`reply`) no `bot/index.js`.
