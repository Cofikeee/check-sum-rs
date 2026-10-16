{{ config(
    alias='merchants',
    unique_key='id',
    pre_hook=[
        "alter table if exists silver.merchants drop column if exists name_en",
        "delete from silver.input_entries",
        "delete from silver.slipapp_items",
        "delete from silver.products",
        "delete from silver.slipapp_receipts",
        "delete from silver.merchants"
    ]
) }}

with merchant_sources as (
    select merchant_name_source as name_source
    from {{ source('bronze', 'slipapp_receipts') }}
    where nullif(trim(merchant_name_source), '') is not null

    union all

    select merchant_name_source as name_source
    from {{ source('bronze', 'input_entries') }}
    where nullif(trim(merchant_name_source), '') is not null
),
normalized as (
    select
        name_source,
        {{ normalize_company_name('name_source') }} as company_name
    from merchant_sources
),
deduplicated as (
    select *, row_number() over (
        partition by company_name order by name_source
    ) as row_number
    from normalized
)
select
    {{ sha256_hex('company_name') }} as id,
    name_source,
    company_name as name_normalized,
    current_timestamp as updated_at
from deduplicated
where row_number = 1 and nullif(company_name, '') is not null
