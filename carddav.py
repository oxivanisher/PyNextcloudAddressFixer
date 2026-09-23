"""CardDAV client for fetching and updating contacts via Nextcloud."""

import logging
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import requests
import vobject

_FN_RE = re.compile(r"^FN[;:](.+)$", re.MULTILINE)

logger = logging.getLogger(__name__)

NS = {
    "d": "DAV:",
    "card": "urn:ietf:params:xml:ns:carddav",
}

PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:getetag/>
    <d:getcontenttype/>
  </d:prop>
</d:propfind>"""

MULTIGET_BODY_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<card:addressbook-multiget xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag/>
    <card:address-data/>
  </d:prop>
  {hrefs}
</card:addressbook-multiget>"""


@dataclass
class Contact:
    url: str
    etag: str
    raw_vcf: str
    vcard: vobject.vCard


class CardDAVClient:
    def __init__(self, config: dict):
        nc = config["nextcloud"]
        self.base_url = nc["url"].rstrip("/")
        self.username = nc["username"]
        self.password = nc["password"]
        path = nc.get(
            "addressbook_path",
            f"/remote.php/dav/addressbooks/users/{nc['username']}/contacts/",
        )
        self.addressbook_url = self.base_url + path
        self.session = requests.Session()
        self.session.auth = (self.username, self.password)
        self.session.headers.update({"User-Agent": "PyNextcloudAddressFixer/1.0"})

    def fetch_all_contacts(self) -> list[Contact]:
        """Fetch all vCards from the addressbook."""
        logger.info("Fetching contact list from %s", self.addressbook_url)
        resource_urls = self._propfind()
        if not resource_urls:
            return []
        logger.info("Found %d contacts, fetching data...", len(resource_urls))
        return self._multiget_report(resource_urls)

    def update_contact(self, contact: Contact, new_vcf: str) -> bool:
        """PUT updated vCard back to server. Returns True on success."""
        headers = {
            "Content-Type": "text/vcard; charset=utf-8",
            "If-Match": contact.etag,
        }
        try:
            resp = self.session.put(contact.url, data=new_vcf.encode("utf-8"), headers=headers)
        except requests.exceptions.ConnectionError as e:
            logger.error("Connection error updating %s: %s", contact.url, e)
            return False

        if resp.status_code == 204:
            logger.info("Updated: %s", contact.url)
            return True
        elif resp.status_code == 412:
            logger.warning(
                "ETag mismatch for %s - contact was modified externally. Skipping.", contact.url
            )
            return False
        else:
            logger.error("Unexpected status %d for %s: %s", resp.status_code, contact.url, resp.text)
            return False

    def _propfind(self) -> list[str]:
        """Return list of absolute vCard resource URLs."""
        try:
            resp = self.session.request(
                "PROPFIND",
                self.addressbook_url,
                data=PROPFIND_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                timeout=30,
            )
        except requests.exceptions.ConnectionError as e:
            raise SystemExit(f"Cannot connect to {self.addressbook_url}: {e}") from e

        if resp.status_code == 401:
            raise SystemExit("Authentication failed. Check your username and app password.")
        if resp.status_code not in (207,):
            raise SystemExit(f"PROPFIND failed with HTTP {resp.status_code}: {resp.text[:200]}")

        root = ET.fromstring(resp.text)
        urls = []
        for response in root.findall("d:response", NS):
            href = response.findtext("d:href", namespaces=NS)
            if href is None:
                continue
            content_type = response.findtext(
                "d:propstat/d:prop/d:getcontenttype", namespaces=NS
            ) or ""
            # Skip the collection itself and non-vCard resources
            if "vcard" not in content_type.lower() and not href.endswith(".vcf"):
                # Check if it has an etag (resource) vs collection
                etag = response.findtext("d:propstat/d:prop/d:getetag", namespaces=NS)
                if not etag:
                    continue
            if href.startswith("http"):
                urls.append(href)
            else:
                urls.append(self.base_url + href)
        return urls

    def _multiget_report(self, urls: list[str]) -> list[Contact]:
        """Fetch all vCards in one REPORT request."""
        hrefs_xml = "\n  ".join(f'<d:href>{self._href_path(u)}</d:href>' for u in urls)
        body = MULTIGET_BODY_TEMPLATE.format(hrefs=hrefs_xml)

        try:
            resp = self.session.request(
                "REPORT",
                self.addressbook_url,
                data=body,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                timeout=60,
            )
        except requests.exceptions.ConnectionError as e:
            raise SystemExit(f"Cannot fetch contacts: {e}") from e

        if resp.status_code not in (207,):
            raise SystemExit(f"REPORT failed with HTTP {resp.status_code}: {resp.text[:200]}")

        root = ET.fromstring(resp.text)
        contacts = []
        for response in root.findall("d:response", NS):
            href = response.findtext("d:href", namespaces=NS)
            etag = response.findtext("d:propstat/d:prop/d:getetag", namespaces=NS) or ""
            vcf_data = response.findtext(
                "d:propstat/d:prop/card:address-data", namespaces=NS
            )
            if not vcf_data:
                continue
            url = self.base_url + href if not href.startswith("http") else href
            try:
                vcard = vobject.readOne(vcf_data)
            except Exception as e:
                m = _FN_RE.search(vcf_data)
                name = m.group(1).strip() if m else url.rstrip("/").rsplit("/", 1)[-1]
                logger.warning("Skipping '%s': %s", name, e)
                continue
            contacts.append(Contact(url=url, etag=etag.strip(), raw_vcf=vcf_data, vcard=vcard))
        return contacts

    def _href_path(self, url: str) -> str:
        """Strip base URL to get just the path."""
        if url.startswith(self.base_url):
            return url[len(self.base_url):]
        return url
