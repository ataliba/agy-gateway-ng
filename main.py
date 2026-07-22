import re
import time
import uuid
import json
import asyncio
import os
import yaml
from dataclasses import dataclass, field
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

AGY_BIN = os.getenv("AGY_BIN", "agy")
CONTINUE_CONVERSATION = os.getenv("AGY_CONTINUE", "true").lower() == "true"
MODELS_FILE = os.getenv("MODELS_FILE", "models.yaml")
PRINT_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "300"))
BRAIN_DIR = os.getenv(
    "BRAIN_DIR", os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain")
)
AGY_MAX_CONCURRENT = int(os.getenv("AGY_MAX_CONCURRENT", "1"))
API_KEY = os.getenv("AGY_API_KEY", "").strip()

ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

# agy trava o banco local (brain dir) por processo — serializa execuções pra evitar
# corrida entre requests concorrentes disputando a mesma trava.
_agy_semaphore = asyncio.Semaphore(AGY_MAX_CONCURRENT)

# mapeia identificador de usuário (campo "user" do request) -> conversation_id do agy,
# assim cada cliente segue a própria conversa em vez de todos brigarem pela última (--continue).
_user_conversations: dict[str, str] = {}

# heurística pra detectar quando o agy tá parado esperando aprovação y/n no stdin —
# mesmo padrão usado pelo bridge Telegram irmão deste projeto (agy-gateway/src/agy-wrapper.js)
_PERMISSION_MARK = re.compile(r'\[y/N\]|\(y/n\)|\[Y/n\]|\[Allow/Deny\]', re.IGNORECASE)
_PERMISSION_KEYWORDS = re.compile(r'run|allow|execute|permission|approve', re.IGNORECASE)


def _detect_permission_prompt(tail: str) -> str | None:
    for line in tail.splitlines():
        if _PERMISSION_MARK.search(line) and _PERMISSION_KEYWORDS.search(line):
            return line.strip()
    return None


@dataclass
class PendingApproval:
    """Um processo agy pausado esperando resposta y/n de /v1/approvals/{id}."""
    proc: asyncio.subprocess.Process
    command: str
    conversation_id: str | None
    known_ids: set[str]
    output_so_far: str
    mode: str  # "sync" (via /v1/chat/completions normal) ou "stream" (via SSE)
    user: str | None = None
    model_name: str | None = None
    decision_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: bool | None = None


# approval_id -> PendingApproval, enquanto aguarda resposta de /v1/approvals/{id}
_pending_approvals: dict[str, PendingApproval] = {}


def _register_pending(
    proc, command, conversation_id, known_ids, output_so_far, mode, user=None, model_name=None
) -> str:
    approval_id = uuid.uuid4().hex
    pending = PendingApproval(proc, command, conversation_id, known_ids, output_so_far, mode, user, model_name)
    _pending_approvals[approval_id] = pending
    if mode == "sync":
        # no modo stream quem cuida do timeout é o próprio generator (segue esperando no
        # mesmo request); no modo sync o request original já retornou, então precisa
        # de alguém pra matar o processo órfão se ninguém responder a aprovação.
        asyncio.create_task(_expire_pending(approval_id))
    return approval_id


async def _expire_pending(approval_id: str) -> None:
    await asyncio.sleep(PRINT_TIMEOUT)
    pending = _pending_approvals.pop(approval_id, None)
    if pending is None or pending.decision_event.is_set():
        return
    pending.proc.kill()
    await pending.proc.wait()
    _agy_semaphore.release()


def _strip_ansi(text: str) -> str:
    return ANSI.sub('', text)


async def _require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "Bearer token inválido ou ausente")


def _list_conversation_ids() -> set[str]:
    """IDs das pastas de conversa no brain dir do agy (uma pasta = uma conversa)."""
    if not os.path.isdir(BRAIN_DIR):
        return set()
    return {
        name for name in os.listdir(BRAIN_DIR)
        if os.path.isdir(os.path.join(BRAIN_DIR, name))
    }


def _load_registry() -> dict:
    with open(MODELS_FILE) as f:
        return yaml.safe_load(f) or {}


MODEL_REGISTRY = _load_registry()

__version__ = "0.5.0"

app = FastAPI(title="agy-gateway", version=__version__)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool | None = False
    user: str | None = None  # identificador do cliente, usado pra isolar conversation_id


def _build_args(prompt: str, real_model: str, conversation_id: str | None = None) -> list[str]:
    args = [AGY_BIN, "--model", real_model]
    if conversation_id:
        args += ["--conversation", conversation_id]
    elif CONTINUE_CONVERSATION:
        args.append("--continue")
    args += ["-p", prompt]
    assert "-p" in args, "flag -p é obrigatória — sem ela o agy abre REPL interativo e trava o subprocess"
    return args


async def _iter_agy_stdout(proc: asyncio.subprocess.Process):
    """Lê stdout aos pedaços. Levanta asyncio.TimeoutError se agy ficar mudo por
    tempo demais (trava real, não pausa esperando aprovação — essa é detectada
    pelo texto do próprio pedaço lido antes de qualquer bloqueio)."""
    while True:
        chunk = await asyncio.wait_for(proc.stdout.read(1024), timeout=PRINT_TIMEOUT)
        if not chunk:
            return
        yield _strip_ansi(chunk.decode(errors="ignore"))


async def _drain_until_prompt_or_exit(proc: asyncio.subprocess.Process, output_so_far: str) -> tuple[str, str | None]:
    """Acumula stdout até o processo sair ou aparecer um novo pedido de permissão.
    Retorna (saída_acumulada, comando_do_pedido_ou_None)."""
    output = output_so_far
    tail = ""
    async for text in _iter_agy_stdout(proc):
        output += text
        tail = (tail + text)[-500:]
        cmd = _detect_permission_prompt(tail)
        if cmd:
            return output, cmd
    return output, None


async def _finalize_sync(proc: asyncio.subprocess.Process, output: str, conversation_id: str | None, known_ids: set[str]) -> dict:
    stderr = (await proc.stderr.read()).decode()
    await proc.wait()
    _agy_semaphore.release()

    if proc.returncode != 0:
        raise HTTPException(500, _strip_ansi(stderr).strip() or "agy falhou sem stderr")

    new_conversation_id = None
    if conversation_id is None:
        new_ids = _list_conversation_ids() - known_ids
        if new_ids:
            new_conversation_id = next(iter(new_ids))

    return {"status": "ok", "output": output.strip(), "new_conversation_id": new_conversation_id}


async def _run_agy(
    prompt: str, real_model: str, conversation_id: str | None = None, user: str | None = None, model_name: str | None = None
) -> dict:
    """Roda agy uma vez. Retorna:
      {"status": "ok", "output": str, "new_conversation_id": str|None}
      {"status": "permission_required", "approval_id": str, "command": str}
    Nesse segundo caso o semáforo continua preso — só é liberado quando
    /v1/approvals/{id} resolver ou o pedido expirar (AGY_TIMEOUT)."""
    args = _build_args(prompt, real_model, conversation_id)
    known_ids = _list_conversation_ids() if conversation_id is None else set()

    await _agy_semaphore.acquire()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        output, cmd = await _drain_until_prompt_or_exit(proc, "")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        _agy_semaphore.release()
        raise HTTPException(504, f"agy excedeu timeout de {PRINT_TIMEOUT}s (comando: {' '.join(args)})")

    if cmd:
        approval_id = _register_pending(proc, cmd, conversation_id, known_ids, output, "sync", user, model_name)
        return {"status": "permission_required", "approval_id": approval_id, "command": cmd}

    return await _finalize_sync(proc, output, conversation_id, known_ids)


async def _resume_sync(pending: PendingApproval, approved: bool) -> dict:
    """Continua um processo agy sync depois que /v1/approvals/{id} decidiu."""
    proc = pending.proc
    proc.stdin.write(b"y\n" if approved else b"n\n")
    await proc.stdin.drain()

    try:
        output, cmd = await _drain_until_prompt_or_exit(proc, pending.output_so_far)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        _agy_semaphore.release()
        raise HTTPException(504, f"agy excedeu timeout de {PRINT_TIMEOUT}s")

    if cmd:
        approval_id = _register_pending(
            proc, cmd, pending.conversation_id, pending.known_ids, output, "sync", pending.user, pending.model_name
        )
        return {"status": "permission_required", "approval_id": approval_id, "command": cmd}

    return await _finalize_sync(proc, output, pending.conversation_id, pending.known_ids)


@app.get("/v1/models", dependencies=[Depends(_require_api_key)])
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "agy-gateway"}
            for name in MODEL_REGISTRY
        ],
    }


async def _stream_chat_completion(req: ChatRequest, real_model: str, prompt: str, conversation_id: str | None):
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def _sse(delta: dict, finish_reason: str | None = None, extra: dict | None = None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": req.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if extra:
            payload.update(extra)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield _sse({"role": "assistant"})

    args = _build_args(prompt, real_model, conversation_id)
    known_ids = _list_conversation_ids() if conversation_id is None else set()

    await _agy_semaphore.acquire()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    output_so_far = ""
    tail = ""
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(1024), timeout=PRINT_TIMEOUT)
            if not chunk:
                break
            text = _strip_ansi(chunk.decode(errors="ignore"))
            output_so_far += text
            tail = (tail + text)[-500:]
            cmd = _detect_permission_prompt(tail)

            if cmd:
                approval_id = _register_pending(
                    proc, cmd, conversation_id, known_ids, output_so_far, "stream", req.user, req.model
                )
                pending = _pending_approvals[approval_id]
                yield _sse(
                    {"content": None},
                    extra={"permission_required": {"approval_id": approval_id, "command": cmd}},
                )
                try:
                    await asyncio.wait_for(pending.decision_event.wait(), timeout=PRINT_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    _agy_semaphore.release()
                    _pending_approvals.pop(approval_id, None)
                    yield _sse({"content": "\n[erro] tempo esgotado esperando aprovação"}, finish_reason="stop")
                    yield "data: [DONE]\n\n"
                    return

                _pending_approvals.pop(approval_id, None)
                proc.stdin.write(b"y\n" if pending.decision else b"n\n")
                await proc.stdin.drain()
                tail = ""  # evita redetectar o mesmo prompt já respondido
                continue

            if text:
                yield _sse({"content": text})
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        _agy_semaphore.release()
        yield _sse({"content": f"\n[erro] agy excedeu timeout de {PRINT_TIMEOUT}s"}, finish_reason="stop")
        yield "data: [DONE]\n\n"
        return

    stderr = (await proc.stderr.read()).decode()
    await proc.wait()
    _agy_semaphore.release()

    if proc.returncode != 0:
        err = _strip_ansi(stderr).strip() or "agy falhou sem stderr"
        yield _sse({"content": f"\n[erro] {err}"}, finish_reason="stop")
        yield "data: [DONE]\n\n"
        return

    if conversation_id is None and req.user:
        new_ids = _list_conversation_ids() - known_ids
        if new_ids:
            _user_conversations[req.user] = next(iter(new_ids))

    yield _sse({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions", dependencies=[Depends(_require_api_key)])
async def chat_completions(req: ChatRequest):
    config = MODEL_REGISTRY.get(req.model)
    if not config:
        raise HTTPException(
            400,
            f"Modelo '{req.model}' não configurado no gateway. "
            f"Disponíveis: {list(MODEL_REGISTRY.keys())}",
        )

    if not req.messages:
        raise HTTPException(400, "messages não pode estar vazio")

    prompt = req.messages[-1].content
    conversation_id = _user_conversations.get(req.user) if req.user else None

    if req.stream:
        return StreamingResponse(
            _stream_chat_completion(req, config["model"], prompt, conversation_id),
            media_type="text/event-stream",
        )

    result = await _run_agy(
        prompt, real_model=config["model"], conversation_id=conversation_id, user=req.user, model_name=req.model
    )
    return _chat_response_or_pending(req.model, req.user, result)


class ApprovalRequest(BaseModel):
    approved: bool


def _chat_response_or_pending(model: str, user: str | None, result: dict):
    if result["status"] == "permission_required":
        return JSONResponse(status_code=202, content={
            "status": "permission_required",
            "approval_id": result["approval_id"],
            "command": result["command"],
            "hint": f"POST /v1/approvals/{result['approval_id']} com {{\"approved\": true|false}} pra continuar",
        })

    if user and result["new_conversation_id"]:
        _user_conversations[user] = result["new_conversation_id"]

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["output"]},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/approvals/{approval_id}", dependencies=[Depends(_require_api_key)])
async def approve(approval_id: str, body: ApprovalRequest):
    pending = _pending_approvals.pop(approval_id, None)
    if pending is None:
        raise HTTPException(404, "approval_id não encontrado, já respondido ou expirado")

    pending.decision = body.approved
    pending.decision_event.set()

    if pending.mode == "stream":
        return {"status": "ok", "note": "resposta registrada — acompanhe o stream original de /v1/chat/completions"}

    result = await _resume_sync(pending, body.approved)
    return _chat_response_or_pending(pending.model_name, pending.user, result)


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__, "models_loaded": len(MODEL_REGISTRY)}
