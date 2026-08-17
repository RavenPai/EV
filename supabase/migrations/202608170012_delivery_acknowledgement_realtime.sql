-- The operations UI reads command acknowledgement state through the Data API
-- and refreshes it from Supabase Realtime. Keep robot command writes owned by
-- the server-side ingestion path; this publication only exposes RLS-visible
-- changes to authenticated subscribers.
do $$
begin
  if exists (
    select 1
    from pg_catalog.pg_publication
    where pubname = 'supabase_realtime'
  ) and not exists (
    select 1
    from pg_catalog.pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'robot_commands'
  ) then
    execute 'alter publication supabase_realtime add table public.robot_commands';
  end if;
end;
$$;

-- dispatch-delivery resolves the human-readable pickup and destination names
-- before publishing the delivery envelope to EMQX. Migration 011 deliberately
-- reduced service-role table privileges, so grant this read explicitly.
grant select on table public.locations to service_role;

comment on table public.robot_commands is
  'Auditable robot commands; status changes are published through Supabase Realtime and remain protected by RLS.';
