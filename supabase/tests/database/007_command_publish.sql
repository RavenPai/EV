begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(16);

insert into auth.users (
  id,
  email,
  raw_user_meta_data,
  created_at,
  updated_at
) values (
  '70000000-0000-4000-8000-000000000001',
  'publish.operator@example.test',
  '{"full_name":"Publish Test Operator"}'::jsonb,
  now(),
  now()
);

update public.profiles
set role = 'OPERATOR'
where id = '70000000-0000-4000-8000-000000000001';

select set_config(
  'request.jwt.claim.sub',
  '70000000-0000-4000-8000-000000000001',
  true
);

insert into public.robots (
  id, name, model, status, mode, battery, location_id, signal, speed_mps,
  lidar, camera, esp32, motor_temp_c, last_seen, telemetry_at,
  telemetry_received_at, bridge_online, bridge_last_seen
) values (
  'test-publish-robot', 'Publish Test Robot', 'Test Model', 'ONLINE',
  'IDLE', 90, 'loc-home', 90, 0, 'OK', 'OK', 'OK', 30, now(), now(),
  statement_timestamp(), true, statement_timestamp()
);

insert into public.deliveries (
  id, tracking_code, requester_id, requester_name, requester_email,
  recipient_name, source_id, destination_id, item_name, category, weight_kg,
  status, robot_id
) values (
  '71000000-0000-4000-8000-000000000001',
  'TEST-PUBLISH-ATOMIC',
  '70000000-0000-4000-8000-000000000001',
  'Publish Test Operator',
  'publish.operator@example.test',
  'Publish Recipient',
  'loc-fcs',
  'loc-library',
  'Atomic publish fixture',
  'DOCUMENTS',
  1,
  'REQUESTED',
  null
);

select set_config('request.jwt.claim.sub', '', true);

update public.deliveries
set status = 'ASSIGNED',
    robot_id = 'test-publish-robot'
where id = '71000000-0000-4000-8000-000000000001';

insert into public.robot_commands (
  id, robot_id, delivery_id, command_type, payload, status, issued_by,
  issued_at, expires_at
) values (
  '72000000-0000-4000-8000-000000000001',
  'test-publish-robot',
  '71000000-0000-4000-8000-000000000001',
  'START_MISSION',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '70000000-0000-4000-8000-000000000001',
  now() - interval '5 minutes',
  now() - interval '1 minute'
);

select is(
  public.expire_stale_robot_commands(),
  0,
  'an unknown publish outcome never expires automatically'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '72000000-0000-4000-8000-000000000001'
  ),
  'PUBLISH_UNKNOWN',
  'the uncertain command keeps its duplicate-prevention barrier'
);

select is(
  public.finalize_robot_command_publish(
    '72000000-0000-4000-8000-000000000001',
    'test-publish-robot',
    '71000000-0000-4000-8000-000000000001',
    statement_timestamp()
  ),
  'PUBLISHED',
  'successful broker publication is finalized atomically'
);

select is(
  (
    select status::text
    from public.deliveries
    where id = '71000000-0000-4000-8000-000000000001'
  ),
  'DISPATCHED',
  'atomic publication finalization dispatches the reserved delivery'
);

select ok(
  (
    select published_at is not null
      and dispatched_at is not null
    from public.robot_commands
    join public.deliveries
      on deliveries.id = robot_commands.delivery_id
    where robot_commands.id = '72000000-0000-4000-8000-000000000001'
  ),
  'the command and delivery both receive publication timestamps'
);

select is(
  public.finalize_robot_command_publish(
    '72000000-0000-4000-8000-000000000001',
    'test-publish-robot',
    '71000000-0000-4000-8000-000000000001',
    statement_timestamp()
  ),
  'PUBLISHED',
  'publication finalization is idempotent'
);

update public.robot_commands
set status = 'EXPIRED'
where id = '72000000-0000-4000-8000-000000000001';

update public.deliveries
set status = 'CANCELLED'
where id = '71000000-0000-4000-8000-000000000001';

select throws_ok(
  $$
    select public.apply_robot_ack(
      '72000000-0000-4000-8000-000000000001',
      'test-publish-robot',
      'COMPLETED',
      'late completion evidence for cancelled delivery',
      now() - interval '1 minute 1 second'
    )
  $$,
  'P0001',
  'On-time acknowledgement no longer matches the mission reservation',
  'late completion evidence cannot revive a cancelled mission reservation'
);

insert into public.robot_commands (
  id, robot_id, command_type, payload, status, issued_by, expires_at
) values (
  '72000000-0000-4000-8000-000000000002',
  'test-publish-robot',
  'PAUSE',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '70000000-0000-4000-8000-000000000001',
  now() + interval '5 minutes'
);

select set_config(
  'request.jwt.claim.sub',
  '70000000-0000-4000-8000-000000000001',
  true
);

insert into public.deliveries (
  id, tracking_code, requester_id, requester_name, requester_email,
  recipient_name, source_id, destination_id, item_name, category, weight_kg,
  status, robot_id
) values (
  '71000000-0000-4000-8000-000000000002',
  'TEST-PUBLISH-CONTROL',
  '70000000-0000-4000-8000-000000000001',
  'Publish Test Operator',
  'publish.operator@example.test',
  'Control Recipient',
  'loc-fcs',
  'loc-library',
  'Control serialization fixture',
  'DOCUMENTS',
  1,
  'REQUESTED',
  null
);

select set_config('request.jwt.claim.sub', '', true);

update public.deliveries
set status = 'ASSIGNED',
    robot_id = 'test-publish-robot'
where id = '71000000-0000-4000-8000-000000000002';

select throws_ok(
  $$
    insert into public.robot_commands (
      id, robot_id, delivery_id, command_type, payload, status, issued_by,
      expires_at
    ) values (
      '72000000-0000-4000-8000-000000000007',
      'test-publish-robot',
      '71000000-0000-4000-8000-000000000002',
      'START_MISSION',
      '{}'::jsonb,
      'PUBLISH_UNKNOWN',
      '70000000-0000-4000-8000-000000000001',
      now() + interval '5 minutes'
    )
  $$,
  'P0001',
  'START_MISSION is blocked by an unresolved robot control command',
  'an unresolved control command blocks a new mission reservation'
);

update public.deliveries
set status = 'TO_SOURCE'
where id = '71000000-0000-4000-8000-000000000002';

update public.robots
set status = 'BUSY',
    mode = 'AUTO',
    current_delivery_id = '71000000-0000-4000-8000-000000000002'
where id = 'test-publish-robot';

select throws_ok(
  $$
    insert into public.robot_commands (
      id, robot_id, delivery_id, command_type, payload, status, issued_by,
      expires_at
    ) values (
      '72000000-0000-4000-8000-000000000003',
      'test-publish-robot',
      '71000000-0000-4000-8000-000000000002',
      'RETURN_HOME',
      '{}'::jsonb,
      'PUBLISH_UNKNOWN',
      '70000000-0000-4000-8000-000000000001',
      now() + interval '5 minutes'
    )
  $$,
  '23505',
  'duplicate key value violates unique constraint "robot_commands_one_active_control_per_robot_idx"',
  'the database serializes concurrent non-ESTOP control reservations'
);

select is(
  public.resolve_unknown_robot_command(
    '72000000-0000-4000-8000-000000000002',
    '70000000-0000-4000-8000-000000000001',
    'CONFIRMED_NOT_PUBLISHED'
  ),
  true,
  'staff can release a barrier only after broker verification'
);

select ok(
  exists (
    select 1
    from public.robot_commands command
    join public.robot_events event on event.command_id = command.id
    where command.id = '72000000-0000-4000-8000-000000000002'
      and command.status = 'FAILED'
      and event.event_type = 'COMMAND_PUBLISH_RECONCILED'
      and event.severity = 'WARNING'
  ),
  'manual reconciliation leaves both terminal command and audit event records'
);

update public.deliveries
set status = 'ASSIGNED'
where id = '71000000-0000-4000-8000-000000000002';

update public.robots
set status = 'ONLINE',
    mode = 'IDLE',
    current_delivery_id = null,
    speed_mps = 0,
    telemetry_at = statement_timestamp(),
    telemetry_received_at = statement_timestamp(),
    bridge_online = true,
    bridge_last_seen = statement_timestamp(),
    lidar = 'OK',
    camera = 'OK',
    esp32 = 'OK'
where id = 'test-publish-robot';

insert into public.robot_commands (
  id, robot_id, delivery_id, command_type, payload, status, issued_by,
  expires_at
) values (
  '72000000-0000-4000-8000-000000000004',
  'test-publish-robot',
  '71000000-0000-4000-8000-000000000002',
  'START_MISSION',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '70000000-0000-4000-8000-000000000001',
  now() + interval '5 minutes'
);

update public.robot_commands
set status = 'ACKNOWLEDGED'
where id = '72000000-0000-4000-8000-000000000004';

update public.deliveries
set status = 'TO_SOURCE'
where id = '71000000-0000-4000-8000-000000000002';

update public.robots
set status = 'BUSY',
    mode = 'PAUSED',
    current_delivery_id = '71000000-0000-4000-8000-000000000002',
    speed_mps = 0,
    control_event_at = statement_timestamp() - interval '1 second',
    control_event_received_at = statement_timestamp() - interval '1 second'
where id = 'test-publish-robot';

select throws_ok(
  $$
    select public.apply_robot_event(
      '73000000-0000-4000-8000-000000000002',
      'test-publish-robot',
      '71000000-0000-4000-8000-000000000002',
      '72000000-0000-4000-8000-000000000004',
      'ARRIVED_SOURCE',
      'INFO',
      '{}'::jsonb,
      statement_timestamp() + interval '1 second'
    )
  $$,
  'P0001',
  'Robot pause blocks mission progress until RESUMED',
  'mission events cannot advance a delivery while the robot is paused'
);

select throws_ok(
  $$
    insert into public.robot_commands (
      id, robot_id, delivery_id, command_type, payload, status, issued_by,
      expires_at
    ) values (
      '72000000-0000-4000-8000-000000000009',
      'test-publish-robot',
      '71000000-0000-4000-8000-000000000002',
      'RETURN_HOME',
      '{}'::jsonb,
      'PUBLISH_UNKNOWN',
      '70000000-0000-4000-8000-000000000001',
      now() + interval '5 minutes'
    )
  $$,
  'P0001',
  'RESUME the robot before RETURN_HOME',
  'RETURN_HOME cannot authorize motion while the robot is paused'
);

update public.robots
set mode = 'AUTO'
where id = 'test-publish-robot';

update public.deliveries
set status = 'DELIVERED'
where id = '71000000-0000-4000-8000-000000000002';

update public.robots
set status = 'BUSY',
    mode = 'AUTO',
    current_delivery_id = '71000000-0000-4000-8000-000000000002'
where id = 'test-publish-robot';

insert into public.robot_commands (
  id, robot_id, delivery_id, command_type, payload, status, issued_by,
  expires_at
) values (
  '72000000-0000-4000-8000-000000000005',
  'test-publish-robot',
  '71000000-0000-4000-8000-000000000002',
  'RETURN_HOME',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '70000000-0000-4000-8000-000000000001',
  now() + interval '5 minutes'
);

update public.robot_commands
set status = 'COMPLETED',
    result = '{"consumed":true,"marker":"historical"}'::jsonb
where id = '72000000-0000-4000-8000-000000000005';

insert into public.robot_commands (
  id, robot_id, delivery_id, command_type, payload, status, issued_by,
  expires_at
) values (
  '72000000-0000-4000-8000-000000000006',
  'test-publish-robot',
  '71000000-0000-4000-8000-000000000002',
  'RETURN_HOME',
  '{}'::jsonb,
  'PUBLISH_UNKNOWN',
  '70000000-0000-4000-8000-000000000001',
  now() + interval '5 minutes'
);

update public.robot_commands
set status = 'COMPLETED'
where id = '72000000-0000-4000-8000-000000000006';

select is(
  public.apply_robot_event(
    '73000000-0000-4000-8000-000000000001',
    'test-publish-robot',
    '71000000-0000-4000-8000-000000000002',
    '72000000-0000-4000-8000-000000000004',
    'RETURNING_HOME',
    'INFO',
    '{}'::jsonb,
    statement_timestamp() + interval '1 second'
  ),
  true,
  'RETURNING_HOME applies while a direct return command awaits its event'
);

select ok(
  (
    select status = 'COMPLETED'
      and result @> '{"consumed":true}'::jsonb
      and result ->> 'eventType' = 'RETURNING_HOME'
    from public.robot_commands
    where id = '72000000-0000-4000-8000-000000000006'
  ),
  'RETURNING_HOME consumes only the newest unconsumed return command'
);

select ok(
  (
    select result = '{"consumed":true,"marker":"historical"}'::jsonb
    from public.robot_commands
    where id = '72000000-0000-4000-8000-000000000005'
  ),
  'RETURNING_HOME does not rewrite historical consumed command evidence'
);

select * from finish();
rollback;
