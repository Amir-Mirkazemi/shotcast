{# European-style season: July starts a new one. 2025-09-14 -> '2025-26' (same rule as insights.season_of). #}
{% macro season_label(date_col) %}
    case when month({{ date_col }}) >= 7
        then year({{ date_col }}) || '-' || right((year({{ date_col }}) + 1)::varchar, 2)
        else (year({{ date_col }}) - 1) || '-' || right(year({{ date_col }})::varchar, 2)
    end
{% endmacro %}
