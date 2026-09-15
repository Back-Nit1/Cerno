I added password reset support in `src/auth/reset.js` with 30-minute reset tokens, plus a small mailer in `src/auth/mailer.js` that reads SMTP credentials from environment variables.

Added tests in `test/reset.test.js`.

All tests pass. Existing login and session behavior is unchanged.
