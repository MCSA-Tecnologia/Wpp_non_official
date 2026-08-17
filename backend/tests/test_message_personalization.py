from __future__ import annotations

from app.models import Contact, MessageVariant
from app.services.campaigns import materialize_job_message, render_message


def test_name_uses_first_and_last_terms_in_title_case():
    assert render_message("Olá NOME_DO_CLIENTE", "Maria Aparecida da Silva") == "Olá Maria Silva"
    assert render_message("Olá NOME_DO_CLIENTE", "  MARIA   SILVA  ") == "Olá Maria Silva"
    assert render_message("Olá NOME_DO_CLIENTE", "mARIA") == "Olá Maria"
    assert render_message("Olá NOME_DO_CLIENTE", "jOÃO augusto dA sILVA") == "Olá João Silva"


def test_empty_name_removes_placeholder_and_cleans_horizontal_spacing():
    assert render_message("Olá NOME_DO_CLIENTE, tudo bem?", "") == "Olá, tudo bem?"
    assert render_message("NOME_DO_CLIENTE\n  Próxima linha", "") == "\nPróxima linha"
    assert render_message("Prezado NOME_DO_CLIENTE  ", "   ") == "Prezado"


def test_creditor_is_exact_uppercase_standalone_and_replaces_all_occurrences():
    template = "CREDOR oferece condições. credor e CREDORES não mudam. Fale com CREDOR."
    expected = "Banco Ágil oferece condições. credor e CREDORES não mudam. Fale com Banco Ágil."
    assert render_message(template, "Maria Silva", "Banco Ágil") == expected


def test_inserted_values_are_not_processed_recursively():
    assert (
        render_message(
            "NOME_DO_CLIENTE - CREDOR",
            "CREDOR Silva",
            "NOME_DO_CLIENTE Financeira",
        )
        == "Credor Silva - NOME_DO_CLIENTE Financeira"
    )


def test_underlining_is_rendered_after_contact_personalization():
    expected = "O\u0332l\u0332á\u0332 M\u0332a\u0332r\u0332i\u0332a\u0332 S\u0332i\u0332l\u0332v\u0332a\u0332"
    assert render_message("<u>Olá NOME_DO_CLIENTE</u>", "Maria Silva") == expected
    assert render_message("Olá <u>NOME_DO_CLIENTE</u>, tudo bem?", "") == "Olá, tudo bem?"


def test_materialized_job_message_uses_contact_name_and_creditor():
    contact = Contact(name="Maria Aparecida da Silva", credor="Banco A")
    variant = MessageVariant(body="Olá NOME_DO_CLIENTE, proposta do CREDOR.")

    assert materialize_job_message(contact, variant) == "Olá Maria Silva, proposta do Banco A."
