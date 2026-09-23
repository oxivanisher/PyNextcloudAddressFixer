"""PyNextcloudAddressFixer - fix mangled address fields in Nextcloud contacts."""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml
from rich.console import Console

from address_parser import find_malformed_addresses, get_display_name
from carddav import CardDAVClient
from reviewer import confirm_proceed, run as review

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fix mangled address fields in Nextcloud contacts via CardDAV."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and review addresses but do not write changes to Nextcloud.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing backup VCF files before updating.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args()


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        console.print(
            f"[red]Config file not found:[/red] {path}\n"
            "Copy [bold]config.example.yaml[/bold] to [bold]config.yaml[/bold] and fill in your details."
        )
        sys.exit(1)
    with config_path.open() as f:
        config = yaml.safe_load(f)

    nc = config.get("nextcloud", {})
    for required in ("url", "username", "password"):
        if not nc.get(required):
            console.print(f"[red]Missing required config field:[/red] nextcloud.{required}")
            sys.exit(1)

    return config


def backup_vcf(contact, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    uid = "unknown"
    if hasattr(contact.vcard, "uid"):
        uid = contact.vcard.uid.value.replace("/", "_").replace(":", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = backup_dir / f"{uid}_{timestamp}.vcf"
    filename.write_text(contact.raw_vcf, encoding="utf-8")
    logging.debug("Backed up %s to %s", contact.url, filename)


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    config = load_config(args.config)

    # CLI flags override config file
    if args.dry_run:
        config.setdefault("options", {})["dry_run"] = True
    if args.no_backup:
        config.setdefault("options", {})["backup_vcf"] = False

    dry_run = config.get("options", {}).get("dry_run", False)
    do_backup = config.get("options", {}).get("backup_vcf", True)

    if dry_run:
        console.print("[yellow]Dry-run mode enabled. No changes will be written.[/yellow]\n")

    # 1. Fetch contacts
    client = CardDAVClient(config)
    console.print("Connecting to Nextcloud...")
    contacts = client.fetch_all_contacts()
    console.print(f"Fetched [bold]{len(contacts)}[/bold] contacts.")

    # 2. Find malformed addresses
    candidates = find_malformed_addresses(contacts)

    if not candidates:
        console.print("[green]No mangled addresses found. Nothing to do.[/green]")
        return

    auto_count = sum(1 for c in candidates if c.parsed is not None)
    manual_count = len(candidates) - auto_count
    console.print(
        f"\nFound [bold]{len(candidates)}[/bold] mangled address(es): "
        f"[green]{auto_count} auto-parseable[/green], "
        f"[yellow]{manual_count} need manual input[/yellow].\n"
    )

    # 3. Interactive review
    approved = review(candidates, config)

    # 4. Confirm and apply
    if not confirm_proceed(len(approved), dry_run):
        return

    backup_dir = Path("backups")
    success = 0
    failed = 0

    for change in approved:
        name = get_display_name(change.contact.vcard)

        if do_backup:
            try:
                backup_vcf(change.contact, backup_dir)
            except OSError as e:
                console.print(f"[yellow]Warning: could not back up {name}: {e}[/yellow]")

        ok = client.update_contact(change.contact, change.new_vcf)
        if ok:
            console.print(f"[green]✓[/green] Updated: {name}")
            success += 1
        else:
            console.print(f"[red]✗[/red] Failed:  {name}")
            failed += 1

    console.print(
        f"\nDone. [green]{success} updated[/green]"
        + (f", [red]{failed} failed[/red]" if failed else "")
        + "."
    )
    if do_backup and success > 0:
        console.print(f"[dim]Original VCFs backed up to ./{backup_dir}/[/dim]")


if __name__ == "__main__":
    main()
