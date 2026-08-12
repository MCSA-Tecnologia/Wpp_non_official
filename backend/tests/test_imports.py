from __future__ import annotations

import io
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from app.models import Contact, ImportRow, MessageVariant, Role, User
from app.schemas import CampaignCreate
from app.security import hash_password
from app.services.campaigns import create_campaign, materialize_job_message
from app.services.imports import normalize_phone, persist_import
from app.services.query_export import EXPECTED_COLUMNS


def test_phone_normalization():
    assert normalize_phone("(31) 99999-9999") == "+5531999999999"
    assert normalize_phone("+55 31 99999-9999") == "+5531999999999"
    assert normalize_phone("99999-9999") is None


def test_import_persists_valid_invalid_and_duplicate_rows(db):
    user = User(
        id=uuid.uuid4(),
        email="operator@example.com",
        password_hash=hash_password("very-secure-password"),
        role=Role.operator,
    )
    db.add(user)
    db.commit()
    content = (
        "Nome,Telefone,pessoaId,Credor,Campanha\n"
        "Ana,31999999999,1,Banco A,000033 - Prime\n"
        "Ana duplicada,31999999999,2,Banco A,000033 - Prime\n"
        "Inválido,123,3,Banco B,000074 - B\n"
        "Sem credor,31988888888,4,,000074 - B\n"
    ).encode()
    batch = persist_import(db, filename="contatos.csv", content=content, actor=user)
    assert batch.total_rows == 4
    assert batch.valid_rows == 1
    assert batch.duplicate_rows == 1
    assert batch.invalid_rows == 2


def test_csv_and_xlsx_name_column_reach_final_personalized_message(db, configure_message_card):
    user = User(
        id=uuid.uuid4(),
        email="personalization@example.com",
        password_hash="x",
        role=Role.operator,
    )
    db.add(user)
    db.commit()
    card = configure_message_card(user)
    columns = ["pessoaId", "Nome", "email", "Telefone", "observacao", "Credor", "Campanha"]
    row = ["19576", "MARIA APARECIDA DA SILVA", "", "3196429749", "", "Banco A", "Teste"]
    csv_content = (",".join(columns) + "\n" + ",".join(row) + "\n").encode()
    xlsx_output = io.BytesIO()
    pd.DataFrame([row], columns=columns).to_excel(xlsx_output, index=False)

    for filename, content in (
        ("contatos.csv", csv_content),
        ("contatos.xlsx", xlsx_output.getvalue()),
    ):
        batch = persist_import(db, filename=filename, content=content, actor=user)
        campaign = create_campaign(
            db,
            CampaignCreate(
                name=filename,
                import_id=batch.id,
                message="Olá NOME_DO_CLIENTE, proposta do CREDOR.",
                card_revision=card["revision"],
                confirmed_real_send=True,
                interval_mean_minutes=1,
            ),
            user,
            35,
        )
        contact = db.scalar(select(Contact).where(Contact.campaign_id == campaign.id))
        import_row = db.scalar(select(ImportRow).where(ImportRow.batch_id == batch.id))

        assert import_row.raw_data["Nome"] == "MARIA APARECIDA DA SILVA"
        assert contact.name == "MARIA APARECIDA DA SILVA"
        message_variant = db.scalar(
            select(MessageVariant).where(MessageVariant.campaign_id == campaign.id)
        )
        assert materialize_job_message(contact, message_variant) == (
            "Olá Maria Silva, proposta do Banco A."
        )


def test_system_templates_put_name_immediately_after_person_id():
    assert EXPECTED_COLUMNS == [
        "pessoaId",
        "Nome",
        "email",
        "Telefone",
        "observacao",
        "Credor",
        "Campanha",
    ]
    sample = Path(__file__).parents[2] / "samples" / "modelo_contatos.csv"
    assert sample.read_text(encoding="utf-8").splitlines()[0].split(",") == EXPECTED_COLUMNS
