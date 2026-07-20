create table public.notifications (
  id uuid primary key default gen_random_uuid(),
  recipient_id uuid not null
    references public.profiles(id) on delete cascade,
  delivery_id uuid
    references public.deliveries(id) on delete set null,
  robot_event_id bigint
    references public.robot_events(id) on delete set null,
  event_key text not null,
  audience text not null default 'PERSONAL'
    check (audience in ('PERSONAL', 'STAFF')),
  title text not null,
  message text not null,
  type text not null default 'info'
    check (type in ('info', 'success', 'warning')),
  data jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  unique (recipient_id, event_key)
);

create index notifications_recipient_created_idx
  on public.notifications(recipient_id, created_at desc);

create index notifications_recipient_unread_idx
  on public.notifications(recipient_id, created_at desc)
  where read_at is null;

alter table public.notifications enable row level security;

create policy "users read their visible notifications"
on public.notifications
for select
to authenticated
using (
  recipient_id = auth.uid()
  and (
    audience = 'PERSONAL'
    or public.current_user_role() in ('ADMIN', 'OPERATOR')
  )
);

revoke all on table public.notifications from anon, authenticated;
grant select on table public.notifications to authenticated;

-- The original own-profile UPDATE grant was table-wide, which also allowed a
-- user to alter their role. Keep profile editing, but never role self-service.
revoke update on table public.profiles from authenticated;
grant update (full_name, email) on table public.profiles to authenticated;

create or replace function public.mark_notifications_read()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_count integer;
begin
  if v_user_id is null then
    raise exception 'An authenticated user is required'
      using errcode = '42501';
  end if;

  update public.notifications
  set read_at = now()
  where recipient_id = v_user_id
    and read_at is null
    and (
      audience = 'PERSONAL'
      or public.current_user_role() in ('ADMIN', 'OPERATOR')
    );

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.mark_notifications_read()
from public, anon;

grant execute on function public.mark_notifications_read()
to authenticated;

comment on function public.mark_notifications_read() is
  'Marks only the authenticated caller''s currently visible notifications as read.';

create or replace function public.create_delivery_notifications()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_status text;
  v_title text;
  v_requester_message text;
  v_staff_message text;
  v_type text;
  v_event_key text;
begin
  if tg_op = 'INSERT' then
    if new.requester_id is not null then
      insert into public.notifications (
        recipient_id,
        delivery_id,
        event_key,
        audience,
        title,
        message,
        type,
        data
      )
      values (
        new.requester_id,
        new.id,
        format('delivery:%s:created', new.id),
        'PERSONAL',
        'Delivery request created',
        format('%s is waiting for approval.', new.tracking_code),
        'info',
        jsonb_build_object(
          'trackingCode', new.tracking_code,
          'status', new.status
        )
      )
      on conflict (recipient_id, event_key) do nothing;
    end if;

    insert into public.notifications (
      recipient_id,
      delivery_id,
      event_key,
      audience,
      title,
      message,
      type,
      data
    )
    select
      profile.id,
      new.id,
      format('delivery:%s:requested', new.id),
      'STAFF',
      'New delivery request',
      format('%s is waiting for staff approval.', new.tracking_code),
      'info',
      jsonb_build_object(
        'trackingCode', new.tracking_code,
        'status', new.status
      )
    from public.profiles as profile
    where profile.role in ('ADMIN', 'OPERATOR')
      and profile.id is distinct from new.requester_id
    on conflict (recipient_id, event_key) do nothing;

    return new;
  end if;

  if new.status is not distinct from old.status then
    return new;
  end if;

  v_status := new.status::text;
  v_event_key := format('delivery:%s:status:%s', new.id, lower(v_status));
  v_type := case
    when v_status in ('APPROVED', 'DELIVERED', 'COMPLETED') then 'success'
    when v_status in ('PAUSED', 'FAILED', 'CANCELLED') then 'warning'
    else 'info'
  end;

  v_title := case v_status
    when 'APPROVED' then 'Delivery approved'
    when 'ASSIGNED' then 'Robot assigned'
    when 'DISPATCHED' then 'Mission queued'
    when 'TO_SOURCE' then 'Robot traveling to pickup'
    when 'AT_SOURCE' then 'Robot arrived at pickup'
    when 'PACKAGE_LOADED' then 'Package loaded'
    when 'TO_DESTINATION' then 'Package in transit'
    when 'AT_DESTINATION' then 'Robot arrived at destination'
    when 'DELIVERED' then 'Package delivered'
    when 'RETURNING' then 'Robot returning home'
    when 'COMPLETED' then 'Delivery completed'
    when 'PAUSED' then 'Delivery paused'
    when 'FAILED' then 'Delivery failed'
    when 'CANCELLED' then 'Delivery cancelled'
    else 'Delivery updated'
  end;

  v_requester_message := case v_status
    when 'APPROVED' then format('%s was approved.', new.tracking_code)
    when 'ASSIGNED' then format('A robot was assigned to %s.', new.tracking_code)
    when 'DISPATCHED' then format('The mission for %s was queued for the robot.', new.tracking_code)
    when 'TO_SOURCE' then format('The robot for %s is traveling to the pickup point.', new.tracking_code)
    when 'AT_SOURCE' then format('The robot for %s arrived at the pickup point.', new.tracking_code)
    when 'PACKAGE_LOADED' then format('The package for %s was loaded.', new.tracking_code)
    when 'TO_DESTINATION' then format('%s is traveling to its destination.', new.tracking_code)
    when 'AT_DESTINATION' then format('%s arrived at its destination.', new.tracking_code)
    when 'DELIVERED' then format('The package for %s was delivered.', new.tracking_code)
    when 'RETURNING' then format('The robot for %s is returning to its station.', new.tracking_code)
    when 'COMPLETED' then format('%s is complete.', new.tracking_code)
    when 'PAUSED' then format('%s was paused by operations.', new.tracking_code)
    when 'FAILED' then format('%s could not be completed. Operations has been alerted.', new.tracking_code)
    when 'CANCELLED' then format('%s was cancelled.', new.tracking_code)
    else format('%s changed to %s.', new.tracking_code, replace(lower(v_status), '_', ' '))
  end;

  v_staff_message := format(
    '%s changed to %s.',
    new.tracking_code,
    replace(lower(v_status), '_', ' ')
  );

  if new.requester_id is not null then
    insert into public.notifications (
      recipient_id,
      delivery_id,
      event_key,
      audience,
      title,
      message,
      type,
      data
    )
    values (
      new.requester_id,
      new.id,
      v_event_key,
      'PERSONAL',
      v_title,
      v_requester_message,
      v_type,
      jsonb_build_object(
        'trackingCode', new.tracking_code,
        'status', new.status,
        'robotId', new.robot_id
      )
    )
    on conflict (recipient_id, event_key) do nothing;
  end if;

  insert into public.notifications (
    recipient_id,
    delivery_id,
    event_key,
    audience,
    title,
    message,
    type,
    data
  )
  select
    profile.id,
    new.id,
    v_event_key,
    'STAFF',
    v_title,
    v_staff_message,
    v_type,
    jsonb_build_object(
      'trackingCode', new.tracking_code,
      'status', new.status,
      'robotId', new.robot_id
    )
  from public.profiles as profile
  where profile.role in ('ADMIN', 'OPERATOR')
    and profile.id is distinct from new.requester_id
  on conflict (recipient_id, event_key) do nothing;

  return new;
end;
$$;

revoke all on function public.create_delivery_notifications()
from public, anon, authenticated;

drop trigger if exists deliveries_create_notifications
on public.deliveries;

create trigger deliveries_create_notifications
after insert or update of status on public.deliveries
for each row execute function public.create_delivery_notifications();

create or replace function public.create_robot_event_notifications()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_robot_name text;
  v_title text;
  v_message text;
begin
  if new.severity not in ('WARNING', 'ERROR', 'CRITICAL')
    or new.event_type not in (
    'ROBOT_OFFLINE',
    'COMMAND_EXPIRED',
    'ESTOP_TRIGGERED',
    'OBSTACLE_DETECTED',
    'LOW_BATTERY',
    'ESP32_DISCONNECTED',
    'BRIDGE_FAULT'
    ) then
    return new;
  end if;

  select robot.name
  into v_robot_name
  from public.robots as robot
  where robot.id = new.robot_id;

  v_title := case new.event_type
    when 'ROBOT_OFFLINE' then 'Robot offline'
    when 'COMMAND_EXPIRED' then 'Robot command expired'
    when 'ESTOP_TRIGGERED' then 'Emergency stop triggered'
    when 'OBSTACLE_DETECTED' then 'Obstacle detected'
    when 'LOW_BATTERY' then 'Robot battery warning'
    when 'ESP32_DISCONNECTED' then 'ESP32 disconnected'
    when 'BRIDGE_FAULT' then 'Robot bridge fault'
    else 'Robot safety event'
  end;

  v_message := format(
    '%s (%s) reported %s.',
    coalesce(v_robot_name, 'Robot'),
    new.robot_id,
    replace(lower(new.event_type), '_', ' ')
  );

  insert into public.notifications (
    recipient_id,
    delivery_id,
    robot_event_id,
    event_key,
    audience,
    title,
    message,
    type,
    data
  )
  select
    profile.id,
    new.delivery_id,
    new.id,
    format('robot-event:%s', new.id),
    'STAFF',
    v_title,
    v_message,
    'warning',
    jsonb_build_object(
      'robotId', new.robot_id,
      'eventType', new.event_type,
      'severity', new.severity
    )
  from public.profiles as profile
  where profile.role in ('ADMIN', 'OPERATOR')
  on conflict (recipient_id, event_key) do nothing;

  return new;
end;
$$;

revoke all on function public.create_robot_event_notifications()
from public, anon, authenticated;

drop trigger if exists robot_events_create_notifications
on public.robot_events;

create trigger robot_events_create_notifications
after insert on public.robot_events
for each row execute function public.create_robot_event_notifications();

alter publication supabase_realtime add table public.notifications;

comment on table public.notifications is
  'Database-backed in-app notifications generated from delivery transitions and operational robot events.';
