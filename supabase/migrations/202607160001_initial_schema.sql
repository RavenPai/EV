create extension if not exists pgcrypto;

create type public.app_role as enum ('USER', 'ADMIN', 'OPERATOR');
create type public.delivery_status as enum (
  'REQUESTED', 'APPROVED', 'ASSIGNED', 'TO_SOURCE', 'AT_SOURCE', 'PACKAGE_LOADED',
  'TO_DESTINATION', 'AT_DESTINATION', 'DELIVERED', 'RETURNING', 'COMPLETED',
  'PAUSED', 'FAILED', 'CANCELLED'
);
create type public.robot_status as enum ('ONLINE', 'BUSY', 'CHARGING', 'OFFLINE', 'FAULT');
create type public.robot_mode as enum ('IDLE', 'AUTO', 'MANUAL', 'PAUSED', 'ESTOP', 'FAULT');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  email text not null,
  role public.app_role not null default 'USER',
  created_at timestamptz not null default now()
);

create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  insert into public.profiles (id, full_name, email, role)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', split_part(new.email, '@', 1)), new.email, 'USER');
  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure public.handle_new_user();

create table public.locations (
  id text primary key,
  code text not null unique,
  name text not null,
  description text,
  map_id text not null default 'miit-campus-v1',
  x numeric(6,2) not null,
  y numeric(6,2) not null,
  yaw numeric(7,4) not null default 0,
  marker_id integer unique,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.robots (
  id text primary key,
  name text not null,
  model text not null,
  status public.robot_status not null default 'OFFLINE',
  mode public.robot_mode not null default 'IDLE',
  battery integer not null default 0 check (battery between 0 and 100),
  location_id text references public.locations(id),
  signal integer not null default 0 check (signal between 0 and 100),
  speed_mps numeric(6,3) not null default 0,
  lidar text not null default 'OFFLINE' check (lidar in ('OK', 'WARNING', 'OFFLINE')),
  camera text not null default 'OFFLINE' check (camera in ('OK', 'WARNING', 'OFFLINE')),
  esp32 text not null default 'OFFLINE' check (esp32 in ('OK', 'WARNING', 'OFFLINE')),
  motor_temp_c numeric(5,2) not null default 0,
  map_version text not null default 'miit-campus-v1',
  current_delivery_id uuid,
  last_seen timestamptz,
  updated_at timestamptz not null default now()
);

create table public.deliveries (
  id uuid primary key default gen_random_uuid(),
  tracking_code text not null unique,
  requester_id uuid references public.profiles(id),
  requester_name text not null,
  requester_email text not null,
  recipient_name text not null,
  recipient_phone text,
  source_id text not null references public.locations(id),
  destination_id text not null references public.locations(id),
  item_name text not null,
  category text not null,
  weight_kg numeric(5,2) not null check (weight_kg > 0 and weight_kg <= 10),
  priority text not null default 'NORMAL' check (priority in ('NORMAL', 'HIGH', 'URGENT')),
  status public.delivery_status not null default 'REQUESTED',
  robot_id text references public.robots(id),
  notes text,
  unlock_code_hash text,
  progress integer not null default 5 check (progress between 0 and 100),
  eta_minutes integer,
  approved_by uuid references public.profiles(id),
  approved_at timestamptz,
  dispatched_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint different_delivery_locations check (source_id <> destination_id)
);

alter table public.robots
  add constraint robots_current_delivery_fk
  foreign key (current_delivery_id) references public.deliveries(id) on delete set null;

create table public.robot_commands (
  id uuid primary key default gen_random_uuid(),
  robot_id text not null references public.robots(id),
  delivery_id uuid references public.deliveries(id),
  command_type text not null,
  sequence_no bigint generated always as identity,
  payload jsonb not null,
  status text not null default 'PENDING' check (status in ('PENDING', 'PUBLISHED', 'ACKNOWLEDGED', 'REJECTED', 'COMPLETED', 'FAILED', 'EXPIRED')),
  issued_by uuid references public.profiles(id),
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  published_at timestamptz,
  acknowledged_at timestamptz,
  result jsonb
);

create table public.robot_events (
  id bigint generated always as identity primary key,
  robot_id text not null references public.robots(id),
  delivery_id uuid references public.deliveries(id),
  event_type text not null,
  severity text not null default 'INFO' check (severity in ('INFO', 'WARNING', 'ERROR', 'CRITICAL')),
  payload jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create index deliveries_requester_idx on public.deliveries(requester_id, created_at desc);
create index deliveries_status_idx on public.deliveries(status, created_at desc);
create index deliveries_robot_idx on public.deliveries(robot_id, status);
create index robot_commands_pending_idx on public.robot_commands(robot_id, status, issued_at);
create index robot_events_delivery_idx on public.robot_events(delivery_id, occurred_at desc);

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger deliveries_set_updated_at before update on public.deliveries
for each row execute procedure public.set_updated_at();
create trigger robots_set_updated_at before update on public.robots
for each row execute procedure public.set_updated_at();

create or replace function public.current_user_role()
returns public.app_role
language sql stable security definer set search_path = public
as $$ select role from public.profiles where id = auth.uid() $$;

alter table public.profiles enable row level security;
alter table public.locations enable row level security;
alter table public.robots enable row level security;
alter table public.deliveries enable row level security;
alter table public.robot_commands enable row level security;
alter table public.robot_events enable row level security;

create policy "profiles read own or staff" on public.profiles for select to authenticated
using (id = auth.uid() or public.current_user_role() in ('ADMIN', 'OPERATOR'));
create policy "profiles update own" on public.profiles for update to authenticated
using (id = auth.uid()) with check (id = auth.uid());

create policy "authenticated users read active locations" on public.locations for select to authenticated
using (active = true);
create policy "staff manage locations" on public.locations for all to authenticated
using (public.current_user_role() = 'ADMIN') with check (public.current_user_role() = 'ADMIN');

create policy "authenticated users read robot state" on public.robots for select to authenticated using (true);
create policy "staff update robots" on public.robots for update to authenticated
using (public.current_user_role() in ('ADMIN', 'OPERATOR'))
with check (public.current_user_role() in ('ADMIN', 'OPERATOR'));

create policy "users read own deliveries and staff read all" on public.deliveries for select to authenticated
using (requester_id = auth.uid() or public.current_user_role() in ('ADMIN', 'OPERATOR'));
create policy "users create own deliveries" on public.deliveries for insert to authenticated
with check (requester_id = auth.uid() and status = 'REQUESTED');
create policy "users cancel own waiting requests" on public.deliveries for update to authenticated
using (requester_id = auth.uid() and status in ('REQUESTED', 'APPROVED'))
with check (requester_id = auth.uid() and status = 'CANCELLED');
create policy "staff update deliveries" on public.deliveries for update to authenticated
using (public.current_user_role() in ('ADMIN', 'OPERATOR'))
with check (public.current_user_role() in ('ADMIN', 'OPERATOR'));

create policy "staff read commands" on public.robot_commands for select to authenticated
using (public.current_user_role() in ('ADMIN', 'OPERATOR'));
create policy "staff read events" on public.robot_events for select to authenticated
using (public.current_user_role() in ('ADMIN', 'OPERATOR'));

insert into public.locations (id, code, name, description, x, y, marker_id) values
  ('loc-home', 'HOME', 'Robot Station', 'Charging and maintenance bay', 15, 76, 10),
  ('loc-fcs', 'FCS', 'Faculty of Computer Science', 'Main computer science building', 29, 30, 20),
  ('loc-fcst', 'FCST', 'Faculty of Computer Systems & Technologies', 'Systems and technology building', 49, 18, 21),
  ('loc-library', 'LIB', 'MIIT Library', 'Central library entrance', 67, 34, 24),
  ('loc-data', 'DC', 'Data Center', 'Campus data center reception', 80, 62, 30),
  ('loc-rector', 'RECTOR', 'Rector Office', 'Administration building', 54, 72, 40),
  ('loc-canteen', 'CANTEEN', 'Campus Canteen', 'Main canteen pickup point', 30, 58, 45)
on conflict (id) do nothing;

insert into public.robots (id, name, model, status, mode, battery, location_id, signal, last_seen, lidar, camera, esp32, motor_temp_c) values
  ('robot-01', 'Rover 01', 'MIIT EV Mk-II', 'ONLINE', 'IDLE', 90, 'loc-home', 95, now(), 'OK', 'OK', 'OK', 34),
  ('robot-02', 'Rover 02', 'MIIT EV Mk-II', 'ONLINE', 'IDLE', 88, 'loc-home', 91, now(), 'OK', 'OK', 'OK', 35),
  ('robot-03', 'Rover 03', 'MIIT EV Mk-I', 'CHARGING', 'IDLE', 45, 'loc-home', 82, now(), 'WARNING', 'OK', 'OK', 31)
on conflict (id) do nothing;

alter publication supabase_realtime add table public.deliveries;
alter publication supabase_realtime add table public.robots;
