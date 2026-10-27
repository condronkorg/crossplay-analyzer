-- Persist individual engine requests, including progress before a full review finishes.
alter table public.game_analyses add column if not exists turn_analyses jsonb not null default '{}'::jsonb;

-- Merge only supplied results atomically so tabs and different analysis views
-- cannot overwrite each other's completed work.
create or replace function public.save_crossplay_analysis(
  account_id uuid, game_id text, version text, snapshot jsonb
) returns void language sql set search_path = public as $$
  insert into public.game_analyses (user_id, nyt_game_id, analysis_version, win_trend, move_quality, turn_analyses)
  values (account_id, game_id, version, snapshot->'win_trend', snapshot->'move_quality', coalesce(snapshot->'turn_analyses', '{}'::jsonb))
  on conflict (user_id, nyt_game_id, analysis_version) do update set
    win_trend = coalesce(excluded.win_trend, game_analyses.win_trend),
    move_quality = coalesce(excluded.move_quality, game_analyses.move_quality),
    turn_analyses = game_analyses.turn_analyses || excluded.turn_analyses,
    updated_at = now();
$$;
revoke all on function public.save_crossplay_analysis(uuid, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.save_crossplay_analysis(uuid, text, text, jsonb) to service_role;

create table if not exists public.crossplay_refresh_state (
  user_id uuid primary key references auth.users (id) on delete cascade,
  refreshed_at timestamptz not null default now()
);
alter table public.crossplay_refresh_state enable row level security;
revoke all on public.crossplay_refresh_state from anon, authenticated;
grant all on public.crossplay_refresh_state to service_role;
