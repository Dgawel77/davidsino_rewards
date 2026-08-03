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

// How many cards were on the felt at the last paint, so a hit animates one card
// arriving instead of re-dealing everything.
let lastBlackjackCounts = { dealer: 0, hands: [] };
let lastStudCounts = { hole: 0, community: 0 };

// ===== Sound and motion preferences =====
function refreshPrefButtons() {
    const s = document.getElementById('pref-sound');
    const mo = document.getElementById('pref-motion');
    // Auto mode plays basic strategy, which only exists for blackjack.
    const auto = document.getElementById('pref-auto');
    if (auto) {
        auto.classList.toggle('hidden', !currentTable || !AUTO_GAMES.has(currentTable.key));
        auto.classList.toggle('on', autoPlay);
        document.getElementById('pref-auto-label').textContent = autoPlay ? 'Auto on' : 'Auto';
    }
    if (s) {
        s.classList.toggle('on', soundOn());
        document.getElementById('pref-sound-label').textContent =
            soundOn() ? 'Sound on' : 'Sound off';
    }
    if (mo) {
        mo.classList.toggle('on', motionOn());
        document.getElementById('pref-motion-label').textContent =
            motionOn() ? 'Animation on' : 'Animation off';
    }
}

function toggleSound() {
    setSoundOn(!soundOn());
    refreshPrefButtons();
    if (soundOn()) SFX.chip();      // confirm it actually works
}

function toggleMotion() {
    setMotionOn(!motionOn());
    refreshPrefButtons();
}

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
        if (!data.active) {
            // Clear any stale round we were still holding, or a later guard
            // could think a finished hand is still live.
            activeRound = null;
            box.classList.add('hidden');
            return;
        }

        activeRound = data.active;
        const g = tableGames.find(x => x.key === data.active.game);
        document.getElementById('tables-resume-body').textContent =
            `${g ? g.name : data.active.game} — ${Math.round(data.active.wagered).toLocaleString()} points are still on the table.`;
        box.classList.remove('hidden');
    } catch (err) {
        box.classList.add('hidden');
    }
}

// Pull the open hand from the server and drop the player straight into it.
async function jumpToOpenHand() {
    await checkForOpenHand();
    if (!activeRound) return;
    const g = tableGames.find(x => x.key === activeRound.game);
    if (g) currentTable = g;
    resumeRound();
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
    stopAuto(null);
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
    // Auto mode belongs to one table; changing tables must not leave it running.
    if (!currentTable || !AUTO_GAMES.has(currentTable.key)) stopAuto(null);
    showView('tables-play-view');
    document.getElementById('table-title').textContent = currentTable.name;
    document.getElementById('table-tagline').textContent = currentTable.tagline;
    document.getElementById('table-result').textContent = '';
    document.getElementById('table-result').className = 'spin-result';
    document.getElementById('table-rules').classList.add('hidden');
    // Render the action grid greyed rather than empty, so the Deal button below
    // it sits at the same y before the first hand as it does during one.
    renderActions([]);
    document.getElementById('table-wager').classList.add('hidden');
    setBettingEnabled(true);
    document.getElementById('table-bet-label').textContent =
        currentTable.key === 'mississippi' ? 'Ante' : 'Bet per hand';

    lastBlackjackCounts = { dealer: 0, hands: [] };
    lastStudCounts = { hole: 0, community: 0 };

    updateTableBalance(tableBalance);
    updateTableBetDisplay();
    renderBetPicker();
    renderRules();
    renderIdleStage();
    refreshPrefButtons();
}

function updateTableBalance(points, delta) {
    tableBalance = points;
    document.getElementById('table-balance').textContent = Math.floor(points).toLocaleString();

    const d = document.getElementById('table-delta');
    if (d) {
        if (typeof delta === 'number' && delta !== 0) {
            d.textContent = (delta > 0 ? '+' : '−') + Math.abs(Math.round(delta)).toLocaleString();
            d.className = 'bank-delta ' + (delta > 0 ? 'up' : 'down');
        } else {
            d.textContent = '';
            d.className = 'bank-delta';
        }
    }

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
    const next = Math.max(0, Math.min(steps.length - 1, i + dir));

    // Chips stacking on the way up, a single chip lifted on the way down; a
    // flat tone at the ends so a dead press still tells you it registered.
    if (next === i) SFX.button();
    else if (dir > 0) { SFX.chip(); SFX.chip(0.07); }
    else SFX.chip();

    tableBet = steps[next];
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
    if (key !== tableBetType) SFX.chip();
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
    // The empty bead line stays in the layout so the pile arriving does not
    // shove the Deal button a hundred pixels down the page.
    stage.innerHTML = `
        <div class="hand-label">Pick ${need} number${need > 1 ? 's' : ''}</div>
        <div class="bead-line"></div>
        <div class="num-picker">
            ${[1, 2, 3, 4].map(n => `
                <div class="num-pick ${tablePicks.includes(n) ? 'selected' : ''}"
                     data-n="${n}" onclick="toggleFanPick(${n})">${n}</div>`).join('')}
        </div>
        <div class="table-note">Beads are counted out in fours; you're betting on what's left over.</div>`;
}

function toggleFanPick(n) {
    SFX.button();
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
// `seq` is this card's place in the deal order across the WHOLE table, not just
// its own row — otherwise the dealer's first card and the player's first card
// both land at once instead of alternating the way a real deal does.
// null means the card is already on the felt: paint it, don't animate it.
function cardHTML(card, seq, flip) {
    let cls = '';
    let style = '';
    if (flip) {
        cls = ' flip-anim';
    } else if (seq !== null && seq !== undefined) {
        cls = ' deal-anim';
        style = ` style="--i:${seq}"`;
    }

    if (!card || card === '??') {
        return `<div class="playing-card face-down${cls}"${style}>
                    <div class="pc-rank">?</div></div>`;
    }
    const rank = card.slice(0, -1);
    const suit = card.slice(-1);
    const red = RED_SUITS.includes(suit) ? ' red' : '';
    return `<div class="playing-card${red}${cls}"${style}>
                <div class="pc-rank">${rank}</div>
                <div class="pc-suit">${suit}</div>
            </div>`;
}

// `seqs` is one entry per card: a deal-order position, or null for "already there".
function cardsHTML(cards, seqs, flipIndex) {
    return cards.map((c, i) =>
        cardHTML(c, seqs ? seqs[i] : null, flipIndex === i)).join('');
}

// Convenience for the games that simply deal every card at once.
function seqAll(cards, animate) {
    return animate ? cards.map((_, i) => i) : cards.map(() => null);
}

// Trailing `n` cards are new; everything before them is already on the felt.
function seqTail(cards, n, start, animate) {
    const first = cards.length - n;
    return cards.map((_, i) => (animate && i >= first ? start + (i - first) : null));
}

function renderIdleStage() {
    const stage = document.getElementById('table-stage');
    if (currentTable.key === 'fan_tan') { renderNumberPicker(); return; }
    if (currentTable.key === 'baccarat') {
        stage.innerHTML = `
            <div class="hand-block">
                <div class="hand-label">Player<span class="hand-total">–</span></div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div>
            </div>
            <div class="hand-block">
                <div class="hand-label">Banker<span class="hand-total">–</span></div>
                <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div>
            </div>
            <div class="table-note"></div>`;
        return;
    }
    if (currentTable.key === 'blackjack') {
        // Deliberately the same markup a live hand uses, down to the empty
        // verdict line — an idle felt that is shorter than a dealt one would
        // move every button below it the moment you press Deal.
        const seat = (name, slots, cls) => `
            <div class="seat ${cls}">
                ${cls === 'you' ? '' : `<div class="seat-head">
                    <span class="seat-name">${name}</span>
                    <span class="seat-total">–</span></div>`}
                <div class="card-row">${'<div class="card-slot"></div>'.repeat(slots)}</div>
                ${cls === 'you' ? `<div class="seat-head">
                    <span class="seat-name">${name}</span>
                    <span class="seat-total">–</span></div>` : ''}
                <div class="seat-verdict"></div>
            </div>`;
        stage.innerHTML = seat('Dealer', 2, '') + seat('You', 2, 'you');
        return;
    }
    // Same skeleton the live hand renders, street dots and note included, so
    // dealing does not move the buttons.
    stage.innerHTML = `
        <div class="hand-block"><div class="hand-label">Your two</div>
            <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div></div></div>
        <div class="hand-block"><div class="hand-label">The board</div>
            <div class="card-row"><div class="card-slot"></div><div class="card-slot"></div><div class="card-slot"></div></div></div>
        <div class="street-dots">
            <div class="street-dot"></div><div class="street-dot"></div><div class="street-dot"></div>
        </div>
        <div class="table-note">Ante up, then raise or fold on each of three streets.</div>`;
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
    SFX.chip();                       // the bet going out

    // Every deal starts from an empty felt. This has to happen here rather than
    // only on entering the table: since a settled hand goes straight back to the
    // Deal button, the second hand would otherwise still be compared against the
    // first one's card counts, come out as "nothing new", and neither animate
    // nor make a sound.
    lastBlackjackCounts = { dealer: 0, hands: [] };
    lastStudCounts = { hole: 0, community: 0 };

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
            // 409 means a hand is already open — take them to it rather than
            // leaving them staring at an error they can't act on.
            if (resp.status === 409) await jumpToOpenHand();
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
    if (!btn) return;
    btn.disabled = !on;
    btn.innerHTML = on ? 'Deal<span class="btn-key">space</span>' : 'Dealing…';
}

// Betting controls stay on screen at all times and are greyed while a hand is
// live. Hiding them used to shift every button below by their height.
function setBettingEnabled(on) {
    setDealEnabled(on);
    document.querySelectorAll('#table-bet-controls .bet-adjust')
        .forEach(b => { b.disabled = !on; });
    document.querySelectorAll('#table-bet-picker .bet-option')
        .forEach(b => b.classList.toggle('disabled', !on));
}

// ===== Instant games: baccarat and fan-tan =====
function renderInstantResult(data) {
    const stage = document.getElementById('table-stage');
    const anim = motionOn();

    if (data.game === 'baccarat') {
        const winner = data.outcome;
        // Punto banco deals player, banker, player, banker, then any third
        // cards — player's first. Animate them in exactly that order.
        const bacSeq = { player: [], banker: [] };
        let seq = 0;
        for (let i = 0; i < 2; i++) {
            bacSeq.player.push(anim ? seq++ : null);
            bacSeq.banker.push(anim ? seq++ : null);
        }
        if (data.player.length > 2) bacSeq.player.push(anim ? seq++ : null);
        if (data.banker.length > 2) bacSeq.banker.push(anim ? seq++ : null);
        playDealSounds(seq || data.player.length + data.banker.length);
        stage.innerHTML = `
            <div class="hand-block">
                <div class="hand-label">Player<span class="hand-total">${data.player_points}</span></div>
                <div class="card-row">${cardsHTML(data.player, bacSeq.player)}</div>
            </div>
            <div class="hand-block">
                <div class="hand-label">Banker<span class="hand-total">${data.banker_points}</span></div>
                <div class="card-row">${cardsHTML(data.banker, bacSeq.banker)}</div>
            </div>
            <div class="table-note">${data.natural ? 'Natural — no third card drawn. ' : ''}
                ${winner === 'tie' ? 'A tie.' : `${winner === 'player' ? 'Player' : 'Banker'} wins with ${
                    winner === 'player' ? data.player_points : data.banker_points}.`}</div>`;
    } else {
        // Spread the whole pile over a fixed window so 24 beads and 119 beads
        // take about the same time to come up — long enough to watch, short
        // enough that auto mode isn't waiting on it.
        const step = (900 / Math.max(1, data.beads)).toFixed(1);
        const beads = Array.from({ length: data.beads }, (_, i) =>
            `<div class="bead${i >= data.beads - data.result ? ' final' : ''}${anim ? ' rise' : ''}"
                  style="--i:${i}"></div>`).join('');
        if (anim) playBeadSounds(data.beads);
        stage.innerHTML = `
            <div class="hand-label">${data.beads} beads — ${data.result} left over</div>
            <div class="bead-line" style="--bead-step:${step}ms">${beads}</div>
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
    updateTableBalance(tableBalance, net);
    setTimeout(() => {
        if (net > 0) SFX.win();
        else if (net === 0) SFX.push();
        else SFX.lose();
    }, motionOn() ? 420 : 120);
}

// ===== Live games: blackjack and Mississippi Stud =====
function renderRound(round) {
    activeRound = round;
    const live = round.status === 'active';

    // While a hand is live the only way to stake more is the action buttons, so
    // the deal controls stay out of the way. Once it settles, "Next hand" is the
    // single way forward — showing Deal as well was two buttons for one job.
    setBettingEnabled(!live);

    if (round.game === 'blackjack') renderBlackjack(round);
    else renderMississippi(round);

    const wager = document.getElementById('table-wager');
    wager.innerHTML = `On the table <span>${Math.round(round.wagered).toLocaleString()}</span>`;
    wager.classList.remove('hidden');

    if (live) {
        // Clear the "Dealing…" placeholder — the hand is dealt and it's your move.
        showTableResult('', '');
    } else {
        activeRound = null;
        const net = round.payout - round.wagered;
        if (net > 0) showTableResult(`+${Math.round(net).toLocaleString()} points`, 'win');
        else if (net === 0) showTableResult('Push — stake returned', '');
        else showTableResult(`−${Math.abs(Math.round(net)).toLocaleString()} points`, 'lose');
        updateTableBalance(tableBalance, net);
        playSettleSound(round, net);

        // Straight back to betting — no extra tap to clear the table. The last
        // hand stays on screen underneath until the next deal replaces it.
        setTimeout(() => {
            if (activeRound) return;                       // a new hand already began
            setBettingEnabled(true);
            renderActions([]);
        }, motionOn() ? 900 : 250);
    }
}

function playSettleSound(round, net) {
    const cardDelay = motionOn() ? 0.35 : 0.1;
    setTimeout(() => {
        if (round.game === 'blackjack' &&
            round.hands.some(h => h.result === 'blackjack')) return SFX.blackjack();
        if (round.game === 'blackjack' && round.hands.every(h => h.status === 'bust')) return SFX.bust();
        if (net > 0) SFX.win();
        else if (net === 0) SFX.push();
        else SFX.lose();
    }, cardDelay * 1000);
}

function renderBlackjack(round) {
    const stage = document.getElementById('table-stage');
    const live = round.stage === 'player';
    const anim = motionOn();

    const prev = lastBlackjackCounts;
    const dealerNew = Math.max(0, round.dealer.length - (prev.dealer || 0));
    const handNew = round.hands.map((h, i) => Math.max(0, h.cards.length - (prev.hands[i] || 0)));

    // The hole card turns over when the hand ends: same card count, but the
    // placeholder becomes a real card. That is a flip, not a deal.
    const holeFlip = anim && prev.dealerHidden && !round.dealer.includes('??') ? 1 : -1;

    let dealerSeq;
    let handSeqs;
    let dealtCount;

    const opening = dealerNew === 2 && handNew.length === 1 && handNew[0] === 2;
    if (opening) {
        // A real deal alternates: player, dealer, player, dealer.
        handSeqs = [anim ? [0, 2] : [null, null]];
        dealerSeq = anim ? [1, 3] : [null, null];
        dealtCount = 4;
    } else {
        // Any other paint is a draw: the player's new cards, then the dealer's.
        let seq = 0;
        handSeqs = round.hands.map((h, i) => {
            const s = seqTail(h.cards, handNew[i], seq, anim);
            seq += handNew[i];
            return s;
        });
        dealerSeq = seqTail(round.dealer, dealerNew, seq, anim);
        seq += dealerNew;
        dealtCount = seq;
    }

    const dealerTotal = round.dealer_total != null ? round.dealer_total : '';
    const dealerBust = typeof dealerTotal === 'number' && dealerTotal > 21;

    const seats = round.hands.map((h, i) => {
        const isTurn = live && i === round.active;
        const label = round.hands.length > 1 ? `Hand ${i + 1}` : 'You';
        const totalCls = [
            'seat-total',
            h.status === 'bust' ? 'bust' : (isTurn ? 'live' : ''),
            h.soft && h.total <= 21 ? 'soft' : '',
        ].filter(Boolean).join(' ');
        return `
            <div class="seat you ${isTurn ? 'turn' : ''}">
                <div class="card-row">${cardsHTML(h.cards, handSeqs[i])}</div>
                <div class="seat-head">
                    <span class="seat-name">${label}${isTurn ? ' — your move' : ''}</span>
                    <span class="${totalCls}" data-total>${h.total}</span>
                </div>
                <div class="seat-verdict ${h.result || ''}">${h.result ? blackjackVerdict(h) : ''}</div>
            </div>`;
    }).join('');

    stage.innerHTML = `
        <div class="seat">
            <div class="seat-head">
                <span class="seat-name">Dealer</span>
                <span class="seat-total ${dealerBust ? 'bust' : ''}" data-total>${dealerTotal}</span>
            </div>
            <div class="card-row">${cardsHTML(round.dealer, dealerSeq, holeFlip)}</div>
            <div class="seat-verdict">${!live && allBust(round)
                ? 'All hands busted — the dealer stood' : ''}</div>
        </div>
        ${seats}`;

    // Remember what is on the felt so the next paint knows what is new.
    lastBlackjackCounts = {
        dealer: round.dealer.length,
        hands: round.hands.map(h => h.cards.length),
        dealerHidden: round.dealer.includes('??'),
    };

    if (holeFlip >= 0) SFX.flip();
    playDealSounds(dealtCount);

    renderActions(round.actions);
}

// One papery skim per new card, spaced to match the visual stagger.
function playDealSounds(count) {
    if (!count) return;
    const gap = motionOn() ? 0.19 : 0.06;
    for (let i = 0; i < Math.min(count, 8); i++) SFX.deal(i * gap);
}

// Beads land far too fast to click one-for-one, so play a sparse run of chips
// across the same window and finish on the remainder.
function playBeadSounds(count) {
    const window = 0.9;
    const ticks = Math.min(10, count);
    for (let i = 0; i < ticks; i++) SFX.chip((i / ticks) * window);
    SFX.flip(window + 0.05);
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
    const anim = motionOn();

    const holeNew = Math.max(0, round.hole.length - lastStudCounts.hole);
    const communityNew = Math.max(0, round.community.length - lastStudCounts.community);
    lastStudCounts = { hole: round.hole.length, community: round.community.length };
    playDealSounds(holeNew + communityNew);

    stage.innerHTML = `
        <div class="hand-block">
            <div class="hand-label">Your two</div>
            <div class="card-row">${cardsHTML(round.hole, seqTail(round.hole, holeNew, 0, anim))}</div>
        </div>
        <div class="hand-block">
            <div class="hand-label">The board${round.label ? `<span class="hand-total">${round.label}</span>` : ''}</div>
            <div class="card-row">
                ${cardsHTML(round.community, seqTail(round.community, communityNew, holeNew, anim))}
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

    renderActions(live ? ['raise', 'fold'] : []);
}

// Every game gets a fixed grid of action slots. Buttons are enabled and
// disabled in place and never added or removed, so a button never moves out
// from under the pointer — put the mouse on Hit and it stays Hit.
const ACTION_SLOTS = {
    blackjack: [
        { action: 'hit',    label: 'Hit',    key: 'H', gold: true },
        { action: 'stand',  label: 'Stand',  key: 'S' },
        { action: 'double', label: 'Double', key: 'D' },
        { action: 'split',  label: 'Split',  key: 'P' },
    ],
    mississippi: [
        { action: 'raise', multiple: 1, label: '1× raise', key: '1' },
        { action: 'raise', multiple: 2, label: '2× raise', key: '2' },
        { action: 'raise', multiple: 3, label: '3× raise', key: '3', gold: true },
        { action: 'fold',  label: 'Fold', key: 'F' },
    ],
};

// `available` is the list of action names the server says are legal right now.
function renderActions(available) {
    const el = document.getElementById('table-actions');
    const slots = ACTION_SLOTS[currentTable && currentTable.key] || [];
    if (!slots.length) { el.innerHTML = ''; return; }

    const legal = new Set(available || []);
    el.innerHTML = slots.map(s => {
        const on = legal.has(s.action);
        return `
        <button class="btn ${s.gold ? 'btn-gold' : 'btn-secondary'}"
                data-action="${s.action}" data-multiple="${s.multiple || 0}"
                ${on ? '' : 'disabled'}
                onclick="tableAct('${s.action}', ${s.multiple || 0})">
            ${s.label}<span class="btn-key">${s.key}</span>
        </button>`;
    }).join('');
}

// The keyboard equivalent of the same grid.
function tableHotkey(e) {
    if (activeView !== 'tables-play-view') return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const key = e.key.toUpperCase();

    // Space or Enter deals, when dealing is what's on offer.
    if (e.key === ' ' || e.key === 'Enter') {
        const deal = document.getElementById('table-deal-btn');
        if (deal && !deal.disabled) { e.preventDefault(); deal.click(); }
        return;
    }

    const slots = ACTION_SLOTS[currentTable && currentTable.key] || [];
    const slot = slots.find(s => s.key === key);
    if (!slot) return;
    const btn = document.querySelector(
        `#table-actions .btn[data-action="${slot.action}"][data-multiple="${slot.multiple || 0}"]`);
    if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
}

document.addEventListener('keydown', tableHotkey);

// ===== Auto mode =====
// Plays the same hit-soft-17 basic strategy that scripts/bj_basic_strategy.py
// uses to measure the house edge, so what you watch here is the 0.64% line —
// not a guess. It only ever presses the buttons you could press yourself.
// Blackjack plays basic strategy. Baccarat and fan-tan involve no decisions at
// all once the bet is placed, so auto simply keeps dealing the bet you chose.
// Mississippi Stud is deliberately absent: playing it well needs a strategy
// chart, and the rough one used for the edge simulation gives away about three
// points of edge — not something to run unattended on someone's balance.
const AUTO_GAMES = new Set(['blackjack', 'baccarat', 'fan_tan']);

let autoPlay = false;
let autoTimer = null;

function upcardValue(card) {
    const r = card.slice(0, -1);
    if (r === 'A') return 11;
    return ['K', 'Q', 'J', '10'].includes(r) ? 10 : parseInt(r, 10);
}

function pairRank(card) {
    const r = card.slice(0, -1);
    if (r === 'A') return 'A';
    return ['K', 'Q', 'J', '10'].includes(r) ? '10' : r;
}

function basicStrategy(hand, up, legal) {
    const cards = hand.cards;
    const total = hand.total;
    const soft = hand.soft;
    const canDouble = legal.includes('double');
    const canSplit = legal.includes('split');

    if (canSplit) {
        const p = pairRank(cards[0]);
        if (p === 'A' || p === '8') return 'split';
        if (p === '9') return [2, 3, 4, 5, 6, 8, 9].includes(up) ? 'split' : 'stand';
        if (p === '10') return 'stand';
        if (p === '7') return up <= 7 ? 'split' : 'hit';
        if (p === '6') return up <= 6 ? 'split' : 'hit';
        if (p === '4') return [5, 6].includes(up) ? 'split' : 'hit';
        if (p === '2' || p === '3') return up <= 7 ? 'split' : 'hit';
        // A pair of fives is never split — it plays as a hard ten.
    }

    if (soft) {
        const kicker = total - 11;
        if (kicker >= 9) return 'stand';
        if (kicker === 8) return (canDouble && up === 6) ? 'double' : 'stand';   // H17
        if (kicker === 7) {
            if (canDouble && up >= 2 && up <= 6) return 'double';                // H17
            return [7, 8].includes(up) ? 'stand' : 'hit';
        }
        if (kicker === 6) return (canDouble && up >= 3 && up <= 6) ? 'double' : 'hit';
        if (kicker === 4 || kicker === 5) return (canDouble && up >= 4 && up <= 6) ? 'double' : 'hit';
        if (kicker === 2 || kicker === 3) return (canDouble && up >= 5 && up <= 6) ? 'double' : 'hit';
        return 'hit';
    }

    if (total >= 17) return 'stand';
    if (total >= 13) return up <= 6 ? 'stand' : 'hit';
    if (total === 12) return (up >= 4 && up <= 6) ? 'stand' : 'hit';
    if (total === 11) return canDouble ? 'double' : 'hit';                       // H17
    if (total === 10) return (canDouble && up <= 9) ? 'double' : 'hit';
    if (total === 9) return (canDouble && up >= 3 && up <= 6) ? 'double' : 'hit';
    return 'hit';
}

function toggleAuto() {
    autoPlay = !autoPlay;
    const btn = document.getElementById('pref-auto');
    if (btn) btn.classList.toggle('on', autoPlay);
    const label = document.getElementById('pref-auto-label');
    if (label) label.textContent = autoPlay ? 'Auto on' : 'Auto';
    SFX.button();
    if (autoPlay) scheduleAuto(400);
    else if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
}

function stopAuto(reason) {
    if (!autoPlay) return;
    autoPlay = false;
    const btn = document.getElementById('pref-auto');
    if (btn) btn.classList.remove('on');
    const label = document.getElementById('pref-auto-label');
    if (label) label.textContent = 'Auto';
    if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
    if (reason) showTableResult(reason, 'lose');
}

function scheduleAuto(delay) {
    if (autoTimer) clearTimeout(autoTimer);
    autoTimer = setTimeout(autoStep, delay);
}

function autoStep() {
    autoTimer = null;
    if (!autoPlay) return;
    // Anything that takes us off the table stops it.
    if (activeView !== 'tables-play-view' || !currentTable || !AUTO_GAMES.has(currentTable.key)) {
        return stopAuto(null);
    }
    if (tableBusy) return scheduleAuto(220);

    if (activeRound && activeRound.status === 'active' && currentTable.key === 'blackjack') {
        const legal = activeRound.actions || [];
        if (!legal.length) return scheduleAuto(260);
        const hand = activeRound.hands[activeRound.active];
        const up = upcardValue(activeRound.dealer[0]);
        let move = basicStrategy(hand, up, legal);
        if (!legal.includes(move)) move = legal.includes('hit') ? 'hit' : 'stand';
        tableAct(move, 0);
        return scheduleAuto(motionOn() ? 1100 : 500);
    }

    // Between hands: deal the next one, if it can still be afforded.
    if (tableBalance < tableBet) {
        return stopAuto('Auto stopped — not enough points for another hand');
    }
    const dealBtn = document.getElementById('table-deal-btn');
    if (dealBtn && dealBtn.disabled) return scheduleAuto(300);
    dealTable();
    // Fan-tan spends about a second pouring the pile out before it means anything.
    const settle = currentTable.key === 'fan_tan' ? 2200 : 1600;
    scheduleAuto(motionOn() ? settle : 700);
}

async function tableAct(action, multiple) {
    if (tableBusy || !activeRound) return;
    tableBusy = true;
    // Raising and doubling put more out; hit, stand and fold just acknowledge.
    if (action === 'double' || action === 'raise') SFX.chip();
    else SFX.button();
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
