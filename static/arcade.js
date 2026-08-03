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
let crashPoll = null;       // asks the server whether it has busted yet
let crashTrail = [];        // recent rocket positions, for the vapour trail
let plinkoRisk = 'medium';
let crashTarget = null;      // optional auto cash-out
let mineCount = 3;
let plinkoBalls_count = 1;   // how many to drop at once

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

    const prog = Math.log2(Math.max(mult, 1.001)) / Math.log2(top);
    const x = Math.max(6, Math.min(94, prog * 100));
    // Climb on a curve rather than a diagonal, so it reads as a launch.
    const y = Math.max(6, Math.min(90, Math.pow(prog, 0.72) * 100));

    if (flying) {
        crashTrail.push([x, y]);
        if (crashTrail.length > 26) crashTrail.shift();
    }
    // Older points are fainter and smaller — a vapour trail, not a dotted line.
    const trail = crashTrail.map(([tx, ty], i) => {
        const age = i / Math.max(1, crashTrail.length - 1);
        return `<div class="crash-trail" style="left:${tx}%; bottom:${ty}%;
                     opacity:${(age * 0.5).toFixed(3)};
                     transform:translate(-50%,50%) scale(${(0.35 + age * 0.65).toFixed(2)})"></div>`;
    }).join('');

    track.innerHTML = `${lines}${trail}
        <div class="crash-rocket ${gone ? 'gone' : 'lit'}"
             style="left:${x}%; bottom:${y}%">${gone ? '💥' : '🚀'}</div>`;
}

function startCrashClimb(round) {
    stopCrashClimb();
    crashRoundId = round.round_id;
    crashDouble = round.double_seconds || 4.5;
    // Trust the server's elapsed, not our own clock: it is the only one the
    // cash-out will be measured against.
    crashStartMs = Date.now() - (round.elapsed || 0) * 1000;
    crashTrail = [];

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

    // The client is not told the bust point -- that would give the game away --
    // so it asks. Without this the multiplier would keep climbing long after the
    // rocket was gone, and you could "cash out at 8x" only to be told you busted
    // at 2x. Polling is what makes it bust ON you.
    crashPoll = setInterval(async () => {
        if (!activeRound || activeRound.round_id !== crashRoundId) return stopCrashClimb();
        try {
            const resp = await api(
                `/api/tables/round/${crashRoundId}?card_id=${encodeURIComponent(currentCardId)}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.stage !== 'flying') {
                stopCrashClimb();
                updateTableBalance(data.reward_points, data.payout - data.wagered);
                renderRound(data);
            }
        } catch (e) { /* a dropped poll just means we ask again in 250ms */ }
    }, 250);
}

function stopCrashClimb() {
    if (crashTimer) cancelAnimationFrame(crashTimer);
    if (crashPoll) clearInterval(crashPoll);
    crashTimer = null;
    crashPoll = null;
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

function plinkoBuckets(hits) {
    const risks = (currentTable && currentTable.risks) || {};
    const table = (risks[plinkoRisk] && risks[plinkoRisk].table) || [];
    const best = Math.max(...table, 0);
    return table.map((m, i) => {
        const n = hits && hits[i];
        const cls = n ? 'landed' : (m >= best ? 'hot' : '');
        // With several balls in play, show how many finished in each bucket.
        const badge = n > 1 ? `<span class="bucket-n">${n}</span>` : '';
        return `<div class="plinko-bucket ${cls}">${m}x${badge}</div>`;
    }).join('');
}

// ===== Plinko physics =====
//
// The bucket is NOT decided here — the server already drew each ball's path from
// the seed. What this does is make the ball *travel* like a ball: gravity,
// bounce, a little squash on impact. Each row's peg deflects it left or right
// according to the server's path, so the physics is the presentation and the
// outcome underneath it is still the provably fair one.
const PLINKO_GRAVITY = 1650;      // px/s^2
const PLINKO_BOUNCE = 0.42;       // how much vertical speed survives a peg
const PLINKO_SPREAD = 0.62;       // sideways kick off a peg, as a fraction of pitch

let plinkoRunning = 0;
let plinkoFrame = null;
let plinkoBalls = [];

function plinkoGeometry(board, rows) {
    const w = board.clientWidth || 400;
    const h = board.clientHeight || 258;
    return { w, h, rows, pitch: (w * 0.9) / rows, rowH: h / rows };
}

function spawnPlinkoBall(board, geo, drop, index) {
    const el = document.createElement('div');
    el.className = 'plinko-ball';
    board.appendChild(el);
    return {
        el,
        drop,
        // Fan the launch a touch so simultaneous balls do not overlap exactly.
        x: geo.w / 2 + (index % 3 - 1) * 2.2,
        y: -8,
        vx: 0,
        vy: 0,
        row: 0,
        offset: 0,
        done: false,
    };
}

function stepPlinkoBall(b, geo, dt) {
    b.vy += PLINKO_GRAVITY * dt;
    b.x += b.vx * dt;
    b.y += b.vy * dt;

    // Has it reached the next peg row?
    const nextRowY = (b.row + 0.5) * geo.rowH;
    if (b.row < geo.rows && b.y >= nextRowY) {
        const dir = b.drop.path[b.row] === 'R' ? 1 : -1;
        b.offset += dir * 0.5;
        // Bounce: kill most of the fall speed, kick sideways toward the side the
        // server's path says this ball goes.
        b.vy = -Math.abs(b.vy) * PLINKO_BOUNCE;
        b.vx = dir * geo.pitch * PLINKO_SPREAD * (0.8 + (b.row % 3) * 0.1);
        b.y = nextRowY;
        b.row += 1;
        if (soundOn()) SFX.chip();
        b.el.classList.remove('squash');
        void b.el.offsetWidth;                 // restart the squash animation
        b.el.classList.add('squash');
    }

    // Steer gently toward where this ball must be by the end. Without this the
    // bounce noise would slowly drift it off its own column.
    const targetX = geo.w / 2 + b.offset * geo.pitch;
    b.x += (targetX - b.x) * Math.min(1, dt * 9);

    if (b.row >= geo.rows && b.y >= geo.h) {
        b.done = true;
        b.x = geo.w / 2 + b.offset * geo.pitch;
        b.y = geo.h;
    }
    b.el.style.left = b.x + 'px';
    b.el.style.top = b.y + 'px';
}

function animatePlinko(data) {
    const rows = (currentTable && currentTable.rows) || 12;
    const board = document.getElementById('plinko-board');
    if (!board) return;

    const drops = data.drops || [data];
    if (!motionOn()) {
        // No animation: just show where they landed.
        finishPlinko(drops);
        return;
    }

    const geo = plinkoGeometry(board, rows);
    drops.forEach((d, i) => {
        // Stagger the releases slightly so a handful reads as a handful.
        setTimeout(() => {
            plinkoBalls.push(spawnPlinkoBall(board, geo, d, i));
            plinkoRunning += 1;
            if (!plinkoFrame) runPlinko(board, geo, drops);
        }, i * 130);
    });
}

function runPlinko(board, geo, drops) {
    let last = null;
    const tick = (now) => {
        if (last === null) last = now;
        const dt = Math.min(0.032, (now - last) / 1000);
        last = now;

        for (const b of plinkoBalls) {
            if (!b.done) stepPlinkoBall(b, geo, dt);
        }
        const live = plinkoBalls.filter(b => !b.done);
        if (live.length || plinkoRunning > plinkoBalls.length) {
            plinkoFrame = requestAnimationFrame(tick);
            return;
        }
        // Everything has landed.
        plinkoFrame = null;
        plinkoBalls.forEach(b => b.el.remove());
        plinkoBalls = [];
        plinkoRunning = 0;
        finishPlinko(drops);
    };
    plinkoFrame = requestAnimationFrame(tick);
}

function finishPlinko(drops) {
    const el = document.getElementById('plinko-buckets');
    if (!el) return;
    const hits = {};
    for (const d of drops) hits[d.bucket] = (hits[d.bucket] || 0) + 1;
    el.innerHTML = plinkoBuckets(hits);
}

function stopPlinko() {
    if (plinkoFrame) cancelAnimationFrame(plinkoFrame);
    plinkoFrame = null;
    plinkoBalls.forEach(b => b.el.remove());
    plinkoBalls = [];
    plinkoRunning = 0;
}

function adjustBalls(dir) {
    const max = (currentTable && currentTable.max_balls) || 10;
    const next = Math.max(1, Math.min(max, plinkoBalls_count + dir));
    if (next === plinkoBalls_count) { SFX.button(); return; }
    plinkoBalls_count = next;
    SFX.chip();
    const el = document.getElementById('plinko-ball-count');
    if (el) el.textContent = plinkoBalls_count + (plinkoBalls_count === 1 ? ' ball' : ' balls');
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
