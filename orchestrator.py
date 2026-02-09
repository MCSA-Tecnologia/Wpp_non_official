"""
WhatsApp Multi-Account Orchestrator with Smart Coordinator
Default: Smart random account rotation (max 3 consecutive)
Only uses authenticated accounts
"""

import subprocess
import sys
import time
import os
import json
import random
import threading
from datetime import datetime
import pyodbc
import pandas as pd
from typing import Optional, Iterable, List
from pathlib import Path

import settings

# Ensure proper UTF-8 encoding for output
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Configuration for multiple accounts
ACCOUNTS = [
    {
        'id': 'account_1',
        'name': 'Account 1',
        'process': None,
        'authenticated': False,
        'ready': False,
        'consecutive_uses': 0
    },
    {
        'id': 'account_2',
        'name': 'Account 2',
        'process': None,
        'authenticated': False,
        'ready': False,
        'consecutive_uses': 0
    }
]

CONTACTS_FILE = 'contacts.json'
MAX_CONSECUTIVE_USES = 3
contacts_lock = threading.Lock()
authenticated_accounts = []


pending_contacts_df: Optional[pd.DataFrame] = None
contacts_json_built = False

def fetch_negociador_df() -> Optional[pd.DataFrame]:
    """Fetch negotiator data from the legacy database."""
    try:
        query_negociador = settings.QUERY_CLIENTS_PHONE
        if not query_negociador:
            print("⚠️  QUERY_CLIENTS_PHONE is empty. Skipping contacts generation.")
            return None

        conn = pyodbc.connect(
            'DRIVER={SQL Server};SERVER=' + settings.SERVER_OLD
            + ';DATABASE=' + settings.DATABASE_OLD
            + ';UID=' + settings.USERNAME_OLD
            + ';PWD=' + settings.PASSWORD_OLD
        )
        query_result = pd.read_sql_query(query_negociador, conn)
        conn.close()
        return query_result
    except Exception as e:
        print(f"Erro ao buscar dados do negociador: {e}")
        df = settings.df
        print(df.iloc[0])
        return df


def df_to_contacts_json(
    df: pd.DataFrame,
    message: str,
    output_path: str = "contacts.json",
    account_ids: Optional[Iterable[str]] = None,
) -> str:
    """
    Create contacts.json from a DataFrame.
    Alternates `sentBy` between authenticated accounts when provided.
    Now includes delivery tracking fields.
    """
    if "Telefone" not in df.columns:
        raise ValueError("DataFrame must contain a 'Telefone' column.")

    def normalize_phone_br(value) -> str:
        # Keep only digits
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return "+55"  # fallback (still valid string)

        # If it already includes country code 55, keep it; else add it
        if digits.startswith("55"):
            return f"+{digits}"
        return f"+55{digits}"

    normalized_accounts: List[str] = list(account_ids or authenticated_accounts)
    contacts = []
    for index, row in df.iterrows():
        sent_by = None
        if normalized_accounts:
            # Alternate between accounts
            sent_by = normalized_accounts[index % len(normalized_accounts)]
        contacts.append({
            "phone": normalize_phone_br(row["Telefone"]),
            "message": message,
            "delay": 30000,
            "sent": False,
            "sentBy": sent_by,
            "delivered": False,      # NEW: Track if message was delivered
            "deliveredAt": None,     # NEW: Timestamp when delivered
            "ackLevel": None,        # NEW: WhatsApp ack level (2=delivered, 3=read, 4=played)
            "sentAt": None
        })

    out = Path(output_path)
    out.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Created {output_path} with {len(contacts)} contacts")

    # Show distribution
    if normalized_accounts:
        distribution = {}
        for contact in contacts:
            account = contact['sentBy']
            distribution[account] = distribution.get(account, 0) + 1
        print(f"📊 Distribution by account:")
        for acc, count in distribution.items():
            print(f"   - {acc}: {count} contacts")

    return str(out)


def print_header():
    """Print orchestrator header"""
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║   WhatsApp Smart Orchestrator with Auto-Coordinator      ║")
    print("║   Random Load Balancing • Only Authenticated Accounts    ║")
    print("║   Now with Delivery Confirmation Tracking                ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

def load_contacts():
    """Load contacts from JSON file"""
    with contacts_lock:
        try:
            with open(CONTACTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading {CONTACTS_FILE}: {e}")
            return []

def save_contacts(contacts):
    """Save contacts to JSON file"""
    with contacts_lock:
        try:
            with open(CONTACTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(contacts, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"❌ Error saving {CONTACTS_FILE}: {e}")
            return False

def is_error_sent_at(value: Optional[str]) -> bool:
    """Return True if sentAt contains an error marker."""
    if not value:
        return False
    return str(value).startswith("ERROR")

def start_bot(account):
    """Start a bot instance for an account (Persistent Mode)"""
    try:
        # We start in persistent mode. 'index.js' will read contacts.json on ready.
        cmd = ['node', 'index.js', account['id'], CONTACTS_FILE]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace'
        )

        return process

    except Exception as e:
        print(f"❌ Error starting bot for {account['name']}: {e}")
        return None

def monitor_authentication(process, account):
    """Monitor process output for authentication status"""
    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                print(f"[{account['name']}] {line.rstrip()}")

                # Check for authentication success
                if 'Authenticated successfully' in line or 'Client is ready' in line:
                    if not account['authenticated']:
                        account['authenticated'] = True
                        account['ready'] = True
                        if account['id'] not in authenticated_accounts:
                            authenticated_accounts.append(account['id'])
                        print(f"\n✅ {account['name']} is now authenticated and ready!\n")

                # Check for QR code generation
                if 'QR RECEIVED' in line or 'Scan this QR code' in line:
                    print(f"\n📱 {account['name']}: Please scan the QR code above\n")

    except Exception as e:
        # Process likely died
        pass

def wait_for_all_authentication():
    """
    Wait until ALL started accounts are authenticated.
    Fixes the issue where the Orchestrator moves on too fast.
    """
    print("\n⏳ Waiting for ALL accounts to authenticate...")

    # Count how many accounts we actually started
    started_accounts = [acc for acc in ACCOUNTS if acc['process'] is not None]
    total_to_wait = len(started_accounts)

    if total_to_wait == 0:
        return False

    timeout = 120  # 2 minutes
    start_time = time.time()

    while time.time() - start_time < timeout:
        # Count how many are currently authenticated
        current_auth_count = sum(1 for acc in started_accounts if acc['authenticated'])

        if current_auth_count == total_to_wait:
            print(f"\n✅ All {total_to_wait} account(s) authenticated successfully!\n")
            return True

        # Sleep briefly before next check
        time.sleep(1)

    # If we get here, we timed out
    auth_accounts = [acc['name'] for acc in started_accounts if acc['authenticated']]
    unauth_accounts = [acc['name'] for acc in started_accounts if not acc['authenticated']]

    if auth_accounts:
        print(f"\n⚠️  Timeout: Only {len(auth_accounts)}/{total_to_wait} account(s) authenticated:")
        for name in auth_accounts:
            print(f"   ✅ {name}")
    if unauth_accounts:
        print(f"\n   The following accounts failed to authenticate:")
        for name in unauth_accounts:
            print(f"   ❌ {name}")

    # Decide if we want to proceed with partial authentication or fail completely
    # For now, let's require all accounts
    return len(auth_accounts) == total_to_wait

def check_files():
    """Check if required files exist"""
    if not os.path.exists('index.js'):
        print("❌ index.js not found!")
        return False
    return True

def build_contacts_json_final() -> bool:
    """
    After authentication, we know which accounts are authenticated.
    Generate contacts.json properly assigned to those accounts.
    """
    global contacts_json_built

    auth_ids = list(authenticated_accounts)  # IDs of authenticated accounts

    if not auth_ids:
        print("❌ No authenticated accounts available to assign contacts!")
        return False

    if pending_contacts_df is None or pending_contacts_df.empty:
        print("❌ No contacts to assign (pending_contacts_df is empty).")
        return False

    print(f"\n📋 Generating {CONTACTS_FILE} with delivery tracking fields...")
    print(f"   Total contacts to distribute: {len(pending_contacts_df)}")
    print(f"   Assigning to {len(auth_ids)} authenticated account(s).")

    default_message = getattr(settings.CONTACT_MESSAGE, 'DEFAULT_MESSAGE', settings.CONTACT_MESSAGE)

    try:
        df_to_contacts_json(
            df=pending_contacts_df,
            message=default_message,
            output_path=CONTACTS_FILE,
            account_ids=auth_ids
        )
        contacts_json_built = True
        print(f"✅ {CONTACTS_FILE} generated successfully!")
        return True
    except Exception as e:
        print(f"❌ Error generating contacts.json: {e}")
        return False

def stop_bots(accounts):
    """Gracefully stop all bot processes"""
    print("\n🛑 Stopping all bots...")
    for account in accounts:
        if account['process'] and account['process'].poll() is None:
            print(f"   Stopping {account['name']}...")
            account['process'].terminate()

    print("   Waiting for processes to shut down...")
    time.sleep(3) # Wait for graceful exit

    # Force kill if necessary
    for account in accounts:
        if account['process'] and account['process'].poll() is None:
            print(f"   Force killing {account['name']}...")
            account['process'].kill()

    # Reset process handles
    for account in accounts:
        account['process'] = None

    print("✅ All bots stopped.")

def monitor_and_commands(accounts):
    """Main monitoring loop handling user input"""
    print("\n📝 Commands:")
    print("  • 'status'    - Show account status")
    print("  • 'stats'     - Show sending statistics")
    print("  • 'delivery'  - Show delivery confirmation stats")
    print("  • 'terminate' - Stop all bots")
    print("=" * 60)
    print()

    try:
        while True:
            # Check if any process has died
            for account in accounts:
                if account['process'] and account['process'].poll() is not None:
                    print(f"\n⚠️  {account['name']} has stopped unexpectedly!")

            try:
                user_input = input().strip().lower()

                if user_input == 'terminate':
                    stop_bots(accounts)
                    break

                elif user_input == 'status':
                    print("\n📊 Account Status:")
                    print("─" * 60)
                    for account in accounts:
                        auth_status = "🟢 Authenticated" if account['authenticated'] else "🔴 Not Authenticated"
                        running = "Running" if (account['process'] and account['process'].poll() is None) else "Stopped"
                        consecutive = account['consecutive_uses']
                        print(f"  {account['name']}: {auth_status} | {running} | Consecutive: {consecutive}/3")
                    print("─" * 60)
                    print()

                elif user_input == 'stats':
                    contacts = load_contacts()
                    total = len(contacts)
                    sent = len([c for c in contacts if c.get('sent', False)])
                    unsent = total - sent
                    errors = len([c for c in contacts if is_error_sent_at(c.get('sentAt'))])
                    sent_success = len([
                        c for c in contacts
                        if c.get('sent', False)
                        and not is_error_sent_at(c.get('sentAt'))
                    ])

                    print("\n📊 Sending Statistics:")
                    print("─" * 60)
                    print(f"  Total contacts: {total}")
                    print(f"  Sent successfully: {sent_success}")
                    print(f"  Failed (errors): {errors}")
                    print(f"  Unsent: {unsent}")
                    print(f"  Authenticated accounts: {len(authenticated_accounts)}")

                    # Show breakdown by account
                    by_account = {}
                    error_by_account = {}
                    for c in contacts:
                        if c.get('sent') and c.get('sentBy'):
                            if is_error_sent_at(c.get('sentAt')):
                                error_by_account[c['sentBy']] = error_by_account.get(c['sentBy'], 0) + 1
                            else:
                                by_account[c['sentBy']] = by_account.get(c['sentBy'], 0) + 1

                    if by_account:
                        print("\n  Successfully sent by account:")
                        for acc_id, count in by_account.items():
                            print(f"    - {acc_id}: {count}")

                    if error_by_account:
                        print("\n  Errors by account:")
                        for acc_id, count in error_by_account.items():
                            print(f"    - {acc_id}: {count}")
                    print("─" * 60)
                    print()

                elif user_input == 'delivery':
                    contacts = load_contacts()
                    total = len(contacts)
                    sent = len([c for c in contacts if c.get('sent', False)])
                    delivered = len([c for c in contacts if c.get('delivered', False)])
                    read = len([c for c in contacts if c.get('ackLevel') == 3])
                    played = len([c for c in contacts if c.get('ackLevel') == 4])

                    print("\n📊 Delivery Confirmation Statistics:")
                    print("─" * 60)
                    print(f"  Total contacts: {total}")
                    print(f"  Sent: {sent}")
                    print(f"  Delivered (✓✓): {delivered}")
                    print(f"  Read (✓✓✓): {read}")
                    print(f"  Played (voice/video): {played}")
                    
                    if sent > 0:
                        delivery_rate = (delivered / sent) * 100
                        print(f"\n  Delivery rate: {delivery_rate:.1f}%")
                        
                        if read > 0:
                            read_rate = (read / sent) * 100
                            print(f"  Read rate: {read_rate:.1f}%")

                    # Show breakdown by account
                    by_account_delivery = {}
                    for c in contacts:
                        if c.get('delivered') and c.get('sentBy'):
                            by_account_delivery[c['sentBy']] = by_account_delivery.get(c['sentBy'], 0) + 1

                    if by_account_delivery:
                        print("\n  Deliveries confirmed by account:")
                        for acc_id, count in by_account_delivery.items():
                            print(f"    - {acc_id}: {count}")
                    print("─" * 60)
                    print()

                elif user_input:
                    print(f"⚠️  Unknown command: '{user_input}'")
                    print("Valid commands: status, stats, delivery, terminate")
                    print()

            except EOFError:
                break

    except KeyboardInterrupt:
        print("\n\n⚠️  Ctrl+C detected. Terminating all bots...")
        stop_bots(accounts)

    print("✅ All bots terminated!")

def main():
    """Main orchestrator function"""
    print_header()

    global pending_contacts_df
    pending_contacts_df = fetch_negociador_df()

    if not check_files():
        sys.exit(1)

    # --- PHASE 1: AUTHENTICATION ---
    print("\n" + "=" * 60)
    print("🚀 PHASE 1: Starting WhatsApp Bots for Authentication")
    print("=" * 60)

    # CRITICAL FIX: Clear contacts.json to an empty list.
    # This prevents bots from starting to send messages from a previous run
    # before we have calculated the new distribution.
    print("\n🧹 Clearing contacts.json to prevent premature sending...")
    save_contacts([])

    # Start all bots
    processes = []
    for idx, account in enumerate(ACCOUNTS):
        print(f"\n🔄 Starting {account['name']}...")
        process = start_bot(account)
        if process:
            account['process'] = process
            processes.append(account)

            # Start thread to monitor authentication
            monitor_thread = threading.Thread(
                target=monitor_authentication,
                args=(process, account),
                daemon=True
            )
            monitor_thread.start()

            print(f"✅ {account['name']} started (PID: {process.pid})")

            # Add 4 second delay between accounts to prevent browser conflicts
            if idx < len(ACCOUNTS) - 1:
                print(f"⏳ Waiting 4 seconds before starting next account...")
                time.sleep(4)

    if not processes:
        print("\n❌ No bots started. Exiting...")
        sys.exit(1)

    # Wait for ALL Auth
    # Previously we waited for 'any', which left account_2 behind.
    if not wait_for_all_authentication():
        print("❌ Critical failure waiting for authentication. Exiting...")
        stop_bots(processes)
        sys.exit(1)

    # --- PHASE 2: PREPARATION ---
    print("\n" + "=" * 60)
    print("🛠️  PHASE 2: Generating Contacts Configuration")
    print("=" * 60)

    # Stop bots to release locks/connections before generating new config
    stop_bots(processes)

    # Generate Contacts with proper sentBy assignment
    if not build_contacts_json_final():
        print("❌ Failed to build contacts.json. Exiting.")
        sys.exit(1)

    # --- PHASE 3: SENDING ---
    print("\n" + "=" * 60)
    print("🚀 PHASE 3: Starting Bots for Sending Messages")
    print("=" * 60)

    # Restart bots (They will auto-auth because session is saved, then read new contacts.json)
    for idx, account in enumerate(ACCOUNTS):
        print(f"\n🔄 Restarting {account['name']}...")
        process = start_bot(account)
        if process:
            account['process'] = process
            monitor_thread = threading.Thread(
                target=monitor_authentication,
                args=(process, account),
                daemon=True
            )
            monitor_thread.start()

            print(f"✅ {account['name']} restarted")

            if idx < len(ACCOUNTS) - 1:
                time.sleep(4)
        else:
            print(f"❌ Failed to restart {account['name']}")

    print("\n✅ Bots are running and processing messages...")
    print("💡 Delivery confirmations will be tracked automatically via MESSAGE_ACK events")

    # Run Monitor Loop
    monitor_and_commands(ACCOUNTS)

    print("\n👋 Orchestrator shutting down...")

if __name__ == "__main__":
    main()
