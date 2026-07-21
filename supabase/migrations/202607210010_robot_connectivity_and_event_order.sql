alter table public.robots
  add column if not exists bridge_last_seen timestamptz,
  add column if not exists bridge_online boolean not null default false,
  add column if not exists control_event_at timestamptz;

update public.robots
set bridge_last_seen = coalesce(bridge_last_seen, last_seen)
where bridge_last_seen is null;

create index if not exists robots_bridge_last_seen_idx
  on public.robots(bridge_last_seen);

create or replace function public.enforce_robot_last_seen_source()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.last_seen is distinct from old.last_seen
    and new.telemetry_at is not distinct from old.telemetry_at then
    new.last_seen := old.last_seen;
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_robot_last_seen_source()
from public, anon, authenticated;

drop trigger if exists robots_enforce_last_seen_source on public.robots;

create trigger robots_enforce_last_seen_source
before update of last_seen, telemetry_at on public.robots
for each row execute function public.enforce_robot_last_seen_source();

create or replace function public.enforce_robot_safety_latch()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_reset_robot_id text := current_setting('app.robot_safety_reset', true);
begin
  if old.mode = 'ESTOP'
    and new.mode is distinct from 'ESTOP'
    and v_reset_robot_id is distinct from old.id then
    new.mode := 'ESTOP';
    new.status := 'FAULT';
    new.speed_mps := 0;
  elsif old.mode = 'FAULT'
    and new.mode not in ('FAULT', 'ESTOP')
    and v_reset_robot_id is distinct from old.id then
    new.mode := 'FAULT';
    new.status := 'FAULT';
    new.speed_mps := 0;
  end if;

  if new.mode in ('ESTOP', 'FAULT') then
    new.status := 'FAULT';
    new.speed_mps := 0;
  elsif new.mode = 'PAUSED' then
    new.speed_mps := 0;
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_robot_safety_latch()
from public, anon, authenticated;

drop trigger if exists robots_enforce_safety_latch on public.robots;

create trigger robots_enforce_safety_latch
before update of status, mode, speed_mps on public.robots
for each row execute function public.enforce_robot_safety_latch();

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
  v_robot public.robots%rowtype;
  v_preserve_latch boolean;
begin
  if p_observed_at is null then
    raise exception 'State observation timestamp is required';
  end if;
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
  if p_mode in ('ESTOP', 'FAULT') and p_status <> 'FAULT' then
    raise exception 'ESTOP or FAULT mode requires FAULT status';
  end if;
  if p_status = 'FAULT' and p_mode not in ('ESTOP', 'FAULT') then
    raise exception 'FAULT status requires ESTOP or FAULT mode';
  end if;
  if p_mode = 'PAUSED' and p_speed_mps <> 0 then
    raise exception 'PAUSED mode requires zero speed';
  end if;
  if p_status = 'OFFLINE' and p_speed_mps <> 0 then
    raise exception 'OFFLINE status requires zero speed';
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

  select *
  into v_robot
  from public.robots
  where id = p_robot_id
  for update;

  if not found then
    raise exception 'Unknown robot';
  end if;
  if v_robot.telemetry_at is not null
    and p_observed_at <= v_robot.telemetry_at then
    return false;
  end if;

  v_preserve_latch := (v_robot.mode = 'ESTOP' and p_mode <> 'ESTOP')
    or (v_robot.mode = 'FAULT' and p_mode not in ('FAULT', 'ESTOP'));

  update public.robots
  set status = case
        when v_preserve_latch then 'FAULT'::public.robot_status
        else p_status::public.robot_status
      end,
      mode = case
        when v_preserve_latch then v_robot.mode
        else p_mode::public.robot_mode
      end,
      battery = p_battery,
      signal = p_signal,
      speed_mps = case when v_preserve_latch then 0 else p_speed_mps end,
      location_id = coalesce(p_location_id, location_id),
      current_delivery_id = case
        when v_preserve_latch then v_robot.current_delivery_id
        else p_current_delivery_id
      end,
      lidar = p_lidar,
      camera = p_camera,
      esp32 = p_esp32,
      motor_temp_c = p_motor_temp_c,
      firmware_version = coalesce(
        nullif(left(p_firmware_version, 80), ''),
        firmware_version
      ),
      telemetry_at = p_observed_at,
      last_seen = now(),
      bridge_online = true,
      bridge_last_seen = greatest(coalesce(bridge_last_seen, '-infinity'), now()),
      updated_at = now()
  where id = p_robot_id;

  return true;
end;
$$;

create or replace function public.apply_robot_ack(
  p_command_id uuid,
  p_robot_id text,
  p_status text,
  p_reason text,
  p_occurred_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_command public.robot_commands%rowtype;
begin
  if p_status not in ('ACKNOWLEDGED', 'REJECTED', 'COMPLETED', 'FAILED') then
    raise exception 'Invalid acknowledgement status';
  end if;
  if p_occurred_at is null then
    raise exception 'Acknowledgement timestamp is required';
  end if;

  select *
  into v_command
  from public.robot_commands
  where id = p_command_id
  for update;

  if not found then
    raise exception 'Unknown commandId';
  end if;
  if v_command.robot_id <> p_robot_id then
    raise exception 'Command belongs to another robot';
  end if;
  if v_command.status in ('REJECTED', 'COMPLETED', 'FAILED', 'EXPIRED') then
    return false;
  end if;
  if v_command.acknowledged_at is not null
    and p_occurred_at <= v_command.acknowledged_at then
    return false;
  end if;
  if v_command.status = 'ACKNOWLEDGED' and p_status = 'REJECTED' then
    raise exception 'An acknowledged command cannot become rejected';
  end if;

  update public.robot_commands
  set status = p_status,
      acknowledged_at = p_occurred_at,
      result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
        'reason', left(coalesce(p_reason, ''), 240),
        'at', p_occurred_at
      )
  where id = p_command_id;

  return true;
end;
$$;

revoke all on function public.apply_robot_ack(uuid, text, text, text, timestamptz)
from public, anon, authenticated;

grant execute on function public.apply_robot_ack(uuid, text, text, text, timestamptz)
to service_role;

create or replace function public.apply_robot_presence(
  p_robot_id text,
  p_online boolean,
  p_firmware_version text,
  p_observed_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_robot public.robots%rowtype;
  v_now timestamptz := now();
  v_telemetry_stale boolean;
  v_new_status public.robot_status;
  v_transitioned boolean;
  v_reason text;
begin
  select *
  into v_robot
  from public.robots
  where id = p_robot_id
  for update;

  if not found then
    raise exception 'Unknown robot';
  end if;
  if p_observed_at is null then
    raise exception 'Presence observation timestamp is required';
  end if;
  if v_robot.bridge_last_seen is not null
    and p_observed_at <= v_robot.bridge_last_seen then
    return false;
  end if;

  v_telemetry_stale := v_robot.telemetry_at is null
    or v_robot.telemetry_at < v_now - interval '60 seconds';

  if v_robot.mode in ('ESTOP', 'FAULT') then
    v_new_status := 'FAULT';
  else
    v_new_status := 'OFFLINE';
  end if;

  if p_online and not v_telemetry_stale then
    update public.robots
    set bridge_online = true,
        bridge_last_seen = p_observed_at,
        firmware_version = coalesce(
          nullif(left(p_firmware_version, 80), ''),
          firmware_version
        ),
        updated_at = v_now
    where id = p_robot_id;
    return false;
  end if;

  v_transitioned := v_robot.status is distinct from v_new_status;
  v_reason := case
    when p_online then 'telemetry heartbeat timeout while MQTT bridge is online'
    else 'MQTT bridge reported offline'
  end;

  update public.robots
  set bridge_online = p_online,
      bridge_last_seen = p_observed_at,
      status = v_new_status,
      speed_mps = 0,
      signal = 0,
      lidar = 'OFFLINE',
      camera = 'OFFLINE',
      esp32 = 'OFFLINE',
      firmware_version = coalesce(
        nullif(left(p_firmware_version, 80), ''),
        firmware_version
      ),
      updated_at = v_now
  where id = p_robot_id;

  if v_transitioned then
    insert into public.robot_events (
      robot_id,
      delivery_id,
      event_type,
      severity,
      payload,
      occurred_at
    ) values (
      p_robot_id,
      v_robot.current_delivery_id,
      'ROBOT_OFFLINE',
      'ERROR',
      jsonb_build_object(
        'reason', v_reason,
        'timeoutSeconds', 60,
        'bridgeOnline', p_online
      ),
      v_now
    );
  end if;

  return v_transitioned;
end;
$$;

revoke all on function public.apply_robot_presence(text, boolean, text, timestamptz)
from public, anon, authenticated;

grant execute on function public.apply_robot_presence(text, boolean, text, timestamptz)
to service_role;

create or replace function public.validate_robot_event_order()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_control_event_at timestamptz;
begin
  if new.event_type not in (
    'MISSION_STARTED', 'MISSION_COMPLETED', 'MISSION_FAILED',
    'PAUSED', 'RESUMED', 'ESTOP_TRIGGERED'
  ) then
    return new;
  end if;

  if new.message_id is not null
    and exists (
      select 1 from public.robot_events where message_id = new.message_id
    ) then
    return new;
  end if;

  select control_event_at
  into v_control_event_at
  from public.robots
  where id = new.robot_id
  for update;

  if not found then
    raise exception 'Unknown robot';
  end if;

  if v_control_event_at is not null
    and new.occurred_at <= v_control_event_at then
    raise exception 'Robot control event is older than the current control state';
  end if;

  update public.robots
  set control_event_at = new.occurred_at
  where id = new.robot_id;

  return new;
end;
$$;

revoke all on function public.validate_robot_event_order()
from public, anon, authenticated;

drop trigger if exists robot_events_validate_control_order
on public.robot_events;

create trigger robot_events_validate_control_order
before insert on public.robot_events
for each row execute function public.validate_robot_event_order();

update public.robots as robot
set control_event_at = latest.occurred_at
from (
  select event.robot_id, max(event.occurred_at) as occurred_at
  from public.robot_events as event
  where event.event_type in (
      'MISSION_STARTED', 'MISSION_COMPLETED', 'MISSION_FAILED',
      'PAUSED', 'RESUMED', 'ESTOP_TRIGGERED'
    )
  group by event.robot_id
) as latest
where latest.robot_id = robot.id
  and latest.occurred_at is not null
  and robot.control_event_at is distinct from latest.occurred_at;

create or replace function public.mark_stale_robots_offline()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  update public.robots
  set bridge_online = false,
      updated_at = now()
  where bridge_online = true
    and (
      bridge_last_seen is null
      or bridge_last_seen < now() - interval '60 seconds'
    );

  with stale as (
    update public.robots
    set status = case
          when mode in ('ESTOP', 'FAULT') then 'FAULT'::public.robot_status
          else 'OFFLINE'::public.robot_status
        end,
        speed_mps = 0,
        signal = 0,
        lidar = 'OFFLINE',
        camera = 'OFFLINE',
        esp32 = 'OFFLINE',
        updated_at = now()
    where status not in ('OFFLINE', 'FAULT')
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
    jsonb_build_object('reason', 'telemetry heartbeat timeout', 'timeoutSeconds', 60),
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

comment on function public.apply_robot_presence(text, boolean, text, timestamptz) is
  'Tracks MQTT bridge connectivity separately and never treats presence alone as fresh operational telemetry.';

comment on function public.validate_robot_event_order() is
  'Rejects delayed control events that would overwrite newer PAUSE, ESTOP, mission completion, or fault state.';
