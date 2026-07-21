-- Follow-up hardening is intentionally append-only. Migration 010 was already
-- published in Git even though the audited production project had not applied
-- it, so environments that did apply 010 still receive these corrections.

alter table public.robots
  add column if not exists safety_latched_at timestamptz,
  add column if not exists control_event_received_at timestamptz,
  add column if not exists telemetry_received_at timestamptz;

-- telemetry_at is the robot's device clock and is used only for ordering. A
-- separate server receipt time prevents a clock-ahead robot from appearing
-- operationally fresh after its telemetry stream has stopped. Do not backfill
-- this from legacy last_seen: older event ingestion also changed last_seen, so
-- existing robots intentionally fail closed until their next real state frame.

alter table public.robot_commands
  drop constraint if exists robot_commands_status_check;

alter table public.robot_commands
  add constraint robot_commands_status_check check (status in (
    'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED',
    'REJECTED', 'COMPLETED', 'FAILED', 'EXPIRED'
  ));

create index if not exists robots_telemetry_received_at_idx
  on public.robots(telemetry_received_at);

-- Expire unacknowledged commands whose wall-clock TTL has already elapsed so
-- they do not cause a false duplicate failure when the safety indexes below
-- are installed. ACKNOWLEDGED duplicates require an explicit operator review;
-- choosing which physical mission is authoritative cannot be automated safely.
select public.expire_stale_robot_commands();

do $$
begin
  if exists (
    select 1
    from public.robot_commands
    where command_type = 'START_MISSION'
      and status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED')
    group by robot_id
    having count(*) > 1
  ) then
    raise exception
      'Resolve duplicate active START_MISSION commands for a robot before applying migration 011';
  end if;

  if exists (
    select 1
    from public.robot_commands
    where command_type = 'START_MISSION'
      and delivery_id is not null
      and status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED')
    group by delivery_id
    having count(*) > 1
  ) then
    raise exception
      'Resolve duplicate active START_MISSION commands for a delivery before applying migration 011';
  end if;

  if exists (
    select 1
    from public.robot_commands
    where command_type in ('PAUSE', 'RESUME', 'RETURN_HOME')
      and (
        status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED')
        or (
          status = 'COMPLETED'
          and not (
            coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
          )
        )
      )
    group by robot_id
    having count(*) > 1
  ) then
    raise exception
      'Resolve duplicate active control commands before applying migration 011';
  end if;
end;
$$;

create unique index if not exists robot_commands_one_active_mission_per_robot_idx
  on public.robot_commands(robot_id)
  where command_type = 'START_MISSION'
    and status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED');

create unique index if not exists robot_commands_one_active_mission_per_delivery_idx
  on public.robot_commands(delivery_id)
  where command_type = 'START_MISSION'
    and delivery_id is not null
    and status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED');

create unique index if not exists robot_commands_one_active_control_per_robot_idx
  on public.robot_commands(robot_id)
  where command_type in ('PAUSE', 'RESUME', 'RETURN_HOME')
    and (
      status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED')
      or (
        status = 'COMPLETED'
        and not (
          coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
        )
      )
    );

create or replace function public.validate_start_mission_command_reservation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_delivery_robot_id text;
  v_delivery_status public.delivery_status;
  v_robot public.robots%rowtype;
begin
  if new.command_type not in (
    'START_MISSION', 'PAUSE', 'RESUME', 'RETURN_HOME'
  ) then
    return new;
  end if;

  select *
  into v_robot
  from public.robots
  where id = new.robot_id
  for update;
  if not found then
    raise exception 'Command requires a known robot';
  end if;

  if new.command_type = 'START_MISSION' and exists (
    select 1
    from public.robot_commands as existing_command
    where existing_command.robot_id = new.robot_id
      and existing_command.command_type <> 'START_MISSION'
      and (
        existing_command.status = 'PUBLISH_UNKNOWN'
        or (
          existing_command.command_type in (
            'PAUSE', 'RESUME', 'RETURN_HOME', 'ESTOP'
          )
          and (
            existing_command.status in (
              'PENDING', 'PUBLISHED', 'ACKNOWLEDGED'
            )
            or (
              existing_command.status = 'COMPLETED'
              and not (
                coalesce(existing_command.result, '{}'::jsonb)
                  @> '{"consumed": true}'::jsonb
              )
            )
          )
        )
      )
  ) then
    raise exception
      'START_MISSION is blocked by an unresolved robot control command';
  end if;

  if new.command_type = 'RESUME' then
    if v_robot.mode not in ('PAUSED', 'ESTOP', 'FAULT') then
      raise exception 'RESUME requires a paused or safety-latched robot';
    end if;
    if not exists (
      select 1
      from public.profiles
      where id = new.issued_by
        and role in ('ADMIN', 'OPERATOR')
    ) then
      raise exception 'RESUME requires a staff issuer';
    end if;
    if v_robot.mode in ('ESTOP', 'FAULT') and (
      v_robot.safety_latched_at is null
      or new.issued_at <= v_robot.safety_latched_at
    ) then
      raise exception 'RESUME must be issued after the current safety latch';
    end if;
    if v_robot.mode = 'PAUSED' and (
      v_robot.control_event_received_at is null
      or new.issued_at <= v_robot.control_event_received_at
    ) then
      raise exception 'RESUME must be issued after the current pause';
    end if;
    return new;
  end if;

  if new.command_type in ('PAUSE', 'RETURN_HOME') then
    if v_robot.mode in ('ESTOP', 'FAULT') then
      raise exception 'Safety latch blocks this control command';
    end if;
    if new.command_type = 'PAUSE' and v_robot.mode = 'PAUSED' then
      raise exception 'Robot is already paused';
    end if;
    if new.command_type = 'RETURN_HOME' and v_robot.mode = 'PAUSED' then
      raise exception 'RESUME the robot before RETURN_HOME';
    end if;
    if new.command_type = 'RETURN_HOME' and (
      new.delivery_id is null
      or v_robot.current_delivery_id is distinct from new.delivery_id
      or not exists (
          select 1
          from public.deliveries delivery
          where delivery.id = new.delivery_id
            and delivery.robot_id = new.robot_id
            and delivery.status not in ('COMPLETED', 'CANCELLED', 'FAILED')
        )
    ) then
      raise exception 'RETURN_HOME delivery is no longer active for this robot';
    end if;
    return new;
  end if;

  if new.delivery_id is null then
    raise exception 'START_MISSION requires a delivery';
  end if;
  if v_robot.status <> 'ONLINE'
    or v_robot.mode <> 'IDLE'
    or v_robot.current_delivery_id is not null
    or v_robot.speed_mps <> 0
    or v_robot.battery < 20
    or not v_robot.bridge_online
    or v_robot.bridge_last_seen is null
    or v_robot.bridge_last_seen < statement_timestamp() - interval '60 seconds'
    or v_robot.telemetry_received_at is null
    or v_robot.telemetry_received_at
      < statement_timestamp() - interval '60 seconds'
    or v_robot.lidar <> 'OK'
    or v_robot.camera <> 'OK'
    or v_robot.esp32 <> 'OK' then
    raise exception 'START_MISSION requires a ready robot with fresh telemetry';
  end if;

  select robot_id, status
  into v_delivery_robot_id, v_delivery_status
  from public.deliveries
  where id = new.delivery_id
  for update;

  if not found
    or v_delivery_robot_id is distinct from new.robot_id
    or v_delivery_status is distinct from 'ASSIGNED' then
    raise exception 'START_MISSION requires its ASSIGNED delivery and robot';
  end if;
  return new;
end;
$$;

revoke all on function public.validate_start_mission_command_reservation()
from public, anon, authenticated;

drop trigger if exists robot_commands_validate_mission_reservation
on public.robot_commands;

create trigger robot_commands_validate_mission_reservation
before insert on public.robot_commands
for each row execute function public.validate_start_mission_command_reservation();

create or replace function public.protect_active_mission_assignment()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if exists (
    select 1
    from public.robot_commands
    where delivery_id = old.id
      and command_type = 'START_MISSION'
      and status in (
        'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED'
      )
  ) then
    if new.robot_id is distinct from old.robot_id then
      raise exception 'Delivery assignment is reserved by an active mission command';
    end if;
    if new.status not in (
      'ASSIGNED', 'DISPATCHED', 'TO_SOURCE', 'AT_SOURCE', 'PACKAGE_LOADED',
      'TO_DESTINATION', 'AT_DESTINATION', 'DELIVERED', 'RETURNING'
    ) then
      raise exception
        'Resolve the active mission command before changing delivery state';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.protect_active_mission_assignment()
from public, anon, authenticated;

drop trigger if exists deliveries_protect_active_mission_assignment
on public.deliveries;

create trigger deliveries_protect_active_mission_assignment
before update of robot_id, status on public.deliveries
for each row execute function public.protect_active_mission_assignment();

create or replace function public.enforce_authenticated_delivery_insert()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    return new;
  end if;

  if new.status is distinct from 'REQUESTED'::public.delivery_status
    or new.robot_id is not null
    or new.progress <> 5
    or new.eta_minutes is not null
    or new.approved_by is not null
    or new.approved_at is not null
    or new.dispatched_at is not null
    or new.completed_at is not null then
    raise exception 'Authenticated delivery requests must use initial lifecycle defaults';
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_authenticated_delivery_insert()
from public, anon, authenticated;

drop trigger if exists deliveries_enforce_authenticated_insert
on public.deliveries;

create trigger deliveries_enforce_authenticated_insert
before insert on public.deliveries
for each row execute function public.enforce_authenticated_delivery_insert();

create or replace function public.enforce_authenticated_delivery_workflow()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Trusted service-role ingestion has no end-user auth.uid(). Authenticated
  -- browser callers may approve/assign/cancel requests, but robot lifecycle
  -- checkpoints are owned exclusively by apply_robot_event().
  if auth.uid() is null then
    return new;
  end if;

  if new.status is distinct from old.status and not (
    (old.status = 'REQUESTED' and new.status in ('APPROVED', 'CANCELLED'))
    or (old.status = 'APPROVED' and new.status in ('ASSIGNED', 'CANCELLED'))
  ) then
    raise exception 'Delivery lifecycle advances only through robot events';
  end if;

  if new.dispatched_at is distinct from old.dispatched_at
    or new.completed_at is distinct from old.completed_at
    or new.eta_minutes is distinct from old.eta_minutes then
    raise exception 'Delivery lifecycle fields advance only through robot events';
  end if;

  if new.status is distinct from old.status then
    if (new.status = 'APPROVED' and new.progress <> 12)
      or (new.status = 'ASSIGNED' and new.progress <> 20)
      or (new.status = 'CANCELLED' and new.progress <> 0) then
      raise exception 'Delivery workflow transition has invalid progress';
    end if;
  elsif new.progress is distinct from old.progress then
    raise exception 'Delivery progress advances only through robot events';
  end if;

  if old.status = 'REQUESTED' and new.status = 'APPROVED' then
    new.approved_by := auth.uid();
    new.approved_at := statement_timestamp();
  elsif new.approved_by is distinct from old.approved_by
    or new.approved_at is distinct from old.approved_at then
    raise exception 'Delivery approval audit fields are database-owned';
  end if;

  if new.robot_id is distinct from old.robot_id
    and not (
      new.status = 'ASSIGNED'
      and old.status in ('APPROVED', 'ASSIGNED')
    ) then
    raise exception 'Robot assignment is not valid for this delivery state';
  end if;
  if new.status = 'ASSIGNED' and new.robot_id is null then
    raise exception 'ASSIGNED delivery requires a robot';
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_authenticated_delivery_workflow()
from public, anon, authenticated;

drop trigger if exists deliveries_enforce_authenticated_workflow
on public.deliveries;

create trigger deliveries_enforce_authenticated_workflow
before update of robot_id, status, progress, eta_minutes, dispatched_at,
  completed_at, approved_by, approved_at on public.deliveries
for each row execute function public.enforce_authenticated_delivery_workflow();

create or replace function public.resolve_unknown_robot_command(
  p_command_id uuid,
  p_actor_id uuid,
  p_resolution text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_command public.robot_commands%rowtype;
begin
  if p_resolution <> 'CONFIRMED_NOT_PUBLISHED' then
    raise exception 'Unsupported publish reconciliation outcome';
  end if;
  if not exists (
    select 1
    from public.profiles
    where id = p_actor_id
      and role in ('ADMIN', 'OPERATOR')
  ) then
    raise exception 'Staff role required for publish reconciliation';
  end if;

  select *
  into v_command
  from public.robot_commands
  where id = p_command_id
  for update;

  if not found then
    raise exception 'Unknown commandId';
  end if;
  if v_command.status <> 'PUBLISH_UNKNOWN' then
    raise exception 'Command does not have an unknown publish outcome';
  end if;

  update public.robot_commands
  set status = 'FAILED',
      result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
        'resolution', p_resolution,
        'resolvedBy', p_actor_id,
        'resolvedAt', statement_timestamp()
      )
  where id = p_command_id
    and status = 'PUBLISH_UNKNOWN';

  insert into public.robot_events (
    robot_id,
    delivery_id,
    command_id,
    event_type,
    severity,
    payload,
    occurred_at
  ) values (
    v_command.robot_id,
    v_command.delivery_id,
    v_command.id,
    'COMMAND_PUBLISH_RECONCILED',
    'WARNING',
    jsonb_build_object(
      'resolution', p_resolution,
      'resolvedBy', p_actor_id
    ),
    statement_timestamp()
  );

  return true;
end;
$$;

revoke all on function public.resolve_unknown_robot_command(uuid, uuid, text)
from public, anon, authenticated;

grant execute on function public.resolve_unknown_robot_command(uuid, uuid, text)
to service_role;

create or replace function public.finalize_robot_command_publish(
  p_command_id uuid,
  p_robot_id text,
  p_delivery_id uuid,
  p_published_at timestamptz
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_command public.robot_commands%rowtype;
  v_command_status text;
  v_delivery_status public.delivery_status;
begin
  if p_published_at is null then
    raise exception 'Publication timestamp is required';
  end if;

  select *
  into v_command
  from public.robot_commands
  where id = p_command_id
  for update;

  if not found
    or v_command.robot_id is distinct from p_robot_id
    or v_command.delivery_id is distinct from p_delivery_id then
    raise exception 'Published command identity does not match its audit row';
  end if;
  if v_command.status in ('REJECTED', 'FAILED', 'EXPIRED') then
    update public.robot_commands
    set published_at = coalesce(published_at, p_published_at)
    where id = p_command_id;
    return v_command.status;
  end if;

  update public.robot_commands
  set status = case
        when status in ('PENDING', 'PUBLISH_UNKNOWN') then 'PUBLISHED'
        else status
      end,
      published_at = coalesce(published_at, p_published_at)
  where id = p_command_id
  returning status into v_command_status;

  if p_delivery_id is not null and v_command.command_type = 'START_MISSION' then
    update public.deliveries
    set status = 'DISPATCHED',
        dispatched_at = coalesce(dispatched_at, p_published_at)
    where id = p_delivery_id
      and robot_id = p_robot_id
      and status = 'ASSIGNED'
    returning status into v_delivery_status;

    if not found then
      select status
      into v_delivery_status
      from public.deliveries
      where id = p_delivery_id
        and robot_id = p_robot_id
      for update;

      if not found or v_delivery_status not in (
        'DISPATCHED', 'TO_SOURCE', 'AT_SOURCE', 'PACKAGE_LOADED',
        'TO_DESTINATION', 'AT_DESTINATION', 'DELIVERED', 'RETURNING',
        'PAUSED', 'COMPLETED'
      ) then
        raise exception 'Published command conflicts with delivery state';
      end if;
    end if;
  end if;

  return v_command_status;
end;
$$;

revoke all on function public.finalize_robot_command_publish(
  uuid, text, uuid, timestamptz
) from public, anon, authenticated;

grant execute on function public.finalize_robot_command_publish(
  uuid, text, uuid, timestamptz
) to service_role;

create or replace function public.enforce_robot_safety_latch()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_reset_robot_id text := current_setting('app.robot_safety_reset', true);
  v_new_safety_epoch_robot_id text :=
    current_setting('app.robot_new_safety_epoch', true);
  v_old_latched boolean;
begin
  if tg_op = 'INSERT' then
    if new.mode in ('ESTOP', 'FAULT') or new.status = 'FAULT' then
      if new.mode not in ('ESTOP', 'FAULT') then
        new.mode := 'FAULT';
      end if;
      new.status := 'FAULT';
      new.speed_mps := 0;
      -- Use trusted database receipt time. Older control/telemetry timestamps
      -- must never make a pre-latch RESUME command look newer than the latch.
      new.safety_latched_at := statement_timestamp();
    elsif new.mode = 'PAUSED' or new.status = 'OFFLINE' then
      new.speed_mps := 0;
      new.safety_latched_at := null;
    else
      new.safety_latched_at := null;
    end if;
    return new;
  end if;

  v_old_latched := old.mode in ('ESTOP', 'FAULT');

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

  if new.mode in ('ESTOP', 'FAULT') or new.status = 'FAULT' then
    if new.mode not in ('ESTOP', 'FAULT') then
      new.mode := 'FAULT';
    end if;
    new.status := 'FAULT';
    new.speed_mps := 0;
    if v_old_latched then
      new.safety_latched_at := case
        when v_new_safety_epoch_robot_id = old.id then statement_timestamp()
        else coalesce(old.safety_latched_at, statement_timestamp())
      end;
    else
      new.safety_latched_at := statement_timestamp();
    end if;
  elsif new.mode = 'PAUSED' or new.status = 'OFFLINE' then
    new.speed_mps := 0;
    new.safety_latched_at := null;
  else
    new.safety_latched_at := null;
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_robot_safety_latch()
from public, anon, authenticated;

drop trigger if exists robots_enforce_safety_latch on public.robots;
drop trigger if exists robots_enforce_safety_latch_insert on public.robots;

create trigger robots_enforce_safety_latch
before update of status, mode, speed_mps, safety_latched_at on public.robots
for each row execute function public.enforce_robot_safety_latch();

create trigger robots_enforce_safety_latch_insert
before insert on public.robots
for each row execute function public.enforce_robot_safety_latch();

create or replace function public.apply_robot_state_observed(
  p_robot_id text,
  p_observed_at timestamptz,
  p_broker_observed_at timestamptz,
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
  v_preserve_pause boolean;
  v_apply_bridge_observation boolean;
begin
  if p_observed_at is null or p_broker_observed_at is null then
    raise exception 'State and broker observation timestamps are required';
  end if;
  if p_status not in ('ONLINE', 'BUSY', 'CHARGING', 'OFFLINE', 'FAULT') then
    raise exception 'Invalid robot status';
  end if;
  if p_mode not in ('IDLE', 'AUTO', 'MANUAL', 'PAUSED', 'ESTOP', 'FAULT') then
    raise exception 'Invalid robot mode';
  end if;
  if p_battery is null or p_signal is null
    or p_battery not between 0 and 100
    or p_signal not between 0 and 100 then
    raise exception 'Battery and signal must be between 0 and 100';
  end if;
  if p_speed_mps is null or p_speed_mps < 0 or p_speed_mps > 5 then
    raise exception 'Speed must be between 0 and 5 m/s';
  end if;
  if p_motor_temp_c is null
    or p_motor_temp_c < -20 or p_motor_temp_c > 150 then
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

  v_apply_bridge_observation := v_robot.bridge_last_seen is null
    or p_broker_observed_at > v_robot.bridge_last_seen;
  if not v_apply_bridge_observation and not v_robot.bridge_online then
    -- The broker already observed a newer disconnect. A delayed HTTP retry of
    -- an older state message must not revive the robot.
    return false;
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

  v_preserve_latch := (v_robot.mode = 'ESTOP' and p_mode <> 'ESTOP')
    or (v_robot.mode = 'FAULT' and p_mode not in ('FAULT', 'ESTOP'));
  v_preserve_pause := v_robot.mode = 'PAUSED' and p_mode <> 'PAUSED';

  update public.robots
  set status = case
        when v_preserve_latch then 'FAULT'::public.robot_status
        else p_status::public.robot_status
      end,
      mode = case
        when v_preserve_latch then v_robot.mode
        when v_preserve_pause then 'PAUSED'::public.robot_mode
        else p_mode::public.robot_mode
      end,
      battery = p_battery,
      signal = p_signal,
      speed_mps = case
        when v_preserve_latch or v_preserve_pause then 0
        else p_speed_mps
      end,
      location_id = coalesce(p_location_id, location_id),
      current_delivery_id = case
        -- Once a pause or safety latch owns a mission, a later state frame
        -- cannot clear or replace that ownership. Only the validated event
        -- workflow may release it.
        when v_robot.mode in ('PAUSED', 'ESTOP', 'FAULT')
          then v_robot.current_delivery_id
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
      telemetry_received_at = statement_timestamp(),
      last_seen = statement_timestamp(),
      bridge_online = case
        when v_apply_bridge_observation then true
        else bridge_online
      end,
      bridge_last_seen = case
        when v_apply_bridge_observation then p_broker_observed_at
        else bridge_last_seen
      end,
      updated_at = statement_timestamp()
  where id = p_robot_id;

  return true;
end;
$$;

revoke all on function public.apply_robot_state_observed(
  text, timestamptz, timestamptz, text, text, integer, integer, numeric,
  text, uuid, text, text, text, numeric, text
) from public, anon, authenticated;

grant execute on function public.apply_robot_state_observed(
  text, timestamptz, timestamptz, text, text, integer, integer, numeric,
  text, uuid, text, text, text, numeric, text
) to service_role;

-- Compatibility wrapper for an older deployed ingestion function. New code
-- must use apply_robot_state_observed so broker ordering remains authoritative.
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
language sql
security definer
set search_path = public
as $$
  select public.apply_robot_state_observed(
    p_robot_id,
    p_observed_at,
    statement_timestamp(),
    p_status,
    p_mode,
    p_battery,
    p_signal,
    p_speed_mps,
    p_location_id,
    p_current_delivery_id,
    p_lidar,
    p_camera,
    p_esp32,
    p_motor_temp_c,
    p_firmware_version
  );
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
  v_delivery_robot_id text;
  v_delivery_status public.delivery_status;
  v_robot public.robots%rowtype;
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
  if p_occurred_at < v_command.issued_at - interval '5 minutes' then
    raise exception 'Acknowledgement predates the command';
  end if;
  if v_command.status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED')
    and p_occurred_at > v_command.expires_at then
    raise exception 'Acknowledgement occurred after the command expired';
  end if;
  if v_command.status = p_status then
    return false;
  end if;
  if v_command.status = 'EXPIRED' and p_occurred_at <= v_command.expires_at then
    if p_status in ('ACKNOWLEDGED', 'COMPLETED')
      and v_command.command_type = 'START_MISSION' then
      select robot_id, status
      into v_delivery_robot_id, v_delivery_status
      from public.deliveries
      where id = v_command.delivery_id
      for update;
      if not found
        or v_delivery_robot_id is distinct from v_command.robot_id
        or v_delivery_status not in ('ASSIGNED', 'DISPATCHED') then
        raise exception 'On-time acknowledgement no longer matches the mission reservation';
      end if;
      if exists (
        select 1
        from public.robot_commands other_command
        where other_command.id <> v_command.id
          and other_command.command_type = 'START_MISSION'
          and other_command.sequence_no > v_command.sequence_no
          and (
            other_command.robot_id = v_command.robot_id
            or (
              v_command.delivery_id is not null
              and other_command.delivery_id = v_command.delivery_id
            )
          )
      ) then
        raise exception 'On-time acknowledgement conflicts with a newer mission command';
      end if;
    end if;
    if p_status in ('ACKNOWLEDGED', 'COMPLETED')
      and v_command.command_type in ('PAUSE', 'RESUME', 'RETURN_HOME') then
      if exists (
        select 1
        from public.robot_commands other_command
        where other_command.robot_id = v_command.robot_id
          and other_command.command_type in ('PAUSE', 'RESUME', 'RETURN_HOME')
          and other_command.sequence_no > v_command.sequence_no
      ) then
        raise exception 'On-time acknowledgement conflicts with a newer control command';
      end if;

      select *
      into v_robot
      from public.robots
      where id = v_command.robot_id
      for update;

      if v_command.command_type = 'RESUME' and (
        v_robot.mode not in ('PAUSED', 'ESTOP', 'FAULT')
        or (
          v_robot.mode in ('ESTOP', 'FAULT')
          and (
            v_robot.safety_latched_at is null
            or v_command.issued_at <= v_robot.safety_latched_at
          )
        )
        or (
          v_robot.mode = 'PAUSED'
          and (
            v_robot.control_event_received_at is null
            or v_command.issued_at <= v_robot.control_event_received_at
          )
        )
      ) then
        raise exception 'On-time RESUME acknowledgement is from an obsolete safety epoch';
      end if;
      if v_command.command_type in ('PAUSE', 'RETURN_HOME')
        and v_robot.mode in ('ESTOP', 'FAULT') then
        raise exception 'On-time control acknowledgement was superseded by a safety latch';
      end if;
      if v_command.command_type = 'RETURN_HOME' then
        select robot_id, status
        into v_delivery_robot_id, v_delivery_status
        from public.deliveries
        where id = v_command.delivery_id
        for update;
        if not found
          or v_robot.current_delivery_id is distinct from v_command.delivery_id
          or v_delivery_robot_id is distinct from v_command.robot_id
          or v_delivery_status in ('COMPLETED', 'CANCELLED', 'FAILED') then
          raise exception 'On-time RETURN_HOME acknowledgement no longer matches an active delivery';
        end if;
      end if;
    end if;

    update public.robot_commands
    set status = p_status,
        acknowledged_at = p_occurred_at,
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'reason', left(coalesce(p_reason, ''), 240),
          'at', p_occurred_at,
          'reconciledAfterExpiration', true
        )
    where id = p_command_id;
    return true;
  end if;
  if v_command.status in ('REJECTED', 'COMPLETED', 'FAILED', 'EXPIRED') then
    if p_status = 'ACKNOWLEDGED' then
      return false;
    end if;
    raise exception 'Acknowledgement conflicts with terminal command status';
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
  v_now timestamptz := statement_timestamp();
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

  v_telemetry_stale := v_robot.telemetry_received_at is null
    or v_robot.telemetry_received_at < v_now - interval '60 seconds';

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
    return true;
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

  -- The boolean reports whether this observation was applied, not whether it
  -- also changed operational status. Only an older observation returns false.
  return true;
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
  v_existing public.robot_events%rowtype;
  v_control_event_at timestamptz;
  v_control_event_received_at timestamptz;
  v_safety_latched_at timestamptz;
  v_telemetry_at timestamptz;
  v_telemetry_received_at timestamptz;
  v_robot_mode public.robot_mode;
  v_robot_speed numeric;
  v_robot_lidar text;
  v_robot_camera text;
  v_robot_esp32 text;
  v_bridge_online boolean;
  v_bridge_last_seen timestamptz;
  v_command public.robot_commands%rowtype;
  v_is_mission_event boolean := new.event_type in (
    'MISSION_STARTED', 'ARRIVED_SOURCE', 'PACKAGE_LOADED',
    'DEPARTED_SOURCE', 'ARRIVED_DESTINATION', 'PACKAGE_RELEASED',
    'RETURNING_HOME', 'MISSION_COMPLETED', 'MISSION_FAILED'
  );
  v_is_ordered_event boolean := new.event_type in (
    'MISSION_STARTED', 'ARRIVED_SOURCE', 'PACKAGE_LOADED',
    'DEPARTED_SOURCE', 'ARRIVED_DESTINATION', 'PACKAGE_RELEASED',
    'RETURNING_HOME', 'MISSION_COMPLETED', 'MISSION_FAILED',
    'PAUSED', 'RESUMED', 'ESTOP_TRIGGERED'
  );
begin
  if new.message_id is not null then
    perform pg_advisory_xact_lock(hashtextextended(new.message_id::text, 0));

    select *
    into v_existing
    from public.robot_events
    where message_id = new.message_id;

    if found then
      if v_existing.robot_id is distinct from new.robot_id
        or v_existing.delivery_id is distinct from new.delivery_id
        or v_existing.command_id is distinct from new.command_id
        or v_existing.event_type is distinct from new.event_type
        or v_existing.severity is distinct from new.severity
        or coalesce(v_existing.payload, '{}'::jsonb)
          is distinct from coalesce(new.payload, '{}'::jsonb)
        or v_existing.occurred_at is distinct from new.occurred_at then
        raise exception 'Event ID is already used by different event content';
      end if;
      return new;
    end if;
  end if;

  if v_is_ordered_event then
    select control_event_at, control_event_received_at, safety_latched_at,
           telemetry_at, telemetry_received_at, mode,
           speed_mps, lidar, camera, esp32, bridge_online, bridge_last_seen
    into v_control_event_at, v_control_event_received_at, v_safety_latched_at,
         v_telemetry_at, v_telemetry_received_at, v_robot_mode,
         v_robot_speed, v_robot_lidar, v_robot_camera, v_robot_esp32,
         v_bridge_online, v_bridge_last_seen
    from public.robots
    where id = new.robot_id
    for update;

    if not found then
      raise exception 'Unknown robot';
    end if;
  end if;

  if v_is_mission_event then
    if new.delivery_id is null or new.command_id is null then
      raise exception 'Mission event requires deliveryId and commandId';
    end if;

    select *
    into v_command
    from public.robot_commands
    where id = new.command_id
    for update;

    if not found
      or v_command.robot_id is distinct from new.robot_id
      or v_command.delivery_id is distinct from new.delivery_id
      or v_command.command_type is distinct from 'START_MISSION'
      or (
        new.event_type = 'MISSION_STARTED'
        and v_command.status not in (
          'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED',
          'EXPIRED'
        )
      )
      or (
        new.event_type not in (
          'MISSION_STARTED', 'MISSION_COMPLETED', 'MISSION_FAILED'
        )
        and v_command.status not in ('PUBLISHED', 'ACKNOWLEDGED')
      )
      or (
        new.event_type = 'MISSION_COMPLETED'
        and v_command.status not in (
          'PUBLISHED', 'ACKNOWLEDGED', 'COMPLETED'
        )
      )
      or (
        new.event_type = 'MISSION_FAILED'
        and v_command.status not in (
          'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED',
          'FAILED', 'EXPIRED'
        )
      )
      or new.occurred_at < v_command.issued_at
      or (
        new.event_type = 'MISSION_STARTED'
        and new.occurred_at > v_command.expires_at
      ) then
      raise exception 'Mission event requires its valid START_MISSION command';
    end if;

    if new.event_type = 'MISSION_FAILED'
      and v_command.status = 'EXPIRED' then
      if new.occurred_at > v_command.expires_at then
        raise exception 'Expired mission failure evidence occurred after command expiry';
      end if;
      if exists (
        select 1
        from public.robot_commands as newer_command
        where newer_command.id <> v_command.id
          and newer_command.command_type = 'START_MISSION'
          and newer_command.sequence_no > v_command.sequence_no
          and (
            newer_command.robot_id = v_command.robot_id
            or newer_command.delivery_id = v_command.delivery_id
          )
      ) then
        raise exception 'Expired mission failure evidence conflicts with a newer mission command';
      end if;

      -- Preserve the delayed but on-time robot evidence. The legacy RPC's
      -- post-insert transition can then attach its normal failure result.
      update public.robot_commands
      set status = 'FAILED'
      where id = new.command_id
        and status = 'EXPIRED';
    end if;

    if new.event_type = 'MISSION_STARTED'
      and v_command.status in (
        'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'EXPIRED'
      ) then
      if v_command.status = 'EXPIRED' and exists (
        select 1
        from public.robot_commands other_command
        where other_command.id <> v_command.id
          and other_command.command_type = 'START_MISSION'
          and other_command.sequence_no > v_command.sequence_no
          and (
            other_command.robot_id = v_command.robot_id
            or other_command.delivery_id = v_command.delivery_id
          )
      ) then
        raise exception 'On-time mission evidence conflicts with a newer mission command';
      end if;

      -- A robot MISSION_STARTED event proves that the broker delivered the
      -- command even if its ACK or the HTTP publish response raced the Edge
      -- Function. Prevent the expiration job from expiring an active mission.
      update public.robot_commands
      set status = 'ACKNOWLEDGED',
          acknowledged_at = coalesce(acknowledged_at, new.occurred_at),
          result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
            'eventId', new.message_id,
            'eventType', new.event_type,
            'at', new.occurred_at,
            'acknowledgedByEvent', true
          )
      where id = new.command_id
        and status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'EXPIRED');
    end if;
  elsif new.event_type = 'RESUMED' then
    if new.command_id is null then
      raise exception 'RESUMED requires its authorized RESUME command';
    end if;
    if v_robot_mode not in ('PAUSED', 'ESTOP', 'FAULT') then
      raise exception 'Robot is not paused or safety-latched';
    end if;

    select *
    into v_command
    from public.robot_commands
    where id = new.command_id
    for update;

    if not found
      or v_command.robot_id is distinct from new.robot_id
      or v_command.command_type is distinct from 'RESUME'
      or v_command.status not in (
        'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED',
        'COMPLETED', 'EXPIRED'
      )
      or (
        v_command.status = 'COMPLETED'
        and coalesce(v_command.result, '{}'::jsonb)
          @> '{"consumed": true}'::jsonb
      )
      or new.occurred_at < v_command.issued_at
      or new.occurred_at > v_command.expires_at
      or not exists (
        select 1
        from public.profiles
        where id = v_command.issued_by
          and role in ('ADMIN', 'OPERATOR')
      ) then
      raise exception 'RESUMED requires an active staff-issued RESUME command';
    end if;

    if v_command.status = 'EXPIRED' and exists (
      select 1
      from public.robot_commands as newer_command
      where newer_command.robot_id = v_command.robot_id
        and newer_command.command_type in ('PAUSE', 'RESUME', 'RETURN_HOME')
        and newer_command.sequence_no > v_command.sequence_no
    ) then
      raise exception 'Expired RESUME evidence conflicts with a newer control command';
    end if;

    if not (coalesce(new.payload, '{}'::jsonb)
      @> '{"localSafetyChecksPassed": true}'::jsonb) then
      raise exception 'RESUMED requires confirmed local safety checks';
    end if;
    if not coalesce(v_bridge_online, false)
      or v_bridge_last_seen is null
      or v_bridge_last_seen < statement_timestamp() - interval '60 seconds'
      or v_telemetry_at is null
      or v_telemetry_received_at is null
      or v_telemetry_received_at < statement_timestamp() - interval '60 seconds'
      or v_robot_speed <> 0
      or v_robot_lidar <> 'OK'
      or v_robot_camera <> 'OK'
      or v_robot_esp32 <> 'OK' then
      raise exception 'RESUMED requires fresh safe robot telemetry';
    end if;

    if v_robot_mode in ('ESTOP', 'FAULT') then
      if v_safety_latched_at is null
        or v_command.issued_at <= v_safety_latched_at then
        raise exception 'RESUME command must be newer than the safety latch';
      end if;
      if v_telemetry_received_at <= v_safety_latched_at then
        raise exception 'RESUMED requires telemetry observed after the safety latch';
      end if;
      if new.occurred_at <= v_telemetry_at then
        raise exception 'RESUMED event is older than the current safety state';
      end if;
    elsif v_control_event_received_at is not null
      and v_command.issued_at <= v_control_event_received_at then
      raise exception 'RESUME command must be newer than PAUSED';
    elsif v_control_event_received_at is not null
      and v_telemetry_received_at <= v_control_event_received_at then
      raise exception 'RESUMED requires telemetry observed after PAUSED';
    end if;
  end if;

  if not v_is_ordered_event then
    return new;
  end if;

  if v_control_event_at is not null
    and new.occurred_at <= v_control_event_at then
    raise exception 'Robot control event is older than the current control state';
  end if;

  if v_robot_mode in ('ESTOP', 'FAULT')
    and v_is_mission_event
    and new.event_type <> 'MISSION_FAILED' then
    raise exception 'Robot safety latch blocks mission progress until RESUMED';
  end if;
  if v_robot_mode = 'PAUSED'
    and v_is_mission_event
    and new.event_type <> 'MISSION_FAILED' then
    raise exception 'Robot pause blocks mission progress until RESUMED';
  end if;
  if v_robot_mode in ('ESTOP', 'FAULT') and new.event_type = 'PAUSED' then
    raise exception 'Robot safety latch requires an authorized RESUMED event';
  end if;

  if new.event_type = 'RESUMED' then
    update public.robot_commands
    set status = 'COMPLETED',
        acknowledged_at = coalesce(acknowledged_at, new.occurred_at),
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'eventId', new.message_id,
          'eventType', new.event_type,
          'at', new.occurred_at,
          'consumed', true
        )
    where id = new.command_id
      and (
        status in (
          'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED',
          'EXPIRED'
        )
        or (
          status = 'COMPLETED'
          and not (
            coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
          )
        )
      );
    if not found then
      raise exception 'RESUME command was already consumed';
    end if;
    perform set_config('app.robot_safety_reset', new.robot_id, true);
  elsif new.event_type = 'PAUSED' and new.command_id is not null then
    update public.robot_commands
    set status = 'COMPLETED',
        acknowledged_at = coalesce(acknowledged_at, new.occurred_at),
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'eventId', new.message_id,
          'eventType', new.event_type,
          'at', new.occurred_at,
          'consumed', true
        )
    where id = new.command_id
      and robot_id = new.robot_id
      and command_type = 'PAUSE'
      and new.occurred_at >= issued_at
      and (
        status = 'ACKNOWLEDGED'
        or (
          status = 'COMPLETED'
          and not (
            coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
          )
        )
        or (
          status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'EXPIRED')
          and new.occurred_at <= expires_at
        )
      );
  elsif new.event_type = 'ESTOP_TRIGGERED' and new.command_id is not null then
    -- ESTOP itself must never be rejected merely because its optional command
    -- linkage is absent or stale. Consume the matching audit command when it
    -- is valid, while always continuing with the safety event.
    update public.robot_commands
    set status = 'COMPLETED',
        acknowledged_at = coalesce(acknowledged_at, new.occurred_at),
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'eventId', new.message_id,
          'eventType', new.event_type,
          'at', new.occurred_at,
          'consumed', true
        )
    where id = new.command_id
      and robot_id = new.robot_id
      and command_type = 'ESTOP'
      and new.occurred_at >= issued_at
      and (
        status = 'ACKNOWLEDGED'
        or (
          status = 'COMPLETED'
          and not (
            coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
          )
        )
        or (
          status in ('PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'EXPIRED')
          and new.occurred_at <= expires_at
        )
      );
  end if;

  if new.event_type in ('ESTOP_TRIGGERED', 'MISSION_FAILED') then
    -- A new independent ESTOP/failure invalidates any reset command issued
    -- after an earlier latch but before this new hazard.
    update public.robot_commands
    set status = 'FAILED',
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'invalidatedByEventId', new.message_id,
          'invalidatedByEventType', new.event_type,
          'invalidatedAt', statement_timestamp()
        )
    where robot_id = new.robot_id
      and command_type in ('PAUSE', 'RESUME', 'RETURN_HOME')
      and (
        status in (
          'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'ACKNOWLEDGED'
        )
        or (
          status = 'COMPLETED'
          and not (
            coalesce(result, '{}'::jsonb) @> '{"consumed": true}'::jsonb
          )
        )
      );
    perform set_config('app.robot_new_safety_epoch', new.robot_id, true);
  end if;

  if new.event_type = 'RETURNING_HOME' then
    update public.robot_commands
    set status = 'COMPLETED',
        acknowledged_at = coalesce(acknowledged_at, new.occurred_at),
        result = coalesce(result, '{}'::jsonb) || jsonb_build_object(
          'eventId', new.message_id,
          'eventType', new.event_type,
          'at', new.occurred_at,
          'consumed', true
        )
    where id = (
      select command.id
      from public.robot_commands as command
      where command.robot_id = new.robot_id
        and command.command_type = 'RETURN_HOME'
        and command.delivery_id = new.delivery_id
        and new.occurred_at >= command.issued_at
        and (
          command.status = 'ACKNOWLEDGED'
          or (
            command.status = 'COMPLETED'
            and not (
              coalesce(command.result, '{}'::jsonb)
                @> '{"consumed": true}'::jsonb
            )
          )
          or (
            command.status in (
              'PENDING', 'PUBLISH_UNKNOWN', 'PUBLISHED', 'EXPIRED'
            )
            and new.occurred_at <= command.expires_at
          )
        )
      order by command.sequence_no desc
      limit 1
    );
  end if;

  update public.robots
  set control_event_at = new.occurred_at,
      control_event_received_at = statement_timestamp()
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

-- Migration 008/009 required DISPATCHED before MISSION_STARTED. The robot
-- event itself is stronger evidence than the still-in-flight HTTP publish
-- response, so 011 also accepts ASSIGNED during that narrow race when the
-- event is linked to the valid START_MISSION command checked above.
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

  if new.message_id is not null then
    perform pg_advisory_xact_lock(hashtextextended(new.message_id::text, 0));
    if exists (
      select 1 from public.robot_events where message_id = new.message_id
    ) then
      return new;
    end if;
  end if;

  if new.delivery_id is null or new.command_id is null then
    raise exception
      'MISSION_STARTED requires its assigned delivery and START_MISSION command';
  end if;

  select delivery.status
  into v_delivery_status
  from public.deliveries as delivery
  where delivery.id = new.delivery_id
    and delivery.robot_id = new.robot_id
  for update;

  if not found
    or v_delivery_status not in ('ASSIGNED', 'DISPATCHED') then
    raise exception
      'MISSION_STARTED is not valid for the current delivery state';
  end if;

  return new;
end;
$$;

revoke all on function public.validate_mission_started_delivery_state()
from public, anon, authenticated;

update public.robots as robot
set control_event_at = greatest(
      coalesce(robot.control_event_at, '-infinity'::timestamptz),
      latest.occurred_at
    ),
    control_event_received_at = coalesce(
      robot.control_event_received_at,
      statement_timestamp()
    )
from (
  select event.robot_id, max(event.occurred_at) as occurred_at
  from public.robot_events as event
  where event.event_type in (
      'MISSION_STARTED', 'ARRIVED_SOURCE', 'PACKAGE_LOADED',
      'DEPARTED_SOURCE', 'ARRIVED_DESTINATION', 'PACKAGE_RELEASED',
      'RETURNING_HOME', 'MISSION_COMPLETED', 'MISSION_FAILED',
      'PAUSED', 'RESUMED', 'ESTOP_TRIGGERED'
    )
  group by event.robot_id
) as latest
where latest.robot_id = robot.id
  and latest.occurred_at is not null
  and (
    robot.control_event_at is null
    or latest.occurred_at > robot.control_event_at
    or robot.control_event_received_at is null
  );

update public.robots
set control_event_received_at = statement_timestamp()
where mode = 'PAUSED'
  and control_event_received_at is null;

update public.robots
set mode = case
      when status = 'FAULT' and mode not in ('ESTOP', 'FAULT')
        then 'FAULT'::public.robot_mode
      else mode
    end,
    status = 'FAULT',
    speed_mps = 0,
    safety_latched_at = statement_timestamp()
where (mode in ('ESTOP', 'FAULT') or status = 'FAULT')
  and safety_latched_at is null;

-- Robot state is ingestion-owned. Browser staff can observe it and issue
-- audited commands, but cannot bypass the event/reset path with table updates.
drop policy if exists "staff update robots" on public.robots;
revoke select on public.robots from anon;
grant select on public.robots to authenticated;
revoke insert, update, delete, truncate, references, trigger
on public.robots from anon, authenticated;

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
    where (
      telemetry_received_at is null
      or telemetry_received_at < now() - interval '60 seconds'
    )
      and (
        status not in ('OFFLINE', 'FAULT')
        or speed_mps <> 0
        or signal <> 0
        or lidar <> 'OFFLINE'
        or camera <> 'OFFLINE'
        or esp32 <> 'OFFLINE'
      )
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
    jsonb_build_object(
      'reason', 'telemetry heartbeat timeout',
      'timeoutSeconds', 60
    ),
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

-- Supabase projects can disable automatic Data API grants. Keep the public
-- schema usable only through the privileges required by the browser and the
-- two server-side Edge Functions, independent of the project's API defaults.
grant usage on schema public to authenticated, service_role;

revoke all on table
  public.profiles,
  public.locations,
  public.robots,
  public.deliveries,
  public.robot_commands,
  public.robot_events,
  public.notifications
from anon, authenticated, service_role;

grant select on table
  public.profiles,
  public.locations,
  public.robots,
  public.deliveries,
  public.robot_commands,
  public.robot_events,
  public.notifications
to authenticated;

grant insert, update, delete on table public.locations to authenticated;
grant insert, update on table public.deliveries to authenticated;
grant update (full_name, email) on table public.profiles to authenticated;

grant select on table
  public.profiles,
  public.robots,
  public.deliveries,
  public.robot_commands
to service_role;

grant insert, update on table public.robot_commands to service_role;

revoke all on sequence
  public.delivery_tracking_sequence,
  public.robot_commands_sequence_no_seq
from anon, authenticated, service_role;

grant usage on sequence public.delivery_tracking_sequence to authenticated;
grant usage on sequence public.robot_commands_sequence_no_seq to service_role;

-- Reassert the event RPC ACL because CREATE OR REPLACE preserves an older
-- function ACL and the final migration should describe the complete contract.
revoke all on function public.apply_robot_event(
  uuid, text, uuid, uuid, text, text, jsonb, timestamptz
) from public, anon, authenticated;

grant execute on function public.apply_robot_event(
  uuid, text, uuid, uuid, text, text, jsonb, timestamptz
) to service_role;

comment on function public.apply_robot_state_observed(
  text, timestamptz, timestamptz, text, text, integer, integer, numeric,
  text, uuid, text, text, text, numeric, text
) is 'Applies device telemetry in device-time order, records server receipt time for freshness, and applies bridge connectivity in EMQX broker-time order.';

comment on function public.apply_robot_presence(text, boolean, text, timestamptz) is
  'Returns true when a fresh broker observation is applied and false only when that observation is stale.';

comment on function public.validate_robot_event_order() is
  'Serializes exact event retries; blocks latched mission progress; consumes one staff-issued post-latch RESUME command to clear safety state.';
