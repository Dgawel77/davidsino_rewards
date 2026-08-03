// Davidsino Rewards - provably fair slots
let slotMachines = [];
let currentMachine = null;
let currentBet = 0;
let slotCardId = null;
let slotBalance = 0;
let spinning = false;

const BET_STEPS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000];
const FALLBACK_SYMBOLS = ['🍒', '🍋', '🔔', '⭐', '💎', '7️⃣', '🎰'];

// Symbols used only for the spin animation. The server decides the real result.
const REEL_SYMBOLS = {
    classic: ['🍒', '🍋', '🔔', '⭐', '💎', '7️⃣', '🎰'],
    diamond_dave: ['➖', '🍀', '💰', '💎', '7️⃣', '🎰'],
    vig_city: ['🍒', '🍋', '🍇', '🔔', '⭐', '💎', '7️⃣', '🃏'],
};

// ===== Lobby =====
async function showSlotsLobby() {
    showView('slots-lobby-view');
    syncCardInputs();
    await loadMachines();
    // The card you scanned at the door is the card you play on. No second login.
    const cardId = currentCardId || document.getElementById('slots-card-id').value.trim();
    if (cardId) loadSlotPlayer(cardId);
    else document.getElementById('slots-player-banner').classList.add('hidden');
}

async function loadMachines() {
    const el = document.getElementById('machine-list');
    try {
        const resp = await fetch(`${API_BASE}/api/slots/machines`);
        const data = await resp.json();
        slotMachines = data.machines || [];

        el.innerHTML = slotMachines.map(m => `
            <div class="machine-item" onclick="openMachine('${m.key}')">
                <div class="machine-name">${m.name}</div>
                <div class="machine-tagline">${m.tagline}</div>
                <div class="machine-bets">${m.min_bet.toLocaleString()} – ${m.max_bet.toLocaleString()} pts per spin</div>
            </div>
        `).join('');
    } catch (err) {
        el.innerHTML = '<div style="color:var(--marker);">Failed to load machines</div>';
    }
}

async function loadSlotPlayer(cardId) {
    try {
        const resp = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId })
        });
        const data = await resp.json();
        const banner = document.getElementById('slots-player-banner');
        if (data.registered) {
            slotCardId = cardId;
            slotBalance = data.player.reward_points;
            document.getElementById('slots-player-name').textContent = data.player.name;
            document.getElementById('slots-player-points').textContent = Math.floor(slotBalance).toLocaleString();
            banner.classList.remove('hidden');
            setSession(data.player, cardId, data.token);
            return true;
        }
        banner.classList.add('hidden');
        return false;
    } catch (err) {
        return false;
    }
}

// ===== Machine view =====
async function openMachine(key) {
    const cardId = currentCardId || document.getElementById('slots-card-id').value.trim();
    if (!cardId) {
        // No card in hand — send them to the one place that takes one.
        toggleIdentityMenu();
        return;
    }
    const found = await loadSlotPlayer(cardId);
    if (!found) {
        alert('That card is not on file — see the dealer');
        return;
    }

    currentMachine = slotMachines.find(m => m.key === key);
    currentBet = currentMachine.min_bet;

    showView('slots-play-view');
    document.getElementById('machine-title').textContent = currentMachine.name;
    document.getElementById('machine-tagline').textContent = currentMachine.tagline;
    document.getElementById('spin-result').textContent = '';
    document.getElementById('spin-result').className = 'spin-result';
    document.getElementById('paytable').classList.add('hidden');
    updateBetDisplay();
    updateBalance(slotBalance);
    renderPaytable();
    renderIdleGrid();
    loadSeedInfo();
}

function renderIdleGrid() {
    const rows = currentMachine.kind === 'reel3' ? 1 : 3;
    const cols = currentMachine.kind === 'reel3' ? 3 : 5;
    const pool = REEL_SYMBOLS[currentMachine.key] || FALLBACK_SYMBOLS;
    const grid = [];
    for (let r = 0; r < rows; r++) {
        const row = [];
        for (let c = 0; c < cols; c++) row.push(pool[(r * cols + c) % pool.length]);
        grid.push(row);
    }
    drawGrid(grid);
}

function drawGrid(grid, winCells) {
    const area = document.getElementById('reel-area');
    const cols = grid[0].length;
    let html = '';
    for (let c = 0; c < cols; c++) {
        html += '<div class="reel-col" data-col="' + c + '">';
        for (let r = 0; r < grid.length; r++) {
            const isWin = winCells && winCells.has(`${r},${c}`);
            html += `<div class="reel-cell${isWin ? ' win-cell' : ''}" data-r="${r}" data-c="${c}">${grid[r][c]}</div>`;
        }
        html += '</div>';
    }
    area.innerHTML = html;
}

function updateBalance(points) {
    slotBalance = points;
    document.getElementById('slot-balance').textContent = Math.floor(points).toLocaleString();
    // Keep the header in step so the standing is never stale after a spin.
    const headerPoints = document.getElementById('head-points');
    if (headerPoints) headerPoints.textContent = Math.floor(points).toLocaleString();
}

function updateBetDisplay() {
    document.getElementById('bet-amount').textContent = currentBet.toLocaleString();
}

function adjustBet(direction) {
    if (spinning) return;
    const steps = BET_STEPS.filter(s => s >= currentMachine.min_bet && s <= currentMachine.max_bet);
    if (!steps.includes(currentMachine.min_bet)) steps.unshift(currentMachine.min_bet);
    if (!steps.includes(currentMachine.max_bet)) steps.push(currentMachine.max_bet);

    let idx = steps.indexOf(currentBet);
    if (idx === -1) idx = 0;
    idx = Math.min(steps.length - 1, Math.max(0, idx + direction));
    currentBet = steps[idx];
    updateBetDisplay();
}

function maxBet() {
    if (spinning) return;
    currentBet = Math.min(currentMachine.max_bet, Math.floor(slotBalance) || currentMachine.min_bet);
    if (currentBet < currentMachine.min_bet) currentBet = currentMachine.min_bet;
    updateBetDisplay();
}

// ===== Spin =====
async function doSpin() {
    if (spinning || !currentMachine) return;
    const resultEl = document.getElementById('spin-result');

    if (slotBalance < currentBet) {
        resultEl.textContent = `Not enough points — need ${currentBet.toLocaleString()}`;
        resultEl.className = 'spin-result lose';
        return;
    }

    spinning = true;
    const btn = document.getElementById('spin-btn');
    btn.disabled = true;
    btn.textContent = 'Spinning…';
    resultEl.textContent = '';
    resultEl.className = 'spin-result';

    const shuffle = startShuffle();

    try {
        const resp = await api(`/api/slots/spin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: slotCardId, machine: currentMachine.key, bet: currentBet })
        });
        const data = await resp.json();

        // Let the reels blur briefly even on a fast response — a slot that
        // resolves instantly doesn't feel like a slot.
        await sleep(700);
        clearInterval(shuffle);

        if (!resp.ok) {
            renderIdleGrid();
            resultEl.textContent = data.detail || 'Spin failed';
            resultEl.className = 'spin-result lose';
            return;
        }

        await settleReels(data.grid);
        showSpinOutcome(data);
        updateBalance(data.reward_points);
        document.getElementById('seed-nonce').textContent = data.next_nonce;
    } catch (err) {
        clearInterval(shuffle);
        renderIdleGrid();
        resultEl.textContent = 'Connection error';
        resultEl.className = 'spin-result lose';
    } finally {
        spinning = false;
        btn.disabled = false;
        btn.textContent = 'Spin';
    }
}

function startShuffle() {
    const pool = REEL_SYMBOLS[currentMachine.key] || FALLBACK_SYMBOLS;
    document.querySelectorAll('.reel-cell').forEach(c => c.classList.add('spinning'));
    return setInterval(() => {
        document.querySelectorAll('.reel-cell').forEach(cell => {
            cell.textContent = pool[Math.floor(Math.random() * pool.length)];
        });
    }, 70);
}

async function settleReels(grid) {
    const cols = grid[0].length;
    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < grid.length; r++) {
            const cell = document.querySelector(`.reel-cell[data-r="${r}"][data-c="${c}"]`);
            if (cell) {
                cell.textContent = grid[r][c];
                cell.classList.remove('spinning');
            }
        }
        await sleep(160);
    }
}

function showSpinOutcome(data) {
    const resultEl = document.getElementById('spin-result');
    if (data.win > 0) {
        resultEl.textContent = `${data.detail} — won ${Math.round(data.win).toLocaleString()} pts`;
        resultEl.className = 'spin-result win';
        highlightWins(data);
    } else {
        resultEl.textContent = `No win · −${currentBet.toLocaleString()} pts`;
        resultEl.className = 'spin-result lose';
    }
}

function highlightWins(data) {
    const cells = new Set();
    if (data.lines && data.lines.length) {
        // 5 fixed paylines, mirroring PAYLINES_5X3 on the server
        const PAYLINES = [
            [0, 0, 0, 0, 0], [1, 1, 1, 1, 1], [2, 2, 2, 2, 2],
            [0, 1, 2, 1, 0], [2, 1, 0, 1, 2],
        ];
        data.lines.forEach(hit => {
            const line = PAYLINES[hit.line];
            for (let c = 0; c < hit.count; c++) cells.add(`${line[c]},${c}`);
        });
    } else {
        // 3-reel: light only the repeated symbol, so a pair doesn't look like a triple
        const row = data.grid[0];
        const counts = {};
        row.forEach(s => { counts[s] = (counts[s] || 0) + 1; });
        const winner = Object.keys(counts).find(s => counts[s] >= 2);
        row.forEach((s, c) => { if (s === winner) cells.add(`0,${c}`); });
    }
    cells.forEach(key => {
        const [r, c] = key.split(',');
        const cell = document.querySelector(`.reel-cell[data-r="${r}"][data-c="${c}"]`);
        if (cell) cell.classList.add('win-cell');
    });
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ===== Paytable =====
function renderPaytable() {
    const el = document.getElementById('paytable');
    const rows = currentMachine.paytable.map(([combo, pay]) => `
        <div class="paytable-row"><span>${combo}</span><span style="color:var(--brass);">${pay}</span></div>
    `).join('');
    el.innerHTML = rows +
        (currentMachine.paytable_note ? `<div class="paytable-note">${currentMachine.paytable_note}</div>` : '');
}

function togglePaytable() {
    document.getElementById('paytable').classList.toggle('hidden');
}

// ===== Provable fairness =====
async function loadSeedInfo() {
    try {
        const resp = await api(`/api/slots/seed?card_id=${encodeURIComponent(slotCardId)}`);
        const data = await resp.json();
        if (!resp.ok) return;
        document.getElementById('seed-hash').textContent = data.seed.server_seed_hash;
        document.getElementById('seed-client').textContent = data.seed.client_seed;
        document.getElementById('seed-nonce').textContent = data.seed.nonce;
    } catch (err) {
        /* fairness panel just stays empty */
    }
}

function toggleFairness() {
    document.getElementById('fairness-panel').classList.toggle('hidden');
}

async function rotateSeed() {
    const custom = document.getElementById('new-client-seed').value.trim();
    if (!confirm('Rotate seeds? This reveals the current server seed so you can verify every spin you just made.')) return;

    try {
        const resp = await api(`/api/slots/seed/rotate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: slotCardId, client_seed: custom || null })
        });
        const data = await resp.json();
        if (!resp.ok) {
            showResult('rotate-result', `❌ ${data.detail}`, 'error');
            return;
        }

        document.getElementById('revealed-box').classList.remove('hidden');
        document.getElementById('revealed-seed').textContent = data.revealed.server_seed;
        document.getElementById('revealed-client').textContent = data.revealed.client_seed;
        document.getElementById('revealed-hash').textContent = data.revealed.server_seed_hash;
        document.getElementById('revealed-spins').textContent = data.revealed.spins_made;

        document.getElementById('seed-hash').textContent = data.new_seed.server_seed_hash;
        document.getElementById('seed-client').textContent = data.new_seed.client_seed;
        document.getElementById('seed-nonce').textContent = data.new_seed.nonce;
        document.getElementById('new-client-seed').value = '';
    } catch (err) {
        showResult('rotate-result', '❌ Connection error', 'error');
    }
}

async function verifyRevealedSeed() {
    const serverSeed = document.getElementById('revealed-seed').textContent.trim();
    const clientSeed = document.getElementById('revealed-client').textContent.trim();
    const nonce = parseInt(document.getElementById('verify-nonce').value, 10);
    const out = document.getElementById('verify-output');

    if (!serverSeed || isNaN(nonce)) {
        out.textContent = 'Enter a spin number to check.';
        return;
    }

    out.textContent = 'Recomputing...';
    try {
        const resp = await fetch(`${API_BASE}/api/slots/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                machine: currentMachine.key, bet: currentBet,
                server_seed: serverSeed, client_seed: clientSeed, nonce: nonce
            })
        });
        const data = await resp.json();
        if (!resp.ok) {
            out.textContent = data.detail || 'Verification failed';
            return;
        }
        const grid = data.grid.map(row => row.join(' ')).join('\n');
        out.textContent =
            `Spin #${nonce} on ${currentMachine.name}\n` +
            `${grid}\n` +
            `${data.detail || 'No win'} · pays ${Math.round(data.win).toLocaleString()} pts at a ${currentBet.toLocaleString()} bet\n` +
            `hash matches commitment: ${data.server_seed_hash_matches ? 'YES ✅' : 'NO ❌'}`;
    } catch (err) {
        out.textContent = 'Connection error';
    }
}

function slotsBack() {
    showView('slots-lobby-view');
    loadSlotPlayer(slotCardId);
}
