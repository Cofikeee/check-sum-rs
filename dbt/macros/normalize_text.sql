{% macro normalize_text(expression) -%}
upper(
    trim(
        regexp_replace(
            regexp_replace(coalesce({{ expression }}, ''), '>>[^<]+<<', '', 'g'),
            '[[:space:]]+', ' ', 'g'
        )
    )
)
{%- endmacro %}

{% macro sha256_hex(expression) -%}
encode(digest(convert_to(coalesce({{ expression }}, ''), 'UTF8'), 'sha256'), 'hex')
{%- endmacro %}

{% macro trim_product_metadata(expression) -%}
upper(
    trim(
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(regexp_replace(coalesce({{ expression }}, ''), '>>[^<]+<<', '', 'g'), '^[[:space:]]*\[[0-9]+\][[:space:]]*', '', 'g'),
                    '^[[:space:]]*[0-9]+[[:space:]]+', '', 'g'
                ),
                '[[:space:]]*/[[:space:]]*(KOM|КОМ|PCS|KG|КГ|G|Г|L|Л|SET|M)[.]?[[:space:]]*$', '', 'gi'
            ),
            '[[:space:]]*\[(KOM|КОМ|PCS|KG|КГ|G|Г|L|Л|SET|M)\][.]?[[:space:]]*$', '', 'gi'
        )
    )
)
{%- endmacro %}

{% macro natural_product_source(expression) -%}
regexp_replace(coalesce({{ expression }}, ''), '[[:space:]]*/[^/]+/[^/]+[[:space:]]*$', '', 'g')
{%- endmacro %}

{% macro normalize_company_name(expression) -%}
trim(
    regexp_replace(
            regexp_replace(
                regexp_replace(
                    upper(coalesce({{ expression }}, '')),
                '[[:<:]](D[.]?[[:space:]]*O[.]?[[:space:]]*O[.]?|Д[.]?[[:space:]]*О[.]?[[:space:]]*О[.]?|NOVI[[:space:]]+SAD|BEOGRAD|SERBIA|SRBIJA|RS|NS)[[:>:]]',
                ' ', 'gi'
            ),
            '[,.;:/()_-]+', ' ', 'g'
        ),
        '[[:space:]]+', ' ', 'g'
    )
)
{%- endmacro %}
