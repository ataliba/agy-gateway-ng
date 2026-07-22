# agy-gateway-ng

Gateway HTTP OpenAI-compatible que expõe o CLI `agy` (Antigravity) como um backend
`/v1/chat/completions` / `/v1/models`. Cada request HTTP dispara um processo `agy`
via subprocess em modo `-p` (print, sem TUI).

Projeto irmão: `/home/ataliba/agy-gateway` — bridge Telegram↔`agy` em Node.js, mesma
ideia (spawn `agy --print` por mensagem), UI diferente (botões Telegram em vez de
HTTP). A heurística de detecção de pedido de permissão aqui foi portada de lá
(`src/agy-wrapper.js`).

## Arquitetura

- `main.py` — todo o gateway: FastAPI app, subprocess management, streaming SSE,
  fluxo de aprovação de permissão. Um único arquivo, sem camadas extras.
- `models.yaml` — registro de modelos expostos (`agy-*` alias → nome real do modelo
  que o CLI `agy` espera em `--model`).
- `.env` / `.env.example` — configuração via variáveis de ambiente (ver tabela abaixo).
- `agy-gateway.service` — unit systemd (`--user`) pra rodar via `uvicorn`.
- `test_gateway.py` + `pytest.ini` (`asyncio_mode = auto`) + `requirements-dev.txt`.
- `Dockerfile` / `docker-compose.yml` — imagem instala o `agy` via instalador
  oficial (`curl -fsSL https://antigravity.google/cli/install.sh | bash`), vai
  pra `/root/.local/bin/agy` (no `PATH`). Login/conversas ficam no volume nomeado
  `gemini_data:/root/.gemini` — isolado do host, sobrevive a restart/rebuild, só
  some com `down -v`. Login inicial: `docker compose exec agy-gateway agy`.

## Invariantes importantes (não quebrar sem entender por quê)

1. **`agy` trava um banco local por processo** (pasta `brain`). Rodar dois `agy`
   simultâneos pode corromper/travar o estado. Por isso todo spawn passa por
   `_agy_semaphore` (`AGY_MAX_CONCURRENT`, default 1) — não remover o semáforo pra
   "ganhar performance" sem entender esse risco.
2. **Isolamento de conversa é por campo `user`** do request (campo padrão OpenAI,
   reaproveitado). Sem `user`, cai no `--continue` legado (continua a última
   conversa global do agy — várias chamadas sem `user` disputam a mesma conversa).
   Com `user`, o gateway mapeia `user → conversation_id` em `_user_conversations`
   (em memória, some ao reiniciar) e passa `--conversation <id>` pro agy.
3. **Detecção de novo `conversation_id`** é feita comparando o conteúdo do
   `BRAIN_DIR` antes/depois do processo rodar (`_list_conversation_ids`), não por
   parsing de stdout. Se o agy mudar onde grava conversas, isso quebra.
4. **Pedido de permissão do agy é detectado por heurística de texto** no stdout
   (`_detect_permission_prompt`: procura `[y/N]`/`(y/n)`/`[Allow/Deny]` + palavra
   tipo run/allow/execute/permission/approve). Não existe um protocolo formal —
   se o agy mudar a wording do prompt, a detecção para de funcionar silenciosamente
   (o processo trava até `AGY_TIMEOUT` e é morto). Ver seção Fluxo de aprovação.
5. **Todo subprocess é criado com `stdin=PIPE`** — necessário pra poder responder
   y/n. Não voltar pra stdin herdado do processo pai.

## Fluxo de aprovação de permissão

Quando o agy pede aprovação no meio da execução, o gateway não trava a request:

- **Não-stream**: responde HTTP `202` com `{"status":"permission_required",
  "approval_id", "command"}`. Cliente decide chamando
  `POST /v1/approvals/{approval_id}` com `{"approved": true|false}` — essa segunda
  chamada retoma o processo e devolve o `chat.completion` final (ou outro `202` se
  o agy pedir uma segunda aprovação em sequência).
- **Streaming**: emite um chunk SSE com campo extra `permission_required` e
  **continua esperando na mesma conexão** (`asyncio.Event` por pedido pendente) até
  alguém chamar o mesmo `POST /v1/approvals/{approval_id}` de outra conexão.
- Pedido pendente sem resposta expira em `AGY_TIMEOUT` segundos
  (`_expire_pending`): mata o processo órfão e libera o semáforo. Isso só roda como
  task de fundo no modo sync — no modo stream quem cuida do timeout é o próprio
  generator (já está com um `await` na mesma corotina).

## Variáveis de ambiente (`.env`)

| Var | Default | Uso |
|---|---|---|
| `AGY_BIN` | `agy` | binário/caminho do CLI |
| `AGY_CONTINUE` | `true` | usa `--continue` quando não há `conversation_id` resolvido |
| `MODELS_FILE` | `models.yaml` | registro de modelos |
| `AGY_TIMEOUT` | `300` | timeout de processo parado E timeout de espera por aprovação |
| `BRAIN_DIR` | `~/.gemini/antigravity-cli/brain` | onde o agy guarda conversas |
| `AGY_MAX_CONCURRENT` | `1` | tamanho do semáforo — manter em 1 a menos que confirme que o agy aguenta concorrência |
| `AGY_API_KEY` | (vazio) | se setado, exige `Authorization: Bearer <valor>` em `/v1/*` (exceto `/health`) |

## Endpoints

- `GET /health` — sem auth, sempre aberto (monitoramento).
- `GET /v1/models` — lista aliases de `models.yaml`.
- `POST /v1/chat/completions` — aceita `stream`, `user`; pode responder `200`
  (completion normal), `202` (permission_required) ou SSE.
- `POST /v1/approvals/{approval_id}` — resolve uma aprovação pendente.

## Rodando / testando

```bash
.venv/bin/uvicorn main:app --reload          # dev
.venv/bin/python -m pytest -q                # testes (fastapi + pytest-asyncio, sem TestClient/httpx)
```

Testes de fluxo completo (streaming, aprovação, timeout) foram validados
manualmente com um `agy` fake de stdout controlado — não existe suite automatizada
de integração porque o `agy` real exige login Google, então CI não consegue rodar
o binário de verdade.

Deploy local via `systemctl --user` usando `agy-gateway.service` (aponta pro
`.venv` do próprio diretório).
