"""Bounded, conservative extraction of explicit English pricing contexts."""

import hashlib
import json
import re
from decimal import Decimal

CURRENCIES = "USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|INR"
MONEY = re.compile(
    rf"(?<![\w.])(?:(?P<before>{CURRENCIES})\s*(?P<a>\d+(?:\.\d{{1,2}})?)|"
    rf"(?P<b>\d+(?:\.\d{{1,2}})?)\s*(?P<after>{CURRENCIES}))(?![\w.,])"
)
PRICE_SIGNAL = re.compile(
    r"(?:\b(?:"
    + CURRENCIES
    + r")\b|[$€£¥])\s*\d|\d\s*\b(?:"
    + CURRENCIES
    + r")\b|\b\d+(?:\.\d{1,2})?\s*(?:/\s*|per\s+)(?:month|year)\b"
)
PERIOD = re.compile(
    r"(?:/\s*|\bper\s+|\ba\s+|\beach\s+)(month|year)\b|\b(monthly|yearly|annually)\b", re.IGNORECASE
)
UNSUPPORTED = re.compile(
    r"\b(from|starting|start at|trial|discount|save|saving|off|introductory|promo|"
    r"promotion|coupon|first|then|was|previously|normally|tax|vat|minimum|maximum|"
    r"up to|as low|equivalent|effective|billed|charged|payable|one.time|setup|"
    r"add.on|overage|usage|credit|custom|contact|volume|tier|region|country|"
    r"only|until|limited|new customer|existing customer|annual|contract|commitment)\b",
    re.IGNORECASE,
)
GENERIC_NAMES = {
    "pricing",
    "prices",
    "plans",
    "our plans",
    "choose your plan",
    "subscriptions",
    "offers",
}

# Start at a visible price-bearing leaf, then use its closest named card. innerText
# omits hidden descendants. No page attributes or model interpretations are needed.
PRICE_BLOCKS_JS = r"""() => {
    const signal = /(?:\b(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|INR)\b|[$€£¥])\s*\d|\d\s*\b(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|JPY|INR)\b|\b\d+(?:\.\d{1,2})?\s*(?:\/\s*|per\s+)(?:month|year)\b/;
    const visible = e => e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    const elements = Array.from(document.querySelectorAll('body *'));
    const positions = new Map(elements.map((e, i) => [e, i]));
    const selector = e => {
        const parts = [];
        while (e && e !== document.body) {
            const index = Array.from(e.parentElement.children).indexOf(e) + 1;
            parts.unshift(e.tagName.toLowerCase() + ':nth-child(' + index + ')');
            e = e.parentElement;
        }
        return 'body > ' + parts.join(' > ');
    };
    const found = new Map();
    for (const leaf of elements) {
        if (found.size >= 80) break;
        if (!visible(leaf) || !signal.test(leaf.innerText || '') ||
            Array.from(leaf.children).some(e => visible(e) && signal.test(e.innerText || ''))) continue;
        if (leaf.closest('[data-fact-key][data-context]')) continue;
        let card = leaf;
        let heading = '';
        while (card && !['BODY', 'MAIN', 'HTML'].includes(card.tagName)) {
            const headings = Array.from(card.querySelectorAll('h1,h2,h3,h4,h5,h6')).filter(visible);
            if (headings.length === 1) { heading = headings[0].innerText.trim(); break; }
            if (headings.length > 1) break;
            card = card.parentElement;
        }
        if (!heading) card = leaf;
        if (found.has(card)) continue;
        const text = (card.innerText || '').trim();
        const ancestors = [];
        for (let node = card; node; node = node.parentElement) ancestors.push(node);
        let parent = card.parentElement;
        const groups = [];
        while (heading && parent && parent !== document.documentElement) {
            const names = Array.from(parent.children).filter(e => /^H[1-6]$/.test(e.tagName) && visible(e));
            groups.unshift(...names.map(e => e.innerText.trim()));
            parent = parent.parentElement;
        }
        found.set(card, {text: text.slice(0, 2000), heading, selector: selector(card),
            source_position: positions.get(card), groups, hidden_text: Array.from(card.querySelectorAll('*')).some(e => getComputedStyle(e).opacity === '0' && e.textContent.trim()), truncated: text.length > 2000,
            struck: Array.from(card.querySelectorAll('*')).concat(ancestors).some(e => visible(e) &&
                (['S','DEL','STRIKE'].includes(e.tagName) || getComputedStyle(e).textDecorationLine.includes('line-through')))});
    }
    return {blocks: Array.from(found.values()), truncated: found.size >= 80};
}"""


def extract_prices(blocks):
    facts, exclusions = [], []
    for block in blocks:
        text = block.get("text", "").strip()
        if not PRICE_SIGNAL.search(text):
            continue
        base = {
            "quote": text,
            "selector": block.get("selector"),
            "source_position": block.get("source_position"),
        }
        matches = list(MONEY.finditer(text))
        periods = {match.group(1) or match.group(2) for match in PERIOD.finditer(text.lower())}
        periods = {"year" if p in {"year", "yearly", "annually"} else "month" for p in periods}
        name = " ".join(block.get("heading", "").split()).casefold()
        reason = None
        if block.get("truncated"):
            reason = "The price card exceeds the text limit."
        elif not name or len(name) > 100 or name in GENERIC_NAMES or PRICE_SIGNAL.search(name):
            reason = "The price has no unique plan or product heading."
        elif block.get("hidden_text"):
            reason = "Hidden card text prevents a reliable visible price context."
        elif block.get("struck") or UNSUPPORTED.search(text):
            reason = "The offer has a promotion, a condition, or unsupported billing terms."
        elif any(symbol in text for symbol in "$€£¥"):
            reason = "A currency symbol is ambiguous; an explicit ISO currency code is required."
        elif re.search(r"[-−+]\s*(?:" + CURRENCIES + r")\s*\d", text):
            reason = "Signed prices are outside the supported amount format."
        elif len(matches) != 1:
            reason = "The card does not have exactly one supported explicit currency amount."
        elif len(periods) != 1:
            reason = "The card does not have one explicit monthly or yearly billing period."
        else:
            match = matches[0]
            remaining = text[: match.start()] + text[match.end() :]
            units = {
                u.lower()
                for u in re.findall(
                    r"(?:\bper\s+|/\s*)(seat|user|license|account)s?\b", text, re.IGNORECASE
                )
            }
            # Numeric limits and unnamed units can change the offer. Do not infer
            # equivalence from a shared plan heading in these cases.
            unknown_units = re.findall(r"(?:\bper\s+|/\s*)([a-z]+)", text, re.IGNORECASE)
            if len(units) > 1 or any(
                u.lower() not in {"month", "year", "seat", "user", "license", "account"}
                for u in unknown_units
            ):
                reason = "The offer has mixed or unsupported units."
            elif re.search(
                r"\d|\b(?:seats|users|licenses|accounts|daily|weekly|quarterly|day|week|quarter)\b",
                remaining,
                re.IGNORECASE,
            ):
                reason = "The offer has a quantity limit or unsupported period."
            elif re.search(r"\b(?:seat|user|license)\b", remaining, re.IGNORECASE) and not units:
                reason = "The offer does not state an explicit unit."
        if reason:
            exclusions.append({**base, "reason": reason})
            continue
        groups = [" ".join(g.split()).casefold() for g in block.get("groups", [])]
        groups = [g for g in groups if g not in GENERIC_NAMES and g != name]
        # Preserve all remaining card words. This distinguishes qualifiers and
        # feature limits that the supported price grammar does not interpret.
        qualifier = remaining.replace(block.get("heading", ""), "", 1)
        qualifier = PERIOD.sub(" ", qualifier)
        qualifier = re.sub(
            r"(?:\bper\s+|/\s*)(?:seat|user|license|account)s?\b",
            " ",
            qualifier,
            flags=re.IGNORECASE,
        )
        qualifier = " ".join(re.sub(r"[^\w\s]", " ", qualifier).casefold().split())
        context = {
            "name": name,
            "product_context": groups,
            "currency": match.group("before") or match.group("after"),
            "period": next(iter(periods)),
            "unit": next(iter(units)).lower() if units else "offer",
            "qualifiers": [qualifier] if qualifier else [],
        }
        key = (
            "visible-price|"
            + hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:24]
        )
        value = format(Decimal(match.group("a") or match.group("b")).normalize(), "f")
        if len(facts) >= 12:
            exclusions.append(
                {**base, "reason": "The page exceeds the twelve price-card evidence limit."}
            )
            continue
        facts.append(
            {**base, "key": key, "value": value, "source": "visible_price", "context": context}
        )
    return {"facts": facts, "exclusions": exclusions}
