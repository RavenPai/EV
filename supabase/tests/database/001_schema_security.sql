begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(37);

select has_table(
  'public',
  'notifications',
  'notifications table exists'
);

select has_column(
  'public',
  'notifications',
  'recipient_id',
  'notifications identify their recipient'
);

select has_column(
  'public',
  'notifications',
  'read_at',
  'notifications persist their read timestamp'
);

select ok(
  (
    select class.relrowsecurity
    from pg_catalog.pg_class as class
    join pg_catalog.pg_namespace as namespace
      on namespace.oid = class.relnamespace
    where namespace.nspname = 'public'
      and class.relname = 'notifications'
  ),
  'notifications have row-level security enabled'
);

select is(
  (
    select count(*)::integer
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'notifications'
      and policyname = 'users read their visible notifications'
  ),
  1,
  'the notification visibility policy is installed once'
);

select ok(
  has_table_privilege('authenticated', 'public.notifications', 'SELECT'),
  'authenticated users can select notifications through RLS'
);

select ok(
  not has_table_privilege('anon', 'public.notifications', 'SELECT'),
  'anonymous users cannot select notifications'
);

select ok(
  not has_table_privilege('authenticated', 'public.notifications', 'INSERT'),
  'authenticated users cannot forge notifications'
);

select ok(
  not has_table_privilege('authenticated', 'public.notifications', 'UPDATE'),
  'authenticated users cannot directly edit notifications'
);

select ok(
  not has_table_privilege('authenticated', 'public.notifications', 'DELETE'),
  'authenticated users cannot delete notifications'
);

select ok(
  has_function_privilege(
    'authenticated',
    'public.mark_notifications_read()',
    'EXECUTE'
  ),
  'authenticated users can call the protected mark-read function'
);

select ok(
  not has_function_privilege(
    'anon',
    'public.mark_notifications_read()',
    'EXECUTE'
  ),
  'anonymous users cannot call the mark-read function'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.apply_robot_state(text,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)',
    'EXECUTE'
  ),
  'the service role can apply robot state'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.apply_robot_state(text,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)',
    'EXECUTE'
  ),
  'authenticated clients cannot apply robot state directly'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.apply_robot_state_observed(text,timestamptz,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)',
    'EXECUTE'
  ),
  'the service role can apply broker-ordered robot state'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.apply_robot_state_observed(text,timestamptz,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)',
    'EXECUTE'
  ),
  'authenticated clients cannot apply broker-ordered robot state directly'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.apply_robot_event(uuid,text,uuid,uuid,text,text,jsonb,timestamptz)',
    'EXECUTE'
  ),
  'the service role can apply robot events'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.apply_robot_event(uuid,text,uuid,uuid,text,text,jsonb,timestamptz)',
    'EXECUTE'
  ),
  'authenticated clients cannot apply robot events directly'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.expire_stale_robot_commands()',
    'EXECUTE'
  ),
  'the service role can expire robot commands'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.expire_stale_robot_commands()',
    'EXECUTE'
  ),
  'authenticated clients cannot expire robot commands'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.apply_robot_ack(uuid,text,text,text,timestamp with time zone)',
    'EXECUTE'
  ),
  'the service role can apply robot acknowledgements'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.apply_robot_ack(uuid,text,text,text,timestamp with time zone)',
    'EXECUTE'
  ),
  'authenticated clients cannot apply robot acknowledgements directly'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.resolve_unknown_robot_command(uuid,uuid,text)',
    'EXECUTE'
  ),
  'the service role can reconcile an unknown command publish outcome'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.resolve_unknown_robot_command(uuid,uuid,text)',
    'EXECUTE'
  ),
  'authenticated clients cannot call publish reconciliation directly'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.finalize_robot_command_publish(uuid,text,uuid,timestamp with time zone)',
    'EXECUTE'
  ),
  'the service role can atomically finalize command publication'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.finalize_robot_command_publish(uuid,text,uuid,timestamp with time zone)',
    'EXECUTE'
  ),
  'authenticated clients cannot finalize command publication directly'
);

select has_column(
  'public',
  'robots',
  'telemetry_received_at',
  'robots record a trusted telemetry receipt timestamp'
);

select ok(
  has_table_privilege('authenticated', 'public.robots', 'SELECT'),
  'authenticated users can still read robot state through RLS'
);

select ok(
  not has_table_privilege('anon', 'public.robots', 'SELECT'),
  'anonymous users cannot read robot state'
);

select ok(
  not has_table_privilege('authenticated', 'public.robots', 'UPDATE'),
  'authenticated users cannot bypass ingestion with direct robot updates'
);

select ok(
  has_function_privilege(
    'service_role',
    'public.mark_stale_robots_offline()',
    'EXECUTE'
  ),
  'the service role can mark stale robots offline'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.mark_stale_robots_offline()',
    'EXECUTE'
  ),
  'authenticated clients cannot mark robots offline'
);

select is(
  (
    select count(*)::integer
    from pg_catalog.pg_trigger
    where tgrelid = 'public.deliveries'::regclass
      and tgname = 'deliveries_create_notifications'
      and not tgisinternal
  ),
  1,
  'the delivery notification trigger is installed once'
);

select is(
  (
    select count(*)::integer
    from pg_catalog.pg_trigger
    where tgrelid = 'public.robot_events'::regclass
      and tgname = 'robot_events_create_notifications'
      and not tgisinternal
  ),
  1,
  'the robot-event notification trigger is installed once'
);

select ok(
  not has_column_privilege(
    'authenticated',
    'public.profiles',
    'role',
    'UPDATE'
  ),
  'authenticated users cannot promote their own profile role'
);

select ok(
  has_column_privilege(
    'authenticated',
    'public.profiles',
    'full_name',
    'UPDATE'
  ),
  'authenticated users can still update their profile name'
);

select ok(
  exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.notifications'::regclass
      and contype = 'u'
      and pg_get_constraintdef(oid) =
        'UNIQUE (recipient_id, event_key)'
  ),
  'notifications enforce recipient/event deduplication'
);

select * from finish();
rollback;
