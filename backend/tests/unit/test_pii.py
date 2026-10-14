"""PII screen: blocks identifiers, lets ordinary policy questions and statistics through.
All identifiers below are synthetic (well-known sample formats), not real people's details."""
import pytest

from app.guardrails.pii import rejection_message, screen

BLOCKED = [
    ("My NRIC is S1234567D, am I eligible?", "NRIC/FIN number"),
    ("fin g7654321x", "NRIC/FIN number"),
    ("Email me at tan.ah.kow@example.com", "email address"),
    ("Call 9123 4567 tomorrow", "phone number"),
    ("my number is +65-81234567", "phone number"),
    ("landline 61234567", "phone number"),
    ("I live at Blk 123 Ang Mo Kio Ave 3 #05-123", "street address unit number"),
    ("pay into my bank account 123-45678-9", "bank account number"),
    ("card 4111 1111 1111 1111 was charged", "payment card number"),
]

ALLOWED = [
    "Analyse employment trends in the technology sector from 2020-2024",
    "What did the Minister for Manpower say about foreign workers?",
    "Compare HDB resale prices in Tampines and Bishan since 2017",
    "Total employment rose from 3,700,000 to 3,950,000 persons",
    "COE premium hit $106,000 in 2023; what drove it?",
    "Show SSIC 62011 software jobs and the 2024 Budget measures",
    "How many of the 12345678 records are missing values?",
    "Population of 6000000 by 2030?",
]


@pytest.mark.parametrize("text,kind", BLOCKED)
def test_blocks_identifiers(text, kind):
    assert kind in {f.kind for f in screen(text)}


@pytest.mark.parametrize("text", ALLOWED)
def test_allows_policy_questions_names_and_statistics(text):
    assert screen(text) == []


def test_card_number_needs_valid_luhn_checksum():
    assert screen("reference 4111 1111 1111 1112") == []


def test_rejection_names_kinds_but_never_echoes_values():
    findings = screen("S1234567D and 91234567 and S7654321A")
    message = rejection_message(findings)
    assert "NRIC/FIN number and phone number" in message
    assert "S1234567D" not in message and "91234567" not in message
