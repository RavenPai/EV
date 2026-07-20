create or replace function public.set_authenticated_delivery_requester()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_requester_id uuid := auth.uid();
  v_requester_name text;
  v_requester_email text;
begin
  if v_requester_id is null then
    raise exception 'An authenticated user is required to create a delivery'
      using errcode = '42501';
  end if;

  select
    nullif(btrim(profile.full_name), ''),
    nullif(btrim(profile.email), '')
  into
    v_requester_name,
    v_requester_email
  from public.profiles as profile
  where profile.id = v_requester_id;

  if not found then
    raise exception 'The authenticated user profile is missing'
      using errcode = '23503';
  end if;

  if v_requester_name is null or v_requester_email is null then
    raise exception 'The authenticated user profile must include a full name and email'
      using errcode = '23514';
  end if;

  new.requester_id := v_requester_id;
  new.requester_name := v_requester_name;
  new.requester_email := v_requester_email;
  return new;
end;
$$;

revoke all on function public.set_authenticated_delivery_requester()
from public, anon, authenticated;

drop trigger if exists deliveries_set_authenticated_requester
on public.deliveries;

create trigger deliveries_set_authenticated_requester
before insert on public.deliveries
for each row execute function public.set_authenticated_delivery_requester();

comment on function public.set_authenticated_delivery_requester() is
  'Copies requester identity from the authenticated user profile before delivery insertion.';
