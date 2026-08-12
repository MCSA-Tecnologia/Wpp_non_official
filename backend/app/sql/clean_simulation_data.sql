-- Run only after a verified PostgreSQL backup and with every worker stopped.
-- This installation previously used the removed fake connector, so none of its
-- campaigns, delivery results, sessions or counters represent real WhatsApp activity.
DO $$
DECLARE
    removed_campaigns integer;
    removed_imports integer;
    removed_jobs integer;
    removed_contacts integer;
    removed_auth_records integer;
    removed_audit_logs integer;
BEGIN
    SELECT count(*) INTO removed_campaigns FROM campaigns;
    SELECT count(*) INTO removed_imports FROM import_batches;
    SELECT count(*) INTO removed_jobs FROM message_jobs;
    SELECT count(*) INTO removed_contacts FROM contacts;
    SELECT count(*) INTO removed_auth_records FROM account_auth_records;
    SELECT count(*) INTO removed_audit_logs FROM audit_logs;

    DELETE FROM campaigns;
    DELETE FROM import_batches;
    DELETE FROM account_auth_records;
    DELETE FROM audit_logs;

    UPDATE accounts
    SET enabled = false,
        state = 'disabled',
        phone = NULL,
        node_id = NULL,
        lease_owner = NULL,
        lease_until = NULL,
        last_heartbeat_at = NULL,
        last_error = NULL,
        qr_code = NULL,
        sent_today = 0,
        sent_today_date = NULL,
        reconnect_count = 0,
        session_revision = session_revision + 1,
        updated_at = now();

    INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, details, created_at)
    VALUES (
        gen_random_uuid(),
        NULL,
        'system.simulation_data_removed',
        'system',
        NULL,
        json_build_object(
            'campaigns', removed_campaigns,
            'imports', removed_imports,
            'jobs', removed_jobs,
            'contacts', removed_contacts,
            'auth_records', removed_auth_records,
            'audit_logs', removed_audit_logs
        ),
        now()
    );
END $$;
