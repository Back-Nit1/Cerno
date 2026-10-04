# 1 finding

## Password reset accepts an expired reset\_token

`CERNO-AUTHZ` · critical · confirmed · `src/auth/reset.js:34-41`

consumeToken in src/auth/reset.js no longer compares record.expires\_at against Date.now(), so any token ever issued keeps working. The expiry constant RESET\_TTL\_MS is now unused.

**Next:** Restore the expiry comparison in consumeToken before the password write, and re-enable test/reset.test.js:"rejects an expired token".

Evidence:

- `E-test-01` - check\_test (test) - `test/reset.test.js:48` - npm run test: 1 failing - "rejects an expired token" expected 400, received 200.
- `E-struct-02` - caller\_graph (structural) - `src/auth/reset.js:34` - consumeToken is reached from the unauthenticated POST /auth/reset/confirm route in src/server/routes.js:88.
- `E-rule-03` - rule\_hit (rule) - File rule glob src/auth/\*\* matched; RESET\_TTL\_MS became unreferenced in this diff.

## Insufficient evidence

- `CERNO-DATA-EXPOSURE` - sendResetMail in src/auth/mailer.js logs its whole payload, but the logger sink is configured outside this repository. Missing: where logger.info writes in production, whether the payload still contains reset\_token at that point.

1 finding suppressed (below the severity threshold, outside the diff, or not evidence-backed); see the JSON result.

base `9f1c2e8` · head `5c6d7e8` · model `gpt-5.6-luna` · 42.8s · 96431 in / 5207 out tokens · $0.1820
