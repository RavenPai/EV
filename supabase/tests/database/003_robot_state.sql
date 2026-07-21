begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(9);

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
  telemetry_at,
  last_seen
)
values (
  'test-state-robot',
  'State Test Robot',
  'Test Model',
  'ONLINE',
  'IDLE',
  90,
  'loc-home',
  90,
  0,
  'OK',
  'OK',
  'OK',
  30,
  '2026-07-20 09:59:00+00',
  '2026-07-20 09:59:00+00'
);

select is(
  public.apply_robot_state(
    'test-state-robot',
    '2026-07-20 10:00:20+00',
    'BUSY',
    'AUTO',
    70,
    84,
    1.25,
    'loc-fcs',
    null,
    'OK',
    'WARNING',
    'OK',
    41.5,
    'state-test-v1'
  ),
  true,
  'a newer robot-state sample is applied'
);

select is(
  (
    select status::text
    from public.robots
    where id = 'test-state-robot'
  ),
  'BUSY',
  'the newer sample updates robot status'
);

select is(
  (
    select battery
    from public.robots
    where id = 'test-state-robot'
  ),
  70,
  'the newer sample updates telemetry values'
);

select is(
  public.apply_robot_state(
    'test-state-robot',
    '2026-07-20 10:00:20+00',
    'BUSY',
    'AUTO',
    69,
    83,
    1.1,
    'loc-fcs',
    null,
    'OK',
    'OK',
    'OK',
    42,
    'state-test-v1'
  ),
  true,
  'a sample with the same observed timestamp is accepted'
);

select is(
  (
    select battery
    from public.robots
    where id = 'test-state-robot'
  ),
  69,
  'the equal-timestamp sample replaces telemetry values'
);

select is(
  public.apply_robot_state(
    'test-state-robot',
    '2026-07-20 10:00:10+00',
    'FAULT',
    'FAULT',
    1,
    1,
    0,
    'loc-home',
    null,
    'OFFLINE',
    'OFFLINE',
    'OFFLINE',
    100,
    'stale-firmware'
  ),
  false,
  'an older robot-state sample is ignored'
);

select is(
  (
    select battery
    from public.robots
    where id = 'test-state-robot'
  ),
  69,
  'an older sample cannot overwrite current telemetry'
);

select is(
  (
    select telemetry_at
    from public.robots
    where id = 'test-state-robot'
  ),
  '2026-07-20 10:00:20+00'::timestamptz,
  'the robot retains the newest observed timestamp'
);

select throws_ok(
  $$
    select public.apply_robot_state(
      'missing-state-robot',
      '2026-07-20 10:00:30+00',
      'ONLINE',
      'IDLE',
      100,
      100,
      0,
      'loc-home',
      null,
      'OK',
      'OK',
      'OK',
      30,
      'state-test-v1'
    )
  $$,
  'P0001',
  'Unknown robot',
  'robot state rejects an unknown robot identity'
);

select * from finish();
rollback;
