create extension if not exists pg_cron with schema pg_catalog;

alter table public.robots
  add column if not exists telemetry_at timestamptz,
  add column if not exists firmware_version text;

create index if not exists robots_last_seen_idx
  on public.robots(last_seen);

alter table public.robot_events
  add column if not exists message_id uuid,
  add column if not exists command_id uuid
    references public.robot_commands(id) on delete set null;

create unique index if not exists robot_events_message_id_idx
  on public.robot_events(message_id)
  where message_id is not null;

create or replace function public.apply_robot_state(
  p_robot_id text,
  p_observed_at timestamptz,
  p_status text,
  p_mode text,
  p_battery integer,
  p_signal integer,
  p_speed_mps numeric,
  p_location_id text,
  p_current_delivery_id uuid,
  p_lidar text,
  p_camera text,
  p_esp32 text,
  p_motor_temp_c numeric,
  p_firmware_version text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_changed integer;
begin
  if p_status not in ('ONLINE', 'BUSY', 'CHARGING', 'OFFLINE', 'FAULT') then
    raise exception 'Invalid robot status';
  end if;
  if p_mode not in ('IDLE', 'AUTO', 'MANUAL', 'PAUSED', 'ESTOP', 'FAULT') then
    raise exception 'Invalid robot mode';
  end if;
  if p_battery not between 0 and 100 or p_signal not between 0 and 100 then
    raise exception 'Battery and signal must be between 0 and 100';
  end if;
  if p_speed_mps < 0 or p_speed_mps > 5 then
    raise exception 'Speed must be between 0 and 5 m/s';
  end if;
  if p_motor_temp_c < -20 or p_motor_temp_c > 150 then
    raise exception 'Motor temperature is outside the accepted range';
  end if;
  if p_lidar not in ('OK', 'WARNING', 'OFFLINE')
    or p_camera not in ('OK', 'WARNING', 'OFFLINE')
    or p_esp32 not in ('OK', 'WARNING', 'OFFLINE') then
    raise exception 'Invalid sensor state';
  end if;
  if p_location_id is not null and not exists (
    select 1 from public.locations where id = p_location_id and active = true
  ) then
    raise exception 'Unknown or inactive location';
  end if;
  if p_current_delivery_id is not null and not exists (
    select 1
    from public.deliveries
    where id = p_current_delivery_id
      and robot_id = p_robot_id
      and status not in ('COMPLETED', 'CANCELLED', 'FAILED')
  ) then
    raise exception 'Active delivery is not assigned to this robot';
  end if;

  update public.robots
  set status = p_status::public.robot_status,
      mode = p_mode::public.robot_mode,
      battery = p_battery,
      signal = p_signal,
      speed_mps = p_speed_mps,
      location_id = coalesce(p_location_id, location_id),
      current_delivery_id = p_current_delivery_id,
      lidar = p_lidar,
      camera = p_camera,
      esp32 = p_esp32,
      motor_temp_c = p_motor_temp_c,
      firmware_version = coalesce(nullif(p_firmware_version, ''), firmware_version),
      telemetry_at = p_observed_at,
      last_seen = now(),
      updated_at = now()
  where id = p_robot_id
    and (telemetry_at is null or p_observed_at >= telemetry_at);

  get diagnostics v_changed = row_count;
  if v_changed = 1 then
    return true;
  end if;

  if not exists (select 1 from public.robots where id = p_robot_id) then
    raise exception 'Unknown robot';
  end if;

  -- An older QoS message arrived after a newer telemetry sample.
  return false;
end;
$$;

revoke all on function public.apply_robot_state(
  text, timestamptz, text, text, integer, integer, numeric, text, uuid,
  text, text, text, numeric, text
) from public, anon, authenticated;

grant execute on function public.apply_robot_state(
  text, timestamptz, text, text, integer, integer, numeric, text, uuid,
  text, text, text, numeric, text
) to service_role;

create or replace function public.apply_robot_event(
  p_message_id uuid,
  p_robot_id text,
  p_delivery_id uuid,
  p_command_id uuid,
  p_event_type text,
  p_severity text,
  p_payload jsonb,
  p_occurred_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_event_id bigint;
  v_changed integer;
begin
  if p_event_type not in (
    'MISSION_STARTED', 'ARRIVED_SOURCE', 'PACKAGE_LOADED',
    'DEPARTED_SOURCE', 'ARRIVED_DESTINATION', 'PACKAGE_RELEASED',
    'RETURNING_HOME', 'MISSION_COMPLETED', 'MISSION_FAILED',
    'PAUSED', 'RESUMED', 'ESTOP_TRIGGERED', 'OBSTACLE_DETECTED',
    'LOW_BATTERY', 'ESP32_DISCONNECTED', 'BRIDGE_FAULT'
  ) then
    raise exception 'Unsupported event type';
  end if;
  if p_severity not in ('INFO', 'WARNING', 'ERROR', 'CRITICAL') then
    raise exception 'Invalid event severity';
  end if;
  if not exists (select 1 from public.robots where id = p_robot_id) then
    raise exception 'Unknown robot';
  end if;
  if p_delivery_id is not null and not exists (
    select 1
    from public.deliveries
    where id = p_delivery_id
      and robot_id = p_robot_id
  ) then
    raise exception 'Delivery is not assigned to this robot';
  end if;
  if p_command_id is not null and not exists (
    select 1
    from public.robot_commands
    where id = p_command_id
      and robot_id = p_robot_id
      and (delivery_id is null or delivery_id = p_delivery_id)
  ) then
    raise exception 'Command does not belong to this robot and delivery';
  end if;
  if p_event_type in (
    'MISSION_STARTED', 'ARRIVED_SOURCE', 'PACKAGE_LOADED',
    'DEPARTED_SOURCE', 'ARRIVED_DESTINATION', 'PACKAGE_RELEASED',
    'RETURNING_HOME', 'MISSION_COMPLETED', 'MISSION_FAILED'
  ) and p_delivery_id is null then
    raise exception 'Mission event requires deliveryId';
  end if;

  insert into public.robot_events (
    message_id,
    robot_id,
    delivery_id,
    command_id,
    event_type,
    severity,
    payload,
    occurred_at
  )
  values (
    p_message_id,
    p_robot_id,
    p_delivery_id,
    p_command_id,
    p_event_type,
    p_severity,
    coalesce(p_payload, '{}'::jsonb),
    p_occurred_at
  )
  on conflict (message_id) where message_id is not null
  do nothing
  returning id into v_event_id;

  if v_event_id is null then
    return false;
  end if;

  update public.robots
  set last_seen = now(),
      updated_at = now()
  where id = p_robot_id;

  if p_command_id is not null and p_event_type = 'MISSION_COMPLETED' then
    update public.robot_commands
    set status = 'COMPLETED',
        result = jsonb_build_object(
          'eventId', p_message_id,
          'eventType', p_event_type,
          'at', p_occurred_at
        )
    where id = p_command_id
      and status not in ('REJECTED', 'FAILED', 'EXPIRED');
  elsif p_command_id is not null and p_event_type = 'MISSION_FAILED' then
    update public.robot_commands
    set status = 'FAILED',
        result = jsonb_build_object(
          'eventId', p_message_id,
          'eventType', p_event_type,
          'at', p_occurred_at,
          'detail', coalesce(p_payload, '{}'::jsonb)
        )
    where id = p_command_id
      and status not in ('REJECTED', 'COMPLETED', 'EXPIRED');
  end if;

  case p_event_type
    when 'MISSION_STARTED' then
      update public.deliveries
      set status = 'TO_SOURCE',
          progress = 28,
          eta_minutes = 12,
          dispatched_at = coalesce(dispatched_at, p_occurred_at)
      where id = p_delivery_id
        and status in ('ASSIGNED', 'DISPATCHED', 'TO_SOURCE');
      get diagnostics v_changed = row_count;

      update public.robots
      set status = 'BUSY',
          mode = 'AUTO',
          current_delivery_id = p_delivery_id
      where id = p_robot_id;

    when 'ARRIVED_SOURCE' then
      update public.deliveries
      set status = 'AT_SOURCE', progress = 40, eta_minutes = 10
      where id = p_delivery_id and status = 'TO_SOURCE';
      get diagnostics v_changed = row_count;

    when 'PACKAGE_LOADED' then
      update public.deliveries
      set status = 'PACKAGE_LOADED', progress = 50, eta_minutes = 9
      where id = p_delivery_id and status = 'AT_SOURCE';
      get diagnostics v_changed = row_count;

    when 'DEPARTED_SOURCE' then
      update public.deliveries
      set status = 'TO_DESTINATION', progress = 62, eta_minutes = 7
      where id = p_delivery_id and status = 'PACKAGE_LOADED';
      get diagnostics v_changed = row_count;

    when 'ARRIVED_DESTINATION' then
      update public.deliveries
      set status = 'AT_DESTINATION', progress = 82, eta_minutes = 2
      where id = p_delivery_id and status = 'TO_DESTINATION';
      get diagnostics v_changed = row_count;

    when 'PACKAGE_RELEASED' then
      update public.deliveries
      set status = 'DELIVERED', progress = 90, eta_minutes = 1
      where id = p_delivery_id and status = 'AT_DESTINATION';
      get diagnostics v_changed = row_count;

    when 'RETURNING_HOME' then
      update public.deliveries
      set status = 'RETURNING', progress = 95, eta_minutes = 5
      where id = p_delivery_id
        and status in ('AT_DESTINATION', 'DELIVERED', 'RETURNING');
      get diagnostics v_changed = row_count;

    when 'MISSION_COMPLETED' then
      update public.deliveries
      set status = 'COMPLETED',
          progress = 100,
          eta_minutes = 0,
          completed_at = p_occurred_at
      where id = p_delivery_id
        and status in ('DELIVERED', 'RETURNING');
      get diagnostics v_changed = row_count;

      update public.robots
      set status = 'ONLINE',
          mode = 'IDLE',
          current_delivery_id = null,
          speed_mps = 0
      where id = p_robot_id;

    when 'MISSION_FAILED' then
      update public.deliveries
      set status = 'FAILED',
          eta_minutes = null
      where id = p_delivery_id
        and status not in ('COMPLETED', 'CANCELLED', 'FAILED');
      get diagnostics v_changed = row_count;

      update public.robots
      set status = 'FAULT',
          mode = 'FAULT',
          speed_mps = 0
      where id = p_robot_id;

    when 'PAUSED' then
      update public.robots
      set mode = 'PAUSED', speed_mps = 0
      where id = p_robot_id;
      v_changed := 1;

    when 'RESUMED' then
      update public.robots
      set mode = case
            when current_delivery_id is null then 'IDLE'::public.robot_mode
            else 'AUTO'::public.robot_mode
          end,
          status = case
            when current_delivery_id is null then 'ONLINE'::public.robot_status
            else 'BUSY'::public.robot_status
          end
      where id = p_robot_id;
      v_changed := 1;

    when 'ESTOP_TRIGGERED' then
      update public.robots
      set status = 'FAULT', mode = 'ESTOP', speed_mps = 0
      where id = p_robot_id;
      v_changed := 1;

    else
      v_changed := 1;
  end case;

  if coalesce(v_changed, 0) = 0 then
    raise exception 'Event is not valid for the current delivery state';
  end if;

  return true;
end;
$$;

revoke all on function public.apply_robot_event(
  uuid, text, uuid, uuid, text, text, jsonb, timestamptz
) from public, anon, authenticated;

grant execute on function public.apply_robot_event(
  uuid, text, uuid, uuid, text, text, jsonb, timestamptz
) to service_role;

create or replace function public.mark_stale_robots_offline()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  with stale as (
    update public.robots
    set status = 'OFFLINE',
        speed_mps = 0,
        signal = 0,
        lidar = 'OFFLINE',
        camera = 'OFFLINE',
        esp32 = 'OFFLINE',
        updated_at = now()
    where status <> 'OFFLINE'
      and (last_seen is null or last_seen < now() - interval '60 seconds')
    returning id, current_delivery_id
  )
  insert into public.robot_events (
    robot_id,
    delivery_id,
    event_type,
    severity,
    payload,
    occurred_at
  )
  select
    id,
    current_delivery_id,
    'ROBOT_OFFLINE',
    'ERROR',
    jsonb_build_object('reason', 'heartbeat timeout', 'timeoutSeconds', 60),
    now()
  from stale;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.mark_stale_robots_offline()
from public, anon, authenticated;

grant execute on function public.mark_stale_robots_offline()
to service_role;

select cron.schedule(
  'mark-stale-robots-offline',
  '* * * * *',
  'select public.mark_stale_robots_offline();'
)
where not exists (
  select 1 from cron.job where jobname = 'mark-stale-robots-offline'
);
