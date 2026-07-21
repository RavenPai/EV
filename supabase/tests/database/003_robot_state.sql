begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(13);

insert into auth.users (
  id,
  email,
  raw_user_meta_data,
  created_at,
  updated_at
) values (
  '22000000-0000-4000-8000-000000000001',
  'state.requester@example.test',
  '{"full_name":"State Test Requester"}'::jsonb,
  now(),
  now()
);

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
  telemetry_received_at,
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
  false,
  'a duplicate sample with the same observed timestamp is ignored'
);

select is(
  (
    select battery
    from public.robots
    where id = 'test-state-robot'
  ),
  70,
  'the equal-timestamp duplicate cannot replace telemetry values'
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
  70,
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

select set_config(
  'request.jwt.claim.sub',
  '22000000-0000-4000-8000-000000000001',
  true
);

insert into public.deliveries (
  id, tracking_code, requester_name, requester_email, recipient_name,
  source_id, destination_id, item_name, category, weight_kg, status, robot_id
) values (
  '23000000-0000-4000-8000-000000000001',
  'TEST-STATE-PAUSED',
  'State Test Requester',
  'state.requester@example.test',
  'State Test Recipient',
  'loc-fcs',
  'loc-library',
  'Paused state fixture',
  'DOCUMENTS',
  1,
  'REQUESTED',
  null
);

select set_config('request.jwt.claim.sub', '', true);

update public.deliveries
set status = 'TO_SOURCE',
    robot_id = 'test-state-robot'
where id = '23000000-0000-4000-8000-000000000001';

update public.robots
set mode = 'PAUSED',
    speed_mps = 0,
    current_delivery_id = '23000000-0000-4000-8000-000000000001'
where id = 'test-state-robot';

select is(
  public.apply_robot_state(
    'test-state-robot',
    '2026-07-20 10:00:30+00',
    'BUSY',
    'PAUSED',
    68,
    82,
    0,
    'loc-fcs',
    null,
    'OK',
    'OK',
    'OK',
    42,
    'state-before-resumed'
  ),
  true,
  'fresh state is still recorded while PAUSED is latched'
);

select ok(
  exists (
    select 1
    from public.robots
    where id = 'test-state-robot'
      and mode = 'PAUSED'
      and speed_mps = 0
      and current_delivery_id = '23000000-0000-4000-8000-000000000001'
  ),
  'state before RESUMED cannot clear PAUSED, motion, or mission ownership'
);

update public.robots
set status = 'FAULT',
    mode = 'ESTOP',
    speed_mps = 0
where id = 'test-state-robot';

select is(
  public.apply_robot_state(
    'test-state-robot',
    '2026-07-20 10:00:40+00',
    'FAULT',
    'ESTOP',
    67,
    81,
    0,
    'loc-fcs',
    null,
    'OK',
    'OK',
    'OK',
    42,
    'state-during-estop'
  ),
  true,
  'fresh same-mode ESTOP telemetry is still recorded'
);

select is(
  (
    select current_delivery_id
    from public.robots
    where id = 'test-state-robot'
  ),
  '23000000-0000-4000-8000-000000000001'::uuid,
  'same-mode ESTOP telemetry cannot clear mission ownership'
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
