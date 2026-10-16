{{ config(
    alias='input_entries',
    unique_key='bronze_input_id'
) }}

with prepared as (
    select
        input_entry.id as bronze_input_id,
        merchant.id as merchant_id,
        merchant.name_source as merchant_name,
        product.id as product_id,
        {{ trim_product_metadata(natural_product_source('input_entry.product_name_source')) }} as product_name_source,
        input_entry.quantity,
        input_entry.unit,
        product.category_wide,
        product.category_narrow,
        product.category_source,
        product.category_confidence
    from {{ source('bronze', 'input_entries') }} as input_entry
    left join {{ ref('silver_merchants') }} as merchant
        on merchant.name_normalized = {{ normalize_company_name('input_entry.merchant_name_source') }}
    left join {{ ref('silver_products') }} as product
        on product.product_key = md5(
            coalesce(merchant.name_source, '') || '|' || {{ trim_product_metadata(natural_product_source('input_entry.product_name_source')) }}
        )
)
select
    bronze_input_id,
    merchant_id,
    product_id,
    merchant_name,
    {{ trim_product_metadata('coalesce(override.english_text, product_name_source)') }} as product_name,
    product_name_source,
    quantity,
    unit,
    coalesce(category_wide, 'OTHER') as category_wide,
    coalesce(category_narrow, 'OTHER') as category_narrow,
    coalesce(category_source, 'dbt_heuristic') as category_source,
    coalesce(category_confidence, 0.70)::double precision as category_confidence,
    current_timestamp as created_at
from prepared
left join {{ source('meta', 'translation_overrides') }} as override
    on override.source_normalized = lower(trim(product_name_source))
