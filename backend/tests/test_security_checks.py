"""The pure checks shared by the checker and the swarm: people, secrets, private files, masks."""

import base64
import gzip
import json

import pytest
from app.bounty.security_checks import (
    baas_host,
    file_matches,
    file_names,
    find_people,
    find_secret,
    mask,
)


def jwt(payload: dict) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(payload)}.c2lnbmF0dXJlLXBhcnQtb25l"


ROWS = [
    {"id": 1, "email": "maria.lopez@fastmail.example", "phone": "+34 600 111 222"},
    {"id": 2, "email": "jon.berg@mailbox.example", "phone": "+46 70 123 45 67"},
    {"id": 3, "email": "aiko@postbox.example", "phone": "612345678"},
    {"id": 4, "email": "info@shop.com", "phone": None},
]


def test_people_are_counted_without_public_data_and_site_emails():
    found = find_people([json.dumps(ROWS)], "", "shop.com")
    assert found["emails"] == 3 and found["phones"] == 3 and found["people"] == 3
    assert len(found["samples"]) == 3
    assert all("***" in s for s in found["samples"])
    assert "maria.lopez@fastmail.example" not in repr(found)

    public = "<p>Write to Maria.Lopez@fastmail.example or call +34 600 111 222</p>"
    found = find_people([json.dumps(ROWS)], public, "shop.com")
    assert found["emails"] == 2 and found["phones"] == 2 and found["people"] == 2


def test_phones_count_when_there_are_no_emails():
    rows = [{"mobile": f"+1 415 555 01{n:02d}"} for n in range(4)]
    assert find_people([json.dumps(rows)], "", "x.com")["people"] == 4
    # Plain numbers outside phone keys are ids or prices, not people.
    numbers = [{"id": 1234567890 + n, "total": 987654321} for n in range(5)]
    assert find_people([json.dumps(numbers)], "", "x.com")["people"] == 0


def test_people_ignore_file_names_and_join_several_bodies():
    bodies = ["logo@2x.png icon@3x.webp", "a@one.example", '{"email": "b@two.example"}']
    found = find_people(bodies, "", "")
    assert found["emails"] == 2


def test_mask():
    assert mask("bob@gmail.com") == "b***@gmail.com"
    assert mask("+34 600 111 212") == "+34*******12"
    assert mask("sk_live_abcdefghijklmnopqrstuv") == "sk_liv****stuv"
    assert mask("short") == "sh****"


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("supabase_service_role", f'const k = "{jwt({"role": "service_role", "ref": "x"})}";'),
        ("stripe_secret", 'stripe("sk_live_51HxYzAbCdEfGhIjKlMnOpQr");'),
        (
            "aws_secret",
            'id="AKIAIOSFODNN7EXAMPLE" secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
        ),
        (
            "private_key",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow"
            + "A" * 64
            + "\n-----END RSA PRIVATE KEY-----",
        ),
        ("openai_key", 'OPENAI="sk-proj-Ab12Cd34Ef56Gh78Ij90KlMn"'),
        ("anthropic_key", 'key: "sk-ant-api03-Ab12Cd34Ef56Gh78Ij90KlMn"'),
    ],
)
def test_each_secret_kind_is_found(kind, body):
    value = find_secret(body, kind)
    assert value and value in body


def test_secret_negatives():
    anon = jwt({"role": "anon", "ref": "x"})
    assert find_secret(f'const k = "{anon}";', "supabase_service_role") is None
    assert find_secret("sk_test_51HxYzAbCdEfGhIjKlMnOpQr", "stripe_secret") is None
    assert find_secret('id="AKIAIOSFODNN7EXAMPLE" region="eu-west-1"', "aws_secret") is None
    assert find_secret("-----BEGIN PUBLIC KEY-----", "private_key") is None
    assert find_secret("sk-loading-spinner-wrapper-outer", "openai_key") is None
    assert find_secret("sk-ant-api03-Ab12Cd34Ef56Gh78Ij90KlMn", "openai_key") is None
    assert find_secret("anything", "unknown_kind") is None


ENV = b"# settings\nDATABASE_URL=postgres://x\nexport SECRET_KEY=abc\nSMTP_HOST=mail\n"
SOURCE_MAP = {"version": 3, "sources": ["src/app.ts"], "sourcesContent": ["const a = 1;"]}


@pytest.mark.parametrize(
    ("kind", "body", "url"),
    [
        ("env", ENV, "https://x.com/.env"),
        ("git", b"[core]\n\trepositoryformatversion = 0\n", "https://x.com/.git/config"),
        ("git", b"ref: refs/heads/main\n", "https://x.com/.git/HEAD"),
        ("backup", b"-- dump\nCREATE TABLE users (id int);\n", "https://x.com/backup.sql"),
        ("backup", gzip.compress(b"data"), "https://x.com/site-backup.tar.gz"),
        ("backup", b"PK\x03\x04rest", "https://x.com/db.zip"),
        ("source_map", json.dumps(SOURCE_MAP).encode(), "https://x.com/app.js.map"),
    ],
)
def test_each_file_kind_matches(kind, body, url):
    assert file_matches(body, kind, url)


def test_file_negatives():
    html = b"<!doctype html><html><body>DATABASE_URL=x\nA=1\nB=2\n</body></html>"
    assert not file_matches(html, "env", "https://x.com/.env")
    assert not file_matches(b"A=1\nB=2\n", "env", "https://x.com/.env")
    assert not file_matches(b"ref: refs/heads/main", "git", "https://x.com/.git/config")
    assert not file_matches(b"<html>CREATE TABLE x</html>", "backup", "https://x.com/b.sql")
    assert not file_matches(gzip.compress(b"x"), "backup", "https://x.com/app.js.gz")
    empty_map = {**SOURCE_MAP, "sourcesContent": [None, ""]}
    assert not file_matches(json.dumps(empty_map).encode(), "source_map", "https://x.com/a.map")
    assert not file_matches(b"[1, 2]", "source_map", "https://x.com/a.map")


def test_file_names_hold_no_values():
    names = file_names(ENV, "env")
    assert names == ["DATABASE_URL", "SECRET_KEY", "SMTP_HOST"]
    assert "postgres" not in repr(names)
    assert file_names(b"CREATE TABLE IF NOT EXISTS users (id int);", "backup") == ["users"]


def test_baas_hosts():
    assert baas_host("https://abcd.supabase.co/rest/v1/users")
    assert baas_host("https://demo-default-rtdb.firebaseio.com/users.json")
    assert baas_host("https://firestore.googleapis.com/v1/projects/p/databases")
    assert not baas_host("https://supabase.co/x")
    assert not baas_host("http://abcd.supabase.co/x")
    assert not baas_host("https://evil.com/abcd.supabase.co")
