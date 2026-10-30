-- DocentsCV · AVISO A QUIEN SIGUE SIN PLAZA cuando se carga una adjudicación continua nueva
-- Ejecutar una vez en Supabase > SQL Editor (se puede repetir sin problema).
-- Requiere push.sql y las tablas de adjudicaciones continuas (ya las tienes).

create table if not exists avisos_continua (
  user_id uuid not null references auth.users(id) on delete cascade,
  fecha   date not null,
  grupo   text not null,
  primary key (user_id, fecha, grupo)
);
alter table avisos_continua enable row level security;
revoke all on avisos_continua from anon, authenticated;

-- Personas con notificaciones que, en la lista recién cargada, siguen en la bolsa SIN plaza
-- (estado 2 «No adjudicat», 3 «Ha participat» o 4 «No ha participat») y no tienen plaza en ninguna otra lista.
-- «vuelve» = en la tanda anterior tenía plaza (estado 1) y ahora vuelve a estar disponible.
create or replace function push_continua_pendientes(p_fecha date, p_grupo text)
returns table(user_id uuid, endpoint text, p256dh text, auth text, pos integer, bolsa text, nlistas integer, vuelve boolean)
language plpgsql security definer set search_path = public as $$
declare ins smallint; prev smallint;
begin
  select id into ins from instantaneas where tipo = 'continua' and grupo = p_grupo and fecha = p_fecha;
  if ins is null then return; end if;
  select id into prev from instantaneas where tipo = 'continua' and grupo = p_grupo and fecha < p_fecha order by fecha desc limit 1;
  return query
  with cand as (
    select d.user_id as uid, d.persona_id as pid
    from identidades d
    where exists (select 1 from push_suscripciones s where s.user_id = d.user_id)
      and not exists (select 1 from avisos_continua a where a.user_id = d.user_id and a.fecha = p_fecha and a.grupo = p_grupo)
      and coalesce((acceso_de(d.user_id) ->> 'activo')::boolean, false)),
  hoy as (                                  -- su situación en la lista recién cargada
    select c.uid, c.pid, p.lista_id, p.posicion, p.estado from cand c join posiciones p on p.persona_id = c.pid and p.instantanea_id = ins),
  ult as (                                  -- su situación más reciente en cada lista (cualquier grupo)
    select distinct on (c.uid, p.lista_id) c.uid, p.lista_id, p.estado
    from cand c join posiciones p on p.persona_id = c.pid join instantaneas i on i.id = p.instantanea_id
    order by c.uid, p.lista_id, i.fecha desc, i.id desc),
  libres as (
    select h.uid, min(h.posicion)::integer as pos, count(*)::integer as n,
           (array_agg(h.lista_id order by h.posicion))[1] as lista
    from hoy h where h.estado in (2, 3, 4) and not exists (select 1 from ult u where u.uid = h.uid and u.estado = 1)
    group by h.uid),
  antes as (
    select distinct h.uid from hoy h join posiciones q on q.persona_id = h.pid and q.instantanea_id = prev and q.lista_id = h.lista_id and q.estado = 1
    where h.estado in (2, 3, 4))
  select s.user_id, s.endpoint, s.p256dh, s.auth, l.pos, coalesce(li.nombre, li.bolsa), l.n, exists (select 1 from antes a where a.uid = l.uid)
  from libres l join listas li on li.id = l.lista join push_suscripciones s on s.user_id = l.uid
  order by s.user_id, s.id;
end $$;
revoke execute on function push_continua_pendientes(date, text) from public, anon, authenticated;
