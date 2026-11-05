"""Portable export retains a real below-fold price-card capture."""

import base64
from datetime import timedelta
from uuid import uuid4

import pytest
from app.browser.prices import PRICE_BLOCKS_JS, extract_prices
from app.core.config import settings
from app.core.db import Session
from app.core.models import Job, utcnow
from app.v1.services.repair_report import render_repair_report
from playwright.async_api import async_playwright


async def test_export_embeds_real_price_card_with_visible_quote(tmp_path, monkeypatch):
    job_id = str(uuid4())
    monkeypatch.setattr(settings, "evidence_dir", tmp_path)
    directory = tmp_path / job_id
    directory.mkdir()
    filename = "capture-price.png"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<section style="margin-top:1500px;width:300px"><h2>Basic</h2>'
            "<p>USD 19 per month, per seat</p></section>"
        )
        card = page.locator("section")
        assert (await card.bounding_box())["y"] > page.viewport_size["height"]
        quote = await card.inner_text()
        await card.screenshot(path=str(directory / filename))
        await browser.close()
    finding = {
        "status": "confirmed",
        "details": {"key": "visible-price-test"},
        "evidence": [
            {
                "url": "https://example.test/pricing",
                "facts": [
                    {
                        "source": "visible_price",
                        "key": "visible-price-test",
                        "quote": quote,
                        "context": {"name": "Basic", "currency": "USD", "period": "month"},
                        "selector": "section",
                        "screenshot": filename,
                    }
                ],
            }
        ],
    }
    with Session.begin() as session:
        session.add(
            Job(
                id=job_id,
                url="https://example.test/pricing",
                mode="scripted",
                status="completed",
                deadline=utcnow() + timedelta(minutes=2),
                report={"findings": [finding]},
            )
        )
    try:
        body = render_repair_report(job_id).body.decode()
        encoded = base64.b64encode((directory / filename).read_bytes()).decode()
        assert 'src="data:image/png;base64,' + encoded + '"' in body
        assert "Matched price card" in body
        assert "USD 19 per month, per seat" in body
        assert "Price card screenshot" in body
    finally:
        with Session.begin() as session:
            session.delete(session.get(Job, job_id))


@pytest.mark.parametrize(
    "markup",
    [
        (
            '<div style="text-decoration:line-through"><section><h2>Basic</h2>'
            "<p>USD 19 per month, per seat</p></section></div>"
        ),
        "<section><h2>Basic</h2><p>−USD 19 per month, per seat</p></section>",
    ],
)
async def test_unsupported_amount_cannot_supply_a_current_price(markup):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div style="text-decoration:line-through"><section><h2>Basic</h2>'
            "<p>USD 19 per month, per seat</p></section></div>"
        )
        blocks = await page.evaluate(PRICE_BLOCKS_JS)
        prices = extract_prices(blocks["blocks"])
        assert prices["facts"] == []
        assert prices["exclusions"]
        await browser.close()
