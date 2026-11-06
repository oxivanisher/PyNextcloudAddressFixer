# PyNextcloudAddressFixer

A vibe-coded (!) CLI tool that fixes mangled address entries in Nextcloud contacts. If your contacts have the full address crammed into a single comma-separated field (e.g. `"Musterstrasse 12, 8001 Zürich, Schweiz"`) instead of properly structured fields, this tool finds them, parses them, and lets you review and apply the fixes.

Supports Swiss (4-digit) and German (5-digit) postal codes, including `CH-` and `D-` prefixed variants.

## Prerequisites

- Python 3.12+
- A Nextcloud instance with CardDAV enabled
- A Nextcloud **app password** (Settings → Security → Devices & sessions → Create new app password)

## Installation

```bash
git clone https://github.com/oxivanisher/PyNextcloudAddressFixer.git
cd PyNextcloudAddressFixer
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and fill in your details:

```bash
cp config.example.yaml config.yaml
```

```yaml
nextcloud:
  url: "https://your.nextcloud.instance"
  username: "your_username"
  password: "your_app_password"

options:
  dry_run: false          # Preview changes without writing
  backup_vcf: true        # Save original VCFs to ./backups/ before modifying
  review_mode: "table"    # "table" or "one_by_one"
  skip_country_field: false
```

`config.yaml` is gitignored — never commit it.

## Usage

```bash
# Review and fix addresses (default: table mode)
python main.py

# Preview only, no writes
python main.py --dry-run

# Confirm each change individually
# Set review_mode: "one_by_one" in config.yaml, or temporarily:
python main.py --dry-run  # then edit config and re-run

# Use a different config file
python main.py --config ~/my-other-nextcloud.yaml

# Verbose output (shows HTTP requests)
python main.py --verbose
```

### Review workflow

**Table mode** (default): all candidates are shown at once in a table. Each row shows the original mangled value alongside the proposed fix. Commands:

| Command | Effect |
|---|---|
| `apply` | Apply all auto-parsed fixes |
| `skip N` | Skip row N |
| `edit N` | Manually enter fields for row N |
| `abort` | Exit without any changes |

**One-by-one mode**: each candidate is shown with the original highlighted in red and the proposed fix in green. Answer `y` / `n` / `e` (edit) / `a` (abort).

Before writing, you will be asked to confirm the total number of changes.

## How it works

1. **PROPFIND** — fetches the list of vCard resource URLs from your Nextcloud addressbook.
2. **REPORT (addressbook-multiget)** — retrieves all vCards in a single request.
3. **Parse** — scans every `ADR` field for commas; tries to split the value into street, postal code, city, and country using a Swiss address regex.
4. **Review** — presents proposed fixes for your approval.
5. **PUT** — writes each approved change back with an ETag check to prevent lost-update conflicts. Original VCFs are backed up to `./backups/` first (unless `--no-backup`).

## Limitations

- Supports Swiss (4-digit) and German (5-digit) postal codes, including `CH-`/`D-` prefixed variants. Other address formats may not auto-parse and will be flagged for manual input.
- Does not handle PO boxes (`Postfach`) automatically — these will be flagged for manual review.
- Requires Python 3.12+ (uses `X | Y` union type hints).

## Security

- Always use an **app password**, not your Nextcloud login password.
- `config.yaml` is in `.gitignore` — double-check before committing.
- Backups are stored locally in `./backups/` as plain VCF files.

## License

MIT — see [LICENSE](LICENSE).
