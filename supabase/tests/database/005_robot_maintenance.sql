begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(15);

update public.robots
set
  status = 'OFFLINE',
  last_seen = now();

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
  last_seen
)
values
  (
    'test-maintenance-robot',
    'Command Maintenance Robot',
    'Test Model',
    'OFFLINE',
    'IDLE',
    90,
    'loc-home',
    0,
    0,
    'OFFLINE',
    'OFFLINE',
    'OFFLINE',
    30,
    now()
  ),
  (
    'test-stale-online',
    'Stale Online Robot',
    'Test Model',
    'ONLINE',
    'AUTO',
    70,
    'loc-fcs',
    75,
    1.2,
    'OK',
    'OK',
    'OK',
    38,
    now() - interval '61 seconds'
  ),
  (
    'test-stale-null',
    'Never Seen Robot',
    'Test Model',
    'FAULT',
    'FAULT',
    50,
    'loc-home',
    20,
    0,
    'WARNING',
    'WARNING',
    'WARNING',
    45,
    null
  ),
  (
    'test-fresh-online',
    'Fresh Online Robot',
    'Test Model',
    'ONLINE',
    'IDLE',
    80,
    'loc-home',
    88,
    0,
    'OK',
    'OK',
    'OK',
    32,
    now()
  ),
  (
    'test-already-offline',
    'Already Offline Robot',
    'Test Model',
    'OFFLINE',
    'IDLE',
    40,
    'loc-home',
    0,
    0,
    'OFFLINE',
    'OFFLINE',
    'OFFLINE',
    30,
    now() - interval '10 minutes'
  );

insert into public.robot_commands (
  id,
  robot_id,
  command_type,
  payload,
  status,
  expires_at
)
values
  (
    '40000000-0000-0000-0000-000000000001',
    'test-maintenance-robot',
    'TEST_PENDING_EXPIRED',
    '{}'::jsonb,
    'PENDING',
    now() - interval '2 minutes'
  ),
  (
    '40000000-0000-0000-0000-000000000002',
    'test-maintenance-robot',
    'TEST_PUBLISHED_EXPIRED',
    '{}'::jsonb,
    'PUBLISHED',
    now() - interval '1 minute'
  ),
  (
    '40000000-0000-0000-0000-000000000003',
    'test-maintenance-robot',
    'TEST_ACKNOWLEDGED',
    '{}'::jsonb,
    'ACKNOWLEDGED',
    now() - interval '1 minute'
  ),
  (
    '40000000-0000-0000-0000-000000000004',
    'test-maintenance-robot',
    'TEST_PENDING_FUTURE',
    '{}'::jsonb,
    'PENDING',
    now() + interval '5 minutes'
  );

select is(
  public.expire_stale_robot_commands(),
  2,
  'command expiration processes expired PENDING and PUBLISHED commands'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '40000000-0000-0000-0000-000000000001'
  ),
  'EXPIRED',
  'an expired PENDING command becomes EXPIRED'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '40000000-0000-0000-0000-000000000002'
  ),
  'EXPIRED',
  'an expired PUBLISHED command becomes EXPIRED'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '40000000-0000-0000-0000-000000000003'
  ),
  'ACKNOWLEDGED',
  'an acknowledged command is not expired'
);

select is(
  (
    select status
    from public.robot_commands
    where id = '40000000-0000-0000-0000-000000000004'
  ),
  'PENDING',
  'a command with a future deadline remains pending'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where command_id in (
      '40000000-0000-0000-0000-000000000001',
      '40000000-0000-0000-0000-000000000002'
    )
      and event_type = 'COMMAND_EXPIRED'
      and severity = 'WARNING'
  ),
  2,
  'each expired command produces a warning event'
);

select is(
  (
    select result ->> 'previousStatus'
    from public.robot_commands
    where id = '40000000-0000-0000-0000-000000000002'
  ),
  'PUBLISHED',
  'the expiration result records the prior command status'
);

select is(
  public.expire_stale_robot_commands(),
  0,
  'command expiration is idempotent on a second run'
);

select is(
  public.mark_stale_robots_offline(),
  2,
  'the heartbeat job marks only stale non-offline robots'
);

select is(
  (
    select status::text
    from public.robots
    where id = 'test-stale-online'
  ),
  'OFFLINE',
  'a stale online robot becomes offline'
);

select ok(
  exists (
    select 1
    from public.robots
    where id = 'test-stale-online'
      and signal = 0
      and lidar = 'OFFLINE'
      and camera = 'OFFLINE'
      and esp32 = 'OFFLINE'
      and speed_mps = 0
  ),
  'offline handling clears communication and motion telemetry'
);

select is(
  (
    select status::text
    from public.robots
    where id = 'test-fresh-online'
  ),
  'ONLINE',
  'a fresh robot remains online'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where robot_id in ('test-stale-online', 'test-stale-null')
      and event_type = 'ROBOT_OFFLINE'
      and severity = 'ERROR'
  ),
  2,
  'each newly offline robot produces one error event'
);

select is(
  (
    select count(*)::integer
    from public.robot_events
    where robot_id = 'test-already-offline'
      and event_type = 'ROBOT_OFFLINE'
  ),
  0,
  'an already offline robot does not produce another offline event'
);

select is(
  public.mark_stale_robots_offline(),
  0,
  'the heartbeat job is idempotent on a second run'
);

select * from finish();
rollback;
