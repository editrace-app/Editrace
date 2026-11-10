-- #287: join_waitlist() now reports whether it actually inserted a new row, so the waitlist API route can fire a
-- team notification only on a genuinely new signup, not a duplicate absorbed by ON CONFLICT DO NOTHING.
--
-- This doesn't weaken the anti-enumeration guarantee from the original migration (#153 review): the boolean is
-- read only by our own trusted, service-role-only caller, which already decides what -- if anything -- to tell
-- the browser, and keeps returning the same generic response to it regardless of this value. Nothing new is
-- exposed to anon or authenticated: they still have no execute on this function at all.
--
-- Postgres can't change a function's return type with CREATE OR REPLACE, hence the drop. Not shipped to a live
-- Supabase project yet (same as the migration this amends), so there's no live data or callers to migrate.
drop function public.join_waitlist(text, text, text, text, text, text);

create or replace function public.join_waitlist(
  p_first_name text,
  p_last_name text,
  p_email text,
  p_role text,
  p_source text,
  p_ip_address text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_rows int;
begin
  perform set_config('editrace.waitlist_client_ip', coalesce(p_ip_address, ''), true);

  insert into public.waitlist_signups (first_name, last_name, email, role, source)
  values (p_first_name, p_last_name, p_email, p_role, p_source)
  on conflict (email) do nothing;

  get diagnostics v_rows = row_count;
  return v_rows > 0;
end;
$$;

comment on function public.join_waitlist(text, text, text, text, text, text) is
  'service-role only: returns true when a new row was inserted, false for a duplicate email absorbed by ON CONFLICT DO NOTHING. The caller (web/app/api/waitlist/route.ts) uses this only to gate the #287 team-notification email -- it must never let this value change the anti-enumeration response the browser sees.';

-- DROP FUNCTION clears every grant on it, so both lines from the original migration are repeated here.
revoke execute on function public.join_waitlist(text, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.join_waitlist(text, text, text, text, text, text) to service_role;
