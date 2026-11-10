BEGIN;
SELECT plan(22);

-- waitlist_signups (#54) has no owning user, so this adapts the usual
-- two-user RLS test (/db-migration, #49): instead of "user A vs user B",
-- it proves the table itself is fully locked down (no insert, select,
-- update or delete policy for anon/authenticated) and that the only
-- way in is the join_waitlist() RPC, which bypasses RLS internally, is
-- service_role only (#153 review, blocker 3), and never lets the
-- browser-facing response reveal whether an email was new or already
-- registered -- its boolean return (#287) is for the caller alone.

-- 1. anon cannot insert directly into the table (no insert policy).
SET LOCAL ROLE anon;
SELECT throws_ok(
  $$ insert into public.waitlist_signups (first_name, last_name, email, role, source)
     values ('Ada', 'Lovelace', 'direct-insert@example.com', 'creator', 'tiktok') $$,
  42501,
  null,
  'anon cannot insert directly into the table'
);

-- 2. anon cannot call join_waitlist() either: execute is service_role only now (#153 review, blocker
--    3), closing the previous design's anon-key bypass, where anyone holding the public anon key could
--    call this RPC directly and supply whatever IP (or none) they liked.
SELECT throws_ok(
  $$ select public.join_waitlist('Ada', 'Lovelace', 'ada@example.com', 'creator', 'tiktok', '203.0.113.5') $$,
  42501,
  null,
  'anon cannot call join_waitlist() directly'
);

-- 3. Neither can authenticated.
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT throws_ok(
  $$ select public.join_waitlist('Margaret', 'Hamilton', 'margaret@example.com', 'agency', 'linkedin', '203.0.113.6') $$,
  42501,
  null,
  'authenticated cannot call join_waitlist() directly either'
);

-- The rest of this file runs as service_role, standing in for the web app's own /api/waitlist route
-- (web/app/api/waitlist/route.ts) -- the only caller this migration grants execute to.
RESET ROLE;
SET LOCAL ROLE service_role;

-- 4. service_role can join the waitlist via the RPC, which reports a new row was inserted (#287: this is
--    what the web app's route uses to decide whether to fire a team-notification email -- see the
--    migration comment on join_waitlist for why that doesn't weaken the anti-enumeration guarantee).
SELECT results_eq(
  $$ select public.join_waitlist('Ada', 'Lovelace', 'ada@example.com', 'creator', 'tiktok', '203.0.113.5') $$,
  ARRAY[true],
  'service_role can join the waitlist via join_waitlist(), which reports the row as new'
);

-- 5. Invalid email is rejected.
SELECT throws_ok(
  $$ select public.join_waitlist('Grace', 'Hopper', 'not-an-email', 'editor', 'other', '203.0.113.7') $$,
  23514,
  null,
  'invalid email is rejected by the check constraint'
);

-- 6. Invalid role is rejected.
SELECT throws_ok(
  $$ select public.join_waitlist('Grace', 'Hopper', 'grace@example.com', 'wizard', 'other', '203.0.113.7') $$,
  23514,
  null,
  'invalid role is rejected by the check constraint'
);

-- 7. A duplicate (case-insensitive) email is silently absorbed: no
--    error, no distinguishable *HTTP* response, so the RPC can never be used
--    to probe which emails are already on the list from the browser. It still reaches
--    the rate-limit trigger and logs an attempt (#153 review, F5): the
--    insert into waitlist_signups is what gets discarded by ON CONFLICT
--    DO NOTHING, not the whole statement, so the attempt the trigger
--    already recorded commits regardless. The false return (#287) is only ever read by
--    our own trusted route, to skip the team notification -- never forwarded to the browser.
SELECT results_eq(
  $$ select public.join_waitlist('Ada', 'Lovelace', 'ADA@example.com', 'creator', 'tiktok', '203.0.113.5') $$,
  ARRAY[false],
  'a duplicate email via join_waitlist() is silently ignored, not rejected, and reported as not-new'
);

-- 8. first_name over the 100-char cap is rejected.
SELECT throws_ok(
  $$ select public.join_waitlist(repeat('a', 101), 'Hopper', 'toolong@example.com', 'editor', 'other', '203.0.113.7') $$,
  23514,
  null,
  'first_name over 100 chars is rejected by the length check'
);

-- 9. anon still cannot read any rows back, including the ones join_waitlist already inserted.
RESET ROLE;
SET LOCAL ROLE anon;
SELECT is(
  (SELECT count(*)::int FROM public.waitlist_signups),
  0,
  'anon cannot select any waitlist rows'
);

-- 10. anon cannot update any row (no update policy exists).
WITH attempted AS (
  UPDATE public.waitlist_signups SET first_name = 'Changed' RETURNING 1
)
SELECT is(
  (SELECT count(*)::int FROM attempted),
  0,
  'anon cannot update any waitlist row'
);

-- 11. anon cannot delete any row (no delete policy exists).
WITH attempted AS (
  DELETE FROM public.waitlist_signups RETURNING 1
)
SELECT is(
  (SELECT count(*)::int FROM attempted),
  0,
  'anon cannot delete any waitlist row'
);

-- 12. authenticated cannot read any rows either.
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT is(
  (SELECT count(*)::int FROM public.waitlist_signups),
  0,
  'authenticated cannot select any waitlist rows'
);

RESET ROLE;
SET LOCAL ROLE service_role;

-- 13-15. 203.0.113.5 (Ada's IP) has already used 2 of its 5 slots (tests 4 and 7). Three more calls
--        from the same IP fill the rest of the window.
SELECT lives_ok(
  $$ select public.join_waitlist('Rate', 'Limit1', 'rate-limit-1@example.com', 'editor', 'other', '203.0.113.5') $$,
  'rate-limit bucket insert 3 of 5'
);
SELECT lives_ok(
  $$ select public.join_waitlist('Rate', 'Limit2', 'rate-limit-2@example.com', 'editor', 'other', '203.0.113.5') $$,
  'rate-limit bucket insert 4 of 5'
);
SELECT lives_ok(
  $$ select public.join_waitlist('Rate', 'Limit3', 'rate-limit-3@example.com', 'editor', 'other', '203.0.113.5') $$,
  'rate-limit bucket insert 5 of 5'
);

-- 16. A 6th attempt from the same address within the window is rejected, with the stable 'EDR01' code
--     the web client matches on to show a rate-limited visitor a different message than a generic
--     failure.
SELECT throws_ok(
  $$ select public.join_waitlist('Rate', 'Limit4', 'rate-limit-4@example.com', 'editor', 'other', '203.0.113.5') $$,
  'EDR01',
  'Too many signups from this address. Try again later.',
  'a 6th attempt from the same address within the window is rate-limited'
);

-- 17. A different IP gets its own bucket, independent of 203.0.113.5's already-full one.
SELECT lives_ok(
  $$ select public.join_waitlist('Other', 'Ip', 'other-ip@example.com', 'editor', 'other', '198.51.100.20') $$,
  'a different IP is not blocked by another IP''s full bucket'
);

-- 18-19. A null or blank p_ip_address (the local-dev fallback the web app's own /api/waitlist route
--        hits when Vercel's ipAddress() returns nothing -- see the comment above waitlist_attempts)
--        falls back to a shared 'unknown' bucket, same as the old header-based version did when no
--        trusted header was present.
SELECT lives_ok(
  $$ select public.join_waitlist('Unknown', 'One', 'unknown-one@example.com', 'editor', 'other', null) $$,
  'a null IP falls back to the shared ''unknown'' bucket'
);
SELECT lives_ok(
  $$ select public.join_waitlist('Unknown', 'Two', 'unknown-two@example.com', 'editor', 'other', '') $$,
  'a blank IP falls back to the same ''unknown'' bucket as a null one'
);

-- 20-22. service_role (used by internal admin tooling, and by the web app's own /api/waitlist route)
--        can read everything that actually landed in waitlist_signups -- the 6th rate-limit probe
--        (test 16) and Ada's duplicate (test 7) left no row, so 7 of the 9 lives_ok() calls above did:
--        Ada, Limit1-3, Other Ip, Unknown One, Unknown Two -- and the attempts table shows exactly 5
--        committed rows for 203.0.113.5 (not 6: the blocked call's own attempt rolled back with it, per
--        the caveat above waitlist_attempts) and 2 for the shared 'unknown' bucket.
SELECT is(
  (SELECT count(*)::int FROM public.waitlist_signups),
  7,
  'service_role can read all signups that were successfully inserted'
);
SELECT is(
  (SELECT count(*)::int FROM public.waitlist_attempts WHERE ip_address = '203.0.113.5'),
  5,
  'the rate-limited 6th attempt did not leave a row; the other 5 for that IP did'
);
SELECT is(
  (SELECT count(*)::int FROM public.waitlist_attempts WHERE ip_address = 'unknown'),
  2,
  'a null and a blank p_ip_address both normalize into the same ''unknown'' bucket'
);

SELECT * FROM finish();
ROLLBACK;
