import json, urllib.request, urllib.error
B = "http://localhost:8000"
TOKEN = None

def call(path, data=None, method=None):
    url = B + path
    headers = {}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    if data is None:
        req = urllib.request.Request(url, headers=headers, method=method or "GET")
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers=headers, method=method or "POST")
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

CARD = "TEST123"

def _login():
    global TOKEN
    s, r = call("/api/scan", {"card_id": CARD})
    TOKEN = r.get("token")
    assert TOKEN, "scan did not return a token"
_login()
def bal():
    return call("/api/scan", {"card_id": CARD})[1]["player"]["reward_points"]

# Any hand left open by earlier testing still holds points and blocks new deals,
# so clear the table before asserting anything.
def _clear_table():
    for _ in range(8):
        s, a = call(f"/api/tables/active?card_id={CARD}")
        if not a.get("active"):
            return
        rid = a["active"]["round_id"]
        for act in ("fold", "stand", "hit"):
            s, r = call("/api/tables/action",
                        {"card_id": CARD, "round_id": rid, "action": act})
            if s == 200:
                break
_clear_table()

fails = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   <- {detail}"))
    if not cond: fails.append(name)

print("== games list ==")
s, g = call("/api/tables/games")
check("lists all seven games", len(g["games"]) == 7, [x["key"] for x in g["games"]])
check("no server seed leaked", "server_seed\"" not in json.dumps(g))

print("\n== baccarat ==")
for bt in ("player","banker","tie"):
    b0 = bal()
    s, r = call("/api/tables/deal", {"card_id":CARD,"game":"baccarat","bet":25,"bet_type":bt})
    check(f"{bt} deal ok", s==200, r)
    if s==200:
        check(f"{bt} balance moved by net", abs((bal()) - (b0 + r["net"])) < 0.01,
              f"{b0} + {r['net']} != {bal()}")
        check(f"{bt} totals 0-9", 0 <= r["player_points"] <= 9 and 0 <= r["banker_points"] <= 9, r)

print("\n== fan-tan ==")
for bt, picks in (("fan",[2]), ("kwok",[1,3]), ("nga",[1,2,4])):
    b0 = bal()
    s, r = call("/api/tables/deal", {"card_id":CARD,"game":"fan_tan","bet":25,"bet_type":bt,"picks":picks})
    check(f"{bt} deal ok", s==200, r)
    if s==200:
        check(f"{bt} result 1-4", r["result"] in (1,2,3,4), r)
        check(f"{bt} beads in range", 24 <= r["beads"] <= 119, r)
        check(f"{bt} balance moved by net", abs(bal() - (b0 + r["net"])) < 0.01)

s, r = call("/api/tables/deal", {"card_id":CARD,"game":"fan_tan","bet":25,"bet_type":"fan","picks":[1,2]})
check("wrong pick count rejected", s==400, r)
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"fan_tan","bet":25,"bet_type":"fan","picks":[9]})
check("out of range pick rejected", s==400, r)

print("\n== blackjack: play many hands to completion ==")
import random
results = {}
for i in range(40):
    b0 = bal()
    s, r = call("/api/tables/deal", {"card_id":CARD,"game":"blackjack","bet":25})
    if s != 200: check("bj deal", False, r); break
    if r["status"] == "active":
        check_hidden = r["dealer"][1] == "??"
        if not check_hidden: check("hole card hidden", False, r["dealer"]); break
        guard = 0
        while r["status"] == "active":
            guard += 1
            if guard > 20: check("bj terminates", False, r); break
            acts = r["actions"]
            a = "stand" if "stand" in acts and random.random() < 0.5 else acts[0]
            s, r = call("/api/tables/action", {"card_id":CARD,"round_id":r["round_id"],"action":a})
            if s != 200: check("bj action", False, r); break
    if r.get("status") == "settled":
        check_reveal = "??" not in r["dealer"]
        if not check_reveal: check("hole revealed at settle", False, r["dealer"]); break
        for h in r["hands"]: results[h["result"]] = results.get(h["result"],0)+1
        expect = b0 - r["wagered"] + r["payout"]
        if abs(bal() - expect) > 0.01:
            check("bj accounting", False, f"{b0} -{r['wagered']} +{r['payout']} != {bal()}"); break
check("40 blackjack hands all settled cleanly", True)
print("   outcomes:", results)

print("\n== blackjack: replay a finished hand is rejected ==")
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"blackjack","bet":25})
rid = r["round_id"]
while r["status"] == "active":
    s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"stand"})
s2, r2 = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"hit"})
check("finished hand rejects further action", s2==400, r2)

print("\n== mississippi ==")
b0 = bal()
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"mississippi","bet":25})
check("ms deal ok", s==200, r)
rid = r["round_id"]
check("two hole cards", len(r["hole"])==2, r)
check("no community yet", r["community"]==[], r)
for street in (3,4,5):
    s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"raise","multiple":1})
    check(f"street {street} raise ok", s==200, r)
check("settled after 5th", r["stage"]=="settled", r)
check("three community cards", len(r["community"])==3, r)
check("wagered = 4x ante", r["wagered"]==100, r)
check("ms accounting", abs(bal() - (b0 - 100 + r["payout"])) < 0.01)

s, r = call("/api/tables/deal", {"card_id":CARD,"game":"mississippi","bet":25})
rid = r["round_id"]
s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"fold"})
check("fold ends hand", r["stage"]=="folded", r)
check("fold shows board for audit", len(r["community"])==3, r)
check("fold forfeits only the ante", r["wagered"]==25 and r["payout"]==0, r)
s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"raise","multiple":1})
check("cannot act after folding", s==400, r)
s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"raise","multiple":7})
check("bad multiple rejected", s==400, r)

print("\n== guards ==")
s, r = call("/api/tables/deal", {"card_id":"NOSUCHCARD","game":"blackjack","bet":25})
# 403, not 404: a session may only act on its own card, and refusing before the
# lookup also avoids confirming whether a given card exists.
check("cannot act as another card", s==403, r)
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"roulette","bet":25})
check("unknown game 400", s==400, r)
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"blackjack","bet":1})
check("under table minimum rejected", s==400, r)
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"blackjack","bet":999999})
check("over table maximum rejected", s==400, r)
s, r = call("/api/tables/action", {"card_id":CARD,"round_id":999999,"action":"hit"})
check("unknown round 404", s==404, r)

print("\n== active-round recovery ==")
s, r = call("/api/tables/deal", {"card_id":CARD,"game":"blackjack","bet":25})
if r["status"] == "active":
    rid = r["round_id"]
    s, a = call(f"/api/tables/active?card_id={CARD}")
    check("active round found", a["active"] and a["active"]["round_id"]==rid, a)
    check("recovered hand still hides the hole", a["active"]["dealer"][1]=="??", a)
    while r["status"] == "active":
        s, r = call("/api/tables/action", {"card_id":CARD,"round_id":rid,"action":"stand"})
s, a = call(f"/api/tables/active?card_id={CARD}")
check("no active round once settled", a["active"] is None, a)

print("\n== history ==")
s, h = call("/api/players/1/table-history?limit=5")
check("table history returns rounds", len(h["rounds"])>0, h)
check("history carries seed context", all("server_seed_hash" in x for x in h["rounds"]), h["rounds"][:1])

print("\n" + ("ALL TABLE E2E CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
