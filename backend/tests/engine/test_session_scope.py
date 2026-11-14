"""The scope of a security test: the proved host's registrable domain and its subdomains."""

from app.browser.session import in_scope, scope_base


def test_scope_base():
    assert scope_base("staging.personaizit.com") == "personaizit.com"
    assert scope_base("personaizit.com") == "personaizit.com"
    assert scope_base("api.app.personaizit.com.") == "personaizit.com"
    assert scope_base("shop.example.co.uk") == "example.co.uk"
    assert scope_base("www.loja.com.br") == "loja.com.br"
    assert scope_base("127.0.0.1") == "127.0.0.1"


def test_in_scope():
    base = scope_base("staging.personaizit.com")
    assert in_scope("https://personaizit.com/x", base)
    assert in_scope("https://api.personaizit.com/v1?q=1", base)
    assert in_scope("http://staging.personaizit.com:8080/", base)
    assert not in_scope("https://evilpersonaizit.com/", base)
    assert not in_scope("https://personaizit.com.evil.com/", base)
    assert not in_scope("ftp://personaizit.com/", base)
    assert not in_scope("https://example.co.uk/", scope_base("shop.other.co.uk"))
    assert in_scope("https://a.other.co.uk/", scope_base("shop.other.co.uk"))
