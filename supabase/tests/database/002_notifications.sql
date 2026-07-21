begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(22);

insert into auth.users (
  id,
  email,
  raw_user_meta_data,
  created_at,
  updated_at
)
values
  (
    '10000000-0000-0000-0000-000000000001',
    'requester.notifications@example.test',
    '{"full_name":"Notification Requester"}'::jsonb,
    now(),
    now()
  ),
  (
    '10000000-0000-0000-0000-000000000002',
    'admin.notifications@example.test',
    '{"full_name":"Notification Admin"}'::jsonb,
    now(),
    now()
  ),
  (
    '10000000-0000-0000-0000-000000000003',
    'operator.notifications@example.test',
    '{"full_name":"Notification Operator"}'::jsonb,
    now(),
    now()
  ),
  (
    '10000000-0000-0000-0000-000000000004',
    'other.notifications@example.test',
    '{"full_name":"Other User"}'::jsonb,
    now(),
    now()
  );

update public.profiles
set role = case id
  when '10000000-0000-0000-0000-000000000002'::uuid
    then 'ADMIN'::public.app_role
  when '10000000-0000-0000-0000-000000000003'::uuid
    then 'OPERATOR'::public.app_role
  else 'USER'::public.app_role
end
where id in (
  '10000000-0000-0000-0000-000000000001',
  '10000000-0000-0000-0000-000000000002',
  '10000000-0000-0000-0000-000000000003',
  '10000000-0000-0000-0000-000000000004'
);

select set_config(
  'request.jwt.claim.sub',
  '10000000-0000-0000-0000-000000000001',
  true
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
  status
)
values (
  '20000000-0000-0000-0000-000000000001',
  'TEST-NOTIFY-0001',
  '10000000-0000-0000-0000-000000000004',
  'Spoofed Requester',
  'spoofed@example.test',
  'Test Recipient',
  'loc-fcs',
  'loc-library',
  'Integration test parcel',
  'DOCUMENTS',
  1.25,
  'REQUESTED'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where delivery_id = '20000000-0000-0000-0000-000000000001'
  ),
  3,
  'delivery creation notifies the requester and both staff members'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where delivery_id = '20000000-0000-0000-0000-000000000001'
      and recipient_id = '10000000-0000-0000-0000-000000000001'
      and audience = 'PERSONAL'
      and title = 'Delivery request created'
  ),
  1,
  'delivery creation produces one personal requester notification'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where delivery_id = '20000000-0000-0000-0000-000000000001'
      and audience = 'STAFF'
      and title = 'New delivery request'
  ),
  2,
  'delivery creation produces one notification for each staff member'
);

update public.deliveries
set status = 'APPROVED',
    progress = 12
where id = '20000000-0000-0000-0000-000000000001';

select is(
  (
    select count(*)::integer
    from public.notifications
    where delivery_id = '20000000-0000-0000-0000-000000000001'
      and event_key =
        'delivery:20000000-0000-0000-0000-000000000001:status:approved'
  ),
  3,
  'a delivery status change notifies the requester and staff'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where delivery_id = '20000000-0000-0000-0000-000000000001'
      and title = 'Delivery approved'
      and type = 'success'
  ),
  3,
  'approved delivery notifications use the expected title and type'
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
  'test-notification-robot',
  'Notification Test Robot',
  'Test Model',
  'ONLINE',
  'IDLE',
  24,
  'loc-home',
  90,
  'OK',
  'OK',
  'OK',
  30,
  now()
);

insert into public.robot_events (
  robot_id,
  event_type,
  severity,
  payload,
  occurred_at
)
values (
  'test-notification-robot',
  'LOW_BATTERY',
  'WARNING',
  '{"battery":24,"privateDiagnostic":"must-not-be-copied"}'::jsonb,
  '2026-07-20 10:00:00+00'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where robot_event_id is not null
      and title = 'Robot battery warning'
  ),
  2,
  'a warning robot event notifies all staff'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where robot_event_id is not null
      and audience = 'STAFF'
  ),
  2,
  'robot-event notifications are staff-only'
);

select ok(
  not exists (
    select 1
    from public.notifications
    where robot_event_id is not null
      and data ? 'privateDiagnostic'
  ),
  'notification data does not copy private robot-event payload fields'
);

insert into public.robot_events (
  robot_id,
  event_type,
  severity,
  payload,
  occurred_at
)
values (
  'test-notification-robot',
  'OBSTACLE_DETECTED',
  'INFO',
  '{}'::jsonb,
  '2026-07-20 10:00:01+00'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where robot_event_id = currval(
      pg_get_serial_sequence('public.robot_events', 'id')
    )
  ),
  0,
  'informational robot events do not create staff alerts'
);

select is(
  (select count(*)::integer from public.notifications),
  8,
  'only the expected database-backed notifications were created'
);

select set_config(
  'request.jwt.claim.sub',
  '10000000-0000-0000-0000-000000000001',
  true
);
set local role authenticated;

select is(
  (select count(*)::integer from public.notifications),
  2,
  'a requester sees only their personal notifications'
);

select is(
  public.mark_notifications_read(),
  2,
  'mark-read updates every visible unread requester notification'
);

select is(
  public.mark_notifications_read(),
  0,
  'mark-read is idempotent when no visible notification is unread'
);

select is(
  (
    select count(*)::integer
    from public.notifications
    where read_at is null
  ),
  0,
  'the requester has no unread visible notifications after mark-read'
);

reset role;
select set_config(
  'request.jwt.claim.sub',
  '10000000-0000-0000-0000-000000000004',
  true
);
set local role authenticated;

select is(
  (select count(*)::integer from public.notifications),
  0,
  'an unrelated user cannot read another recipient notification'
);

select is(
  public.mark_notifications_read(),
  0,
  'an unrelated user cannot mark another recipient notification read'
);

reset role;
select set_config(
  'request.jwt.claim.sub',
  '10000000-0000-0000-0000-000000000002',
  true
);
set local role authenticated;

select is(
  (select count(*)::integer from public.notifications),
  3,
  'an administrator sees only staff notifications addressed to them'
);

reset role;

update public.profiles
set role = 'USER'
where id = '10000000-0000-0000-0000-000000000002';

set local role authenticated;

select is(
  (select count(*)::integer from public.notifications),
  0,
  'a demoted staff member immediately loses access to staff notifications'
);

select is(
  public.mark_notifications_read(),
  0,
  'a demoted staff member cannot mark hidden staff notifications read'
);

reset role;

select is(
  (
    select count(*)::integer
    from public.notifications
    where recipient_id = '10000000-0000-0000-0000-000000000002'
      and read_at is null
  ),
  3,
  'demotion leaves the hidden staff notifications unread in storage'
);

update public.profiles
set role = 'ADMIN'
where id = '10000000-0000-0000-0000-000000000002';

set local role authenticated;

select is(
  public.mark_notifications_read(),
  3,
  'an administrator can mark their staff notifications read'
);

reset role;

select is(
  (
    select count(*)::integer
    from public.notifications
    where recipient_id = '10000000-0000-0000-0000-000000000003'
      and read_at is null
  ),
  3,
  'mark-read leaves another staff member notifications unread'
);

select * from finish();
rollback;
