begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(32);

insert into auth.users (
  id,
  email,
  raw_user_meta_data,
  created_at,
  updated_at
) values (
  '60000000-0000-4000-8000-000000000001',
  'presence.operator@example.test',
  '{"full_name":"Presence Test Operator"}'::jsonb,
  now(),
  now()
);

update public.profiles
set role = 'OPERATOR'
where id = '60000000-0000-4000-8000-000000000001';

insert into public.robots (
  id,
  name,
  model,
  status,
  mode,
  battery,
  location_id,
  signal,
  speed_mps,
  lidar,
  camera,
  esp32,
  motor_temp_c,
  last_seen,
  telemetry_at,
  telemetry_received_at,
  bridge_online,
  bridge_last_seen
) values (
  'test-presence-robot',
  'Presence Contract Robot',
  'Test Model',
  'ONLINE',
  'IDLE',
  80,
  'loc-home',
  90,
  0,
  'OK',
  'OK',
  'OK',
  30,
  '2026-07-20 00:00:00+00',
  null,
  null,
  false,
  null
);

select ok(
  has_function_privilege(
    'service_role',
    'public.apply_robot_presence(text,boolean,text,timestamptz)',
    'EXECUTE'
  ),
  'the service role can apply robot presence'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.apply_robot_presence(text,boolean,text,timestamptz)',
    'EXECUTE'
  ),
  'authenticated clients cannot apply robot presence directly'
);

select is(
  public.apply_robot_presence(
    'test-presence-robot',
    true,
    'presence-test-v1',
    '2026-07-21 04:00:00+00'
  ),
  true,
  'online bridge presence transitions a robot with no telemetry to safe OFFLINE'
);

select is(
  (select bridge_online from public.robots where id = 'test-presence-robot'),
  true,
  'online bridge connectivity is recorded separately'
);

select is(
  (select status::text from public.robots where id = 'test-presence-robot'),
  'OFFLINE',
  'presence alone does not claim operational readiness'
);

select is(
  (select last_seen from public.robots where id = 'test-presence-robot'),
  '2026-07-20 00:00:00+00'::timestamptz,
  'bridge presence does not refresh the operational telemetry heartbeat'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where robot_id = 'test-presence-robot'
      and event_type = 'ROBOT_OFFLINE'
  ),
  1,
  'the telemetry timeout transition creates one operational event'
);

update public.robots
set status = 'ONLINE',
    telemetry_at = now(),
    telemetry_received_at = statement_timestamp(),
    last_seen = '2026-07-20 00:00:00+00'
where id = 'test-presence-robot';

select is(
  public.apply_robot_presence(
    'test-presence-robot',
    true,
    'presence-test-v2',
    '2026-07-21 04:00:05+00'
  ),
  true,
  'a fresh online presence is applied even without an operational transition'
);

select is(
  (select status::text from public.robots where id = 'test-presence-robot'),
  'ONLINE',
  'fresh telemetry keeps its operational status'
);

select is(
  (select last_seen from public.robots where id = 'test-presence-robot'),
  '2026-07-20 00:00:00+00'::timestamptz,
  'fresh online presence still leaves last_seen to telemetry'
);

select is(
  public.apply_robot_presence(
    'test-presence-robot',
    false,
    'presence-test-v2',
    '2026-07-21 04:00:10+00'
  ),
  true,
  'offline MQTT presence records an operational transition'
);

select is(
  (select bridge_online from public.robots where id = 'test-presence-robot'),
  false,
  'offline MQTT presence clears bridge connectivity'
);

select is(
  public.apply_robot_event(
    '61000000-0000-4000-8000-000000000001',
    'test-presence-robot',
    null,
    null,
    'ESTOP_TRIGGERED',
    'CRITICAL',
    '{}'::jsonb,
    '2026-07-21 03:00:00+00'
  ),
  true,
  'a current ESTOP control event is applied'
);

select throws_ok(
  $$
    insert into public.robot_commands (
      id,
      robot_id,
      command_type,
      payload,
      status,
      issued_by,
      issued_at,
      published_at,
      acknowledged_at,
      expires_at
    ) values (
      '62000000-0000-4000-8000-000000000001',
      'test-presence-robot',
      'RESUME',
      '{}'::jsonb,
      'COMPLETED',
      '60000000-0000-4000-8000-000000000001',
      '2026-07-21 02:50:00+00',
      '2026-07-21 02:50:01+00',
      '2026-07-21 02:50:02+00',
      '2099-01-01 00:00:00+00'
    )
  $$,
  'P0001',
  'RESUME must be issued after the current safety latch',
  'the database cannot reserve a stale pre-latch RESUME command'
);

select is(
  (select mode::text from public.robots where id = 'test-presence-robot'),
  'ESTOP',
  'the rejected delayed event leaves ESTOP active'
);

-- apply_robot_event marks a new safety epoch with a transaction-local GUC so
-- the latch trigger can use trusted receipt time. Production ingestion handles
-- one message per transaction; this pgTAP file intentionally keeps every case
-- in one outer transaction, so clear that fixture-only flag before simulating
-- the later telemetry message. Otherwise the state update would re-stamp the
-- latch at the same statement_timestamp as telemetry_received_at.
do $$
begin
  perform set_config('app.robot_new_safety_epoch', '', true);
end
$$;

insert into public.robot_commands (
  id,
  robot_id,
  command_type,
  payload,
  status,
  issued_by,
  issued_at,
  published_at,
  acknowledged_at,
  expires_at
)
select
  '62000000-0000-4000-8000-000000000002',
  'test-presence-robot',
  'RESUME',
  '{}'::jsonb,
  'COMPLETED',
  '60000000-0000-4000-8000-000000000001',
  safety_latched_at + interval '1 second',
  safety_latched_at + interval '1 second',
  safety_latched_at + interval '1 second',
  safety_latched_at + interval '5 minutes'
from public.robots
where id = 'test-presence-robot';

select is(
  public.apply_robot_state(
    'test-presence-robot',
    (
      select safety_latched_at + interval '2 seconds'
      from public.robots
      where id = 'test-presence-robot'
    ),
    'ONLINE',
    'AUTO',
    75,
    80,
    1.0,
    'loc-home',
    null,
    'OK',
    'OK',
    'OK',
    32,
    'state-cannot-reset-estop'
  ),
  true,
  'fresh telemetry is recorded while the safety latch is active'
);

select is(
  (select mode::text from public.robots where id = 'test-presence-robot'),
  'ESTOP',
  'fresh telemetry cannot clear ESTOP'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000003',
      'test-presence-robot',
      null,
      null,
      'RESUMED',
      'INFO',
      '{}'::jsonb,
      (
        select safety_latched_at + interval '3 seconds'
        from public.robots
        where id = 'test-presence-robot'
      )
    )
  $$,
  'P0001',
  'RESUMED requires its authorized RESUME command',
  'an unlinked RESUMED event cannot clear ESTOP'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000007',
      'test-presence-robot',
      null,
      '62000000-0000-4000-8000-000000000002',
      'RESUMED',
      'INFO',
      '{}'::jsonb,
      (
        select safety_latched_at + interval '3 seconds'
        from public.robots
        where id = 'test-presence-robot'
      )
    )
  $$,
  'P0001',
  'RESUMED requires confirmed local safety checks',
  'RESUMED requires explicit local safety evidence'
);

select is(
  public.apply_robot_event(
      '61000000-0000-4000-8000-000000000004',
      'test-presence-robot',
      null,
      '62000000-0000-4000-8000-000000000002',
    'RESUMED',
    'INFO',
    '{"localSafetyChecksPassed":true}'::jsonb,
    (
      select safety_latched_at + interval '3 seconds'
      from public.robots
      where id = 'test-presence-robot'
    )
  ),
  true,
  'a current RESUMED event linked to an authorized RESUME command is applied'
);

select is(
  (select mode::text from public.robots where id = 'test-presence-robot'),
  'IDLE',
  'the authorized robot event clears the safety latch'
);

select is(
  (
    select status = 'COMPLETED'
      and result @> '{"consumed":true}'::jsonb
    from public.robot_commands
    where id = '62000000-0000-4000-8000-000000000002'
  ),
  true,
  'a completed ACK race still lets RESUMED consume the command exactly once'
);

update public.robots
set mode = 'PAUSED',
    speed_mps = 0,
    control_event_at = now() + interval '2 seconds',
    control_event_received_at = statement_timestamp()
where id = 'test-presence-robot';

insert into public.robot_commands (
  id,
  robot_id,
  command_type,
  payload,
  status,
  issued_by,
  issued_at,
  published_at,
  acknowledged_at,
  expires_at
) values (
  '62000000-0000-4000-8000-000000000003',
  'test-presence-robot',
  'RESUME',
  '{}'::jsonb,
  'ACKNOWLEDGED',
  '60000000-0000-4000-8000-000000000001',
  statement_timestamp() + interval '1 millisecond',
  statement_timestamp() + interval '1 millisecond',
  statement_timestamp() + interval '1 millisecond',
  statement_timestamp() + interval '5 minutes'
);

select is(
  public.apply_robot_event(
    '61000000-0000-4000-8000-000000000005',
    'test-presence-robot',
    null,
    null,
    'ESTOP_TRIGGERED',
    'CRITICAL',
    '{}'::jsonb,
    now() + interval '3 seconds'
  ),
  true,
  'a later ESTOP creates a new safety latch'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000008',
      'test-presence-robot',
      null,
      '62000000-0000-4000-8000-000000000003',
      'RESUMED',
      'INFO',
      '{"localSafetyChecksPassed":true}'::jsonb,
      now() + interval '4 seconds'
    )
  $$,
  'P0001',
  'RESUMED requires an active staff-issued RESUME command',
  'a new ESTOP invalidates a RESUME issued in the previous safety epoch'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000006',
      'test-presence-robot',
      null,
      '62000000-0000-4000-8000-000000000002',
      'RESUMED',
      'INFO',
      '{"localSafetyChecksPassed":true}'::jsonb,
      now() + interval '4 seconds'
    )
  $$,
  'P0001',
  'RESUMED requires an active staff-issued RESUME command',
  'a consumed RESUME command cannot clear another latch'
);

select is(
  (select mode::text from public.robots where id = 'test-presence-robot'),
  'ESTOP',
  'the second latch remains active after command reuse is rejected'
);

select is(
  public.apply_robot_event(
    '61000000-0000-4000-8000-000000000001',
    'test-presence-robot',
    null,
    null,
    'ESTOP_TRIGGERED',
    'CRITICAL',
    '{}'::jsonb,
    '2026-07-21 03:00:00+00'
  ),
  false,
  'an identical QoS retry bypasses order rejection and remains idempotent'
);

insert into public.robots (
  id, name, model, status, mode, battery, location_id, signal, speed_mps,
  lidar, camera, esp32, motor_temp_c, last_seen, telemetry_at,
  telemetry_received_at, bridge_online, bridge_last_seen, control_event_at,
  control_event_received_at
) values (
  'test-expired-resume-robot',
  'Expired Resume Robot',
  'Test Model',
  'BUSY',
  'PAUSED',
  80,
  'loc-home',
  90,
  0,
  'OK',
  'OK',
  'OK',
  30,
  statement_timestamp(),
  now() - interval '1 second',
  now(),
  true,
  now(),
  now() - interval '2 minutes',
  now() - interval '2 minutes'
);

insert into public.robot_commands (
  id, robot_id, command_type, payload, status, issued_by, issued_at,
  expires_at
) values (
  '62000000-0000-4000-8000-000000000006',
  'test-expired-resume-robot',
  'RESUME',
  '{}'::jsonb,
  'PUBLISHED',
  '60000000-0000-4000-8000-000000000001',
  now() - interval '90 seconds',
  now() - interval '30 seconds'
);

update public.robot_commands
set status = 'EXPIRED'
where id = '62000000-0000-4000-8000-000000000006';

select is(
  public.apply_robot_event(
    '61000000-0000-4000-8000-000000000009',
    'test-expired-resume-robot',
    null,
    '62000000-0000-4000-8000-000000000006',
    'RESUMED',
    'INFO',
    '{"localSafetyChecksPassed":true}'::jsonb,
    now() - interval '60 seconds'
  ),
  true,
  'an on-time RESUMED event reconciles after the expiration cron race'
);

select ok(
  (
    select status = 'COMPLETED'
      and result @> '{"consumed":true}'::jsonb
    from public.robot_commands
    where id = '62000000-0000-4000-8000-000000000006'
  ),
  'the reconciled expired RESUME command is consumed exactly once'
);

update public.robots
set status = 'BUSY',
    mode = 'PAUSED',
    speed_mps = 0,
    telemetry_at = now() - interval '1 second',
    telemetry_received_at = now(),
    bridge_online = true,
    bridge_last_seen = now(),
    control_event_at = now() - interval '20 seconds',
    control_event_received_at = now() - interval '20 seconds'
where id = 'test-expired-resume-robot';

insert into public.robot_commands (
  id, robot_id, command_type, payload, status, issued_by, issued_at,
  expires_at
) values (
  '62000000-0000-4000-8000-000000000007',
  'test-expired-resume-robot',
  'RESUME',
  '{}'::jsonb,
  'PUBLISHED',
  '60000000-0000-4000-8000-000000000001',
  now() - interval '15 seconds',
  now() - interval '5 seconds'
);

update public.robot_commands
set status = 'EXPIRED'
where id = '62000000-0000-4000-8000-000000000007';

insert into public.robot_commands (
  id, robot_id, command_type, payload, status, issued_by, issued_at,
  expires_at
) values (
  '62000000-0000-4000-8000-000000000008',
  'test-expired-resume-robot',
  'RESUME',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '60000000-0000-4000-8000-000000000001',
  now(),
  now() + interval '5 minutes'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000010',
      'test-expired-resume-robot',
      null,
      '62000000-0000-4000-8000-000000000007',
      'RESUMED',
      'INFO',
      '{"localSafetyChecksPassed":true}'::jsonb,
      now() - interval '10 seconds'
    )
  $$,
  'P0001',
  'Expired RESUME evidence conflicts with a newer control command',
  'expired RESUME evidence cannot consume a newer control authorization'
);

update public.robots
set bridge_online = true,
    bridge_last_seen = now() - interval '61 seconds'
where id = 'test-presence-robot';

select lives_ok(
  $$ select public.mark_stale_robots_offline() $$,
  'the maintenance job handles stale bridge connectivity'
);

select is(
  (select bridge_online from public.robots where id = 'test-presence-robot'),
  false,
  'the maintenance job clears an expired bridge heartbeat'
);

select * from finish();
rollback;
