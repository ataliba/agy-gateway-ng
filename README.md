# agy-gateway-ng

Gateway HTTP **OpenAI-compatible** (`/v1/chat/completions`, `/v1/models`) que expõe
o [Antigravity CLI](https://github.com/) (`agy`) como se fosse um backend de LLM
qualquer — dá pra apontar qualquer client/SDK que fale API da OpenAI (curl, código,
extensões de editor, etc.) direto pro `agy` rodando localmente.

Projeto irmão: [agy-gateway](https://github.com/antoniocarlos97ss/agy-gateway) —
mesma ideia, só que como bridge de Telegram em vez de API HTTP.

---

## Como funciona

Cada request HTTP dispara um processo `agy --print` (modo sem TUI), captura a saída
e devolve no formato `chat.completion` da OpenAI. Por trás disso:

- **Execuções serializadas** — o `agy` trava um banco local por processo; o gateway
  usa um semáforo (`AGY_MAX_CONCURRENT`, default 1) pra nunca rodar dois ao mesmo
  tempo.
- **Conversa isolada por cliente** — manda o campo `user` (padrão OpenAI) e o
  gateway mantém sua conversa separada das dos outros clientes.
- **Streaming de verdade** — `stream: true` devolve Server-Sent Events incrementais,
  não só o texto todo de uma vez.
- **Aprovação de comandos** — se o `agy` pedir permissão pra rodar algo no meio do
  caminho, o gateway não trava: devolve um `approval_id` e o cliente aprova/nega via
  outro endpoint.
- **Auth opcional** — `AGY_API_KEY` liga checagem de `Authorization: Bearer`.

Detalhes de arquitetura e invariantes internas estão em [`CLAUDE.md`](CLAUDE.md).

---

## Instalação

```bash
git clone git@github.com:ataliba/agy-gateway-ng.git
cd agy-gateway-ng
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Pré-requisito: `agy` (Antigravity CLI) instalado e autenticado na máquina
(`agy` rodado ao menos uma vez pra validar login).

## Configuração (`.env`)

| Var | Default | Uso |
|---|---|---|
| `AGY_BIN` | `agy` | binário/caminho do CLI |
| `AGY_CONTINUE` | `true` | continua a última conversa quando não há `user` no request |
| `MODELS_FILE` | `models.yaml` | registro de aliases de modelo |
| `AGY_TIMEOUT` | `300` | timeout de processo travado e de espera por aprovação |
| `BRAIN_DIR` | `~/.gemini/antigravity-cli/brain` | pasta onde o agy guarda conversas |
| `AGY_MAX_CONCURRENT` | `1` | quantos `agy` podem rodar ao mesmo tempo |
| `AGY_API_KEY` | (vazio) | se setado, exige Bearer token em `/v1/*` |

Modelos expostos ficam em `models.yaml`, mapeando um alias (`agy-claude-sonnet-4-6`)
pro nome real que o `--model` do `agy` espera.

## Rodando

```bash
.venv/bin/uvicorn main:app --reload --port 8000
```

Ou em produção via systemd (unit já pronta em `agy-gateway.service`):

```bash
mkdir -p ~/.config/systemd/user
cp agy-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agy-gateway
```

### Docker

A imagem já vem com o Antigravity CLI instalado (instalador oficial, dentro do
`Dockerfile`). Login e conversas ficam num volume próprio (`gemini_data`),
isolado do host — sobrevive a restart/rebuild, só some com
`docker compose down -v`.

```bash
cp .env.example .env
docker compose up -d --build
```

Na primeira vez, entre no container e faça o login do Google (o `agy` mostra uma
URL pra abrir num navegador):

```bash
docker compose exec agy-gateway agy
```

Depois disso o gateway já usa essa sessão autenticada normalmente — não precisa
logar de novo a não ser que o volume `gemini_data` seja apagado.

`BRAIN_DIR` do `.env` é sobrescrito pelo compose pro caminho de dentro do
container (`/root/.gemini/antigravity-cli/brain`) — não precisa mexer nessa
variável pra rodar via Docker.

### LXC no Proxmox VE

Instalador standalone (não depende do framework community-scripts) que cria um
LXC Debian 13 e instala tudo nativo (venv + systemd, sem Docker, em
`/opt/api-gateway-ng`). Roda no shell do **host** Proxmox:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ataliba/agy-gateway-ng/main/ct/agy-gateway-ng.sh)"
```

Customiza via env var antes do comando (`CTID`, `HOSTNAME`, `STORAGE`, `DISK_GB`,
`CORES`, `MEMORY_MB`, `NET_CONFIG`, `GATEWAY_PORT` — ver cabeçalho de
`ct/agy-gateway-ng.sh`). No fim, falta só logar o `agy` dentro do container:

```bash
pct exec <CTID> -- agy
```

## Testando

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

---

## Uso

### Listar modelos

```bash
curl http://127.0.0.1:8000/v1/models
```

### Chat simples

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agy-claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "oi"}],
    "user": "ataliba"
  }'
```

`user` é o que faz o gateway lembrar da sua conversa entre chamadas — sem ele, cada
request sem contexto cai no `--continue` (segue a última conversa global do agy).

### Streaming

```bash
curl -N -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agy-claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "oi"}],
    "stream": true,
    "user": "ataliba"
  }'
```

### Quando o agy pede aprovação

Uma resposta pode vir com `HTTP 202`:

```json
{
  "status": "permission_required",
  "approval_id": "45e08c8581d34915abf6151021c35de4",
  "command": "Run this command? [y/N]"
}
```

Aprovar ou negar:

```bash
curl -X POST http://127.0.0.1:8000/v1/approvals/45e08c8581d34915abf6151021c35de4 \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'
```

Em modo streaming, o mesmo aviso chega como um chunk SSE extra
(`"permission_required": {...}`) e o stream original continua depois que a
aprovação chegar por essa mesma rota.

### Com `AGY_API_KEY` configurado

```bash
curl http://127.0.0.1:8000/v1/models -H "Authorization: Bearer <sua-chave>"
```
