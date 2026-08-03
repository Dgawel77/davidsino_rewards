// Davidsino Rewards — table sound, synthesised in the browser.
//
// Every sound here is generated with the Web Audio API. No files to download,
// nothing to 404, and the whole thing weighs nothing. Browsers refuse to start
// audio before a user gesture, so the context is created lazily on the first
// click and resumed if it was suspended.

const SFX_KEY = 'davidsino.sound';
const MOTION_KEY = 'davidsino.motion';

let audioCtx = null;

function soundOn() {
    try {
        const v = localStorage.getItem(SFX_KEY);
        return v === null ? true : v === '1';     // on unless turned off
    } catch (e) { return true; }
}

function setSoundOn(on) {
    try { localStorage.setItem(SFX_KEY, on ? '1' : '0'); } catch (e) { /* private mode */ }
}

// Animations default to off when the player's OS asks for reduced motion.
function motionOn() {
    try {
        const v = localStorage.getItem(MOTION_KEY);
        if (v !== null) return v === '1';
    } catch (e) { /* private mode */ }
    return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function setMotionOn(on) {
    try { localStorage.setItem(MOTION_KEY, on ? '1' : '0'); } catch (e) { /* private mode */ }
}

function ctx() {
    if (!soundOn()) return null;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!audioCtx) audioCtx = new AC();
    // Autoplay policy parks the context until a gesture unlocks it.
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
}

// A plain tone with an exponential tail — the building block for most cues.
function tone(freq, when, dur, type = 'sine', gain = 0.14) {
    const ac = ctx();
    if (!ac) return;
    const t = ac.currentTime + when;
    const osc = ac.createOscillator();
    const amp = ac.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    amp.gain.setValueAtTime(0.0001, t);
    amp.gain.exponentialRampToValueAtTime(gain, t + 0.012);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(amp).connect(ac.destination);
    osc.start(t);
    osc.stop(t + dur + 0.02);
}

// Filtered noise — the papery part of a card landing, and the bust thud.
function noise(when, dur, freq, q, gain = 0.2) {
    const ac = ctx();
    if (!ac) return;
    const t = ac.currentTime + when;
    const frames = Math.max(1, Math.floor(ac.sampleRate * dur));
    const buf = ac.createBuffer(1, frames, ac.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < frames; i++) {
        // Fade the noise out across the buffer so it lands rather than clicks off.
        data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
    }
    const src = ac.createBufferSource();
    src.buffer = buf;
    const filter = ac.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = freq;
    filter.Q.value = q;
    const amp = ac.createGain();
    amp.gain.setValueAtTime(gain, t);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(filter).connect(amp).connect(ac.destination);
    src.start(t);
    src.stop(t + dur);
}

const SFX = {
    // A card skimming onto the felt.
    deal(delay = 0) {
        noise(delay, 0.13, 1900, 1.1, 0.16);
        tone(320, delay + 0.02, 0.07, 'triangle', 0.05);
    },
    // The hole card turning over — same paper, lower and slower.
    flip(delay = 0) {
        noise(delay, 0.17, 1200, 0.9, 0.18);
        tone(210, delay + 0.03, 0.1, 'triangle', 0.06);
    },
    // Clay on clay.
    chip(delay = 0) {
        noise(delay, 0.05, 2600, 3.5, 0.14);
        tone(900, delay, 0.05, 'square', 0.03);
    },
    button() { tone(520, 0, 0.05, 'triangle', 0.05); },

    win() {
        [523.25, 659.25, 783.99].forEach((f, i) => tone(f, i * 0.09, 0.34, 'sine', 0.13));
    },
    blackjack() {
        // A proper little fanfare — it should feel different from a plain win.
        [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => tone(f, i * 0.085, 0.45, 'sine', 0.14));
        tone(1318.5, 0.34, 0.55, 'triangle', 0.09);
    },
    lose() {
        tone(300, 0, 0.24, 'sine', 0.11);
        tone(220, 0.11, 0.32, 'sine', 0.1);
    },
    bust() {
        noise(0, 0.3, 160, 0.7, 0.26);
        tone(150, 0.02, 0.34, 'sawtooth', 0.09);
    },
    push() {
        tone(440, 0, 0.16, 'sine', 0.09);
        tone(440, 0.14, 0.2, 'sine', 0.07);
    },
};

// A top-level `const` lives in the global lexical scope, which other page
// scripts can see but external tooling cannot. Publish it explicitly so the
// sound API is reachable the same way the rest of the app's globals are.
window.SFX = SFX;
