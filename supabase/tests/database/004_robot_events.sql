begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(27);

insert into auth.users (
  id,
  email,
  raw_user_meta_data,
  created_at,
  updated_at
)
values (
  '30000000-0000-0000-0000-000000000001',
  'events.requester@example.test',
  '{"full_name":"Event Test Requester"}'::jsonb,
  now(),
  now()
);

select set_config(
  'request.jwt.claim.sub',
  '30000000-0000-0000-0000-000000000001',
  true
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
  lidar,
  camera,
  esp32,
  motor_temp_c,
  last_seen
)
values (
  'test-event-robot',
  'Event Test Robot',
  'Test Model',
  'ONLINE',
  'IDLE',
  90,
  'loc-home',
  90,
  'OK',
  'OK',
  'OK',
  30,
  now()
);

insert into public.deliveries (
  id,
  tracking_code,
  requester_id,
  requester_name,
  requester_email,
  recipient_name,
  source_id,
  destination_id,
  item_name,
  category,
  weight_kg,
  status,
  robot_id
)
values
  (
    '31000000-0000-0000-0000-000000000001',
    'TEST-EVENT-DISPATCHED',
    '30000000-0000-0000-0000-000000000001',
    'Ignored',
    'ignored@example.test',
    'Event Recipient',
    'loc-fcs',
    'loc-library',
    'Dispatched transition fixture',
    'DOCUMENTS',
    1,
    'DISPATCHED',
    'test-event-robot'
  ),
  (
    '31000000-0000-0000-0000-000000000002',
    'TEST-EVENT-ASSIGNED',
    '30000000-0000-0000-0000-000000000001',
    'Ignored',
    'ignored@example.test',
    'Event Recipient',
    'loc-fcs',
    'loc-library',
    'Assigned transition fixture',
    'DOCUMENTS',
    1,
    'ASSIGNED',
    'test-event-robot'
  );

insert into public.robot_commands (
  id,
  robot_id,
  delivery_id,
  command_type,
  payload,
  status,
  expires_at
)
values (
  '32000000-0000-0000-0000-000000000001',
  'test-event-robot',
  '31000000-0000-0000-0000-000000000001',
  'START_MISSION',
  '{}'::jsonb,
  'ACKNOWLEDGED',
  '2099-01-01 00:00:00+00'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '33000000-0000-0000-0000-000000000001',
      'test-event-robot',
      '31000000-0000-0000-0000-000000000002',
      null,
      'MISSION_STARTED',
      'INFO',
      '{}'::jsonb,
      '2026-07-20 10:00:00+00'
    )
  $$,
  'P0001',
  'MISSION_STARTED is not valid for the current delivery state; a DISPATCHED delivery assigned to this robot is required',
  'MISSION_STARTED is rejected while a delivery is only ASSIGNED'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where message_id = '33000000-0000-0000-0000-000000000001'
  ),
  0,
  'an invalid ASSIGNED transition does not persist its event'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '33000000-0000-0000-0000-000000000002',
      'test-event-robot',
      '31000000-0000-0000-0000-000000000001',
      null,
      'ARRIVED_SOURCE',
      'INFO',
      '{}'::jsonb,
      '2026-07-20 10:00:01+00'
    )
  $$,
  'P0001',
  'Event is not valid for the current delivery state',
  'ARRIVED_SOURCE is rejected before MISSION_STARTED'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'DISPATCHED',
  'an invalid event leaves delivery state unchanged'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000003',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    '32000000-0000-0000-0000-000000000001',
    'MISSION_STARTED',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:02+00'
  ),
  true,
  'MISSION_STARTED advances a DISPATCHED delivery'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'TO_SOURCE',
  'MISSION_STARTED advances the delivery to TO_SOURCE'
);

select is(
  (
    select status::text
    from public.robots
    where id = 'test-event-robot'
  ),
  'BUSY',
  'MISSION_STARTED marks the robot busy'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000003',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    '32000000-0000-0000-0000-000000000001',
    'MISSION_STARTED',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:02+00'
  ),
  false,
  'replaying the same event message is idempotent'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where message_id = '33000000-0000-0000-0000-000000000003'
  ),
  1,
  'an idempotent replay stores only one robot event'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000004',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'ARRIVED_SOURCE',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:03+00'
  ),
  true,
  'ARRIVED_SOURCE is accepted from TO_SOURCE'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'AT_SOURCE',
  'ARRIVED_SOURCE advances the delivery to AT_SOURCE'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000005',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'PACKAGE_LOADED',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:04+00'
  ),
  true,
  'PACKAGE_LOADED is accepted from AT_SOURCE'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'PACKAGE_LOADED',
  'PACKAGE_LOADED advances the delivery state'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000006',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'DEPARTED_SOURCE',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:05+00'
  ),
  true,
  'DEPARTED_SOURCE is accepted after package loading'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'TO_DESTINATION',
  'DEPARTED_SOURCE advances the delivery to TO_DESTINATION'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000007',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'ARRIVED_DESTINATION',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:06+00'
  ),
  true,
  'ARRIVED_DESTINATION is accepted in transit'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'AT_DESTINATION',
  'ARRIVED_DESTINATION advances the delivery to AT_DESTINATION'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000008',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'PACKAGE_RELEASED',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:07+00'
  ),
  true,
  'PACKAGE_RELEASED is accepted at the destination'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'DELIVERED',
  'PACKAGE_RELEASED advances the delivery to DELIVERED'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000009',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    null,
    'RETURNING_HOME',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:08+00'
  ),
  true,
  'RETURNING_HOME is accepted after delivery'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'RETURNING',
  'RETURNING_HOME advances the delivery to RETURNING'
);

select is(
  public.apply_robot_event(
    '33000000-0000-0000-0000-000000000010',
    'test-event-robot',
    '31000000-0000-0000-0000-000000000001',
    '32000000-0000-0000-0000-000000000001',
    'MISSION_COMPLETED',
    'INFO',
    '{}'::jsonb,
    '2026-07-20 10:00:09+00'
  ),
  true,
  'MISSION_COMPLETED is accepted while returning'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000001'
  ),
  'COMPLETED',
  'MISSION_COMPLETED advances the delivery to COMPLETED'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '32000000-0000-0000-0000-000000000001'
  ),
  'COMPLETED',
  'MISSION_COMPLETED completes the related robot command'
);

select is(
  (
    select status::text
    from public.robots
    where id = 'test-event-robot'
  ),
  'ONLINE',
  'MISSION_COMPLETED returns the robot to ONLINE'
);

select is(
  (
    select mode::text
    from public.robots
    where id = 'test-event-robot'
  ),
  'IDLE',
  'MISSION_COMPLETED returns the robot to IDLE mode'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where delivery_id = '31000000-0000-0000-0000-000000000001'
  ),
  8,
  'the valid mission sequence stores exactly eight unique events'
);

select * from finish();
rollback;
