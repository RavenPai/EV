begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(14);

select ok(
  has_schema_privilege('service_role', 'public', 'USAGE'),
  'the service role can resolve public API objects'
);

with api_tables(table_name) as (
  values
    ('public.profiles'),
    ('public.locations'),
    ('public.robots'),
    ('public.deliveries'),
    ('public.robot_commands'),
    ('public.robot_events'),
    ('public.notifications')
), api_privileges(privilege_name) as (
  values
    ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
    ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
)
select is(
  (
    select count(*)::integer
    from api_tables
    cross join api_privileges
    where has_table_privilege('anon', table_name, privilege_name)
  ),
  0,
  'anonymous clients have no public application-table privileges'
);

with required(table_name, privilege_name) as (
  values
    ('public.profiles', 'SELECT'),
    ('public.locations', 'SELECT'),
    ('public.locations', 'INSERT'),
    ('public.locations', 'UPDATE'),
    ('public.locations', 'DELETE'),
    ('public.robots', 'SELECT'),
    ('public.deliveries', 'SELECT'),
    ('public.deliveries', 'INSERT'),
    ('public.deliveries', 'UPDATE'),
    ('public.robot_commands', 'SELECT'),
    ('public.robot_events', 'SELECT'),
    ('public.notifications', 'SELECT')
)
select is(
  (
    select count(*)::integer
    from required
    where not has_table_privilege(
      'authenticated', table_name, privilege_name
    )
  ),
  0,
  'authenticated clients have every RLS-gated application privilege'
);

with ingestion_tables(table_name) as (
  values
    ('public.robots'),
    ('public.robot_commands'),
    ('public.robot_events')
), mutation_privileges(privilege_name) as (
  values
    ('INSERT'), ('UPDATE'), ('DELETE'),
    ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
)
select is(
  (
    select count(*)::integer
    from ingestion_tables
    cross join mutation_privileges
    where has_table_privilege(
      'authenticated', table_name, privilege_name
    )
  ),
  0,
  'authenticated clients cannot mutate ingestion-owned tables directly'
);

select ok(
  not has_table_privilege('authenticated', 'public.deliveries', 'DELETE'),
  'authenticated clients cannot delete delivery audit records'
);

select ok(
  has_sequence_privilege(
    'authenticated',
    'public.delivery_tracking_sequence',
    'USAGE'
  ),
  'authenticated delivery creation can allocate a tracking code'
);

with required(table_name, privilege_name) as (
  values
    ('public.profiles', 'SELECT'),
    ('public.robots', 'SELECT'),
    ('public.deliveries', 'SELECT'),
    ('public.robot_commands', 'SELECT'),
    ('public.robot_commands', 'INSERT'),
    ('public.robot_commands', 'UPDATE')
)
select is(
  (
    select count(*)::integer
    from required
    where not has_table_privilege(
      'service_role', table_name, privilege_name
    )
  ),
  0,
  'the service role has the table privileges required by Edge Functions'
);

select ok(
  has_sequence_privilege(
    'service_role',
    'public.robot_commands_sequence_no_seq',
    'USAGE'
  ),
  'the dispatch Edge Function can allocate a command sequence number'
);

with protected_tables(table_name) as (
  values
    ('public.profiles'),
    ('public.robots'),
    ('public.deliveries'),
    ('public.robot_events'),
    ('public.notifications')
), mutation_privileges(privilege_name) as (
  values
    ('INSERT'), ('UPDATE'), ('DELETE'),
    ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
)
select is(
  (
    select count(*)::integer
    from protected_tables
    cross join mutation_privileges
    where has_table_privilege(
      'service_role', table_name, privilege_name
    )
  ),
  0,
  'Edge Functions cannot bypass protected-table workflows with direct writes'
);

select is(
  (
    select count(*)::integer
    from (
      values
        ('public.delivery_tracking_sequence'),
        ('public.robot_commands_sequence_no_seq')
    ) as api_sequences(sequence_name)
    where has_sequence_privilege('anon', sequence_name, 'USAGE')
       or has_sequence_privilege('anon', sequence_name, 'SELECT')
       or has_sequence_privilege('anon', sequence_name, 'UPDATE')
  ),
  0,
  'anonymous clients cannot use application sequences'
);

with ingestion_rpc(rpc) as (
  values
    ('public.apply_robot_ack(uuid,text,text,text,timestamptz)'),
    ('public.apply_robot_state_observed(text,timestamptz,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)'),
    ('public.apply_robot_event(uuid,text,uuid,uuid,text,text,jsonb,timestamptz)'),
    ('public.apply_robot_presence(text,boolean,text,timestamptz)')
)
select is(
  (
    select count(*)::integer
    from ingestion_rpc
    where not has_function_privilege('service_role', rpc, 'EXECUTE')
  ),
  0,
  'the service role can execute every ingestion RPC'
);

with browser_roles(role_name) as (
  values ('anon'), ('authenticated')
), ingestion_rpc(rpc) as (
  values
    ('public.apply_robot_ack(uuid,text,text,text,timestamptz)'),
    ('public.apply_robot_state_observed(text,timestamptz,timestamptz,text,text,integer,integer,numeric,text,uuid,text,text,text,numeric,text)'),
    ('public.apply_robot_event(uuid,text,uuid,uuid,text,text,jsonb,timestamptz)'),
    ('public.apply_robot_presence(text,boolean,text,timestamptz)')
)
select is(
  (
    select count(*)::integer
    from browser_roles
    cross join ingestion_rpc
    where has_function_privilege(role_name, rpc, 'EXECUTE')
  ),
  0,
  'browser roles cannot execute ingestion RPCs'
);

select is(
  (
    select count(*)::integer
    from pg_catalog.pg_class as class
    join pg_catalog.pg_namespace as namespace
      on namespace.oid = class.relnamespace
    where namespace.nspname = 'public'
      and class.relname in ('robots', 'robot_commands', 'robot_events')
      and class.relrowsecurity
  ),
  3,
  'every ingestion-owned table enforces RLS'
);

select is(
  (
    select count(*)::integer
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename in ('robots', 'robot_commands', 'robot_events')
      and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')
      and roles && array['anon', 'authenticated', 'public']::name[]
  ),
  0,
  'no browser RLS policy permits ingestion-table mutation'
);

select * from finish();
rollback;
