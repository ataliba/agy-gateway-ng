# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

## [0.6.5] - 2026-09-22

### Changed

- `models.yaml`: adicionados `agy-gemini-3.7-flash-*` e `agy-gemini-3.8-flash-*`;
  removidos `agy-gemini-3.5-flash-*` (não aparecem mais em `agy models`).

## [0.6.4] - 2026-09-22

### Fixed

- Semáforo `_agy_semaphore` não era liberado quando o client disconnect
  cancelava a task uma segunda vez durante o `await proc.wait()` da limpeza,
  pulando o `release()` logo em seguida. Com `AGY_MAX_CONCURRENT=1` isso
  travava toda request futura sem nunca spawnar o `agy` (visto em produção:
  requests seguidas sem log de "spawnado"). `release()` agora fica dentro de
  `finally` aninhado nos 5 pontos de cleanup (stream e sync), garantindo
  liberação mesmo sob cancelamento repetido.

## [0.6.3] - 2026-09-16

### Added

- Log de debug detalhado pra rastrear casos de gateway travado/mudo sob uso
  prolongado. `AGY_LOG_LEVEL` (env, default `INFO`) controla o nível — `DEBUG`
  loga espera de semáforo e args de cada spawn. Todo request ganha um `rid`
  (id curto) propagado por toda a cadeia (spawn, drain, timeout, aprovação,
  disconnect), permitindo isolar um request específico no log. Cobre: entrada
  e saída de cada request (model/user/stream/prompt_len/elapsed), spawn do
  processo `agy` (pid/model/conversation_id), espera >1s pelo semáforo,
  returncode/output_len/stderr_len na saída, timeout (mata processo órfão),
  exceção/cancelamento no meio do fluxo, pedido de permissão
  (comando/approval_id), expiração de approval sem resposta, e disconnect do
  cliente no meio de um stream.

## [0.6.2] - 2026-09-12

### Fixed

- Vazamento de semáforo/processo quando cliente derrubava a conexão no meio
  duma request (streaming ou aprovação pendente). O Starlette encerra o
  generator jogando `GeneratorExit`/`CancelledError` no ponto do último
  `yield` — nenhum dos dois é subclasse de `Exception`, então escapava dos
  `except Exception` existentes sem matar o processo `agy` nem liberar
  `_agy_semaphore`. Como `AGY_MAX_CONCURRENT` default é `1`, um único
  disconnect nesse ponto travava toda request futura pra sempre (gateway
  "parava de responder" depois de alguns dias de uso). Trocado por
  `except BaseException` nos pontos certos + `finally` em `_finalize_sync`
  (`main.py`: `_run_agy`, `_resume_sync`, `_finalize_sync`,
  `_stream_chat_completion`). Também corrigido: entrada de
  `_pending_approvals` (modo stream) que ficava órfã pra sempre no mesmo
  cenário.
- `_user_conversations` (mapa `user → conversation_id`) crescia sem limite —
  um cliente que manda `user` diferente a cada request (ex: UUID por sessão)
  vazava memória lentamente ao longo de dias. Virou `OrderedDict` com LRU
  limitado por `AGY_MAX_USERS` (default `1000`).

## [0.6.1] - 2026-08-29

### Fixed

- `agy` podia sair com `returncode 0` e stdout vazio (visto sob uso alto,
  provável rate-limit/quota do modelo engolido silenciosamente) e o gateway
  devolvia `200` com `content` vazio sem log nenhum — cliente só via "model
  returned empty response" sem pista da causa. Agora loga o `stderr` do
  processo e retorna `502` (modo sync) ou chunk de erro (streaming) em vez de
  completion vazio mudo (`main.py`: `_finalize_sync`,
  `_stream_chat_completion`). Projeto ganhou logging (`logger`), antes
  inexistente.

## [0.6.0] - 2026-08-13

### Added

- Variável `AGY_SKIP_PERMISSIONS` (default `false`): quando `true`, passa
  `--dangerously-skip-permissions` pro `agy`, pulando o fluxo de aprovação
  inteiro em vez de depender da heurística de detecção de prompt no stdout.

## [0.5.2] - 2026-08-09

### Fixed

- Workflow `Pylint` (`.github/workflows/pylint.yml`) quebrava em todo push:
  não instalava `requirements.txt`/`requirements-dev.txt`, gerando
  `import-error` em `yaml`/`fastapi`/`pydantic`, e travava no exit-code de
  score baixo. Adicionado `.pylintrc` alinhado às convenções do projeto
  (sem docstrings obrigatórias, sem limite artificial de args/atributos num
  arquivo único de propósito) e `main.py` reformatado — nota 10/10, sem
  mudança de comportamento.

## [0.5.1] - 2026-08-09

### Fixed

- Vazamento de `_agy_semaphore` quando uma exceção não-timeout (pipe quebrado
  no stdin, `agy` sumindo do PATH, etc) ocorria durante o processamento de um
  request — o semáforo só era liberado em `except asyncio.TimeoutError`,
  deixando qualquer outra falha travar todo `POST /v1/chat/completions`
  seguinte até o processo do gateway ser reiniciado (`main.py`: `_run_agy`,
  `_resume_sync`, `_stream_chat_completion`).

## [0.5.0] - 2026-07-22

Primeiro release.

### Added

- Gateway HTTP OpenAI-compatible (`main.py`) expondo o CLI `agy` via
  `/v1/chat/completions` e `/v1/models`, com streaming SSE e fluxo de
  aprovação de permissão (`/v1/approvals/{approval_id}`).
- `Dockerfile` e `docker-compose.yml` instalando o `agy` via instalador
  oficial, com volume nomeado `gemini_data` pra persistir login/conversas.
- Instalador de LXC pro Proxmox VE (`ct/agy-gateway-ng.sh`), seguindo a
  convenção `var_cpu`/`var_ram`/`var_disk`/`var_version` do
  community-scripts.
- README com instruções de instalação, configuração e exemplos de uso.
- `version` exposto em `GET /health` e no schema OpenAPI da app.

### Fixed

- Bugs conhecidos do template Debian 13 em LXC no instalador Proxmox.
- Diretório `/opt/api-gateway-ng` e template Debian 13 no instalador LXC.

[0.5.2]: https://github.com/ataliba/agy-gateway-ng/releases/tag/v0.5.2
[0.5.1]: https://github.com/ataliba/agy-gateway-ng/releases/tag/v0.5.1
[0.5.0]: https://github.com/ataliba/agy-gateway-ng/releases/tag/v0.5.0
