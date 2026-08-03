// Davidsino Rewards - provably fair table games
let tableGames = [];
let currentTable = null;     // the game definition from /api/tables/games
let tableBet = 25;
let tableBetType = null;     // baccarat / fan-tan bet selection
let tablePicks = [];         // fan-tan numbers
let activeRound = null;      // live blackjack / Mississippi Stud round
let tableBalance = 0;
let tableBusy = false;

const TABLE_BET_STEPS = [25, 50, 100, 250, 500, 1000, 2500, 5000];
const RED_SUITS = ['♥', '♦'];

// ===== Lobby =====
async function showTablesLobby() {
    showView('tables-lobby-view');
    syncCardInputs();
    const input = document.getElementById('tables-card-id');
    if (input && currentCardId) input.value = currentCardId;

    await loadTableGames();
    if (currentCardId) {
        tableBalance = currentPlayer ? currentPlayer.reward_points : 0;
        checkForOpenHand();
    }
}

async function loadTableGames() {
    const el = document.getElementById('table-list');
    try {
        const resp = await fetch(`${API_BASE}/api/tables/games`);
        const data = await resp.json();
        tableGames = data.games || [];

        el.innerHTML = tableGames.map(g => `
            <div class="machine-item" onclick="openTable('${g.key}')">
                <div class="machine-name">${g.name}</div>
                <div class="machine-tagline">${g.tagline}</div>
                <div class="machine-bets">${g.min_bet.toLocaleString()} – ${g.max_bet.toLocaleString()} pts
                    ${g.key === 'mississippi' ? 'ante' : 'per hand'}</div>
            </div>
        `).join('');
    } catch (err) {
        el.innerHTML = '<div style="color:var(--marker);">Failed to load the tables</div>';
    }
}

async function loadTablePlayer(cardId) {
    if (!cardId) return false;
    try {
        const resp = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId })
        });
        const data = await resp.json();
        if (!data.registered) return false;
        setSession(data.player, cardId);
        tableBalance = data.player.reward_points;
        checkForOpenHand();
        return true;
    } catch (err) {
        return false;
    }
}

// A hand you walked away from is still yours — and still holding your points.
async function checkForOpenHand() {
    const box = document.getElementById('tables-resume');
    if (!currentCardId) { box.classList.add('hidden'); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/tables/active?card_id=${encodeURIComponent(currentCardId)}`);
        const data = await resp.json();
        if (!data.active) { box.classList.add('hidden'); return; }

        activeRound = data.active;
        const g = tableGames.find(x => x.key === data.active.game);
        document.getElementById('tables-resume-body').textContent =
            `${g ? g.name : data.active.game} — ${Math.round(data.active.wagered).toLocaleString()} points are still on the table.`;
        box.classList.remove('hidden');
    } catch (err) {
        box.classList.add('hidden');
    }
}

function resumeRound() {
    if (!activeRound) return;
    const g = tableGames.find(x => x.key === activeRound.game);
    if (!g) return;
    currentTable = g;
    enterTableView();
    renderRound(activeRound);
}

function tablesBack() {
    showTablesLobby();
}

// ===== Entering a table =====
async function openTable(key) {
    if (!currentCardId) { toggleIdentityMenu(); return; }
    const found = await loadTablePlayer(currentCardId);
    if (!found) { alert('That card is not on file — see the dealer'); return; }

    currentTable = tableGames.find(g => g.key === key);
    if (!currentTable) return;

    if (activeRound && activeRound.status === 'active') {
        // Walking away from a live hand would strand the points sitting on it.
        if (activeRound.game !== key) {
            alert('Finish your open hand first — it still has points on it.');
            currentTable = tableGames.find(g => g.key === activeRound.game) || currentTable;
        }
        resumeRound();
        return;
    }

    tableBet = currentTable.min_bet;
    tableBetType = currentTable.bets ? Object.keys(currentTable.bets)[0] : null;
    tablePicks = key === 'fan_tan' ? [1] : [];
    activeRound = null;
    enterTableView();
}

function enterTableView() {
    showView('tables-play-view');
    document.getElementById('table-title').textContent = currentTable.name;
    document.getElementById('table-tagline').textContent = currentTable.tagline;
    document.getElementById('table-result').textContent = '';
    document.getElementById('table-result').className = 'spin-result';
    document.getElementById('table-rules').classList.add('hidden');
    document.getElementById('table-actions').innerHTML = '';
    document.getElementById('table-wager').classList.add('hidden');
    document.getElementById('table-bet-controls').classList.remove('hidden');
    document.getElementById('table-bet-label').textContent =
        currentTable.key === 'mississippi' ? 'Ante' : 'Bet per hand';

    updateTableBalance(tableBalance);
    updateTableBetDisplay();
    renderBetPicker();
    renderRules();
    renderIdleStage();
}

function updateTableBalance(points) {
    tableBalance = points;
    document.getElementById('table-balance').textContent = Math.floor(points).toLocaleString();
    const hp = document.getElementById('head-points');
    if (hp) hp.textContent = Math.floor(points).toLocaleString();
    if (currentPlayer) currentPlayer.reward_points = points;
}

// ===== Bet controls =====
function adjustTableBet(dir) {
    const steps = TABLE_BET_STEPS.filter(
        s => s >= currentTable.min_bet && s <= currentTable.max_bet);
    if (!steps.length) return;
    let i = steps.indexOf(tableBet);
    if (i === -1) i = 0;
    i = Math.max(0, Math.min(steps.length - 1, i + dir));
    tableBet = steps[i];
    updateTableBetDisplay();
}

function updateTableBetDisplay() {
    document.getElementById('table-bet-amount').textContent = tableBet.toLocaleString();
}

function renderBetPicker() {
    const el = document.getElementById('table-bet-picker');
    if (!currentTable.bets) { el.innerHTML = ''; return; }

    el.innerHTML = Object.entries(currentTable.bets).map(([key, spec]) => {
        const pays = currentTable.key === 'baccarat'
            ? `${spec.pays === 0.95 ? '0.95' : spec.pays} : 1`
            : (spec.pays >= 1 ? `${spec.pays} : 1` : `1 : ${Math.round(1 / spec.pays)}`);
        return `
            <div class="bet-option ${key === tableBetType ? 'selected' : ''}"
                 data-bet="${key}" onclick="selectTableBet('${key}')">
                <div class="bet-option-label">${spec.label}</div>
                <div class="bet-option-pays">${pays}</div>
                ${spec.help ? `<div class="bet-option-help">${spec.help}</div>` : ''}
                <div class="bet-option-edge">house ${spec.edge}</div>
            </div>`;
    }).join('');

    renderNumberPicker();
}

function selectTableBet(key) {
    tableBetType = key;
    document.querySelectorAll('.bet-option').forEach(el => {
        el.classList.toggle('selected', el.dataset.bet === key);
    });
    if (currentTable.key === 'fan_tan') {
        const need = currentTable.bets[key].picks;
        tablePicks = tablePicks.slice(0, need);
        while (tablePicks.length < need) {
            for (let n = 1; n <= 4 && tablePicks.length < need; n++) {
                if (!tablePicks.includes(n)) tablePicks.push(n);
            }
        }
        renderNumberPicker();
    }
}

function renderNumberPicker() {
    const stage = document.getElementById('table-stage');
    if (currentTable.key !== 'fan_tan') return;
    const need = currentTable.bets[tableBetType].picks;
    stage.innerHTML = `
        <div class="hand-label">Pick ${need} number${need > 1 ? 's' : ''}</div>
        <div class="num-picker">
            ${[1, 2, 3, 4].map(n => `
                <div class="num-pick ${tablePicks.includes(n) ? 'selected' : ''}"
                     data-n="${n}" onclick="toggleFanPick(${n})">${n}</div>`).join('')}
        </div>
        <div class="table-note">Beads are counted out in fours; you're betting on what's left over.</div>`;
}

function toggleFanPick(n) {
    const need = currentTable.bets[tableBetType].picks;
    if (tablePicks.includes(n)) {
        if (tablePicks.length <= need) return;   // keep the bet complete
        tablePicks = tablePicks.filter(p => p !== n);
    } else {
        tablePicks.push(n);
        while (tablePicks.length > need) tablePicks.shift();
    }
    tablePicks.sort();
    renderNumberPicker();
}

// ===== Rendering the felt =====
function cardHTML(card, dealt) {
    if (!card || card === '??') {
        return `<div class="playing-card face-down${dealt ? ' dealt' : ''}">
                    <div class="pc-rank">?</div></div>`;
    }
    const rank = card.slice(0, -1);
    const suit = card.slice(-1);
    const red = RED_SUITS.includes(suit) ? ' red' : '';
    return `<div class="playing-card${red}${dealt ? ' dealt' : ''}">
                <div class="pc-rank">${rank}</div>
                <div class="pc-suit">${suit}</div>
            </div>`;
}

function cardsHTML(cards, dealt) {
    return cards.map(c => cardHTML(c, dealt)).join('');
}

function renderIdleStage() {
    const stage = document.getElementById('table-stage');
    if (currentTable.key === 'fan_tan') { renderNumberPicker(); return; }
    if (currentTable.key === 'baccarat') {
        stage.innerHTML = `
            <div class="hand-block"><div class="hand-label">Player</div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>
            <div class="hand-block"><div class="hand-label">Banker</div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>`;
        return;
    }
    if (currentTable.key === 'blackjack') {
        stage.innerHTML = `
            <div class="hand-block"><div class="hand-label">Dealer</div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>
            <div class="hand-block"><div class="hand-label">You</div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>`;
        return;
    }
    stage.innerHTML = `
        <div class="hand-block"><div class="hand-label">Your two</div>
            <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>
        <div class="hand-block"><div class="hand-label">The board</div>
            <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div><div class="card-slot"></div></div></div>`;
}

// ===== Dealing =====
async function dealTable() {
    if (tableBusy) return;
    if (!currentCardId) { toggleIdentityMenu(); return; }

    const need = currentTable.key === 'mississippi' ? tableBet * 4 : tableBet;
    if (tableBalance < need) {
        return showTableResult(
            currentTable.key === 'mississippi'
                ? `Mississippi needs ${need.toLocaleString()} on hand — the ante plus a raise on every street.`
                : 'Not enough points for that bet',
            'lose');
    }

    tableBusy = true;
    setDealEnabled(false);
    showTableResult('Dealing…', '');

    const body = { card_id: currentCardId, game: currentTable.key, bet: tableBet };
    if (tableBetType) body.bet_type = tableBetType;
    if (currentTable.key === 'fan_tan') body.picks = tablePicks;

    try {
        const resp = await fetch(`${API_BASE}/api/tables/deal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (!resp.ok) {
            showTableResult(data.detail || 'That hand was refused', 'lose');
            return;
        }
        updateTableBalance(data.reward_points);
        if (data.settled) renderInstantResult(data);
        else { activeRound = data; renderRound(data); }
    } catch (err) {
        showTableResult('Connection error', 'lose');
    } finally {
        tableBusy = false;
        setDealEnabled(true);
    }
}

function setDealEnabled(on) {
    const btn = document.getElementById('table-deal-btn');
    if (btn) { btn.disabled = !on; btn.textContent = on ? 'Deal' : 'Dealing…'; }
}

// ===== Instant games: baccarat and fan-tan =====
function renderInstantResult(data) {
    const stage = document.getElementById('table-stage');

    if (data.game === 'baccarat') {
        const winner = data.outcome;
        stage.innerHTML = `
            <div class="hand-block">
                <div class="hand-label">Player<span class="hand-total">${data.player_points}</span></div>
                <div class="card-row">${cardsHTML(data.player, true)}</div>
            </div>
            <div class="hand-block">
                <div class="hand-label">Banker<span class="hand-total">${data.banker_points}</span></div>
                <div class="card-row">${cardsHTML(data.banker, true)}</div>
            </div>
            <div class="table-note">${data.natural ? 'Natural — no third card drawn. ' : ''}
                ${winner === 'tie' ? 'A tie.' : `${winner === 'player' ? 'Player' : 'Banker'} wins with ${
                    winner === 'player' ? data.player_points : data.banker_points}.`}</div>`;
    } else {
        const beads = Array.from({ length: data.beads }, (_, i) =>
            `<div class="bead${i >= data.beads - data.result ? ' final' : ''}"></div>`).join('');
        stage.innerHTML = `
            <div class="hand-label">${data.beads} beads — ${data.result} left over</div>
            <div class="bead-line">${beads}</div>
            <div class="num-picker">
                ${[1, 2, 3, 4].map(n => `
                    <div class="num-pick ${n === data.result ? 'hit' : (tablePicks.includes(n) ? 'selected' : '')}"
                         onclick="toggleFanPick(${n})">${n}</div>`).join('')}
            </div>
            <div class="table-note">The last ${data.result} bead${data.result > 1 ? 's' : ''} are the ones that count.</div>`;
    }

    const net = data.net;
    if (data.verdict === 'win') showTableResult(`+${Math.round(net).toLocaleString()} points`, 'win');
    else if (data.verdict === 'push') showTableResult('Push — stake returned', '');
    else showTableResult(`−${Math.abs(Math.round(net)).toLocaleString()} points`, 'lose');
}

// ===== Live games: blackjack and Mississippi Stud =====
function renderRound(round) {
    activeRound = round;
    const live = round.status === 'active';
    document.getElementById('table-bet-controls').classList.toggle('hidden', live);

    if (round.game === 'blackjack') renderBlackjack(round);
    else renderMississippi(round);

    const wager = document.getElementById('table-wager');
    wager.innerHTML = `On the table <span>${Math.round(round.wagered).toLocaleString()}</span>`;
    wager.classList.remove('hidden');

    if (!live) {
        activeRound = null;
        const net = round.payout - round.wagered;
        if (net > 0) showTableResult(`+${Math.round(net).toLocaleString()} points`, 'win');
        else if (net === 0) showTableResult('Push — stake returned', '');
        else showTableResult(`−${Math.abs(Math.round(net)).toLocaleString()} points`, 'lose');
    }
}

function renderBlackjack(round) {
    const stage = document.getElementById('table-stage');
    const live = round.stage === 'player';

    const dealerTotal = round.dealer_total != null ? round.dealer_total : '';
    const hands = round.hands.map((h, i) => {
        const isActive = live && i === round.active;
        const verdict = h.result
            ? `<div class="hand-verdict ${h.result}">${blackjackVerdict(h)}</div>` : '';
        const label = round.hands.length > 1 ? `Hand ${i + 1}` : 'You';
        return `
            <div class="hand-block ${isActive ? 'active' : ''}">
                <div class="hand-label">${label}${isActive ? ' — your move' : ''}
                    <span class="hand-total">${h.total}${h.soft ? ' soft' : ''}</span></div>
                <div class="card-row">${cardsHTML(h.cards, true)}</div>
                ${verdict}
            </div>`;
    }).join('');

    stage.innerHTML = `
        <div class="hand-block">
            <div class="hand-label">Dealer<span class="hand-total">${dealerTotal}</span></div>
            <div class="card-row">${cardsHTML(round.dealer, true)}</div>
        </div>
        ${hands}
        ${!live && allBust(round) ? '<div class="table-note">Every hand busted, so the dealer had nothing left to beat and stood.</div>' : ''}`;

    renderActions(round.actions.map(a => ({
        action: a,
        label: { hit: 'Hit', stand: 'Stand', double: 'Double', split: 'Split' }[a] || a,
        gold: a === 'hit',
    })));
}

function allBust(round) {
    return round.hands.every(h => h.status === 'bust');
}

function blackjackVerdict(h) {
    return { blackjack: 'Blackjack — paid 3:2', win: 'Win', lose: 'Lost',
             push: 'Push', bust: 'Bust' }[h.result] || h.result;
}

function renderMississippi(round) {
    const stage = document.getElementById('table-stage');
    const live = round.stage === 'playing';
    const slots = 3 - round.community.length;

    stage.innerHTML = `
        <div class="hand-block">
            <div class="hand-label">Your two</div>
            <div class="card-row">${cardsHTML(round.hole, true)}</div>
        </div>
        <div class="hand-block">
            <div class="hand-label">The board${round.label ? `<span class="hand-total">${round.label}</span>` : ''}</div>
            <div class="card-row">
                ${cardsHTML(round.community, true)}
                ${'<div class="card-slot"></div>'.repeat(Math.max(0, slots))}
            </div>
        </div>
        <div class="street-dots">
            ${[3, 4, 5].map(s => {
                const done = round.community.length >= s - 2;
                const now = live && round.street === s;
                return `<div class="street-dot ${done ? 'done' : ''} ${now ? 'now' : ''}"></div>`;
            }).join('')}
        </div>
        <div class="table-note">${live
            ? `${round.street}${round.street === 3 ? 'rd' : 'th'} street — raise or fold.`
            : (round.stage === 'folded' ? 'Folded. The rest of the board is shown so you can still check the deal.'
                                        : round.label)}</div>`;

    if (live) {
        renderActions([
            { action: 'raise', multiple: 1, label: '1× raise' },
            { action: 'raise', multiple: 2, label: '2× raise' },
            { action: 'raise', multiple: 3, label: '3× raise', gold: true },
            { action: 'fold', label: 'Fold' },
        ]);
    } else {
        renderActions([]);
    }
}

function renderActions(actions) {
    const el = document.getElementById('table-actions');
    if (!actions.length) {
        el.innerHTML = `<button class="btn btn-gold" style="flex:1 1 100%;"
                            onclick="nextHand()">Next hand</button>`;
        return;
    }
    el.innerHTML = actions.map(a => `
        <button class="btn btn-small ${a.gold ? 'btn-gold' : 'btn-secondary'}"
                onclick="tableAct('${a.action}', ${a.multiple || 0})">${a.label}</button>`).join('');
}

function nextHand() {
    activeRound = null;
    enterTableView();
}

async function tableAct(action, multiple) {
    if (tableBusy || !activeRound) return;
    tableBusy = true;
    document.querySelectorAll('#table-actions .btn').forEach(b => b.disabled = true);

    try {
        const resp = await fetch(`${API_BASE}/api/tables/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                card_id: currentCardId,
                round_id: activeRound.round_id,
                action: action,
                multiple: multiple || null,
            })
        });
        const data = await resp.json();
        if (!resp.ok) {
            showTableResult(data.detail || 'That move was refused', 'lose');
            document.querySelectorAll('#table-actions .btn').forEach(b => b.disabled = false);
            return;
        }
        updateTableBalance(data.reward_points);
        renderRound(data);
    } catch (err) {
        showTableResult('Connection error', 'lose');
        document.querySelectorAll('#table-actions .btn').forEach(b => b.disabled = false);
    } finally {
        tableBusy = false;
    }
}

function showTableResult(text, cls) {
    const el = document.getElementById('table-result');
    el.textContent = text;
    el.className = 'spin-result' + (cls ? ' ' + cls : '');
}

// ===== Rules =====
function renderRules() {
    const el = document.getElementById('table-rules');
    let html = `<ul class="rules-list">${currentTable.rules.map(r => `<li>${r}</li>`).join('')}</ul>`;

    if (currentTable.paytable) {
        html += '<div class="paytable-row" style="border:none; padding-top:14px;"><span>Hand</span><span>Pays</span></div>';
        html += currentTable.paytable.map(([label, mult]) => {
            const pays = mult > 0 ? `${mult} : 1` : (mult === 0 ? 'push' : 'loses');
            return `<div class="paytable-row"><span>${label}</span><span>${pays}</span></div>`;
        }).join('');
        html += '<div class="paytable-note">Payouts apply to everything wagered — ante and raises together.</div>';
    }
    el.innerHTML = html;
}

function toggleTableRules() {
    document.getElementById('table-rules').classList.toggle('hidden');
}
