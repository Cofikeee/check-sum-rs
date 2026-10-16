{{ config(
    alias='input_entries',
    unique_key='id',
    pre_hook="alter table if exists silver.input_entries drop column if exists product_name"
) }}

with prepared as (
    select
        input_entry.id as bronze_input_id,
        input_entry.source_chat_id,
        input_entry.source_message_id,
        input_entry.source_text,
        input_entry.created_at,
        merchant.id as merchant_id,
        merchant.name_source as merchant_name,
        product.id as product_id,
        input_entry.product_name_source as product_name_source,
        {{ natural_product_source('input_entry.product_name_source') }} as natural_product_name_source,
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
        on product.product_key = encode(
            digest(
                convert_to(
                    coalesce(merchant.id, '') || '|' || {{ trim_product_metadata(natural_product_source('input_entry.product_name_source')) }},
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        )
)
select
    {{ sha256_hex(
        "'input:' || coalesce(prepared.source_chat_id::text || ':' || prepared.source_message_id::text, prepared.source_text || '|' || coalesce(prepared.merchant_name, '') || '|' || prepared.product_name_source || '|' || prepared.created_at::text)"
    ) }} as id,
    bronze_input_id,
    merchant_id,
    product_id,
    merchant_name,
    {{ trim_product_metadata('coalesce(override.english_text, natural_product_name_source)') }} as product_name_normalized,
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
    on override.source_normalized = lower(trim(prepared.natural_product_name_source))
