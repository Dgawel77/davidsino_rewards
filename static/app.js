// Davidsino Rewards - Frontend JavaScript
const API_BASE = '';
let nfcReader = null;
let currentRole = null;
let currentScanAbort = null;
let currentCardId = null;
let currentPlayerId = null;
let currentLeaderboardSort = 'pnl';
// Kept in memory only, so dealer-only endpoints can be called with the PIN header.
let adminPin = null;

// Check NFC support
function checkNFC() {
    const statusEl = document.getElementById('nfc-status');
    if (!statusEl) return;
    if ('NDEFReader' in window) {
        statusEl.textContent = 'NFC ready — tap a card to scan';
        statusEl.classList.remove('hidden');
    } else {
        statusEl.textContent = 'No NFC on this device — use the USB reader or type a card ID';
        statusEl.classList.remove('hidden');
    }
}
checkNFC();

// ===== The tally bar =====
// One line that reads as both ledger columns: debit left of zero, credit right.
// `scale` is what a full half-bar means — usually the player's total buy-in, so
// the bar answers "how much of what I brought am I up or down?"
function setTally(containerId, value, scale) {
    const box = document.getElementById(containerId);
    if (!box) return;

    const fill = box.querySelector('.tally-fill');
    if (fill) {
        const span = Math.max(Math.abs(scale) || 0, Math.abs(value), 1);
        const pct = Math.min(1, Math.abs(value) / span) * 50;
        fill.classList.remove('up', 'down');
        fill.classList.add(value >= 0 ? 'up' : 'down');
        fill.style.width = (value === 0 ? 0 : pct) + '%';
    }

    const label = box.querySelector('.tally-value');
    if (label) {
        label.textContent = (value > 0 ? '+$' : value < 0 ? '−$' : '$') +
                            Math.abs(value).toFixed(2);
        label.className = 'tally-value ' + (value > 0 ? 'up' : value < 0 ? 'down' : 'flat');
    }
}

// ===== Header standing =====
function setHeaderPlayer(player) {
    const empty = document.getElementById('head-empty');
    const box = document.getElementById('head-player');
    if (!box || !empty) return;

    if (!player) {
        box.classList.add('hidden');
        empty.classList.remove('hidden');
        return;
    }

    document.getElementById('head-name').textContent = player.name;
    document.getElementById('head-points').textContent =
        Math.floor(player.reward_points).toLocaleString();

    const pnlEl = document.getElementById('head-pnl');
    const pnl = player.pnl;
    if (typeof pnl === 'number') {
        pnlEl.textContent = (pnl > 0 ? '+$' : pnl < 0 ? '−$' : '$') + Math.abs(pnl).toFixed(2);
        pnlEl.style.color = pnl > 0 ? 'var(--chip)' : pnl < 0 ? 'var(--marker)' : 'var(--bone-dim)';
    }
    empty.classList.add('hidden');
    box.classList.remove('hidden');
}

// A one-word read on the number, so the stat block says something a person would.
function verdictFor(pnl) {
    if (pnl > 0) return { text: 'Up', color: 'var(--chip)' };
    if (pnl < 0) return { text: 'Down', color: 'var(--marker)' };
    return { text: 'Even', color: 'var(--bone-dim)' };
}

// ===== View Navigation =====
function updateNav(viewId) {
    // Login views should light up the destination they lead to.
    const alias = { 'admin-login-view': 'admin-view', 'worker-login-view': 'worker-view',
                    'slots-play-view': 'slots-lobby-view', 'summary-view': 'scan-view' };
    const target = alias[viewId] || viewId;
    document.querySelectorAll('.rail-item').forEach(item => {
        item.classList.toggle('active', item.dataset.nav === target);
    });
}

function showView(viewId) {
    ['menu-view', 'scan-view', 'summary-view', 'leaderboard-view', 'worker-login-view', 'worker-view',
     'admin-login-view', 'admin-view', 'slots-lobby-view', 'slots-play-view', 'deposit-view'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    document.getElementById(viewId).classList.remove('hidden');
    updateNav(viewId);
    window.scrollTo(0, 0);
}

function backToMenu() {
    if (currentScanAbort) {
        currentScanAbort.abort();
        currentScanAbort = null;
    }
    currentRole = null;
    adminPin = null;
    currentCardId = null;
    currentPlayerId = null;
    showView('menu-view');
}

function backToScan() {
    showView('scan-view');
}

// ===== Scan Card =====
async function startScan() {
    showView('scan-view');
    document.getElementById('player-result').classList.add('hidden');
    document.getElementById('unregistered-result').classList.add('hidden');

    const statusEl = document.getElementById('scan-status');
    statusEl.textContent = 'Waiting for a card…';
    statusEl.className = 'scan-status scanning';

    const hiddenInput = document.createElement('input');
    hiddenInput.type = 'text';
    hiddenInput.style.position = 'fixed';
    hiddenInput.style.left = '-9999px';
    document.body.appendChild(hiddenInput);
    hiddenInput.focus();

    let cardInput = '';
    let inputTimeout = null;

    hiddenInput.addEventListener('input', (e) => {
        cardInput = hiddenInput.value;
        if (e.data === '\n' || cardInput.length > 8) {
            cardInput = cardInput.replace(/[\n\r]/g, '').trim();
            if (cardInput.length > 0) {
                clearTimeout(inputTimeout);
                processScan(cardInput);
            }
        }
    });

    hiddenInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            cardInput = cardInput.replace(/[\n\r]/g, '').trim();
            if (cardInput.length > 0) {
                clearTimeout(inputTimeout);
                processScan(cardInput);
            }
        }
    });

    if ('NDEFReader' in window && !nfcReader) {
        try {
            currentScanAbort = new AbortController();
            nfcReader = new NDEFReader();
            await nfcReader.scan({ signal: currentScanAbort.signal });

            nfcReader.onreading = (event) => {
                const decoder = new TextDecoder();
                for (const record of event.message.records) {
                    const cardId = decoder.decode(record.data);
                    if (cardId && cardId.length > 0) {
                        processScan(cardId.trim());
                        break;
                    }
                }
            };
        } catch (err) {
            console.log('NFC scan init failed:', err);
        }
    }

    inputTimeout = setTimeout(() => {
        if (!document.getElementById('player-result').classList.contains('hidden')) return;
        hiddenInput.remove();
        const manualDiv = document.createElement('div');
        manualDiv.style.marginTop = '16px';
        manualDiv.innerHTML = `
            <div class="input-group">
                <input type="text" id="manual-card-id" placeholder="Enter card ID manually"
                       onkeypress="if(event.key==='Enter') processScan(this.value)">
            </div>
            <button class="btn btn-small btn-secondary" onclick="processScan(document.getElementById('manual-card-id').value)">Submit</button>
        `;
        statusEl.parentNode.appendChild(manualDiv);
    }, 30000);
}

async function processScan(cardId) {
    if (!cardId || cardId.length === 0) return;
    currentCardId = cardId;

    const statusEl = document.getElementById('scan-status');
    statusEl.textContent = 'Looking up…';

    try {
        const resp = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId })
        });
        const data = await resp.json();

        if (data.registered) {
            statusEl.textContent = 'Found it';
            statusEl.className = 'scan-status found';

            currentPlayerId = data.player.id;
            document.getElementById('result-name').textContent = data.player.name;
            document.getElementById('result-points').textContent = data.player.reward_points.toFixed(0);
            document.getElementById('result-cashin').textContent = '$' + data.player.total_cash_in.toFixed(2);
            document.getElementById('result-cashout').textContent = '$' + data.player.total_cash_out.toFixed(2);

            // Scale the bar against what they bought in for, so it reads as
            // "how much of my buy-in am I up or down".
            setTally('result-tally', data.player.pnl, data.player.total_cash_in);

            const verdict = verdictFor(data.player.pnl);
            const verdictEl = document.getElementById('result-net-label');
            verdictEl.textContent = verdict.text;
            verdictEl.style.color = verdict.color;

            setHeaderPlayer(data.player);

            document.getElementById('player-result').classList.remove('hidden');
            document.getElementById('unregistered-result').classList.add('hidden');
        } else {
            statusEl.textContent = 'Not on file';
            statusEl.className = 'scan-status not-found';
            document.getElementById('player-result').classList.add('hidden');
            document.getElementById('unregistered-result').classList.remove('hidden');
        }
    } catch (err) {
        statusEl.textContent = 'Error: ' + err.message;
        statusEl.className = 'scan-status not-found';
    }
}

// ===== Summary View =====
async function showSummary() {
    if (!currentPlayerId) return;
    showView('summary-view');

    try {
        const resp = await fetch(`${API_BASE}/api/players/${currentPlayerId}/summary`);
        const data = await resp.json();

        document.getElementById('summary-name').textContent = data.player.name;
        document.getElementById('summary-points').textContent =
            Math.floor(data.player.reward_points).toLocaleString();

        setTally('summary-tally', data.player.pnl, data.player.total_cash_in);
        setHeaderPlayer(data.player);

        // Show roast
        if (data.roast) {
            document.getElementById('roast-text').textContent = data.roast;
            document.getElementById('summary-roast').classList.remove('hidden');
        } else {
            // Generate roast
            try {
                const roastResp = await fetch(`${API_BASE}/api/players/${currentPlayerId}/roast`, { method: 'POST' });
                const roastData = await roastResp.json();
                document.getElementById('roast-text').textContent = roastData.roast;
                document.getElementById('summary-roast').classList.remove('hidden');
            } catch (e) {
                document.getElementById('summary-roast').classList.add('hidden');
            }
        }

        // Load history and daily PNL
        loadHistory();
        loadDailyPnl();
    } catch (err) {
        console.error('Summary load error:', err);
    }
}

function showSummaryTab(tab) {
    document.querySelectorAll('#summary-view .tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');

    document.getElementById('summary-history').classList.add('hidden');
    document.getElementById('summary-daily').classList.add('hidden');
    document.getElementById('summary-' + tab).classList.remove('hidden');
}

async function loadHistory() {
    try {
        const resp = await fetch(`${API_BASE}/api/players/${currentPlayerId}/history?limit=50`);
        const data = await resp.json();

        const tbody = document.getElementById('history-body');
        if (data.events.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--bone-mute);">No events yet</td></tr>';
            return;
        }

        tbody.innerHTML = data.events.map(e => {
            const typeClass = 'event-' + e.event_type.replace(/-/g, '_');
            let changeText = '';
            if (e.cash_amount !== 0) {
                changeText = `$${e.cash_amount.toFixed(2)}`;
            } else if (e.points_delta !== 0) {
                changeText = `${e.points_delta > 0 ? '+' : ''}${e.points_delta.toFixed(0)} pts`;
            }

            return `
                <tr>
                    <td class="${typeClass}">${formatEventType(e.event_type)}</td>
                    <td style="font-size:0.8rem; color:var(--bone-dim);">${e.description || ''}</td>
                    <td style="text-align:right; font-weight:bold;">${changeText}</td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        document.getElementById('history-body').innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--marker);">Failed to load</td></tr>';
    }
}

async function loadDailyPnl() {
    try {
        const resp = await fetch(`${API_BASE}/api/players/${currentPlayerId}/daily-pnl`);
        const data = await resp.json();

        const chartEl = document.getElementById('daily-chart');
        if (data.daily_pnl.length === 0) {
            chartEl.innerHTML = '<div class="empty">No data yet</div>';
            return;
        }

        const maxPnl = Math.max(...data.daily_pnl.map(d => Math.abs(d.daily_pnl)));
        const maxHeight = 100;

        chartEl.innerHTML = data.daily_pnl.map(d => {
            const height = maxPnl > 0 ? (Math.abs(d.daily_pnl) / maxPnl) * maxHeight : 0;
            const barClass = d.daily_pnl >= 0 ? 'chart-bar-positive' : 'chart-bar-negative';
            const dateParts = d.date.split('-');
            const shortDate = `${dateParts[1]}/${dateParts[2]}`;

            return `
                <div class="chart-bar ${barClass}" style="height: ${height}px;" title="${d.date}: ${d.daily_pnl >= 0 ? '+' : ''}$${d.daily_pnl.toFixed(2)} (Running: ${d.running_total >= 0 ? '+' : ''}$${d.running_total.toFixed(2)})">
                    <div class="chart-bar-value">${d.daily_pnl >= 0 ? '+' : ''}$${d.daily_pnl.toFixed(0)}</div>
                    <div class="chart-bar-label">${shortDate}</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        document.getElementById('daily-chart').innerHTML = '<div style="text-align:center; color:var(--marker); padding: 20px;">Failed to load</div>';
    }
}

function formatEventType(type) {
    const labels = {
        'deposit': '💵 Deposit',
        'cashout': '💸 Cashout',
        'reward_add': '⭐ Bonus Pts',
        'reward_redeem': '🎁 Redeem',
        'registration': '📝 Registered',
        'slot_spin': '🎰 Slots',
    };
    return labels[type] || type;
}

// ===== Leaderboard =====
async function showLeaderboard() {
    showView('leaderboard-view');
    await loadLeaderboard('pnl');
}

async function showLeaderboardSort(sortBy) {
    currentLeaderboardSort = sortBy;
    document.querySelectorAll('#leaderboard-view .tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    await loadLeaderboard(sortBy);
}

async function loadLeaderboard(sortBy) {
    try {
        const resp = await fetch(`${API_BASE}/api/leaderboard?sort_by=${sortBy}`);
        const data = await resp.json();

        const houseEl = document.getElementById('house-pnl');
        const housePnl = data.house_pnl;
        houseEl.textContent = (housePnl > 0 ? 'up $' : housePnl < 0 ? 'down $' : 'even at $') +
                              Math.abs(housePnl).toFixed(2);
        houseEl.style.color = housePnl > 0 ? 'var(--chip)'
                            : housePnl < 0 ? 'var(--marker)' : 'var(--bone-dim)';

        // Scale the house bar against everything wagered through it tonight.
        const totalIn = data.players.reduce((sum, p) => sum + p.total_cash_in, 0);
        setTally('house-tally', housePnl, totalIn);

        const listEl = document.getElementById('leaderboard-list');
        if (data.players.length === 0) {
            listEl.innerHTML = '<div class="empty">No players yet</div>';
            return;
        }

        const valueKey = sortBy === 'pnl' ? 'pnl' : (sortBy === 'points' ? 'reward_points' : 'total_cash_in');
        const prefix = sortBy === 'pnl' || sortBy === 'cash_in' ? '$' : '';

        listEl.innerHTML = data.players.map((p, idx) => {
            const rank = idx + 1;
            const rankClass = rank <= 3 ? `rank-${rank}` : '';
            const value = p[valueKey];
            let displayValue;
            if (sortBy === 'pnl') {
                displayValue = `${value >= 0 ? '+' : '-'}${prefix}${Math.abs(value).toFixed(2)}`;
            } else if (sortBy === 'points') {
                displayValue = `${value.toFixed(0)} pts`;
            } else {
                displayValue = `${prefix}${value.toFixed(2)}`;
            }

            return `
                <div class="leaderboard-item ${rankClass}">
                    <div class="leaderboard-rank">#${rank}</div>
                    <div class="leaderboard-name">${p.name}</div>
                    <div class="leaderboard-value">${displayValue}</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        document.getElementById('leaderboard-list').innerHTML = '<div style="text-align:center; color:var(--marker); padding: 20px;">Failed to load</div>';
    }
}

// ===== Worker Functions =====
function showWorkerLogin() {
    showView('worker-login-view');
    document.getElementById('worker-pin').value = '';
    document.getElementById('worker-login-error').classList.add('hidden');
}

async function workerLogin() {
    const pin = document.getElementById('worker-pin').value;
    try {
        const resp = await fetch(`${API_BASE}/api/admin/auth`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin: pin, role: 'worker' })
        });

        if (resp.ok) {
            const data = await resp.json();
            currentRole = data.role;
            showView('worker-view');
            loadRewards();
        } else {
            document.getElementById('worker-login-error').textContent = 'Invalid PIN';
            document.getElementById('worker-login-error').classList.remove('hidden');
        }
    } catch (err) {
        document.getElementById('worker-login-error').textContent = 'Connection error';
        document.getElementById('worker-login-error').classList.remove('hidden');
    }
}

function workerLogout() {
    currentRole = null;
    backToMenu();
}

async function workerLookupPlayer() {
    const cardId = document.getElementById('worker-card-id').value.trim();
    if (!cardId) return;

    try {
        const resp = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId })
        });
        const data = await resp.json();

        if (data.registered) {
            document.getElementById('worker-player-name').textContent = data.player.name;
            document.getElementById('worker-player-points').textContent = data.player.reward_points.toFixed(0);
            document.getElementById('worker-player-info').classList.remove('hidden');
        } else {
            document.getElementById('worker-player-info').classList.add('hidden');
            showResult('worker-result', '❌ Player not found', 'error');
        }
    } catch (err) {
        showResult('worker-result', '❌ Connection error', 'error');
    }
}

async function loadRewards() {
    try {
        const resp = await fetch(`${API_BASE}/api/worker/rewards`);
        const rewards = await resp.json();

        const container = document.getElementById('rewards-container');
        container.innerHTML = rewards.map(r => `
            <div class="reward-item" onclick="redeemReward('${r.key}', '${r.name}', ${r.points})">
                <div>
                    <div class="reward-name">${r.name}</div>
                    <div class="reward-desc">${r.description}</div>
                </div>
                <div class="reward-cost">${r.points} pts</div>
            </div>
        `).join('');
    } catch (err) {
        document.getElementById('rewards-container').innerHTML = '<p style="color:var(--marker);">Failed to load rewards</p>';
    }
}

async function redeemReward(rewardKey, rewardName, pointsCost) {
    const cardId = document.getElementById('worker-card-id').value.trim();
    if (!cardId) {
        showResult('worker-result', '❌ Please enter card ID first', 'error');
        return;
    }
    if (!confirm(`Redeem ${rewardName} for ${pointsCost} points?`)) return;

    try {
        const resp = await fetch(`${API_BASE}/api/worker/redeem`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId, reward_key: rewardKey })
        });
        const data = await resp.json();

        if (resp.ok) {
            showResult('worker-result', `✅ ${data.message}\nRemaining points: ${data.reward_points.toFixed(0)}`, 'success');
            document.getElementById('worker-player-points').textContent = data.reward_points.toFixed(0);
        } else {
            showResult('worker-result', `❌ ${data.detail}`, 'error');
        }
    } catch (err) {
        showResult('worker-result', '❌ Connection error', 'error');
    }
}

// ===== Admin Functions =====
function showAdminLogin() {
    showView('admin-login-view');
    document.getElementById('admin-pin').value = '';
    document.getElementById('login-error').classList.add('hidden');
}

async function adminLogin() {
    const pin = document.getElementById('admin-pin').value;
    try {
        const resp = await fetch(`${API_BASE}/api/admin/auth`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin: pin, role: 'admin' })
        });

        if (resp.ok) {
            const data = await resp.json();
            currentRole = data.role;
            adminPin = pin;   // needed for the dealer-only deposit endpoints
            showView('admin-view');
            loadPlayers();
        } else {
            document.getElementById('login-error').textContent = 'Invalid PIN';
            document.getElementById('login-error').classList.remove('hidden');
        }
    } catch (err) {
        document.getElementById('login-error').textContent = 'Connection error';
        document.getElementById('login-error').classList.remove('hidden');
    }
}

function adminLogout() {
    currentRole = null;
    adminPin = null;
    backToMenu();
}

function showAdminTab(tab) {
    document.querySelectorAll('#admin-view .tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');

    ['actions', 'pending', 'players', 'register'].forEach(name => {
        const el = document.getElementById('admin-' + name);
        if (el) el.classList.add('hidden');
    });

    document.getElementById('admin-' + tab).classList.remove('hidden');

    if (tab === 'players') loadPlayers();
    if (tab === 'pending') loadPendingDeposits();
}

async function submitAction() {
    const cardId = document.getElementById('action-card-id').value.trim();
    const actionType = document.getElementById('action-type').value;
    const amount = parseFloat(document.getElementById('action-amount').value);
    const description = document.getElementById('action-description').value.trim();

    if (!cardId || isNaN(amount) || amount <= 0) {
        showResult('action-result', 'Please fill in all fields', 'error');
        return;
    }

    const endpointMap = {
        'deposit': '/api/admin/deposit',
        'cashout': '/api/admin/cashout',
        'add_points': '/api/admin/add_points',
        'redeem_points': '/api/admin/redeem_points',
    };
    const endpoint = endpointMap[actionType];

    if (!endpoint) {
        showResult('action-result', '❌ Unknown action type', 'error');
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                card_id: cardId,
                amount: amount,
                description: description
            })
        });

        const data = await resp.json();

        if (resp.ok) {
            let msg = `✅ ${data.message}`;
            if (data.reward_points !== undefined) msg += `\nPoints: ${data.reward_points.toFixed(0)}`;
            if (data.reward_earned) msg += ` (+${data.reward_earned.toFixed(0)} earned)`;
            if (data.pnl !== undefined) {
                const pnlSign = data.pnl >= 0 ? '+' : '-';
                msg += `\nPNL: ${pnlSign}$${Math.abs(data.pnl).toFixed(2)}`;
            }
            showResult('action-result', msg, 'success');
            document.getElementById('action-amount').value = '';
            document.getElementById('action-description').value = '';
        } else {
            showResult('action-result', `❌ ${data.detail}`, 'error');
        }
    } catch (err) {
        showResult('action-result', '❌ Connection error', 'error');
    }
}

async function loadPlayers() {
    try {
        const resp = await fetch(`${API_BASE}/api/admin/players`);
        const players = await resp.json();

        const listEl = document.getElementById('players-list');
        if (players.length === 0) {
            listEl.innerHTML = '<div class="empty">No players registered</div>';
            return;
        }

        listEl.innerHTML = players.map(p => `
            <div class="player-list-item">
                <div>
                    <div class="player-list-name">${p.name}</div>
                    <div style="font-size:0.75rem;color:var(--bone-mute);">${p.card_id.substring(0, 12)}...</div>
                </div>
                <div style="text-align:right;">
                    <div class="player-list-points">${p.reward_points.toFixed(0)} pts</div>
                    <div style="font-size:0.75rem;color:var(--bone-mute);">In: $${p.total_cash_in.toFixed(0)} | Out: $${p.total_cash_out.toFixed(0)}</div>
                    <div style="font-size:0.75rem;color:${p.pnl >= 0 ? 'var(--chip)' : 'var(--marker)'};">
                        P/L: ${p.pnl >= 0 ? '+' : '-'}$${Math.abs(p.pnl).toFixed(2)}
                    </div>
                </div>
            </div>
        `).join('');
    } catch (err) {
        document.getElementById('players-list').innerHTML = '<p style="text-align:center;color:var(--marker);">Failed to load</p>';
    }
}

async function registerPlayer() {
    const cardId = document.getElementById('reg-card-id').value.trim();
    const name = document.getElementById('reg-name').value.trim();

    if (!cardId || !name) {
        showResult('reg-result', 'Please fill in all fields', 'error');
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/api/admin/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId, name: name })
        });

        const data = await resp.json();

        if (resp.ok) {
            showResult('reg-result', `✅ ${data.message}`, 'success');
            document.getElementById('reg-card-id').value = '';
            document.getElementById('reg-name').value = '';
        } else {
            showResult('reg-result', `❌ ${data.detail}`, 'error');
        }
    } catch (err) {
        showResult('reg-result', '❌ Connection error', 'error');
    }
}

// ===== Utilities =====
function showResult(elementId, message, type) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = message;
    el.className = type;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 5000);
}

// Keyboard listeners
const adminPinEl = document.getElementById('admin-pin');
if (adminPinEl) {
    adminPinEl.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') adminLogin();
    });
}
const workerPinEl = document.getElementById('worker-pin');
if (workerPinEl) {
    workerPinEl.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') workerLogin();
    });
}

// ===== Player Search =====
async function searchPlayers() {
    const query = document.getElementById('search-input').value.trim();
    if (!query) return;
    
    const resultsEl = document.getElementById('search-results');
    resultsEl.innerHTML = '<div class="empty">Searching...</div>';
    
    try {
        const resp = await fetch(`${API_BASE}/api/players/search?query=${encodeURIComponent(query)}`);
        const data = await resp.json();
        
        if (data.count === 0) {
            resultsEl.innerHTML = '<div class="empty">No players found</div>';
            return;
        }
        
        resultsEl.innerHTML = data.players.map(p => {
            const pnlSign = p.pnl >= 0 ? '+' : '-';
            return `
                <div class="player-list-item" onclick="selectSearchedPlayer(${p.id})" style="cursor:pointer;">
                    <div>
                        <div class="player-list-name">${p.name}</div>
                        <div style="font-size:0.75rem;color:var(--bone-mute);">${p.card_id.substring(0, 12)}...</div>
                    </div>
                    <div style="text-align:right;">
                        <div class="player-list-points">${p.reward_points.toFixed(0)} pts</div>
                        <div style="font-size:0.75rem;color:${p.pnl >= 0 ? 'var(--chip)' : 'var(--marker)'};">
                            P/L: ${pnlSign}$${Math.abs(p.pnl).toFixed(2)}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsEl.innerHTML = '<div style="text-align:center; color:var(--marker);">Search failed</div>';
    }
}

async function selectSearchedPlayer(playerId) {
    currentPlayerId = playerId;
    try {
        const resp = await fetch(`${API_BASE}/api/admin/players`);
        const players = await resp.json();
        const player = players.find(p => p.id === playerId);
        if (player) {
            document.getElementById('result-name').textContent = player.name;
            document.getElementById('result-points').textContent = player.reward_points.toFixed(0);
            document.getElementById('result-cashin').textContent = '$' + player.total_cash_in.toFixed(2);
            document.getElementById('result-cashout').textContent = '$' + player.total_cash_out.toFixed(2);

            setTally('result-tally', player.pnl, player.total_cash_in);

            const verdict = verdictFor(player.pnl);
            const verdictEl = document.getElementById('result-net-label');
            verdictEl.textContent = verdict.text;
            verdictEl.style.color = verdict.color;

            setHeaderPlayer(player);

            document.getElementById('player-result').classList.remove('hidden');
            document.getElementById('unregistered-result').classList.add('hidden');
            document.getElementById('search-results').innerHTML = '';
            document.getElementById('search-input').value = '';
        }
    } catch (err) {
        console.error('Failed to load player:', err);
    }
}
