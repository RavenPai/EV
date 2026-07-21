create or replace function public.validate_mission_started_delivery_state()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.event_type <> 'MISSION_STARTED' then
    return new;
  end if;

  -- A QoS retry with the same event ID must still reach the unique-index
  -- conflict handler in apply_robot_event(), which returns false so the
  -- endpoint reports duplicate=true without applying the transition again.
  if new.message_id is not null
    and exists (
      select 1
      from public.robot_events
      where message_id = new.message_id
    ) then
    return new;
  end if;

  if new.delivery_id is null
    or not exists (
      select 1
      from public.deliveries
      where id = new.delivery_id
        and robot_id = new.robot_id
        and status = 'DISPATCHED'
    ) then
    raise exception
      'MISSION_STARTED is not valid for the current delivery state; a DISPATCHED delivery assigned to this robot is required';
  end if;

  return new;
end;
$$;

revoke all on function public.validate_mission_started_delivery_state()
from public, anon, authenticated;

drop trigger if exists robot_events_validate_mission_start
on public.robot_events;

create trigger robot_events_validate_mission_start
before insert on public.robot_events
for each row execute function public.validate_mission_started_delivery_state();

comment on function public.validate_mission_started_delivery_state() is
  'Prevents robot evidence from advancing a delivery before its mission command was successfully dispatched.';
