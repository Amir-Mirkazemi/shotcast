{{ config(severity='warn') }}
-- The loader gives repeated ESPN play ids a "~n" suffix so every shot has a unique key. This lists the repeats.
select shot_id, match_id from {{ ref('stg_espn_shots') }} where shot_id like '%~%'
