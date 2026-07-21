begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(18);

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
    last_seen = '2026-07-20 00:00:00+00'
where id = 'test-presence-robot';

select is(
  public.apply_robot_presence(
    'test-presence-robot',
    true,
    'presence-test-v2',
    '2026-07-21 04:00:05+00'
  ),
  false,
  'online presence with fresh telemetry does not change operational state'
);

select is(
  (select status::text from public.robots where id = 'test-presence-robot'),
  'ONLINE',
  'fresh telemetry keeps its operational status'
);

select is(
  (select last_seen from public.robots where id = 'test-presence-robot'),
  '2026-07-20 00:00:00+00'::timestamptz,
  'fresh online presence still leaves last_seen to telemetry and events'
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
    select public.apply_robot_event(
      '61000000-0000-4000-8000-000000000002',
      'test-presence-robot',
      null,
      null,
      'RESUMED',
      'INFO',
      '{}'::jsonb,
      '2026-07-21 02:59:59+00'
    )
  $$,
  'P0001',
  'Robot control event is older than the current control state',
  'a delayed RESUMED event cannot clear a newer ESTOP'
);

select is(
  (select mode::text from public.robots where id = 'test-presence-robot'),
  'ESTOP',
  'the rejected delayed event leaves ESTOP active'
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
