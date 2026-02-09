const { Client, LocalAuth, Events } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const { google } = require('googleapis');

function loadEnv() {
    const envPath = path.join(__dirname, '.env');
    if (!fs.existsSync(envPath)) return;
    const contents = fs.readFileSync(envPath, 'utf8');
    contents.split('\n').forEach((line) => {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) return;
        const separatorIndex = trimmed.indexOf('=');
        if (separatorIndex === -1) return;
        const key = trimmed.slice(0, separatorIndex).trim();
        const value = trimmed.slice(separatorIndex + 1).trim();
        if (!process.env[key]) {
            process.env[key] = value;
        }
    });
}

function requireEnv(key) {
    const value = process.env[key];
    if (!value) {
        throw new Error(`Missing required environment variable: ${key}`);
    }
    return value;
}

loadEnv();

// Get account info from command line arguments
const accountId = process.argv[2] || 'default';
const contactsFile = process.argv[3] || 'contacts.json';
const mode = process.argv[4] || 'persistent'; // 'persistent' or 'oneshot'

const isOneShotMode = mode === 'oneshot';

console.log(`╔════════════════════════════════════════════╗`);
console.log(`║  Mode:       ${isOneShotMode ? 'ONE-SHOT (send and exit)' : 'PERSISTENT (with auto-reply)'.padEnd(30)} ║`);
console.log(`║  Account ID: ${accountId.padEnd(29)} ║`);
console.log(`║  Contacts:   ${contactsFile.padEnd(29)} ║`);
console.log(`╚════════════════════════════════════════════╝\n`);

// Load contacts
const contactsPath = path.join(__dirname, contactsFile);
let contacts = [];

const SHEET_ID = requireEnv('GOOGLE_SHEET_ID');
const SHEET_RANGE = requireEnv('GOOGLE_SHEET_RANGE');
const TOKEN_PATH = path.join(__dirname, 'token.json');
const CREDENTIALS_PATH = path.join(__dirname, 'Tetrakey.json');

// Error reporting configuration
const ERROR_REPORT_URL = requireEnv('ERROR_REPORT_URL');
const ERROR_REPORT_AUTH_TOKEN = requireEnv('ERROR_REPORT_AUTH_TOKEN');
const ERROR_REPORT_HEADER_KEY = requireEnv('ERROR_REPORT_HEADER_KEY');
const ERROR_REPORT_HEADER_VALUE = requireEnv('ERROR_REPORT_HEADER_VALUE');

async function reportError(phone) {
    const today = new Date().toISOString().split('T')[0];
    try {
        await axios.post(ERROR_REPORT_URL, {
            data: phone,
            exdata: today
        }, {
            headers: {
                [ERROR_REPORT_HEADER_KEY]: ERROR_REPORT_HEADER_VALUE,
                'Authorization': `Bearer ${ERROR_REPORT_AUTH_TOKEN}`,
                'Content-Type': 'application/json'
            }
        });
        console.log(`[${accountId}] 📡 Error reported for ${phone}`);
    } catch (reportError) {
        console.error(`[${accountId}] ⚠️  Failed to report error:`, reportError.message);
    }
}

function loadContacts() {
    const maxRetries = 3;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const contactsData = fs.readFileSync(contactsPath, 'utf8');
            return JSON.parse(contactsData);
        } catch (error) {
            if (attempt === maxRetries - 1) throw error;
            const delay = 100 * (attempt + 1);
            const start = Date.now();
            while (Date.now() - start < delay) {}
        }
    }
}

// UPDATED: Enhanced error logging with delivery tracking
function markContactAsSent(phoneNumber, success = true, error = null) {
    const maxRetries = 5;
    const retryDelay = 100;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const currentContacts = loadContacts();
            const contactIndex = currentContacts.findIndex(c => c.phone === phoneNumber);
            if (contactIndex === -1) return false;

            // Verify assignment
            if (currentContacts[contactIndex].sentBy && currentContacts[contactIndex].sentBy !== accountId) {
                console.error(`[${accountId}] ⚠️  Contact ${phoneNumber} is assigned to ${currentContacts[contactIndex].sentBy}, skipping.`);
                return false;
            }

            currentContacts[contactIndex].sent = success;
            currentContacts[contactIndex].sentBy = accountId;

            if (success) {
                currentContacts[contactIndex].sentAt = new Date().toISOString();
            } else {
                // Extract Error Code
                const errorCode = error?.statusCode || error?.status || 500;

                // Extract Error Description (Message)
                // Replace pipes '|' with dashes '-' to avoid breaking the format
                const errorMessage = (error?.message || "Unknown Error").replace(/\|/g, '-');

                const timestamp = new Date().toISOString();

                // Format: ERROR | Code | Description | Timestamp
                currentContacts[contactIndex].sentAt = `ERROR | ${errorCode} | ${errorMessage} | ${timestamp}`;
            }

            const tempPath = contactsPath + '.tmp';
            fs.writeFileSync(tempPath, JSON.stringify(currentContacts, null, 2), 'utf8');
            fs.renameSync(tempPath, contactsPath);

            return true;
        } catch (error) {
            if (attempt === maxRetries - 1) return false;
            const jitter = Math.random() * retryDelay;
            const delay = retryDelay * (attempt + 1) + jitter;
            const start = Date.now();
            while (Date.now() - start < delay) {}
        }
    }
    return false;
}

// NEW: Mark contact as delivered
function markContactAsDelivered(phoneNumber, ackLevel) {
    const maxRetries = 5;
    const retryDelay = 100;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const currentContacts = loadContacts();
            const contactIndex = currentContacts.findIndex(c => c.phone === phoneNumber);
            if (contactIndex === -1) return false;

            // Verify assignment
            if (currentContacts[contactIndex].sentBy && currentContacts[contactIndex].sentBy !== accountId) {
                return false;
            }

            // Only update if message was sent successfully
            if (!currentContacts[contactIndex].sent) {
                return false;
            }

            // Update delivery status
            currentContacts[contactIndex].delivered = true;
            currentContacts[contactIndex].deliveredAt = new Date().toISOString();
            currentContacts[contactIndex].ackLevel = ackLevel; // 2=delivered, 3=read, 4=played

            const tempPath = contactsPath + '.tmp';
            fs.writeFileSync(tempPath, JSON.stringify(currentContacts, null, 2), 'utf8');
            fs.renameSync(tempPath, contactsPath);

            return true;
        } catch (error) {
            if (attempt === maxRetries - 1) return false;
            const jitter = Math.random() * retryDelay;
            const delay = retryDelay * (attempt + 1) + jitter;
            const start = Date.now();
            while (Date.now() - start < delay) {}
        }
    }
    return false;
}

function randomBetween(min, max) {
    return Math.random() * (max - min) + min;
}

function waitForDelivery(client, message, timeoutMs = 30000) {
    return new Promise((resolve) => {
        let settled = false;

        const cleanup = () => {
            if (settled) return;
            settled = true;
            client.off(Events.MESSAGE_ACK, handler);
            clearTimeout(timer);
        };

        const handler = (msg, ack) => {
            if (msg?.id?._serialized === message?.id?._serialized) {
                cleanup();
                resolve(ack);
            }
        };

        const timer = setTimeout(() => {
            cleanup();
            resolve(null);
        }, timeoutMs);

        client.on(Events.MESSAGE_ACK, handler);
    });
}

let sheetsClientPromise;

async function getSheetsClient() {
    if (!sheetsClientPromise) {
        sheetsClientPromise = (async () => {
            const credentials = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, 'utf8'));
            const token = JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8'));
            const { client_id, client_secret, redirect_uris } = credentials.installed || credentials.web;
            const auth = new google.auth.OAuth2(client_id, client_secret, redirect_uris[0]);
            auth.setCredentials(token);
            return google.sheets({ version: 'v4', auth });
        })();
    }
    return sheetsClientPromise;
}

async function appendLeadToSheet(phoneNumber, cpf, email) {
    try {
        const sheets = await getSheetsClient();
        const now = new Date();
        const pad = (value) => String(value).padStart(2, '0');
        const timestamp = `${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${String(now.getFullYear()).slice(-2)} - ${pad(now.getHours())}:${pad(now.getMinutes())}`;
        await sheets.spreadsheets.values.append({
            spreadsheetId: SHEET_ID,
            range: SHEET_RANGE,
            valueInputOption: 'USER_ENTERED',
            requestBody: {
                values: [[phoneNumber, cpf, email, timestamp]]
            }
        });
        console.log(`[${accountId}] ✅ Lead appended for ${phoneNumber}`);
        await reportSuccess(phoneNumber, cpf, timestamp);
    } catch (error) {
        console.error(`[${accountId}] ❌ Failed to append lead:`, error.message);
    }
}

const SUCCESS_REPORT_URL = requireEnv('SUCCESS_REPORT_URL');
const SUCCESS_REPORT_HEADER_KEY = requireEnv('SUCCESS_REPORT_HEADER_KEY');
const SUCCESS_REPORT_HEADER_VALUE = requireEnv('SUCCESS_REPORT_HEADER_VALUE');

async function reportSuccess(phoneNumber, cpf, timestamp) {
    try {
        await axios.post(SUCCESS_REPORT_URL, {
            telefone: phoneNumber,
            cpf_cnpj: cpf,
            time: timestamp
        }, {
            headers: {
                [SUCCESS_REPORT_HEADER_KEY]: SUCCESS_REPORT_HEADER_VALUE,
                'Content-Type': 'application/json'
            }
        });
        console.log(`[${accountId}] 📡 Success reported for ${phoneNumber}`);
    } catch (error) {
        console.error(`[${accountId}] ⚠️  Failed to report success:`, error.message);
    }
}

async function resolvePhoneNumber(client, from) {
    if (!from) return null;
    if (from.endsWith('@c.us')) return from.replace('@c.us', '');
    if (from.endsWith('@lid')) {
        const results = await client.getContactLidAndPhone([from]);
        const phone = results?.[0]?.pn ?? null;
        return phone ? phone.replace('@c.us', '') : null;
    }
    return null;
}

// NEW: Helper function to convert phone number to match format
function normalizePhoneForMatching(phone) {
    // Remove all non-digits and add + prefix if not present
    const digits = phone.replace(/\D/g, '');
    return '+' + digits;
}

// NEW: Helper to get ack level description
function ackLevelToText(ack) {
    const levels = {
        [-1]: 'pending (server not received)',
        0:  'pending',
        1:  'sent (gray ✓✓)',
        2:  'delivered (blue ✓✓)',
        3:  'read (blue ✓✓✓)',
        4:  'played (voice/video)'
    };
    return levels[ack] || `unknown (${ack})`;
}

try {
    contacts = loadContacts();
    console.log(`[${accountId}] 📋 Loaded ${contacts.length} contacts from ${contactsFile}`);
} catch (error) {
    console.error(`[${accountId}] ❌ Error loading ${contactsFile}:`, error.message);
    if (isOneShotMode) process.exit(1);
    contacts = [];
}

const client = new Client({
    authStrategy: new LocalAuth({ clientId: accountId }),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

client.on('qr', (qr) => {
    console.log(`\n[${accountId}] 📱 Scan this QR code with WhatsApp:`);
    qrcode.generate(qr, { small: true });
});

async function sendMessagesAndExit() {
    console.log(`\n[${accountId}] 🚀 Starting one-shot sender...\n`);
    const currentContacts = loadContacts();
    const myUnsentContacts = currentContacts.filter(c => {
        if (c.sentBy) return c.sentBy === accountId && c.sent === false;
        return c.sent === false;
    });

    if (myUnsentContacts.length === 0) {
        console.log(`[${accountId}] ℹ️  No messages assigned. Exiting.`);
        await client.destroy();
        process.exit(0);
    }

    for (const contact of myUnsentContacts) {
        try {
            const chatId = contact.phone.replace('+', '') + '@c.us';
            console.log(`[${accountId}] 📤 Sending to ${contact.phone}...`);
            const sentMessage = await client.sendMessage(chatId, contact.message);
            markContactAsSent(contact.phone, true);
            const ack = await waitForDelivery(client, sentMessage);
            if (ack !== null && ack >= 2) {
                markContactAsDelivered(contact.phone, ack);
            }
        } catch (error) {
            console.error(`[${accountId}] ❌ Error sending to ${contact.phone}:`, error.message);
            await reportError(contact.phone);
            // Passing error object for logging
            markContactAsSent(contact.phone, false, error);
        }
    }
    await client.destroy();
    process.exit(0);
}

async function sendMessagesAndStayAlive() {
    console.log(`\n[${accountId}] 🚀 Starting to send messages...\n`);
    const currentContacts = loadContacts();

    const myUnsentContacts = currentContacts.filter(c => {
        if (c.sentBy) return c.sentBy === accountId && c.sent === false;
        return false;
    });

    if (myUnsentContacts.length === 0) {
        console.log(`[${accountId}] ℹ️  No unsent messages assigned to this account.`);
        console.log(`[${accountId}] Bot is running and will auto-reply to incoming messages\n`);
        return;
    }

    console.log(`[${accountId}] 📊 Found ${myUnsentContacts.length} unsent contact(s) assigned to this account\n`);

    for (let i = 0; i < myUnsentContacts.length; i++) {
        const contact = myUnsentContacts[i];
        try {
            const chatId = contact.phone.replace('+', '') + '@c.us';
            console.log(`[${accountId}] 📤 Sending to ${contact.phone}...`);
            const sentMessage = await client.sendMessage(chatId, contact.message);
            markContactAsSent(contact.phone, true);
            const ack = await waitForDelivery(client, sentMessage);
            if (ack !== null && ack >= 2) {
                markContactAsDelivered(contact.phone, ack);
            }

            if (i < myUnsentContacts.length - 1) {
                const delay = contact.delay || 2000;
                const jitter = Math.floor(randomBetween(1000, 5000));
                await new Promise(resolve => setTimeout(resolve, delay + jitter));
            }
        } catch (error) {
            console.error(`[${accountId}] ❌ Error sending to ${contact.phone}:`, error.message);
            await reportError(contact.phone);
            // Passing error object for logging
            markContactAsSent(contact.phone, false, error);
        }
    }
    console.log(`\n[${accountId}] ✅ All assigned messages sent! Listening for replies...\n`);
}

client.on('ready', async () => {
    console.log(`\n[${accountId}] ✅ Client is ready!\n`);
    try {
        if (isOneShotMode) await sendMessagesAndExit();
        else await sendMessagesAndStayAlive();
    } catch (error) {
        console.error(`[${accountId}] ❌ Error in ready handler:`, error);
        if (isOneShotMode) process.exit(1);
    }
});

// NEW: Listen for message acknowledgment (delivery confirmation)
client.on(Events.MESSAGE_ACK, (msg, ack) => {
    try {
        // Only process if ack is 2 (delivered) or higher
        if (ack >= 2) {
            // Extract phone number from message
            const chatId = msg.to || msg.from;
            let phoneNumber = chatId.replace('@c.us', '');
            
            // Add + prefix if not present
            if (!phoneNumber.startsWith('+')) {
                phoneNumber = '+' + phoneNumber;
            }

            // Try to find and mark contact as delivered
            const success = markContactAsDelivered(phoneNumber, ack);
            
            if (success) {
                console.log(`[${accountId}] ✓✓ Delivery confirmed for ${phoneNumber} → ${ackLevelToText(ack)}`);
            }
        }
    } catch (error) {
        console.error(`[${accountId}] ❌ Error processing MESSAGE_ACK:`, error.message);
    }
});

client.on('authenticated', () => console.log(`[${accountId}] ✅ Authenticated successfully!`));

client.on('disconnected', (reason) => console.log(`[${accountId}] Disconnected:`, reason));

if (!isOneShotMode) {
    const leadCapture = new Map();
    const MAX_WRONG_ANSWERS = 3;

    function getLeadState(chatId) {
        if (!leadCapture.has(chatId)) {
            leadCapture.set(chatId, { step: 'cpf', cpf: null, email: null, invalidCpfAttempts: 0, postCompletionReplies: 0, blocked: false });
        }
        return leadCapture.get(chatId);
    }

    function extractDocumentNumber(value) {
        if (!value) return null;
        const digits = value.replace(/\D/g, '');
        if (digits.length === 11 || digits.length === 14) return digits;
        return null;
    }

    client.on('message_create', async (message) => {
        if (!message.fromMe && message.body) {
            try {
                const replyDelayMs = randomBetween(1000, 3000);
                await new Promise(resolve => setTimeout(resolve, replyDelayMs));
                const state = getLeadState(message.from);
                const text = message.body.trim();

                if (state.blocked) return;

                if (state.step === 'cpf') {
                    const docNum = extractDocumentNumber(text);
                    if (!state.cpf && docNum) {
                        state.cpf = docNum;
                        state.step = 'email';
                        await client.sendMessage(message.from, 'Obrigado! Agora informe seu e-mail, por gentiliza:');
                    } else if (!state.cpf) {
                        state.invalidCpfAttempts += 1;
                        if (state.invalidCpfAttempts > MAX_WRONG_ANSWERS) { state.blocked = true; return; }
                        await client.sendMessage(message.from, 'Por gentileza informe seu CPF ou CNPJ.');
                    } else {
                        await client.sendMessage(message.from, 'Estamos aguardando seu e-mail.');
                    }
                } else if (state.step === 'email') {
                    if (!state.email) {
                        state.email = text;
                        state.step = 'done';
                        const phoneNumber = await resolvePhoneNumber(client, message.from);
                        await appendLeadToSheet(phoneNumber, state.cpf, state.email);
                        await client.sendMessage(message.from, 'Obrigado! Um especialista entrará em contato em breve.');
                    } else {
                        await client.sendMessage(message.from, 'Sua requisição já foi enviada, em breve retornaremos com um de nossos advogados 😊');
                    }
                } else {
                    state.postCompletionReplies += 1;
                    if (state.postCompletionReplies > MAX_WRONG_ANSWERS) { state.blocked = true; return; }
                    await client.sendMessage(message.from, 'Sua requisição já foi enviada, em breve retornaremos com um de nossos advogados 😊');
                }
            } catch (error) {
                console.error(`[${accountId}] ❌ Error replying:`, error.message);
            }
        }
    });
}

console.log(`[${accountId}] 🚀 Starting WhatsApp client...\n`);
client.initialize();

if (isOneShotMode) {
    setTimeout(() => { console.error(`[${accountId}] ⏱️  Timeout (60s)`); process.exit(1); }, 60000);
}

process.on('SIGINT', async () => {
    console.log(`\n[${accountId}] Shutting down...`);
    await client.destroy();
    process.exit(0);
});
