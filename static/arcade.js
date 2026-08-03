// Davidsino Rewards - crash, plinko and mines.
//
// These share the table plumbing (bet controls, session, seed, auto mode) and
// only bring their own felt. Nothing here decides an outcome: the server has
// already fixed the bust point, the ball's path and the mine layout from the
// seed before any of this runs.

let crashTimer = null;      // requestAnimationFrame handle for the climb
let crashRoundId = null;
let crashStartMs = 0;
let crashDouble = 4.5;
let plinkoRisk = 'medium';
let crashTarget = null;      // optional auto cash-out
let mineCount = 3;

const CRASH_GRID = [1.5, 2, 3, 5, 10];

// ===== Crash =====
function renderCrashIdle() {
    const stage = document.getElementById('table-stage');
    stage.innerHTML = crashShell('1.00', '', 'Place a bet and hold your nerve');
    drawCrashTrack(1.0, false);
}

function crashShell(mult, cls, sub) {
    return `
        <div class="crash-stage">
            <div class="crash-mult ${cls}" id="crash-mult">${mult}<span style="font-size:0.5em">x</span></div>
            <div class="crash-sub" id="crash-sub">${sub}</div>
            <div class="crash-track" id="crash-track"></div>
        </div>`;
}

// The track is drawn from scratch each frame-ish: a few reference lines and the
// rocket's position along the same exponential the server settles against.
function drawCrashTrack(mult, flying, gone) {
    const track = document.getElementById('crash-track');
    if (!track) return;
    const top = Math.max(2.0, mult * 1.25);

    const lines = CRASH_GRID.filter(g => g <= top).map(g => {
        const pct = (Math.log2(g) / Math.log2(top)) * 100;
        return `<div class="crash-grid-line" style="bottom:${pct}%"></div>
                <div class="crash-grid-label" style="bottom:${pct}%">${g}x</div>`;
    }).join('');

    const x = Math.min(96, (Math.log2(Math.max(mult, 1.001)) / Math.log2(top)) * 100);
    const y = Math.min(92, (Math.log2(Math.max(mult, 1.001)) / Math.log2(top)) * 100);
    track.innerHTML = `${lines}
        <div class="crash-rocket ${gone ? 'gone' : ''}"
             style="left:${Math.max(4, x)}%; bottom:${Math.max(4, y)}%">${gone ? '💥' : '🚀'}</div>`;
}

function startCrashClimb(round) {
    stopCrashClimb();
    crashRoundId = round.round_id;
    crashDouble = round.double_seconds || 4.5;
    // Trust the server's elapsed, not our own clock: it is the only one the
    // cash-out will be measured against.
    crashStartMs = Date.now() - (round.elapsed || 0) * 1000;

    const tick = () => {
        if (!activeRound || activeRound.round_id !== crashRoundId) return stopCrashClimb();
        const elapsed = (Date.now() - crashStartMs) / 1000;
        const m = Math.max(1, Math.pow(2, elapsed / crashDouble));
        const shown = Math.floor(m * 100) / 100;
        const el = document.getElementById('crash-mult');
        if (el) el.innerHTML = shown.toFixed(2) + '<span style="font-size:0.5em">x</span>';
        drawCrashTrack(shown, true);
        crashTimer = requestAnimationFrame(tick);
    };
    crashTimer = requestAnimationFrame(tick);
}

function stopCrashClimb() {
    if (crashTimer) cancelAnimationFrame(crashTimer);
    crashTimer = null;
}

function renderCrash(round) {
    const stage = document.getElementById('table-stage');
    const live = round.stage === 'flying';

    if (live) {
        stage.innerHTML = crashShell('1.00', 'flying', 'Cash out before it goes');
        startCrashClimb(round);
        renderActions(['cashout']);
        return;
    }

    stopCrashClimb();
    const bust = round.bust;
    const cashed = round.cashed_at;
    if (cashed) {
        stage.innerHTML = crashShell(cashed.toFixed(2), 'cashed',
            `Out at ${cashed.toFixed(2)}x — it ran to ${bust.toFixed(2)}x`);
        drawCrashTrack(bust, false, false);
    } else {
        stage.innerHTML = crashShell(bust.toFixed(2), 'bust',
            round.target ? `Busted at ${bust.toFixed(2)}x, short of your ${round.target.toFixed(2)}x`
                         : `Busted at ${bust.toFixed(2)}x`);
        drawCrashTrack(bust, false, true);
        SFX.bust();
    }
    renderActions([]);
}

// ===== Plinko =====
function renderPlinkoIdle() {
    const stage = document.getElementById('table-stage');
    const rows = (currentTable && currentTable.rows) || 12;
    stage.innerHTML = `
        <div class="plinko-board" id="plinko-board">${plinkoPins(rows)}</div>
        <div class="plinko-buckets" id="plinko-buckets">${plinkoBuckets()}</div>`;
}

function plinkoPins(rows) {
    // Row r has r+1 pins; lay them out as a triangle inside the board.
    let html = '';
    for (let r = 1; r < rows; r++) {
        const count = r + 1;
        for (let i = 0; i < count; i++) {
            const x = 50 + (i - r / 2) * (90 / rows);
            const y = ((r + 0.5) / rows) * 100;
            html += `<div class="plinko-pin" style="left:${x}%; top:${y}%"></div>`;
        }
    }
    return html;
}

function plinkoBuckets(landed) {
    const risks = (currentTable && currentTable.risks) || {};
    const table = (risks[plinkoRisk] && risks[plinkoRisk].table) || [];
    const best = Math.max(...table, 0);
    return table.map((m, i) => {
        const cls = i === landed ? 'landed' : (m >= best ? 'hot' : '');
        return `<div class="plinko-bucket ${cls}">${m}x</div>`;
    }).join('');
}

// Walk the ball down the path the server already drew.
function animatePlinko(data) {
    const rows = (currentTable && currentTable.rows) || 12;
    const board = document.getElementById('plinko-board');
    if (!board) return;

    const ball = document.createElement('div');
    ball.className = 'plinko-ball';
    ball.style.left = '50%';
    ball.style.top = '0%';
    board.appendChild(ball);

    const anim = motionOn();
    const stepMs = anim ? 105 : 0;
    let offset = 0;

    const step = (r) => {
        if (r >= rows) {
            board.removeChild(ball);
            document.getElementById('plinko-buckets').innerHTML = plinkoBuckets(data.bucket);
            return;
        }
        if (data.path[r] === 'R') offset += 0.5; else offset -= 0.5;
        ball.style.left = (50 + offset * (90 / rows)) + '%';
        ball.style.top = (((r + 1) / rows) * 100) + '%';
        if (anim) SFX.chip();
        setTimeout(() => step(r + 1), stepMs);
    };
    step(0);
}

function selectPlinkoRisk(risk) {
    plinkoRisk = risk;
    SFX.chip();
    document.querySelectorAll('#table-bet-picker .bet-option').forEach(el => {
        el.classList.toggle('selected', el.dataset.bet === risk);
    });
    document.getElementById('plinko-buckets').innerHTML = plinkoBuckets();
}

// ===== Mines =====
function renderMinesIdle() {
    const stage = document.getElementById('table-stage');
    stage.innerHTML = `
        <div class="mines-grid" id="mines-grid">
            ${Array.from({ length: 25 }, (_, i) =>
                `<div class="mine-tile done" data-tile="${i}">·</div>`).join('')}
        </div>
        <div class="mines-meta">
            <div class="mines-stat"><span class="eyebrow">Mines</span>
                <span class="mines-stat-val" id="mines-count">${mineCount}</span></div>
            <div class="mines-stat"><span class="eyebrow">Next pick</span>
                <span class="mines-stat-val" id="mines-next">–</span></div>
        </div>`;
}

function renderMines(round) {
    const stage = document.getElementById('table-stage');
    const live = round.stage === 'picking';
    const picked = new Set(round.picked || []);
    const mines = new Set(round.mines || []);

    stage.innerHTML = `
        <div class="mines-grid" id="mines-grid">
            ${Array.from({ length: 25 }, (_, i) => {
                let cls = 'mine-tile';
                let face = '·';
                if (round.hit === i) { cls += ' boom done'; face = '💣'; }
                else if (picked.has(i)) { cls += ' safe done'; face = '◆'; }
                else if (!live && mines.has(i)) { cls += ' boom revealed done'; face = '💣'; }
                else if (!live) { cls += ' revealed done'; }
                return `<div class="${cls}" data-tile="${i}"
                             ${live && !picked.has(i) ? `onclick="pickTile(${i})"` : ''}>${face}</div>`;
            }).join('')}
        </div>
        <div class="mines-meta">
            <div class="mines-stat"><span class="eyebrow">Safe</span>
                <span class="mines-stat-val">${round.picks}</span></div>
            <div class="mines-stat"><span class="eyebrow">Holding</span>
                <span class="mines-stat-val">${(round.multiplier || 1).toFixed(2)}x</span></div>
            <div class="mines-stat"><span class="eyebrow">Next</span>
                <span class="mines-stat-val">${round.next_multiplier
                    ? round.next_multiplier.toFixed(2) + 'x' : '–'}</span></div>
        </div>`;

    // Only cashing out is possible, and only once something is turned over.
    renderActions(live && round.picks > 0 ? ['cashout'] : []);
}

function pickTile(tile) {
    if (tableBusy || !activeRound) return;
    const el = document.querySelector(`.mine-tile[data-tile="${tile}"]`);
    if (el && motionOn()) el.classList.add('pop');
    tableAct('pick', 0, tile);
}

function adjustMines(dir) {
    const next = Math.max(1, Math.min(24, mineCount + dir));
    if (next === mineCount) { SFX.button(); return; }
    mineCount = next;
    SFX.chip();
    const el = document.getElementById('mines-count');
    if (el) el.textContent = mineCount;
    const lbl = document.getElementById('mines-picker-count');
    if (lbl) lbl.textContent = mineCount + ' mines';
}

function setCrashTarget(t) {
    crashTarget = t;
    SFX.chip();
    document.querySelectorAll('#table-bet-picker .bet-option').forEach(el => {
        el.classList.toggle('selected',
            (t === null && el.dataset.bet === 'manual') ||
            (t !== null && Number(el.dataset.bet) === t));
    });
}
