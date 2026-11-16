"""Small CLI for testing the client.

    python -m threet centers
    python -m threet classes --center 1 --from 2026-11-22
    python -m threet bookings
    python -m threet suggest --since 2025-11-16
    python -m threet book <activity_id> --confirm        # real booking!
    python -m threet cancel <booking_id> --confirm       # real cancellation!

Auth: set THREET_ACCESS_TOKEN, or THREET_USERNAME (password is prompted).
Nothing is written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from datetime import date, datetime, time, timezone

from pathlib import Path

import aiohttp

# The API library lives inside the HA integration so it ships with it; it has no HA imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "threet"))
from threet_api import ThreeTClient  # noqa: E402
from threet_api.suggest import suggest  # noqa: E402


def _client(session: aiohttp.ClientSession) -> ThreeTClient:
    if token := os.environ.get("THREET_ACCESS_TOKEN"):
        return ThreeTClient(session, access_token=token)
    if username := os.environ.get("THREET_USERNAME"):
        if not sys.stdin.isatty():
            sys.exit("Passord-prompt krever en interaktiv terminal (kjør i et vanlig terminalvindu).")
        return ThreeTClient(session, username=username, password=getpass.getpass("3T-passord: "))
    return ThreeTClient(session)


def _local(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%a %d.%m %H:%M") if dt else "-"


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="threet")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("centers")
    c = sub.add_parser("classes")
    c.add_argument("--center", action="append", default=[])
    c.add_argument("--from", dest="from_date", type=date.fromisoformat)
    c.add_argument("--to", dest="to_date", type=date.fromisoformat)
    c.add_argument("--anon", action="store_true", help="uten innlogging")
    sub.add_parser("bookings")
    s = sub.add_parser("suggest", help="foreslå faste timer fra historikk")
    s.add_argument("--since", type=date.fromisoformat, required=True)
    s.add_argument("--min", type=int, default=2)
    b = sub.add_parser("book")
    b.add_argument("activity_id")
    b.add_argument("--confirm", action="store_true")
    x = sub.add_parser("cancel")
    x.add_argument("booking_id")
    x.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)

    async with aiohttp.ClientSession() as session:
        api = _client(session)
        if args.cmd == "centers":
            for ctr in await api.get_centers():
                print(f"{ctr.id:>5}  {ctr.slug:<18} {ctr.name}")
        elif args.cmd == "classes":
            acts = await api.get_group_activities(
                args.center, args.from_date, args.to_date, authenticated=not args.anon
            )
            for a in acts:
                print(
                    f"{a.id:>9}  {_local(a.start)}  {a.name:<28} {a.center_name:<16}"
                    f" {a.slots_available}/{a.slots_total}  åpner {_local(a.bookable_earliest)}"
                )
        elif args.cmd == "bookings":
            for bk in await api.get_my_bookings(datetime.now(timezone.utc)):
                a = bk.activity
                wl = f" (venteliste #{bk.waiting_list_position})" if bk.is_waiting_list else ""
                print(
                    f"booking {bk.id:>9}  {_local(a.start if a else None)}  {a.name if a else '?'}"
                    f" @ {a.center_name if a else '?'}{wl}  kan avbestilles={bk.can_cancel}"
                )
        elif args.cmd == "suggest":
            since = datetime.combine(args.since, time(), timezone.utc)
            history = await api.get_my_bookings(since)
            starts = [b.activity.start for b in history if b.activity]
            print(f"{len(history)} bookinger fra API-et", end="")
            print(f", eldste {_local(min(starts))}" if starts else "")
            for sg in suggest(history, args.min):
                ns = f", {sg.no_shows} no-show" if sg.no_shows else ""
                print(f"{sg.count:>3}x  {sg.slot}  (sist {_local(sg.last_seen)}{ns})")
        elif args.cmd == "book":
            if not args.confirm:
                print("Dette lager en ekte booking. Kjør på nytt med --confirm.")
                return 1
            print(await api.book(args.activity_id))
        elif args.cmd == "cancel":
            bookings = {bk.id: bk for bk in await api.get_my_bookings(datetime.now(timezone.utc))}
            bk = bookings.get(args.booking_id)
            if bk is None:
                print("Fant ingen kommende booking med den ID-en.")
                return 1
            if not args.confirm:
                print(f"Dette avbestiller {bk.activity.name if bk.activity else bk.id}. Bruk --confirm.")
                return 1
            print(await api.cancel(bk))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
