create index if not exists robot_commands_expires_at_idx
  on public.robot_commands(expires_at)
  where status in ('PENDING', 'PUBLISHED');

create or replace function public.expire_stale_robot_commands()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_expired_count integer;
begin
  with due_commands as materialized (
    select
      id,
      robot_id,
      delivery_id,
      command_type,
      status as previous_status,
      expires_at
    from public.robot_commands
    where status in ('PENDING', 'PUBLISHED')
      and expires_at <= now()
    order by expires_at
    for update skip locked
  ),
  expired_commands as (
    update public.robot_commands as command
    set status = 'EXPIRED',
        result = coalesce(command.result, '{}'::jsonb) || jsonb_build_object(
          'reason', 'Command TTL elapsed before robot acknowledgement',
          'expiredAt', now(),
          'previousStatus', due.previous_status
        )
    from due_commands as due
    where command.id = due.id
    returning
      command.id,
      command.robot_id,
      command.delivery_id,
      command.command_type,
      command.expires_at,
      due.previous_status
  ),
  inserted_events as (
    insert into public.robot_events (
      robot_id,
      delivery_id,
      command_id,
      event_type,
      severity,
      payload,
      occurred_at
    )
    select
      robot_id,
      delivery_id,
      id,
      'COMMAND_EXPIRED',
      'WARNING',
      jsonb_build_object(
        'commandId', id,
        'commandType', command_type,
        'previousStatus', previous_status,
        'expiresAt', expires_at,
        'reason', 'Command TTL elapsed before robot acknowledgement'
      ),
      now()
    from expired_commands
    returning 1
  )
  select count(*)::integer
  into v_expired_count
  from inserted_events;

  return coalesce(v_expired_count, 0);
end;
$$;

revoke all on function public.expire_stale_robot_commands()
from public, anon, authenticated;

grant execute on function public.expire_stale_robot_commands()
to service_role;

select cron.schedule(
  'expire-stale-robot-commands',
  '* * * * *',
  'select public.expire_stale_robot_commands();'
)
where not exists (
  select 1
  from cron.job
  where jobname = 'expire-stale-robot-commands'
);
