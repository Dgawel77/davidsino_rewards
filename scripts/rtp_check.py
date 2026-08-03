"""
Report each machine's return-to-player.

3-reel machines are enumerated exactly (every reel-strip combination weighted by
probability). The 5x3 machine has 40^15 combinations, so it is sampled with the
real provably-fair RNG.

Usage:  python3 scripts/rtp_check.py [samples]
Target band: 88-96% RTP (i.e. a 4-12% house edge).
"""
import os
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slots  # noqa: E402

TARGET_LO, TARGET_HI = 88.0, 96.0


def exact_rtp_reel3(machine):
    """Exhaustive: every combination of three reel positions, equally likely."""
    reel = slots.REELS[machine["key"]]
    n = len(reel)
    bet = 1.0
    total = 0.0
    for combo in product(range(n), repeat=3):
        rolls = [(i + 0.5) / n for i in combo]
        total += slots._spin_reel3(machine, bet, rolls)["win"]
    return total / (n ** 3) * 100


def sampled_rtp(machine, samples):
    key, bet = machine["key"], 1.0
    server = slots.new_server_seed()
    client = "rtp-check"
    total = 0.0
    for nonce in range(samples):
        rolls = list(slots.float_stream(server, client, nonce, slots.rolls_needed(key)))
        if machine["kind"] == "reel3":
            total += slots._spin_reel3(machine, bet, rolls)["win"]
        else:
            total += slots._spin_lines5x3(machine, bet, rolls)["win"]
    return total / (bet * samples) * 100


def hit_rate(machine, samples):
    key, bet = machine["key"], 1.0
    server = slots.new_server_seed()
    hits = 0
    for nonce in range(samples):
        rolls = list(slots.float_stream(server, "hit-rate", nonce, slots.rolls_needed(key)))
        if machine["kind"] == "reel3":
            win = slots._spin_reel3(machine, bet, rolls)["win"]
        else:
            win = slots._spin_lines5x3(machine, bet, rolls)["win"]
        if win > 0:
            hits += 1
    return hits / samples * 100


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
    print(f"{'machine':16s} {'RTP':>8s} {'edge':>7s} {'hit rate':>9s}  method")
    print("-" * 60)
    ok = True
    for key, machine in slots.MACHINES.items():
        if machine["kind"] == "reel3":
            rtp = exact_rtp_reel3(machine)
            method = f"exact ({len(slots.REELS[key])**3:,} combos)"
        else:
            rtp = sampled_rtp(machine, samples)
            method = f"sampled ({samples:,} spins)"
        hr = hit_rate(machine, min(samples, 200_000))
        flag = "" if TARGET_LO <= rtp <= TARGET_HI else "  <-- OUT OF BAND"
        if flag:
            ok = False
        print(f"{key:16s} {rtp:7.2f}% {100 - rtp:6.2f}% {hr:8.2f}%  {method}{flag}")
    print("-" * 60)
    print(f"target band: {TARGET_LO}-{TARGET_HI}% RTP")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
