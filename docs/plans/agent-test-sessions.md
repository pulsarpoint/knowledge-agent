# Agent test conversations and tuning

Completed and deployed locally on 2026-09-11. Verification is recorded in
[runtime verification](../runtime-verification.md#2026-09-11--persistent-test-conversations-and-tuning-reviews).

1. Add PostgreSQL test sessions and saved tuning reviews with golang-migrate.
   Existing run records remain readable; new runs belong to a session.
2. Persist the native Codex thread in the existing administration data volume.
   Follow-ups resume that thread. Credentials are recreated only during a turn.
3. Keep execution independent of the browser stream, allow reconnecting by
   session_id, serialize turns per session, and support cancellation and closure.
   Sessions expire after one hour without a running turn or new user activity.
4. Show the whole conversation in Test, with session history, URL restoration,
   New session, Close session, and the existing configuration preview.
5. Add a tool-free tuning review that receives the full current configuration,
   every stored conversation turn, and administrator feedback. Save suggestions
   as reviewable Markdown, including proposed instructions and candidate memory
   notes. Do not automatically alter agent settings, prompts, or permissions.
6. Verify native resume with an isolated model, session lifecycle and isolation
   against disposable PostgreSQL, UI interactions, migrations, then live Bedrock
   with a temporary agent that cannot access real SSH servers.
