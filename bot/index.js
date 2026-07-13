/**
 * AutoWpp 2 — WhatsApp bot (whatsapp-web.js)
 *
 * Usage:
 *   node bot/index.js <accountId> auth               # authenticate (QR) and exit
 *   node bot/index.js <accountId> send [contacts]    # send assigned contacts and exit
 *
 * Design notes:
 * - Each account is an isolated process with its own LocalAuth session
 *   (.wwebjs_auth/session-<accountId>).
 * - The bot NEVER writes contacts.json directly. Every state change is
 *   appended to runtime/updates_<accountId>.jsonl. The Python orchestrator
 *   merges those files into contacts.json — this removes every JSON write
 *   race the old project had.
 * - QR codes are written to runtime/qr_<accountId>.txt (raw string) so the
 *   Gradio frontend can render them, and also printed to the terminal.
 * - Status is published to runtime/status_<accountId>.json.
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcodeTerminal = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');
const axios = require('axios');

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
const ROOT = path.resolve(__dirname, '..');
const RUNTIME_DIR = path.join(ROOT, 'runtime');

function loadEnv() {
    const envPath = path.join(ROOT, '.env');
    if (!fs.existsSync(envPath)) return;
    for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const sep = trimmed.indexOf('=');
        if (sep === -1) continue;
        const key = trimmed.slice(0, sep).trim();
        const value = trimmed.slice(sep + 1).trim();
        if (!process.env[key]) process.env[key] = value;
    }
}
loadEnv();

const envInt = (key, fallback) => {
    const value = parseInt(process.env[key], 10);
    return Number.isFinite(value) ? value : fallback;
};

const CONFIG = {
    minDelayMs: envInt('MIN_SEND_DELAY_MS', 20000),
    maxDelayMs: envInt('MAX_SEND_DELAY_MS', 45000),
    ackGraceMs: envInt('ACK_GRACE_MS', 30000),
    inactivityTimeoutMs: envInt('SEND_INACTIVITY_TIMEOUT_MS', 300000),
    validateNumbers: String(process.env.VALIDATE_NUMBERS || 'True').toLowerCase() !== 'false',
};

// ---------------------------------------------------------------------------
// CLI arguments
// ---------------------------------------------------------------------------
const accountId = process.argv[2];
const mode = (process.argv[3] || 'send').toLowerCase();
const contactsFile = process.argv[4] || 'contacts.json';

if (!accountId || !['auth', 'send', 'logout'].includes(mode)) {
    console.error('Usage: node bot/index.js <accountId> <auth|send|logout> [contactsFile]');
    process.exit(2);
}

const contactsPath = path.join(ROOT, contactsFile);
const statusPath = path.join(RUNTIME_DIR, `status_${accountId}.json`);
const qrPath = path.join(RUNTIME_DIR, `qr_${accountId}.txt`);
const updatesPath = path.join(RUNTIME_DIR, `updates_${accountId}.jsonl`);

fs.mkdirSync(RUNTIME_DIR, { recursive: true });

// ---------------------------------------------------------------------------
// Runtime file helpers
// ---------------------------------------------------------------------------
const log = (...args) => console.log(`[${accountId}]`, ...args);
const logError = (...args) => console.error(`[${accountId}]`, ...args);

let counters = { total: 0, sent: 0, failed: 0 };
let lastActivity = Date.now();

function publishStatus(state, extra = {}) {
    const payload = {
        account: accountId,
        mode,
        state,
        ...counters,
        ...extra,
        updatedAt: new Date().toISOString(),
    };
    try {
        fs.writeFileSync(statusPath, JSON.stringify(payload, null, 2), 'utf8');
    } catch (error) {
        logError('Failed to write status file:', error.message);
    }
}

function appendUpdate(update) {
    // Append-only JSONL: safe under concurrency, merged later by Python.
    const line = JSON.stringify({ account: accountId, at: new Date().toISOString(), ...update });
    fs.appendFileSync(updatesPath, line + '\n', 'utf8');
}

function clearQrFile() {
    try { if (fs.existsSync(qrPath)) fs.unlinkSync(qrPath); } catch (_) { /* noop */ }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const randomDelay = () =>
    CONFIG.minDelayMs + Math.floor(Math.random() * Math.max(1, CONFIG.maxDelayMs - CONFIG.minDelayMs));

// ---------------------------------------------------------------------------
// Optional external error reporting
// ---------------------------------------------------------------------------
async function reportError(phone) {
    const url = process.env.ERROR_REPORT_URL;
    if (!url) return;
    try {
        const headers = { 'Content-Type': 'application/json' };
        if (process.env.ERROR_REPORT_AUTH_TOKEN) {
            headers.Authorization = `Bearer ${process.env.ERROR_REPORT_AUTH_TOKEN}`;
        }
        if (process.env.ERROR_REPORT_HEADER_KEY && process.env.ERROR_REPORT_HEADER_VALUE) {
            headers[process.env.ERROR_REPORT_HEADER_KEY] = process.env.ERROR_REPORT_HEADER_VALUE;
        }
        await axios.post(url, { data: phone, exdata: new Date().toISOString().split('T')[0] }, { headers, timeout: 15000 });
        log(`Error reported to endpoint for ${phone}`);
    } catch (error) {
        logError('Failed to report error:', error.message);
    }
}

// ---------------------------------------------------------------------------
// Contacts
// ---------------------------------------------------------------------------
function loadMyPendingContacts() {
    const raw = fs.readFileSync(contactsPath, 'utf8');
    const all = JSON.parse(raw);
    return all.filter((c) => c.sentBy === accountId && c.sent === false && !c.error);
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------
const client = new Client({
    authStrategy: new LocalAuth({ clientId: accountId, dataPath: path.join(ROOT, '.wwebjs_auth') }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
        ],
    },
});

// msgId -> phone, so ACK events can be attributed even after send loop moves on
const pendingAcks = new Map();

client.on('qr', (qr) => {
    if (mode === 'logout') {
        // No stored session -> nothing to log out from.
        log('No active session for this account — nothing to log out.');
        publishStatus('logged_out');
        shutdown(0);
        return;
    }
    fs.writeFileSync(qrPath, qr, 'utf8');
    publishStatus('qr');
    log('QR code generated — scan it in WhatsApp > Linked devices');
    qrcodeTerminal.generate(qr, { small: true });
});

client.on('authenticated', () => {
    clearQrFile();
    publishStatus('authenticated');
    log('Authenticated.');
});

client.on('auth_failure', (msg) => {
    publishStatus('error', { error: `auth_failure: ${msg}` });
    logError('Authentication failed:', msg);
    process.exit(1);
});

client.on('disconnected', (reason) => {
    if (mode === 'logout') {
        // Expected: logout() disconnects the session.
        publishStatus('logged_out');
        process.exit(0);
    }
    publishStatus('error', { error: `disconnected: ${reason}` });
    logError('Disconnected:', reason);
    process.exit(1);
});

client.on('message_ack', (msg, ack) => {
    // ack: 1 = sent (server), 2 = delivered, 3 = read, 4 = played
    const phone = pendingAcks.get(msg.id?._serialized);
    if (!phone) return;
    lastActivity = Date.now();
    appendUpdate({
        phone,
        ackLevel: ack,
        delivered: ack >= 2,
        deliveredAt: ack >= 2 ? new Date().toISOString() : null,
    });
});

// ---------------------------------------------------------------------------
// Send flow
// ---------------------------------------------------------------------------
async function resolveChatId(phone) {
    const bare = phone.replace(/\D/g, '');
    if (!CONFIG.validateNumbers) return `${bare}@c.us`;
    const numberId = await client.getNumberId(bare);
    if (!numberId) return null; // number is not on WhatsApp
    return numberId._serialized;
}

function composeMessage(contact) {
    const base = String(contact.message || '').trim();
    const url = String(contact.buttonUrl || '').trim();
    return url ? `${base}\n\n${url}` : base;
}

async function runSend() {
    const myContacts = loadMyPendingContacts();
    counters.total = myContacts.length;
    publishStatus('sending');
    log(`${myContacts.length} contacts assigned to this account.`);

    if (myContacts.length === 0) {
        publishStatus('done');
        await shutdown(0);
        return;
    }

    for (const contact of myContacts) {
        lastActivity = Date.now();
        try {
            const chatId = await resolveChatId(contact.phone);
            if (!chatId) {
                throw new Error('Number is not registered on WhatsApp');
            }

            log(`Sending to ${contact.phone}...`);
            const msg = await client.sendMessage(chatId, composeMessage(contact));
            if (msg?.id?._serialized) pendingAcks.set(msg.id._serialized, contact.phone);

            counters.sent += 1;
            appendUpdate({ phone: contact.phone, sent: true, sentAt: new Date().toISOString(), error: null });
            log(`Sent to ${contact.phone} (${counters.sent}/${counters.total})`);
        } catch (error) {
            counters.failed += 1;
            const message = error?.message || String(error);
            appendUpdate({
                phone: contact.phone,
                sent: false,
                sentAt: `ERROR: ${new Date().toISOString()}`,
                error: message.slice(0, 300),
            });
            logError(`Failed to send to ${contact.phone}:`, message);
            await reportError(contact.phone);
        }

        publishStatus('sending');
        const delay = randomDelay();
        log(`Waiting ${(delay / 1000).toFixed(1)}s before next message...`);
        await sleep(delay);
    }

    // Give WhatsApp a moment to deliver ACKs before exiting.
    log(`Waiting ${CONFIG.ackGraceMs / 1000}s for delivery ACKs...`);
    publishStatus('waiting_acks');
    await sleep(CONFIG.ackGraceMs);

    publishStatus('done');
    log('Finished. Exiting.');
    await shutdown(0);
}

async function shutdown(code) {
    try { await client.destroy(); } catch (_) { /* noop */ }
    process.exit(code);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
client.on('ready', async () => {
    clearQrFile();
    log('Client ready.');
    if (mode === 'auth') {
        publishStatus('ready');
        await shutdown(0);
        return;
    }
    if (mode === 'logout') {
        try {
            await client.logout(); // unlinks the device on WhatsApp's side
            log('Logged out — device unlinked from WhatsApp.');
        } catch (error) {
            logError('Logout call failed (session will be removed locally anyway):', error?.message || error);
        }
        publishStatus('logged_out');
        await shutdown(0);
        return;
    }
    try {
        publishStatus('ready');
        await runSend();
    } catch (error) {
        publishStatus('error', { error: error?.message || String(error) });
        logError('Fatal error during send:', error);
        await shutdown(1);
    }
});

// Inactivity watchdog (send mode only)
if (mode === 'send' && CONFIG.inactivityTimeoutMs > 0) {
    setInterval(() => {
        if (Date.now() - lastActivity > CONFIG.inactivityTimeoutMs) {
            publishStatus('error', { error: 'inactivity timeout' });
            logError('Inactivity timeout — exiting.');
            process.exit(1);
        }
    }, 10000).unref();
}

function isRetryableInitError(error) {
    const message = String(error?.message || error || '');
    return message.includes('Execution context was destroyed')
        || message.includes('Protocol error');
}

(async function initializeWithRetry(maxAttempts = 3) {
    publishStatus('starting');
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            await client.initialize();
            return;
        } catch (error) {
            logError(`Initialization failed (${attempt}/${maxAttempts}):`, error?.message || error);
            if (!isRetryableInitError(error) || attempt === maxAttempts) {
                publishStatus('error', { error: error?.message || String(error) });
                process.exit(1);
            }
            try { await client.destroy(); } catch (_) { /* noop */ }
            await sleep(2000 * attempt);
        }
    }
})();
