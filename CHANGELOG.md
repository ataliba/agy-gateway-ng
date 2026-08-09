# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

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

[0.5.1]: https://github.com/ataliba/agy-gateway-ng/releases/tag/v0.5.1
[0.5.0]: https://github.com/ataliba/agy-gateway-ng/releases/tag/v0.5.0
