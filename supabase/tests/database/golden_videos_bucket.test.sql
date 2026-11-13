-- RLS test for the golden-videos storage bucket (#344). Not a user-owned table: the "two users" are the golden-eval
-- CI user (app_metadata golden_eval = 'read') and an ordinary signed-in user. Rolled back, nothing is left behind.
begin;
select plan(13);

select is(
  (select public from storage.buckets where id = 'golden-videos'), false, 'the golden-videos bucket is private');

insert into auth.users (id, email, aud, role, raw_app_meta_data) values
  ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'ci@golden.test', 'authenticated', 'authenticated',
   '{"provider":"email","golden_eval":"read"}'),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'a@golden.test', 'authenticated', 'authenticated',
   '{"provider":"email"}');

-- Storage refuses any delete in SQL unless this is set, which the Storage API does before its own deletes. Set it, so
-- the delete checks below reach the RLS policies the API's deletes go through.
select set_config('storage.allow_delete_query', 'true', true);

-- Uploaded with the service role, the way storage.py does.
insert into storage.objects (bucket_id, name) values ('golden-videos', 'sha256/probe.mp4');
-- Another bucket's object, to check the CI user's read stops at this bucket.
insert into storage.buckets (id, name, public) values ('golden-test-other', 'golden-test-other', false);
insert into storage.objects (bucket_id, name) values ('golden-test-other', 'other.mp4');

-- Act as a signed-in user, with the app_metadata their JWT would carry (Supabase copies it from raw_app_meta_data).
create function pg_temp.act_as(uid uuid, app_metadata jsonb) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', uid, 'role', 'authenticated', 'app_metadata', app_metadata)::text, true);
  perform set_config('request.jwt.claim.sub', uid::text, true);
  set local role authenticated;
end $$;

create function pg_temp.affected(q text) returns int language plpgsql as $$
declare n int;
begin
  execute q;
  get diagnostics n = row_count;
  return n;
end $$;

-- The CI user: reads this bucket, writes nothing.
select pg_temp.act_as('cccccccc-cccc-4ccc-8ccc-cccccccccccc', '{"provider":"email","golden_eval":"read"}');
select is((select count(*)::int from storage.objects where bucket_id = 'golden-videos'), 1,
  'the CI user can read the golden videos');
select is((select count(*)::int from storage.objects where bucket_id = 'golden-test-other'), 0,
  'the CI user cannot read other buckets');
select throws_ok(
  $$insert into storage.objects (bucket_id, name) values ('golden-videos', 'sha256/forged.mp4')$$,
  '42501', null, 'the CI user cannot upload');
select is(pg_temp.affected($$update storage.objects set name = 'sha256/renamed.mp4' where bucket_id = 'golden-videos'$$), 0,
  'the CI user cannot change a video');
select is(pg_temp.affected($$delete from storage.objects where bucket_id = 'golden-videos'$$), 0,
  'the CI user cannot delete a video');

-- An ordinary signed-in user, including one who set golden_eval in their own user_metadata (which they can).
reset role;
select pg_temp.act_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '{"provider":"email"}');
select is((select count(*)::int from storage.objects where bucket_id = 'golden-videos'), 0,
  'another signed-in user cannot read the golden videos');
select set_config('request.jwt.claims', json_build_object('sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  'role', 'authenticated', 'user_metadata', json_build_object('golden_eval', 'read'))::text, true);
select is((select count(*)::int from storage.objects where bucket_id = 'golden-videos'), 0,
  'golden_eval in user_metadata (user-editable) does not grant access');
select throws_ok(
  $$insert into storage.objects (bucket_id, name) values ('golden-videos', 'sha256/forged.mp4')$$,
  '42501', null, 'another signed-in user cannot upload');
select is(pg_temp.affected($$delete from storage.objects where bucket_id = 'golden-videos'$$), 0,
  'another signed-in user cannot delete a video');

-- Anonymous. Clear the JWT claims too, as in 001_two_user_probe.test.sql.
reset role;
select set_config('request.jwt.claims', '', true);
select set_config('request.jwt.claim.sub', '', true);
set local role anon;
select is((select count(*)::int from storage.objects where bucket_id = 'golden-videos'), 0,
  'anonymous users cannot read the golden videos');
select throws_ok(
  $$insert into storage.objects (bucket_id, name) values ('golden-videos', 'sha256/anon.mp4')$$,
  '42501', null, 'anonymous users cannot upload');
select is(pg_temp.affected($$delete from storage.objects where bucket_id = 'golden-videos'$$), 0,
  'anonymous users cannot delete a video');

select * from finish();
rollback;
