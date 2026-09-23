"""Interactive terminal review workflow for address fixes."""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import box

from address_parser import Candidate, ParsedAddress, apply_fix, get_display_name
from carddav import Contact

console = Console()


@dataclass
class ApprovedChange:
    contact: Contact
    new_vcf: str


def run(candidates: list[Candidate], config: dict) -> list[ApprovedChange]:
    """Run the interactive review workflow, return approved changes."""
    mode = config.get("options", {}).get("review_mode", "table")
    skip_country = config.get("options", {}).get("skip_country_field", False)

    if mode == "one_by_one":
        return _review_one_by_one(candidates, skip_country)
    else:
        return _review_table(candidates, skip_country)


# ── Table mode ─────────────────────────────────────────────────────────────────

def _review_table(candidates: list[Candidate], skip_country: bool) -> list[ApprovedChange]:
    """Show all candidates in a table, then let the user decide per-row."""
    # approved[i] = None (skip) | ParsedAddress (use this fix)
    fixes: list[ParsedAddress | None] = [c.parsed for c in candidates]

    while True:
        _render_table(candidates, fixes)

        console.print(
            "\n[bold]Commands:[/bold]  "
            "[cyan]apply[/cyan]  apply all auto-parsed fixes  |  "
            "[cyan]skip N[/cyan]  skip row N  |  "
            "[cyan]edit N[/cyan]  manually edit row N  |  "
            "[cyan]abort[/cyan]  exit without changes\n"
        )
        cmd = Prompt.ask("[bold]>[/bold]").strip().lower()

        if cmd == "apply":
            break
        elif cmd == "abort":
            console.print("[yellow]Aborted. No changes made.[/yellow]")
            return []
        elif cmd.startswith("skip "):
            idx = _parse_index(cmd[5:], len(candidates))
            if idx is not None:
                fixes[idx] = None
                console.print(f"[dim]Row {idx + 1} will be skipped.[/dim]")
        elif cmd.startswith("edit "):
            idx = _parse_index(cmd[5:], len(candidates))
            if idx is not None:
                edited = _edit_fix(candidates[idx])
                fixes[idx] = edited
        else:
            console.print("[red]Unknown command.[/red]")

    # Build approved changes
    approved = []
    for candidate, fix in zip(candidates, fixes):
        if fix is None:
            continue
        new_vcf = apply_fix(candidate.contact, candidate, fix, skip_country)
        approved.append(ApprovedChange(contact=candidate.contact, new_vcf=new_vcf))
    return approved


def _render_table(candidates: list[Candidate], fixes: list[ParsedAddress | None]) -> None:
    table = Table(
        box=box.ROUNDED,
        show_lines=True,
        title="[bold]Addresses to Fix[/bold]",
        expand=True,
    )
    table.add_column("#", style="dim", width=3, no_wrap=True)
    table.add_column("Contact", style="bold", max_width=25)
    table.add_column("Original (mangled)", style="red", max_width=35)
    table.add_column("Street", max_width=25)
    table.add_column("Postal", width=8)
    table.add_column("City", max_width=20)
    table.add_column("Country", max_width=12)
    table.add_column("Status", width=14)

    for i, (cand, fix) in enumerate(zip(candidates, fixes)):
        name = get_display_name(cand.contact.vcard)
        if fix is None and cand.parsed is None:
            status = "[red]NEEDS MANUAL[/red]"
            street = postal = city = country = "[dim]-[/dim]"
        elif fix is None:
            status = "[yellow]SKIP[/yellow]"
            street = postal = city = country = "[dim]-[/dim]"
        else:
            status = "[green]APPLY[/green]"
            street = fix.street
            postal = fix.postal_code
            city = fix.city
            country = fix.country or ""

        table.add_row(
            str(i + 1),
            name,
            cand.raw_value,
            street,
            postal,
            city,
            country,
            status,
        )

    console.print(table)


# ── One-by-one mode ────────────────────────────────────────────────────────────

def _review_one_by_one(candidates: list[Candidate], skip_country: bool) -> list[ApprovedChange]:
    approved = []
    total = len(candidates)

    for i, candidate in enumerate(candidates):
        name = get_display_name(candidate.contact.vcard)
        console.rule(f"[bold]Contact {i + 1}/{total}: {name}[/bold]")
        console.print(f"[red]  Original:[/red] {candidate.raw_value}")

        fix = candidate.parsed

        if fix:
            country_str = f", {fix.country}" if fix.country else ""
            console.print(
                f"[green]  Proposed:[/green] {fix.street} | {fix.postal_code} {fix.city}{country_str}"
            )
        else:
            console.print("[yellow]  Could not auto-parse this address.[/yellow]")

        while True:
            choices = "[y]es / [n]o / [e]dit" + (" / [a]bort" if i > 0 else " / [a]bort")
            answer = Prompt.ask(f"  {choices}").strip().lower()

            if answer in ("y", "yes"):
                if fix is None:
                    console.print("[yellow]  No parsed fix available. Use [e]dit or [n]o.[/yellow]")
                    continue
                new_vcf = apply_fix(candidate.contact, candidate, fix, skip_country)
                approved.append(ApprovedChange(contact=candidate.contact, new_vcf=new_vcf))
                break
            elif answer in ("n", "no"):
                console.print("[dim]  Skipped.[/dim]")
                break
            elif answer in ("e", "edit"):
                fix = _edit_fix(candidate)
                if fix:
                    country_str = f", {fix.country}" if fix.country else ""
                    console.print(
                        f"[green]  Updated:[/green] {fix.street} | {fix.postal_code} {fix.city}{country_str}"
                    )
            elif answer in ("a", "abort"):
                console.print("[yellow]Aborted. No further changes will be made.[/yellow]")
                return approved
            else:
                console.print("[red]  Unknown input.[/red]")

    return approved


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _edit_fix(candidate: Candidate) -> ParsedAddress | None:
    """Prompt user to enter address fields manually."""
    console.print("[bold]Enter address fields (press Enter to keep current value):[/bold]")
    existing = candidate.parsed

    street = Prompt.ask(
        "  Street + number", default=existing.street if existing else ""
    ).strip()
    postal = Prompt.ask(
        "  Postal code (4 digits)", default=existing.postal_code if existing else ""
    ).strip()
    city = Prompt.ask(
        "  City", default=existing.city if existing else ""
    ).strip()
    country = Prompt.ask(
        "  Country (leave blank to skip)", default=existing.country if existing and existing.country else ""
    ).strip()

    if not street or not postal or not city:
        console.print("[red]  Street, postal code, and city are required. Fix skipped.[/red]")
        return None

    return ParsedAddress(
        street=street,
        postal_code=postal,
        city=city,
        country=country or None,
        source_field=existing.source_field if existing else "street",
    )


def _parse_index(s: str, length: int) -> int | None:
    try:
        idx = int(s.strip()) - 1
        if 0 <= idx < length:
            return idx
        console.print(f"[red]Row number must be between 1 and {length}.[/red]")
    except ValueError:
        console.print("[red]Expected a row number.[/red]")
    return None


def confirm_proceed(count: int, dry_run: bool) -> bool:
    """Ask user to confirm before writing changes."""
    if dry_run:
        console.print(f"\n[yellow]Dry-run mode: {count} change(s) would be applied. Nothing written.[/yellow]")
        return False
    if count == 0:
        console.print("\n[dim]No changes to apply.[/dim]")
        return False
    return Confirm.ask(f"\nApply [bold]{count}[/bold] change(s) to Nextcloud?")
