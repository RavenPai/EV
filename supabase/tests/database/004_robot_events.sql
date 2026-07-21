begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(40);

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
  last_seen,
  telemetry_at,
  telemetry_received_at,
  bridge_online,
  bridge_last_seen
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
  now(),
  statement_timestamp(),
  statement_timestamp(),
  true,
  statement_timestamp()
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
    'REQUESTED',
    null
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
    'REQUESTED',
    null
  ),
  (
    '31000000-0000-0000-0000-000000000003',
    'TEST-EVENT-APPROVAL',
    '30000000-0000-0000-0000-000000000001',
    'Ignored',
    'ignored@example.test',
    'Approval Recipient',
    'loc-fcs',
    'loc-library',
    'Approval audit fixture',
    'DOCUMENTS',
    1,
    'REQUESTED',
    null
  );

select set_config('request.jwt.claim.sub', '', true);

update public.deliveries
set status = 'ASSIGNED',
    robot_id = 'test-event-robot'
where id in (
  '31000000-0000-0000-0000-000000000001',
  '31000000-0000-0000-0000-000000000002'
);

select set_config(
  'request.jwt.claim.sub',
  '30000000-0000-0000-0000-000000000001',
  true
);

select throws_ok(
  $$
    insert into public.deliveries (
      tracking_code, requester_id, requester_name, requester_email,
      recipient_name, source_id, destination_id, item_name, category,
      weight_kg, status, progress, dispatched_at
    ) values (
      'TEST-EVENT-FORGED',
      '30000000-0000-0000-0000-000000000001',
      'Ignored',
      'ignored@example.test',
      'Forged Recipient',
      'loc-fcs',
      'loc-library',
      'Forged lifecycle fixture',
      'DOCUMENTS',
      1,
      'REQUESTED',
      99,
      statement_timestamp()
    )
  $$,
  'P0001',
  'Authenticated delivery requests must use initial lifecycle defaults',
  'authenticated creation cannot forge lifecycle fields'
);

select throws_ok(
  $$
    update public.deliveries
    set progress = 99
    where id = '31000000-0000-0000-0000-000000000002'
  $$,
  'P0001',
  'Delivery progress advances only through robot events',
  'an authenticated caller cannot fake mission progress'
);

select throws_ok(
  $$
    update public.deliveries
    set dispatched_at = statement_timestamp()
    where id = '31000000-0000-0000-0000-000000000002'
  $$,
  'P0001',
  'Delivery lifecycle fields advance only through robot events',
  'an authenticated caller cannot fake dispatch evidence'
);

select lives_ok(
  $$
    update public.deliveries
    set status = 'APPROVED', progress = 12
    where id = '31000000-0000-0000-0000-000000000003'
  $$,
  'the authenticated approval transition remains available'
);

select is(
  (
    select approved_by
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000003'
  ),
  '30000000-0000-0000-0000-000000000001'::uuid,
  'approval records the authenticated staff actor'
);

select ok(
  (
    select approved_at is not null
    from public.deliveries
    where id = '31000000-0000-0000-0000-000000000003'
  ),
  'approval records a trusted database timestamp'
);

select throws_ok(
  $$
    update public.deliveries
    set status = 'ASSIGNED', progress = 20
    where id = '31000000-0000-0000-0000-000000000003'
  $$,
  'P0001',
  'ASSIGNED delivery requires a robot',
  'assignment cannot advance without a robot identity'
);

select throws_ok(
  $$
    update public.deliveries
    set status = 'TO_SOURCE'
    where id = '31000000-0000-0000-0000-000000000002'
  $$,
  'P0001',
  'Delivery lifecycle advances only through robot events',
  'an authenticated caller cannot advance a mission checkpoint directly'
);

select set_config('request.jwt.claim.sub', '', true);

insert into public.robot_commands (
  id,
  robot_id,
  delivery_id,
  command_type,
  payload,
  status,
  issued_at,
  published_at,
  expires_at
)
values (
  '32000000-0000-0000-0000-000000000002',
  'test-event-robot',
  '31000000-0000-0000-0000-000000000002',
  'START_MISSION',
  '{}'::jsonb,
  'FAILED',
  '2026-07-20 09:58:00+00',
  '2026-07-20 09:58:01+00',
  '2026-07-20 10:03:00+00'
);

select throws_ok(
  $$
    select public.apply_robot_event(
      '33000000-0000-0000-0000-000000000001',
      'test-event-robot',
      '31000000-0000-0000-0000-000000000002',
      '32000000-0000-0000-0000-000000000002',
      'MISSION_STARTED',
      'INFO',
      '{}'::jsonb,
      '2026-07-20 10:00:00+00'
    )
  $$,
  'P0001',
  'Mission event requires its valid START_MISSION command',
  'MISSION_STARTED is rejected when its command is already failed'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where message_id = '33000000-0000-0000-0000-000000000001'
  ),
  0,
  'an invalid command transition does not persist its event'
);

update public.robot_commands
set status = 'FAILED'
where id = '32000000-0000-0000-0000-000000000002';

insert into public.robot_commands (
  id,
  robot_id,
  delivery_id,
  command_type,
  payload,
  status,
  issued_at,
  published_at,
  expires_at
) values (
  '32000000-0000-0000-0000-000000000001',
  'test-event-robot',
  '31000000-0000-0000-0000-000000000001',
  'START_MISSION',
  '{}'::jsonb,
  'ACKNOWLEDGED',
  '2026-07-20 09:59:00+00',
  '2026-07-20 09:59:01+00',
  '2026-07-20 10:04:00+00'
);

select throws_ok(
  $$
    update public.deliveries
    set robot_id = 'robot-02'
    where id = '31000000-0000-0000-0000-000000000001'
  $$,
  'P0001',
  'Delivery assignment is reserved by an active mission command',
  'an active mission command prevents concurrent robot reassignment'
);

select throws_ok(
  $$
    update public.deliveries
    set status = 'CANCELLED'
    where id = '31000000-0000-0000-0000-000000000001'
  $$,
  'P0001',
  'Resolve the active mission command before changing delivery state',
  'an active mission command prevents concurrent cancellation'
);

update public.robot_commands
set status = 'EXPIRED',
    result = '{"previousStatus":"PUBLISHED"}'::jsonb
where id = '32000000-0000-0000-0000-000000000001';

update public.deliveries
set status = 'DISPATCHED',
    dispatched_at = '2026-07-20 09:59:01+00'
where id = '31000000-0000-0000-0000-000000000001';

-- Keep the command valid while proving event-state ordering. The following
-- MISSION_STARTED fixture restores EXPIRED to test the on-time expiry race.
update public.robot_commands
set status = 'ACKNOWLEDGED'
where id = '32000000-0000-0000-0000-000000000001';

select throws_ok(
  $$
    select public.apply_robot_event(
      '33000000-0000-0000-0000-000000000002',
      'test-event-robot',
      '31000000-0000-0000-0000-000000000001',
      '32000000-0000-0000-0000-000000000001',
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

update public.robot_commands
set status = 'EXPIRED'
where id = '32000000-0000-0000-0000-000000000001';

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
    select status
    from public.robot_commands
    where id = '32000000-0000-0000-0000-000000000001'
  ),
  'ACKNOWLEDGED',
  'on-time mission evidence reconciles an expiration race'
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
    '32000000-0000-0000-0000-000000000001',
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
    '32000000-0000-0000-0000-000000000001',
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
    '32000000-0000-0000-0000-000000000001',
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
    '32000000-0000-0000-0000-000000000001',
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
    '32000000-0000-0000-0000-000000000001',
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
    '32000000-0000-0000-0000-000000000001',
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

update public.robot_commands
set status = 'COMPLETED',
    acknowledged_at = '2026-07-20 10:00:08.500+00'
where id = '32000000-0000-0000-0000-000000000001';

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

insert into public.robots (
  id, name, model, status, mode, battery, location_id, signal, speed_mps,
  lidar, camera, esp32, motor_temp_c, last_seen, telemetry_at,
  telemetry_received_at, bridge_online, bridge_last_seen
) values (
  'test-expired-failure-robot',
  'Expired Failure Robot',
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
  now(),
  now(),
  now(),
  true,
  now()
);

select set_config(
  'request.jwt.claim.sub',
  '30000000-0000-0000-0000-000000000001',
  true
);

insert into public.deliveries (
  id, tracking_code, requester_name, requester_email, recipient_name,
  source_id, destination_id, item_name, category, weight_kg, status, robot_id
) values (
  '34000000-0000-4000-8000-000000000001',
  'TEST-EXPIRED-FAILURE',
  'Expired Failure Requester',
  'expired.failure@example.test',
  'Expired Failure Recipient',
  'loc-fcs',
  'loc-library',
  'Expired failure fixture',
  'DOCUMENTS',
  1,
  'REQUESTED',
  null
);

select set_config('request.jwt.claim.sub', '', true);

update public.deliveries
set status = 'ASSIGNED',
    robot_id = 'test-expired-failure-robot'
where id = '34000000-0000-4000-8000-000000000001';

insert into public.robot_commands (
  id, robot_id, delivery_id, command_type, payload, status, issued_at,
  expires_at
) values (
  '35000000-0000-4000-8000-000000000001',
  'test-expired-failure-robot',
  '34000000-0000-4000-8000-000000000001',
  'START_MISSION',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  now() - interval '90 seconds',
  now() - interval '30 seconds'
);

update public.robot_commands
set status = 'EXPIRED'
where id = '35000000-0000-4000-8000-000000000001';

select is(
  public.apply_robot_event(
    '36000000-0000-4000-8000-000000000001',
    'test-expired-failure-robot',
    '34000000-0000-4000-8000-000000000001',
    '35000000-0000-4000-8000-000000000001',
    'MISSION_FAILED',
    'ERROR',
    '{"reason":"local failure before command expiry"}'::jsonb,
    now() - interval '60 seconds'
  ),
  true,
  'an on-time MISSION_FAILED event reconciles after command expiration'
);

select ok(
  exists (
    select 1
    from public.robot_commands as command
    join public.deliveries as delivery on delivery.id = command.delivery_id
    join public.robots as robot on robot.id = command.robot_id
    where command.id = '35000000-0000-4000-8000-000000000001'
      and command.status = 'FAILED'
      and delivery.status = 'FAILED'
      and robot.status = 'FAULT'
      and robot.mode = 'FAULT'
  ),
  'delayed failure evidence fails the command and delivery and latches the robot'
);

select * from finish();
rollback;
