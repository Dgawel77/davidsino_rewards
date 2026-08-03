#!/usr/bin/env python3
"""
Independently recompute any table hand from a revealed seed.

Run this yourself. It talks to no server and trusts nothing the house says --
it re-derives the shoe from the seed and deals the hand again.

    1. Before playing you were shown a server seed HASH. Write it down.
    2. Play. Note the nonce printed on each hand.
    3. Rotate your seed (Slots -> Fairness -> Rotate). That reveals the secret.
    4. Run:

       python3 scripts/verify_table.py --hash <committed hash> \
           --server-seed <revealed> --client-seed <yours> \
           --game blackjack --nonce 7

The first thing it checks is that sha256(server seed) equals the hash you were
committed to. If that fails, nothing else matters -- the house swapped the seed.
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tables  # noqa: E402


def show_cards(label, cards):
    print(f"  {label:<10} {' '.join(cards)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server-seed", required=True, help="the revealed server seed")
    p.add_argument("--client-seed", required=True, help="your client seed for that hand")
    p.add_argument("--nonce", type=int, required=True, help="the hand number")
    p.add_argument("--game", required=True, choices=sorted(tables.TABLES),
                   help="which game the hand was")
    p.add_argument("--hash", help="the server seed hash you were shown BEFORE playing")
    p.add_argument("--bet", type=float, default=100.0, help="stake, for payout figures")
    p.add_argument("--raises", default="1,1,1",
                   help="Mississippi Stud only: the multiples you raised, e.g. 3,1,1")
    args = p.parse_args()

    if args.hash:
        actual = hashlib.sha256(args.server_seed.encode()).hexdigest()
        print(f"committed hash : {args.hash}")
        print(f"sha256(seed)   : {actual}")
        if actual != args.hash.strip().lower():
            print("\nCOMMITMENT BROKEN — this is not the seed you were promised.")
            return 1
        print("commitment     : holds\n")

    print(f"{tables.TABLES[args.game]['name']}, hand #{args.nonce}")
    if args.game == "fan_tan":
        # Fan-tan draws beads, not cards -- there is no shoe to show.
        deck = None
        print()
    else:
        deck = tables.deck_for(args.game, args.server_seed, args.client_seed, args.nonce)
        print(f"top of the shoe: {' '.join(deck[:8])} ...\n")

    if args.game == "baccarat":
        r = tables.baccarat_deal(deck)
        show_cards("player", r["player"])
        show_cards("banker", r["banker"])
        print(f"\n  player {r['player_points']}  banker {r['banker_points']}"
              f"  ->  {r['outcome'].upper()}{'  (natural)' if r['natural'] else ''}")
        for bt in tables.BACCARAT["bets"]:
            s = tables.baccarat_settle(r, bt, args.bet)
            print(f"    {bt:<7} {s['verdict']:<5} returns {s['payout']:.2f}")

    elif args.game == "fan_tan":
        d = tables.fan_tan_draw(args.server_seed, args.client_seed, args.nonce)
        print(f"  {d['beads']} beads, counted out in fours -> {d['result']} left over")
        for bt, spec in tables.FAN_TAN["bets"].items():
            picks = list(range(1, spec["picks"] + 1))
            s = tables.fan_tan_settle(d, bt, picks, args.bet)
            print(f"    {spec['label']:<8} on {picks}: {s['verdict']:<5} returns {s['payout']:.2f}")

    elif args.game == "blackjack":
        # Cards are fixed by the shuffle, so we replay the only line that needs
        # no choices; anything you actually did draws from these same positions.
        st = tables.blackjack_start(deck, args.bet)
        while st["stage"] == "player":
            tables.blackjack_act(st, deck, "stand")
        show_cards("dealer", st["dealer"])
        show_cards("you", st["hands"][0]["cards"])
        print(f"\n  dealer {tables.hand_value(st['dealer'])}"
              f"   you {tables.hand_value(st['hands'][0]['cards'])}"
              f"  ->  {st['hands'][0]['result']}")
        print("\n  Your first two cards and the dealer's two are the first four in the")
        print("  shoe above, in the order player-dealer-player-dealer. Every card you")
        print("  drew came off the top of that same shoe, in order.")

    else:
        multiples = [int(x) for x in args.raises.split(",")]
        st = tables.mississippi_start(deck, args.bet)
        for m in multiples:
            if st["stage"] != "playing":
                break
            tables.mississippi_act(st, deck, "raise", m)
        show_cards("your two", st["hole"])
        show_cards("board", st["community"])
        print(f"\n  {st.get('label')}  ->  wagered {st['wagered']:.0f}, returns {st['payout']:.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
