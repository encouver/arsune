"""Test account A is now a login page, an email and a password (was one JSON login step)."""

import json
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, url, access_test FROM bounties")).fetchall()
    for bounty_id, url, value in rows:
        access_test = json.loads(value) if isinstance(value, str) else value
        if not access_test or "login" not in access_test:
            continue
        login = access_test.pop("login") or {}
        body = login.get("body") or {}
        account = None
        # Only the demo shop has a known login page; other owners enter theirs again.
        if login.get("path") == "/shop/api/login" and body.get("email"):
            account = {
                "login_url": "{0.scheme}://{0.netloc}/shop/login".format(urlsplit(url)),
                "email": body["email"],
                "password": body.get("password", ""),
            }
        access_test["account"] = account
        bind.execute(
            sa.text("UPDATE bounties SET access_test = :value WHERE id = :id"),
            {"value": json.dumps(access_test), "id": bounty_id},
        )


def downgrade():
    pass
