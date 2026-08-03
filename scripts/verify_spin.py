"""
Standalone spin verifier — proof the Davidsino isn't cheating.

Run this on YOUR machine, against the seed the house revealed to you. It talks to
no server and trusts nothing: it recomputes the spin from scratch using the same
public algorithm the house claims to use.

    python3 scripts/verify_spin.py \
        --server-seed <revealed server seed> \
        --client-seed <your client seed> \
        --hash <the hash you were shown BEFORE you played> \
        --machine classic --bet 100 --spins 5

What it checks:
  1. sha256(server_seed) == the hash published before you played
     -> proves the house didn't swap the seed after seeing your bets
  2. Each spin recomputed from (server_seed, client_seed, nonce)
     -> compare these grids against what the app showed you

Only needs Python 3 and the repo's slots.py. No dependencies.
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slots  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Verify Davidsino slot spins")
    p.add_argument("--server-seed", required=True, help="revealed after rotating your seed")
    p.add_argument("--client-seed", required=True, help="the client seed those spins used")
    p.add_argument("--hash", help="the server seed hash shown BEFORE you played")
    p.add_argument("--machine", default="classic", choices=sorted(slots.MACHINES))
    p.add_argument("--bet", type=float, default=100)
    p.add_argument("--spins", type=int, default=10, help="how many spins to recompute")
    p.add_argument("--start", type=int, default=0, help="first spin number (nonce)")
    args = p.parse_args()

    print("=" * 62)
    print("STEP 1 — did the house honour its commitment?")
    print("=" * 62)
    calculated = hashlib.sha256(args.server_seed.encode()).hexdigest()
    print(f"  sha256(server seed) = {calculated}")
    if args.hash:
        print(f"  hash shown to you   = {args.hash}")
        if calculated == args.hash.strip().lower():
            print("  ✅ MATCH — the seed was locked in before you played.")
        else:
            print("  ❌ MISMATCH — the revealed seed is NOT the one committed to.")
            print("     The results below are meaningless. Something is wrong.")
            return 1
    else:
        print("  (no --hash given; compare the value above yourself)")

    print()
    print("=" * 62)
    print(f"STEP 2 — recomputing spins on {slots.MACHINES[args.machine]['name']}")
    print("=" * 62)
    print(f"  client seed: {args.client_seed}")
    print(f"  bet: {args.bet:g} points")
    print()

    total_wagered = total_won = 0.0
    for nonce in range(args.start, args.start + args.spins):
        r = slots.spin(args.machine, args.bet, args.server_seed, args.client_seed, nonce)
        total_wagered += args.bet
        total_won += r["win"]
        grid = " | ".join(" ".join(row) for row in r["grid"])
        outcome = r["detail"] or "no win"
        print(f"  spin #{nonce:<4} {grid}")
        print(f"            {outcome} -> {r['win']:g} pts")

    print()
    net = total_won - total_wagered
    print(f"  over {args.spins} spins: wagered {total_wagered:g}, "
          f"won {total_won:g}, net {net:+g} pts")
    print()
    print("  Compare these grids to what the app showed you. If they match,")
    print("  the results were fixed by the seed before you ever hit SPIN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
