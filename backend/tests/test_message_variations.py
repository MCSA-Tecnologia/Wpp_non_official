from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models import AppSetting, AuditLog, Role, User
from app.schemas import MessageGenerationSettingsUpdate, MessageVariationGenerateRequest
from app.services import message_variations


def actor(db) -> User:
    user = User(
        id=uuid.uuid4(),
        email="variations@example.com",
        password_hash="hash",
        role=Role.admin,
    )
    db.add(user)
    db.commit()
    return user


def environment(api_key: str = ""):
    return SimpleNamespace(openai_api_key=api_key, openai_timeout_seconds=10)


def openai_response(original: str, variations: list[str]) -> httpx.Response:
    result = {"original": original}
    result.update({f"v{index}": value for index, value in enumerate(variations, start=1)})
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(result)}],
                }
            ],
        },
    )


def test_api_key_is_encrypted_preserved_and_never_returned(db, monkeypatch):
    monkeypatch.setattr(message_variations, "get_settings", lambda: environment())
    monkeypatch.setattr(message_variations, "encrypt_json", lambda value: b"encrypted-key")
    monkeypatch.setattr(message_variations, "decrypt_json", lambda value: "sk-secret")
    user = actor(db)

    result = message_variations.save_message_generation_settings(
        db,
        MessageGenerationSettingsUpdate(api_key="sk-secret"),
        user,
    )

    assert result == {"api_key_configured": True, "model": "gpt-5.6-luna"}
    stored = db.get(AppSetting, message_variations.MESSAGE_GENERATION_KEY).value
    assert stored == {"api_key_ciphertext": "encrypted-key"}
    assert "sk-secret" not in str(result) + str(stored)
    assert message_variations.message_generation_api_key(db) == "sk-secret"

    message_variations.save_message_generation_settings(
        db,
        MessageGenerationSettingsUpdate(api_key=""),
        user,
    )
    assert db.get(AppSetting, message_variations.MESSAGE_GENERATION_KEY).value == stored


def test_environment_api_key_is_supported_without_database_setting(db, monkeypatch):
    monkeypatch.setattr(
        message_variations,
        "get_settings",
        lambda: environment("sk-environment"),
    )

    assert message_variations.message_generation_settings(db)["api_key_configured"] is True
    assert message_variations.message_generation_api_key(db) == "sk-environment"


def test_generation_makes_one_structured_luna_request_and_audits(db, monkeypatch):
    original = "Olá NOME_DO_CLIENTE, fale com CREDOR."
    generated = [
        "Oi NOME_DO_CLIENTE, converse com CREDOR.",
        "Olá NOME_DO_CLIENTE, temos novidades de CREDOR.",
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return openai_response(original, generated)

    monkeypatch.setattr(
        message_variations,
        "get_settings",
        lambda: environment("sk-test"),
    )
    async def generate():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await message_variations.generate_message_variations(
                db,
                MessageVariationGenerateRequest(original=original, count=2),
                actor(db),
                client=client,
            )

    result = asyncio.run(generate())

    assert result == {"original": original, "variations": generated}
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.6-luna"
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}
    assert set(body["text"]["format"]["schema"]["required"]) == {
        "original",
        "v1",
        "v2",
    }
    assert "sempre em português" in body["input"][0]["content"]
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "message_variations.generated")
    )
    assert audit.details == {"model": "gpt-5.6-luna", "count": 2}


@pytest.mark.parametrize(
    "generated",
    [
        ["Olá cliente, fale com CREDOR."],
        ["Olá NOME_DO_CLIENTE, fale com outro banco."],
        ["Olá NOME_DO_CLIENTE, fale com CREDOR."],
    ],
)
def test_generation_rejects_changed_placeholders_and_duplicates(db, monkeypatch, generated):
    original = "Olá NOME_DO_CLIENTE, fale com CREDOR."
    monkeypatch.setattr(
        message_variations,
        "get_settings",
        lambda: environment("sk-test"),
    )
    transport = httpx.MockTransport(lambda request: openai_response(original, generated))
    async def generate():
        async with httpx.AsyncClient(transport=transport) as client:
            return await message_variations.generate_message_variations(
                db,
                MessageVariationGenerateRequest(original=original, count=1),
                actor(db),
                client=client,
            )

    with pytest.raises(message_variations.MessageGenerationUpstreamError):
        asyncio.run(generate())


def test_generation_reports_missing_key_and_timeout(db, monkeypatch):
    user = actor(db)
    monkeypatch.setattr(message_variations, "get_settings", lambda: environment())
    with pytest.raises(message_variations.MessageGenerationNotConfigured):
        asyncio.run(
            message_variations.generate_message_variations(
                db,
                MessageVariationGenerateRequest(original="Olá", count=1),
                user,
            )
        )

    monkeypatch.setattr(
        message_variations,
        "get_settings",
        lambda: environment("sk-test"),
    )

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async def generate_timeout():
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            return await message_variations.generate_message_variations(
                db,
                MessageVariationGenerateRequest(original="Olá", count=1),
                user,
                client=client,
            )

    with pytest.raises(message_variations.MessageGenerationTimeout):
        asyncio.run(generate_timeout())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": {"message": "upstream"}}),
        httpx.Response(401, json={"error": {"message": "invalid key"}}),
        httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "no"}],
                    }
                ],
            },
        ),
        httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "not-json"}],
                    }
                ],
            },
        ),
        httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"original": "Olá", "v1": "Oi", "v2": "Extra"}
                                ),
                            }
                        ],
                    }
                ],
            },
        ),
    ],
)
def test_generation_rejects_http_refusal_invalid_json_and_wrong_count(
    db, monkeypatch, response
):
    monkeypatch.setattr(
        message_variations,
        "get_settings",
        lambda: environment("sk-test"),
    )
    user = actor(db)

    async def generate():
        transport = httpx.MockTransport(lambda request: response)
        async with httpx.AsyncClient(transport=transport) as client:
            return await message_variations.generate_message_variations(
                db,
                MessageVariationGenerateRequest(original="Olá", count=1),
                user,
                client=client,
            )

    with pytest.raises(message_variations.MessageGenerationUpstreamError):
        asyncio.run(generate())
