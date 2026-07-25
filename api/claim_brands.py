# -*- coding: utf-8 -*-
"""Claim the brands that pre-date ownership — a human decision, taken deliberately.

    python -m api.claim_brands --list
    python -m api.claim_brands --email you@example.com

WHY THIS IS A COMMAND AND NOT AN AUTOMATIC MIGRATION. Brands stored before accounts were scoped
carry no `owner_id`, and who created them is recorded nowhere. Any owner the code picked — the first
account, the oldest account, the only account — would be a GUESS written to disk as if it were a
fact, which is the one thing this codebase exists not to do. So the store surfaces them as
`unclaimed`: readable (they are the operator's own catalog on the operator's own machine), never
deletable, and visibly flagged. This command is how a person answers the question the data cannot.
"""
from __future__ import annotations

import argparse
import sys

import config_env

config_env.load_env()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assign unowned brands to an account.")
    ap.add_argument("--email", help="the account that will own every currently-unowned brand")
    ap.add_argument("--list", action="store_true", help="show unowned brands and known accounts")
    args = ap.parse_args(argv)

    from api.brand_store import BrandStore
    from api.user_auth import UserStore

    store = BrandStore()
    unowned = [b for b in store.list() if not b.get("owner_id")]

    if args.list or not args.email:
        print(f"Unowned brands: {len(unowned)}")
        for b in unowned:
            print(f"  - {b.get('domain_key')}  ({b.get('organization_name') or '—'})")
        print("\nAccounts:")
        for email in sorted(UserStore().emails()):
            print(f"  - {email}")
        if not args.email:
            print("\nRun again with --email <one of the accounts above> to claim them.")
        return 0

    user = UserStore().get_by_email(args.email)
    if user is None:
        print(f"No account with email {args.email!r}. Run --list to see the accounts.",
              file=sys.stderr)
        return 2
    if not unowned:
        print("Nothing to claim — every brand already has an owner.")
        return 0

    claimed = store.claim_unowned(user["id"])
    print(f"Claimed {claimed} brand(s) for {args.email}:")
    for b in unowned:
        print(f"  - {b.get('domain_key')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
