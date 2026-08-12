# AutoWpp

Plataforma distribuída para campanhas reais de WhatsApp com FastAPI, React, PostgreSQL, Redis e workers Baileys.

Não existe conector simulado no runtime. Chips só ficam disponíveis após autenticação real por QR, sessão persistida, número identificado e heartbeat válido.

> **Atenção:** Baileys é uma integração não oficial com o WhatsApp. Utilize apenas com contatos legítimos, cadência responsável e números preparados para esse tipo de operação.

## Inicialização

Pré-requisitos: Docker Desktop ou Docker Engine com Compose v2.

```powershell
Copy-Item .env.scaled.example .env.scaled
```

Preencha todos os segredos de `.env.scaled` e execute:

```powershell
docker compose --env-file .env.scaled up -d --build
```

Painel: `http://localhost:8080`

No primeiro acesso:

1. Troque a senha administrativa inicial.
2. Configure teto diário, janela e fuso.
3. Configure o card global com imagem JPG/PNG, texto e URL HTTPS.
4. Crie os 30 cadastros lógicos de chips.
5. Ative e autentique somente um chip por vez.
6. Execute um canário com um número controlado antes de ampliar a operação.

## Estrutura

```text
backend/              API, modelos, migrações e testes Python
web/                  painel React/Vite servido por Nginx
worker/               conexão Baileys e processamento dos jobs
samples/              exemplo de planilha de contatos
compose.yaml          ambiente local completo
compose.node.yaml     implantação distribuída por nó
DEPLOYMENT.md         operação, produção, backup e recuperação
```

As sessões Baileys ficam criptografadas no PostgreSQL. Não são usados diretórios locais de sessão nem arquivos JSON como fila operacional.

## Campanhas

CSV, XLS ou XLSX podem conter:

- `Telefone`, `pessoaId`, `Credor` e `Campanha`: obrigatórios.
- `Nome`, `email` e `observacao`: opcionais.

O marcador `NOME_DO_CLIENTE` usa o primeiro e o último nomes. A palavra exata `CREDOR` usa o valor da planilha.

Antes do envio, a interface mostra contatos válidos únicos, duplicados, inválidos, chips autenticados, teto por chip, previsão, mensagem e card. A campanha só é criada após confirmação explícita.

## Desenvolvimento e validação

```powershell
uv sync --directory backend --extra test
uv run --directory backend pytest -q

npm.cmd --prefix web ci
npm.cmd --prefix web run build

npm.cmd --prefix worker ci
npm.cmd --prefix worker test

docker compose --env-file .env.scaled config --quiet
```

Artefatos locais como `.venv`, `node_modules`, `dist` e caches não pertencem ao repositório e podem ser recriados pelos comandos acima.

## Operação

```powershell
docker compose --env-file .env.scaled ps
docker compose --env-file .env.scaled logs -f api worker-node-1 worker-node-2
docker compose --env-file .env.scaled down
```

Não execute `docker compose down -v` se quiser preservar o banco, sessões e auditoria. Consulte [DEPLOYMENT.md](DEPLOYMENT.md) antes de atualizar produção ou operar múltiplos nós.
