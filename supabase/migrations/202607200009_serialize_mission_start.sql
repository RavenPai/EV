create or replace function public.validate_mission_started_delivery_state()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_delivery_status public.delivery_status;
begin
  if new.event_type <> 'MISSION_STARTED' then
    return new;
  end if;

  -- Serialize concurrent retries that use the same MQTT event ID. After the
  -- first transaction commits, the retry reaches the unique-index conflict
  -- handler in apply_robot_event() and is reported as duplicate=true.
  if new.message_id is not null then
    perform pg_advisory_xact_lock(
      hashtextextended(new.message_id::text, 0)
    );

    if exists (
      select 1
      from public.robot_events
      where message_id = new.message_id
    ) then
      return new;
    end if;
  end if;

  if new.delivery_id is null then
    raise exception
      'MISSION_STARTED is not valid for the current delivery state; a DISPATCHED delivery assigned to this robot is required';
  end if;

  select delivery.status
  into v_delivery_status
  from public.deliveries as delivery
  where delivery.id = new.delivery_id
    and delivery.robot_id = new.robot_id
  for update;

  if not found or v_delivery_status is distinct from 'DISPATCHED' then
    raise exception
      'MISSION_STARTED is not valid for the current delivery state; a DISPATCHED delivery assigned to this robot is required';
  end if;

  return new;
end;
$$;

revoke all on function public.validate_mission_started_delivery_state()
from public, anon, authenticated;

comment on function public.validate_mission_started_delivery_state() is
  'Serializes mission-start evidence and prevents a delivery from accepting more than one initial transition.';
