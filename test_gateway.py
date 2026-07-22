import pytest
from fastapi import HTTPException

import main
from main import _build_args, _require_api_key, _detect_permission_prompt

def test_print_flag_always_present():
    args = _build_args("oi", "gemini-3.5-flash-low")
    assert "-p" in args

def test_model_flag_uses_real_name():
    args = _build_args("oi", "claude-sonnet-4-6")
    assert "--model" in args
    idx = args.index("--model")
    assert args[idx + 1] == "claude-sonnet-4-6"

def test_conversation_id_uses_conversation_flag_not_continue():
    args = _build_args("oi", "gemini-3.5-flash-low", conversation_id="abc-123")
    idx = args.index("--conversation")
    assert args[idx + 1] == "abc-123"
    assert "--continue" not in args

def test_no_conversation_id_falls_back_to_continue():
    args = _build_args("oi", "gemini-3.5-flash-low")
    assert "--continue" in args
    assert "--conversation" not in args

@pytest.mark.asyncio
async def test_require_api_key_passes_when_unset(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "")
    await _require_api_key(authorization=None)  # não deve levantar

@pytest.mark.asyncio
async def test_require_api_key_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "segredo")
    with pytest.raises(HTTPException) as exc:
        await _require_api_key(authorization=None)
    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_require_api_key_accepts_correct_bearer(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "segredo")
    await _require_api_key(authorization="Bearer segredo")  # não deve levantar

def test_detect_permission_prompt_matches_run_confirmation():
    tail = "Vou rodar:\nrm -rf /tmp/x\nExecute this command? [y/N]"
    cmd = _detect_permission_prompt(tail)
    assert cmd == "Execute this command? [y/N]"

def test_detect_permission_prompt_ignores_unrelated_yn_lines():
    tail = "Deseja continuar mesmo assim? [y/N]"  # tem [y/N] mas nenhuma keyword de ação
    assert _detect_permission_prompt(tail) is None

def test_detect_permission_prompt_ignores_plain_text():
    tail = "Aqui está o resultado do seu pedido, sem pedir nada."
    assert _detect_permission_prompt(tail) is None
