"""Parse and fix malformed Swiss address entries in vCard ADR fields."""

import re
from dataclasses import dataclass

import vobject

from carddav import Contact

# Matches: "Musterstrasse 12a, 8001 Zürich" or "Musterstrasse 12a, 8001 Zürich, Schweiz"
SWISS_ADDRESS_RE = re.compile(
    r"""
    ^
    (?P<street>[^,]+?)           # street + optional number
    \s*,\s*
    (?P<postal>\d{4,5})          # 4-digit Swiss or 5-digit German postal code
    \s+
    (?P<city>[^,]+?)             # city name
    (?:\s*,\s*(?P<country>.+?))? # optional country
    \s*$
    """,
    re.VERBOSE,
)

# Also handle "CH-8001 Zürich" prefix style
CH_POSTAL_RE = re.compile(
    r"""
    ^
    (?P<street>[^,]+?)
    \s*,\s*
    (?:CH-|D-)?(?P<postal>\d{4,5})
    \s+
    (?P<city>[^,]+?)
    (?:\s*,\s*(?P<country>.+?))?
    \s*$
    """,
    re.VERBOSE,
)


@dataclass
class ParsedAddress:
    street: str
    postal_code: str
    city: str
    country: str | None
    source_field: str  # which ADR subfield contained the malformed data


@dataclass
class Candidate:
    contact: Contact
    adr: object  # the vobject ADR component
    raw_value: str  # the comma-containing string that was found
    parsed: ParsedAddress | None  # None = auto-parse failed, needs manual fix


def get_display_name(vcard: vobject.vCard) -> str:
    """Return a human-readable name for the contact."""
    if hasattr(vcard, "fn"):
        return vcard.fn.value
    if hasattr(vcard, "n"):
        n = vcard.n.value
        parts = [n.given, n.family]
        name = " ".join(p for p in parts if p)
        if name.strip():
            return name
    return "(unnamed)"


def find_malformed_addresses(contacts: list[Contact]) -> list[Candidate]:
    """Find contacts with comma-containing ADR fields."""
    candidates = []
    for contact in contacts:
        adrs = contact.vcard.contents.get("adr", [])
        for adr in adrs:
            v = adr.value
            # Check all ADR subfields for comma-containing values
            for field_name in ("street", "box", "extended", "city", "region", "code", "country"):
                field_val = getattr(v, field_name, "") or ""
                if "," in field_val:
                    parsed = _try_parse(field_val.strip(), field_name)
                    candidates.append(
                        Candidate(
                            contact=contact,
                            adr=adr,
                            raw_value=field_val.strip(),
                            parsed=parsed,
                        )
                    )
                    break  # one candidate per ADR component
    return candidates


def _try_parse(raw: str, source_field: str) -> ParsedAddress | None:
    """Try to parse a raw address string into structured components."""
    for pattern in (SWISS_ADDRESS_RE, CH_POSTAL_RE):
        m = pattern.match(raw)
        if m:
            return ParsedAddress(
                street=m.group("street").strip(),
                postal_code=m.group("postal").strip(),
                city=m.group("city").strip(),
                country=(m.group("country").strip() if m.group("country") else None),
                source_field=source_field,
            )
    return None


def apply_fix(contact: Contact, candidate: Candidate, fix: ParsedAddress, skip_country: bool = False) -> str:
    """
    Apply a parsed address fix to the vCard in-place and return
    the serialized vCard string ready for PUT.
    """
    v = candidate.adr.value

    # Clear the source field and set structured fields
    setattr(v, fix.source_field, "")
    v.street = fix.street
    v.code = fix.postal_code
    v.city = fix.city
    v.region = getattr(v, "region", "") or ""
    v.box = ""
    v.extended = ""

    if fix.country and not skip_country:
        v.country = fix.country
    elif not skip_country and not (getattr(v, "country", "") or ""):
        pass  # leave country empty if not provided

    return contact.vcard.serialize()
