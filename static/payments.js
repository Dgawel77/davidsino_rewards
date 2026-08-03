// Davidsino Rewards - deposit requests (crypto first)
let depositMethods = [];
let depositMaxUsd = 10000;
let selectedMethod = null;
let activeRequestId = null;

// ===== Player: request a deposit =====
async function showDeposit() {
    showView('deposit-view');
    document.getElementById('deposit-form').classList.remove('hidden');
    document.getElementById('deposit-instructions').classList.add('hidden');
    document.getElementById('deposit-error').classList.add('hidden');
    activeRequestId = null;
    selectedMethod = null;

    syncCardInputs();
    await loadDepositMethods();
}

async function loadDepositMethods() {
    const container = document.getElementById('method-list');
    container.innerHTML = '<div style="color:var(--bone-mute);">Loading...</div>';

    try {
        const resp = await fetch(`${API_BASE}/api/payments/methods`);
        const data = await resp.json();
        depositMethods = data.methods || [];
        depositMaxUsd = data.max_deposit || 10000;

        if (depositMethods.length === 0) {
            container.innerHTML = `
                <div style="text-align:center; color:var(--bone-mute); padding:16px;">
                    No deposit methods set up yet.<br>
                    <span style="font-size:0.8rem;">The dealer needs to add a wallet
                    address or payment handle to the server config.</span>
                </div>`;
            return;
        }

        container.innerHTML = depositMethods.map(m => {
            const sub = m.kind === 'crypto'
                ? `${m.symbol} · ${m.network}`
                : m.handle;
            return `
                <div class="method-item" data-key="${m.key}" onclick="selectMethod('${m.key}')">
                    <div class="method-icon">${methodIcon(m)}</div>
                    <div style="flex:1;">
                        <div class="method-label">${m.label}</div>
                        <div class="method-sub">${sub}</div>
                    </div>
                </div>`;
        }).join('');
    } catch (err) {
        container.innerHTML = '<div style="color:var(--marker);">Failed to load payment methods</div>';
    }
}

function methodIcon(m) {
    const icons = {
        btc: '₿', eth: 'Ξ', ltc: 'Ł', sol: '◎', usdt: '₮', usdc: '$',
        venmo: '💸', cashapp: '💵', paypal: '🅿️', zelle: '🏦',
    };
    return icons[m.key] || '💰';
}

function selectMethod(key) {
    selectedMethod = key;
    document.querySelectorAll('.method-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.key === key);
    });
}

function setDepositAmount(amount) {
    document.getElementById('deposit-amount').value = amount;
}

async function submitDepositRequest() {
    const cardId = currentCardId || document.getElementById('deposit-card-id').value.trim();
    const amount = parseFloat(document.getElementById('deposit-amount').value);
    const errEl = document.getElementById('deposit-error');
    errEl.classList.add('hidden');

    if (!cardId) return showDepositError('Scan in first — use the card control up top');
    if (isNaN(amount) || amount <= 0) return showDepositError('Enter an amount');
    if (amount > depositMaxUsd) return showDepositError(`Max deposit is $${depositMaxUsd.toLocaleString()}`);
    if (!selectedMethod) return showDepositError('Pick a payment method');

    try {
        const resp = await fetch(`${API_BASE}/api/payments/deposit-request`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_id: cardId, amount: amount, method: selectedMethod })
        });
        const data = await resp.json();
        if (!resp.ok) return showDepositError(data.detail || 'Request failed');

        activeRequestId = data.request_id;
        renderInstructions(data);
    } catch (err) {
        showDepositError('Connection error');
    }
}

function showDepositError(msg) {
    const errEl = document.getElementById('deposit-error');
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
}

function renderInstructions(data) {
    const ins = data.instructions;
    document.getElementById('deposit-form').classList.add('hidden');
    const box = document.getElementById('deposit-instructions');
    box.classList.remove('hidden');

    const isCrypto = ins.kind === 'crypto';
    const target = isCrypto ? ins.address : ins.handle;

    let amountLine;
    if (isCrypto && ins.crypto_amount) {
        amountLine = `
            <div class="pay-amount">${ins.crypto_amount} ${ins.symbol}</div>
            <div class="pay-amount-sub">= $${ins.usd_amount.toFixed(2)}
                ${ins.price_usd ? `· 1 ${ins.symbol} = $${ins.price_usd.toLocaleString()}` : ''}</div>`;
    } else {
        amountLine = `
            <div class="pay-amount">$${ins.usd_amount.toFixed(2)}</div>
            ${isCrypto ? `<div class="pay-amount-sub">worth of ${ins.symbol}</div>` : ''}`;
    }

    const qrBlock = `
        <img class="pay-qr" src="${API_BASE}/api/payments/request/${data.request_id}/qr"
             alt="Payment QR code" onerror="this.style.display='none'">`;

    const openLink = ins.uri && (ins.uri.startsWith('http') || isCrypto)
        ? `<a class="btn btn-secondary" href="${ins.uri}" style="display:block; text-decoration:none;
             text-align:center;">Open in ${isCrypto ? 'wallet' : 'app'}</a>`
        : '';

    box.innerHTML = `
        <div class="card" style="text-align:center;">
            <div class="pay-label">Send ${isCrypto ? ins.symbol : ins.label}</div>
            ${amountLine}
            ${qrBlock}
            <div class="pay-address" id="pay-target">${target}</div>
            <button class="btn btn-small btn-secondary" onclick="copyTarget()">📋 Copy</button>
            ${isCrypto ? `<div class="pay-network">Network: <strong>${ins.network}</strong></div>` : ''}
            <div class="pay-note">${ins.note}</div>
            ${openLink}
        </div>

        <div class="card">
            <h4 style="margin-bottom:10px;">Already sent it?</h4>
            ${isCrypto ? `
                <div class="input-group">
                    <label>Transaction ID (optional, speeds up confirmation)</label>
                    <input type="text" id="deposit-txid" placeholder="Paste the tx hash">
                </div>
                <button class="btn btn-small btn-secondary" onclick="submitTxid()">Submit TXID</button>
            ` : ''}
            <p style="color:var(--bone-mute); font-size:0.85rem; margin-top:10px;">
                Show this screen to the dealer. Your
                <strong style="color:var(--brass);">${(data.points_on_confirm).toLocaleString()} points</strong>
                land as soon as they confirm the money arrived.
            </p>
            <div id="txid-result" class="hidden"></div>
        </div>

        <button class="btn btn-secondary" onclick="showDeposit()">← New Request</button>`;
}

function copyTarget() {
    const text = document.getElementById('pay-target').textContent.trim();
    navigator.clipboard.writeText(text).then(
        () => showResult('txid-result', '✅ Copied', 'success'),
        () => showResult('txid-result', 'Copy failed — select it manually', 'error')
    );
}

async function submitTxid() {
    const input = document.getElementById('deposit-txid');
    if (!input || !activeRequestId) return;
    const txid = input.value.trim();
    if (!txid) return showResult('txid-result', 'Paste the transaction ID first', 'error');

    try {
        const resp = await fetch(`${API_BASE}/api/payments/request/${activeRequestId}/txid`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ txid: txid })
        });
        const data = await resp.json();
        showResult('txid-result', resp.ok ? `✅ ${data.message}` : `❌ ${data.detail}`,
                   resp.ok ? 'success' : 'error');
    } catch (err) {
        showResult('txid-result', '❌ Connection error', 'error');
    }
}

// ===== Dealer: confirm queue =====
async function loadPendingDeposits() {
    const listEl = document.getElementById('pending-list');
    listEl.innerHTML = '<div style="color:var(--bone-mute);">Loading...</div>';

    try {
        const resp = await fetch(`${API_BASE}/api/admin/pending-deposits`, {
            headers: { 'X-Admin-Pin': adminPin || '' }
        });
        if (resp.status === 401) {
            listEl.innerHTML = '<div style="color:var(--marker);">Session expired — log in again</div>';
            return;
        }
        const data = await resp.json();
        const requests = data.requests || [];

        if (requests.length === 0) {
            listEl.innerHTML = '<div class="empty">No pending deposits</div>';
            return;
        }

        listEl.innerHTML = requests.map(r => {
            const ins = r.instructions || {};
            const amountDetail = ins.crypto_amount
                ? `${ins.crypto_amount} ${ins.symbol}`
                : (ins.symbol ? `$${r.amount.toFixed(2)} of ${ins.symbol}` : '');
            return `
                <div class="pending-item">
                    <div class="pending-head">
                        <div>
                            <div class="pending-player">${r.player}</div>
                            <div class="pending-meta">${ins.label || r.method}
                                ${amountDetail ? `· ${amountDetail}` : ''}
                                · ${timeAgo(r.created_at)}</div>
                        </div>
                        <div class="pending-amount">$${r.amount.toFixed(2)}</div>
                    </div>
                    ${r.txid ? `<div class="pending-txid" title="${r.txid}">tx: ${r.txid}</div>`
                             : '<div class="pending-meta" style="margin-top:4px;">no txid submitted</div>'}
                    <div class="pending-actions">
                        <button class="btn btn-small btn-green" onclick="resolveDeposit(${r.id}, 'confirm')">
                            ✅ Confirm (+${(r.amount * 100).toLocaleString()} pts)</button>
                        <button class="btn btn-small btn-primary" onclick="resolveDeposit(${r.id}, 'cancel')">
                            ❌ Reject</button>
                    </div>
                </div>`;
        }).join('');
    } catch (err) {
        listEl.innerHTML = '<div style="color:var(--marker);">Failed to load</div>';
    }
}

async function resolveDeposit(reqId, action) {
    const label = action === 'confirm' ? 'Confirm this deposit and credit points?'
                                       : 'Reject this deposit request?';
    if (!confirm(label)) return;

    try {
        const resp = await fetch(`${API_BASE}/api/admin/pending-deposits/${reqId}/${action}`, {
            method: 'POST',
            headers: { 'X-Admin-Pin': adminPin || '' }
        });
        const data = await resp.json();
        showResult('pending-result', resp.ok ? `✅ ${data.message}` : `❌ ${data.detail}`,
                   resp.ok ? 'success' : 'error');
        loadPendingDeposits();
    } catch (err) {
        showResult('pending-result', '❌ Connection error', 'error');
    }
}

function timeAgo(iso) {
    if (!iso) return '';
    // Timestamps are stored naive-UTC, so tag them as UTC unless an offset is present.
    const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
    const then = new Date(hasZone ? iso : iso + 'Z');
    if (isNaN(then.getTime())) return '';
    const mins = Math.floor((Date.now() - then.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}
