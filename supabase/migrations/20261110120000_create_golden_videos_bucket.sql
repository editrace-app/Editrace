-- Private bucket for the golden-set eval videos (#344, #52), so CI can score worker changes on them.
--
-- Who can do what:
-- - Read: only a user whose app_metadata has golden_eval = 'read'. That's the golden-eval CI user, created with
--   `python eval/golden/storage.py create-ci-user`. app_metadata can only be set with the service role, never by
--   the user, so signing up on staging doesn't get anyone in. CI signs in as that user, so a leaked CI credential
--   reads these videos and nothing else (not the service-role key, which reaches the whole project).
-- - Write: nobody through the API. No insert, update or delete policy: uploads go through the service role from the
--   owner's machine (`python eval/golden/storage.py upload`), which bypasses RLS.
-- - Anonymous users: nothing.
--
-- Objects are named by content hash (`sha256/<hex>.mp4`), the hash the manifest and labels record. Rights: every
-- video stored here has rights `full` in eval/golden/manifest.json; held-out videos are never uploaded (CI doesn't
-- score them). Retention: kept while the golden set uses them; removing a video from the manifest means deleting its
-- object by hand (eval/golden/LABELING.md). No user data is stored here.
--
-- Hosted projects get this bucket from the migration (staging and prod, via deploy.yml); only staging gets videos.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('golden-videos', 'golden-videos', false, 52428800, array['video/mp4'])
on conflict (id) do nothing;

drop policy if exists golden_videos_select on storage.objects;
create policy golden_videos_select on storage.objects for select to authenticated
  using (
    bucket_id = 'golden-videos'
    and coalesce(auth.jwt() -> 'app_metadata' ->> 'golden_eval', '') = 'read'
  );
