create sequence if not exists public.delivery_tracking_sequence;

select setval(
  'public.delivery_tracking_sequence',
  greatest(
    1050,
    coalesce(
      (
        select max(substring(tracking_code from 'MIIT-([0-9]+)')::bigint)
        from public.deliveries
        where tracking_code ~ '^MIIT-[0-9]+$'
      ),
      0
    )
  ),
  true
);

alter table public.deliveries
  alter column tracking_code set default (
    'MIIT-' || lpad(nextval('public.delivery_tracking_sequence')::text, 4, '0')
  );
