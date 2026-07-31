-- 017: Drop the dead per-user notification preference columns.
-- (016 is the audit_log DEFAULT partition fix, committed 2026-07-30.)
--
-- These were readable and writable via GET/PATCH /v1/agents/me/notifications,
-- but no code anywhere ever delivered a notification to them. The endpoints are
-- removed; the columns go with them. Storing a user's Slack webhook URL (an
-- effective secret) to accomplish nothing is not acceptable in a public release.

ALTER TABLE users DROP COLUMN IF EXISTS notif_telegram_chat_id;
ALTER TABLE users DROP COLUMN IF EXISTS notif_slack_webhook_url;
ALTER TABLE users DROP COLUMN IF EXISTS notif_email;
ALTER TABLE users DROP COLUMN IF EXISTS notif_frequency;
