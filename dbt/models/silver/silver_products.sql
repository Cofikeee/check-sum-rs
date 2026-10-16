{{ config(
    alias='products',
    unique_key='product_key'
) }}

with receipt_items as (
    select
        receipt.receipt_key,
        receipt.merchant_id,
        receipt.merchant_name,
        item.name_source,
        item.unit,
        item.bronze_receipt_id,
        item.id as bronze_item_id
    from {{ source('bronze', 'slipapp_items') }} as item
    join {{ ref('silver_slipapp_receipts') }} as receipt
        on receipt.bronze_slipapp_receipt_id = item.bronze_receipt_id
),
manual_items as (
    select
        null::text as receipt_key,
        merchant.id as merchant_id,
        merchant.name_source as merchant_name,
        input_entry.product_name_source as name_source,
        input_entry.unit,
        null::bigint as bronze_receipt_id,
        null::bigint as bronze_item_id
    from {{ source('bronze', 'input_entries') }} as input_entry
    left join {{ ref('silver_merchants') }} as merchant
        on merchant.name_normalized = {{ normalize_company_name('input_entry.merchant_name_source') }}
    
),
all_items as (
    select * from receipt_items
    union all
    select * from manual_items
),
translated as (
    select
        all_items.*,
        {{ trim_product_metadata(natural_product_source('all_items.name_source')) }} as clean_name_source,
        {{ natural_product_source('all_items.name_source') }} as natural_name_source
    from all_items
),
deduplicated as (
    select *, row_number() over (
        partition by coalesce(merchant_name, ''), clean_name_source
        order by coalesce(bronze_item_id, 9223372036854775807)
    ) as row_number
    from translated
)
select
    md5(coalesce(merchant_name, '') || '|' || clean_name_source) as product_key,
    merchant_id,
    merchant_name,
    clean_name_source as name_source,
    {{ trim_product_metadata('coalesce(override.english_text, natural_name_source)') }} as name_en,
    lower(trim(clean_name_source)) as name_normalized,
    {{ category_wide('clean_name_source', "coalesce(merchant_name, '')") }} as category_wide,
    {{ category_narrow('clean_name_source', "coalesce(merchant_name, '')") }} as category_narrow,
    'dbt_heuristic' as category_source,
    0.70::double precision as category_confidence,
    current_timestamp as updated_at
from deduplicated
left join {{ source('meta', 'translation_overrides') }} as override
    on override.source_normalized = lower(trim(natural_name_source))
where row_number = 1
